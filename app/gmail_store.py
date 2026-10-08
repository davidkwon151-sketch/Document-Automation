"""Encrypted, tenant-scoped Gmail connection and one-use OAuth state storage."""
from base64 import urlsafe_b64encode
from hashlib import sha256
import hmac
import secrets
import sqlite3
import time

from cryptography.fernet import Fernet, InvalidToken


class GmailStore:
    def __init__(self, path, secret):
        if not isinstance(secret, str) or len(secret) < 32:
            raise ValueError('메일 연결 암호화 비밀값이 필요함')
        key = hmac.new(secret.encode(), b'gmail-refresh-token-v1', sha256).digest()
        self.cipher = Fernet(urlsafe_b64encode(key))
        self.path = str(path)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS gmail_oauth_state (
                state_hash TEXT PRIMARY KEY, owner_hash TEXT NOT NULL,
                verifier TEXT NOT NULL, expires_at REAL NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS gmail_connections (
                owner_hash TEXT PRIMARY KEY, email TEXT NOT NULL,
                refresh_token BLOB NOT NULL, connected_at REAL NOT NULL)''')

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    @staticmethod
    def owner_hash(owner):
        if not isinstance(owner, str) or not owner:
            raise ValueError('사용자 식별이 필요함')
        return sha256(owner.encode()).hexdigest()

    def begin(self, owner):
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        owner_hash = self.owner_hash(owner)
        with self._db() as db:
            db.execute('DELETE FROM gmail_oauth_state WHERE expires_at < ?', (time.time(),))
            db.execute('INSERT INTO gmail_oauth_state VALUES (?, ?, ?, ?)',
                       (sha256(state.encode()).hexdigest(), owner_hash, verifier, time.time() + 600))
        return state, verifier

    def consume(self, state):
        if not isinstance(state, str) or len(state) > 200:
            raise ValueError('메일 연결 상태가 유효하지 않음')
        with self._db() as db:
            row = db.execute('SELECT owner_hash, verifier, expires_at FROM gmail_oauth_state WHERE state_hash=?',
                             (sha256(state.encode()).hexdigest(),)).fetchone()
            db.execute('DELETE FROM gmail_oauth_state WHERE state_hash=?',
                       (sha256(state.encode()).hexdigest(),))
        if row is None or row[2] < time.time():
            raise ValueError('메일 연결 요청이 만료되었거나 이미 사용됨')
        return row[0], row[1]

    def save(self, owner_hash, email, refresh_token):
        if (not isinstance(owner_hash, str) or len(owner_hash) != 64
                or not isinstance(email, str) or '@' not in email or len(email) > 320
                or not isinstance(refresh_token, str) or not refresh_token):
            raise ValueError('Gmail 연결 정보를 확인해야 함')
        encrypted = self.cipher.encrypt(refresh_token.encode())
        with self._db() as db:
            db.execute('''INSERT INTO gmail_connections VALUES (?, ?, ?, ?)
                ON CONFLICT(owner_hash) DO UPDATE SET email=excluded.email,
                refresh_token=excluded.refresh_token, connected_at=excluded.connected_at''',
                (owner_hash, email, encrypted, time.time()))

    def get(self, owner):
        return self.get_hash(self.owner_hash(owner))

    def get_hash(self, owner_hash):
        with self._db() as db:
            row = db.execute('SELECT email, refresh_token FROM gmail_connections WHERE owner_hash=?',
                             (owner_hash,)).fetchone()
        if row is None:
            return None
        try:
            return {'email': row[0], 'refresh_token': self.cipher.decrypt(row[1]).decode()}
        except (InvalidToken, UnicodeError) as exc:
            raise ValueError('Gmail 연결을 다시 설정해야 함') from exc

    def owners(self):
        with self._db() as db:
            return [row[0] for row in db.execute('SELECT owner_hash FROM gmail_connections')]

    def disconnect(self, owner):
        with self._db() as db:
            db.execute('DELETE FROM gmail_connections WHERE owner_hash=?', (self.owner_hash(owner),))
