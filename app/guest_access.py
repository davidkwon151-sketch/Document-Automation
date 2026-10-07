"""Operator-issued, one-use invitations; no public registration or shared identity.

Only opaque token hashes are persisted. The authenticated gateway must resolve
the session on every request, including downloads; browser identities are ignored.
"""
import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
from uuid import uuid4


MAX_INVITE_TTL = 7 * 24 * 3600
MAX_SESSION_TTL = 24 * 3600
MAX_ACTIVE_INVITES = 1000  # Total retained identities, including expired/revoked invites.
TOKEN = re.compile(r'^[A-Za-z0-9_-]{43}$')
IDENTIFIER = re.compile(r'^[0-9a-f]{32}$')


class GuestAccessError(ValueError):
    """Safe, constant error codes: never include a supplied secret."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _token_hash(token):
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise GuestAccessError('guest_access_invalid')
    return sha256(token.encode('ascii')).hexdigest()


def _validate_ttl(ttl):
    if type(ttl) is not int or not 60 <= ttl <= MAX_INVITE_TTL:
        raise GuestAccessError('guest_expiry_invalid')


class GuestAccess:
    def __init__(self, root):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.database = root / '.guest-access.sqlite3'
        with self._connect() as database:
            database.executescript('''
                CREATE TABLE IF NOT EXISTS invites (
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                    label TEXT NOT NULL, user_id TEXT UNIQUE NOT NULL,
                    expires_at INTEGER NOT NULL, used_at INTEGER, revoked_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    invite_id TEXT NOT NULL REFERENCES invites(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_invite ON sessions(invite_id);
            ''')
        # Windows permissions inherit the operator's private data-root ACL.
        os.chmod(self.database, 0o600)

    @contextmanager
    def _connect(self):
        database = sqlite3.connect(self.database, timeout=15)
        database.row_factory = sqlite3.Row
        database.execute('PRAGMA foreign_keys = ON')
        try:
            with database:
                yield database
        finally:
            database.close()

    def issue(self, label, ttl=86400):
        """Local operator only. Return the invitation secret exactly once."""
        if (not isinstance(label, str) or not 1 <= len(label) <= 120 or not label.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in label)):
            raise GuestAccessError('guest_label_invalid')
        try:
            label.encode('utf-8')
        except UnicodeError as error:
            raise GuestAccessError('guest_label_invalid') from error
        _validate_ttl(ttl)
        now, identifier, token = int(time.time()), uuid4().hex, secrets.token_urlsafe(32)
        expiry = now + ttl
        with self._connect() as database:
            database.execute('BEGIN IMMEDIATE')
            database.execute('DELETE FROM sessions WHERE expires_at <= ?', (now,))
            # Keep identity rows for explicit local renewal and existing workspaces.
            if database.execute('SELECT count(*) FROM invites').fetchone()[0] >= MAX_ACTIVE_INVITES:
                raise GuestAccessError('guest_invitation_limit')
            database.execute('INSERT INTO invites VALUES (?, ?, ?, ?, ?, NULL, NULL)',
                             (identifier, _token_hash(token), label.strip(),
                              'guest:' + identifier, expiry))
        return {'invite_id': identifier, 'token': token, 'label': label.strip(),
                'expires_at': expiry}

    def renew(self, invite_id, ttl=86400):
        """Local operator only: replace access secrets without changing identity.

        Expired/revoked invitations remain renewable. Renewal invalidates every
        prior session and the old one-use invitation token in the same transaction.
        """
        if not isinstance(invite_id, str) or not IDENTIFIER.fullmatch(invite_id):
            raise GuestAccessError('guest_invitation_invalid')
        _validate_ttl(ttl)
        now, token = int(time.time()), secrets.token_urlsafe(32)
        with self._connect() as database:
            database.execute('BEGIN IMMEDIATE')
            invite = database.execute('SELECT label FROM invites WHERE id = ?', (invite_id,)).fetchone()
            if invite is None:
                raise GuestAccessError('guest_invitation_invalid')
            database.execute('DELETE FROM sessions WHERE expires_at <= ? OR invite_id = ?',
                             (now, invite_id))
            database.execute('UPDATE invites SET token_hash = ?, expires_at = ?, used_at = NULL, revoked_at = NULL WHERE id = ?',
                             (_token_hash(token), now + ttl, invite_id))
        return {'invite_id': invite_id, 'token': token, 'label': invite['label'],
                'expires_at': now + ttl}

    def redeem(self, token):
        """Atomic one-use redemption, also across gateway/server processes."""
        digest, now = _token_hash(token), int(time.time())
        session = secrets.token_urlsafe(32)
        with self._connect() as database:
            database.execute('BEGIN IMMEDIATE')
            invite = database.execute('SELECT * FROM invites WHERE token_hash = ? AND expires_at > ? AND used_at IS NULL AND revoked_at IS NULL',
                                      (digest, now)).fetchone()
            if invite is None:
                raise GuestAccessError('guest_access_invalid')
            expiry = min(invite['expires_at'], now + MAX_SESSION_TTL)
            database.execute('UPDATE invites SET used_at = ? WHERE id = ?', (now, invite['id']))
            database.execute('INSERT INTO sessions VALUES (?, ?, ?)',
                             (_token_hash(session), invite['id'], expiry))
        return {'session': session, 'user_id': invite['user_id'],
                'invite_id': invite['id'], 'label': invite['label'], 'expires_at': expiry}

    def resolve(self, session):
        """Revalidate revocation and both expiries on every protected operation."""
        digest, now = _token_hash(session), int(time.time())
        with self._connect() as database:
            invite = database.execute('''
                SELECT i.id AS invite_id, i.user_id, i.label, s.expires_at
                FROM sessions s JOIN invites i ON i.id = s.invite_id
                WHERE s.token_hash = ? AND s.expires_at > ?
                  AND i.expires_at > ? AND i.revoked_at IS NULL AND i.used_at IS NOT NULL
            ''', (digest, now, now)).fetchone()
        if invite is None:
            raise GuestAccessError('guest_access_invalid')
        return dict(invite)

    def revoke(self, invite_id):
        """Local operator only. Invalidate its session immediately."""
        if not isinstance(invite_id, str) or not IDENTIFIER.fullmatch(invite_id):
            raise GuestAccessError('guest_invitation_invalid')
        with self._connect() as database:
            database.execute('BEGIN IMMEDIATE')
            changed = database.execute('UPDATE invites SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL',
                                       (int(time.time()), invite_id)).rowcount
            database.execute('DELETE FROM sessions WHERE invite_id = ?', (invite_id,))
        return bool(changed)

    def end(self, session):
        """Explicit logout invalidates the capability, not just its browser cookie."""
        with self._connect() as database:
            changed = database.execute('DELETE FROM sessions WHERE token_hash = ?',
                                       (_token_hash(session),)).rowcount
        return bool(changed)


def issue_to_file(access, label, ttl, site, output, *, invite_id=None):
    """Write the capability fragment to a NEW .runtime file; never stdout.

    Fragments are not sent in HTTP URLs. The browser exchanges it via a protected
    POST, removes it from history, and stores the session in an HttpOnly cookie.
    """
    parsed = urlsplit(site)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {'', '/'}):
        raise GuestAccessError('guest_site_invalid')
    private = (Path.cwd() / '.runtime').resolve()
    output = Path(output).resolve()
    if not output.is_relative_to(private) or output == private:
        raise GuestAccessError('guest_output_must_be_private')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve before issuing, so an existing file never consumes an invitation.
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    invitation = None
    try:
        invitation = (access.renew(invite_id, ttl) if invite_id is not None
                      else access.issue(label, ttl))
        record = {'invite_id': invitation['invite_id'], 'label': invitation['label'],
                  'expires_at': invitation['expires_at'],
                  'url': site.rstrip('/') + '/access#token=' + invitation['token']}
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            descriptor = None
            json.dump(record, stream, ensure_ascii=False, indent=2)
        return {'invite_id': invitation['invite_id'], 'output': str(output)}
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        if invitation is not None:
            access.revoke(invitation['invite_id'])
        output.unlink(missing_ok=True)
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def renew_to_file(access, invite_id, ttl, site, output):
    return issue_to_file(access, None, ttl, site, output, invite_id=invite_id)


def main():
    parser = argparse.ArgumentParser(description='Local operator: issue/renew/revoke private RA invitation links.')
    parser.add_argument('--root', default=os.getenv('RA_WEB_DATA_ROOT', 'data/external-users'))
    commands = parser.add_subparsers(dest='command', required=True)
    issue = commands.add_parser('issue')
    issue.add_argument('--label', required=True)
    issue.add_argument('--site', required=True)
    issue.add_argument('--ttl', type=int, default=86400)
    issue.add_argument('--output', required=True, help='New JSON file inside .runtime/')
    renew = commands.add_parser('renew')
    renew.add_argument('invite_id')
    renew.add_argument('--site', required=True)
    renew.add_argument('--ttl', type=int, default=86400)
    renew.add_argument('--output', required=True, help='New JSON file inside .runtime/')
    revoke = commands.add_parser('revoke')
    revoke.add_argument('invite_id')
    arguments = parser.parse_args()
    access = GuestAccess(arguments.root)
    try:
        if arguments.command == 'issue':
            result = issue_to_file(access, arguments.label, arguments.ttl, arguments.site,
                                   arguments.output)
        elif arguments.command == 'renew':
            result = renew_to_file(access, arguments.invite_id, arguments.ttl,
                                   arguments.site, arguments.output)
        else:
            result = {'revoked': access.revoke(arguments.invite_id)}
    except GuestAccessError as error:
        parser.exit(2, error.code + '\n')
    except OSError:
        parser.exit(2, 'guest_storage_error\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
