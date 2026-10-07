"""Regression gates for explicit form inputs and mixed unresolved claims."""

from copy import deepcopy

import pytest

from agent.pipeline import build_downloads, run_pipeline
from agent.review import CITATION_PATTERN
from evals.run import MockEvaluationClient


def case():
    return {"mock_brief": {"목적": "매출", "보고 대상": "팀장", "보고서 유형": "결과보고서",
                           "마감": "", "분량": "", "부족한 정보": [], "질문": []}}


def documents():
    text = "매출 120만원을 달성함"
    return [{"파일명": "원자료.txt", "본문": text, "표 목록": [],
             "페이지/시트 정보": [{"본문": text, "위치": "1쪽", "페이지": 1,
                                 "시트": None, "표 목록": []}]}]


def author_profile(*, explicit_required=True):
    field = {"value_key": "작성자", "label": "작성자", "input_required": True}
    if explicit_required:
        field["required"] = True
    return {"fields": [field]}


class AuthorClient(MockEvaluationClient):
    def generate_json(self, name, payload):
        if name == "draft":
            source = payload["sources"][0]
            line = f"□ {source['text']} [{source['source_id']}]"
            author = payload["brief"]["양식 항목"].get("작성자", "김영희")
            source_id = payload["brief"].get("양식 항목 출처", {}).get("작성자", source["source_id"])
            return {"제목": "매출 결과보고서", "요약": line, "본문": line,
                    "작성자": f"{author} [{source_id}]"}
        return super().generate_json(name, payload)


def test_required_default_cannot_accept_fabricated_brief_form_value():
    fixture = case()
    fixture["mock_brief"]["양식 항목"] = {"작성자": "없는 이름"}
    result = run_pipeline("매출 보고서", documents=documents(), client=AuthorClient(fixture),
                          template_profile=author_profile(explicit_required=False))
    assert result["status"] == "needs_information"
    assert "draft" not in result
    assert 1 <= len(result["questions"]) <= 2


def test_generated_form_value_cannot_overwrite_explicit_user_identity():
    class WrongAuthor(AuthorClient):
        def generate_json(self, name, payload):
            result = super().generate_json(name, payload)
            if name == "draft":
                result["작성자"] = result["작성자"].replace("홍길동", "김영희")
            return result

    result = run_pipeline("매출 보고서", documents=documents(), client=WrongAuthor(case()),
                          template_profile=author_profile(), field_values={"작성자": "홍길동"})
    value = CITATION_PATTERN.sub("", result["draft"]["작성자"]).strip()
    assert result["status"] != "ready" or value == "홍길동"


def test_export_rechecks_user_identity_after_manual_change():
    result = run_pipeline("매출 보고서", documents=documents(), client=AuthorClient(case()),
                          template_profile=author_profile(), field_values={"작성자": "홍길동"})
    assert result["status"] == "ready"
    changed = deepcopy(result)
    changed["draft"]["작성자"] = changed["draft"]["작성자"].replace("홍길동", "김영희")
    with pytest.raises(ValueError):
        build_downloads(changed, confirmed=True)


def test_unresolved_clause_does_not_exempt_a_completed_fact_from_review():
    class MixedClaimClient(MockEvaluationClient):
        def generate_json(self, name, payload):
            if name == "draft":
                return {"제목": "결과보고서", "요약": "□ 투자 승인을 완료함; 예산은 미확정임",
                        "본문": "○ 투자 승인을 완료함; 예산은 미확정임"}
            if name == "grounding":
                return {"claims": [{"field": item["field"], "line": item["line"],
                                    "status": "unsupported", "evidence": []}
                                   for item in payload["claims"]]}
            if name == 'completeness':
                return {'checked_fields': list(payload['draft']), 'issues': []}
            return super().generate_json(name, payload)

    result = run_pipeline("매출 보고서", documents=documents(), client=MixedClaimClient(case()),
                          semantic_review=True)
    assert result["status"] == "needs_revision"
    assert result["grounding"]["claim_count"] == 2
    assert result["review"]["blocking"]
