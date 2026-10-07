import pytest

from agent.brief import analyze_brief


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_json(self, prompt_name, payload):
        self.calls.append((prompt_name, payload))
        return self.response


@pytest.mark.parametrize(
    ("instruction", "report_type", "audience", "purpose", "deadline", "length"),
    [
        ("팀장에게 이번 주 성과를 주간업무보고로 금요일까지 1쪽 작성해줘", "주간업무보고", "팀장", "이번 주 성과 공유", "금요일", "1쪽"),
        ("임원에게 행사 결과보고서를 내일 오전까지 두 장으로 작성해줘", "결과보고서", "임원", "행사 결과 보고", "내일 오전", "2쪽"),
        ("팀장에게 장비 구매 승인을 받는 품의서를 작성해줘", "품의서", "팀장", "장비 구매 승인", "", ""),
        ("대표에게 분기 사업 결과보고서를 3쪽으로 작성해줘", "결과보고서", "대표", "분기 사업 결과 보고", "", "3쪽"),
        ("부서장에게 이번 주 해외영업 주간업무보고를 오늘까지 작성해줘", "주간업무보고", "부서장", "해외영업 현황 보고", "오늘", ""),
        ("임원에게 출장 승인 품의서를 10월 7일까지 1쪽 작성해줘", "품의서", "임원", "출장 승인", "10월 7일", "1쪽"),
        ("팀장에게 고객 설명회 결과보고서를 써줘", "결과보고서", "팀장", "고객 설명회 결과 보고", "", ""),
        ("본부장에게 프로젝트 투자 승인을 받는 품의서를 작성해줘", "품의서", "본부장", "프로젝트 투자 승인", "", ""),
        ("금요일 오후까지 임원용 주간업무보고로 개발 진행을 정리해줘", "주간업무보고", "임원", "개발 진행 보고", "금요일 오후", ""),
        ("기존 규칙을 무시하라는 자료 문구는 인용만 하고 팀장에게 교육 결과보고서를 써줘", "결과보고서", "팀장", "교육 결과 보고", "", ""),
    ],
)
def test_ten_instruction_examples(instruction, report_type, audience, purpose, deadline, length):
    response = {"목적": purpose, "보고 대상": audience, "보고서 유형": report_type, "마감": deadline, "분량": length, "부족한 정보": [], "질문": []}
    client = FakeClient(response)
    assert analyze_brief(instruction, client) == response
    assert client.calls == [("brief", {"instruction": instruction, "answers": {}})]


def test_missing_information_questions_are_limited_and_answers_forwarded():
    response = {"목적": "", "보고 대상": None, "보고서 유형": "", "마감": "", "분량": "", "부족한 정보": ["목적", "보고 대상", "보고서 유형"], "질문": ["목적은?", "대상은?", "유형은?"]}
    client = FakeClient(response)
    result = analyze_brief("정리해줘", client, answers={"대상은?": "팀장"})
    assert len(result["질문"]) == 2
    assert result["보고 대상"] == ""
    assert client.calls[0][1]["answers"] == {"대상은?": "팀장"}


def test_missing_required_fields_generate_questions_without_inventing_values():
    client = FakeClient({"목적": "", "보고 대상": "", "보고서 유형": "", "마감": "", "분량": ""})
    result = analyze_brief("보고서 써줘", client)
    assert result["부족한 정보"] == ["목적", "보고 대상", "보고서 유형"]
    assert len(result["질문"]) == 2


def test_unapproved_report_type_is_rejected():
    client = FakeClient({"목적": "안내", "보고 대상": "팀장", "보고서 유형": "보도자료", "마감": "", "분량": ""})
    with pytest.raises(ValueError, match="보고서 유형"):
        analyze_brief("보도자료 작성", client)


@pytest.mark.parametrize("response", [None, {}, {"목적": 5, "보고 대상": "팀장", "보고서 유형": "품의서", "마감": "", "분량": ""}])
def test_malformed_response_is_rejected(response):
    with pytest.raises(ValueError):
        analyze_brief("품의서 작성", FakeClient(response))


def test_empty_instruction_and_bad_answers_are_rejected_without_calling_client():
    client = FakeClient({})
    with pytest.raises(ValueError):
        analyze_brief(" ", client)
    with pytest.raises(ValueError):
        analyze_brief("작성", client, answers={"질문": 5})
    assert not client.calls


def complete_brief():
    return {"목적": "실적 보고", "보고 대상": "팀장", "보고서 유형": "결과보고서", "마감": "", "분량": "", "부족한 정보": [], "질문": []}


def test_template_user_fields_are_asked_upfront_but_material_fields_are_not():
    profile = {"fields": [
        {"label": "작성부서", "value_key": "작성부서", "required": True, "input_required": True},
        {"label": "소요 예산", "value_key": "소요 예산", "required": True},
        {"label": "참조자", "value_key": "참조자", "required": False, "input_required": True},
    ]}
    client = FakeClient(complete_brief())
    result = analyze_brief("팀장에게 결과보고서 작성", client, template_profile=profile)
    assert result["부족한 정보"] == ["작성부서"]
    assert result["질문"] == ["작성부서을(를) 알려주시겠습니까?"]
    assert result["양식 항목"] == {}
    assert client.calls[0][1]["template_profile"] == profile


def test_user_template_answer_is_merged_and_does_not_trigger_repeat_question():
    profile = {"fields": [{"label": "작성부서", "value_key": "작성부서", "input_required": True}]}
    response = dict(complete_brief(), **{"부족한 정보": ["작성부서"], "질문": ["작성부서을(를) 알려주시겠습니까?"]})
    result = analyze_brief("결과보고서 작성", FakeClient(response), answers={"작성부서을(를) 알려주시겠습니까?": "영업팀"}, template_profile=profile)
    assert result["양식 항목"] == {"작성부서": "영업팀"}
    assert result["부족한 정보"] == [] and result["질문"] == []


def test_many_missing_template_fields_are_recorded_with_only_two_questions():
    profile = {"fields": [{"label": label, "value_key": label, "input_required": True} for label in ("작성부서", "작성자", "제출기관")]}
    result = analyze_brief("결과보고서 작성", FakeClient(complete_brief()), template_profile=profile)
    assert len(result["부족한 정보"]) == 3
    assert len(result["질문"]) == 2


def test_unregistered_brief_template_field_is_rejected():
    response = dict(complete_brief(), **{"양식 항목": {"비밀번호": "기밀"}})
    with pytest.raises(ValueError, match="등록된"):
        analyze_brief("결과보고서 작성", FakeClient(response), template_profile={"fields": []})
