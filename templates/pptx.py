"""PPTX의 기존 텍스트 위치만 수정하며 ZIP의 나머지 부품을 보존함."""

from copy import deepcopy
from zipfile import ZipFile

from lxml import etree

from parsers.extract import _check_zip, _xml
from parsers.pptx import A, P, NS, _slide_parts, _body_text
from .fill import PLACEHOLDER, TemplateError, XML_SPACE


def _locked(node):
    return bool(node.xpath('.//a:spLocks[@noTextEdit="1" or @noTextEdit="true"]', namespaces=NS))


def pptx_fields(archive, warnings):
    from .compatibility import _field
    presentation = _xml(archive.read('ppt/presentation.xml'))
    if presentation.find(f'{{{P}}}modifyVerifier') is not None:
        warnings.append('편집 보호가 설정된 PPTX임. 보호 해제한 사본이 필요함')
        return []
    fields, placeholders = [], set()
    for page, part in enumerate(_slide_parts(archive), 1):
        root = _xml(archive.read(part))
        tree = root.getroottree()
        for shape in root.xpath('.//p:sp | .//p:graphicFrame', namespaces=NS):
            if _locked(shape):
                warnings.append(f'슬라이드 {page}: 텍스트 편집이 잠긴 도형은 기입에서 제외함')
                continue
            bodies = shape.xpath('./p:txBody | .//a:tc/a:txBody', namespaces=NS)
            if not bodies:
                warnings.append(f'슬라이드 {page}: 차트·SmartArt·그림은 원본 보존하며 직접 기입할 수 없음')
            for body in bodies:
                for paragraph in body.findall(f'{{{A}}}p'):
                    text = ''.join(node.text or '' for node in paragraph.iter(f'{{{A}}}t'))
                    for match in PLACEHOLDER.finditer(text):
                        key = match[1].strip()
                        if key not in placeholders:
                            fields.append(_field('placeholder:'+key, key, 'placeholder', required=True, confidence=1))
                            placeholders.add(key)
                if _body_text(body).strip():
                    continue
                cell = body.getparent()
                if cell.tag == f'{{{A}}}tc':
                    if cell.get('hMerge') in {'1','true'} or cell.get('vMerge') in {'1','true'}:
                        continue
                    row = cell.getparent()
                    cells = row.findall(f'{{{A}}}tc')
                    index = cells.index(cell)
                    label = _body_text(cells[index-1].find(f'{{{A}}}txBody')).strip() if index else ''
                    kind = 'pptx_cell'
                else:
                    identity = shape.find('.//' + f'{{{P}}}cNvPr')
                    label = identity.get('name','') if identity is not None else ''
                    kind = 'pptx_text'
                field = _field(f'pptx:{part}:{tree.getpath(body)}', label or f'빈 입력칸 {len(fields)+1}', kind)
                field['slide'] = page
                fields.append(field)
    warnings.append('슬라이드 크기·글꼴·표·테마·그림은 보존함. 기입 분량에 따른 넘침은 PowerPoint에서 확인해야 함')
    return fields


def _set_text(node, text):
    run = node.getparent()
    if run.tag != f'{{{A}}}r':
        raise TemplateError('PPTX 자동 날짜·필드 텍스트는 수정할 수 없음')
    lines = text.replace('\r\n','\n').replace('\r','\n').split('\n')
    node.text = lines[0]
    node.set(XML_SPACE,'preserve')
    paragraph = run.getparent()
    position = paragraph.index(run)+1
    properties = run.find(f'{{{A}}}rPr')
    for line in lines[1:]:
        br = etree.Element(f'{{{A}}}br')
        if properties is not None:
            br.append(deepcopy(properties))
        new_run = etree.Element(f'{{{A}}}r')
        if properties is not None:
            new_run.append(deepcopy(properties))
        new_text = etree.SubElement(new_run,f'{{{A}}}t')
        new_text.text = line
        new_text.set(XML_SPACE,'preserve')
        paragraph.insert(position,br)
        paragraph.insert(position+1,new_run)
        position += 2


def _replace(paragraph, values):
    segments = list(paragraph.xpath('./a:r/a:t | ./a:fld/a:t | ./a:br', namespaces=NS))
    texts = ['\n' if node.tag == f'{{{A}}}br' else node.text or '' for node in segments]
    text = ''.join(texts)
    starts, offset = [], 0
    for value in texts:
        starts.append(offset)
        offset += len(value)
    for match in reversed(list(PLACEHOLDER.finditer(text))):
        key = match[1].strip()
        if key not in values or not values[key].strip():
            raise TemplateError('PPTX 자리표시자 값이 누락됨: '+key)
        affected = [index for index,value in enumerate(texts) if starts[index] < match.end() and starts[index]+len(value) > match.start()]
        if any(segments[index].getparent().tag != f'{{{A}}}r' for index in affected):
            raise TemplateError('PPTX 자리표시자가 줄 나눔·자동 필드를 가로지름')
        first,last = affected[0],affected[-1]
        prefix = (segments[first].text or '')[:match.start()-starts[first]]
        suffix = (segments[last].text or '')[match.end()-starts[last]:]
        _set_text(segments[first], prefix+values[key]+(suffix if first==last else ''))
        if first != last:
            for index in affected[1:-1]:
                _set_text(segments[index],'')
            _set_text(segments[last],suffix)


def _insert_blank(body, value):
    if _body_text(body).strip():
        raise TemplateError('PPTX의 기존 본문은 빈 입력칸으로 덮어쓸 수 없음')
    paragraph = body.find(f'{{{A}}}p')
    if paragraph is None:
        paragraph = etree.SubElement(body,f'{{{A}}}p')
    run = paragraph.find(f'{{{A}}}r')
    if run is None:
        run = etree.Element(f'{{{A}}}r')
        end = paragraph.find(f'{{{A}}}endParaRPr')
        if end is not None:
            properties = deepcopy(end)
            properties.tag = f'{{{A}}}rPr'
            run.append(properties)
        paragraph.insert(len(paragraph)-int(end is not None),run)
    node = run.find(f'{{{A}}}t')
    if node is None:
        node = etree.SubElement(run,f'{{{A}}}t')
    _set_text(node,value)


def fill_pptx_selected(source, target, selected):
    from .compatibility import _rewrite_zip
    changes = {}
    with ZipFile(source) as archive:
        _check_zip(archive)
        # 재분석하여 편집 보호/임의 프로파일의 비어 있지 않은 위치를 우회하지 않음.
        fields = pptx_fields(archive,[])
        allowed = {field['id']: field for field in fields}
        if any(field['id'] not in allowed or field['kind'] != allowed[field['id']]['kind'] for field,_ in selected):
            raise TemplateError('PPTX 원본에서 기입할 수 없는 입력칸임')
        values = {allowed[field['id']]['label']: value for field,value in selected if field['kind']=='placeholder'}
        for part in _slide_parts(archive):
            root = _xml(archive.read(part))
            before = etree.tostring(root)
            # 원문 매치만 먼저 처리하여 빈 셀에 넣는 {{...}}도 문자 그대로 유지함.
            if values:
                for shape in root.xpath('.//p:sp | .//p:graphicFrame', namespaces=NS):
                    if not _locked(shape):
                        for body in shape.xpath('./p:txBody | .//a:tc/a:txBody',namespaces=NS):
                            for paragraph in body.findall(f'{{{A}}}p'):
                                _replace(paragraph,values)
            for field,value in selected:
                if field['kind'] == 'placeholder':
                    continue
                _,field_part,xpath = field['id'].split(':',2)
                if field_part == part:
                    nodes = root.xpath(xpath,namespaces=NS)
                    if len(nodes) != 1:
                        raise TemplateError('PPTX 기입 위치가 원본과 일치하지 않음')
                    _insert_blank(nodes[0],value)
            if etree.tostring(root) != before:
                changes[part] = etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    _rewrite_zip(source,target,changes)
