"""확인된 원본 입력 위치와 규칙을 반복 프로파일에 승계함."""

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
from zipfile import ZipFile

from lxml import etree

from parsers.extract import _check_zip, _xml
from .docx_choices import native_choice
from .fill import WORD_NS, TemplateError, _segments
from .repeat_fields import _matches, _repeat_key
from .value_rules import validate_rule_profile


RULES = ('validation','required','input_required','input_mode','max_chars','narrative_style_required',
         'report_style_required','style_required','confidence')
META = ('domain','ra_workflow','business_workflow','office_workflow','document_kind','citation_mode',
        'schema_version','resource_kind','profile_id','form_version','version_status','checked_at',
        'source_url','source_page','source_filename','demo_notice')
NS = {'w':WORD_NS}


def _package(data, mode):
    with ZipFile(BytesIO(data)) as archive:
        _check_zip(archive)
        parts = {name:archive.read(name) for name in archive.namelist()}
    names = [name for name in parts if name.startswith('word/') and name.endswith('.xml')] if mode == 'docx' else [name for name in parts if re.fullmatch(r'Contents/(section|masterpage)\d+\.xml', name)]
    return parts, {name:_xml(parts[name]) for name in names}


def _locations(roots):
    return {part:{root.getroottree().getpath(node):node for node in root.iter() if isinstance(node.tag,str)} for part,root in roots.items()}


def _without_values_and_paths(profile):
    from agent.brief import model_profile
    def clean(value, choice_item=False):
        if isinstance(value,dict):
            return {key:([clean(option,True) for option in item] if key=='choice_items' and isinstance(item,list) else clean(item))
                    for key,item in value.items() if not (key=='path' or (key.endswith('_path') and key!='xml_path') or key.endswith('_dir')
                    or key in {'directory','demo_values','verification','values','answers','user_values','field_values','draft','locked_fields','sources','documents','default','default_value','answer','user_value','actual_value'}
                    or (key=='value' and not choice_item))}
        if isinstance(value,list):
            return [clean(item) for item in value]
        return value
    return clean(model_profile(profile))


def _check_location(node, field, mode):
    if field.get('readonly') or field.get('read_only') or field.get('editable') is False:
        raise TemplateError('읽기 전용 원본 입력 위치를 쓰기 위치로 승계할 수 없음')
    if mode == 'docx':
        expected={'docx_cell':'tc','docx_paragraph':'p','docx_inline':'p','docx_append':'tc','docx_sdt':'sdtContent',
                  'docx_choice':'sdtContent','docx_combobox':'sdtContent','docx_checkbox':'sdtContent','docx_legacy_text':'fldChar'}
        if field['kind'] not in expected or etree.QName(node).localname!=expected[field['kind']]:
            raise TemplateError('선언한 원본 입력 종류가 실제 XML 위치와 다름')
        if field['kind'] not in {'docx_sdt','docx_choice','docx_combobox','docx_checkbox'} and node.xpath('.//w:sdt',namespaces=NS):
            raise TemplateError('native 구조화 입력을 주변 일반 입력으로 위장할 수 없음')
        chain = [node,*node.iterancestors()]
        controls = [item for item in chain if item.tag == f'{{{WORD_NS}}}sdt']
        if any(item.xpath('./w:sdtPr/w:lock | ./w:sdtPr/w:dataBinding', namespaces=NS) for item in controls):
            raise TemplateError('잠금·XML 연결 원본 입력은 승계할 수 없음')
        if controls:
            control = controls[0]
            props = control.find(f'{{{WORD_NS}}}sdtPr')
            content = control.find(f'{{{WORD_NS}}}sdtContent')
            if props is None or content is None:
                raise TemplateError('원본 구조화 입력의 속성·내용 정의가 없음')
            if node is not content:
                raise TemplateError('원본 구조화 입력 컨트롤의 위치를 바꿀 수 없음')
            if props.xpath('./w:date | ./w:picture | ./w:group | ./*[local-name()="repeatingSection" or local-name()="repeatingSectionItem"]',namespaces=NS):
                raise TemplateError('미지원 원본 구조화 입력을 일반 텍스트로 바꿀 수 없음')
            if props.xpath('./w:dropDownList | ./w:comboBox', namespaces=NS):
                metadata = native_choice(props,content)
                if field['kind'] != metadata['kind'] or any(key in field and field[key] != metadata[key] for key in ('control_type','options','choice_items','allow_custom')):
                    raise TemplateError('원본 native 선택 종류·옵션을 승계로 바꿀 수 없음')
                if ''.join(content.xpath('.//w:t/text()',namespaces=NS)).strip() and props.find(f'{{{WORD_NS}}}showingPlcHdr') is None:
                    raise TemplateError('원본의 이미 선택한 입력은 승계할 수 없음')
                if field.get('input_required') is False or field.get('input_mode','user_provided') != 'user_provided':
                    raise TemplateError('원본 native 선택은 사용자 직접 입력이어야 함')
            elif props.xpath('./*[local-name()="checkbox"]'):
                if field['kind'] != 'docx_checkbox' or field.get('input_required') is False or field.get('input_mode','user_provided') != 'user_provided':
                    raise TemplateError('원본 체크박스 종류·직접 입력을 바꿀 수 없음')
                state=props.xpath('./*[local-name()="checkbox"]/*[local-name()="checked"]/@*[local-name()="val"]')
                if len(state)!=1 or state[0] not in {'0','false','off'}:
                    raise TemplateError('이미 선택했거나 미확인인 원본 체크박스는 승계할 수 없음')
            elif field['kind'] != 'docx_sdt':
                raise TemplateError('원본 구조화 입력 종류를 바꿀 수 없음')
            elif ''.join(content.xpath('.//w:t/text()',namespaces=NS)).strip() and props.find(f'{{{WORD_NS}}}showingPlcHdr') is None:
                raise TemplateError('원본의 기존 구조화 입력값을 승계할 수 없음')
        elif field['kind'] in {'docx_choice','docx_combobox','docx_checkbox','docx_sdt'}:
            raise TemplateError('원본에 없는 native 입력 종류임')
    elif node.get('protect')=='1' or any(item.get('protect')=='1' for item in node.iterancestors()):
        raise TemplateError('보호된 HWPX 원본 입력을 승계할 수 없음')
    elif field['kind'] not in {'hwpx_cell','hwpx_paragraph'} or etree.QName(node).localname != ('tc' if field['kind']=='hwpx_cell' else 'p'):
        raise TemplateError('선언한 HWPX 입력 종류가 실제 XML 위치와 다름')
    if field['kind']=='docx_append':
        if ''.join(node.xpath('.//w:t/text()',namespaces=NS))!=field.get('anchor_text'):
            raise TemplateError('추가 입력 위치의 원본 안내 anchor가 다름')
    if field['kind']=='docx_inline':
        from .compatibility import _blank_marker
        text=''.join(segment.value for segment in _segments(node,mode))
        start,end=field.get('blank_start'),field.get('blank_end')
        if (text!=field.get('anchor_text') or type(start) is not int or type(end) is not int
                or not 0<=start<end<=len(text) or not _blank_marker(text[start:end])):
            raise TemplateError('명시한 원본 줄 입력 위치의 anchor가 다름')
    if field['kind'] in {'docx_cell','hwpx_cell','docx_paragraph','hwpx_paragraph'}:
        from .compatibility import _blank_marker
        text=''.join(node.xpath('.//*[local-name()="t"]/text()'))
        if text.strip() and not (mode=='docx' and _blank_marker(text)):
            raise TemplateError('기존 고정 문구 또는 입력값이 있는 원본 위치임')


def inherit_repeat_rules(original_path, prepared_profile, original_profile, *, prepared_path=None):
    """원본이 없을 때는 prepared SHA와 확인 프로파일의 원본 SHA 연결로 검사함."""
    if prepared_profile.get('format') == 'xlsx':
        from templates.repeat_xlsx_profile import inherit_xlsx_repeat_rules
        return inherit_xlsx_repeat_rules(original_path, prepared_profile, original_profile, prepared_path=prepared_path)
    if original_profile.get('repeat_expansion'):
        raise TemplateError('확장된 양식을 다시 확장하는 규칙 승계는 이번 단일 계획에서 지원하지 않음')
    result = deepcopy(prepared_profile)
    binding = result.get('repeat_expansion',{})
    if (set(binding) != {'original_sha256','prepared_sha256','plan'} or any(not isinstance(binding.get(key),str) or not re.fullmatch('[0-9a-f]{64}',binding[key]) for key in ('original_sha256','prepared_sha256')) or original_profile.get('source_sha256') != binding.get('original_sha256')
            or result.get('source_sha256') != binding.get('prepared_sha256')):
        raise TemplateError('반복 승계의 원본/준비 프로파일 SHA 바인딩이 다름')
    validate_rule_profile(original_profile)
    mode = result.get('format')
    if mode not in {'docx','hwpx'} or original_profile.get('format') != mode:
        raise TemplateError('반복 승계의 원본/준비 형식이 다름')
    plan = binding['plan']
    if (not isinstance(plan,dict) or set(plan) != {'table_id','row','count'} or type(plan['row']) is not int or type(plan['count']) is not int or plan['row']<1 or not 1<=plan['count']<=200):
        raise TemplateError('반복 승계의 행 확장 계획이 유효하지 않음')
    source_parts = source_roots = None
    if original_path is not None:
        raw = Path(original_path).read_bytes()
        if sha256(raw).hexdigest() != binding['original_sha256']:
            raise TemplateError('실제 원본 파일 SHA가 반복 승계 기록과 다름')
        source_parts,source_roots = _package(raw,mode)
    prepared = prepared_path or result.get('source_path')
    if prepared is not None:
        raw = Path(prepared).read_bytes()
        if sha256(raw).hexdigest() != binding['prepared_sha256']:
            raise TemplateError('실제 준비 파일 SHA가 반복 승계 기록과 다름')
        _,roots = _package(raw,mode)
    elif source_parts is not None:
        from .repeat_docx import transform as docx_transform
        from .repeat_hwpx import transform as hwpx_transform
        changes = (docx_transform if mode=='docx' else hwpx_transform)(source_parts,plan)
        roots = {name:_xml(changes.get(name,data)) for name,data in source_parts.items() if name in source_roots}
    else:
        raise TemplateError('원본 없는 승계에는 준비 파일 경로가 필요함')
    locations = _locations(roots)
    try:
        prefix,part,table_path = plan['table_id'].split(':',2)
        table = locations[part][table_path]
    except (ValueError,KeyError) as exc:
        raise TemplateError('준비 XML에서 반복 대상 표를 찾지 못함') from exc
    if prefix != mode or etree.QName(table).localname != 'tbl':
        raise TemplateError('반복 대상 표의 원본 종류가 다름')
    target_rows = table.xpath('./*[local-name()="tr"]')
    start,delta = plan['row']-1,plan['count']-1
    if start+plan['count']>len(target_rows):
        raise TemplateError('준비 양식의 반복 행 구간이 부족함')
    if source_roots is None:
        # Original absence is explicit: back-project positions only; the caller
        # still verifies expansion against the actual original at full export.
        source_roots = deepcopy(roots)
        source_table = _locations(source_roots)[part][table_path]
        for row in source_table.xpath('./*[local-name()="tr"]')[start+1:start+1+delta]:
            source_table.remove(row)
    source_locations = _locations(source_roots)
    try:
        source_table = source_locations[part][table_path]
    except KeyError as exc:
        raise TemplateError('원본 XML에서 반복 대상 표를 찾지 못함') from exc
    source_rows = source_table.xpath('./*[local-name()="tr"]')
    if start>=len(source_rows) or len(source_rows)+delta != len(target_rows):
        raise TemplateError('원본과 준비 양식의 실제 행 수가 다름')
    def mapped_nodes(node, source_part):
        owner = next((item for item in [node,*node.iterancestors()] if item in source_rows),None) if source_part==part else None
        if owner is None:
            path = source_roots[source_part].getroottree().getpath(node)
            target = locations.get(source_part,{}).get(path)
            if target is None:
                raise TemplateError('바깥 원본 입력 위치가 준비 양식에서 사라짐')
            return [(0,target)]
        index = source_rows.index(owner)
        route,cursor = [],node
        while cursor is not owner:
            parent = cursor.getparent()
            route.insert(0,parent.index(cursor))
            cursor = parent
        destinations = [(i+1,target_rows[start+i]) for i in range(plan['count'])] if index==start else [(0,target_rows[index+delta if index>start else index])]
        output=[]
        for relative,target in destinations:
            try:
                for step in route: target=target[step]
            except IndexError as exc:
                raise TemplateError('승계 대상 행의 원본 입력 구조가 다름') from exc
            if target.tag != node.tag:
                raise TemplateError('승계 대상 입력의 XML 종류가 다름')
            output.append((relative,target))
        return output
    available={field['id']:field for field in result.get('fields',[])}
    if len(available)!=len(result.get('fields',[])):
        raise TemplateError('준비 프로파일 입력 ID가 중복됨')
    if len({field['id'] for field in original_profile.get('fields',[])})!=len(original_profile.get('fields',[])):
        raise TemplateError('원본 확인 입력 ID가 중복됨')
    assignments={}
    replaced=set()
    source_policy=_without_values_and_paths(original_profile)
    for original in source_policy.get('fields',[]):
        if original.get('readonly') or original.get('read_only') or original.get('editable') is False:
            raise TemplateError('읽기 전용 원본 입력 위치를 쓰기 위치로 승계할 수 없음')
        found=[]
        if original['kind']=='placeholder':
            token=original['id'].split(':',1)[1]
            for source_part,root in source_roots.items():
                for paragraph in root.xpath('.//*[local-name()="p"]'):
                    text,matches=_matches(paragraph,mode,set())
                    for match in matches:
                        if match[1].strip()!=token: continue
                        for relative,target in mapped_nodes(paragraph,source_part):
                            path=roots[source_part].getroottree().getpath(target)
                            identifier=f'repeat_{mode}:{source_part}:{path}#slot:{match.start()}' if relative else original['id']
                            field=available.get(identifier)
                            if field is None or (relative and (field.get('anchor_text')!=text or field.get('placeholder_key')!=token)):
                                raise TemplateError('원본 자리표시자의 동일 입력 위치를 승계할 수 없음')
                            found.append((relative,field))
        else:
            try:
                field_mode,source_part,path=original['id'].split(':',2)
                node=source_locations[source_part][path]
            except (ValueError,KeyError) as exc:
                raise TemplateError('확인한 원본 입력 위치가 실제 XML에 없음') from exc
            if field_mode!=mode:
                raise TemplateError('원본 입력 ID의 형식이 다름')
            _check_location(node,original,mode)
            for relative,target in mapped_nodes(node,source_part):
                path=roots[source_part].getroottree().getpath(target)
                identifier=f'{mode}:{source_part}:{path}'
                field=available.get(identifier)
                if field is None or field['kind']!=original['kind']:
                    # Approved exact blank/append/inline locations outrank a raw
                    # adjacency heuristic. Native guards above remain source-led.
                    field=deepcopy(original)
                    field['id']=identifier
                    if relative:
                        cell=next((item for item in [target,*target.iterancestors()] if etree.QName(item).localname=='tc'),None)
                        cells=target_rows[start+relative-1].xpath('./*[local-name()="tc"]')
                        if cell not in cells:
                            raise TemplateError('명시 입력칸이 반복 행의 직접 셀에 속하지 않음')
                        field['repeat_info']={'row':relative,'column':cells.index(cell)+1,'source_label':original['label']}
                    for raw_id,raw_field in list(available.items()):
                        if raw_field['kind']=='placeholder' or not raw_id.startswith(mode+':'+source_part+':'): continue
                        raw_node=locations[source_part].get(raw_id.split(':',2)[2])
                        if raw_node is not None and (raw_node is target or raw_node in target.iterancestors() or target in raw_node.iterancestors()):
                            if raw_id in assignments:
                                raise TemplateError('확인한 원본 입력 범위들이 서로 겹침')
                            replaced.add(raw_id)
                    available[identifier]=field
                    result['fields'].append(field)
                _check_location(target,field,mode)
                for key in ('control_type','options','choice_items','allow_custom'):
                    if key in original and original[key]!=field.get(key):
                        raise TemplateError('native 원본 옵션 또는 편집 범위를 승계로 바꿀 수 없음')
                found.append((relative,field))
        if not found:
            raise TemplateError('원본 확인 입력칸의 승계 대상을 찾지 못함')
        for relative,field in found:
            existing=assignments.get(field['id'])
            if existing and existing[0]!=original:
                raise TemplateError('같은 준비 입력칸에 서로 다른 원본 규칙이 겹침')
            assignments[field['id']]=(original,relative)
    fields=list({field['id']:field for field in result['fields'] if (field.get('repeat_info') or field['id'] in assignments)
                 and (field['id'] not in replaced or field['id'] in assignments)}.values())
    used={assignments[field['id']][0]['value_key'] for field in fields if field['id'] in assignments and not field.get('repeat_info')}
    used.update(field['value_key'] for field in fields if field['id'] not in assignments)
    destinations={}
    for field in fields:
        assignment=assignments.get(field['id'])
        if assignment is None: continue
        original,relative=assignment
        for key in RULES:
            if key in original:
                field[key]=deepcopy(original[key])
        if field['kind'].endswith('_repeat_placeholder') and field.get('required') is False:
            raise TemplateError('원본 자리표시자의 필수 조건을 완화할 수 없음')
        field['label']=original['label']
        if relative:
            field['repeat_info']['source_label']=original['label']
            field['value_key']=_repeat_key(original['value_key'],relative,field['repeat_info']['column'],used)
        else:
            field['value_key']=original['value_key']
        field['suggested_mapping']=field['value_key']
        destinations.setdefault(original['value_key'],{}).setdefault(relative,set()).add(field['value_key'])
    def rewrite(item,keys):
        mapping={}
        scopes=set()
        for key in keys:
            places=destinations.get(key,{})
            if not places or any(len(values)!=1 for values in places.values()):
                raise TemplateError('원본 관계의 동일 값을 모호하게 나눌 수 없음')
            classification='outside' if set(places)=={0} else 'repeat' if set(places)==set(range(1,plan['count']+1)) else 'mixed'
            scopes.add(classification)
            mapping[key]=places
        if scopes=={'outside'}: rows=[0]
        elif scopes=={'repeat'}: rows=list(range(1,plan['count']+1))
        else: raise TemplateError('반복행·바깥 또는 복수 원본 행을 연결하는 관계는 승계 미지원')
        output=[]
        for row in rows:
            renamed={key:next(iter(mapping[key][row])) for key in keys}
            copy=deepcopy(item)
            for name in ('total','left','right','start','end','year','month','day'):
                if name in copy: copy[name]=renamed[copy[name]]
            for name in ('parts','fields','required_fields'):
                if name in copy: copy[name]=[renamed[key] for key in copy[name]]
            output.append(copy)
        return output
    constraints={'relations':[],'groups':[]}
    for item in original_profile.get('constraints',{}).get('relations',[]):
        keys=[item[name] for name in ('total','left','right','start','end','year','month','day') if name in item]+item.get('parts',[])
        constraints['relations'].extend(rewrite(item,keys))
    for item in original_profile.get('constraints',{}).get('groups',[]):
        constraints['groups'].extend(rewrite(item,item['fields']))
    summaries=[]
    for relative in range(1,plan['count']+1):
        members=[field['value_key'] for field in fields if field.get('repeat_info',{}).get('row')==relative]
        required=[field['value_key'] for field in fields if field.get('repeat_info',{}).get('row')==relative and field.get('required') is True]
        if required and not any(set(group['fields'])==set(members) for group in constraints['groups']):
            constraints['groups'].append({'kind':'all_or_none','fields':members,'required_fields':required})
        summaries.append({'row':relative,'fields':members,'required_fields':required,'required_definition_pending':bool(members and not required)})
    result.update(fields=fields,constraints=constraints,repeat_rows=summaries)
    for key in META:
        if key in original_profile: result[key]=deepcopy(original_profile[key])
    result['repeat_source_profile']=source_policy
    result['repeat_rule_verification']={'original_file_checked':original_path is not None,'prepared_file_checked':prepared is not None}
    unresolved=[field['id'] for field in fields if field.get('repeat_info') and field['id'] not in assignments]
    if unresolved:
        result['repeat_unconfirmed_fields']=unresolved
        result.setdefault('warnings',[]).append(f'반복행 신규 입력칸 {len(unresolved)}개는 원본 확인 매핑에 없음; 사용자 매핑 확인 필요')
    validate_rule_profile(result)
    return result
