"""Private deterministic same-block RA document/section qualification.

This identifies provenance and section applicability, not clinical validity.
No filename, model-produced metadata, user answer, or cross-page inheritance
can supply a missing document/section anchor.
"""
from datetime import date
from hashlib import sha256
import re

ALIASES = {
    '임상시험 목적': ('protocol', ('study objectives', 'primary objective', 'secondary objectives',
                                 'exploratory objectives', 'trial objectives', '임상시험 목적', '시험 목적')),
    '투여 방법': ('protocol', ('drug administration', 'study drug administration', 'dose and schedule',
                            'dosing regimen', '투여 방법', '시험약 투여 방법', '시험약 투여')),
    '서론': ('dsur', ('introduction', '서론')),
    '일련 목록(Line listing)과 요약표의 데이터': ('dsur', (
        'data in line listings and summary tabulations',
        'line listings of serious adverse reactions during the reporting period',
        'cumulative summary tabulations of serious adverse events',
        'line listings', 'summary tabulations', '일련 목록과 요약표의 데이터',
        '일련 목록', '누적 요약표')),
}
PROTOCOL = re.compile(r'(?:Clinical\s+Study\s+Protocol|Clinical\s+Trial\s+Protocol|'
                      r'임상시험\s*(?:변경\s*)?계획서)\s+(?P<study>[A-Z0-9][A-Z0-9._-]{3,60})\b', re.I)
DSUR_TITLE = re.compile(r'^\s*(?:Development\s+Safety\s+Update\s+Report|DSUR|'
                        r'임상시험\s*안전성\s*(?:정기\s*)?보고서)\s*$', re.I | re.M)
REPORT_NUMBER = re.compile(r'(?:DSUR\s*(?:number|no\.?|#)|보고서\s*번호)\s*[:：]?\s*(\d+)\b', re.I)
PERIOD = re.compile(r'(?:Reporting\s+period|보고\s*기간)\s*[:：]\s*'
                    r'(\d{4}-\d{2}-\d{2})\s*(?:to|through|~|–|—)\s*(\d{4}-\d{2}-\d{2})\b', re.I)
EXCLUDED = re.compile(r'^.*(?:GUIDANCE\s+FOR\s+INDUSTRY|GUIDELINE|TEMPLATE|BLANK\s+FORM|'
                      r'SAMPLE\s+(?:DSUR|REPORT)|EXAMPLE\s+(?:DSUR|REPORT)|TABLE\s+OF\s+CONTENTS|'
                      r'작성\s*(?:가이드|예시)|빈\s*양식|목차).*$|\.{3,}\s*\d+\s*$', re.I | re.M)
NUMBERED_HEADING = re.compile(r'^\s*\d+(?:\.\d+)*\.?\s+([^\n]{1,120})\s*$')
STUDY_REFERENCE = re.compile(r'(?:(?:Protocol|Study|Trial)\s*(?:Number|No\.?|ID)|'
                             r'임상시험\s*번호|시험계획서\s*번호|프로토콜\s*번호)\s*[:：]?\s*'
                             r'([A-Z0-9][A-Z0-9._-]{3,60})\b', re.I)
NATURAL_STUDY_REFERENCE = re.compile(r'\b(?:Study|Trial|Protocol)\s+'
    r'((?=[A-Z0-9._-]*\d)[A-Z0-9]+[-_.][A-Z0-9._-]+)\b', re.I)
DECLARED_PRODUCT = re.compile(r'(?:Product\s+name|Investigational\s+(?:drug|product)|Study\s+drug|'
    r'Compound|제품명|시험약)\s*[:：]\s*([A-Za-z가-힣][A-Za-z가-힣0-9_.-]*)', re.I)
TRIAL_PRODUCT_ROLE = re.compile(r'\b([A-Za-z][A-Za-z0-9_.-]*)\s+is\s+(?:the|an?)\s+'
                               r'investigational\s+(?:drug|product)\b', re.I)
DATA_PERIOD = re.compile(r'(?:report\s+data\s+(?:cover|for)|data\s+cover|보고\s*자료\s*기간)\s*[:：]?\s*'
                        r'(\d{4}-\d{2}-\d{2})\s*(?:to|through|~|–|—)\s*(\d{4}-\d{2}-\d{2})\b', re.I)
PLACEHOLDER = re.compile(r'\{\{|\[(?:insert|enter|to be completed|기입|입력)[^\]]*\]', re.I)
SECTION_WORDS = {alias for _, aliases in ALIASES.values() for alias in aliases} | {
    'study design', 'hypothesis and rationale', 'references', 'worldwide marketing approval status',
    'overall safety assessment', 'conclusions', '서론', '시험 설계', '참고문헌', '결론',
}


def _lines(text):
    offset = 0
    for line in text.splitlines(keepends=True):
        yield line.rstrip('\r\n'), offset, offset + len(line)
        offset += len(line)


def _headings(text):
    found = []
    for line, start, end in _lines(text):
        matched = NUMBERED_HEADING.fullmatch(line)
        declared = re.fullmatch(r'\s*항목\s*[:：]\s*([^\n]{1,120})\s*', line)
        title = declared[1].strip() if declared else matched[1].strip() if matched else line.strip()
        normalized = re.sub(r'\s+', ' ', title).casefold()
        if matched or declared or normalized in SECTION_WORDS:
            found.append({'title': normalized, 'start': start, 'end': end})
    return found


def qualify_natural_scope(source, field_label, *, expected_product, expected_study=None,
                          expected_period=None, known_products=(), allowed_document_labels=None,
                          product_policy='explicit_quote', quote_start=0, quote_end=None):
    """Return independently recomputable span anchors, or None for unknown/mismatch.

    The source must come from the parser's current immutable document set.
    Caller still performs original quote, product/variant, numbers, polarity,
    conditions, and semantic checks. Optional IDs constrain; they never supply
    identity missing in the original block.
    """
    if source.get('filename') == '사용자 입력' or source.get('source_origin') == 'user_input':
        return None
    if source.get('requires_verification'):
        return None
    identity = source.get('document_sha256')
    if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{64}', identity):
        return None
    if type(source.get('page')) is not int or source['page'] < 1:
        # DOCX parsers preserve actual paragraph/table locations; they do not
        # invent a rendered page. A filename cannot supply document identity.
        filename, location = source.get('filename'), source.get('location')
        if (source.get('page') is not None or not isinstance(filename, str) or not filename.lower().endswith('.docx')
                or not isinstance(location, str) or not location.strip()):
            return None
    context, text = source.get('context_text'), source.get('text')
    start, end = source.get('context_start'), source.get('context_end')
    if (not isinstance(context, str) or not isinstance(text, str) or type(start) is not int
            or type(end) is not int or not 0 <= start < end <= len(context) or context[start:end] != text):
        return None
    quote_end = len(text) if quote_end is None else quote_end
    if type(quote_start) is not int or type(quote_end) is not int or not 0 <= quote_start < quote_end <= len(text):
        return None
    quote_lo, quote_hi = start + quote_start, start + quote_end
    declared_types = [match for match in re.finditer(
        r'(?m)^[ \t]*(?:자료 유형|문서 종류)[ \t]*[:：][ \t]*(?P<name>[^\r\n]+)', context)
        if match.end() <= quote_lo]
    if (declared_types and allowed_document_labels is not None
            and declared_types[-1]['name'].strip() not in allowed_document_labels):
        return None
    if field_label not in ALIASES or not isinstance(expected_product, str) or not expected_product.strip():
        return None
    if EXCLUDED.search(context):
        return None
    family, aliases = ALIASES[field_label]
    if product_policy not in {'explicit_quote', 'declared_single_product_section'}:
        return None
    if family == 'dsur' and PROTOCOL.search(context) or family == 'protocol' and DSUR_TITLE.search(context):
        return None
    permitted = {'protocol': {'임상시험계획서', '임상시험 변경계획서'},
                 'dsur': {'DSUR 원자료', '임상시험 안전성 보고 원자료'}}
    if allowed_document_labels is not None and (not isinstance(allowed_document_labels, list)
            or not set(allowed_document_labels).intersection(permitted[family])):
        return None
    study, period, anchors = None, None, []
    if family == 'protocol':
        matches = list(PROTOCOL.finditer(context))
        if not matches or len({match['study'] for match in matches}) != 1:
            return None
        preceding_headers = [match for match in matches if match.end() <= quote_lo]
        if not preceding_headers:
            return None
        study = matches[0]['study']
        if expected_study is not None and expected_study != study:
            return None
        anchors.append({'kind': 'document_header', 'start': preceding_headers[-1].start(), 'end': preceding_headers[-1].end()})
    else:
        titles, numbers, periods = list(DSUR_TITLE.finditer(context)), list(REPORT_NUMBER.finditer(context)), list(PERIOD.finditer(context))
        if (not titles or not numbers or not periods or len({match[1] for match in numbers}) != 1
                or len({(match[1], match[2]) for match in periods}) != 1):
            return None
        title, number, report_period = titles[0], numbers[0], periods[0]
        if any(match.end() > quote_lo for match in [title, number, report_period]):
            return None
        try:
            period = [date.fromisoformat(report_period[1]).isoformat(), date.fromisoformat(report_period[2]).isoformat()]
        except ValueError:
            return None
        if period[0] > period[1] or expected_period is not None and list(expected_period) != period:
            return None
        anchors.extend({'kind': kind, 'start': match.start(), 'end': match.end()}
                       for kind, match in [('document_title', title), ('report_number', number), ('report_period', report_period)])
    headings = _headings(context)
    preceding = [(index, heading) for index, heading in enumerate(headings) if heading['end'] <= quote_lo]
    if not preceding:
        return None
    index, heading = preceding[-1]
    section_end = headings[index + 1]['start'] if index + 1 < len(headings) else len(context)
    if heading['title'] not in {*aliases, re.sub(r'\s+', ' ', field_label).casefold()} or quote_hi > section_end:
        return None
    quoted = context[quote_lo:quote_hi]
    if PLACEHOLDER.search(quoted):
        return None
    if study and any(match[1] != study for pattern in (STUDY_REFERENCE, NATURAL_STUDY_REFERENCE)
                     for match in pattern.finditer(quoted)):
        return None
    if period and any([match[1], match[2]] != period for match in DATA_PERIOD.finditer(quoted)):
        return None
    if any(match[1].casefold() != expected_product.casefold() for pattern in (DECLARED_PRODUCT, TRIAL_PRODUCT_ROLE)
           for match in pattern.finditer(quoted)):
        return None
    if re.search(r'(?<![\w-])' + re.escape(expected_product) +
                 r'\s+(?:is|as)\s+(?:(?:the|an?)\s+)?comparator\b', quoted, re.I):
        return None
    if any(isinstance(product, str) and product.casefold() != expected_product.casefold()
            and re.search(r'(?<![\w-])' + re.escape(product) + r'(?![\w-])', quoted, re.I)
            for product in known_products):
        return None
    product_pattern = re.compile(r'(?<![\w-])' + re.escape(expected_product.strip()) + r'(?![\w-])', re.I)
    remainder = product_pattern.sub('', quoted).strip(' \t\r\n.,:：;')
    if not remainder or re.fullmatch(r'(?:Investigational\s+(?:drug|product)|Study\s+drug|'
                                     r'Compound|Product(?:\s+name)?|제품명|시험약)', remainder, re.I):
        return None
    # The actual section's body must name the selected product, not just a page
    # header or another section. This does not classify comparators as trial drugs.
    product = product_pattern.search(context, quote_lo, quote_hi)
    if product_policy == 'declared_single_product_section' and not product:
        declaration = re.compile(r'(?im)^\s*(?:Investigational\s+(?:drug|product)|Study\s+drug|'
                                 r'Compound|제품명|시험약)\s*[:：]\s*'
                                 + re.escape(expected_product.strip()) + r'\s*$')
        declarations = list(declaration.finditer(context, heading['end'], quote_lo))
        if (declarations and not any(isinstance(other, str) and other.casefold() != expected_product.casefold()
                and re.search(r'(?<![\w-])' + re.escape(other) + r'(?![\w-])',
                              context[heading['end']:section_end], re.I) for other in known_products)):
            product = declarations[-1]
    if not product:
        return None
    anchors.extend([{'kind': 'body_section', 'start': heading['start'], 'end': heading['end']},
                    {'kind': 'product_in_section', 'start': product.start(), 'end': product.end()},
                    {'kind': 'quoted_span', 'start': quote_lo, 'end': quote_hi}])
    return {'version': 1, 'family': family, 'field_label': field_label, 'document_sha256': identity,
            'page': source['page'], 'context_sha256': sha256(context.encode()).hexdigest(),
            'section': heading['title'], 'section_end': section_end,
            'product_binding_policy': product_policy,
            'study_id': study, 'reporting_period': period, 'product': expected_product,
            'anchors': anchors}
