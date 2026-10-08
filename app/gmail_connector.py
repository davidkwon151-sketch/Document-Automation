"""Small Gmail OAuth/API adapter. The caller owns tokens and approval state."""

import base64
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import SMTP
from email.utils import getaddresses
import hashlib
import re
import secrets
from urllib.parse import urlencode

import httpx

from agent.buyer_email import parse_buyer_email


AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
API_URL = 'https://gmail.googleapis.com/gmail/v1/users/me'
SCOPES = ('https://www.googleapis.com/auth/gmail.readonly',
          'https://www.googleapis.com/auth/gmail.send')
_ID = re.compile(r'^[A-Za-z0-9_-]{1,256}$')
_EMAIL = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+$')


class GmailError(ValueError):
    """A safe error code; provider responses and credentials stay private."""


def _text(value, name, *, limit=100000):
    if (not isinstance(value, str) or not value or len(value) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise GmailError(name + '_invalid')
    return value


class GmailConnector:
    def __init__(self, client_id, client_secret, redirect_uri, *, transport=None, timeout=10):
        self.client_id = _text(client_id, 'client_id', limit=500)
        self.client_secret = _text(client_secret, 'client_secret', limit=500)
        self.redirect_uri = _text(redirect_uri, 'redirect_uri', limit=1000)
        if not (self.redirect_uri.startswith('https://') or
                self.redirect_uri.startswith('http://localhost/') or
                self.redirect_uri.startswith('http://127.0.0.1/')):
            raise GmailError('redirect_uri_invalid')
        self.transport = transport
        self.timeout = timeout

    def authorization_request(self):
        """Persist state and verifier in the caller's authenticated session."""
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
        return {'url': self.authorization_url(state, challenge), 'state': state,
                'code_verifier': verifier, 'code_challenge': challenge}

    def authorization_url(self, state, code_challenge):
        _text(state, 'oauth_state', limit=256)
        if not isinstance(code_challenge, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', code_challenge):
            raise GmailError('code_challenge_invalid')
        query = urlencode({'client_id': self.client_id, 'redirect_uri': self.redirect_uri,
                           'response_type': 'code', 'scope': ' '.join(SCOPES),
                           'access_type': 'offline', 'state': state,
                           'code_challenge': code_challenge, 'code_challenge_method': 'S256'})
        return AUTH_URL + '?' + query

    def _request(self, method, url, *, token=None, **kwargs):
        headers = {'Accept': 'application/json'}
        if token is not None:
            headers['Authorization'] = 'Bearer ' + _text(token, 'access_token', limit=4096)
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False,
                              follow_redirects=False, transport=self.transport) as client:
                response = client.request(method, url, headers=headers, **kwargs)
            if response.status_code == 401:
                raise GmailError('gmail_auth_required')
            if response.status_code >= 300:
                raise GmailError('gmail_request_failed')
            result = response.json()
            if not isinstance(result, dict):
                raise GmailError('gmail_response_invalid')
            return result
        except GmailError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise GmailError('gmail_request_failed') from None

    def exchange_code(self, code, code_verifier):
        """The caller verifies OAuth state against its saved session before this call."""
        result = self._request('POST', TOKEN_URL, data={
            'client_id': self.client_id, 'client_secret': self.client_secret,
            'redirect_uri': self.redirect_uri, 'grant_type': 'authorization_code',
            'code': _text(code, 'code', limit=2048),
            'code_verifier': _text(code_verifier, 'code_verifier', limit=128)})
        return self._tokens(result)

    def refresh(self, refresh_token):
        result = self._request('POST', TOKEN_URL, data={
            'client_id': self.client_id, 'client_secret': self.client_secret,
            'grant_type': 'refresh_token',
            'refresh_token': _text(refresh_token, 'refresh_token', limit=2048)})
        return self._tokens(result)

    @staticmethod
    def _tokens(result):
        token_type = result.get('token_type')
        if (not isinstance(token_type, str) or token_type.casefold() != 'bearer'
                or not isinstance(result.get('access_token'), str) or not result['access_token']):
            raise GmailError('oauth_response_invalid')
        return {key: result[key] for key in ('access_token', 'refresh_token', 'expires_in', 'scope') if key in result}

    def profile(self, access_token):
        result = self._request('GET', API_URL + '/profile', token=access_token)
        email = result.get('emailAddress')
        if not isinstance(email, str) or not _EMAIL.fullmatch(email):
            raise GmailError('gmail_response_invalid')
        return email

    def list_inbox(self, access_token, *, unread_only=False, max_results=100):
        if type(unread_only) is not bool or type(max_results) is not int or not 1 <= max_results <= 100:
            raise GmailError('list_options_invalid')
        params = [('labelIds', 'INBOX'), ('maxResults', max_results)]
        if unread_only:
            params.append(('labelIds', 'UNREAD'))
        result = self._request('GET', API_URL + '/messages', token=access_token, params=params)
        messages = result.get('messages', [])
        if not isinstance(messages, list) or any(not isinstance(item, dict) or
                not isinstance(item.get('id'), str) or not _ID.fullmatch(item['id']) for item in messages):
            raise GmailError('gmail_response_invalid')
        return [item['id'] for item in messages]

    def get_message(self, access_token, message_id):
        if not isinstance(message_id, str) or not _ID.fullmatch(message_id):
            raise GmailError('message_id_invalid')
        result = self._request('GET', API_URL + '/messages/' + message_id,
                               token=access_token, params={'format': 'raw'})
        try:
            raw = result['raw']
            data = base64.b64decode(raw + '=' * (-len(raw) % 4), altchars=b'-_', validate=True)
            if result.get('id') != message_id or not data:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise GmailError('gmail_response_invalid') from None
        try:
            email = parse_buyer_email(eml=data)
            parsed = BytesParser(policy=policy.default).parsebytes(data, headersonly=True)
            senders = getaddresses([email['sender']])
            sender = senders[0][1] if len(senders) == 1 else ''
            thread_id = result.get('threadId')
            rfc_message_id = str(parsed.get('Message-ID', '')).strip()
            references = ' '.join(str(parsed.get('References', '')).split())
            if (not _EMAIL.fullmatch(sender) or not isinstance(thread_id, str)
                    or not _ID.fullmatch(thread_id)
                    or not re.fullmatch(r'<[^<>\s]+>', rfc_message_id)):
                raise ValueError
        except (ValueError, TypeError):
            raise GmailError('gmail_message_invalid') from None
        return {'sender': sender, 'subject': email['subject'], 'body': email['body'],
                'email_sha256': email['email_sha256'], 'thread_id': thread_id,
                'message_id': rfc_message_id, 'references': references,
                'raw': data}

    def send_reply(self, access_token, recipient, subject, body, thread_id,
                   message_id, references, *, approved=False):
        """Send exactly the caller-approved reply; no implicit approval."""
        if approved is not True:
            raise GmailError('send_approval_required')
        if not isinstance(recipient, str) or not _EMAIL.fullmatch(recipient):
            raise GmailError('recipient_invalid')
        _text(subject, 'subject', limit=998)
        if not isinstance(body, str) or not body or len(body) > 100000:
            raise GmailError('body_invalid')
        if not isinstance(thread_id, str) or not _ID.fullmatch(thread_id):
            raise GmailError('thread_id_invalid')
        _text(message_id, 'message_id', limit=4000)
        if not isinstance(references, str) or len(references) > 4000 or any(ord(c) in {10, 13, 127} for c in references):
            raise GmailError('references_invalid')
        if not re.fullmatch(r'<[^<>\s]+>', message_id) or (references and not re.fullmatch(r'<[^<>\s]+>( <[^<>\s]+>)*', references)):
            raise GmailError('reply_headers_invalid')
        reference_chain = references.split() if references else []
        if not reference_chain or reference_chain[-1] != message_id:
            reference_chain.append(message_id)
        message = EmailMessage(policy=SMTP)
        message['From'] = self.profile(access_token)
        message['To'] = recipient
        message['Subject'] = subject
        message['In-Reply-To'] = message_id
        message['References'] = ' '.join(reference_chain)
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('ascii')
        result = self._request('POST', API_URL + '/messages/send', token=access_token,
                               json={'raw': raw, 'threadId': thread_id})
        if not isinstance(result.get('id'), str):
            raise GmailError('gmail_response_invalid')
        return {'id': result['id'], 'threadId': result.get('threadId')}
