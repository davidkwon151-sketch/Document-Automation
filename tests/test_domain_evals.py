import json
from types import SimpleNamespace

import pytest

from evals.domains import CASES, evaluate, main


def test_adversarial_domain_regression_detects_corruption_and_accepts_grounded_data():
    cases = json.loads(CASES.read_text(encoding='utf-8'))
    assert len(cases) >= 16
    assert any(case['expected_blocking'] for case in cases)
    assert any(not case['expected_blocking'] for case in cases)
    result = evaluate(cases)
    assert result['passed_count'] == result['case_count'], result['results']
    assert not result['model_evaluated'] and result['synthetic_sources']
    assert result['model_response_count'] == 0 and result['models'] == []


def test_domain_eval_baseline_requires_same_cases_and_mode(tmp_path):
    baseline = tmp_path / 'baseline.json'
    output = tmp_path / 'next.json'
    assert main(['--output', str(baseline)]) == 0
    assert main(['--output', str(output), '--baseline', str(baseline)]) == 0
    assert json.loads(output.read_text(encoding='utf-8'))['regressions'] == []
    old = json.loads(baseline.read_text(encoding='utf-8'))
    old['mode'] = 'live'
    baseline.write_text(json.dumps(old), encoding='utf-8')
    with pytest.raises(ValueError, match='같은'):
        main(['--output', str(output), '--baseline', str(baseline)])


@pytest.mark.parametrize("failure", ["configuration", "api"])
def test_live_client_failures_never_claim_an_actual_model_response(monkeypatch, failure):
    cases = [json.loads(CASES.read_text(encoding='utf-8'))[0]]
    from llm import client as llm_client
    def fail(*args, **kwargs):
        raise llm_client.ConfigurationError("API key missing") if failure == "configuration" else llm_client.LLMError("API unavailable")
    if failure == "configuration":
        monkeypatch.setattr(llm_client, "LLMClient", fail)
        supplied = None
    else:
        supplied = SimpleNamespace(model="configured-but-unavailable", generate_json=fail)
    result = evaluate(cases, mode="live", client=supplied)
    assert result["case_count"] == 1 and result["passed_count"] == 0
    assert result["model_response_count"] == 0 and not result["model_evaluated"]
    assert result["models"] == [] and result["model_name_source"] is None
    assert not result["results"][0]["model_response_received"]
    assert result["results"][0]["model"] is None and result["results"][0]["error"]


@pytest.mark.parametrize("postcheck_failure", [False, True])
def test_received_draft_counts_and_model_provenance_survive_later_check_failure(monkeypatch, postcheck_failure):
    from agent import draft as drafting, grounding, review
    case = json.loads(CASES.read_text(encoding='utf-8'))[0]
    monkeypatch.setattr(drafting, "create_draft", lambda *a, **k: {"제목": "용량 보고", "요약": case["candidate"], "본문": case["candidate"]})
    monkeypatch.setattr(review, "review_draft", lambda *a, **k: {"blocking": False})
    def semantic(*args, **kwargs):
        if postcheck_failure:
            raise RuntimeError("inspection failed")
        return {"blocking": False}
    monkeypatch.setattr(grounding, "inspect_grounding", semantic)
    result = evaluate([case], mode="live", client=SimpleNamespace(model="mock-model"))
    assert result["model_response_count"] == 1 and result["model_evaluated"]
    assert result["models"] == ["mock-model"] and result["model_name_source"] == "client_configuration"
    assert result["results"][0]["model_response_received"] and result["results"][0]["model"] == "mock-model"
    assert result["passed_count"] == int(not postcheck_failure)
