"""Owner-issued MCP capabilities; secrets are returned once, only hashes persist.

The authenticated workspace, never a browser-supplied owner ID, calls issue.
Guest credentials remain bound to their invitation's generation and expiry.
"""
from contextlib import closing, contextmanager
from hashlib import sha256
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from uuid import uuid4

from app.guest_access import TOKEN, IDENTIFIER, MAX_INVITE_TTL


MAX_ACTIVE_PER_OWNER = 20
MAX_CREDENTIALS = 10_000
OWNER = re.compile(r'^[A-Za-z0-9_.:@-]{1,200}$')


class MCPAccessError(ValueError):
    """Constant codes only, including for malformed supplied secrets."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _owner(user_id):
    if (not isinstance(user_id, str) or not OWNER.fullmatch(user_id)
            or user_id.startswith(('mcp:', 'invite:'))):
        raise MCPAccessError('mcp_owner_invalid')
    return user_id


def _digest(token):
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise MCPAccessError('mcp_access_invalid')
    return sha256(token.encode('ascii')).hexdigest()


class MCPAccess:
    def __init__(self, root, guest_access=None):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.database = root / '.mcp-access.sqlite3'
        self.guests = guest_access
        with self._connect() as database:
            database.executescript('''
                CREATE TABLE IF NOT EXISTS credentials (
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL, label TEXT NOT NULL,
                    expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL,
                    revoked_at INTEGER, parent_invite_id TEXT, parent_generation TEXT
                );
                CREATE INDEX IF NOT EXISTS credentials_owner ON credentials(user_id);
            ''')
        os.chmod(self.database, 0o600)

    @contextmanager
    def _connect(self):
        database = sqlite3.connect(self.database, timeout=15)
        database.row_factory = sqlite3.Row
        try:
            with database:
                yield database
        finally:
            database.close()

    def _parent(self, user_id, parent_invite_id=None):
        if not user_id.startswith('guest:'):
            if parent_invite_id is not None:
                raise MCPAccessError('mcp_parent_invalid')
            return None
        identifier = user_id[6:]
        if (not IDENTIFIER.fullmatch(identifier) or self.guests is None
                or parent_invite_id not in (None, identifier)):
            raise MCPAccessError('mcp_parent_invalid')
        # Read the existing invitation store; do not create one or depend on a
        # short-lived browser session. Renewal changes token_hash even in the
        # same clock second, invalidating every prior connector capability.
        try:
            with closing(sqlite3.connect(self.guests.database.resolve().as_uri() + '?mode=ro', uri=True)) as database:
                database.row_factory = sqlite3.Row
                parent = database.execute('''
                    SELECT id, token_hash, expires_at FROM invites
                    WHERE id = ? AND user_id = ? AND used_at IS NOT NULL
                      AND revoked_at IS NULL AND expires_at > ?
                ''', (identifier, user_id, int(time.time()))).fetchone()
        except sqlite3.Error:
            raise MCPAccessError('mcp_parent_invalid') from None
        if parent is None:
            raise MCPAccessError('mcp_parent_invalid')
        return dict(parent)

    def issue(self, user_id, label='Claude connector', ttl=86400, *, parent_invite_id=None):
        """Explicit authenticated owner action. Return a reusable token once."""
        _owner(user_id)
        if (not isinstance(label, str) or not 1 <= len(label) <= 120 or not label.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in label)):
            raise MCPAccessError('mcp_label_invalid')
        try:
            label.encode('utf-8')
        except UnicodeError:
            raise MCPAccessError('mcp_label_invalid') from None
        if type(ttl) is not int or not 60 <= ttl <= MAX_INVITE_TTL:
            raise MCPAccessError('mcp_expiry_invalid')
        parent = self._parent(user_id, parent_invite_id)
        now, token, identifier = int(time.time()), secrets.token_urlsafe(32), uuid4().hex
        expiry = min(now + ttl, parent['expires_at']) if parent else now + ttl
        with self._connect() as database:
            database.execute('BEGIN IMMEDIATE')
            database.execute('DELETE FROM credentials WHERE expires_at <= ? OR revoked_at IS NOT NULL', (now,))
            if database.execute('SELECT count(*) FROM credentials WHERE user_id = ?', (user_id,)).fetchone()[0] >= MAX_ACTIVE_PER_OWNER:
                raise MCPAccessError('mcp_credential_limit')
            if database.execute('SELECT count(*) FROM credentials').fetchone()[0] >= MAX_CREDENTIALS:
                raise MCPAccessError('mcp_credential_limit')
            database.execute('INSERT INTO credentials VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)',
                             (identifier, _digest(token), user_id, label.strip(), expiry, now,
                              parent['id'] if parent else None, parent['token_hash'] if parent else None))
        return {'credential_id': identifier, 'token': token, 'label': label.strip(), 'expires_at': expiry}

    def resolve(self, token):
        """Revalidate capability and guest parent for every tool and download."""
        with self._connect() as database:
            row = database.execute('''
                SELECT * FROM credentials WHERE token_hash = ? AND expires_at > ?
                  AND revoked_at IS NULL
            ''', (_digest(token), int(time.time()))).fetchone()
        if row is None:
            raise MCPAccessError('mcp_access_invalid')
        try:
            parent = self._parent(row['user_id'], row['parent_invite_id'])
        except MCPAccessError:
            raise MCPAccessError('mcp_access_invalid') from None
        if parent and parent['token_hash'] != row['parent_generation']:
            raise MCPAccessError('mcp_access_invalid')
        return {'credential_id': row['id'], 'user_id': row['user_id'],
                'label': row['label'], 'expires_at': row['expires_at']}

    def list_for(self, user_id):
        """Only safe metadata; never return a token or hash to the workspace."""
        _owner(user_id)
        with self._connect() as database:
            rows = database.execute('''
                SELECT id AS credential_id, label, created_at, expires_at, revoked_at
                FROM credentials WHERE user_id = ? ORDER BY created_at DESC, id
            ''', (user_id,)).fetchall()
        return [dict(row) for row in rows]

    def revoke(self, user_id, credential_id):
        """An owner cannot inspect or revoke another owner's capability."""
        _owner(user_id)
        if not isinstance(credential_id, str) or not IDENTIFIER.fullmatch(credential_id):
            raise MCPAccessError('mcp_credential_invalid')
        with self._connect() as database:
            changed = database.execute('''
                UPDATE credentials SET revoked_at = ?
                WHERE id = ? AND user_id = ? AND revoked_at IS NULL
            ''', (int(time.time()), credential_id, user_id)).rowcount
        return bool(changed)
