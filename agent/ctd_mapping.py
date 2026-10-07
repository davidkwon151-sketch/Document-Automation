"""Suggest CTD source-to-section links without creating product facts."""

from hashlib import sha256
import json

from agent.ctd import (CTD_SECTIONS, _PRODUCT, _VARIANT, _canonical,
                       _complete_chunk, _declared_match, _heading,
                       prepare_ctd_package)
from agent.multimodal_intake import generation_sources


def suggest_ctd_section_map(intake, *, product_name, product_variant='',
                            selected_sections=None, confirmed_substance_links=None, client):
    """Return a review-only map; callers must obtain human confirmation before use."""
    if not isinstance(product_name, str) or not product_name.strip() or len(product_name) > 200:
        raise ValueError('선택 제품명이 필요함')
    if not isinstance(product_variant, str) or len(product_variant) > 200:
        raise ValueError('선택 제형·함량 형식이 잘못됨')
    catalog = {section['section_id']: section for section in CTD_SECTIONS}
    selected = list(catalog) if selected_sections is None else selected_sections
    if (not isinstance(selected, list) or not selected
            or len(selected) != len(set(selected))
            or any(identifier not in catalog for identifier in selected)):
        raise ValueError('등록된 CTD 절을 중복 없이 선택해야 함')
    if client is None or not callable(getattr(client, 'generate_json', None)):
        raise ValueError('CTD 매핑에 실제 모델 연결이 필요함')

    sources = generation_sources(intake)
    links = {} if confirmed_substance_links is None else confirmed_substance_links
    # The engine verifies every link against the actual confirmed text and SHA.
    # Do this before any model request, including when the model would return null.
    prepare_ctd_package(sources, product_name=product_name,
                        product_variant=product_variant,
                        selected_sections=selected,
                        confirmed_substance_links=links)
    by_file = {}
    for source in sources:
        if not isinstance(source.get('document_sha256'), str) or not source['document_sha256']:
            raise ValueError('원자료 SHA가 필요함')
        by_file.setdefault((source['filename'], source['document_sha256']), []).append(source)
    matching_files = set()
    for key, members in by_file.items():
        names = [_canonical(name) for source in members for name in _PRODUCT.findall(source['text'])]
        variants = list(dict.fromkeys(_canonical(variant) for source in members
                                      for variant in _VARIANT.findall(source['text'])))
        if (names and all(_declared_match(name, product_name) for name in names)
                and (not product_variant or variants and _canonical(product_variant) in
                     {variants[0], ' '.join(variants), ' '.join(reversed(variants))})):
            matching_files.add(key)

    deferred = [
        {'source_id': source['source_id'], 'reason': (
            '선택 제품·제형의 원문 선언이 없음' if
            ((source['filename'], source['document_sha256']) not in matching_files
             and source['document_sha256'] not in links) else
            '원문 문단·표 행이 중간에서 잘림' if not _complete_chunk(source) else
            '원문 조각이 AI 매핑 제안 한도 1200자를 초과함')}
        for source in sources
        if (((source['filename'], source['document_sha256']) not in matching_files
             and source['document_sha256'] not in links)
            or not _complete_chunk(source) or len(source['text']) > 1200)
    ]
    candidates = [source for source in sources
                  if ((source['filename'], source['document_sha256']) in matching_files
                      or source['document_sha256'] in links)
                  and _complete_chunk(source) and len(source['text']) <= 1200]
    if not candidates:
        raise ValueError('선택 제품·제형의 확인된 완전한 원문 조각이 없음')

    accepted = {}
    calls = 0
    for first in range(0, len(candidates), 64):
        batch = candidates[first:first + 64]
        payload = {
            'product_name': product_name, 'product_variant': product_variant,
            'sections': [{'section_id': identifier, 'title': catalog[identifier]['title']}
                         for identifier in selected],
            'sources': [{**{key: source.get(key) for key in ('source_id', 'text', 'page', 'sheet')},
                         'source_scope': ('confirmed_substance_link'
                                          if source['document_sha256'] in links
                                          else 'selected_product_document')}
                        for source in batch],
        }
        # The only model call. The LLM boundary loads prompts/ctd_map.md.
        response = client.generate_json('ctd_map', payload)
        calls += 1
        if not isinstance(response, dict) or set(response) != {'assignments'} or not isinstance(response['assignments'], list):
            raise ValueError('CTD 매핑 응답은 출처 ID와 절 ID만 포함해야 함')
        seen = set()
        known = {source['source_id']: source for source in batch}
        for assignment in response['assignments']:
            if not isinstance(assignment, dict) or set(assignment) != {'source_id', 'section_id'}:
                raise ValueError('CTD 매핑에 임의 사실 또는 누락 항목이 있음')
            source_id, section_id = assignment['source_id'], assignment['section_id']
            if not isinstance(source_id, str) or source_id not in known or source_id in seen:
                raise ValueError('CTD 매핑에 미등록·중복·다른 배치 출처 ID가 있음')
            if section_id is not None and (not isinstance(section_id, str) or section_id not in selected):
                raise ValueError('CTD 매핑에 미등록 절 ID가 있음')
            if (section_id is not None and known[source_id]['document_sha256'] in links
                    and not section_id.startswith('3.2.S.')):
                raise ValueError('확인된 원료 원자료는 3.2.S 절에만 연결할 수 있음')
            seen.add(source_id)
            if section_id is not None:
                explicit = [section['section_id'] for section in CTD_SECTIONS
                            if _heading(section, known[source_id]['text'])]
                if explicit and explicit != [section_id]:
                    raise ValueError('원문의 명시 CTD 절과 모델 제안이 일치하지 않음')
                accepted[source_id] = section_id
        if seen != set(known):
            raise ValueError('CTD 매핑 응답에서 원자료 ID가 누락됨')

    section_map = {identifier: [source['source_id'] for source in candidates
                                if accepted.get(source['source_id']) == identifier]
                   for identifier in selected}
    section_map = {identifier: ids for identifier, ids in section_map.items() if ids}
    if section_map:
        checked = prepare_ctd_package(sources, product_name=product_name,
                                      product_variant=product_variant,
                                      section_map=section_map, selected_sections=selected,
                                      confirmed_substance_links=links)
        for section in checked['sections']:
            assigned = set(section_map.get(section['section_id'], ()))
            linked = {item['source_id'] for item in section['evidence']}
            if not assigned <= linked:
                raise ValueError('CTD 절과 원자료의 제품·제형 또는 원문 범위가 일치하지 않음')

    proposal_fingerprint = sha256(json.dumps({
        'intake': intake['fingerprint'], 'product': product_name,
        'variant': product_variant, 'selected_sections': selected,
        'confirmed_substance_links': links,
        'source_receipts': {source['source_id']: source.get('verification_receipt')
                            for source in sources},
        'section_map': section_map,
    }, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {
        'section_map': section_map,
        'proposal_fingerprint': proposal_fingerprint,
        'deferred_sources': deferred,
        'unmapped_source_ids': [source['source_id'] for source in sources
                                if source['source_id'] not in accepted],
        'source_count': len(sources), 'model_request_attempts': calls,
        'actual_model_requests': calls if getattr(client, 'provider', None) in {'openai', 'local'} else 0,
        'mapping_origin': 'model_proposal', 'requires_confirmation': True,
        'submission_ready': False,
    }
