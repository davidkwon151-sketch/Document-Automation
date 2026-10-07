import pytest

from agent.boss_review import boss_review


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_json(self, name, payload):
        self.calls.append((name, payload))
        return self.response


QUESTIONS = ["매출은 얼마인가?", "예산 집행은 얼마인가?", "다음 일정은 무엇인가?"]
DRAFT = {"제목": "결과보고서", "요약": "□ 매출 100만원 달성함 [S1]", "본문": "○ 매출 100만원 달성함 [S1]"}
BRIEF = {"보고 대상": "임원", "보고서 유형": "결과보고서"}
SOURCES = [{"source_id": "S1", "filename": "실적.xlsx", "sheet": "실적", "location": "시트 실적", "page": None, "text": "매출 100만원 달성, 예산 50만원 집행", "score": 1.0}]


def test_supported_answer_is_added_and_unanswered_questions_are_flagged():
    client = FakeClient({"questions": QUESTIONS, "answers": [{"question": QUESTIONS[1], "answer": "○ 예산 50만원 집행함 [S1]"}]})
    result = boss_review(DRAFT, BRIEF, SOURCES, client)
    assert result["questions"] == QUESTIONS
    assert "예산 50만원 집행함 [S1]" in result["draft"]["본문"]
    assert len(result["additional_checks"]) == 2
    assert all(value.startswith("추가 확인 필요:") for value in result["additional_checks"])
    assert client.calls == [("boss_review", {"draft": DRAFT, "brief": BRIEF, "sources": SOURCES})]


@pytest.mark.parametrize("answer", ["○ 예산 500만원 집행함 [S1]", "○ 행사 성공함 [S1]", "○ 예산 50만원 집행함 [S999]", "○ 예산 50만원 집행함"])
def test_unsupported_or_uncited_answer_is_never_added(answer):
    client = FakeClient({"questions": QUESTIONS, "answers": [{"question": QUESTIONS[1], "answer": answer}]})
    result = boss_review(DRAFT, BRIEF, SOURCES, client)
    assert result["draft"] == DRAFT
    assert len(result["additional_checks"]) == 3


def test_empty_sources_produce_three_additional_checks():
    empty = {"제목": "결과보고서", "요약": "□ 자료가 부족하여 추가 확인 필요함", "본문": "○ 자료가 부족하여 추가 확인 필요함"}
    result = boss_review(empty, BRIEF, [], FakeClient({"questions": QUESTIONS, "answers": []}))
    assert result["draft"] == empty
    assert len(result["additional_checks"]) == 3


@pytest.mark.parametrize("questions", [QUESTIONS[:2], QUESTIONS + ["추가 질문"], ["같음", "같음", "같음"], ["", "질문", "다른 질문"]])
def test_question_count_and_uniqueness_are_validated(questions):
    with pytest.raises(ValueError, match="3개"):
        boss_review(DRAFT, BRIEF, SOURCES, FakeClient({"questions": questions, "answers": []}))


def test_answer_with_style_problem_is_left_for_user_confirmation():
    result = boss_review(DRAFT, BRIEF, SOURCES, FakeClient({"questions": QUESTIONS, "answers": [{"question": QUESTIONS[1], "answer": "○ 예산 50만원 집행했습니다 [S1]"}]}))
    assert result["draft"] == DRAFT
    assert len(result["additional_checks"]) == 3


def test_boss_review_preserves_custom_template_fields_and_existing_facts():
    original = dict(DRAFT, 예산="50만원 [S1]")
    client = FakeClient({"questions": QUESTIONS, "answers": [{"question": QUESTIONS[1], "answer": "○ 예산 50만원 집행함 [S1]"}]})
    result = boss_review(original, BRIEF, SOURCES, client)
    assert result["draft"]["예산"] == "50만원 [S1]"
    assert "매출 100만원 달성함 [S1]" in result["draft"]["본문"]
