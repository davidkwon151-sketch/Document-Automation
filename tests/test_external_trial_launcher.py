"""The trial launcher reads owner-only secrets, never embeds values in source."""

import json

import pytest

from app import serve_external_trial
from app.serve_external_trial import runtime_config


def test_runtime_config_validates_both_credentials(tmp_path):
    path = tmp_path / 'private.json'
    path.write_text(json.dumps({'gateway_secret': 'a' * 48,
                                'hwp_token': 'b' * 48}), encoding='utf-8')
    assert runtime_config(path) == {'gateway_secret': 'a' * 48,
                                    'hwp_token': 'b' * 48}
    path.write_text(json.dumps({'gateway_secret': 'short',
                                'hwp_token': 'b' * 48}), encoding='utf-8')
    with pytest.raises(ValueError):
        runtime_config(path)


def test_trial_server_reads_private_gmail_oauth_config(tmp_path, monkeypatch):
    runtime = tmp_path / 'private.json'
    runtime.write_text(json.dumps({'gateway_secret': 'a' * 48,
                                   'hwp_token': 'b' * 48}), encoding='utf-8')
    private = tmp_path / '.runtime'
    private.mkdir()
    private.joinpath('external-server.env').write_text(
        'GMAIL_CLIENT_ID=client-id\nGMAIL_CLIENT_SECRET=client-secret\n'
        'GMAIL_REDIRECT_URI=https://example.test/oauth/gmail/callback\n', encoding='utf-8')
    monkeypatch.setattr(serve_external_trial, 'ROOT', tmp_path)
    for key in ('GMAIL_CLIENT_ID', 'GMAIL_CLIENT_SECRET', 'GMAIL_REDIRECT_URI'):
        monkeypatch.setenv(key, '')
    calls = []
    monkeypatch.setattr(serve_external_trial.uvicorn, 'run',
                        lambda app, **kwargs: calls.append((app, kwargs)))
    serve_external_trial.serve(runtime_file=runtime)
    assert len(calls) == 1
    assert calls[0][1]['port'] == 8603
    for key in ('GMAIL_CLIENT_ID', 'GMAIL_CLIENT_SECRET', 'GMAIL_REDIRECT_URI'):
        assert key in serve_external_trial.os.environ
