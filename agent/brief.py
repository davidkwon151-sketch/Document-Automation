"""사용자 지시를 검증 가능한 보고서 작성 요청으로 변환함."""

from copy import deepcopy
from templates.value_rules import validate_rule_profile

REPORT_TYPES = ("주간업무보고", "결과보고서", "품의서")
BRIEF_FIELDS = ("목적", "보고 대상", "보고서 유형", "마감", "분량")


def model_profile(profile: dict | None) -> dict | None:
    """시험값·QA 기록을 실제 문서 작성의 근거로 전달하지 않음."""
    if profile is None:
        return None
    profile_fields(profile)
    result = deepcopy(profile)
    for key in ('demo_values', 'demo_notice', 'qa', 'qa_values', 'test_values', 'expected_values', 'verification', 'profile_verification', 'values', 'answers', 'user_values',
                'field_values', 'draft', 'locked_fields', 'sources', 'documents'):
        result.pop(key, None)
    for field in result.get('fields', []):
        for key in ('value', 'default', 'default_value', 'answer', 'user_value', 'actual_value', 'demo_values',
                    'qa', 'qa_values', 'test_values', 'expected_values', 'verification', 'profile_verification', 'demo_notice'):
            field.pop(key, None)
    if isinstance(result.get('repeat_source_profile'), dict):
        result['repeat_source_profile'] = model_profile(result['repeat_source_profile'])
    return result


def profile_fields(template_profile: dict | None) -> list[dict]:
    if template_profile is None:
        return []
    if not isinstance(template_profile, dict) or not isinstance(template_profile.get("fields", []), list):
        raise ValueError("양식 프로필에 fields 목록이 필요함")
    fields = []
    for field in template_profile.get("fields", []):
        if not isinstance(field, dict):
            raise ValueError("양식 항목은 JSON 객체여야 함")
        key = field.get("value_key")
        label = field.get("label", key)
        if not isinstance(key, str) or not key.strip() or not isinstance(label, str) or not label.strip():
            raise ValueError("양식 항목의 value_key와 label 문자열이 필요함")
        if len(key) > 100 or any(character in key for character in "\r\n\t"):
            raise ValueError("양식 항목의 value_key 형식이 잘못됨")
        if any(flag in field and not isinstance(field[flag], bool) for flag in ("required", "input_required")):
            raise ValueError("양식 항목의 필수 여부는 boolean이어야 함")
        maximum = field.get("max_chars")
        if maximum is not None and (not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0):
            raise ValueError("양식 항목 max_chars는 양의 정수여야 함")
        fields.append(dict(field, value_key=key.strip(), label=label.strip()))
    validate_rule_profile(template_profile)
    return fields


def analyze_brief(instruction: str, client=None, answers: dict | None = None, template_profile: dict | None = None) -> dict:
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("지시문을 입력해야 함")
    if answers is not None and (
        not isinstance(answers, dict)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in answers.items())
    ):
        raise ValueError("보완 답변은 질문과 답변 문자열의 객체여야 함")
    fields = profile_fields(template_profile)
    if client is None:
        from llm.client import LLMClient

        client = LLMClient()
    payload = {"instruction": instruction.strip(), "answers": answers or {}}
    if template_profile is not None:
        payload["template_profile"] = model_profile(template_profile)
    result = client.generate_json("brief", payload)
    if not isinstance(result, dict) or any(field not in result for field in BRIEF_FIELDS):
        raise ValueError("지시 해석 응답에 필수 항목이 없음")
    brief = {}
    for field in BRIEF_FIELDS:
        value = result[field]
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError(f"지시 해석 항목은 문자열이어야 함: {field}")
        brief[field] = value.strip()
    if brief["보고서 유형"] and brief["보고서 유형"] not in REPORT_TYPES:
        raise ValueError("보고서 유형은 주간업무보고, 결과보고서, 품의서만 허용함")
    missing = result.get("부족한 정보", [])
    questions = result.get("질문", [])
    if not isinstance(missing, list) or any(not isinstance(item, str) for item in missing):
        raise ValueError("부족한 정보는 문자열 목록이어야 함")
    if not isinstance(questions, list) or any(not isinstance(item, str) for item in questions):
        raise ValueError("질문은 문자열 목록이어야 함")
    brief["부족한 정보"] = list(dict.fromkeys(item.strip() for item in missing if item.strip()))
    kind = (template_profile or {}).get('document_kind', 'report')
    if kind != 'report':
        # Internal API classification stays separate from the explicitly selected form purpose.
        brief['보고서 유형'] = brief['보고서 유형'] or '결과보고서'
        brief['부족한 정보'] = [item for item in brief['부족한 정보'] if item != '보고서 유형']
        questions = [question for question in questions if '보고서 유형' not in question]
    for field in ("목적", "보고 대상", "보고서 유형"):
        if not brief[field] and field not in brief["부족한 정보"]:
            brief["부족한 정보"].append(field)
    brief["질문"] = list(dict.fromkeys(question.strip() for question in questions if question.strip()))[:2]
    if template_profile is not None:
        values = result.get("양식 항목", {})
        allowed = {field["value_key"] for field in fields}
        if not isinstance(values, dict) or any(key not in allowed or not isinstance(value, str) for key, value in values.items()):
            raise ValueError("양식 항목 응답에는 등록된 항목의 문자열만 허용함")
        values = {key: value.strip() for key, value in values.items()}
        for field in fields:
            key, label = field["value_key"], field["label"]
            for question, answer in (answers or {}).items():
                if question in {key, label} or question == f"{label}을(를) 알려주시겠습니까?":
                    if answer.strip():
                        values[key] = answer.strip()
            if field.get("input_required") and field.get("required", True) and not values.get(key):
                if label not in brief["부족한 정보"]:
                    brief["부족한 정보"].append(label)
            elif values.get(key):
                brief["부족한 정보"] = [item for item in brief["부족한 정보"] if item not in {key, label}]
        brief["양식 항목"] = values
        answered_labels = {field["label"] for field in fields if values.get(field["value_key"])}
        brief["질문"] = [question for question in brief["질문"] if not any(label in question for label in answered_labels)]
    if brief["부족한 정보"] and not brief["질문"]:
        brief["질문"] = [f"{field}을(를) 알려주시겠습니까?" for field in brief["부족한 정보"][:2]]
    elif brief["부족한 정보"]:
        for field in brief["부족한 정보"]:
            question = f"{field}을(를) 알려주시겠습니까?"
            if len(brief["질문"]) < 2 and not any(field in item for item in brief["질문"]):
                brief["질문"].append(question)
    if not brief["부족한 정보"]:
        brief["질문"] = []
    return brief
