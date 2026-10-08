from hashlib import sha256
import sqlite3

import pytest

from app.gmail_store import GmailStore


def test_oauth_state_is_one_use_and_tenant_bound(tmp_path):
    store = GmailStore(tmp_path / 'gmail.sqlite3', 'test-secret-' + 'x' * 40)
    state, verifier = store.begin('user-A')
    owner_hash, returned_verifier = store.consume(state)
    assert owner_hash == sha256(b'user-A').hexdigest()
    assert returned_verifier == verifier
    with pytest.raises(ValueError):
        store.consume(state)


def test_refresh_token_is_encrypted_and_isolated(tmp_path):
    path = tmp_path / 'gmail.sqlite3'
    store = GmailStore(path, 'test-secret-' + 'x' * 40)
    store.save(store.owner_hash('user-A'), 'a@example.test', 'refresh-secret-A')
    assert store.get('user-B') is None
    assert store.get('user-A') == {'email': 'a@example.test', 'refresh_token': 'refresh-secret-A'}
    with sqlite3.connect(path) as db:
        stored = db.execute('SELECT refresh_token FROM gmail_connections').fetchone()[0]
    assert b'refresh-secret-A' not in stored
    store.disconnect('user-A')
    assert store.get('user-A') is None
