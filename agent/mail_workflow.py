"""Persist incoming mail drafts and require an exact human approval before sending."""

from hashlib import sha256
from email.utils import getaddresses
from contextlib import contextmanager
import json
import re
import sqlite3


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode('utf-8')).hexdigest()


def _address(value):
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise ValueError('수신인 주소를 확인해야 함')
    addresses = getaddresses([value])
    if len(addresses) != 1:
        raise ValueError('수신인은 이메일 주소 하나이어야 함')
    address = addresses[0][1]
    if not re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+', address):
        raise ValueError('수신인 주소를 확인해야 함')
    return address


def _content(recipient, subject, body, *, sending=False):
    recipient = _address(recipient)
    if (not isinstance(subject, str) or not 0 < len(subject.strip()) <= 300
            or any(ord(char) < 32 for char in subject)):
        raise ValueError('메일 제목을 확인해야 함')
    if (not isinstance(body, str) or not 0 < len(body.strip()) <= 30_000
            or '\x00' in body or (sending and re.search(
                r'\[(?:Your name|보내는 사람 이름)\]', body))):
        raise ValueError('메일 본문과 서명을 확인해야 함')
    return recipient, subject, body


def _identity(owner_id, mailbox_id, incoming_id):
    if any(not isinstance(value, str) or not value.strip() or len(value) > 300
           for value in (owner_id, mailbox_id, incoming_id)):
        raise ValueError('사용자·메일함·수신 메일 ID를 확인해야 함')
    return owner_id, mailbox_id, incoming_id


class MailWorkflow:
    """compose(incoming)->reviewed draft; send_mail(**snapshot)->provider message ID.

    A claimed send is never retried automatically: without a provider-side
    idempotency guarantee, a lost response could otherwise send twice.
    """

    def __init__(self, db_path, compose, send_mail):
        self.db_path = str(db_path)
        self.compose = compose
        self.send_mail = send_mail
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS mail_workflow (
                owner_id TEXT NOT NULL, mailbox_id TEXT NOT NULL,
                incoming_id TEXT NOT NULL, incoming_json TEXT NOT NULL,
                state TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                recipient TEXT, subject TEXT, body TEXT, draft_fingerprint TEXT,
                fingerprint TEXT, review_json TEXT, draft_json TEXT,
                provider_message_id TEXT,
                PRIMARY KEY (owner_id, mailbox_id, incoming_id))''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _view(row):
        if row is None:
            raise KeyError('메일을 찾을 수 없음')
        return {'owner_id': row['owner_id'], 'mailbox_id': row['mailbox_id'],
                'incoming_id': row['incoming_id'], 'incoming': json.loads(row['incoming_json']),
                'state': row['state'], 'revision': row['revision'],
                'recipient': row['recipient'], 'subject': row['subject'], 'body': row['body'],
                'draft_fingerprint': row['draft_fingerprint'], 'fingerprint': row['fingerprint'],
                'review': json.loads(row['review_json']) if row['review_json'] else None,
                'draft': json.loads(row['draft_json']) if row['draft_json'] else None,
                'provider_message_id': row['provider_message_id']}

    def get(self, owner_id, mailbox_id, incoming_id):
        key = _identity(owner_id, mailbox_id, incoming_id)
        with self._db() as db:
            return self._view(db.execute('''SELECT * FROM mail_workflow WHERE owner_id=?
                AND mailbox_id=? AND incoming_id=?''', key).fetchone())

    def list_inbox(self, owner_id, mailbox_id, limit=100):
        _identity(owner_id, mailbox_id, '_')
        if not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError('조회 건수는 1~500이어야 함')
        with self._db() as db:
            rows = db.execute('''SELECT * FROM mail_workflow WHERE owner_id=?
                AND mailbox_id=? ORDER BY rowid DESC LIMIT ?''', (owner_id, mailbox_id, limit))
            return [self._view(row) for row in rows]

    def receive(self, owner_id, mailbox_id, incoming_id, incoming):
        """Register one provider message and compose only on first receipt/failure retry."""
        key = _identity(owner_id, mailbox_id, incoming_id)
        if (not isinstance(incoming, dict) or not isinstance(incoming.get('body'), str)
                or not incoming['body'].strip() or len(incoming['body']) > 30_000):
            raise ValueError('수신 메일 본문을 확인해야 함')
        recipient = _address(incoming.get('sender'))
        stored = {field: incoming.get(field, '') for field in
                  ('sender', 'subject', 'body', 'email_sha256', 'thread_id',
                   'message_id', 'references')}
        if any(not isinstance(stored[field], str) or len(stored[field]) > 2_000
               or any(ord(char) < 32 for char in stored[field])
               for field in ('subject', 'email_sha256', 'thread_id', 'message_id',
                             'references')):
            raise ValueError('수신 메일 머리글을 확인해야 함')
        stored['sender'] = recipient
        stored['body_sha256'] = sha256(stored['body'].encode('utf-8')).hexdigest()
        incoming_json = json.dumps(stored, ensure_ascii=False, sort_keys=True)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT * FROM mail_workflow WHERE owner_id=? AND mailbox_id=?
                AND incoming_id=?''', key).fetchone()
            if row:
                if row['incoming_json'] != incoming_json:
                    raise ValueError('같은 수신 ID에 다른 원문이 들어옴')
                if row['state'] != 'draft_failed':
                    return self._view(row)
                db.execute('''UPDATE mail_workflow SET state='drafting' WHERE owner_id=?
                    AND mailbox_id=? AND incoming_id=?''', key)
            else:
                db.execute('''INSERT INTO mail_workflow
                    (owner_id, mailbox_id, incoming_id, incoming_json, state)
                    VALUES (?, ?, ?, ?, 'drafting')''', (*key, incoming_json))
        try:
            result = self.compose(dict(stored))
            if (not isinstance(result, dict) or result.get('status') != 'review_required'
                    or not isinstance(result.get('fingerprint'), str)
                    or not result['fingerprint'] or not isinstance(result.get('review'), dict)):
                raise ValueError('검수된 초안 결과가 필요함')
            recipient, subject, body = _content(recipient, result.get('subject'), result.get('email'))
            fingerprint = _digest((key, stored, result, recipient, subject, body))
            with self._db() as db:
                db.execute('''UPDATE mail_workflow SET state='review_required', revision=revision+1,
                    recipient=?, subject=?, body=?, draft_fingerprint=?, fingerprint=?,
                    review_json=?, draft_json=?
                    WHERE owner_id=? AND mailbox_id=? AND incoming_id=? AND state='drafting' ''',
                    (recipient, subject, body, result['fingerprint'], fingerprint,
                     json.dumps(result['review'], ensure_ascii=False),
                     json.dumps(result, ensure_ascii=False), *key))
        except Exception:
            with self._db() as db:
                db.execute('''UPDATE mail_workflow SET state='draft_failed' WHERE owner_id=?
                    AND mailbox_id=? AND incoming_id=? AND state='drafting' ''', key)
            raise
        return self.get(*key)

    def edit(self, owner_id, mailbox_id, incoming_id, *, expected_revision,
             recipient, subject, body):
        key = _identity(owner_id, mailbox_id, incoming_id)
        recipient, subject, body = _content(recipient, subject, body)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT * FROM mail_workflow WHERE owner_id=? AND mailbox_id=?
                AND incoming_id=?''', key).fetchone()
            self._view(row)
            if row['state'] not in {'review_required', 'approved'} or row['revision'] != expected_revision:
                raise ValueError('초안 상태 또는 수정 버전이 변경됨')
            if (recipient, subject, body) == (row['recipient'], row['subject'], row['body']):
                return self._view(row)
            revision = row['revision'] + 1
            fingerprint = _digest((key, row['incoming_json'], row['draft_fingerprint'],
                                   recipient, subject, body, revision))
            db.execute('''UPDATE mail_workflow SET state='review_required', revision=?,
                recipient=?, subject=?, body=?, fingerprint=? WHERE owner_id=? AND mailbox_id=?
                AND incoming_id=?''', (revision, recipient, subject, body, fingerprint, *key))
        return self.get(*key)

    def confirm_and_send(self, owner_id, mailbox_id, incoming_id, *, expected_revision,
                         fingerprint, confirmed_recipient, confirmed_subject,
                         confirmed_body, facts_confirmed):
        """Approve an exact snapshot and claim the send once, including across workers."""
        key = _identity(owner_id, mailbox_id, incoming_id)
        if facts_confirmed is not True:
            raise ValueError('본문의 근거·거래조건을 담당자가 확인해야 함')
        confirmed = _content(confirmed_recipient, confirmed_subject, confirmed_body,
                             sending=True)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT * FROM mail_workflow WHERE owner_id=? AND mailbox_id=?
                AND incoming_id=?''', key).fetchone()
            self._view(row)
            if (row['state'] not in {'review_required', 'approved'}
                    or row['revision'] != expected_revision or row['fingerprint'] != fingerprint
                    or confirmed != (row['recipient'], row['subject'], row['body'])):
                raise ValueError('확인한 초안과 현재 발송 내용이 다름')
            db.execute('''UPDATE mail_workflow SET state='sending' WHERE owner_id=?
                AND mailbox_id=? AND incoming_id=?''', key)
        send_key = _digest((key, expected_revision, fingerprint))
        try:
            provider_id = self.send_mail(mailbox_id=mailbox_id, incoming_id=incoming_id,
                                         incoming=json.loads(row['incoming_json']),
                                         recipient=confirmed[0],
                                         subject=confirmed[1], body=confirmed[2],
                                         idempotency_key=send_key)
            if not isinstance(provider_id, str) or not provider_id:
                raise ValueError('발송 확인 ID가 없음')
        except Exception:
            with self._db() as db:
                db.execute('''UPDATE mail_workflow SET state='send_uncertain' WHERE owner_id=?
                    AND mailbox_id=? AND incoming_id=? AND state='sending' ''', key)
            raise
        with self._db() as db:
            db.execute('''UPDATE mail_workflow SET state='sent', provider_message_id=?
                WHERE owner_id=? AND mailbox_id=? AND incoming_id=? AND state='sending' ''',
                (provider_id, *key))
        return self.get(*key)
