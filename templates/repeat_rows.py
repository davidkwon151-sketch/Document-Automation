"""Prepare a separate variable-row form and verify it before publishing it."""

from hashlib import sha256
import os
from pathlib import Path
import tempfile
from zipfile import ZipFile

from parsers.extract import _check_zip, _path
from templates.compatibility import _rewrite_zip
from templates.fill import TemplateError


def _package(path):
    source = _path(path)
    if source.suffix.lower() not in {'.docx', '.hwpx', '.xlsx'}:
        raise TemplateError('반복 행은 DOCX/HWPX/XLSX 양식에서 지원함')
    with ZipFile(source) as archive:
        _check_zip(archive)
        parts = {item.filename: archive.read(item) for item in archive.infolist()}
    return source, parts


def _adapter(source):
    if source.suffix.lower() == '.xlsx':
        from templates import repeat_xlsx
        return repeat_xlsx
    if source.suffix.lower() == '.docx':
        from templates import repeat_docx
        return repeat_docx
    from templates import repeat_hwpx
    return repeat_hwpx


def inspect_repeat_tables(path):
    """Return physical rows; the user selects the business row to repeat."""
    source, parts = _package(path)
    return _adapter(source).inventory(parts)


def prepare_repeat_template(original_path, prepared_path, plan):
    """Expand, independently verify, then atomically save without changing source."""
    from agent.repeat_check import verify_repeat_expansion

    source, parts = _package(original_path)
    target = Path(prepared_path)
    if source.resolve() == target.resolve() or source.suffix.lower() != target.suffix.lower():
        raise TemplateError('반복 행 준비본은 원본과 다른 경로의 같은 형식이어야 함')
    original_sha = sha256(source.read_bytes()).hexdigest()
    changes = _adapter(source).transform(parts, plan)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, suffix=target.suffix)
    os.close(descriptor)
    staged = Path(temporary)
    try:
        _rewrite_zip(source, staged, changes)
        checked = verify_repeat_expansion(source, staged, plan)
        if sha256(source.read_bytes()).hexdigest() != original_sha:
            raise TemplateError('행 준비 중 원본이 변경되어 다시 분석해야 함')
        prepared_sha = sha256(staged.read_bytes()).hexdigest()
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)
    return {'original_path': str(source.resolve()), 'prepared_path': str(target.resolve()),
            'original_sha256': original_sha, 'prepared_sha256': prepared_sha,
            'plan': dict(plan), 'verification': checked}


def expansion_binding(expansion):
    return {key: expansion[key] for key in ('original_sha256', 'prepared_sha256', 'plan')}


def validate_repeat_fields(prepared_path, profile, *, original_path=None):
    """Rebuild source positions so a global mapping cannot replace row slots."""
    from templates.repeat_fields import repeat_profile
    from templates.value_rules import mapped_rule_profile

    binding = profile.get('repeat_expansion')
    if not binding:
        return
    expected = repeat_profile(prepared_path, binding.get('plan'), binding)
    source_profile = profile.get('repeat_source_profile')
    if source_profile:
        from templates.repeat_rules import inherit_repeat_rules
        expected = inherit_repeat_rules(original_path, expected, source_profile, prepared_path=prepared_path)
    original_fields = {field['id']: field for field in expected['fields']}
    fields = profile.get('fields', [])
    if (not isinstance(fields, list) or any(not isinstance(field, dict) for field in fields)
            or len({field.get('id') for field in fields}) != len(fields)):
        raise TemplateError('반복 행의 입력칸 ID가 누락 또는 중복됨')
    actual = {field['id']: field for field in fields}
    if any(identifier not in original_fields for identifier in actual):
        raise TemplateError('반복 행의 원래 위치별 입력칸을 다른 종류로 대체할 수 없음')
    for identifier, original in original_fields.items():
        field = actual.get(identifier)
        if field is None:
            if original.get('required'):
                raise TemplateError('반복 행의 필수 원래 입력 위치가 누락됨')
            continue
        for key in ('kind', 'xml_path', 'anchor_text', 'placeholder_start', 'placeholder_end',
                    'placeholder_key', 'repeat_info', 'control_type', 'options', 'choice_items', 'allow_custom', 'xlsx_list'):
            if original.get(key) != field.get(key):
                raise TemplateError('반복 행의 입력 위치·원문·원본 선택 제약이 변경됨')
        if original.get('required') and not field.get('required'):
            raise TemplateError('반복 행의 필수 항목을 완화할 수 없음')
        if original.get('input_required') and not field.get('input_required'):
            raise TemplateError('반복 행의 직접 입력 조건을 완화할 수 없음')
        if original.get('input_required') and field.get('input_mode', 'user_provided') != 'user_provided':
            raise TemplateError('반복 행의 직접 입력을 자료 생성 모드로 바꿀 수 없음')
        if original.get('max_chars'):
            maximum = field.get('max_chars')
            if type(maximum) is not int or not 0 < maximum <= original['max_chars']:
                raise TemplateError('반복 행의 원본 분량 제한을 완화할 수 없음')
        if original.get('validation') and original['validation'] != field.get('validation'):
            raise TemplateError('반복 행의 원본 입력 규칙이 변경됨')
    repeated_keys = [field.get('value_key') for field in fields if field.get('repeat_info')]
    if any(not isinstance(key, str) or not key.strip() for key in repeated_keys) or len(set(repeated_keys)) != len(repeated_keys):
        raise TemplateError('서로 다른 반복 입력칸은 고유한 값 이름이 필요함')
    remapped = mapped_rule_profile(expected, {field['id']: field['value_key'] for field in fields})
    for name in ('groups', 'relations'):
        for constraint in remapped.get('constraints', {}).get(name, []):
            if constraint not in profile.get('constraints', {}).get(name, []):
                raise TemplateError('반복 행의 필수 관계·함께 입력 규칙이 누락 또는 변경됨')


def verify_prepared_template(prepared_path, profile, expansion):
    """Recheck the original-to-prepared stage immediately before final export."""
    from agent.repeat_check import verify_repeat_expansion

    if not isinstance(expansion, dict) or not isinstance(profile, dict):
        raise TemplateError('반복 행 준비 기록이 필요함')
    try:
        binding = expansion_binding(expansion)
        original = Path(expansion['original_path'])
        prepared = Path(prepared_path)
        if (profile.get('repeat_expansion') != binding
                or prepared.resolve() != Path(expansion['prepared_path']).resolve()
                or sha256(original.read_bytes()).hexdigest() != binding['original_sha256']
                or sha256(prepared.read_bytes()).hexdigest() != binding['prepared_sha256']
                or profile.get('source_sha256') != binding['prepared_sha256']):
            raise TemplateError('검수한 반복 행 원본·준비본·계획이 변경됨')
    except (KeyError, TypeError, OSError) as exc:
        raise TemplateError('반복 행 원본·준비본 기록을 확인할 수 없음') from exc
    checked = verify_repeat_expansion(original, prepared, binding['plan'])
    validate_repeat_fields(prepared, profile, original_path=original)
    return checked
