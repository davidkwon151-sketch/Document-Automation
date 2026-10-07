"""PDF choice codes stay distinct from their native display labels."""

import json

from .fill import TemplateError


COMBO = 1 << 17
EDIT = 1 << 18
MULTISELECT = 1 << 21


def choice_metadata(options, flags):
    """Read /Opt without sorting, converting labels, or inventing selections."""
    if not isinstance(flags, int) or isinstance(flags, bool) or flags < 0:
        raise TemplateError('PDF 선택 플래그가 올바르지 않음')
    if not isinstance(options, (list, tuple)):
        raise TemplateError('PDF 원본 선택 목록이 올바르지 않음')
    if flags & EDIT and not flags & COMBO or flags & COMBO and flags & MULTISELECT:
        raise TemplateError('지원하지 않는 PDF 선택 플래그 조합임')
    items, blanks, seen = [], [], set()
    for index, option in enumerate(options):
        if isinstance(option, str):
            value = label = option
        elif isinstance(option, (list, tuple)) and len(option) == 2 and all(isinstance(v, str) for v in option):
            value, label = option
        else:
            raise TemplateError('PDF 원본 선택 코드·표시 문구가 올바르지 않음')
        if not value.strip():
            if label.strip():
                raise TemplateError('빈 저장 코드에 의미 있는 표시 문구가 있어 선택을 구분할 수 없음')
            blanks.append({'index': index, 'value': value, 'label': label})
            continue
        if value != value.strip() or not label.strip() or value in seen:
            raise TemplateError('PDF 원본 선택 코드가 중복되거나 공백·표시 문구가 모호함')
        if any(ord(c) < 32 for c in value + label):
            raise TemplateError('PDF 선택 코드·표시 문구에 제어 문자가 있음')
        seen.add(value)
        items.append({'value': value, 'label': label})
    if not items and not flags & EDIT:
        raise TemplateError('PDF 선택 가능한 원본 코드가 없음')
    editable = bool(flags & EDIT)
    result = {'control_type': 'combobox' if editable else 'choice',
              'options': [item['value'] for item in items], 'choice_items': items,
              'allow_custom': editable, 'multiselect': bool(flags & MULTISELECT),
              'pdf_choice_flags': flags, 'pdf_blank_options': blanks}
    if result['multiselect']:
        result['selection_encoding'] = 'json_array'
    return result


def validate_choice_metadata(field):
    """Validate plain profile metadata; a writer must also bind it to /Opt,/Ff."""
    options, items = field.get('options'), field.get('choice_items')
    if (not isinstance(options, list) or not options and not (field.get('control_type') == 'combobox' and field.get('allow_custom') is True) or
        any(not isinstance(v, str) or not v.strip() or v != v.strip() for v in options) or
        len(set(options)) != len(options)):
        raise TemplateError('선택 코드 목록이 올바르지 않음')
    if items is not None and (not isinstance(items, list) or len(items) != len(options) or
        any(not isinstance(item, dict) or set(item) != {'value', 'label'} or
            item['value'] != code or not isinstance(item['label'], str) or not item['label'].strip()
            for item, code in zip(items, options))):
        raise TemplateError('선택 코드와 표시 문구의 대응이 올바르지 않음')
    multi, custom = field.get('multiselect', False), field.get('allow_custom', False)
    if not isinstance(multi, bool) or not isinstance(custom, bool):
        raise TemplateError('선택 입력 조건은 boolean이어야 함')
    control = field.get('control_type')
    if control not in {'choice', 'combobox'} or custom != (control == 'combobox') or multi and custom:
        raise TemplateError('선택 입력 종류와 자유 입력 조건이 일치하지 않음')
    if multi and field.get('selection_encoding') != 'json_array':
        raise TemplateError('다중 선택은 JSON 배열 문자열이어야 함')
    if not multi and field.get('selection_encoding') not in {None, ''}:
        raise TemplateError('단일 선택에 다중 직렬화 조건이 있음')
    if 'pdf_choice_flags' in field:
        flags = field['pdf_choice_flags']
        if (not isinstance(flags, int) or isinstance(flags, bool) or flags < 0 or
            bool(flags & MULTISELECT) != multi or bool(flags & EDIT) != custom or
            flags & EDIT and not flags & COMBO or flags & COMBO and multi):
            raise TemplateError('원본 PDF 선택 플래그와 프로파일이 일치하지 않음')
    if 'pdf_blank_options' in field:
        blanks = field['pdf_blank_options']
        if (not isinstance(blanks, list) or any(not isinstance(item, dict) or set(item) != {'index', 'value', 'label'} or
            not isinstance(item['index'], int) or isinstance(item['index'], bool) or item['index'] < 0 or
            not isinstance(item['value'], str) or not isinstance(item['label'], str) or
            item['value'].strip() or item['label'].strip() for item in blanks) or
            len({item['index'] for item in blanks}) != len(blanks) or
            any(item['index'] >= len(options) + len(blanks) for item in blanks)):
            raise TemplateError('원본 PDF 공백 선택 항목의 증거가 올바르지 않음')


def encode_choice_values(values):
    if (not isinstance(values, (list, tuple)) or not values or
        any(not isinstance(v, str) or not v.strip() for v in values) or len(set(values)) != len(values)):
        raise TemplateError('다중 선택은 중복 없는 비어 있지 않은 문자열 배열이어야 함')
    return json.dumps(list(values), ensure_ascii=False, separators=(',', ':'))


def parse_choice_value(field, value, *, literal=None):
    """Return selected codes in source order; leave the supplied string intact."""
    validate_choice_metadata(field)
    if not isinstance(value, str) or not value.strip():
        raise TemplateError('선택값이 비어 있음')
    options = field['options']
    # An actual code such as "[S1]" is data, rather than a trailing citation.
    if not field.get('multiselect') and value in options:
        return [value]
    from .value_rules import plain_form_value
    if field.get('multiselect'):
        try:
            selected = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            try:
                selected = json.loads(plain_form_value(value, field, literal=literal))
            except (json.JSONDecodeError, TypeError) as exc:
                raise TemplateError('다중 선택값은 JSON 배열 문자열이어야 함') from exc
        if (not isinstance(selected, list) or not selected or
            any(not isinstance(v, str) or v not in options for v in selected) or len(set(selected)) != len(selected)):
            raise TemplateError('목록에 없는 값·중복값·빈 배열을 다중 선택할 수 없음')
        return [option for option in options if option in selected]
    selected = plain_form_value(value, field, literal=literal)
    if selected in options:
        return [selected]
    if field.get('allow_custom') and selected and not any(ord(c) < 32 for c in selected):
        return [selected]
    raise TemplateError('목록에 없는 선택값임')
