"""DMF 3.2.S originals to a traceable CTD 2.3.S review draft.

This is an extractive working document, not a regulatory quality assessment.
"""

from hashlib import sha256
import json
import re

from agent.ctd import (_SUBSTANCE, _canonical, _evidence, prepare_ctd_package)
from agent.multimodal_intake import validate_generation_source
from agent.ra_workflows import _source_records


QOS_S_SECTIONS = tuple({
    'section_id': f'2.3.S.{number}', 'source_section_id': f'3.2.S.{number}',
    'title': title,
} for number, title in enumerate((
    '원료의약품 일반 정보', '원료의약품 제조', '원료의약품 특성',
    '원료의약품 관리', '표준품·표준물질', '용기·포장', '안정성',
), 1))

_MANUFACTURER = re.compile(
    r'(?im)^\s*(?:원료(?:의약품)?\s*제조원|원료(?:의약품)?\s*제조업체|제조원|'
    r'(?:api|drug\s+substance)\s+manufacturer|manufacturer)\s*[:：|]\s*([^\r\n|]+)')
_CTD_SCOPE = re.compile(r'(?im)^\s*(3\.2\.[SP]\.\d+)(?!\d)')


def _manufacturer_matches(members):
    """A generic Manufacturer label is API evidence only on the DMF cover or S.2."""
    scope = None
    for source in members:
        markers = list(_CTD_SCOPE.finditer(source['text']))
        index = 0
        for match in _MANUFACTURER.finditer(source['text']):
            while index < len(markers) and markers[index].start() < match.start():
                scope = markers[index][1]
                index += 1
            label = source['text'][match.start():match.start(1)]
            if (re.search(r'원료|\bapi\b|drug\s+substance', label, re.I)
                    or scope is None or scope.startswith('3.2.S.2')):
                yield source, match
        if markers:
            scope = markers[-1][1]


def propose_dmf_options(sources):
    """Show names actually declared in verified originals, with exact citations."""
    groups = {}
    for source in _source_records(sources).values():
        validate_generation_source(source)
        groups.setdefault(source['document_sha256'], []).append(source)
    proposals = []
    for digest, members in groups.items():
        names = {'substance_names': {}, 'manufacturer_names': {}}
        for source in members:
            for match in _SUBSTANCE.finditer(source['text']):
                value = match[1].strip()
                if value:
                    names['substance_names'].setdefault(_canonical(value), {
                        'value': value, 'evidence': []})['evidence'].append(
                            _evidence(source, value, match.start(1)))
        for source, match in _manufacturer_matches(members):
            value = match[1].strip()
            if value:
                names['manufacturer_names'].setdefault(_canonical(value), {
                    'value': value, 'evidence': []})['evidence'].append(
                        _evidence(source, value, match.start(1)))
        if any(names.values()):
            proposals.append({'filename': members[0]['filename'],
                              'document_sha256': digest,
                              **{key: list(value.values()) for key, value in names.items()}})
    return proposals


def prepare_qos_package(sources, *, product_name, dmf_links, product_variant='',
                        selected_sections=None, section_map=None):
    """Copy confirmed DMF section evidence into corresponding 2.3.S slots.

    ``dmf_links`` is a human confirmation per original SHA: the literal API and
    manufacturer names in that original, and the selected finished product.
    Neither filename nor DMF number creates this relationship.
    """
    catalogue = {item['section_id']: item for item in QOS_S_SECTIONS}
    selected = list(catalogue) if selected_sections is None else selected_sections
    if (not isinstance(selected, list) or not selected or len(selected) != len(set(selected))
            or any(value not in catalogue for value in selected)):
        raise ValueError('2.3.S.1–2.3.S.7 절을 중복 없이 선택해야 함')
    if (not isinstance(dmf_links, dict) or not dmf_links
            or any(not re.fullmatch(r'[0-9a-f]{64}', digest)
                   or not isinstance(link, dict)
                   or set(link) != {'substance_name', 'manufacturer_name',
                                    'product_name', 'confirmed'}
                   or link.get('confirmed') is not True
                   or link.get('product_name') != product_name
                   or any(not isinstance(link.get(key), str)
                          or not link[key].strip() or len(link[key]) > 200
                          for key in ('substance_name', 'manufacturer_name'))
                   for digest, link in dmf_links.items())):
        raise ValueError('현재 제품·원료명·제조원·원본 SHA의 담당자 확인 연결이 필요함')
    records = _source_records(sources)
    if set(dmf_links) - {source.get('document_sha256') for source in records.values()}:
        raise ValueError('확인한 DMF 원본 SHA가 현재 첨부와 다름')
    if (len({_canonical(link['substance_name']) for link in dmf_links.values()}) != 1
            or len({_canonical(link['manufacturer_name']) for link in dmf_links.values()}) != 1):
        raise ValueError('서로 다른 원료 또는 제조원의 DMF를 한 2.3.S 초안에 합치지 않음')
    chosen = [source for source in records.values()
              if source.get('document_sha256') in dmf_links]
    if not chosen:
        raise ValueError('연결된 DMF 원자료가 없음')
    groups = {}
    for source in chosen:
        validate_generation_source(source)
        groups.setdefault(source['document_sha256'], []).append(source)
    declarations = {}
    for digest, members in groups.items():
        link = dmf_links[digest]
        found = {}
        for field, pattern, key in (('substance', _SUBSTANCE, 'substance_name'),
                                    ('manufacturer', _MANUFACTURER, 'manufacturer_name')):
            matches = ([(source, match) for source in members
                        for match in pattern.finditer(source['text'])]
                       if field == 'substance' else list(_manufacturer_matches(members)))
            if (not matches or any(_canonical(match[1]) != _canonical(link[key])
                                   for _, match in matches)):
                raise ValueError(f'DMF 원문의 {field} 선언이 확인한 이름과 일치하지 않음')
            found[field] = [_evidence(source, match[1].strip(), match.start(1))
                            for source, match in matches]
        declarations[digest] = found
    linked = {digest: {'substance_name': value['substance_name'],
                       'product_name': product_name, 'confirmed': True}
              for digest, value in dmf_links.items()}
    source_selected = [catalogue[value]['source_section_id'] for value in selected]
    source_map = None if section_map is None else {
        catalogue[key]['source_section_id']: ids for key, ids in section_map.items()
        if key in catalogue and key in selected}
    if section_map is not None and set(section_map) != {
            value for value in section_map if value in catalogue and value in selected}:
        raise ValueError('선택하지 않은 QOS 절의 출처 연결은 사용할 수 없음')
    base = prepare_ctd_package(chosen, product_name=product_name,
                               product_variant=product_variant,
                               selected_sections=source_selected,
                               section_map=source_map,
                               confirmed_substance_links=linked)
    inverse = {catalogue[value]['source_section_id']: value for value in selected}
    sections = [{**section, 'section_id': inverse[section['section_id']],
                 'source_section_id': section['section_id'], 'module': 'M2',
                 'title': catalogue[inverse[section['section_id']]]['title']}
                for section in base['sections']]
    # A QOS is a concise assessment. Literal excerpts may automate transfer,
    # but require RA synthesis and cannot be called a completed QOS submission.
    checks = ['원료·제조원과 선택 완제의 관계 및 DMF 접근/참조 권한을 담당자가 확인해야 함',
              '발췌는 3.2.S 원문 복사이며 2.3.S 품질요약의 평가·정당화·제출 적합성을 인증하지 않음',
              'DMF 공개/비공개 부분, 변경 버전, 신청별 제출·면제 요건을 별도 확인해야 함']
    if any(section['status'] != 'manual_check' for section in sections):
        checks.append('비어 있거나 상충·보류된 절은 원자료와 담당자 판단으로 보완해야 함')
    fingerprint = sha256(json.dumps({'base': base['fingerprint'],
                                     'dmf_links': dmf_links,
                                     'declarations': declarations,
                                     'sections': sections}, ensure_ascii=False,
                                    sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {'sections': sections,
            'missing_sections': [inverse[value] for value in base['missing_sections']],
            'ambiguous_sections': [inverse[value] for value in base['ambiguous_sections']],
            'deferred_sections': [inverse[value] for value in base['deferred_sections']],
            'deferred_sources': base['deferred_sources'], 'manual_checks': checks,
            'coverage': base['coverage'], 'fingerprint': fingerprint,
            'dmf_links': dmf_links, 'identity_evidence': declarations,
            'requires_confirmation': True, 'submission_ready': False,
            'actual_model_requests': 0, 'summary_method': 'exact_source_excerpts',
            'document_kind': 'ctd_module_2_3_s_dmf_working_draft'}
