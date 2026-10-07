"""PPTX 슬라이드·도형·표를 읽음. 차트/SmartArt 픽셀은 OCR로 추정하지 않음."""

import posixpath
from pathlib import Path
import warnings
from zipfile import ZipFile

from .extract import ParseError, _path, _check_zip, _xml, _block, _table, _result

A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS = {'a': A, 'p': P, 'r': R}


def _slide_parts(archive):
    root = _xml(archive.read('ppt/presentation.xml'))
    relations = _xml(archive.read('ppt/_rels/presentation.xml.rels'))
    targets = {item.get('Id'): item for item in relations}
    parts = []
    for slide in root.findall(f'{{{P}}}sldIdLst/{{{P}}}sldId'):
        relation = targets.get(slide.get(f'{{{R}}}id'))
        if relation is None or relation.get('TargetMode') == 'External':
            raise ParseError('PPTX 슬라이드 관계가 잘못됨')
        target = relation.get('Target', '')
        part = target.lstrip('/') if target.startswith('/') else posixpath.normpath('ppt/' + target)
        if not part.startswith('ppt/slides/') or part not in archive.namelist():
            raise ParseError('PPTX 슬라이드 경로가 잘못됨')
        parts.append(part)
    if not parts:
        raise ParseError('PPTX에 슬라이드가 없음', 'empty_document')
    return parts


def _body_text(body):
    return '\n'.join(''.join('\n' if node.tag == f'{{{A}}}br' else node.text or ''
                            for node in paragraph.iter() if node.tag in {f'{{{A}}}t', f'{{{A}}}br'})
                     for paragraph in body.findall(f'{{{A}}}p'))


def parse_pptx(path: str | Path) -> dict:
    source = _path(path)
    blocks, tables = [], []
    try:
        with ZipFile(source) as archive:
            _check_zip(archive)
            for page, part in enumerate(_slide_parts(archive), 1):
                root = _xml(archive.read(part))
                for shape in root.xpath('.//p:sp | .//p:graphicFrame', namespaces=NS):
                    identity = shape.find('.//' + f'{{{P}}}cNvPr')
                    shape_id = identity.get('id', '') if identity is not None else ''
                    name = identity.get('name', '') if identity is not None else ''
                    location = f'슬라이드 {page}/도형 {shape_id} {name}'.strip()
                    shape_tables = []
                    table_node = shape.find('.//' + f'{{{A}}}tbl')
                    if table_node is not None:
                        rows = [[_body_text(cell.find(f'{{{A}}}txBody')) for cell in row.findall(f'{{{A}}}tc')]
                                for row in table_node.findall(f'{{{A}}}tr')]
                        table = _table(rows, location + '/표', page=page)
                        tables.append(table)
                        shape_tables.append(table)
                        text = '\n'.join('\t'.join(row) for row in rows)
                    else:
                        body = shape.find(f'{{{P}}}txBody')
                        if body is None:
                            warnings.warn(f'{source.name} {location}: 차트·SmartArt·그림의 시각 내용은 별도 확인 필요함', UserWarning, stacklevel=2)
                            continue
                        text = _body_text(body)
                    block = _block(text, location, page=page, tables=shape_tables)
                    block.update({'슬라이드': page, '도형 ID': shape_id, '도형명': name})
                    transform = shape.find('.//' + f'{{{A}}}xfrm')
                    if transform is not None:
                        block['도형 좌표 EMU'] = {key: value for child in transform for key, value in child.attrib.items()}
                    blocks.append(block)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f'PPTX를 읽을 수 없음: {source.name}') from exc
    return _result(source, blocks, tables)
