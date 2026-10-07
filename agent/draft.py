"""유형별 프롬프트와 검색 자료로 양식 항목을 작성함."""

from agent.brief import REPORT_TYPES, model_profile, profile_fields
from agent.preferences import apply_preferences, validate_preferences
from agent.review import validate_draft
from agent.field_citations import field_citations, is_selection_field, split_field_citations
from templates.pdf_annex import draft_limit


def create_draft(brief: dict, sources: list[dict], client=None, template_profile: dict | None = None, preferences: dict | None = None) -> dict:
    if not isinstance(brief, dict) or brief.get("보고서 유형") not in REPORT_TYPES:
        raise ValueError("지원하는 보고서 유형이 지정되어야 함")
    if brief.get("부족한 정보"):
        raise ValueError("필수 정보를 보완한 후 초안을 작성해야 함")
    if not isinstance(sources, list) or any(
        not isinstance(source, dict)
        or not isinstance(source.get("source_id"), str)
        or not isinstance(source.get("text"), str)
        for source in sources
    ):
        raise ValueError("검색 자료에 출처 ID와 본문이 필요함")
    ids = [source["source_id"] for source in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("검색 자료 출처 ID가 중복됨")
    fields = profile_fields(template_profile)
    preferred = validate_preferences(preferences)
    constraints = (template_profile or {}).get("constraints", {})
    if not isinstance(constraints, dict):
        raise ValueError("양식 constraints는 JSON 객체여야 함")
    for name in ("summary_max_lines", "body_max_chars"):
        value = constraints.get(name)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
            raise ValueError(f"양식 분량 제한은 양의 정수여야 함: {name}")
    if client is None:
        from llm.client import LLMClient

        client = LLMClient()
    payload = {"brief": brief, "sources": sources}
    if template_profile is not None:
        payload["template_profile"] = model_profile(template_profile)
    if preferences is not None:
        payload["preferences"] = preferred
    result = client.generate_json("draft", payload)
    if isinstance(result, dict):
        for field in fields:
            key = field['value_key']
            value = brief.get('양식 항목', {}).get(key)
            source_id = brief.get('양식 항목 출처', {}).get(key)
            if field.get('input_required') and isinstance(value, str) and source_id in ids:
                result[key] = '\n'.join(f'{line} [{source_id}]' if line.strip() else line for line in value.split('\n'))
            elif field.get('input_required') and is_selection_field(field):
                result[key] = ''
    allowed = {field["value_key"] for field in fields}
    draft = validate_draft(result, allowed_fields=allowed)
    protected = {field['value_key']: draft[field['value_key']] for field in fields
                 if field['value_key'] in draft and (field.get('input_required') or is_selection_field(field))}
    draft = apply_preferences(draft, preferred)
    draft.update(protected)
    literals = {field['value_key']: brief.get('양식 항목', {}).get(field['value_key']) for field in fields
                if field.get('input_required') and brief.get('양식 항목 출처', {}).get(field['value_key']) in ids}
    for field in fields:
        key = field["value_key"]
        if field.get("required", True) and not draft.get(key, "").strip():
            raise ValueError(f"필수 양식 항목을 보완해야 함: {field['label']}")
        draft.setdefault(key, "")
        maximum = draft_limit(template_profile, field)
        if maximum is not None:
            displayed = (split_field_citations(draft[key], field, literal=literals.get(key))[0]
                         if (template_profile or {}).get('citation_mode') == 'sidecar' or is_selection_field(field)
                         else draft[key])
            if len(displayed) > maximum:
                raise ValueError(f"양식 항목 분량을 초과함: {field['label']}")
    if len(draft["요약"].splitlines()) > (constraints.get("summary_max_lines") or 3):
        raise ValueError("양식에 허용된 요약 줄 수를 초과함")
    body_limit = constraints.get("body_max_chars")
    if body_limit is not None and len(draft["본문"]) > body_limit:
        raise ValueError("양식에 허용된 본문 분량을 초과함")
    by_key = {field['value_key']: field for field in fields}
    cited = {source_id for key, value in draft.items()
             for source_id in field_citations(value, by_key.get(key), literal=literals.get(key))}
    if cited - set(ids):
        raise ValueError("초안에 존재하지 않는 출처 ID가 있음")
    return draft
