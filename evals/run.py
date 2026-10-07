"""python -m evals.run --mode mock|live [--baseline previous.json]."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

from agent.pipeline import build_downloads, run_pipeline
from agent.review import CITATION_PATTERN, inspect_draft, is_factual_line, number_tokens

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
METRICS = ("양식 준수", "출처 포함률", "수치 정확도", "문체 규칙 준수")


class MockEvaluationClient:
    """Deterministic transport stand-in. Its scores do not measure prompt quality."""

    def __init__(self, case):
        self.case = case

    def embed(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * 128
            tokens = re.findall(r"[가-힣A-Za-z0-9]+", text.lower())
            for token in tokens:
                for offset in range(max(len(token) - 1, 1)):
                    gram = token[offset:offset + 2]
                    bucket = int.from_bytes(hashlib.sha256(gram.encode()).digest()[:2], "big") % len(vector)
                    vector[bucket] += 1
            vectors.append(vector)
        return vectors

    def generate_json(self, prompt_name, payload):
        name = Path(prompt_name).stem
        if name == "brief":
            return dict(self.case["mock_brief"])
        if name == "draft":
            sources = payload["sources"]
            if not sources:
                return {"제목": payload["brief"]["보고서 유형"], "요약": "□ 근거 자료가 부족함", "본문": "○ 추가 확인 필요함"}
            source = sources[0]
            line = f"□ {source['text']} [{source['source_id']}]"
            if self.case.get("inject_wrong_number_in_mock"):
                line = line.replace("140만원", "999만원")
            return {"제목": f"{payload['brief']['목적']} {payload['brief']['보고서 유형']}", "요약": line, "본문": line}
        if name == "review":
            return dict(payload["draft"])
        if name == "boss_review":
            return {"questions": ["성과의 근거는 무엇인가요?", "후속 일정은 언제인가요?", "추가 예산이 필요한가요?"], "answers": []}
        raise ValueError(f"알 수 없는 평가 프롬프트: {prompt_name}")


def score_result(result, expected):
    if "draft" not in result:
        return {metric: None for metric in METRICS}
    draft, sources = result["draft"], result["sources"]
    issues = inspect_draft(draft, sources)
    lines = [(field, index, line) for field in ("요약", "본문") for index, line in enumerate(draft[field].splitlines(), 1) if line.strip()]
    numeric_lines = lines + [("제목", 1, draft["제목"])]
    factual = [(field, index, line) for field, index, line in lines if is_factual_line(line)]
    source_ids = {source["source_id"] for source in sources}
    cited = sum(bool(CITATION_PATTERN.findall(line)) and set(CITATION_PATTERN.findall(line)) <= source_ids for _, _, line in factual)
    numeric_total = sum(len(number_tokens(line)) for _, _, line in numeric_lines)
    numeric_errors = sum(issue["code"] == "number_mismatch" for issue in issues)
    style_bad = {(issue["field"], issue["line"]) for issue in issues if issue["code"] in {"bullet_style", "ending_style", "typo"}}
    required = expected.get("fields", ["제목", "요약", "본문"])
    format_ok = all(isinstance(draft.get(field), str) and draft[field].strip() for field in required)
    format_ok = format_ok and len(draft["요약"].splitlines()) <= expected.get("max_summary_lines", 3)
    format_ok = format_ok and result.get("brief", {}).get("보고서 유형") == expected.get("report_type")
    expected_numbers = {token["key"] for token in number_tokens(expected.get("source_text", ""))}
    actual_numbers = {token["key"] for _, _, line in numeric_lines for token in number_tokens(line)}
    required_numbers_ok = expected_numbers <= actual_numbers
    format_ok = format_ok and required_numbers_ok
    # A numeric metric with no numbers is N/A, never an artificial perfect score.
    return {
        "양식 준수": 100.0 if format_ok else 0.0,
        "출처 포함률": round(cited / len(factual) * 100, 2) if factual else None,
        "수치 정확도": (round(max(numeric_total - numeric_errors, 0) / numeric_total * 100, 2) if numeric_total else None) if required_numbers_ok else 0.0,
        "문체 규칙 준수": round((len(lines) - len(style_bad)) / len(lines) * 100, 2) if lines else None,
    }


def evaluate_case(case, client):
    started = time.perf_counter()
    paths = [ROOT / attachment for attachment in case["attachments"]]
    result = run_pipeline(case["instruction"], paths, client=client, semantic_review=not isinstance(client, MockEvaluationClient))
    scores = score_result(result, case["expected"])
    status_ok = result["status"] == case["expected"]["status"]
    template_ok = None
    if result.get("status") == "ready":
        outputs = build_downloads(result, confirmed=True)
        template_ok = set(outputs) == {"docx", "hwpx"} and all(outputs.values())
        if not template_ok:
            scores["양식 준수"] = 0.0
    return {"id": case["id"], "status": result["status"], "expected_status": case["expected"]["status"], "status_ok": status_ok, "scores": scores, "template_export_ok": bool(template_ok) if template_ok is not None else None, "seconds": round(time.perf_counter() - started, 3), "warnings": result.get("review", {}).get("warnings", []), "result": result}


def evaluate(cases, mode):
    from llm.client import LLMClient

    rows = []
    client = LLMClient() if mode == "live" else None
    for case in cases:
        try:
            rows.append(evaluate_case(case, client or MockEvaluationClient(case)))
        except Exception as exc:
            rows.append({"id": case["id"], "status": "error", "status_ok": False, "scores": {metric: 0.0 for metric in METRICS}, "error": str(exc)})
    summary = {}
    for metric in METRICS:
        values = [row["scores"][metric] for row in rows if row["scores"][metric] is not None]
        summary[metric] = {"score": round(sum(values) / len(values), 2) if values else None, "cases": len(values)}
    prompts = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (PROJECT / "prompts").glob("*.md")}
    dataset_hash = hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    attachments = {attachment: hashlib.sha256((ROOT / attachment).read_bytes()).hexdigest() for case in cases for attachment in case["attachments"]}
    return {"mode": mode, "notice": "mock은 연결·검증 회귀 테스트임. 프롬프트 품질은 --mode live로 비교해야 함." if mode == "mock" else "실제 LLM 평가이며 문체 의미·두괄식·리뷰 유용성은 사람 평가가 필요함.", "metric_note": "출처 포함률은 유효한 출처 ID 포함률이며 사실의 의미상 일치를 보장하지 않음. 기대 업무 수치 누락은 수치 정확도 0점으로 처리함.", "models": {"generation": getattr(client, "model", "mock"), "embedding": getattr(client, "embedding_model", "mock")}, "dataset_hash": dataset_hash, "attachment_hashes": attachments, "prompt_hashes": prompts, "summary": summary, "status_passed": sum(row["status_ok"] for row in rows), "case_count": len(cases), "rows": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description="문서 표준화 AI AGENT 15개 평가 케이스 실행")
    parser.add_argument("--mode", choices=("mock", "live"), default="live")
    parser.add_argument("--cases", type=Path, default=ROOT / "cases.json")
    parser.add_argument("--output", type=Path, default=PROJECT / "data" / "eval-results.json")
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args(argv)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    report = evaluate(cases, args.mode)
    regression = False
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        if baseline["mode"] != report["mode"]:
            parser.error("mock과 live 점수를 서로 비교할 수 없음")
        if baseline.get("dataset_hash") != report["dataset_hash"] or baseline.get("attachment_hashes") != report["attachment_hashes"]:
            parser.error("평가 케이스와 첨부자료가 다른 결과를 기준으로 비교할 수 없음")
        deltas = {}
        for metric in METRICS:
            before = baseline["summary"][metric]["score"]
            after = report["summary"][metric]["score"]
            delta = None if before is None or after is None else round(after - before, 2)
            deltas[metric] = delta
            regression |= delta is not None and delta < 0
            regression |= report["summary"][metric]["cases"] < baseline["summary"][metric]["cases"]
        regression |= report["status_passed"] < baseline["status_passed"]
        report["baseline_deltas"] = deltas
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report["notice"])
    print(f"상태 기준 통과: {report['status_passed']}/{report['case_count']}")
    for metric, value in report["summary"].items():
        print(f"{metric}: {value['score']}점 ({value['cases']}건 평가)")
    if regression:
        print("기준 결과보다 점수가 하락함")
    return 1 if regression or report["status_passed"] != report["case_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
