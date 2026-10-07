"""User-visible form mapping, separate from the document serialization engine."""

from copy import deepcopy
import json
from templates.value_rules import validate_rule_profile, mapped_rule_profile, inspect_form_values

VALUE_TYPES = {'일반 문자': None, '정수': 'integer', '숫자': 'number', '날짜': 'date', '목록 선택': 'choice'}
RELATION_TYPES = {'합계': 'sum', '이하': 'less_equal', '날짜 순서': 'date_order', '년·월·일': 'calendar_date'}


def mapping_rows(profile):
    return [{"입력칸 ID": field['id'], "항목": field['label'],
             "채울 값": field['value_key'] if profile.get('configured') or field.get('required') or field.get('suggested_mapping') else '',
             "필수": field.get('required', False), "직접 입력": field.get('input_required', False), "최대 글자": field.get('max_chars', 0)}
            for field in profile['fields']]


def configure_profile(profile, rows):
    validate_rule_profile(profile)
    available = {field['id']: field for field in profile['fields']}
    if (not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get('입력칸 ID'), str) for row in rows)
            or len({row['입력칸 ID'] for row in rows}) != len(rows)):
        raise ValueError('매핑 행의 입력칸 ID가 중복됨')
    if {field['id'] for field in profile['fields'] if field.get('required') is True} - {row['입력칸 ID'] for row in rows}:
        raise ValueError('필수 원본 입력칸을 매핑에서 제외할 수 없음')
    selected, mapping = [], {}
    for row in rows:
        identifier = row['입력칸 ID']
        if identifier not in available:
            raise ValueError('양식에 없는 입력칸임')
        field = deepcopy(available[identifier])
        key = row.get('채울 값', '').strip()
        if not key:
            if field.get('required'):
                raise ValueError(f"필수 입력칸 매핑이 필요함: {field['label']}")
            continue
        if len(key) > 100 or any(c in key for c in '\n\r\t'):
            raise ValueError('채울 값의 이름이 잘못됨')
        maximum = row.get('최대 글자') or 0
        if type(maximum) is not int or maximum < 0 or type(row.get('필수')) is not bool:
            raise ValueError('최대 글자는 0 이상 정수, 필수는 체크값이어야 함')
        field.update(value_key=key, required=field.get('required', False) or row['필수'], input_required=field.get('input_required', False) or bool(row.get('직접 입력', False)))
        field['input_mode'] = 'user_provided' if field['input_required'] else 'source_grounded'
        if maximum:
            field['max_chars'] = maximum
        selected.append(field)
        mapping[identifier] = key
    if not selected:
        raise ValueError('채울 입력칸을 하나 이상 지정해 주세요')
    result = mapped_rule_profile(profile, mapping)
    result['fields'] = selected
    result['configured'] = True
    validate_rule_profile(result)
    return result, mapping


def validation_rows(profile):
    """Show constraints separately from location mapping to keep the main form short."""
    validate_rule_profile(profile)
    labels = {value: label for label, value in VALUE_TYPES.items()}
    rows = []
    for field in profile['fields']:
        rule = field.get('validation', {})
        if not rule and field.get('control_type') in {'choice', 'radio'}:
            rule = {'type': 'choice', 'options': field.get('options', [])}
        rows.append({'입력칸 ID': field['id'], '항목': field['label'],
                     '값 형식': labels[rule.get('type')], '단위': rule.get('unit', ''),
                     '단위 위치': '양식에 인쇄됨' if rule.get('unit_location') == 'label' else '입력값에 표시',
                     '최솟값': str(rule.get('min', '')), '최댓값': str(rule.get('max', '')),
                     '소수 자릿수': str(rule.get('decimal_places', '')),
                     '날짜 형식': ' | '.join(rule.get('date_formats', ['YYYY-MM-DD'])) if rule.get('type') == 'date' else '',
                     '선택값 JSON': json.dumps(rule.get('options', []), ensure_ascii=False) if rule.get('type') == 'choice' else ''})
    return rows


def configure_value_rules(profile, rows, *, relation_items=None):
    result = deepcopy(profile)
    available = {field['id']: field for field in result['fields']}
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('입력 규칙은 항목별 목록이어야 함')
    if len(rows) != len(available) or {row.get('입력칸 ID') for row in rows} != set(available):
        raise ValueError('입력 규칙에서 입력칸을 누락하거나 중복할 수 없음')
    for row in rows:
        field = available[row['입력칸 ID']]
        label = row.get('값 형식')
        if label not in VALUE_TYPES:
            raise ValueError(f"값 형식을 확인해 주세요: {field['label']}")
        kind = VALUE_TYPES[label]
        evidence_unit = field.get('validation', {}).get('evidence_unit')
        if evidence_unit is not None and kind not in {'integer', 'number'}:
            raise ValueError(f"등록된 근거 단위의 숫자 형식을 제거할 수 없음: {field['label']}")
        if kind is None:
            field.pop('validation', None)
            continue
        rule = {'type': kind}
        if evidence_unit is not None:
            rule['evidence_unit'] = evidence_unit
        if kind == 'choice':
            try:
                rule['options'] = json.loads(row.get('선택값 JSON') or '[]')
            except (TypeError, ValueError) as exc:
                raise ValueError('선택값은 JSON 문자열 목록으로 입력해야 함') from exc
            if any(row.get(name) not in ('', None) for name in ('단위', '최솟값', '최댓값', '소수 자릿수', '날짜 형식')):
                raise ValueError(f"목록 선택에는 숫자·날짜 제한을 적용할 수 없음: {field['label']}")
        elif kind == 'date':
            formats = row.get('날짜 형식', '') or 'YYYY-MM-DD'
            if not isinstance(formats, str):
                raise ValueError('날짜 형식은 문자열이어야 함')
            rule['date_formats'] = [part.strip() for part in formats.split('|')]
            if any(row.get(name) not in ('', None) for name in ('단위', '최솟값', '최댓값', '소수 자릿수')):
                raise ValueError(f"날짜에는 숫자 제한을 적용할 수 없음: {field['label']}")
        else:
            if row.get('날짜 형식') not in ('', None):
                raise ValueError(f"숫자에는 날짜 형식을 적용할 수 없음: {field['label']}")
            unit = row.get('단위', '') or ''
            if not isinstance(unit, str):
                raise ValueError('단위는 원문에 인쇄된 문자열이어야 함')
            if unit.strip():
                rule['unit'] = unit.strip()
                location = row.get('단위 위치', '입력값에 표시')
                if location not in {'양식에 인쇄됨', '입력값에 표시'}:
                    raise ValueError('단위 위치를 확인해 주세요')
                rule['unit_location'] = 'label' if location == '양식에 인쇄됨' else 'value'
            for column, key in (('최솟값', 'min'), ('최댓값', 'max')):
                value = row.get(column)
                if value not in ('', None):
                    if not isinstance(value, str):
                        raise ValueError(f'{column}은 숫자 문자열이어야 함')
                    rule[key] = value.strip()
            places = row.get('소수 자릿수')
            if places not in ('', None):
                if not isinstance(places, str) or not places.isascii() or not places.isdecimal():
                    raise ValueError('소수 자릿수는 0 이상 정수이어야 함')
                rule['decimal_places'] = int(places)
        field['validation'] = rule
        field['narrative_style_required'] = False
        if kind == 'choice':
            field.update(input_required=True, input_mode='user_provided')
    if relation_items is not None:
        return configure_relations(result, relation_items)
    validate_rule_profile(result)
    return result


def relation_rows(profile):
    validate_rule_profile(profile)
    rows = []
    labels = {value: label for label, value in RELATION_TYPES.items()}
    for rule in profile.get('constraints', {}).get('relations', []):
        kind = rule['kind']
        left = rule.get('total', rule.get('left', rule.get('start', rule.get('year'))))
        right = (' | '.join(rule['parts']) if kind == 'sum'
                 else ' | '.join([rule['month'], rule['day']]) if kind == 'calendar_date'
                 else rule.get('right', rule.get('end')))
        rows.append({'검사': labels[kind], '기준 항목': left, '비교 항목': right,
                     '허용 오차': str(rule.get('tolerance', ''))})
    return rows or [{'검사': '합계', '기준 항목': '', '비교 항목': '', '허용 오차': ''}]


def configure_relations(profile, rows):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('항목 간 검사는 목록이어야 함')
    rules = []
    for row in rows:
        left, right = row.get('기준 항목', ''), row.get('비교 항목', '')
        tolerance = row.get('허용 오차', '')
        if not left and not right and tolerance in ('', None):
            continue
        if not isinstance(left, str) or not isinstance(right, str) or row.get('검사') not in RELATION_TYPES:
            raise ValueError('검사 종류와 비교할 항목명을 확인해 주세요')
        kind = RELATION_TYPES[row['검사']]
        rule = {'kind': kind}
        if kind == 'sum':
            rule.update(total=left.strip(), parts=[part.strip() for part in right.split('|')])
            if tolerance not in ('', None):
                if not isinstance(tolerance, str):
                    raise ValueError('허용 오차는 숫자 문자열이어야 함')
                rule['tolerance'] = tolerance.strip()
        else:
            if tolerance not in ('', None):
                raise ValueError('허용 오차는 합계 검사에만 적용함')
            if kind == 'calendar_date':
                parts = [part.strip() for part in right.split('|')]
                if len(parts) != 2:
                    raise ValueError('년·월·일 검사의 비교 항목은 월 항목 | 일 항목이어야 함')
                rule.update(year=left.strip(), month=parts[0], day=parts[1])
            else:
                rule.update(**({'left': left.strip(), 'right': right.strip()} if kind == 'less_equal'
                               else {'start': left.strip(), 'end': right.strip()}))
        rules.append(rule)
    result = deepcopy(profile)
    constraints = result.setdefault('constraints', {})
    constraints['relations'] = rules
    validate_rule_profile(result)
    return result


def value_rule_help(field):
    if field.get('multiselect'):
        return '여러 항목을 선택할 수 있음. 선택하지 않으면 원본 입력칸을 유지함.'
    if field.get('control_type') == 'combobox':
        return '기존 목록의 저장 값 또는 직접 입력한 한 줄 문구를 사용함.'
    rule = field.get('validation', {})
    if not rule and field.get('control_type') in {'choice', 'radio'}:
        rule = {'type': 'choice', 'options': field.get('options', [])}
    if not rule:
        return None
    if rule['type'] == 'choice':
        return '허용된 목록에서 선택함. 표시 이름과 저장 값이 다를 수 있음.'
    if rule['type'] == 'date':
        return '날짜 형식: ' + ' / '.join(rule.get('date_formats', ['YYYY-MM-DD']))
    parts = ['정수로 입력' if rule['type'] == 'integer' else '숫자로 입력']
    if rule.get('unit'):
        parts.append('단위는 양식에 인쇄되어 숫자만 입력' if rule.get('unit_location') == 'label'
                     else f"단위 포함: {rule['unit']}")
    elif rule.get('evidence_unit'):
        parts.append(f"기입은 숫자만 · 원자료 대조 단위: {rule['evidence_unit']}")
    if 'min' in rule:
        parts.append(f"최소 {rule['min']}")
    if 'max' in rule:
        parts.append(f"최대 {rule['max']}")
    if 'decimal_places' in rule:
        parts.append(f"소수 {rule['decimal_places']}자리 이내")
    return ' · '.join(parts)


def group_rows(profile):
    """Keep related row/section names exact, including names containing delimiters."""
    validate_rule_profile(profile)
    rows = [{'함께 입력할 항목 JSON': json.dumps(group['fields'], ensure_ascii=False),
             '사용 시 필수 항목 JSON': json.dumps(group['required_fields'], ensure_ascii=False) if 'required_fields' in group else ''}
            for group in profile.get('constraints', {}).get('groups', [])]
    return rows or [{'함께 입력할 항목 JSON': '', '사용 시 필수 항목 JSON': ''}]


def configure_groups(profile, rows):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('함께 입력할 항목은 목록이어야 함')
    groups = []
    for row in rows:
        names = row.get('함께 입력할 항목 JSON', '')
        required = row.get('사용 시 필수 항목 JSON', '')
        if names in ('', None) and required in ('', None):
            continue
        try:
            group = {'kind': 'all_or_none', 'fields': json.loads(names)}
            if required not in ('', None):
                group['required_fields'] = json.loads(required)
        except (ValueError, TypeError) as exc:
            raise ValueError('함께 입력할 항목과 필수 항목은 JSON 문자열 목록이어야 함') from exc
        groups.append(group)
    result = deepcopy(profile)
    result.setdefault('constraints', {})['groups'] = groups
    validate_rule_profile(result)
    return result


def input_group_issues(values, profile):
    """Validate fully user-entered groups before generation; grounded cells come later."""
    fields = deepcopy(profile['fields'])
    direct = {field['value_key'] for field in fields if field.get('input_required')}
    for field in fields:
        field['required'] = False
    groups = [group for group in profile.get('constraints', {}).get('groups', [])
              if set(group['fields']) <= direct]
    return inspect_form_values(values, {'fields': fields, 'constraints': {'groups': groups}})


def selection_options(field):
    """Export values, never automatically consent or select the first item."""
    control = field.get('control_type')
    if control == 'checkbox':
        return list(dict.fromkeys(['', *field.get('options', []),
                                  *([] if field.get('kind') == 'docx_checkbox' else ['/Off'])]))
    if control in {'choice', 'radio'}:
        return list(field.get('options', [])) if field.get('multiselect') else ['', *field.get('options', [])]
    if field.get('validation', {}).get('type') == 'choice':
        return ['', *field['validation']['options']]
    return None


def selection_label(field, value):
    if value == '':
        return '선택해 주세요'
    if field.get('control_type') == 'checkbox':
        if value in {'false', '/Off'}:
            return '체크하지 않음'
        if value == 'true':
            return '체크함'
    return next((item['label'] for item in field.get('choice_items', []) if item['value'] == value), value)
