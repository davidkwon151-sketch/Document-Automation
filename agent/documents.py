"""Document-purpose context, independent of an organization's form layout."""

DOCUMENT_KINDS = {
    'report': {'title': '보고서·기안', 'style': '한국어 보고 본문은 개조식·두괄식·~함/~임 종결을 적용함'},
    'application': {'title': '신청서·제출서류', 'style': '원본 신청 항목·선택값·고정 셀의 문체와 길이를 우선함'},
    'plan': {'title': '계획서·제안서', 'style': '계획·가정·확정 실적을 구분하고 핵심 제안과 요청을 먼저 작성함'},
    'official_letter': {'title': '공문·안내문', 'style': '수신처·목적·요청·기한을 구분하고 원본 양식의 공문 문체를 적용함'},
    'minutes': {'title': '회의록', 'style': '실제 논의·결정·미결 사항·담당자·기한을 구분하며 발언·합의를 추정하지 않음'},
    'other': {'title': '기타 사내외 문서', 'style': '등록된 항목과 원본 안내에 따라 작성하고 임의의 보고서 구조를 강제하지 않음'},
}


def document_context(profile=None, kind=None):
    selected = kind or (profile or {}).get('document_kind', 'report')
    if not isinstance(selected, str) or selected not in DOCUMENT_KINDS:
        raise ValueError('지원하지 않는 문서 종류임')
    if selected == 'report' and profile is None:
        return None, selected
    enriched = {**(profile or {}), 'document_kind': selected, 'document_context': DOCUMENT_KINDS[selected]}
    return enriched, selected


def style_exempt_fields(profile):
    exempt = {field['value_key'] for field in (profile or {}).get('fields', [])
              if field.get('narrative_style_required') is False}
    if (profile or {}).get('document_kind') in {'application', 'official_letter', 'minutes', 'other'}:
        exempt |= {'요약', '본문'}
    return exempt
