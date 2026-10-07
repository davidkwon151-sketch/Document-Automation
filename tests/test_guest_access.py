"""Persistent no-signup capabilities: replay, isolation, expiry, revocation, limits."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from app import guest_access
from app.guest_access import GuestAccess, GuestAccessError, issue_to_file, renew_to_file


def test_two_recipients_are_isolated_and_secrets_are_only_hashes(tmp_path):
    access = GuestAccess(tmp_path)
    first, second = access.issue('RA 담당자 A'), access.issue('RA 담당자 B')
    a, b = access.redeem(first['token']), access.redeem(second['token'])
    assert a['user_id'] != b['user_id']
    assert a['session'] != b['session']
    assert access.resolve(a['session'])['user_id'] == a['user_id']
    assert access.resolve(b['session'])['user_id'] == b['user_id']
    assert 'session' not in access.resolve(a['session'])
    persisted = access.database.read_bytes()
    for secret in (first['token'], second['token'], a['session'], b['session']):
        assert secret.encode() not in persisted
    with sqlite3.connect(access.database) as database:
        assert database.execute('SELECT token_hash FROM invites WHERE id=?',
                                (first['invite_id'],)).fetchone()[0] == sha256(first['token'].encode()).hexdigest()
    restarted = GuestAccess(tmp_path)
    assert restarted.resolve(a['session']) == access.resolve(a['session'])
    with pytest.raises(GuestAccessError):
        restarted.redeem(first['token'])


def test_one_invite_redeemed_exactly_once_under_concurrent_instances(tmp_path):
    invitation = GuestAccess(tmp_path).issue('동시 시험')
    def redeem(_):
        try:
            return GuestAccess(tmp_path).redeem(invitation['token'])
        except GuestAccessError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(redeem, range(8)))
    assert sum(result is not None for result in results) == 1


def test_revoke_before_and_after_redemption_and_logout(tmp_path):
    access = GuestAccess(tmp_path)
    pending = access.issue('취소 예정')
    assert access.revoke(pending['invite_id'])
    assert not access.revoke(pending['invite_id'])
    with pytest.raises(GuestAccessError):
        access.redeem(pending['token'])
    used = access.issue('사용 후 취소')
    session = access.redeem(used['token'])
    assert access.revoke(used['invite_id'])
    with pytest.raises(GuestAccessError):
        access.resolve(session['session'])
    logout = access.redeem(access.issue('로그아웃')['token'])
    assert access.end(logout['session'])
    assert not access.end(logout['session'])
    with pytest.raises(GuestAccessError):
        access.resolve(logout['session'])


def test_expiry_edges_session_cannot_outlive_invite_and_is_capped(tmp_path, monkeypatch):
    clock = [2_000_000_000]
    monkeypatch.setattr(guest_access.time, 'time', lambda: clock[0])
    access = GuestAccess(tmp_path)
    expired, short = access.issue('사용 전 만료', 60), access.issue('짧은 세션', 120)
    clock[0] += 59
    session = access.redeem(short['token'])
    assert session['expires_at'] == short['expires_at']
    clock[0] += 1
    with pytest.raises(GuestAccessError):
        access.redeem(expired['token'])
    assert access.resolve(session['session'])['expires_at'] == short['expires_at']
    clock[0] = short['expires_at']
    with pytest.raises(GuestAccessError):
        access.resolve(session['session'])
    long = access.redeem(access.issue('긴 초대', guest_access.MAX_INVITE_TTL)['token'])
    assert long['expires_at'] == clock[0] + guest_access.MAX_SESSION_TTL
    clock[0] = long['expires_at']
    with pytest.raises(GuestAccessError):
        access.resolve(long['session'])


@pytest.mark.parametrize('token', ['', None, 1, {}, 'x' * 42, 'x' * 44, 'x' * 100000, '!' * 43, '\n' + 'x' * 42],
                         ids=['empty', 'none', 'integer', 'object', 'short', 'long', 'huge', 'symbols', 'newline'])
def test_untrusted_tokens_fail_safely_without_echo(tmp_path, token):
    access = GuestAccess(tmp_path)
    for method in (access.redeem, access.resolve, access.end):
        with pytest.raises(GuestAccessError) as captured:
            method(token)
        assert str(captured.value) == 'guest_access_invalid'


def test_well_formed_unknown_and_session_or_invite_swapping_fail(tmp_path):
    access = GuestAccess(tmp_path)
    issued = access.issue('토큰 용도 분리')
    with pytest.raises(GuestAccessError):
        access.resolve(issued['token'])
    session = access.redeem(issued['token'])
    with pytest.raises(GuestAccessError):
        access.redeem(session['session'])
    with pytest.raises(GuestAccessError):
        access.resolve('x' * 43)


@pytest.mark.parametrize('ttl', [0, -1, 59, 604801, True, '60', 60.0, None, float('inf')])
def test_ttl_is_bounded_integer(tmp_path, ttl):
    with pytest.raises(GuestAccessError, match='guest_expiry_invalid'):
        GuestAccess(tmp_path).issue('기한 검사', ttl)


@pytest.mark.parametrize('label', ['', ' ', 'a' * 121, 'a\nb', 'a\0b', 'a\x7fb', None, 3, [], '\ud800'],
                         ids=['empty', 'space', 'long', 'newline', 'null', 'delete', 'none', 'integer', 'array', 'surrogate'])
def test_label_is_bounded_and_control_free(tmp_path, label):
    with pytest.raises(GuestAccessError, match='guest_label_invalid'):
        GuestAccess(tmp_path).issue(label)


def test_total_identity_limit_retains_renewal_and_prunes_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(guest_access, 'MAX_ACTIVE_INVITES', 2)
    clock = [2_000_000_000]
    monkeypatch.setattr(guest_access.time, 'time', lambda: clock[0])
    access = GuestAccess(tmp_path)
    first, second = access.issue('첫 번째', 60), access.issue('두 번째', 120)
    session = access.redeem(first['token'])
    with pytest.raises(GuestAccessError, match='guest_invitation_limit'):
        access.issue('한도 초과')
    access.revoke(second['invite_id'])
    with pytest.raises(GuestAccessError, match='guest_invitation_limit'):
        access.issue('취소도 전체 신원 한도 유지')
    renewed = access.renew(second['invite_id'], 120)
    with sqlite3.connect(access.database) as database:
        assert database.execute('SELECT count(*) FROM invites').fetchone()[0] == 2
        assert database.execute('SELECT count(*) FROM sessions').fetchone()[0] == 1
    clock[0] += 60
    with pytest.raises(GuestAccessError, match='guest_invitation_limit'):
        access.issue('만료도 전체 신원 한도 유지', 120)
    access.renew(first['invite_id'], 120)
    with sqlite3.connect(access.database) as database:
        assert database.execute('SELECT count(*) FROM invites').fetchone()[0] == 2
        assert database.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
    with pytest.raises(GuestAccessError):
        access.resolve(session['session'])
    assert access.redeem(renewed['token'])


@pytest.mark.parametrize('state', ['active', 'expired', 'revoked'])
def test_local_renew_preserves_identity_and_rejects_old_secrets(tmp_path, monkeypatch, state):
    clock = [2_000_000_000]
    monkeypatch.setattr(guest_access.time, 'time', lambda: clock[0])
    access = GuestAccess(tmp_path)
    invite = access.issue('회사 RA 담당자', 60)
    previous = access.redeem(invite['token'])
    if state == 'expired':
        clock[0] += 60
    elif state == 'revoked':
        access.revoke(invite['invite_id'])
    renewed = GuestAccess(tmp_path).renew(invite['invite_id'], 120)
    assert renewed['invite_id'] == invite['invite_id']
    assert renewed['label'] == invite['label']
    assert renewed['token'] != invite['token']
    assert renewed['expires_at'] == clock[0] + 120
    with pytest.raises(GuestAccessError):
        access.resolve(previous['session'])
    with pytest.raises(GuestAccessError):
        access.redeem(invite['token'])
    replacement = access.redeem(renewed['token'])
    assert replacement['user_id'] == previous['user_id']
    assert access.resolve(replacement['session'])['user_id'] == previous['user_id']
    with pytest.raises(GuestAccessError):
        access.redeem(renewed['token'])
    with sqlite3.connect(access.database) as database:
        assert database.execute('SELECT count(*) FROM invites').fetchone()[0] == 1
        assert database.execute('SELECT count(*) FROM sessions').fetchone()[0] == 1
    assert invite['token'].encode() not in access.database.read_bytes()
    assert renewed['token'].encode() not in access.database.read_bytes()


@pytest.mark.parametrize('identifier', ['', None, 1, 'a' * 31, 'g' * 32, 'a' * 32])
def test_renew_requires_explicit_existing_identity(tmp_path, identifier):
    with pytest.raises(GuestAccessError, match='guest_invitation_invalid'):
        GuestAccess(tmp_path).renew(identifier)


def test_invalid_renew_ttl_does_not_revoke_existing_session(tmp_path):
    access = GuestAccess(tmp_path)
    invite = access.issue('계속 사용하는 세션')
    session = access.redeem(invite['token'])
    with pytest.raises(GuestAccessError, match='guest_expiry_invalid'):
        access.renew(invite['invite_id'], -1)
    assert access.resolve(session['session'])['user_id'] == session['user_id']


def test_concurrent_issue_cannot_bypass_count_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(guest_access, 'MAX_ACTIVE_INVITES', 2)
    access = GuestAccess(tmp_path)
    def issue(index):
        try:
            return access.issue('시험 ' + str(index))
        except GuestAccessError as error:
            assert error.code == 'guest_invitation_limit'
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(issue, range(8)))
    assert sum(result is not None for result in results) == 2
    with sqlite3.connect(access.database) as database:
        assert database.execute('SELECT count(*) FROM invites').fetchone()[0] == 2


def test_issue_private_fragment_file_no_overwrite_no_public_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    access = GuestAccess(tmp_path / 'private')
    output = tmp_path / '.runtime' / 'invitation.json'
    metadata = issue_to_file(access, 'RA 사용자', 120, 'https://example.test/', output)
    assert set(metadata) == {'invite_id', 'output'}
    record = json.loads(output.read_text(encoding='utf-8'))
    assert record['url'].startswith('https://example.test/access#token=')
    secret = record['url'].split('#token=')[1]
    assert access.redeem(secret)['invite_id'] == metadata['invite_id']
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        issue_to_file(access, '재발급', 120, 'https://example.test', output)
    assert output.read_bytes() == before
    with sqlite3.connect(access.database) as database:
        assert database.execute('SELECT count(*) FROM invites').fetchone()[0] == 1
    with pytest.raises(GuestAccessError, match='guest_output_must_be_private'):
        issue_to_file(access, '공개 출력 차단', 120, 'https://example.test', tmp_path / 'public.json')


@pytest.mark.parametrize('site', ['http://example.test', 'https://a:b@example.test',
                                  'https://example.test/path', 'https://example.test?q=1',
                                  'https://example.test#x', 'javascript:alert(1)'])
def test_cli_link_destination_is_https_origin(tmp_path, monkeypatch, site):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(GuestAccessError, match='guest_site_invalid'):
        issue_to_file(GuestAccess(tmp_path / 'private'), '시험', 60, site,
                      tmp_path / '.runtime' / 'link.json')


def test_failed_file_issue_removes_reserved_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / '.runtime' / 'failed.json'
    with pytest.raises(GuestAccessError):
        issue_to_file(GuestAccess(tmp_path / 'private'), '', 60, 'https://example.test', output)
    assert not output.exists()


def test_cli_stdout_has_no_invitation_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['guest_access', '--root', 'private', 'issue',
                                    '--label', 'RA 담당자', '--site', 'https://example.test',
                                    '--output', '.runtime/link.json'])
    guest_access.main()
    output = capsys.readouterr().out
    private = json.loads(Path('.runtime/link.json').read_text(encoding='utf-8'))
    secret = private['url'].split('#token=')[1]
    assert secret not in output
    assert 'https://' not in output
    assert json.loads(output)['invite_id'] == private['invite_id']


def test_cli_renew_private_file_and_no_overwrite_preserves_work_identity(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    access = GuestAccess('private')
    invite = access.issue('RA 회사 자료')
    previous = access.redeem(invite['token'])
    occupied = Path('.runtime/existing.json')
    occupied.parent.mkdir()
    occupied.write_text('기존 비공개 링크 파일', encoding='utf-8')
    with pytest.raises(FileExistsError):
        renew_to_file(access, invite['invite_id'], 120, 'https://example.test', occupied)
    assert access.resolve(previous['session'])['user_id'] == previous['user_id']
    monkeypatch.setattr('sys.argv', ['guest_access', '--root', 'private', 'renew',
                                    invite['invite_id'], '--site', 'https://example.test',
                                    '--ttl', '120', '--output', '.runtime/renewed.json'])
    guest_access.main()
    stdout = capsys.readouterr().out
    record = json.loads(Path('.runtime/renewed.json').read_text(encoding='utf-8'))
    secret = record['url'].split('#token=')[1]
    assert secret not in stdout and 'https://' not in stdout
    assert json.loads(stdout)['invite_id'] == invite['invite_id']
    assert access.redeem(secret)['user_id'] == previous['user_id']
    with pytest.raises(GuestAccessError):
        access.resolve(previous['session'])
