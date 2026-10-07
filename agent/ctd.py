"""Source-bound CTD Module 1/3 working sections, before RA and submission review.

The result is an extractive proposal, never an eCTD package or an assessment of
which sections a particular Korean application is legally required to submit.
"""

from hashlib import sha256
import json
import re

from agent.multimodal_intake import validate_generation_source
from agent.ra_workflows import _bound_quote, _source_records


# Module 1 labels are local working categories, not Korean regulatory section IDs.
# Module 3 numbers follow ICH M4Q(R1). Module 2.3 QOS is intentionally separate.
CTD_SECTIONS = (
    {'section_id': 'M1_ADMIN', 'title': 'Module 1 행정 신청 자료', 'module': 'M1',
     'aliases': ('신청서', '품목허가신청서', '품목신고서', '행정 신청 자료', 'application form')},
    {'section_id': 'M1_LABEL', 'title': 'Module 1 표시·첨부 자료', 'module': 'M1',
     'aliases': ('표시기재', '첨부문서', '제품정보', '허가사항', 'labelling', 'labeling')},
    {'section_id': '3.2.S.1', 'title': '원료의약품 일반 정보', 'module': 'M3',
     'aliases': ('원료의약품 일반 정보', 'general information')},
    {'section_id': '3.2.S.2', 'title': '원료의약품 제조', 'module': 'M3',
     'aliases': ('원료의약품 제조', 'manufacture of drug substance')},
    {'section_id': '3.2.S.3', 'title': '원료의약품 특성', 'module': 'M3',
     'aliases': ('원료의약품 특성', 'characterisation of drug substance')},
    {'section_id': '3.2.S.4', 'title': '원료의약품 관리', 'module': 'M3',
     'aliases': ('원료의약품 규격', '원료의약품 시험', '원료의약품 관리', 'control of drug substance')},
    {'section_id': '3.2.S.5', 'title': '원료의약품 표준품·표준물질', 'module': 'M3',
     'aliases': ('원료의약품 표준품', '원료의약품 표준물질', 'reference standards or materials')},
    {'section_id': '3.2.S.6', 'title': '원료의약품 용기·포장', 'module': 'M3',
     'aliases': ('원료의약품 용기·포장', 'drug substance container closure system')},
    {'section_id': '3.2.S.7', 'title': '원료의약품 안정성', 'module': 'M3',
     'aliases': ('원료의약품 안정성', 'drug substance stability')},
    {'section_id': '3.2.P.1', 'title': '완제의약품 조성', 'module': 'M3',
     'aliases': ('완제의약품 조성', '완제 조성', 'description and composition of the drug product')},
    {'section_id': '3.2.P.2', 'title': '완제의약품 개발', 'module': 'M3',
     'aliases': ('완제의약품 개발', 'pharmaceutical development')},
    {'section_id': '3.2.P.3', 'title': '완제의약품 제조', 'module': 'M3',
     'aliases': ('완제의약품 제조', 'manufacture of drug product')},
    {'section_id': '3.2.P.3.2', 'title': '배치 처방', 'module': 'M3',
     'aliases': ('배치 처방', '배치처방', 'batch formula')},
    {'section_id': '3.2.P.4', 'title': '첨가제 관리', 'module': 'M3',
     'aliases': ('첨가제 관리', 'control of excipients')},
    {'section_id': '3.2.P.5', 'title': '완제의약품 관리', 'module': 'M3',
     'aliases': ('완제의약품 규격', '완제의약품 시험', '완제의약품 관리', 'control of drug product')},
    {'section_id': '3.2.P.5.4', 'title': '배치 분석', 'module': 'M3',
     'aliases': ('배치 분석', '배치분석', 'batch analyses', 'batch analysis')},
    {'section_id': '3.2.P.6', 'title': '완제의약품 표준품·표준물질', 'module': 'M3',
     'aliases': ('완제의약품 표준품', '완제의약품 표준물질', 'drug product reference standards')},
    {'section_id': '3.2.P.7', 'title': '용기·포장', 'module': 'M3',
     'aliases': ('용기 포장', '용기·포장', 'container closure system')},
    {'section_id': '3.2.P.8', 'title': '완제의약품 안정성', 'module': 'M3',
     'aliases': ('완제의약품 안정성', 'drug product stability')},
    {'section_id': '3.2.P.8.3', 'title': '안정성 자료', 'module': 'M3',
     'aliases': ('안정성 자료', '안정성자료', 'stability data')},
)

_PRODUCT = re.compile(r'(?im)^\s*(?:제품명|품목명|의약품명|product(?:\s+name)?)\s*[:：|]\s*([^\r\n|]+)')
_SUBSTANCE = re.compile(r'(?im)^\s*(?:원료(?:의약품)?명|drug\s+substance(?:\s+name)?|'
                        r'api(?:\s+name)?|material(?:\s+name)?)\s*[:：|]\s*([^\r\n|]+)')
_VARIANT = re.compile(r'(?im)^\s*(?:제형|함량|strength|dosage\s+form)\s*[:：|]\s*([^\r\n|]+)')
_PROTECTED = re.compile(r'신청인|신청업체|대표자|서명|날인|동의|승인|개인정보|생년|연락처|전화|'
                        r'주소|주민|사업자\s*등록번호|이메일|e-?mail|fax|휴대', re.I)


def _canonical(value):
    return re.sub(r'\s+', ' ', value.strip()).casefold()


def _declared_match(declaration, selected):
    actual, target = _canonical(declaration), _canonical(selected)
    return bool(target and re.match(re.escape(target) + r'(?=$|[\s(（/])', actual))


def _heading_match(section, text):
    """Only an anchored full heading/label counts; filenames never classify."""
    section_id = section['section_id']
    patterns = [re.escape(section_id) + r'(?![.\d])'] if section_id.startswith('3.') else []
    patterns.extend(re.escape(alias).replace(r'\ ', r'\s+') for alias in section['aliases'])
    if not patterns:
        return False
    pattern = r'(?im)^\s*(?:' + '|'.join(patterns) + r')\s*(?=[:：|\-]|$)'
    return re.search(pattern, text)


def _heading(section, text):
    return _heading_match(section, text) is not None


def _header_only(section, text):
    match = _heading_match(section, text)
    if match is None:
        return False
    remainder = text[match.end():].strip(' :：|-\t\r\n')
    normalized = _canonical(remainder)
    return not normalized or normalized in {_canonical(section['title']),
                                            *(_canonical(alias) for alias in section['aliases'])}


def _next_section_or_product(text):
    return bool(_PRODUCT.match(text)
                or re.match(r'(?i)^\s*3\.2\.[SP](?:\.\d+){1,2}(?![.\d])\s*[:：|.\- ]', text)
                or re.match(r'(?i)^\s*(?:[1-5](?:\.\d+){1,5})(?![.\d])\s*[:：|.\- ]', text)
                or any(_heading(section, text) for section in CTD_SECTIONS))


def _facts(candidates):
    """Flag conflicting values unless different batch/time/condition is explicit."""
    facts = {}
    for candidate in candidates:
        scope = {}
        for item in candidate['evidence']:
            for line in item['quote'].splitlines():
                match = re.match(r'^\s*([^:：|]{1,60})\s*[:：|]\s*(\S.*?)\s*$', line)
                if not match:
                    continue
                key, value = _canonical(match[1]), _canonical(match[2])
                qualifier = (('batch' if re.fullmatch(r'(?:배치|제조|시험)\s*(?:번호|id)|'
                                                     r'(?:batch|lot)(?:\s*(?:no\.?|id|number))?', key) else
                              'time' if re.fullmatch(r'시점|시험\s*시점|보관\s*기간|'
                                                     r'time\s*point|month', key) else
                              'condition' if re.fullmatch(r'조건|보관\s*조건|storage\s*condition', key) else None))
                if qualifier:
                    scope[qualifier] = value
                    continue
                for previous_value, previous_scope in facts.get(key, []):
                    if previous_value == value:
                        continue
                    if any(previous_scope.get(name) and scope.get(name)
                           and previous_scope[name] != scope[name]
                           for name in ('batch', 'time', 'condition')):
                        continue
                    return True
                facts.setdefault(key, []).append((value, scope.copy()))
    return False


def _complete_chunk(source):
    """Do not pass an 800-character fragment off as a full CTD paragraph/table row."""
    context = source.get('context_text')
    if context is None:
        return True
    start, end, text = source.get('context_start'), source.get('context_end'), source.get('text')
    return (isinstance(context, str) and type(start) is int and type(end) is int
            and isinstance(text, str) and context[start:end] == text
            and (not context[:start].strip() or context[:start].endswith(('\n', '\r')))
            and (not context[end:].strip() or context[end:].startswith(('\n', '\r'))))


def _evidence(source, quote=None, start=None):
    quote = source['text'].strip() if quote is None else quote
    start = source['text'].index(quote) if start is None else start
    _bound_quote({'source_id': source['source_id'], 'quote': quote, 'start': start}, source)
    return {key: source.get(key) for key in ('source_id', 'filename', 'document_sha256',
                                               'page', 'sheet', 'location')} | {
        'quote': quote, 'start': start, 'end': start + len(quote)}


def _safe_m1_evidence(source, skip_first=False):
    """Keep only whole, delimited non-personal cells with exact source spans."""
    records, excluded, pending_value = [], False, skip_first
    for match in re.finditer(r'[^|\r\n]+', source['text']):
        raw = match.group()
        quote = raw.strip()
        if not quote:
            continue
        if pending_value:
            excluded = True
            pending_value = False
            continue
        protected = _PROTECTED.search(quote)
        if protected:
            excluded = True
            pending_value = not bool(quote[protected.end():].strip(' :：=\t'))
            continue
        start = match.start() + len(raw) - len(raw.lstrip())
        records.append(_evidence(source, quote, start))
    return records, excluded, pending_value


def propose_ctd_product_options(sources):
    """List only literal product/variant declarations from verified originals.

    A single name/variant is a UI suggestion, not an approved product identity.
    Conflicting declarations require the user to select and inspect originals.
    """
    records = _source_records(sources)
    groups = {}
    for source in records.values():
        validate_generation_source(source)
        if not _complete_chunk(source):
            continue
        groups.setdefault((source['filename'], source['document_sha256']), []).append(source)
    names, variants = {}, {}
    for members in groups.values():
        first_section = next((index for index, source in enumerate(members)
                              if _next_section_or_product(source['text'])
                              and not _PRODUCT.match(source['text'])), len(members))
        for source in members:
            for match in _PRODUCT.finditer(source['text']):
                value = match[1].strip()
                if value:
                    names.setdefault(_canonical(value), {'value': value, 'evidence': []})[
                        'evidence'].append(_evidence(source, value, match.start(1)))
        for source in members[:first_section]:
            for match in _VARIANT.finditer(source['text']):
                value = match[1].strip()
                if value:
                    variants.setdefault(_canonical(value), {'value': value, 'evidence': []})[
                        'evidence'].append(_evidence(source, value, match.start(1)))
    values = list(names.values())
    variant_values = list(variants.values())
    return {'product_names': values, 'variant_parts': variant_values,
            'proposed_product_name': values[0]['value'] if len(values) == 1 else None,
            'proposed_product_variant': ' '.join(item['value'] for item in variant_values)
                if len(values) == 1 and 0 < len(variant_values) <= 2 else None,
            'requires_confirmation': True}


def propose_ctd_substance_options(sources):
    """Show literal API/material-name candidates per original SHA for RA review."""
    records = _source_records(sources)
    groups = {}
    for source in records.values():
        validate_generation_source(source)
        if not _complete_chunk(source):
            continue
        groups.setdefault((source['filename'], source['document_sha256']), []).append(source)
    result = []
    for (filename, digest), members in groups.items():
        candidates = {}
        for source in members:
            for match in _SUBSTANCE.finditer(source['text']):
                value = match[1].strip()
                if value:
                    candidates.setdefault(_canonical(value), {'value': value, 'evidence': []})[
                        'evidence'].append(_evidence(source, value, match.start(1)))
        if not candidates:
            continue
        declared_products = {_canonical(value) for source in members
                             for value in _PRODUCT.findall(source['text'])}
        values = list(candidates.values())
        consistent = len(values) == 1 and (not declared_products
                                             or declared_products == {_canonical(values[0]['value'])})
        result.append({'filename': filename, 'document_sha256': digest,
                       'substance_names': values,
                       'proposed_substance_name': values[0]['value'] if consistent else None,
                       'requires_confirmation': True})
    return result


def prepare_ctd_package(sources, *, product_name, product_variant='', section_map=None,
                        selected_sections=None, confirmed_substance_links=None):
    """Propose exact source excerpts for selected CTD working sections.

    ``sources`` should be ``generation_sources(intake)``. The optional map is
    a proposed section-to-source-ID connection, not proof that the source's
    content belongs there. Every quoted excerpt remains reviewable and cannot
    be exported as a regulatory submission by this function.
    """
    if not isinstance(product_name, str) or not product_name.strip() or len(product_name) > 200:
        raise ValueError('선택 제품의 정확한 이름이 필요함')
    if not isinstance(product_variant, str) or len(product_variant) > 200:
        raise ValueError('선택 제형·함량은 문자열이어야 함')
    catalogue = {section['section_id']: section for section in CTD_SECTIONS}
    selected = list(catalogue) if selected_sections is None else selected_sections
    if (not isinstance(selected, list) or not selected or len(selected) != len(set(selected))
            or any(section_id not in catalogue for section_id in selected)):
        raise ValueError('등록된 CTD 작업 절을 중복 없이 선택해야 함')
    mapped = {} if section_map is None else section_map
    if (not isinstance(mapped, dict) or any(section_id not in selected or not isinstance(ids, list)
            or not ids or len(ids) != len(set(ids)) or any(not isinstance(value, str) for value in ids)
            for section_id, ids in mapped.items())):
        raise ValueError('절 연결은 선택한 절별 출처 ID 목록이어야 함')
    if len([identifier for ids in mapped.values() for identifier in ids]) != len(
            set(identifier for ids in mapped.values() for identifier in ids)):
        raise ValueError('한 원문 조각을 서로 다른 CTD 절에 동시에 연결할 수 없음')
    records = _source_records(sources)
    links = {} if confirmed_substance_links is None else confirmed_substance_links
    if (not isinstance(links, dict) or any(not re.fullmatch(r'[0-9a-f]{64}', digest)
            or not isinstance(link, dict)
            or set(link) != {'substance_name', 'product_name', 'confirmed'}
            or link.get('confirmed') is not True
            or not isinstance(link.get('substance_name'), str)
            or not link['substance_name'].strip()
            or len(link['substance_name']) > 200
            or link.get('product_name') != product_name for digest, link in links.items())):
        raise ValueError('현재 원본 SHA·선택 제품·확인한 원료명의 명시 연결이 필요함')
    if set(links) - {source.get('document_sha256') for source in records.values()}:
        raise ValueError('원료 연결의 SHA가 현재 첨부 원본과 다름')
    if any(identifier not in records for ids in mapped.values() for identifier in ids):
        raise ValueError('절 연결에 존재하지 않는 원자료 ID가 있음')

    safe = {}
    deferred = []
    for identifier, source in records.items():
        try:
            validate_generation_source(source)
            if not _complete_chunk(source):
                raise ValueError('원문 조각이 문단·표 행 중간에서 잘림')
            _evidence(source)
            safe[identifier] = source
        except (ValueError, KeyError, TypeError) as exc:
            deferred.append({'source_id': identifier, 'reason': str(exc)})

    # A document-wide scope is trusted only when the original text actually
    # declares a single selected product (and selected variant when requested).
    groups = {}
    for source in safe.values():
        groups.setdefault((source['filename'], source['document_sha256']), []).append(source)
    file_scope = {}
    substance_scope = {}
    for key, members in groups.items():
        names = [_canonical(value) for source in members
                 for value in _PRODUCT.findall(source['text'])]
        first_section = next((index for index, source in enumerate(members)
                              if _next_section_or_product(source['text'])
                              and not (_PRODUCT.match(source['text']) or _VARIANT.match(source['text']))),
                             len(members))
        variants = list(dict.fromkeys(_canonical(value) for source in members[:first_section]
                                      for value in _VARIANT.findall(source['text'])))
        name_ok = bool(names) and all(_declared_match(name, product_name) for name in names)
        variant_ok = (not product_variant or bool(variants) and
                      (_canonical(product_variant) in variants
                       or _canonical(product_variant) == ' '.join(variants)
                       or _canonical(product_variant) == ' '.join(reversed(variants))))
        file_scope[key] = name_ok and variant_ok
        link = links.get(key[1])
        if link:
            declared = [_canonical(value) for source in members
                        for value in _SUBSTANCE.findall(source['text'])]
            target = _canonical(link['substance_name'])
            # A material name must be present in the actual source text. Any
            # conflicting product/substance name in the same original fails.
            linked_names = bool(declared) and all(value == target for value in declared)
            product_names_ok = all(value == target for value in names)
            declared_variants = {}
            for source in members[:first_section]:
                for match in _VARIANT.finditer(source['text']):
                    label = _canonical(source['text'][match.start():match.start(1)].strip(' :：|'))
                    declared_variants.setdefault(label, set()).add(_canonical(match[1]))
            variants_ok = all(len(values) == 1 for values in declared_variants.values())
            substance_scope[key] = linked_names and product_names_ok and variants_ok
    if any(not any(group_key[1] == digest and substance_scope.get(group_key, False)
                   for group_key in groups) for digest in links):
        raise ValueError('확인한 원료명·제품명·제형 선언이 현재 원문 파일에서 일치하지 않음')

    sections = []
    used = set()
    ordered = list(records)
    for section_id in selected:
        section = catalogue[section_id]
        candidates = []  # each independent heading starts one source block
        assigned = set()
        allowed = set(mapped[section_id]) if section_id in mapped else None
        for index, identifier in enumerate(ordered):
            source = safe.get(identifier)
            if source is None or identifier in assigned:
                continue
            heading = _heading(section, source['text'])
            if allowed is not None and identifier not in allowed:
                continue
            if not heading and identifier not in mapped.get(section_id, ()):
                continue
            key = (source['filename'], source['document_sha256'])
            used.add(identifier)
            linked_substance = section_id.startswith('3.2.S.') and substance_scope.get(key, False)
            if section_id.startswith('3.2.S.') and key[1] in links and not linked_substance:
                deferred.append({'source_id': identifier, 'section_id': section_id,
                                 'reason': '원료 연결의 제품·제형 범위가 원문과 다름'})
                continue
            if not file_scope[key] and not linked_substance:
                deferred.append({'source_id': identifier, 'section_id': section_id,
                                 'reason': '같은 원문 파일에서 선택 제품·제형 또는 확인된 원료 연결을 확인하지 못함'})
                continue
            if section['module'] == 'M1':
                block, excluded, pending_personal_value = _safe_m1_evidence(source)
                if excluded:
                    deferred.append({'source_id': identifier, 'section_id': section_id,
                                     'reason': '신청인·서명·동의·승인 등의 셀은 직접 확인 항목으로 제외함',
                                     'blocking': False})
            else:
                block = [_evidence(source)]
            if linked_substance:
                for item in block:
                    item['source_scope'] = 'confirmed_substance_link'
                    item['substance_name'] = links[key[1]]['substance_name']
            assigned.add(identifier)
            if heading:
                for next_id in ordered[index + 1:]:
                    following = safe.get(next_id)
                    if following is None or (following['filename'], following['document_sha256']) != key:
                        break
                    if _next_section_or_product(following['text']):
                        break
                    if len(block) >= 50 or sum(len(item['quote']) for item in block) + len(following['text']) > 20000:
                        deferred.append({'source_id': identifier, 'section_id': section_id,
                                         'reason': '절의 원문 범위가 자동 검토 한도를 넘어 전체 연결 확인 필요함'})
                        block = []
                        break
                    if section['module'] == 'M1':
                        values, excluded, pending_personal_value = _safe_m1_evidence(
                            following, pending_personal_value)
                        block.extend(values)
                        if excluded:
                            deferred.append({'source_id': next_id, 'section_id': section_id,
                                             'reason': '신청인·서명·동의·승인 등의 셀은 직접 확인 항목으로 제외함',
                                             'blocking': False})
                    else:
                        item = _evidence(following)
                        if linked_substance:
                            item['source_scope'] = 'confirmed_substance_link'
                            item['substance_name'] = links[key[1]]['substance_name']
                        block.append(item)
                    assigned.add(next_id)
                    used.add(next_id)
            if block and (not heading or not _header_only(section, block[0]['quote']) or len(block) > 1):
                candidates.append({'evidence': block, 'heading': heading})
            elif block:
                deferred.append({'source_id': identifier, 'section_id': section_id,
                                 'reason': '절 표제만 있고 실제 원문 내용이 없음'})
        evidence = [item for candidate in candidates for item in candidate['evidence']]
        blocked_candidate = any(item.get('section_id') == section_id
                                and item.get('blocking') is not False for item in deferred)
        blocked_candidate |= any(identifier in mapped.get(section_id, ())
                                 or isinstance(source.get('text'), str)
                                 and _heading(section, source['text'])
                                 for identifier, source in records.items() if identifier not in safe)
        if blocked_candidate:
            status, draft = 'deferred', ''
        elif evidence and not _facts(candidates):
            status = ('proposed' if all(candidate['heading'] for candidate in candidates)
                      else 'manual_check')
            if any(item.get('source_scope') == 'confirmed_substance_link' for item in evidence):
                status = 'manual_check'
            draft = '\n'.join(item['quote'] + ' [' + item['source_id'] + ']'
                              for item in evidence)
        else:
            status = 'ambiguous' if evidence else 'missing'
            draft = ''
        sections.append({'section_id': section_id, 'title': section['title'],
                         'module': section['module'], 'status': status, 'draft': draft,
                         'evidence': evidence})
    for identifier in safe.keys() - used:
        if identifier in {value for ids in mapped.values() for value in ids}:
            continue
        # An unmatched paragraph is retained for review; its filename alone
        # cannot establish a CTD section or product context.
        deferred.append({'source_id': identifier, 'reason': '선택 절의 원문 표제·명시 연결을 확인하지 못함'})

    missing = [item['section_id'] for item in sections if item['status'] == 'missing']
    ambiguous = [item['section_id'] for item in sections if item['status'] == 'ambiguous']
    deferred_sections = [item['section_id'] for item in sections if item['status'] == 'deferred']
    manual = ['Module 1 한국 지역별 항목·신청 유형별 제출/면제 여부와 현재 서식은 담당자 확인 필요함',
              '신청인·서명·동의·승인·법정 적합성은 원자료 발췌만으로 확정하지 않음',
              'Module 2.3 품질요약(QOS), 나머지 Module 2/4/5 및 eCTD 전자 제출 구조는 별도 작업임']
    if any(item['status'] == 'manual_check' for item in sections):
        manual.append('표제가 없는 명시 연결 출처는 해당 CTD 절과의 관계를 담당자가 확인해야 함')
    if any(item.get('source_scope') == 'confirmed_substance_link'
           for section in sections for item in section['evidence']):
        manual.append('3.2.S 원료 자료는 담당자가 확인한 원료–완제 연결을 바탕으로 제안함; '
                      '독립 입증·허가 적합성이나 완제 3.2.P 항목의 근거가 아님')
    fingerprint = sha256(json.dumps({'product': product_name, 'variant': product_variant,
                                     'selected': selected, 'map': mapped, 'substance_links': links,
                                     'sections': sections,
                                     'source_fingerprints': {key: value.get('verification_fingerprint')
                                                             for key, value in records.items()}},
                                    ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {'sections': sections, 'missing_sections': missing, 'ambiguous_sections': ambiguous,
            'deferred_sections': deferred_sections,
            'manual_checks': manual, 'deferred_sources': deferred, 'fingerprint': fingerprint,
            'coverage': {'selected_sections': len(sections),
                         'proposed_sections': sum(item['status'] == 'proposed' for item in sections),
                         'needs_manual_check': sum(item['status'] == 'manual_check' for item in sections),
                         'deferred_sections': len(deferred_sections)},
            'requires_confirmation': True, 'submission_ready': False, 'actual_model_requests': 0,
            'document_kind': 'ctd_module_1_3_working_draft'}
