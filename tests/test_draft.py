import pytest

from agent.draft import create_draft


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_json(self, name, payload):
        self.calls.append((name, payload))
        return self.response


SOURCE = {"source_id": "S1", "filename": "실적.xlsx", "page": None, "sheet": "실적", "location": "시트 실적", "text": "매출 100만원 달성", "score": 1.0}
DRAFT = {"제목": "실적 결과보고서", "요약": "□ 매출 100만원 달성함 [S1]", "본문": "○ 매출 100만원 달성함 [S1]"}


@pytest.mark.parametrize("report_type", ["주간업무보고", "결과보고서", "품의서"])
def test_three_report_types_produce_template_json(report_type):
    brief = {"보고서 유형": report_type, "부족한 정보": []}
    client = FakeClient(DRAFT)
    result = create_draft(brief, [SOURCE], client)
    assert set(result) == {"제목", "요약", "본문"}
    assert result == DRAFT
    assert client.calls == [("draft", {"brief": brief, "sources": [SOURCE]})]


def test_unknown_citation_is_rejected():
    with pytest.raises(ValueError, match="출처 ID"):
        create_draft({"보고서 유형": "결과보고서"}, [SOURCE], FakeClient(dict(DRAFT, 본문="○ 매출 100만원 달성함 [S999]")))


def test_missing_material_is_not_replaced_with_fiction():
    placeholder = {"제목": "결과보고서", "요약": "□ 자료가 부족하여 추가 확인 필요함", "본문": "○ 자료가 부족하여 추가 확인 필요함"}
    assert create_draft({"보고서 유형": "결과보고서"}, [], FakeClient(placeholder)) == placeholder


def test_unresolved_brief_prevents_generation():
    client = FakeClient(DRAFT)
    with pytest.raises(ValueError, match="보완"):
        create_draft({"보고서 유형": "결과보고서", "부족한 정보": ["목적"]}, [SOURCE], client)
    assert not client.calls


def test_summary_longer_than_three_lines_is_rejected():
    with pytest.raises(ValueError, match="3줄"):
        create_draft({"보고서 유형": "결과보고서"}, [SOURCE], FakeClient(dict(DRAFT, 요약="\n".join([DRAFT["요약"]] * 4))))


@pytest.mark.parametrize("draft", [{}, {"제목": "제목", "요약": [], "본문": "본문"}, dict(DRAFT, 본문="")])
def test_malformed_template_json_is_rejected(draft):
    with pytest.raises(ValueError):
        create_draft({"보고서 유형": "결과보고서"}, [SOURCE], FakeClient(draft))


def test_duplicate_source_identifiers_are_rejected():
    with pytest.raises(ValueError, match="중복"):
        create_draft({"보고서 유형": "결과보고서"}, [SOURCE, SOURCE], FakeClient(DRAFT))


def test_company_and_public_template_fields_are_generated_as_registered_flat_strings():
    profile = {"format": "docx", "fields": [
        {"id": "cell-1", "label": "소요 예산", "value_key": "소요 예산", "kind": "docx_cell", "required": True},
        {"id": "cell-2", "label": "집행 결과", "value_key": "집행 결과", "kind": "docx_cell", "required": True},
    ]}
    response = dict(DRAFT, **{"소요 예산": "100만원 [S1]", "집행 결과": "매출 100만원 달성함 [S1]"})
    client = FakeClient(response)
    result = create_draft({"보고서 유형": "결과보고서"}, [SOURCE], client, template_profile=profile)
    assert result == response
    assert all(isinstance(value, str) for value in result.values())
    assert client.calls[0][1]["template_profile"] == profile


def test_missing_required_custom_field_is_not_fabricated():
    profile = {"fields": [{"label": "예산", "value_key": "예산", "required": True}]}
    with pytest.raises(ValueError, match="필수 양식 항목"):
        create_draft({"보고서 유형": "품의서"}, [SOURCE], FakeClient(DRAFT), template_profile=profile)


def test_optional_empty_field_is_preserved_as_an_empty_cell():
    profile = {"fields": [{"label": "비고", "value_key": "비고", "required": False}]}
    result = create_draft({"보고서 유형": "결과보고서"}, [SOURCE], FakeClient(DRAFT), template_profile=profile)
    assert result["비고"] == ""


@pytest.mark.parametrize("extra", [{"미등록": "내용"}, {"예산": {"금액": 100}}, {"예산": ["100만원"]}])
def test_unregistered_or_nested_generated_fields_are_rejected(extra):
    profile = {"fields": [{"label": "예산", "value_key": "예산"}]}
    with pytest.raises(ValueError):
        create_draft({"보고서 유형": "결과보고서"}, [SOURCE], FakeClient(dict(DRAFT, **extra)), template_profile=profile)


def test_preferences_context_is_applied_without_discarding_report_content():
    original = dict(DRAFT, 제목="실적 리포트")
    preferences = {"preferred_terms": {"리포트": "보고서"}}
    client = FakeClient(original)
    result = create_draft({"보고서 유형": "결과보고서"}, [SOURCE], client, preferences=preferences)
    assert result["제목"] == "실적 보고서"
    assert result["요약"] == DRAFT["요약"] and result["본문"] == DRAFT["본문"]
    assert client.calls[0][1]["preferences"] == preferences


@pytest.mark.parametrize("profile", [
    {"fields": [], "constraints": {"summary_max_lines": 1}},
    {"fields": [], "constraints": {"body_max_chars": 5}},
    {"fields": [{"label": "본문", "value_key": "본문", "max_chars": 5}]},
])
def test_template_limits_raise_instead_of_truncating_required_facts(profile):
    response = dict(DRAFT, 요약=DRAFT["요약"] + "\n" + DRAFT["요약"])
    with pytest.raises(ValueError, match="초과"):
        create_draft({"보고서 유형": "결과보고서"}, [SOURCE], FakeClient(response), template_profile=profile)


def test_bad_template_limit_is_rejected_before_llm_call():
    client = FakeClient(DRAFT)
    with pytest.raises(ValueError, match="양의 정수"):
        create_draft({"보고서 유형": "결과보고서"}, [SOURCE], client, template_profile={"constraints": {"summary_max_lines": "많이"}})
    assert not client.calls
