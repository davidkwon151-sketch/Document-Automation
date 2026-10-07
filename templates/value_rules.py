"""Explicit scalar/relationship rules; never infer units, convert, or edit values.

Declared validation and native closed choices are inspected. Free text is
inspected only for group presence. Blank optional rows are allowed; partial rows
must include their declared required members.
The finite numeric token limit bounds Decimal work, rather than silently rounding.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext
import math
import re

MAX_NUMERIC_CHARACTERS = 4096
NUMERIC_TYPES = {'integer', 'number'}
FIXED_SCALAR_KINDS = {'pdf_overlay', 'pdf_form', 'docx_cell', 'docx_sdt', 'docx_append',
                     'docx_inline', 'docx_legacy_text', 'hwpx_cell', 'hwpx_append',
                     'hwpx_inline', 'xlsx_cell', 'pptx_cell', 'pptx_text'}
NUMBER = re.compile(r'(?P<number>[+-]?(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+))(?P<fraction>\.[0-9]+)?')
DECIMAL_TEXT = re.compile(r'[+-]?[0-9]+(?:\.[0-9]+)?')
END_CITATION = re.compile(r'\[S[A-Za-z0-9_-]+\]$')
CITATION = re.compile(r'\[S[A-Za-z0-9_-]+\]')
DATE_FORMATS = {
    'YYYY-MM-DD': re.compile(r'([0-9]{4})-([0-9]{2})-([0-9]{2})'),
    'YYYY.MM.DD': re.compile(r'([0-9]{4})\.([0-9]{2})\.([0-9]{2})'),
    'YYYY년 M월 D일': re.compile(r'([0-9]{4})년 +([0-9]{1,2})월 +([0-9]{1,2})일'),
}


def _key(value):
    if not isinstance(value, str) or not value or value != value.strip() or any(c in value for c in '\r\n\t'):
        raise ValueError('규칙의 항목 이름은 앞뒤 공백·줄바꿈 없는 비어 있지 않은 문자열이어야 함')
    return value


def _bound(value):
    if type(value) not in (int, float, str) or isinstance(value, float) and not math.isfinite(value):
        raise ValueError('범위·허용 오차는 유한 JSON 숫자 또는 일반 십진 문자열이어야 함')
    if isinstance(value, str) and (len(value) > MAX_NUMERIC_CHARACTERS or not DECIMAL_TEXT.fullmatch(value)):
        raise ValueError('십진 범위·허용 오차에 지수·쉼표·단위·특수값을 사용할 수 없음')
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('비유한 숫자를 규칙으로 사용할 수 없음')
    return result


def _options(values):
    if (not isinstance(values, list) or not values or any(not isinstance(value, str) or not value.strip()
            or value != value.strip() for value in values) or len(set(values)) != len(values)):
        raise ValueError('선택 옵션은 앞뒤 공백 없는 비어 있지 않은 정확 문자열의 중복 없는 목록이어야 함')
    return frozenset(values)


def _validation(raw):
    if not isinstance(raw, dict) or set(raw) - {'type', 'unit', 'unit_location', 'evidence_unit', 'min', 'max', 'decimal_places', 'date_formats', 'options'}:
        raise ValueError('항목 validation 구조 또는 지원하지 않는 규칙 키가 잘못됨')
    kind = raw.get('type')
    if not isinstance(kind, str) or kind not in {'integer', 'number', 'date', 'choice'}:
        raise ValueError('validation type은 integer, number, date, choice 중 하나이어야 함')
    if kind == 'choice':
        if set(raw) - {'type', 'options'}:
            raise ValueError('선택 규칙에 숫자·날짜 전용 metadata를 사용할 수 없음')
        return {'type': kind, 'options': _options(raw.get('options'))}
    if kind == 'date':
        if set(raw) - {'type', 'date_formats'}:
            raise ValueError('날짜 규칙에 숫자 범위·단위·소수 자릿수를 사용할 수 없음')
        formats = raw.get('date_formats', ['YYYY-MM-DD'])
        if (not isinstance(formats, list) or not formats or any(not isinstance(x, str) or x not in DATE_FORMATS for x in formats)
                or len(formats) != len(set(formats))):
            raise ValueError('date_formats는 중복 없는 지원 날짜 형식 목록이어야 함')
        return {'type': kind, 'date_formats': tuple(formats)}
    if 'date_formats' in raw or 'options' in raw:
        raise ValueError('숫자 규칙에 날짜 형식·선택 옵션을 사용할 수 없음')
    unit = raw.get('unit')
    if 'unit' in raw and (not isinstance(unit, str) or not unit or unit != unit.strip()
                          or len(unit) > 64 or any(c in unit for c in '\r\n\t[]')):
        raise ValueError('unit은 명시된 짧은 단위 문자열이어야 함')
    location = raw.get('unit_location', 'value')
    if not isinstance(location, str) or location not in {'label', 'value'} or 'unit_location' in raw and unit is None:
        raise ValueError('unit_location은 단위가 있는 label 또는 value이어야 함')
    places = raw.get('decimal_places')
    if 'decimal_places' in raw and (type(places) is not int or not 0 <= places <= MAX_NUMERIC_CHARACTERS
                                    or kind == 'integer' and places != 0):
        raise ValueError('소수 자릿수는 0 이상 정수이며 integer에서는 0만 허용함')
    result = {'type': kind, 'unit': unit, 'unit_location': location, 'decimal_places': places}
    if 'evidence_unit' in raw:
        evidence_unit = raw['evidence_unit']
        if (not isinstance(evidence_unit, str) or not evidence_unit or evidence_unit != evidence_unit.strip()
                or len(evidence_unit) > 64 or any(c in evidence_unit for c in '\r\n\t[]')
                or unit is not None and unit != evidence_unit):
            raise ValueError('evidence_unit은 명시된 검수 단위이며 출력 단위와 상충할 수 없음')
        result['evidence_unit'] = evidence_unit
    for name in ('min', 'max'):
        if name in raw:
            result[name] = _bound(raw[name])
    if 'min' in result and 'max' in result and result['min'] > result['max']:
        raise ValueError('최솟값은 최댓값 이하이어야 함')
    return result


def _rules(profile):
    if profile is None:
        profile = {}
    if not isinstance(profile, dict) or not isinstance(profile.get('fields', []), list):
        raise ValueError('양식 프로파일과 fields 목록이 잘못됨')
    known, required_keys, rules, selection_contracts, evidence_roles, source_contracts = set(), set(), {}, {}, {}, {}
    for field in profile.get('fields', []):
        if not isinstance(field, dict):
            raise ValueError('양식 항목은 객체이어야 함')
        key = _key(field.get('value_key'))
        role = field.get('evidence_role')
        if 'evidence_role' in field and role != 'product_name':
            raise ValueError('evidence_role은 명시된 product_name 역할만 지원함')
        if key in evidence_roles and evidence_roles[key] != role:
            raise ValueError('동일한 값 이름에 상충하는 근거 역할을 지정할 수 없음')
        evidence_roles[key] = role
        source_contract = None
        if 'evidence_document_labels' in field:
            labels = field['evidence_document_labels']
            _options(labels)
            for document_label in labels:
                _key(document_label)
            scope = field.get('evidence_scope')
            label = field.get('label')
            if (not isinstance(scope, str) or not scope.strip() or scope != scope.strip()
                    or not isinstance(label, str) or not label.strip()):
                raise ValueError('문서 종류 근거에는 명시된 범위와 원본 항목명이 필요함')
            source_contract = (scope, tuple(labels), label)
        if key in source_contracts and source_contracts[key] != source_contract:
            raise ValueError('같은 값 이름에 서로 다른 원문 범위·문서 종류·항목을 합칠 수 없음')
        source_contracts[key] = source_contract
        known.add(key)
        if field.get('required') is True:
            required_keys.add(key)
        raw = field.get('validation')
        control = field.get('control_type')
        if control is not None and not isinstance(control, str):
            raise ValueError('control_type은 문자열이어야 함')
        if 'multiselect' in field and type(field['multiselect']) is not bool:
            raise ValueError('다중 선택 여부는 boolean이어야 함')
        if field.get('multiselect') and (field.get('kind') != 'pdf_form' or control != 'choice'
                or field.get('selection_encoding') != 'json_array'):
            raise ValueError('다중 선택은 원본 PDF 목록의 JSON 배열 문자열로 입력해야 함')
        if field.get('kind') == 'pdf_form' and control in {'choice', 'combobox'}:
            from .pdf_choices import validate_choice_metadata
            validate_choice_metadata(field)
            contract = (control, field.get('multiselect', False), field.get('allow_custom', False),
                        tuple(field.get('options', [])),
                        tuple((item['value'], item['label']) for item in field.get('choice_items',
                              [{'value': code, 'label': code} for code in field['options']])))
            if key in selection_contracts and selection_contracts[key] != contract:
                raise ValueError(f'같은 값에 상충하는 PDF 선택 방식이 지정됨: {key}')
            selection_contracts[key] = contract
        native = _options(field['options']) if control in {'choice', 'radio'} and 'options' in field else None
        if 'validation' not in field and native is not None:
            raw = {'type': 'choice', 'options': field['options']}
        if 'validation' not in field and native is None:
            continue
        rule = _validation(raw)
        if 'evidence_unit' in rule:
            _evidence_field(field)
        if native is not None and (rule['type'] != 'choice' or rule['options'] != native):
            raise ValueError('명시 선택 규칙은 원본 control의 옵션 집합과 같아야 함')
        if 'required' in field and type(field['required']) is not bool:
            raise ValueError('규칙 항목의 required는 체크값이어야 함')
        if key in rules and rules[key]['rule'] != rule:
            raise ValueError(f'같은 값에 상충하는 validation이 지정됨: {key}')
        if key not in rules:
            rules[key] = {'rule': rule, 'raw': raw, 'required': False}
            if field.get('kind') == 'pdf_form' and control == 'choice':
                rules[key]['native_choice'] = field
        elif (rules[key].get('native_choice', {}).get('multiselect', False)
              != field.get('multiselect', False)):
            raise ValueError(f'같은 값에 단일/다중 선택을 혼합할 수 없음: {key}')
        rules[key]['required'] |= field.get('required', False)
    for key, entry in rules.items():
        entry['required'] |= key in required_keys
    constraints = profile.get('constraints', {})
    if (not isinstance(constraints, dict) or not isinstance(constraints.get('relations', []), list)
            or not isinstance(constraints.get('groups', []), list)):
        raise ValueError('constraints의 relations와 groups는 목록이어야 함')
    relations, seen = [], set()
    for relation in constraints.get('relations', []):
        if not isinstance(relation, dict):
            raise ValueError('항목 관계는 객체이어야 함')
        kind = relation.get('kind')
        allowed = {'sum': {'kind', 'total', 'parts', 'tolerance'},
                   'less_equal': {'kind', 'left', 'right'},
                   'date_order': {'kind', 'start', 'end'},
                   'calendar_date': {'kind', 'year', 'month', 'day'}}
        if not isinstance(kind, str) or kind not in allowed or set(relation) - allowed[kind]:
            raise ValueError('지원하지 않는 관계 종류 또는 관계 키임')
        if kind == 'sum':
            total, parts = _key(relation.get('total')), relation.get('parts')
            if not isinstance(parts, list) or not parts:
                raise ValueError('합계 관계에는 항목 목록이 필요함')
            keys = [total, *[_key(part) for part in parts]]
            identity = (kind, total, tuple(sorted(parts)))
        else:
            names = {'less_equal': ('left', 'right'), 'date_order': ('start', 'end'),
                     'calendar_date': ('year', 'month', 'day')}[kind]
            keys = [_key(relation.get(name)) for name in names]
            identity = (kind, *keys)
        if len(set(keys)) != len(keys):
            raise ValueError('관계 안의 같은 항목을 중복하거나 자기 자신과 비교할 수 없음')
        if identity in seen:
            raise ValueError('동일한 항목 관계가 중복됨')
        seen.add(identity)
        if not set(keys) <= known or not set(keys) <= rules.keys():
            raise ValueError('관계는 등록된 validation 항목만 참조할 수 있음')
        expected = {'date'} if kind == 'date_order' else {'integer'} if kind == 'calendar_date' else NUMERIC_TYPES
        if any(rules[key]['rule']['type'] not in expected for key in keys):
            raise ValueError('관계의 항목 타입이 일치하지 않음')
        if kind == 'calendar_date' and any((rules[key]['rule'].get('evidence_unit') or rules[key]['rule']['unit']) not in (None, unit)
                                            for key, unit in zip(keys, ('년', '월', '일'))):
            raise ValueError('달력 관계의 단위는 각 항목의 년·월·일이거나 없어야 함')
        if kind in {'sum', 'less_equal'} and len({rules[key]['rule'].get('evidence_unit') or rules[key]['rule']['unit'] for key in keys}) != 1:
            raise ValueError('서로 다른 단위의 합계·대소 비교는 할 수 없음')
        item = {**relation, 'keys': keys}
        if kind == 'sum':
            item['tolerance'] = _bound(relation.get('tolerance', 0))
            if item['tolerance'] < 0:
                raise ValueError('합계 허용 오차는 0 이상이어야 함')
        relations.append(item)
    groups, seen = [], set()
    for group in constraints.get('groups', []):
        if (not isinstance(group, dict) or group.get('kind') != 'all_or_none'
                or set(group) - {'kind', 'fields', 'required_fields'}):
            raise ValueError('그룹은 지원하는 all_or_none 구조이어야 함')
        keys = group.get('fields')
        required = group.get('required_fields', keys)
        if not isinstance(keys, list) or not keys or not isinstance(required, list) or not required:
            raise ValueError('그룹 fields와 required_fields는 비어 있지 않은 항목 목록이어야 함')
        keys, required = [_key(key) for key in keys], [_key(key) for key in required]
        if len(set(keys)) != len(keys) or len(set(required)) != len(required):
            raise ValueError('그룹의 항목 이름이 중복됨')
        if not set(keys) <= known or not set(required) <= set(keys):
            raise ValueError('그룹은 등록된 항목과 그 안의 필수 항목만 참조할 수 있음')
        identity = tuple(sorted(keys))
        if identity in seen:
            raise ValueError('동일한 항목 그룹이 중복됨')
        seen.add(identity)
        groups.append({'fields': keys, 'required_fields': required})
    return rules, relations, groups


def validate_rule_profile(profile) -> None:
    """Validate only rule metadata, preserving unrelated form/layout properties."""
    _rules(profile)


def mapped_rule_profile(profile, mapping=None):
    """Copy/rebind ID -> value-key mappings without dropping relation dependencies.

    Repeated original keys may split only outside relationships. A relationship
    requires every original participating input to remain mapped to one key.
    """
    rules, relations, groups = _rules(profile)
    if mapping is None:
        return deepcopy(profile)
    if not isinstance(mapping, dict):
        raise ValueError('양식 매핑은 입력칸 ID와 값 이름의 객체이어야 함')
    fields = (profile or {}).get('fields', [])
    available = {}
    for field in fields:
        identifier = _key(field.get('id'))
        if identifier in available:
            raise ValueError('매핑 원본의 입력칸 ID가 중복됨')
        available[identifier] = field
    if any(not isinstance(key, str) or key not in available for key in mapping):
        raise ValueError('매핑에 양식에 없는 입력칸 ID가 있음')
    for value in mapping.values():
        _key(value)
    dependencies = {key for relation in relations for key in relation['keys']}
    dependencies.update(key for group in groups for key in group['fields'])
    destinations, selected = {}, []
    for identifier, field in available.items():
        old = field['value_key']
        if identifier not in mapping:
            if field.get('required') is True:
                raise ValueError('필수 원본 입력칸을 매핑에서 제외할 수 없음')
            if old in dependencies:
                raise ValueError('관계에 사용되는 원본 입력칸을 매핑에서 제외할 수 없음')
            continue
        new = mapping[identifier]
        destinations.setdefault(old, set()).add(new)
        copied = deepcopy(field)
        copied['value_key'] = new
        if old in rules:
            copied['validation'] = deepcopy(rules[old]['raw'])
            if rules[old]['required']:
                copied['required'] = True
        selected.append(copied)
    if any(len(destinations.get(key, set())) != 1 for key in dependencies):
        raise ValueError('관계의 동일 원본 값을 서로 다른 값 이름으로 나눌 수 없음')
    result = deepcopy(profile or {})
    result['fields'] = selected
    if relations:
        rewritten = []
        for original in (profile or {})['constraints']['relations']:
            item = deepcopy(original)
            names = {'sum': ('total',), 'less_equal': ('left', 'right'), 'date_order': ('start', 'end'),
                     'calendar_date': ('year', 'month', 'day')}[item['kind']]
            for name in names:
                item[name] = next(iter(destinations[item[name]]))
            if item['kind'] == 'sum':
                item['parts'] = [next(iter(destinations[key])) for key in item['parts']]
            rewritten.append(item)
        result['constraints']['relations'] = rewritten
    if groups:
        rewritten = []
        for original in (profile or {})['constraints']['groups']:
            item = deepcopy(original)
            for name in ('fields', 'required_fields'):
                if name in item:
                    item[name] = [next(iter(destinations[key])) for key in item[name]]
            rewritten.append(item)
        result['constraints']['groups'] = rewritten
    validate_rule_profile(result)
    return result


def plain_form_value(value, field=None, *, literal=None):
    """Strip outer whitespace/trailing valid citations internally; never edit inputs.

    Free narrative group members have no numeric length limit, and internal
    citations stay intact. Native controls may use this only after rule checks.
    """
    if not isinstance(value, str):
        raise ValueError('입력 값은 문자열이어야 함. 숫자·bool 객체를 자동 변환하지 않음')
    if field is not None or literal is not None:
        from agent.field_citations import split_field_citations
        return split_field_citations(value, field, literal=literal)[0]
    text = value.strip()
    while match := END_CITATION.search(text):
        text = text[:match.start()].rstrip()
    return text


def _plain(value, *, bounded=True):
    if bounded and isinstance(value, str) and len(value) > MAX_NUMERIC_CHARACTERS:
        raise ValueError('숫자·날짜 입력의 검증 가능한 길이를 초과함')
    text = plain_form_value(value)
    if CITATION.search(text):
        raise ValueError('숫자·날짜 중간의 인용 표시는 허용하지 않음')
    return text


def _parse(text, rule):
    if rule['type'] == 'choice':
        if text not in rule['options']:
            raise ValueError('등록된 선택값(옵션)을 정확히 입력해야 함')
        return text
    if rule['type'] == 'date':
        for format_name in rule['date_formats']:
            if match := DATE_FORMATS[format_name].fullmatch(text):
                try:
                    return date(*map(int, match.groups()))
                except ValueError as exc:
                    raise ValueError('달력에 존재하는 날짜를 입력해야 함') from exc
        raise ValueError('선언된 날짜 형식으로 연·월·일 전체를 입력해야 함')
    unit = rule['unit']
    if unit and rule['unit_location'] == 'value':
        if not text.endswith(unit):
            raise ValueError(f'선언된 단위 {unit!r}를 정확히 입력해야 함. 단위 환산은 하지 않음')
        text = text[:-len(unit)].rstrip(' \t')
    match = NUMBER.fullmatch(text)
    if not match or rule['type'] == 'integer' and match['fraction'] is not None:
        detail = ('정수' if rule['type'] == 'integer' else '십진 숫자')
        raise ValueError(f'{detail} 전체를 입력해야 함. 잘못된 쉼표·지수·단위·문장을 허용하지 않음')
    return Decimal(text.replace(',', ''))


def _evidence_field(field):
    if (field.get('kind') not in FIXED_SCALAR_KINDS
            or field.get('value_key') in {'제목', '요약', '본문'}
            or field.get('narrative_style_required') is not False
            or field.get('control_type') not in {None, 'text'}):
        raise ValueError('검수 단위는 명시된 숫자·정수 고정 칸에만 적용할 수 있음')


def parse_scalar(value, validation):
    """Parse a declared numeric scalar exactly; do not alter the input or units."""
    rule = _validation(validation)
    if rule['type'] not in NUMERIC_TYPES:
        raise ValueError('숫자·정수 scalar 규칙이 필요함')
    parsed = _parse(_plain(value), rule)
    if ('min' in rule and parsed < rule['min'] or 'max' in rule and parsed > rule['max']
            or rule['decimal_places'] is not None
            and max(0, -parsed.as_tuple().exponent) > rule['decimal_places']):
        raise ValueError('scalar의 범위 또는 소수 자릿수가 선언된 규칙과 다름')
    return parsed


def field_evidence_scalar(value, field):
    """Return (exact value, declared evidence unit) only for valid bare fixed scalars.

    evidence_unit never appends a printed unit, supplies a population/product,
    converts a value, or changes relationship arithmetic.
    """
    if not isinstance(field, dict) or 'evidence_unit' not in field.get('validation', {}):
        return None
    _evidence_field(field)
    rule = _validation(field['validation'])
    if rule['type'] not in NUMERIC_TYPES or not NUMBER.fullmatch(_plain(value)):
        raise ValueError('검수 단위 연결에는 유효한 무단위 숫자·정수 전체 값이 필요함')
    return parse_scalar(value, field['validation']), rule['evidence_unit']


def inspect_form_values(values, profile, *, literal_values=None) -> list[dict]:
    """Return blocking issues without mutating text; bad rule schemas raise ValueError.

    Only syntactically valid trailing [S...] citations are removed internally.
    Ranges and absolute sum tolerances use exact Decimal arithmetic. Declared
    precision counts written fraction digits, including trailing zeroes.
    Calendar relationships validate separately printed integer year/month/day
    fields together; invalid combinations and partial dates point to the day.
    """
    rules, relations, groups = _rules(profile)
    if not isinstance(values, dict):
        raise ValueError('양식 값은 평면 객체이어야 함')
    if literal_values is not None and (not isinstance(literal_values, dict) or
            any(not isinstance(key, str) or not isinstance(value, str) for key, value in literal_values.items())):
        raise ValueError('직접 입력 원값은 문자열 객체여야 함')
    from agent.field_citations import is_selection_field, profile_field
    issues, parsed, blanks = [], {}, set()
    def issue(code, field, message):
        issues.append({'code': code, 'field': field, 'line': 1, 'message': message, 'severity': 'error'})
    for key, entry in rules.items():
        rule = entry['rule']
        try:
            raw_value = values.get(key, '')
            if rule['type'] == 'choice':
                # A genuine export code may itself look like a citation.
                metadata = profile_field(profile, key)
                if not is_selection_field(metadata):
                    metadata = {'control_type': 'choice', 'options': rule['options']}
                text = plain_form_value(raw_value, metadata, literal=(literal_values or {}).get(key))
            else:
                text = _plain(raw_value)
            if not text:
                blanks.add(key)
                if entry['required']:
                    issue('form_value_required', key, '필수 입력 값이 비어 있음')
                continue
            if entry.get('native_choice'):
                from .pdf_choices import parse_choice_value
                value = parse_choice_value(entry['native_choice'], raw_value, literal=(literal_values or {}).get(key))
            else:
                value = _parse(text, rule)
            parsed[key] = value
            if rule['type'] in NUMERIC_TYPES:
                if 'min' in rule and value < rule['min']:
                    issue('form_value_min', key, f"최솟값 {rule['min']} 이상이어야 함")
                if 'max' in rule and value > rule['max']:
                    issue('form_value_max', key, f"최댓값 {rule['max']} 이하이어야 함")
                places = rule['decimal_places']
                if places is not None and max(0, -value.as_tuple().exponent) > places:
                    issue('form_value_decimal_places', key, f'소수 자릿수는 {places}자리 이하이어야 함')
        except ValueError as exc:
            issue('form_value_' + rule['type'], key, str(exc))
    # Editable native combos still require a valid, one-line PDF choice value.
    # Optional blank input keeps the original control untouched.
    for field in (profile or {}).get('fields', []):
        if field.get('kind') != 'pdf_form' or field.get('control_type') != 'combobox':
            continue
        key = field['value_key']
        try:
            value = values.get(key, '')
            if not plain_form_value(value, field, literal=(literal_values or {}).get(key)):
                if field.get('required') and key not in rules:
                    issue('form_value_required', key, '필수 입력 값이 비어 있음')
                continue
            from .pdf_choices import parse_choice_value
            parse_choice_value(field, value, literal=(literal_values or {}).get(key))
        except ValueError as exc:
            issue('form_value_choice', key, str(exc))
    for relation in relations:
        keys = relation['keys']
        if all(key in blanks for key in keys):
            continue
        if any(key in blanks for key in keys):
            target = relation['day'] if relation['kind'] == 'calendar_date' else keys[0]
            issue('form_relation_missing', target, '항목 관계의 일부 값이 비어 있음: ' + ', '.join(key for key in keys if key in blanks))
            continue
        if any(key not in parsed for key in keys):
            continue  # The invalid field already has a blocking scalar issue.
        kind = relation['kind']
        if kind == 'sum':
            numbers = [parsed[key] for key in keys]
            integer_digits = max(max(0, value.adjusted() + 1) for value in numbers)
            fraction_digits = max(max(0, -value.as_tuple().exponent) for value in numbers)
            with localcontext() as context:
                context.prec = max(28, integer_digits + fraction_digits + len(str(len(numbers))) + 2)
                difference = abs(numbers[0] - sum(numbers[1:], Decimal(0)))
            if difference > relation['tolerance']:
                issue('form_relation_sum', keys[0], '합계가 구성 항목의 합과 허용 오차 안에서 일치하지 않음')
        elif kind == 'calendar_date':
            try:
                date(*(int(parsed[key]) for key in keys))
            except (ValueError, OverflowError):
                issue('form_relation_calendar_date', relation['day'], '분리된 연·월·일을 합친 날짜가 달력에 존재하지 않음')
        elif parsed[keys[0]] > parsed[keys[1]]:
            message = '왼쪽 값이 오른쪽 값보다 클 수 없음' if kind == 'less_equal' else '시작일이 종료일보다 늦을 수 없음'
            issue('form_relation_' + kind, keys[0], message)
    for group in groups:
        contents = {}
        for key in group['fields']:
            try:
                contents[key] = plain_form_value(values.get(key, ''), profile_field(profile, key),
                                                 literal=(literal_values or {}).get(key))
            except ValueError as exc:
                issue('form_group_value', key, str(exc))
                contents[key] = None
        if all(contents[key] == '' for key in group['fields']):
            continue
        for key in group['required_fields']:
            if contents[key] == '':
                issue('form_group_required', key, '그룹의 일부를 입력하면 이 필수 항목도 입력해야 함')
    return issues
