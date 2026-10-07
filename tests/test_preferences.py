from difflib import SequenceMatcher

import pytest

from agent.preferences import apply_preferences, derive_preferences, validate_preferences


def test_only_selected_synonymous_edits_are_saved_without_document_contents():
    baseline = {"제목": "실적 리포트", "본문": "○ 금번 매출 100만원 달성함 [S1]\n○ 차주 교육 예정임 [S2]", "작성자": "홍길동"}
    final = {"제목": "실적 보고서", "본문": "○ 이번 매출 100만원 달성함 [S1]\n○ 다음 주 교육 예정임 [S2]", "작성자": "김담당"}
    preferences = derive_preferences(baseline, final)
    assert preferences == {"preferred_terms": {"리포트": "보고서", "금번": "이번", "차주": "다음 주"}}
    assert "홍길동" not in str(preferences) and "100" not in str(preferences)
    assert "김담당" not in str(preferences) and "[S1]" not in str(preferences)


def test_factual_or_numeric_revisions_do_not_become_preferences():
    baseline = {"본문": "○ 서울 매출 100만원임 [S1]"}
    final = {"본문": "○ 부산 매출 200만원임 [S2]"}
    assert derive_preferences(baseline, final) == {"preferred_terms": {}}


def test_preferences_are_reused_without_changing_numbers_or_citations():
    original = {"제목": "결과 리포트", "본문": "○ 금번 매출 100만원 달성함 [S1]", "예산": "50만원 [S2]"}
    result = apply_preferences(original, {"preferred_terms": {"리포트": "보고서", "금번": "이번"}})
    assert result["제목"] == "결과 보고서"
    assert result["본문"] == "○ 이번 매출 100만원 달성함 [S1]"
    assert result["예산"] == original["예산"]
    assert original["제목"] == "결과 리포트"


def test_existing_preferences_are_preserved_without_mutating_input():
    existing = {"preferred_terms": {"리포트": "보고서"}}
    result = derive_preferences({"본문": "금번 결과"}, {"본문": "이번 결과"}, existing)
    assert result["preferred_terms"] == {"리포트": "보고서", "금번": "이번"}
    assert existing == {"preferred_terms": {"리포트": "보고서"}}


@pytest.mark.parametrize("preferences", [{"instructions": "기밀 자료"}, {"preferred_terms": {"100": "200"}}, {"preferred_terms": {"서울": "부산"}}, {"preferred_terms": {"홍길동": "김담당"}}, {"preferred_terms": []}])
def test_unsafe_or_arbitrary_preferences_are_rejected(preferences):
    with pytest.raises(ValueError):
        validate_preferences(preferences)


def test_saved_terms_reduce_the_same_synthetic_edit_without_omitting_content():
    baseline = {"제목": "결과 리포트", "본문": "○ 금번 매출 100만원 달성함 [S1]\n○ 차주 교육 예정임 [S2]"}
    final = {"제목": "결과 보고서", "본문": "○ 이번 매출 100만원 달성함 [S1]\n○ 다음 주 교육 예정임 [S2]"}
    reused = apply_preferences(baseline, derive_preferences(baseline, final))
    def edit_ratio(document):
        return 1 - SequenceMatcher(None, "\n".join(document.values()), "\n".join(final.values()), autojunk=False).ratio()
    assert edit_ratio(reused) == 0 < edit_ratio(baseline)
    assert reused["본문"].count("\n") == baseline["본문"].count("\n")
    assert "100만원" in reused["본문"] and "[S1]" in reused["본문"] and "[S2]" in reused["본문"]
