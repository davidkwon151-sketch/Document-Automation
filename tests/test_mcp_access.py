"""Offline owner isolation, token persistence and guest-generation security."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import sqlite3

import pytest

from app import mcp_access
from app.guest_access import GuestAccess
from app.mcp_access import MCPAccess, MCPAccessError


def test_account_credential_persists_only_hash_and_owner_metadata(tmp_path):
    access = MCPAccess(tmp_path)
    issued = access.issue('account:a', '내 Claude', 604800)
    assert len(issued['token']) == 43
    assert 'token' not in access.resolve(issued['token'])
    assert access.resolve(issued['token'])['user_id'] == 'account:a'
    assert MCPAccess(tmp_path).resolve(issued['token']) == access.resolve(issued['token'])
    assert issued['token'].encode() not in access.database.read_bytes()
    with sqlite3.connect(access.database) as database:
        assert database.execute('SELECT token_hash FROM credentials').fetchone()[0] == sha256(issued['token'].encode()).hexdigest()
    metadata = access.list_for('account:a')
    assert len(metadata) == 1
    assert set(metadata[0]) == {'credential_id', 'label', 'created_at', 'expires_at', 'revoked_at'}
    assert access.list_for('account:b') == []


def test_owner_revocation_immediate_repeated_resolution_and_token_domains(tmp_path):
    guests, access = GuestAccess(tmp_path), MCPAccess(tmp_path)
    invitation = guests.issue('담당자')
    guest = guests.redeem(invitation['token'])
    issued = access.issue('account:a')
    for _ in range(3):
        assert access.resolve(issued['token'])['user_id'] == 'account:a'
    assert not access.revoke('account:b', issued['credential_id'])
    for token in [invitation['token'], guest['session'], 'x' * 43]:
        with pytest.raises(MCPAccessError, match='^mcp_access_invalid$'):
            access.resolve(token)
    assert access.revoke('account:a', issued['credential_id'])
    assert not access.revoke('account:a', issued['credential_id'])
    with pytest.raises(MCPAccessError, match='^mcp_access_invalid$'):
        access.resolve(issued['token'])


def test_expiry_boundaries_and_pruning(tmp_path, monkeypatch):
    clock = [2_000_000_000]
    monkeypatch.setattr(mcp_access.time, 'time', lambda: clock[0])
    access = MCPAccess(tmp_path)
    issued = access.issue('owner', ttl=60)
    clock[0] += 59
    assert access.resolve(issued['token'])['user_id'] == 'owner'
    clock[0] += 1
    with pytest.raises(MCPAccessError):
        access.resolve(issued['token'])
    fresh = access.issue('owner')
    assert len(access.list_for('owner')) == 1
    assert access.list_for('owner')[0]['credential_id'] == fresh['credential_id']


def test_guest_parent_cap_expiry_revocation_and_same_second_renewal(tmp_path, monkeypatch):
    clock = [2_000_000_000]
    monkeypatch.setattr(mcp_access.time, 'time', lambda: clock[0])
    guests = GuestAccess(tmp_path)
    invitation = guests.issue('RA 담당자', ttl=120)
    identity = guests.redeem(invitation['token'])
    access = MCPAccess(tmp_path, guests)
    issued = access.issue(identity['user_id'], ttl=604800, parent_invite_id=invitation['invite_id'])
    assert issued['expires_at'] == invitation['expires_at']
    assert access.resolve(issued['token'])['user_id'] == identity['user_id']
    # Same clock second: token hash, rather than used_at timestamp, separates generations.
    renewed = guests.renew(invitation['invite_id'], ttl=120)
    new_session = guests.redeem(renewed['token'])
    assert new_session['user_id'] == identity['user_id']
    with pytest.raises(MCPAccessError):
        access.resolve(issued['token'])
    newer = access.issue(identity['user_id'])
    assert access.resolve(newer['token'])['user_id'] == identity['user_id']
    guests.revoke(invitation['invite_id'])
    with pytest.raises(MCPAccessError):
        access.resolve(newer['token'])
    with pytest.raises(MCPAccessError):
        access.issue(identity['user_id'])


def test_guest_parent_requires_redeemed_matching_identity_and_store(tmp_path):
    guests = GuestAccess(tmp_path)
    a, b = guests.issue('A'), guests.issue('B')
    access = MCPAccess(tmp_path, guests)
    user = 'guest:' + a['invite_id']
    with pytest.raises(MCPAccessError):
        access.issue(user)
    guests.redeem(a['token'])
    with pytest.raises(MCPAccessError):
        access.issue(user, parent_invite_id=b['invite_id'])
    with pytest.raises(MCPAccessError):
        MCPAccess(tmp_path).issue(user)
    with pytest.raises(MCPAccessError):
        access.issue('account', parent_invite_id=a['invite_id'])
    with pytest.raises(MCPAccessError):
        access.issue('guest:bad')


def test_guest_session_logout_does_not_remove_separately_approved_connector(tmp_path):
    guests = GuestAccess(tmp_path)
    invitation = guests.issue('A')
    session = guests.redeem(invitation['token'])
    access = MCPAccess(tmp_path, guests)
    issued = access.issue(session['user_id'])
    guests.end(session['session'])
    assert access.resolve(issued['token'])['user_id'] == session['user_id']


def test_guest_expiration_and_missing_store_fail_closed(tmp_path, monkeypatch):
    clock = [2_000_000_000]
    monkeypatch.setattr(mcp_access.time, 'time', lambda: clock[0])
    guests = GuestAccess(tmp_path)
    invitation = guests.issue('A', ttl=60)
    session = guests.redeem(invitation['token'])
    access = MCPAccess(tmp_path, guests)
    issued = access.issue(session['user_id'])
    clock[0] += 60
    with pytest.raises(MCPAccessError):
        access.resolve(issued['token'])
    clock[0] -= 60
    guests.database.unlink()
    with pytest.raises(MCPAccessError):
        access.resolve(issued['token'])
    assert not guests.database.exists()


@pytest.mark.parametrize('token', ['', None, 3, {}, 'x' * 42, 'x' * 44, '!' * 43, 'x' * 100000, '\ud800'],
                         ids=['empty', 'none', 'integer', 'mapping', 'short', 'long', 'symbols', 'huge', 'surrogate'])
def test_invalid_tokens_are_safe_constants(tmp_path, token):
    with pytest.raises(MCPAccessError) as caught:
        MCPAccess(tmp_path).resolve(token)
    assert str(caught.value) == 'mcp_access_invalid'


@pytest.mark.parametrize('owner', ['', None, 1, [], 'a b', 'a\nb', 'a' * 201, '\ud800', 'mcp:abc', 'invite:redeem'],
                         ids=['empty', 'none', 'integer', 'array', 'space', 'newline', 'long', 'surrogate', 'mcp', 'invite'])
def test_owner_trust_boundary_validation(tmp_path, owner):
    access = MCPAccess(tmp_path)
    for method in (lambda: access.issue(owner), lambda: access.list_for(owner), lambda: access.revoke(owner, '0' * 32)):
        with pytest.raises(MCPAccessError, match='^mcp_owner_invalid$'):
            method()


@pytest.mark.parametrize('ttl', [0, -1, 59, 604801, True, None, '60', 60.0])
def test_ttl_rejects_out_of_range_and_non_integer(tmp_path, ttl):
    with pytest.raises(MCPAccessError, match='^mcp_expiry_invalid$'):
        MCPAccess(tmp_path).issue('owner', ttl=ttl)


@pytest.mark.parametrize('label', ['', ' ', 'a' * 121, 'a\nb', 'a\0b', '\ud800', None, [], 3],
                         ids=['empty', 'space', 'long', 'newline', 'null', 'surrogate', 'none', 'array', 'integer'])
def test_label_validation(tmp_path, label):
    with pytest.raises(MCPAccessError, match='^mcp_label_invalid$'):
        MCPAccess(tmp_path).issue('owner', label=label)


@pytest.mark.parametrize('identifier', ['', None, 'x' * 32, '0' * 31, '0' * 33])
def test_invalid_credential_identifier(tmp_path, identifier):
    with pytest.raises(MCPAccessError, match='^mcp_credential_invalid$'):
        MCPAccess(tmp_path).revoke('owner', identifier)


def test_concurrent_owner_and_global_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_access, 'MAX_ACTIVE_PER_OWNER', 2)
    monkeypatch.setattr(mcp_access, 'MAX_CREDENTIALS', 3)
    access = MCPAccess(tmp_path)
    def issue(_):
        try:
            return MCPAccess(tmp_path).issue('owner')
        except MCPAccessError:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(issue, range(6)))
    assert sum(item is not None for item in results) == 2
    other = access.issue('other')
    with pytest.raises(MCPAccessError, match='^mcp_credential_limit$'):
        access.issue('third')
    assert access.revoke('other', other['credential_id'])
    assert access.issue('third')['token']
