"""확장된 반복 행을 위치별 입력칸으로 연결함. 원본 XML은 변경하지 않음."""

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
from zipfile import ZipFile

from lxml import etree

from parsers.extract import _check_zip, _xml
from .compatibility import analyze_template
from .fill import PLACEHOLDER, WORD_NS, TemplateError, _segments
from .value_rules import validate_rule_profile


def _repeat_key(key, row, column, used):
    """원문 label은 유지하고 모델 입력 key만 100자 안에서 고유하게 만듦."""
    clean = re.sub(r'\s+', ' ', key).strip()
    suffix = f' (반복 {row}행 {column}열)'
    base = clean[:100 - len(suffix)] + suffix
    result, number = base, 2
    while result in used:
        tail = suffix + f' {number}'
        result = clean[:100 - len(tail)] + tail
        number += 1
    used.add(result)
    return result


def _binding(path, plan, binding):
    if (not isinstance(plan, dict) or set(plan) != {'table_id', 'row', 'count'}
            or not isinstance(plan['table_id'], str) or type(plan['row']) is not int
            or type(plan['count']) is not int or plan['row'] < 1 or not 1 <= plan['count'] <= 200):
        raise TemplateError('반복 입력 계획의 표·행·개수가 유효하지 않음')
    if (not isinstance(binding, dict) or set(binding) != {'original_sha256', 'prepared_sha256', 'plan'}
            or binding['plan'] != plan or any(not isinstance(binding[key], str)
                or not re.fullmatch('[0-9a-f]{64}', binding[key]) for key in ('original_sha256', 'prepared_sha256'))):
        raise TemplateError('반복 확장 해시와 계획의 바인딩이 유효하지 않음')
    data = path.read_bytes()
    if sha256(data).hexdigest() != binding['prepared_sha256']:
        raise TemplateError('준비된 양식의 SHA가 반복 확장 기록과 다름')
    return data


def _blocked(segment, native_targets, mode):
    node = segment.element
    chain = [node, *node.iterancestors()]
    if any(item in native_targets for item in chain):
        return True
    if mode == 'docx':
        for ancestor in chain:
            if ancestor.tag == f'{{{WORD_NS}}}sdt':
                if ancestor.xpath('./w:sdtPr/w:lock | ./w:sdtPr/w:dataBinding | ./w:sdtPr/w:dropDownList | ./w:sdtPr/w:comboBox | ./w:sdtPr/w:date | ./w:sdtPr/w:picture | ./w:sdtPr/w:group | ./w:sdtPr/*[local-name()="checkbox" or local-name()="repeatingSection" or local-name()="repeatingSectionItem"]', namespaces={'w':WORD_NS}):
                    return True
    return False


def _matches(paragraph, mode, native_targets):
    segments = _segments(paragraph, mode)
    text = ''.join(segment.value for segment in segments)
    search = ''.join(' ' * len(segment.value) if _blocked(segment, native_targets, mode) else segment.value for segment in segments)
    matches = list(PLACEHOLDER.finditer(search))
    offset = 0
    for segment in segments:
        if (not segment.editable or _blocked(segment, native_targets, mode)) and any(match.start() < offset + len(segment.value) and match.end() > offset for match in matches):
            raise TemplateError('반복 자리표시자가 제어 문자·줄 나눔을 가로지름')
        offset += len(segment.value)
    return text, matches


def repeat_profile(prepared_path, plan, binding):
    """준비 SHA에 연결된 위치별 프로파일을 반환함. 확장의 진위 검수는 호출자가 맡음."""
    path = Path(prepared_path)
    mode = path.suffix.lstrip('.').lower()
    if mode == 'xlsx':
        from templates.repeat_xlsx_profile import xlsx_repeat_profile
        return xlsx_repeat_profile(prepared_path, plan, binding)
    if mode not in {'docx','hwpx'}:
        raise TemplateError('반복 입력 프로파일은 DOCX/HWPX만 지원함')
    data = _binding(path, plan, binding)
    profile = deepcopy(analyze_template(path))
    if path.read_bytes() != data or profile.get('source_sha256', binding['prepared_sha256']) != binding['prepared_sha256']:
        raise TemplateError('반복 양식 분석 중 원본이 변경됨')
    if not profile.get('supported'):
        raise TemplateError('반복 양식의 입력 위치를 안전하게 분석할 수 없음')
    with ZipFile(BytesIO(data)) as archive:
        _check_zip(archive)
        names = [name for name in archive.namelist() if name.startswith('word/') and name.endswith('.xml')] if mode == 'docx' else [name for name in archive.namelist() if re.fullmatch(r'Contents/(section|masterpage)\d+\.xml', name)]
        roots = {name: _xml(archive.read(name)) for name in names}
    locations = {part: {root.getroottree().getpath(node): node for node in root.iter() if isinstance(node.tag, str)} for part, root in roots.items()}
    try:
        prefix, part, table_path = plan['table_id'].split(':', 2)
        table = locations[part][table_path]
    except (ValueError, KeyError) as exc:
        raise TemplateError('준비된 양식에 반복 대상 표가 없음') from exc
    if prefix != mode or etree.QName(table).localname != 'tbl':
        raise TemplateError('반복 표의 형식 또는 XML 위치가 다름')
    rows = table.xpath('./*[local-name()="tr"]')
    selected = rows[plan['row'] - 1:plan['row'] - 1 + plan['count']]
    if len(selected) != plan['count']:
        raise TemplateError('준비된 양식에 지정한 반복 행이 모두 없음')
    cells = {}
    for relative, row in enumerate(selected, 1):
        for column, cell in enumerate(row.xpath('./*[local-name()="tc"]'), 1):
            cells[cell] = (relative, column)
    def position(node):
        return next((cells[ancestor] for ancestor in [node,*node.iterancestors()] if ancestor in cells), None)
    existing = profile.get('fields', [])
    if not isinstance(existing, list):
        raise TemplateError('원본 입력칸 목록이 유효하지 않음')
    globals_by_key = {field['id'].split(':', 1)[1]: field for field in existing if field['kind'] == 'placeholder'}
    targets, outside, inside = {}, [], []
    for field in existing:
        if field['kind'] == 'placeholder':
            continue
        try:
            field_mode, field_part, xml_path = field['id'].split(':', 2)
            node = locations[field_part][xml_path]
        except (ValueError, KeyError) as exc:
            raise TemplateError('기존 입력칸의 원본 위치를 확인할 수 없음') from exc
        if field_mode != mode:
            raise TemplateError('기존 입력칸의 형식이 반복 양식과 다름')
        targets[node] = field
        found = position(node) if field_part == part else None
        if found:
            inside.append((deepcopy(field), found))
        else:
            outside.append(field)
    native_targets = {node for node, field in targets.items() if field['kind'] in {'docx_sdt','docx_checkbox','docx_choice','docx_combobox'}}
    outside_keys = set()
    for field_part, root in roots.items():
        for paragraph in root.xpath('.//*[local-name()="p"]'):
            text, matches = _matches(paragraph, mode, native_targets)
            found = position(paragraph) if field_part == part else None
            if not found:
                outside_keys.update(match[1].strip() for match in matches)
                continue
            for match in matches:
                key = match[1].strip()
                prototype = deepcopy(globals_by_key.get(key, {'label':key,'value_key':key,'required':True,'input_required':False,'confidence':1.0}))
                xml_path = root.getroottree().getpath(paragraph)
                prototype.update({'id':f'repeat_{mode}:{field_part}:{xml_path}#slot:{match.start()}',
                                  'kind':f'{mode}_repeat_placeholder','xml_path':xml_path,'anchor_text':text,
                                  'placeholder_start':match.start(),'placeholder_end':match.end(),'placeholder_key':key,'required':True})
                inside.append((prototype, found))
    for cell, found in cells.items():
        if any(position(node) == found for node in targets) or any(location == found for _, location in inside):
            continue
        if mode == 'docx' and cell.xpath('.//w:sdt | .//w:fldChar', namespaces={'w':WORD_NS}):
            profile.setdefault('warnings', []).append(f'반복 {found[0]}행 {found[1]}열: 이름 없는 구조화 입력칸은 수동 확인 필요')
            continue
        if ''.join(cell.xpath('.//*[local-name()="t"]/text()')).strip():
            continue
        xml_path = roots[part].getroottree().getpath(cell)
        label = f'빈 표 셀 {found[1]}'
        for header in reversed(rows[:plan['row'] - 1]):
            columns = header.xpath('./*[local-name()="tc"]')
            marked = bool(header.xpath('./w:trPr/w:tblHeader', namespaces={'w':WORD_NS})) if mode == 'docx' else any(column.get('header') == '1' for column in columns)
            if marked and len(columns) >= found[1]:
                observed = '\n'.join(''.join(segment.value for segment in _segments(paragraph, mode)) for paragraph in columns[found[1] - 1].xpath('.//*[local-name()="p"]')).strip()
                if observed and not PLACEHOLDER.search(observed):
                    label = observed
                    break
        inside.append(({'id':f'{mode}:{part}:{xml_path}','label':label,'value_key':label,'kind':f'{mode}_cell',
                        'required':False,'input_required':False,'confidence':0.1}, found))
    outside.extend(field for key, field in globals_by_key.items() if key in outside_keys)
    changed_keys = {field['value_key'] for field, _ in inside}
    constraints = profile.setdefault('constraints', {})
    dependencies = set()
    for relation in constraints.get('relations', []):
        for name in ('total','left','right','start','end','year','month','day'):
            if name in relation:
                dependencies.add(relation[name])
        dependencies.update(relation.get('parts', []))
    for group in constraints.get('groups', []):
        dependencies.update(group.get('fields', []))
    if changed_keys & dependencies:
        raise TemplateError('반복 행의 기존 관계·그룹을 행별로 안전하게 재매핑할 수 없음')
    used = {field['value_key'] for field in outside}
    repeated, summaries = [], []
    for relative in range(1, plan['count'] + 1):
        row_fields = []
        for field, (row, column) in inside:
            if row != relative:
                continue
            old_key = field['value_key']
            label = field['label']
            key = _repeat_key(old_key, row, column, used)
            field.update({'value_key':key,'suggested_mapping':key,'repeat_info':{'row':row,'column':column,'source_label':label}})
            row_fields.append(field)
            repeated.append(field)
        required = [field['value_key'] for field in row_fields if field.get('required') is True]
        members = [field['value_key'] for field in row_fields]
        if required:
            constraints.setdefault('groups', []).append({'kind':'all_or_none','fields':members,'required_fields':required})
        elif members:
            profile.setdefault('warnings', []).append(f'반복 {relative}행: 원본 필수 항목 정의 없음; 행 필수 조건은 사용자 확인 필요')
        summaries.append({'row':relative,'fields':members,'required_fields':required,'required_definition_pending':bool(members and not required)})
    profile['fields'] = outside + repeated
    if not repeated:
        raise TemplateError('반복 행에서 실제 입력칸을 찾지 못함')
    profile['source_sha256'] = binding['prepared_sha256']
    profile['repeat_expansion'] = deepcopy(binding)
    profile['repeat_rows'] = summaries
    try:
        validate_rule_profile(profile)
    except ValueError as exc:
        raise TemplateError('반복 입력 제약을 유지할 수 없음: ' + str(exc)) from exc
    return profile
