"""사용자가 명시적으로 저장한 동의 표현만 재사용함."""

SAFE_TERMS = {
    "리포트": "보고서",
    "금번": "이번",
    "차주": "다음 주",
    "당분기": "이번 분기",
    "향후": "앞으로",
    "재 검토": "재검토",
}


def validate_preferences(preferences: dict | None) -> dict:
    if preferences is None:
        return {"preferred_terms": {}}
    if not isinstance(preferences, dict) or set(preferences) - {"preferred_terms"}:
        raise ValueError("선호 설정은 preferred_terms만 허용함")
    terms = preferences.get("preferred_terms", {})
    if not isinstance(terms, dict) or any(
        not isinstance(before, str) or not isinstance(after, str) or SAFE_TERMS.get(before) != after
        for before, after in terms.items()
    ):
        raise ValueError("사실이나 수치를 바꾸지 않는 허용된 동의 표현만 저장할 수 있음")
    return {"preferred_terms": dict(terms)}


def derive_preferences(baseline: dict, final: dict, existing: dict | None = None) -> dict:
    """명시적 저장 시 호출함. 보고서 원문, 숫자, 사람·회사 이름은 저장하지 않음."""
    if not isinstance(baseline, dict) or not isinstance(final, dict) or any(
        not isinstance(value, str) for value in list(baseline.values()) + list(final.values())
    ):
        raise ValueError("수정 전후 보고서 항목은 문자열 객체여야 함")
    preferences = validate_preferences(existing)
    for field in baseline.keys() & final.keys():
        before, after = baseline[field], final[field]
        for source, target in SAFE_TERMS.items():
            if source in before and before.count(source) > after.count(source) and after.count(target) > before.count(target):
                preferences["preferred_terms"][source] = target
    return preferences


def apply_preferences(draft: dict, preferences: dict | None) -> dict:
    terms = validate_preferences(preferences)["preferred_terms"]
    result = {}
    for field, value in draft.items():
        for before, after in terms.items():
            value = value.replace(before, after)
        result[field] = value
    return result
