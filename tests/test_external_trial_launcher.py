"""The trial launcher reads owner-only secrets, never embeds values in source."""

import json

import pytest

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
