import pytest

from agent.review import inspect_draft, review_draft


def source(text, source_id="S1"):
    return {"source_id": source_id, "filename": "실적.xlsx", "sheet": "실적", "page": None, "location": "시트 실적", "text": text, "score": 1.0}


def draft(line):
    return {"제목": "결과보고서", "요약": line, "본문": line}


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_json(self, name, payload):
        self.calls.append((name, payload))
        return self.response


def test_wrong_number_is_found_then_safely_corrected_once():
    original = draft("○ 매출 120만원 달성함 [S1]")
    sources = [source("매출 100만원 달성")]
    assert any(issue["code"] == "number_mismatch" for issue in inspect_draft(original, sources))
    client = FakeClient({})
    result = review_draft(original, sources, client)
    assert result["corrected"]
    assert not result["blocking"]
    assert "100만원" in result["draft"]["본문"]
    assert "[S1]" in result["draft"]["본문"]
    assert "120만원" in original["본문"]
    assert not client.calls


def test_unrelated_source_with_same_number_is_not_a_valid_citation():
    result = review_draft(draft("○ 매출 120만원 달성함 [S1]"), [source("비용 120만원 집행"), source("매출 120만원 달성", "S2")])
    assert result["blocking"]
    assert any(issue["code"] == "number_mismatch" for issue in result["warnings"])


def test_identical_number_for_different_item_is_not_validated():
    result = review_draft(draft("○ 매출 100만원 달성함 [S1]"), [source("비용 100만원, 매출 80만원")])
    assert result["corrected"]
    assert "80만원" in result["draft"]["본문"]
    assert not result["blocking"]


def test_ambiguous_numbers_are_not_guessed():
    original = draft("○ 매출 120만원 달성함 [S1]")
    result = review_draft(original, [source("상반기 매출 100만원, 하반기 매출 110만원")])
    assert result["blocking"]
    assert result["draft"] == original
    assert not result["corrected"]


def test_period_and_actual_target_context_are_compared():
    sources = [source("상반기 매출 100만원, 하반기 매출 120만원")]
    original = draft("○ 하반기 매출 100만원 달성함 [S1]")
    assert any(issue["code"] == "number_mismatch" for issue in inspect_draft(original, sources))
    assert "120만원" in review_draft(original, sources)["draft"]["본문"]
    targets = [source("목표 매출 100만원, 실적 매출 90만원")]
    result = review_draft(draft("○ 실적 매출 100만원 달성함 [S1]"), targets)
    assert "90만원" in result["draft"]["본문"]
    assert not result["blocking"]


def test_equivalent_money_units_and_percent_decimals_match():
    original = draft("○ 매출 100만원, 달성률 80.0%임 [S1]")
    result = review_draft(original, [source("매출 1,000,000원, 달성률 80퍼센트")])
    assert not result["warnings"]
    assert not result["corrected"]


def test_unknown_and_missing_citations_block_output():
    missing = review_draft(draft("○ 행사 완료함"), [source("행사 완료")])
    assert missing["blocking"]
    assert any(issue["code"] == "missing_source" for issue in missing["warnings"])
    unknown = review_draft(draft("○ 행사 완료함 [S999]"), [source("행사 완료")])
    assert any(issue["code"] == "unknown_source" for issue in unknown["warnings"])


def test_typo_style_and_term_checks_then_safe_revision():
    original = {"제목": "결과보고서", "요약": "매출 100만원입니다. [S1]", "본문": "○ 달성율 80%입니다. [S1]\n○ 보고서와 리포트 작성 완료함 [S1]"}
    sources = [source("매출 100만원, 달성률 80%, 보고서 작성 완료")]
    codes = {issue["code"] for issue in inspect_draft(original, sources)}
    assert {"bullet_style", "ending_style", "typo", "term_inconsistency"} <= codes
    result = review_draft(original, sources)
    assert not result["warnings"]
    assert result["draft"]["요약"] == "○ 매출 100만원임 [S1]"
    assert "달성률" in result["draft"]["본문"]


def test_insufficient_material_notice_is_not_a_factual_claim():
    result = review_draft(draft("○ 자료가 부족하여 추가 확인 필요함"), [])
    assert not result["blocking"]
    assert not result["warnings"]


def test_model_correction_is_called_once_and_reinspected():
    original = draft("○ 행사 완료함")
    fixed = draft("○ 행사 완료함 [S1]")
    client = FakeClient({"draft": fixed})
    result = review_draft(original, [source("행사 완료")], client)
    assert result["corrected"] and not result["blocking"]
    assert len(client.calls) == 1 and client.calls[0][0] == "review"


def test_model_cannot_invent_a_source_to_resolve_warning():
    original = draft("○ 행사 완료함")
    client = FakeClient(draft("○ 행사 완료함 [S999]"))
    result = review_draft(original, [source("행사 완료")], client)
    assert result["draft"] == original
    assert result["blocking"]
    assert len(client.calls) == 1


def test_uncited_title_number_blocks_output_without_styling_the_title():
    original = dict(draft("○ 매출 100만원 달성함 [S1]"), 제목="매출 999억원 보고서")
    result = review_draft(original, [source("매출 100만원 달성")])
    title_issues = [issue for issue in result["warnings"] if issue["field"] == "제목"]
    assert result["blocking"]
    assert {issue["code"] for issue in title_issues} == {"missing_source", "number_mismatch"}
    assert result["draft"]["제목"] == original["제목"]


def test_grounded_title_number_is_accepted_and_unknown_title_citation_is_rejected():
    original = dict(draft("○ 매출 100만원 달성함 [S1]"), 제목="매출 100만원 보고서 [S1]")
    result = review_draft(original, [source("매출 100만원 달성")])
    assert not result["warnings"]
    assert not result["blocking"]
    unknown = review_draft(dict(original, 제목="결과보고서 [S999]"), [source("매출 100만원 달성")])
    assert any(issue["code"] == "unknown_source" and issue["field"] == "제목" for issue in unknown["warnings"])


def test_cited_title_wrong_number_is_safely_corrected():
    original = dict(draft("○ 매출 100만원 달성함 [S1]"), 제목="매출 999억원 보고서 [S1]")
    result = review_draft(original, [source("매출 100만원 달성")])
    assert result["corrected"]
    assert not result["blocking"]
    assert result["draft"]["제목"] == "매출 100만원 보고서 [S1]"


def test_only_the_wrong_occurrence_of_repeated_number_is_corrected():
    original = draft("○ 예산 100만원, 매출 100만원임 [S1]")
    result = review_draft(original, [source("예산 100만원, 매출 80만원")])
    assert result["draft"]["본문"] == "○ 예산 100만원, 매출 80만원임 [S1]"
    assert not result["blocking"]


def test_multiple_number_replacements_do_not_shift_later_occurrences():
    original = draft("○ 예산 100만원, 매출 100만원임 [S1]")
    result = review_draft(original, [source("예산 1,000만원, 매출 80만원")])
    assert result["draft"]["본문"] == "○ 예산 1,000만원, 매출 80만원임 [S1]"
    assert not result["blocking"]


def test_additional_table_field_is_preserved_and_its_numeric_context_is_validated():
    original = dict(draft("○ 매출 100만원 달성함 [S1]"), 예산="100만원 [S1]")
    sources = [source("매출 100만원, 예산 50만원")]
    assert any(issue["field"] == "예산" and issue["code"] == "number_mismatch" for issue in inspect_draft(original, sources))
    result = review_draft(original, sources)
    assert result["draft"]["예산"] == "50만원 [S1]"
    assert not result["blocking"]
    assert not any(issue["field"] == "예산" and issue["code"] in {"bullet_style", "ending_style"} for issue in result["warnings"])


def test_unattributed_additional_fact_blocks_output_and_optional_empty_field_does_not():
    original = dict(draft("○ 매출 100만원 달성함 [S1]"), 결과="행사 완료", 비고="")
    result = review_draft(original, [source("매출 100만원 달성, 행사 완료")])
    assert result["draft"]["결과"] == "행사 완료"
    assert result["draft"]["비고"] == ""
    assert any(issue["field"] == "결과" and issue["code"] == "missing_source" for issue in result["warnings"])
    assert result["blocking"]


def test_model_review_cannot_silently_drop_registered_fields():
    original = dict(draft("○ 행사 완료함"), 예산="50만원 [S1]")
    client = FakeClient(draft("○ 행사 완료함 [S1]"))
    result = review_draft(original, [source("행사 완료, 예산 50만원")], client)
    assert result["draft"]["예산"] == "50만원 [S1]"
    assert not result["blocking"]


def test_model_cannot_improve_validation_by_removing_supported_performance_numbers():
    original = {"제목": "결과보고서", "요약": "○ 매출 100만원 달성함 [S1]", "본문": "○ 매출 100만원 달성함 [S1]\n○ 행사 완료함"}
    removed = {"제목": "결과보고서", "요약": "○ 추가 확인 필요함", "본문": "○ 행사 완료함 [S1]"}
    result = review_draft(original, [source("매출 100만원 달성, 행사 완료")], FakeClient(removed))
    assert result["draft"] == original
    assert "100만원" in result["draft"]["본문"]
    assert result["blocking"]


def test_saved_synonymous_term_does_not_break_numeric_source_context():
    original = draft("○ 앞으로 2개월임 [S1]")
    assert not review_draft(original, [source("향후 2개월")])["blocking"]


@pytest.mark.parametrize('text', ['목표 매출 120만원임', '매출 목표: 120만원임',
                                  '예측 매출 120만원임', '매출 120만원 달성 예정임'])
def test_target_or_forecast_number_cannot_support_an_achieved_result(text):
    original = draft('○ 매출 120만원을 달성함 [S1]')
    result = review_draft(original, [source(text)])
    assert result['blocking']
    assert result['draft'] == original
    assert any(item['code'] == 'number_mismatch' for item in result['warnings'])


def test_implicit_achievement_uses_actual_value_instead_of_equal_target():
    original = draft('○ 매출 120만원을 달성함 [S1]')
    result = review_draft(original, [source('목표 매출 120만원, 실제 매출 90만원임')])
    assert result['draft']['본문'] == '○ 매출 90만원을 달성함 [S1]'
    assert not result['blocking']


@pytest.mark.parametrize(('claim', 'text'), [
    ('○ 목표 매출 120만원임 [S1]', '매출 계획 120만원임'),
    ('○ 전망 매출 120만원임 [S1]', '예상 매출 120만원임'),
    ('○ 실적 매출 120만원임 [S1]', '실제 매출 120만원임'),
    ('○ 매출 120만원을 달성함 [S1]', '매출: 120만원임'),
])
def test_clear_role_synonyms_and_unknown_original_role_remain_compatible(claim, text):
    assert not review_draft(draft(claim), [source(text)])['blocking']


def test_explicit_company_in_claim_cannot_use_another_companys_same_number():
    result = review_draft(draft('○ A사 매출 120만원을 달성함 [S1]'),
                          [source('B사 매출 120만원을 달성함')])
    assert result['blocking']


def test_period_remains_required_after_numeric_role_normalization():
    result = review_draft(draft('○ 2026년 하반기 매출 120만원을 달성함 [S1]'),
                          [source('2026년 상반기 실제 매출 120만원임')])
    assert result['blocking']
