"""Gmail polling, reviewed reply creation, and approval-gated sending."""
from base64 import urlsafe_b64encode
from hashlib import sha256
import os
from threading import Event, Thread

from dotenv import dotenv_values

from agent.buyer_email import draft_buyer_reply, parse_buyer_email
from agent.mail_workflow import MailWorkflow
from app.gmail_store import GmailStore
from llm.client import LLMClient
from llm.client import PROJECT_ROOT


class GmailService:
    def __init__(self, root, secret, connector, *, model_factory=None):
        self.connector = connector
        self.store = GmailStore(root / '.gmail-connections.sqlite3', secret)
        self.injected_model = model_factory is not None
        self.model_factory = model_factory or (lambda: LLMClient(
            provider='gemini', model='gemini-3.5-flash', timeout=30, max_retries=0))
        self.workflow = MailWorkflow(root / '.gmail-workflow.sqlite3', self._compose, self._send)
        self._stop = Event()
        self._thread = None

    @staticmethod
    def owner(user):
        return sha256(user.encode()).hexdigest()

    def ready(self):
        return self.connector is not None and (self.injected_model or bool(
            os.environ.get('GEMINI_API_KEY') or dotenv_values(PROJECT_ROOT / '.env').get('GEMINI_API_KEY'))
        )

    def status(self, user):
        connection = self.store.get(user)
        return {'connected': connection is not None,
                'address': connection['email'] if connection else None,
                'automation_enabled': self.ready() and connection is not None,
                'reason': ('Gmail OAuth 설정이 필요합니다.' if self.connector is None else
                           '자동 초안용 서버 Gemini API 키가 필요합니다.' if not (self.injected_model or
                               os.environ.get('GEMINI_API_KEY') or
                               dotenv_values(PROJECT_ROOT / '.env').get('GEMINI_API_KEY')) else
                           None)}

    def authorization_url(self, user):
        if self.connector is None:
            raise ValueError('Gmail OAuth 설정이 필요함')
        state, verifier = self.store.begin(user)
        challenge = urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        return self.connector.authorization_url(state, challenge)

    def callback(self, code, state):
        if self.connector is None:
            raise ValueError('Gmail OAuth 설정이 필요함')
        owner_hash, verifier = self.store.consume(state)
        tokens = self.connector.exchange_code(code, verifier)
        email = self.connector.profile(tokens['access_token'])
        refresh = tokens.get('refresh_token')
        if not refresh:
            existing = self.store.get_hash(owner_hash)
            if existing and existing['email'] == email:
                refresh = existing['refresh_token']
        if not refresh:
            raise ValueError('Gmail 오프라인 접근 동의를 다시 진행해야 함')
        self.store.save(owner_hash, email, refresh)
        return email

    def disconnect(self, user):
        self.store.disconnect(user)

    def _access(self, owner_hash):
        connection = self.store.get_hash(owner_hash)
        if connection is None or self.connector is None:
            raise ValueError('Gmail 연결이 필요함')
        token = self.connector.refresh(connection['refresh_token'])
        return connection, token['access_token']

    def _compose(self, incoming):
        parsed = parse_buyer_email(incoming['body'])
        parsed['subject'] = incoming.get('subject', '')
        parsed['sender'] = incoming['sender']
        return draft_buyer_reply(parsed, [], self.model_factory(), language='en')

    def _send(self, *, mailbox_id, incoming_id, incoming, recipient, subject, body,
              idempotency_key):
        connection, token = self._access(mailbox_id)
        result = self.connector.send_reply(token, recipient, subject, body,
                                           incoming.get('thread_id', ''),
                                           incoming.get('message_id', ''),
                                           incoming.get('references', ''), approved=True)
        return result['id']

    def sync_owner_hash(self, owner_hash):
        if not self.ready():
            raise ValueError('Gmail 또는 자동 초안용 Gemini 연결 설정이 필요함')
        connection, token = self._access(owner_hash)
        ids = self.connector.list_inbox(token)
        processed, failed = 0, 0
        for message_id in ids:
            try:
                existing = self.workflow.get(owner_hash, owner_hash, message_id)
                if existing['state'] != 'draft_failed':
                    continue
            except KeyError:
                pass
            try:
                incoming = self.connector.get_message(token, message_id)
                self.workflow.receive(owner_hash, owner_hash, message_id, incoming)
                processed += 1
            except Exception:
                # One malformed mail or model failure must not suppress other inbox mail.
                failed += 1
        return {'checked': len(ids), 'processed': processed, 'failed': failed,
                'address': connection['email']}

    def sync(self, user):
        return self.sync_owner_hash(self.owner(user))

    def inbox(self, user):
        owner = self.owner(user)
        if self.store.get(user) is None:
            raise ValueError('Gmail 연결이 필요함')
        return self.workflow.list_inbox(owner, owner)

    def send(self, user, incoming_id, payload):
        owner = self.owner(user)
        if self.store.get(user) is None:
            raise ValueError('Gmail 연결이 필요함')
        if not isinstance(payload, dict) or set(payload) != {
                'revision', 'fingerprint', 'to', 'subject', 'body', 'confirmed'}:
            raise ValueError('발송 확인 값이 부족함')
        if payload['confirmed'] is not True:
            raise ValueError('발송 전 수신인·제목·본문 확인이 필요함')
        current = self.workflow.get(owner, owner, incoming_id)
        if (current['revision'] != payload['revision'] or
                current['fingerprint'] != payload['fingerprint']):
            raise ValueError('초안이 바뀌었습니다. 새 내용을 다시 확인해야 함')
        if (current['recipient'], current['subject'], current['body']) != (
                payload['to'], payload['subject'], payload['body']):
            current = self.workflow.edit(owner, owner, incoming_id,
                expected_revision=payload['revision'], recipient=payload['to'],
                subject=payload['subject'], body=payload['body'])
        return self.workflow.confirm_and_send(owner, owner, incoming_id,
            expected_revision=current['revision'], fingerprint=current['fingerprint'],
            confirmed_recipient=payload['to'], confirmed_subject=payload['subject'],
            confirmed_body=payload['body'], facts_confirmed=True)

    def start(self, interval=60):
        if self._thread is not None or not self.ready():
            return
        def poll():
            while not self._stop.wait(interval):
                for owner_hash in self.store.owners():
                    if self._stop.is_set():
                        return
                    try:
                        self.sync_owner_hash(owner_hash)
                    except Exception:
                        # A later poll or manual sync can recover. Never auto-send.
                        continue
        self._thread = Thread(target=poll, name='gmail-poll', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
