"""Known LLM failure guidance reaches the RA screen without API requests."""
import pytest

from app import ra_mvp_ui
from llm.client import ConfigurationError, LLMError


@pytest.mark.parametrize('kind,message', [
    ('quota', 'OpenAI API 크레딧 잔액이 부족함. API 결제 설정에서 잔액을 확인해 주세요.'),
    ('rate_limit', 'OpenAI API 호출이 일시적으로 제한됨. 잠시 후 다시 작성해 주세요.'),
])
def test_ra_screen_shows_actionable_llm_guidance(monkeypatch, kind, message):
    shown = []
    monkeypatch.setattr(ra_mvp_ui.st, 'error', shown.append)
    ra_mvp_ui.show_error(LLMError(message, kind=kind))
    assert shown == [message]


def test_ra_configuration_failure_does_not_expose_credentials(monkeypatch):
    shown = []
    monkeypatch.setattr(ra_mvp_ui.st, 'error', shown.append)
    ra_mvp_ui.show_error(ConfigurationError('synthetic-sensitive-test-value'))
    assert len(shown) == 1
    assert 'synthetic-sensitive-test-value' not in shown[0]
    assert 'API 키' in shown[0]
