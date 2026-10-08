"""Authenticated Gmail connection and approval-gated send without live Google traffic."""
from hashlib import sha256
import json
import time
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from fastapi.testclient import TestClient

from app.web_api import create_app, signature


SECRET = 'gmail-gateway-test-' + 'x' * 40


def call(api, method, path, body=None, user='worker-A'):
    encoded = b'' if body is None else json.dumps(body).encode()
    stamp, nonce = str(int(time.time())), uuid4().hex
    headers = {'X-RA-User': user, 'X-RA-Time': stamp, 'X-RA-Nonce': nonce,
               'X-RA-Signature': signature(SECRET, method, path, stamp, nonce, user, encoded)}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    return api.request(method, path, headers=headers, content=encoded)


class Google:
    def __init__(self):
        self.sent = []

    def authorization_url(self, state, challenge):
        assert len(challenge) == 43
        return 'https://accounts.google.com/o/oauth2/v2/auth?state=' + state

    def exchange_code(self, code, verifier):
        assert code == 'test-code' and len(verifier) >= 43
        return {'access_token': 'access', 'refresh_token': 'refresh-private'}

    def profile(self, token):
        assert token == 'access'
        return 'seller@example.test'

    def refresh(self, token):
        assert token == 'refresh-private'
        return {'access_token': 'access'}

    def list_inbox(self, token):
        assert token == 'access'
        return ['gmail-msg-1']

    def get_message(self, token, identifier):
        assert identifier == 'gmail-msg-1'
        body = 'Could you share your quotation?'
        return {'sender': 'buyer@example.test', 'subject': 'Quotation request',
                'body': body, 'email_sha256': sha256(body.encode()).hexdigest(),
                'thread_id': 'thread-1', 'message_id': '<test-1@example.test>',
                'references': ''}

    def send_reply(self, token, recipient, subject, body, thread_id, message_id,
                   references, *, approved):
        assert approved is True and token == 'access'
        assert thread_id == 'thread-1' and message_id == '<test-1@example.test>'
        self.sent.append((recipient, subject, body))
        return {'id': 'gmail-sent-1', 'threadId': thread_id}


class Model:
    def generate_json(self, name, payload):
        if name == 'buyer_email':
            return {'requests': [{'buyer_quote': payload['email']['body'],
                    'answer': 'Could you share the target quantity?', 'evidence': []}]}
        return {'complete': True, 'items': [{'index': 1, 'supported': True}]}


def test_gmail_connect_auto_draft_requires_exact_human_confirmation_to_send(tmp_path):
    google = Google()
    api = TestClient(create_app(root=tmp_path, secret=SECRET, client_factory=Model,
                                gmail_connector=google), raise_server_exceptions=False)
    status = call(api, 'GET', '/api/sales/mail/status').json()
    assert status['connected'] is False
    assert status['oauth_configured'] is True
    connected = call(api, 'POST', '/api/sales/mail/connect', {})
    assert connected.status_code == 200
    state = parse_qs(urlparse(connected.json()['authorization_url']).query)['state'][0]
    path = '/api/sales/mail/oauth/callback?code=test-code&state=' + state
    assert call(api, 'GET', path, user='worker-A').status_code == 403
    assert call(api, 'GET', path, user='oauth:callback').status_code == 200
    assert call(api, 'GET', path, user='oauth:callback').status_code == 200
    assert call(api, 'GET', '/api/sales/mail/status', user='worker-B').json()['connected'] is False
    assert call(api, 'GET', '/api/sales/mail/status').json()['address'] == 'seller@example.test'
    assert call(api, 'POST', '/api/sales/mail/sync', {}).status_code == 200
    for _ in range(100):
        inbox = call(api, 'GET', '/api/sales/mail/inbox').json()
        if inbox['messages'] and inbox['messages'][0]['state'] != 'drafting':
            break
        time.sleep(.02)
    assert len(inbox['messages']) == 1
    mail = inbox['messages'][0]
    assert mail['state'] == 'review_required'
    assert google.sent == []
    assert call(api, 'GET', '/api/sales/mail/inbox', user='worker-B').status_code == 422
    route = '/api/sales/mail/messages/gmail-msg-1/send'
    snapshot = {'revision': mail['revision'], 'fingerprint': mail['fingerprint'],
                'to': mail['recipient'], 'subject': mail['subject'],
                'body': mail['body'].replace('[Your name]', 'Kim'), 'confirmed': True}
    assert call(api, 'POST', route, {**snapshot, 'confirmed': False}).status_code == 422
    assert google.sent == []
    assert call(api, 'POST', route, snapshot, user='worker-B').status_code == 422
    assert google.sent == []
    sent = call(api, 'POST', route, snapshot)
    assert sent.status_code == 200, sent.text
    assert sent.json()['state'] == 'sent'
    assert google.sent == [(snapshot['to'], snapshot['subject'], snapshot['body'])]
    assert call(api, 'POST', route, snapshot).status_code == 422
    assert len(google.sent) == 1


def test_gmail_status_reports_missing_oauth_configuration(tmp_path, monkeypatch):
    for key in ('GMAIL_CLIENT_ID', 'GMAIL_CLIENT_SECRET', 'GMAIL_REDIRECT_URI'):
        monkeypatch.delenv(key, raising=False)
    api = TestClient(create_app(root=tmp_path, secret=SECRET, client_factory=Model),
                     raise_server_exceptions=False)
    status = call(api, 'GET', '/api/sales/mail/status').json()
    assert status['connected'] is False
    assert status['oauth_configured'] is False
    assert 'OAuth 설정' in status['reason']
    assert call(api, 'POST', '/api/sales/mail/connect', {}).status_code == 422
