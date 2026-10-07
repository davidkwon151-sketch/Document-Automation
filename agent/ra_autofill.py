"""Conservative bulk proposals from exact labelled values and product headings.

No generation, inferred role, automatic confirmation, or output authorization.
The caller must keep registered mappings and run prepare_ra_workflow again on
the complete confirmed draft before export, including cross-field constraints.
"""
from copy import deepcopy
import re

from agent.brief import model_profile, profile_fields
from agent.ra import RA_WORKFLOWS, COMPANY_ROLES, _material_scope, _product_names, _scope_matches, _variant, _quantities
from agent.ra_workflows import prepare_ra_workflow, _source_records

PROTECTED = re.compile(r'신청인|신청업체|신청기관|대표자|성명|생년|서명|날인|동의|승인|'
                       r'전화|연락처|개인정보|주민|사업자\s*등록|면허번호', re.I)


# These are explicitly equivalent printed section names, not fuzzy clinical
# aliases. Other labels differ only by horizontal whitespace.
LABEL_GROUPS = (('효능·효과', '효능/효과', '효능 및 효과', '효능효과'),
                ('용법·용량', '용법/용량', '용법 및 용량', '용법용량'))
BOUNDARY_LABELS = ('제품명', '품목명', '제형', '함량', '제형·함량', '제형/함량',
                   '성상', '원료약품 및 그 분량', '효능·효과', '용법·용량',
                   '사용상의 주의사항', '저장방법', '포장', '포장단위', '용량', '유효기간',
                   '제조원', '수입자', '신청인')


def _label_pattern(label):
    normalized = re.sub(r'[ \t]', '', label)
    alternatives = next((group for group in LABEL_GROUPS if normalized in {
        re.sub(r'[ \t]', '', item) for item in group}), (label,))
    return '(?:' + '|'.join('[ \\t]*'.join(re.escape(char) for char in
        re.sub(r'[ \t]', '', item)) for item in alternatives) + ')'


def _labelled_values(field, source, labels):
    """Whole contained values only; never crop a condition at a chunk edge."""
    text, label = source['text'], field['label']
    header = re.compile(r'(?m)^[ \t]*' + _label_pattern(label) + r'[ \t]*[:：=|][ \t]*')
    boundaries = set(labels) | set(BOUNDARY_LABELS)
    if re.sub(r'[ \t·/]|및', '', label) in {'용법', '용법용량'}:
        # A dose subheading may contain the rest of this regimen. Defer that
        # unknown continuation instead of calling it a separate field/cropping.
        boundaries.discard('용량')
    boundary = re.compile(r'^[ \t]*(?:' + '|'.join(_label_pattern(item) for item in
        boundaries if item and '\n' not in item) + r')[ \t]*(?:[:：=|]|$)')
    unknown_header = re.compile(r'^[ \t]*[가-힣A-Za-z][가-힣A-Za-z0-9 ()·/_.-]{0,60}[ \t]*[:：=|]')
    product_field = field.get('evidence_role') == 'product_name' or label in {'제품명', '품목명'}
    found = []
    for match in header.finditer(text):
        context, context_start = source.get('context_text'), source.get('context_start')
        if (isinstance(context, str) and type(context_start) is int
                and context[context_start:context_start + len(text)] == text):
            prefix = context[:context_start + match.start()].rsplit('\n', 1)[-1]
            if prefix.strip():
                continue  # A chunk edge is not an original line/field boundary.
        start, end, blocked = match.end(), len(text), False
        first_end = re.search(r'[\r\n]', text[start:])
        if product_field:
            end = start + first_end.start() if first_end else len(text)
        else:
            offset = start
            for index, line in enumerate(text[start:].splitlines(keepends=True)):
                if index and (not line.strip() or boundary.match(line)):
                    end = offset
                    break
                # An undeclared heading might be another field or a clinical
                # condition. Leave it unresolved rather than guessing/cropping.
                if index and unknown_header.match(line):
                    blocked = True
                    break
                offset += len(line)
        raw = text[start:end]
        quote = raw.strip()
        begin = start + len(raw) - len(raw.lstrip())
        # A parser fragment ending inside a labelled value cannot be proposed
        # as its whole value, including when it contains just the first line.
        context, context_end = source.get('context_text'), source.get('context_end')
        if not product_field and end == len(text) and isinstance(context, str) and type(context_end) is int:
            tail = context[context_end:]
            if tail:
                if not tail.startswith(('\r', '\n')):
                    blocked = True
                else:
                    lines = tail.lstrip('\r\n').splitlines()
                    # A blank line or explicit next field is a real boundary.
                    if not tail.startswith(('\n\n', '\r\n\r\n')) and lines and not boundary.match(lines[0]):
                        blocked = True
        if quote and not blocked:
            found.append({'source_id': source['source_id'], 'quote': quote, 'start': begin})
    return found


def _candidates(field, source, selected_product, labels=()):
    text = source.get('text', '')
    if not isinstance(text, str):
        return []
    label = field['label']
    found = []
    # Exact original characters/offsets survive label matching normalization.
    if '\n' not in label:
        found.extend(_labelled_values(field, source, labels))
        # Only non-escaped, single-line JSON values are exact source text here.
        # Escaped/newline/nested JSON requires the normal parser/manual mapping.
        pattern = re.compile(r'(?m)^[ \t]*"' + _label_pattern(label) + r'"[ \t]*:[ \t]*"(?P<value>[^"\\\r\n]+)"[ \t]*,?[ \t]*$')
        for match in pattern.finditer(text):
            found.append({'source_id': source['source_id'], 'quote': match['value'], 'start': match.start('value')})
        # Three-column structured CSV/TSV rows keep product identity beside the
        # exact source label and value. No file-wide product inheritance is used.
        pattern = re.compile(r'(?m)^\s*(?P<product>[^|\r\n]+)\s*\|\s*' + _label_pattern(label)
                             + r'\s*\|\s*(?P<value>[^|\r\n]+)\s*$')
        for match in pattern.finditer(text):
            if match['product'].strip().casefold() != selected_product.strip().casefold():
                continue
            raw = match['value']
            quote = raw.strip()
            if quote:
                found.append({'source_id':source['source_id'], 'quote':quote,
                              'start':match.start('value') + len(raw) - len(raw.lstrip())})
    if field.get('evidence_role') == 'product_name' or label in {'제품명', '품목명'}:
        stripped = text.strip()
        # Whole short product headings only. Paragraphs/clinical sentences are
        # not product-name candidates even when they contain a drug name.
        heading = re.fullmatch(r'([가-힣A-Za-z][가-힣A-Za-z0-9_-]*)(?:\s+(.+))?', stripped)
        remainder = heading[2] if heading else None
        quantities = _quantities(remainder) if remainder else []
        simple_strength = (len(quantities) == 1 and quantities[0]['start'] == 0
                           and quantities[0]['end'] == len(remainder)) if remainder else True
        if heading and simple_strength:
            found.append({'source_id': source['source_id'], 'quote': stripped,
                          'start': text.index(stripped)})
    return list({(item['source_id'], item['start'], item['quote']): item for item in found}.values())


def _product_scope(profile, source, binding, known):
    selected = profile.get('ra_product_name', '').strip().split()[0]
    context = source.get('context_text', source.get('text', ''))
    start = source.get('context_start', 0) + binding['start']
    end = start + len(binding['quote'])
    if not isinstance(context, str) or not isinstance(start, int):
        return False
    # Metadata/filename/user classification cannot replace the actual name.
    pattern = re.compile(r'(?<![A-Za-z0-9])' + re.escape(selected) + r'(?![A-Za-z0-9])', re.I)
    if not pattern.search(context[:end]):
        return False
    actual = _material_scope(context, end - 1, known, source)
    expected = {'product': selected.casefold()}
    variant = profile.get('ra_product_variant', '')
    if variant:
        expected['variant'] = _variant(variant)
    return _scope_matches(expected, actual, variant=bool(variant))


def propose_ra_bindings(profile, sources):
    """Propose exact bindings; all proposals require explicit user confirmation.

    ``sources`` are unchanged parser/search records with actual file SHA and
    page/sheet/paragraph. Supports labelled text/table rows (label: value,
    label | value), simple raw JSON lines, and selected product title snippets.
    Multiple valid locations remain ambiguous even when the values are equal.
    No suggestion is submission-ready or a legal applicability decision.
    """
    cleaned = model_profile(profile)
    fields = profile_fields(cleaned)
    if (not cleaned or cleaned.get('ra_workflow') not in RA_WORKFLOWS or not fields
            or len({f['value_key'] for f in fields}) != len(fields)):
        raise ValueError('고유 입력칸의 등록·확인된 RA 양식이 필요함')
    records = _source_records(sources)
    if any(not isinstance(source.get('text'), str) for source in records.values()):
        raise ValueError('원자료에 실제 파서 원문 문자열이 필요함')
    known = _product_names([s.get('text', '') for s in records.values()], list(records.values()), cleaned)
    proposed, results = {}, []
    selected = cleaned.get('ra_product_name', '')
    if not isinstance(selected, str):
        raise ValueError('선택 제품은 문자열이어야 함')
    for field in fields:
        key, label = field['value_key'], field['label']
        item = {'value_key': key, 'label': label, 'status': 'missing', 'binding': None,
                'candidate_count': 0, 'reason': '같은 원문 항목의 정확한 값을 찾지 못함'}
        if field.get('input_required') or PROTECTED.search(label):
            item.update(status='direct_input', reason='신원·선택·서명·동의는 실제 사용자 입력으로 확인해야 함')
        elif any(pattern.search(label) for pattern in COMPANY_ROLES.values()):
            item.update(status='blocked', reason='회사 역할은 결합 원문과 전체 회사명을 담당자가 직접 확인해 연결해야 함')
        elif not selected.strip():
            item.update(status='blocked', reason='제품을 명시적으로 선택해야 자동 제안할 수 있음')
        else:
            valid, rejected = [], []
            for source in records.values():
                for binding in _candidates(field, source, selected, [item['label'] for item in fields]):
                    single = deepcopy(cleaned)
                    single['fields'] = [deepcopy(field)]
                    single['constraints'] = {}  # Complete relations remain mandatory after confirmation.
                    try:
                        checked = prepare_ra_workflow(single, [source], {key: binding}, {})
                        if checked['review']['blocking']:
                            rejected.extend(issue['code'] for issue in checked['review']['issues'])
                        elif not _product_scope(cleaned, source, binding, known):
                            rejected.append('selected_product_scope_unverified')
                        else:
                            valid.append(binding)
                    except (ValueError, TypeError, KeyError):
                        rejected.append('source_or_field_guard')
            item['candidate_count'] = len(valid)
            if len(valid) == 1:
                item.update(status='proposed', binding=valid[0], reason='동일 제품 범위의 정확한 원문 후보 1개임; 확인 필요함')
                proposed[key] = valid[0]
            elif len(valid) > 1:
                item.update(status='ambiguous', reason='유효한 원문 위치가 여러 개임; 자동으로 선택하지 않음', candidates=valid)
            elif rejected:
                item.update(status='blocked', reason='후보의 출처·제품·단위·등록 제약 검수를 통과하지 못함',
                            blocking_codes=sorted(set(rejected)))
        results.append(item)
    return {'fields': results, 'source_bindings': proposed, 'requires_confirmation': True,
            'confirmed': False, 'actual_model_requests': 0, 'submission_ready': False,
            'scope': '정형 원문 항목·제품 표제의 후보 제안임; 전체 관계·필수 항목·원위치 출력 검수는 확인 뒤 실행함'}
