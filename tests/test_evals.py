import json
from copy import deepcopy
from pathlib import Path

import pytest

from evals.run import METRICS, MockEvaluationClient, evaluate, evaluate_case, main, score_result

ROOT = Path(__file__).resolve().parents[1]


def cases():
    return json.loads((ROOT / "evals" / "cases.json").read_text(encoding="utf-8"))


def test_fifteen_cases_execute_through_real_parsers_and_exporters():
    report = evaluate(cases(), "mock")
    assert report["case_count"] == report["status_passed"] == 15
    assert all(value["score"] == 100 for value in report["summary"].values())
    assert all(value["cases"] == 14 for value in report["summary"].values())
    assert report["prompt_hashes"] and report["attachment_hashes"]
    assert report["models"]["generation"] == "mock"
    assert report["rows"][-1]["result"]["initial_review"]["corrected"]


def test_metrics_penalize_missing_citations_numbers_and_wrong_type():
    one = cases()[0]
    row = evaluate_case(one, MockEvaluationClient(one))
    result = deepcopy(row["result"])
    result["draft"].update({"요약": "□ 별도 업무를 완료함", "본문": "○ 별도 업무를 완료함"})
    scores = score_result(result, one["expected"])
    assert scores["출처 포함률"] == 0
    assert scores["수치 정확도"] == 0
    assert scores["양식 준수"] == 0
    result = deepcopy(row["result"])
    result["brief"]["보고서 유형"] = "품의서"
    assert score_result(result, one["expected"])["양식 준수"] == 0


def test_numeric_titles_are_included_in_accuracy():
    one = cases()[0]
    result = evaluate_case(one, MockEvaluationClient(one))["result"]
    result["draft"]["제목"] = "매출 999만원 보고서"
    assert score_result(result, one["expected"])["수치 정확도"] < 100


def test_cli_baseline_and_dataset_guard(tmp_path):
    baseline = tmp_path / "baseline.json"
    output = tmp_path / "after.json"
    assert main(["--mode", "mock", "--output", str(baseline)]) == 0
    assert main(["--mode", "mock", "--baseline", str(baseline), "--output", str(output)]) == 0
    report = json.loads(baseline.read_text(encoding="utf-8"))
    report["dataset_hash"] = "different"
    baseline.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        main(["--mode", "mock", "--baseline", str(baseline), "--output", str(output)])
    assert error.value.code == 2


def test_metric_coverage_decline_is_regression(monkeypatch, tmp_path):
    import evals.run as module

    report = evaluate(cases(), "mock")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    declined = deepcopy(report)
    declined["summary"][METRICS[0]]["cases"] -= 1
    monkeypatch.setattr(module, "evaluate", lambda items, mode: declined)
    assert main(["--mode", "mock", "--baseline", str(baseline), "--output", str(tmp_path / "out.json")]) == 1
