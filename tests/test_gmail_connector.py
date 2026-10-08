import base64
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.gmail_connector import GmailConnector, GmailError, SCOPES


def connector(handler):
    return GmailConnector('client-id', 'client-secret', 'https://app.example/oauth/callback',
                          transport=httpx.MockTransport(handler))


def test_authorization_uses_google_host_state_pkce_offline_and_narrow_scopes():
    request = connector(lambda _: pytest.fail('network not expected')).authorization_request()
    url = urlsplit(request['url'])
    query = parse_qs(url.query)
    assert (url.scheme, url.netloc, url.path) == ('https', 'accounts.google.com', '/o/oauth2/v2/auth')
    assert query['scope'] == [' '.join(SCOPES)]
    assert query['access_type'] == ['offline'] and query['code_challenge_method'] == ['S256']
    assert query['state'] == [request['state']]
    assert request['code_verifier'] not in request['url']


def test_code_exchange_checks_state_and_refresh_uses_fixed_token_host():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'access_token': 'access', 'refresh_token': 'refresh',
                                         'expires_in': 3600, 'token_type': 'Bearer', 'extra': 'omit'})

    gmail = connector(handler)
    assert gmail.exchange_code('code', 'verifier') == {
        'access_token': 'access', 'refresh_token': 'refresh', 'expires_in': 3600}
    assert gmail.refresh('refresh')['access_token'] == 'access'
    assert all(str(item.url) == 'https://oauth2.googleapis.com/token' for item in seen)
    assert b'code_verifier=verifier' in seen[0].content
    assert b'grant_type=refresh_token' in seen[1].content


def test_profile_inbox_and_raw_message_are_read_only():
    original = (b'From: Sender <sender@example.com>\r\nSubject: Hello\r\n'
                b'Message-ID: <original@example.com>\r\nReferences: <first@example.com>\r\n\r\nText\r\n')
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith('/profile'):
            return httpx.Response(200, json={'emailAddress': 'owner@example.com'})
        if request.url.path.endswith('/messages'):
            return httpx.Response(200, json={'messages': [{'id': 'm_1', 'threadId': 't_1'}]})
        return httpx.Response(200, json={'id': 'm_1', 'threadId': 't_1', 'labelIds': ['INBOX'],
                                         'raw': base64.urlsafe_b64encode(original).decode().rstrip('=')})

    gmail = connector(handler)
    assert gmail.profile('access') == 'owner@example.com'
    assert gmail.list_inbox('access', unread_only=True) == ['m_1']
    message = gmail.get_message('access', 'm_1')
    assert message['raw'] == original and message['sender'] == 'sender@example.com'
    assert message['thread_id'] == 't_1' and message['message_id'] == '<original@example.com>'
    assert message['references'] == '<first@example.com>'
    assert all(request.method == 'GET' and request.url.host == 'gmail.googleapis.com' for request in seen)
    assert set(seen[1].url.params.get_list('labelIds')) == {'INBOX', 'UNREAD'}
    assert seen[2].url.params['format'] == 'raw'


def test_send_reply_requires_approval_and_preserves_reviewed_fields():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith('/profile'):
            return httpx.Response(200, json={'emailAddress': 'owner@example.com'})
        return httpx.Response(200, json={'id': 'sent-1', 'threadId': 'thread-1'})

    gmail = connector(handler)
    fields = {'recipient': 'buyer@example.com', 'subject': 'Re: 견적 요청', 'body': '가격은 확인 후 안내하겠습니다.',
              'thread_id': 'thread-1', 'message_id': '<original@example.com>',
              'references': '<first@example.com> <original@example.com>'}
    with pytest.raises(GmailError, match='send_approval_required'):
        gmail.send_reply('access', **fields)
    assert not seen
    assert gmail.send_reply('access', approved=True, **fields) == {'id': 'sent-1', 'threadId': 'thread-1'}
    assert seen[0].url.path.endswith('/profile')
    assert seen[1].method == 'POST' and seen[1].url.path.endswith('/messages/send')
    import json
    payload = json.loads(seen[1].content)
    assert payload['threadId'] == fields['thread_id']
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(payload['raw']))
    assert message['From'] == 'owner@example.com'
    assert message['To'] == fields['recipient'] and message['Subject'] == fields['subject']
    assert message['In-Reply-To'] == fields['message_id']
    assert message['References'] == fields['references']
    assert message.get_content().rstrip('\r\n') == fields['body']


def test_provider_errors_and_redirects_do_not_leak_secrets():
    secret = 'private-token'
    for response in (httpx.Response(400, json={'error': secret}),
                     httpx.Response(302, headers={'location': 'https://attacker.example/collect'})):
        gmail = connector(lambda _, response=response: response)
        with pytest.raises(GmailError) as error:
            gmail.profile(secret)
        assert secret not in str(error.value)
    gmail = connector(lambda request: httpx.Response(200, json={'emailAddress': 'owner@example.com'}))
    with pytest.raises(GmailError, match='message_id_invalid'):
        gmail.get_message(secret, '../profile')
