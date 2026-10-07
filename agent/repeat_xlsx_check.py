"""Source-bound XLSX row expansion inspection, independent of the transformer.

Only the approved A1 insertion/copy rules are accepted.  A passed XML inspection
does not assert that Excel has recalculated formulas or rendered the workbook.
"""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
from zipfile import ZipFile

from lxml import etree
from openpyxl.formula.tokenizer import Tokenizer

from parsers.extract import _check_zip, _xml


S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P = 'http://schemas.openxmlformats.org/package/2006/relationships'
C = 'http://schemas.openxmlformats.org/drawingml/2006/chart'
XDR = 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing'
X14 = 'http://schemas.microsoft.com/office/spreadsheetml/2009/9/main'
XM = 'http://schemas.microsoft.com/office/excel/2006/main'
NS = {'s': S, 'r': R, 'c': C, 'xdr': XDR, 'x14': X14, 'xm': XM}
MAX_BYTES = 64 * 1024 * 1024
MAX_ROW = 1048576
CELL = re.compile(r'(\$?)([A-Za-z]{1,3})(\$?)([1-9][0-9]{0,6})')
ROW = re.compile(r'(\$?)([1-9][0-9]{0,6})')
COL = re.compile(r'(\$?)([A-Za-z]{1,3})')
TOKEN = re.compile(r'\{\{\s*[^{}]+\s*\}\}')
TOTAL = re.compile(r'^(?:합계|총계|소계|total|subtotal)(?:\s|[:：]|$)', re.I)
UNSAFE_FUNCTIONS = {'INDIRECT', 'OFFSET', 'FILTER', 'UNIQUE', 'SORT', 'SORTBY',
                    'SEQUENCE', 'RANDARRAY', 'LAMBDA', 'LET'}


def _require(condition, message):
    if not condition:
        raise ValueError('XLSX 반복행 독립 검수 실패: ' + message)


def _canonical(node):
    return etree.tostring(node, method='c14n', with_comments=True)


def _read(path):
    _require(path.is_file() and 0 < path.stat().st_size <= MAX_BYTES, '파일 크기 제한(64MiB) 또는 파일 존재 확인 실패')
    raw = path.read_bytes()
    _require(len(raw) <= MAX_BYTES, '파일 크기 제한 위반')
    try:
        with ZipFile(path) as archive:
            _check_zip(archive)
            infos = archive.infolist()
            _require(len(infos) <= 10000 and sum(item.file_size for item in infos) <= MAX_BYTES, 'ZIP 해제 크기·항목 수 제한 위반')
            for item in infos:
                for name in {item.filename, item.orig_filename}:
                    _require(name and '\\' not in name and not name.startswith('/')
                             and not re.search(r'[\x00-\x1f:]', name)
                             and '..' not in PurePosixPath(name).parts, '안전하지 않은 ZIP 경로')
                _require(not item.flag_bits & 1 and stat.S_IFMT(item.external_attr >> 16) != stat.S_IFLNK,
                         '암호·링크 ZIP 부품은 지원하지 않음')
            parts = {item.filename: archive.read(item) for item in infos}
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('XLSX 반복행 독립 검수 실패: ZIP을 읽을 수 없음') from exc
    _require({'[Content_Types].xml', 'xl/workbook.xml', 'xl/_rels/workbook.xml.rels'} <= parts.keys(), 'XLSX 통합문서 부품 누락')
    _require(not any(name.lower().startswith(('_xmlsignatures/', 'xl/externallinks/', 'xl/pivottables/',
                           'xl/pivotcache/', 'xl/querytables/')) or name == 'xl/metadata.xml' for name in parts),
             '전자 서명·외부·피벗·쿼리·동적 배열 원본은 확장 미지원')
    return raw, parts


def _relation_target(owner, target):
    _require(isinstance(target, str) and target and not re.search(r'[\\\x00-\x1f:?#]', target), '잘못된 부품 관계 경로')
    name = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(owner), target))
    _require(not name.startswith('../') and name not in {'.', '..'}, '패키지 밖의 관계')
    return name


def _rels(parts, owner):
    name = posixpath.join(posixpath.dirname(owner), '_rels', posixpath.basename(owner) + '.rels')
    result = {}
    if name in parts:
        root = _xml(parts[name])
        _require(root.tag == f'{{{P}}}Relationships', '관계 부품 형식 오류')
        for node in root:
            identity = node.get('Id')
            _require(identity and identity not in result, '중복·빈 관계 ID')
            if node.get('TargetMode') == 'External':
                result[identity] = None
            else:
                target = _relation_target(owner, node.get('Target'))
                _require(target in parts, '연결된 부품이 없음: ' + target)
                result[identity] = target
    return result


def _column(text):
    value = 0
    for character in text.upper():
        value = value * 26 + ord(character) - ord('A') + 1
    _require(1 <= value <= 16384, 'Excel 열 범위를 넘음')
    return value


def _endpoint(text):
    match = CELL.fullmatch(text)
    if match:
        _column(match[2])
        _require(int(match[4]) <= MAX_ROW, 'Excel 행 범위를 넘음')
        return ('cell', match[1] + match[2] + match[3], int(match[4]), bool(match[3]))
    match = ROW.fullmatch(text)
    if match:
        _require(int(match[2]) <= MAX_ROW, 'Excel 행 범위를 넘음')
        return ('row', match[1], int(match[2]), bool(match[1]))
    match = COL.fullmatch(text)
    if match:
        _column(match[2])
        return ('column', text, None, bool(match[1]))
    raise ValueError('XLSX 반복행 독립 검수 실패: 잘못된 A1 주소: ' + text)


def _address(text, cut, delta, *, insert=True, copy_offset=0, expand=True, single_expand=False):
    """Our own row arithmetic: insertion moves absolutes; copying does not."""
    pieces = text.split(':')
    _require(1 <= len(pieces) <= 2, '3D·모호한 참조 범위')
    endpoints = [_endpoint(piece) for piece in pieces]
    _require(len({point[0] for point in endpoints}) == 1, '서로 다른 주소 종류의 범위')
    if endpoints[0][0] == 'column':
        _require(len(endpoints) == 2, '단일 열 이름은 A1 주소가 아님')
        return text
    _require(len(endpoints) == 2 or endpoints[0][0] == 'cell', '단일 행 번호는 A1 주소가 아님')
    numbers = [point[2] for point in endpoints]
    _require(len(numbers) == 1 or numbers[0] <= numbers[1], '역순 행 범위는 미지원')
    moved = []
    for index, point in enumerate(endpoints):
        number = point[2] + (delta if insert and point[2] > cut else 0)
        # A rectangular/full-row range ending at Excel's last row remains capped.
        # This exception never permits a physical cell or singleton to overflow.
        if insert and len(endpoints) == 2 and index == 1 and point[2] == MAX_ROW:
            number = MAX_ROW
        if copy_offset and not point[3]:
            number += copy_offset
        _require(1 <= number <= MAX_ROW, '이동된 주소가 Excel 행 범위를 넘음')
        moved.append(number)
    if insert and expand and len(numbers) == 2 and numbers[0] <= cut == numbers[1]:
        moved[1] += delta
    if insert and single_expand and len(numbers) == 1 and numbers[0] == cut:
        endpoints.append(endpoints[0])
        moved.append(cut + delta)
    _require(all(1 <= number <= MAX_ROW for number in moved), '확장 범위가 Excel 최대 행을 넘음')
    return ':'.join(point[1] + str(number) for point, number in zip(endpoints, moved))


def _is_address(value):
    return bool(CELL.fullmatch(value) or ':' in value)


def _formula(text, context, selected, cut, delta, table_names, *, copy_offset=0):
    """Tokenizer supplies lexical boundaries only; no Translator is imported."""
    if not text:
        return text
    _require(isinstance(text, str) and len(text) <= 32767, '잘못된·과도한 수식 길이')
    leading = text.startswith('=')
    body = text[1:] if leading else text
    try:
        tokens = Tokenizer('=' + body).items
    except Exception as exc:
        raise ValueError('XLSX 반복행 독립 검수 실패: 수식 해석 실패') from exc
    output, cursor, depth = [], 0, 0
    for token in tokens:
        original = token.value
        if token.type == 'WHITE-SPACE':
            found = re.match(r'\s+', body[cursor:])
            _require(found is not None, '수식 공백 위치를 확인할 수 없음')
            original = found[0]
        _require(body.startswith(original, cursor), '수식 토큰과 원문 위치가 다름')
        cursor += len(original)
        value = original
        if token.type in {'FUNC', 'PAREN'}:
            depth += 1 if token.subtype == 'OPEN' else -1
            _require(depth >= 0, '수식 괄호 불균형')
        if token.type == 'FUNC' and token.subtype == 'OPEN':
            function = original[:-1].upper()
            _require(function not in UNSAFE_FUNCTIONS and not function.startswith(('_XLFN.', '_XLWS.')),
                     '동적·간접 참조 수식은 미지원')
        _require(not (token.type == 'OPERAND' and token.subtype == 'ERROR'), '오류 참조가 있는 원본 수식')
        if token.type == 'OPERAND' and token.subtype == 'RANGE':
            if '[' in value or ']' in value:
                _require('!' not in value and (value.startswith('[') and bool(table_names)
                         or any(value.casefold().startswith(name.casefold() + '[') for name in table_names)),
                         '외부·알 수 없는 구조화 참조')
            else:
                qualifier, separator, address = value.rpartition('!')
                if not separator:
                    qualifier, address = '', value
                sheet = qualifier[1:-1].replace("''", "'") if qualifier.startswith("'") and qualifier.endswith("'") else qualifier
                _require(':' not in sheet and '[' not in sheet, '3D·외부 시트 참조')
                if _is_address(address):
                    _require(qualifier or context is not None, '전역 비한정 A1 참조는 미지원')
                    affected = (sheet if qualifier else context).casefold() == selected.casefold()
                    address = _address(address, cut, delta, insert=affected and not copy_offset,
                                       copy_offset=copy_offset, expand=True)
                    value = (qualifier + '!' if qualifier else '') + address
        output.append(value)
    _require(cursor == len(body) and depth == 0, '수식 토큰 또는 괄호가 원문 전체를 보존하지 않음')
    return ('=' if leading else '') + ''.join(output)


def _cell_text(cell, strings):
    if cell.find(f'{{{S}}}f') is not None:
        return '=' + (cell.find(f'{{{S}}}f').text or '')
    if cell.get('t') == 'inlineStr':
        return ''.join(cell.xpath('./s:is//s:t/text()', namespaces=NS))
    value = cell.find(f'{{{S}}}v')
    if value is None:
        return ''
    if cell.get('t') == 's':
        _require(bool(re.fullmatch(r'[0-9]+', value.text or '')) and int(value.text) < len(strings), '잘못된 공유 문자열 ID')
        return strings[int(value.text)]
    return value.text or ''


def _rows(root):
    data = root.find(f'{{{S}}}sheetData')
    _require(data is not None and all(node.tag == f'{{{S}}}row' for node in data), 'sheetData 행 구조 오류')
    previous = 0
    for row in data:
        _require(bool(re.fullmatch(r'[1-9][0-9]*', row.get('r', ''))), '잘못된 원본 행 번호')
        number = int(row.get('r'))
        _require(previous < number <= MAX_ROW, '원본 행 중복·역순·범위 오류')
        previous, prior_column = number, 0
        for cell in row.findall(f'{{{S}}}c'):
            match = CELL.fullmatch(cell.get('r', ''))
            _require(match and not match[1] and not match[3] and int(match[4]) == number, '원본 셀과 행 주소 불일치')
            column = _column(match[2])
            _require(column > prior_column, '원본 셀 중복·역순')
            prior_column = column
    return data


def _source(parts, target, cut, count):
    workbook = _xml(parts['xl/workbook.xml'])
    _require(workbook.tag == f'{{{S}}}workbook', '지원하지 않는 통합문서 XML')
    protections = workbook.findall(f'{{{S}}}workbookProtection')
    _require(len(protections) <= 1, '중복 통합문서 보호 정의')
    for protection in protections:
        _require(not any(protection.get(key) in {'1', 'true', 'on'} for key in ('lockStructure', 'lockWindows', 'lockRevision'))
                 and not any(value and ('password' in key.lower() or 'hash' in key.lower()) for key, value in protection.attrib.items()),
                 '활성 통합문서 보호를 유지해야 함')
    references = workbook.find(f'{{{S}}}externalReferences')
    _require(references is None or len(references) == 0, '외부 참조 확장 미지원')
    calc = workbook.find(f'{{{S}}}calcPr')
    _require(calc is None or calc.get('refMode') != 'R1C1' and calc.get('iterate') not in {'1', 'true'},
             'R1C1·반복 계산 원본 미지원')
    relations = _rels(parts, 'xl/workbook.xml')
    sheets = []
    for node in workbook.findall(f'{{{S}}}sheets/{{{S}}}sheet'):
        part = relations.get(node.get(f'{{{R}}}id'))
        _require(part in parts and re.fullmatch(r'xl/worksheets/sheet[0-9]+\.xml', part), '시트 관계 오류·미지원 시트 종류')
        name = node.get('name')
        _require(isinstance(name, str) and name and '[' not in name and ']' not in name and ':' not in name, '잘못된 시트명')
        root = _xml(parts[part])
        _require(root.tag == f'{{{S}}}worksheet', '지원하지 않는 시트 XML')
        _rows(root)
        sheets.append({'part': part, 'name': name, 'root': root})
    _require(len({sheet['part'] for sheet in sheets}) == len(sheets)
             and len({sheet['name'].casefold() for sheet in sheets}) == len(sheets), '중복 시트명·부품')
    selected = next((sheet for sheet in sheets if sheet['part'] == target), None)
    _require(selected is not None, '계획 시트가 원본에 없음')
    root = selected['root']
    _require(root.find(f'{{{S}}}sheetProtection') is None, '대상 시트 보호를 유지해야 함')
    _require(root.find(f'{{{S}}}legacyDrawingHF') is None, '레거시 머리말·꼬리말 개체 위치 미지원')
    selected_relations = _rels(parts, target)
    for drawing in root.findall(f'{{{S}}}legacyDrawing'):
        part = selected_relations.get(drawing.get(f'{{{R}}}id'))
        _require(part in parts, 'VML 관계 오류')
        notes = _xml(parts[part]).xpath('.//*[local-name()="ClientData"]')
        _require(bool(notes), 'VML 개체의 댓글 위치를 확인할 수 없음')
        for note in notes:
            anchors = note.xpath('./*[local-name()="Anchor"]/text()')
            rows = note.xpath('./*[local-name()="Row"]/text()')
            _require(note.get('ObjectType') == 'Note' and len(anchors) == len(rows) == 1,
                     '댓글 이외 VML 개체 미지원')
            pieces = anchors[0].split(',')
            _require(len(pieces) == 8 and all(re.fullmatch(r'[0-9]+', piece.strip()) for piece in pieces)
                     and re.fullmatch(r'[0-9]+', rows[0]), 'VML 댓글 위치 오류')
            _require(max(int(pieces[2]), int(pieces[6]), int(rows[0])) < cut - 1,
                     '복제·이동해야 하는 VML 댓글 미지원')
    for part in selected_relations.values():
        if part and re.fullmatch(r'xl/(?:comments/)?comments?[0-9]+\.xml', part):
            for comment in _xml(parts[part]).findall(f'.//{{{S}}}comment'):
                point = _endpoint(comment.get('ref', ''))
                _require(point[0] == 'cell' and point[2] < cut, '복제·이동해야 하는 댓글 주소 미지원')
    # Unknown extension metadata may contain addresses. Known x14 DV is handled below.
    _extensions(root)
    data = root.find(f'{{{S}}}sheetData')
    prototype = next((row for row in data if row.get('r') == str(cut)), None)
    _require(prototype is not None and prototype.find(f'{{{S}}}extLst') is None, '원본 prototype 행 없음·행 확장 metadata 미지원')
    _require(int(data[-1].get('r')) + count - 1 <= MAX_ROW, '추가 행이 Excel 최대 행을 넘음')
    _require(sum(len(content) for content in parts.values()) + len(etree.tostring(prototype)) * (count - 1) <= MAX_BYTES,
             '확장 예상 XML 해제 크기가 64MiB를 넘음')
    strings = []
    if 'xl/sharedStrings.xml' in parts:
        strings = [''.join(node.xpath('.//s:t/text()', namespaces=NS)) for node in _xml(parts['xl/sharedStrings.xml'])]
    cells = prototype.findall(f'{{{S}}}c')
    texts = [_cell_text(cell, strings) for cell in cells]
    _require(not any(TOTAL.match(text.strip()) for text in texts), '합계·표 머리 행은 복제할 수 없음')
    _require(not (cells and cells[0].get('t') not in {'s', 'inlineStr', 'str'} and re.fullmatch(r'[0-9]+', texts[0])),
             '고정 숫자·순번 행은 복제할 수 없음')
    _require(not cells or any(not text.strip() or TOKEN.search(text) for text in texts)
             or any(cell.find(f'{{{S}}}f') is not None for cell in cells), '입력 후보가 없는 행')
    _require(all(node.tag == f'{{{S}}}c' for node in prototype), '복제할 수 없는 행 부품')
    tables, owners = {}, {}
    for sheet in sheets:
        related = _rels(parts, sheet['part'])
        for node in sheet['root'].findall(f'{{{S}}}tableParts/{{{S}}}tablePart'):
            part = related.get(node.get(f'{{{R}}}id'))
            _require(part in parts and part not in owners and part.startswith('xl/tables/'), '표 부품 관계 오류')
            table = _xml(parts[part])
            _require(table.tag == f'{{{S}}}table', '표 XML 오류')
            tables[part], owners[part] = table, sheet['name']
            if sheet is selected:
                _require(table.find(f'{{{S}}}extLst') is None, '주소 의존성이 불명확한 표 확장 metadata')
                endpoints = table.get('ref', '').split(':')
                _require(len(endpoints) == 2, '표 범위 오류')
                lo, hi = [_endpoint(item)[2] for item in endpoints]
                _require(not (int(table.get('headerRowCount', '1')) and cut == lo)
                         and not (int(table.get('totalsRowCount', '0')) and cut == hi), 'Excel 표 머리·합계 행은 복제할 수 없음')
    for merge in root.findall(f'{{{S}}}mergeCells/{{{S}}}mergeCell'):
        endpoints = merge.get('ref', '').split(':')
        _require(len(endpoints) == 2, '병합 주소 오류')
        lo, hi = [_endpoint(item)[2] for item in endpoints]
        _require(lo is not None and hi is not None and lo <= hi and not (lo < hi and lo <= cut <= hi), '세로 병합·삽입 경계 병합은 미지원')
    names = {table.get('name', '') for table in tables.values()}
    for sheet in sheets:
        for formula in sheet['root'].findall(f'.//{{{S}}}f'):
            _require(formula.get('t', 'normal') == 'normal' and not set(formula.attrib) - {'t', 'ca'},
                     '공유·배열·데이터 표 수식 미지원')
            _formula(formula.text, sheet['name'], selected['name'], cut, 0, names)
        _require(not sheet['root'].xpath('.//s:c[@cm or @vm]', namespaces=NS), '동적 셀 metadata 미지원')
    return workbook, sheets, selected, tables, owners


def _extensions(root):
    extensions = root.find(f'{{{S}}}extLst')
    if extensions is None:
        return
    for extension in extensions:
        _require(extension.tag == f'{{{S}}}ext' and len(extension) == 1
                 and extension[0].tag == f'{{{X14}}}dataValidations', '알 수 없는 시트 확장 metadata 주소 의존성')
        container = extension[0]
        _require(all(node.tag == f'{{{X14}}}dataValidation' for node in container), 'x14 DV 구조 미지원')
        for rule in container:
            _require(all(node.tag in {f'{{{XM}}}sqref', f'{{{X14}}}formula1', f'{{{X14}}}formula2'} for node in rule),
                     'x14 DV 주소·수식 구조 미지원')
            for formula in rule:
                if formula.tag != f'{{{XM}}}sqref':
                    _require(len(formula) == 1 and formula[0].tag == f'{{{XM}}}f', 'x14 DV 수식 자식 미지원')


def _spaces_range(value, cut, delta, expand):
    _require(isinstance(value, str) and value.strip(), '비어 있는 주소 범위')
    return ' '.join(_address(item, cut, delta, single_expand=expand, expand=expand) for item in value.split())


def _height_emu(root, start, stop):
    """Declared source heights only; no inferred font sizing/native layout claim."""
    geometry = root.find(f'{{{S}}}sheetFormatPr')
    default = geometry.get('defaultRowHeight') if geometry is not None else None
    if default is not None:
        _require(bool(re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', default)) and Decimal(default) > 0,
                 '그림 경계 검사에 필요한 기본 행 높이 오류')
        total = Decimal(default) * (stop - start)
    else:
        total = Decimal(0)
    declared = 0
    for row in root.findall(f'{{{S}}}sheetData/{{{S}}}row'):
        index = int(row.get('r')) - 1
        if start <= index < stop:
            hidden = row.get('hidden') in {'1', 'true'}
            value = row.get('ht', default)
            _require(hidden or value is not None and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value),
                     '그림 경계의 행 높이 미확인')
            height = Decimal(0) if hidden else Decimal(value)
            _require(height >= 0, '잘못된 행 높이')
            total += height - (Decimal(default) if default is not None else 0)
            declared += 1
    _require(default is not None or declared == stop - start, '그림 경계의 기본 행 높이 미확인')
    return total * 12700


def _drawing_dimensions(node, attribute):
    _require(node is not None and bool(re.fullmatch(r'[0-9]+', node.get(attribute, ''))), '그림 크기·위치 EMU 미확인')
    return int(node.get(attribute))


def _inspect_expected(parts, target, cut, count):
    workbook, sheets, selected, tables, owners = _source(parts, target, cut, count)
    if count == 1:
        return {}, set()
    delta = count - 1
    roots = {sheet['part']: sheet['root'] for sheet in sheets}
    roots['xl/workbook.xml'] = workbook
    names = {table.get('name', '') for table in tables.values()}
    selected_name = selected['name']
    for sheet in sheets:
        root = sheet['root']
        protected = root.find(f'{{{S}}}sheetProtection') is not None
        for formula in root.findall(f'.//{{{S}}}f'):
            _require(formula.get('t', 'normal') == 'normal' and not set(formula.attrib) - {'t', 'ca'},
                     '공유·배열·데이터 표 수식 미지원')
            formula.text = _formula(formula.text, sheet['name'], selected_name, cut, delta, names)
            if not protected:
                for cached in formula.getparent().findall(f'{{{S}}}v'):
                    formula.getparent().remove(cached)
        for formula in root.xpath('.//s:dataValidation/s:formula1 | .//s:dataValidation/s:formula2 | .//s:cfRule/s:formula', namespaces=NS):
            formula.text = _formula(formula.text, sheet['name'], selected_name, cut, delta, names)
        # x14 list text (quoted text) is not interpreted as an address.
        for formula in root.xpath('.//x14:formula1/xm:f | .//x14:formula2/xm:f', namespaces=NS):
            formula.text = _formula(formula.text, sheet['name'], selected_name, cut, delta, names)
        if sheet is not selected:
            for hyperlink in root.findall(f'{{{S}}}hyperlinks/{{{S}}}hyperlink'):
                if hyperlink.get('location'):
                    hyperlink.set('location', _formula(hyperlink.get('location'), sheet['name'], selected_name, cut, delta, names))
        _require(not root.xpath('.//s:c[@cm or @vm]', namespaces=NS), '동적 셀 metadata 미지원')
        if protected:
            _require(_canonical(root) == _canonical(_xml(parts[sheet['part']])), '영향을 받는 다른 시트 보호를 유지해야 함')
    root = selected['root']
    data = root.find(f'{{{S}}}sheetData')
    prototype = next(row for row in data if row.get('r') == str(cut))
    for row in data:
        old = int(row.get('r'))
        if old > cut:
            row.set('r', str(old + delta))
        for cell in row.findall(f'{{{S}}}c'):
            cell.set('r', _address(cell.get('r'), cut, delta, expand=False))
    position = data.index(prototype)
    for offset in range(1, count):
        cloned = deepcopy(prototype)
        cloned.set('r', str(cut + offset))
        for cell in cloned.findall(f'{{{S}}}c'):
            # Cell addresses are moved regardless of $, formula copy uses relative axes only.
            cell.set('r', _address(cell.get('r'), cut - 1, offset, expand=False))
            formula = cell.find(f'{{{S}}}f')
            if formula is not None:
                formula.text = _formula(formula.text, selected_name, selected_name, cut, 0, names, copy_offset=offset)
        data.insert(position + offset, cloned)
    for node in root.xpath('./s:dimension | ./s:autoFilter | .//s:sortState | .//s:sortCondition | .//s:protectedRange', namespaces=NS):
        if node.get('ref'):
            node.set('ref', _address(node.get('ref'), cut, delta, single_expand=True))
    for node in root.xpath('./s:dataValidations/s:dataValidation | ./s:conditionalFormatting | .//s:ignoredError', namespaces=NS):
        node.set('sqref', _spaces_range(node.get('sqref'), cut, delta, True))
    for node in root.xpath('.//x14:dataValidation/xm:sqref', namespaces=NS):
        node.text = _spaces_range(node.text, cut, delta, True)
    merges = root.find(f'{{{S}}}mergeCells')
    if merges is not None:
        additions = []
        for merge in merges:
            old = merge.get('ref')
            merge.set('ref', _address(old, cut, delta, expand=False))
            if all(_endpoint(item)[2] == cut for item in old.split(':')):
                for offset in range(1, count):
                    new = deepcopy(merge)
                    new.set('ref', _address(old, cut - 1, offset, expand=False))
                    additions.append(new)
        merges.extend(additions)
        merges.set('count', str(len(merges)))
    hyperlinks = []
    for node in root.xpath('.//s:pane | .//s:sheetView | .//s:selection | ./s:hyperlinks/s:hyperlink', namespaces=NS):
        original_ref = node.get('ref')
        for attribute in ('topLeftCell', 'activeCell', 'ref'):
            if node.get(attribute):
                node.set(attribute, _address(node.get(attribute), cut, delta, expand=False))
        if node.get('sqref'):
            node.set('sqref', _spaces_range(node.get('sqref'), cut, delta, False))
        if node.get('location'):
            node.set('location', _formula(node.get('location'), selected_name, selected_name, cut, delta, names))
        if node.tag == f'{{{S}}}hyperlink' and original_ref and all(_endpoint(point)[2] == cut for point in original_ref.split(':')):
            for offset in range(1, count):
                cloned = deepcopy(node)
                cloned.set('ref', _address(original_ref, cut - 1, offset, expand=False))
                hyperlinks.append(cloned)
        if node.tag == f'{{{S}}}pane' and node.get('state') in {'frozen', 'frozenSplit'} and node.get('ySplit'):
            _require(bool(re.fullmatch(r'[0-9]+(?:\.0+)?', node.get('ySplit'))), '동결 행 경계가 정수가 아님')
            split = int(node.get('ySplit').split('.')[0])
            if split > cut:
                node.set('ySplit', str(split + delta))
    if hyperlinks:
        root.find(f'{{{S}}}hyperlinks').extend(hyperlinks)
    for node in root.findall(f'{{{S}}}rowBreaks/{{{S}}}brk'):
        _require(bool(re.fullmatch(r'[0-9]+', node.get('id', ''))), '잘못된 행 페이지 나누기')
        if int(node.get('id')) > cut:
            node.set('id', str(int(node.get('id')) + delta))
    for part, table in tables.items():
        owner = owners[part]
        if owner == selected_name:
            for node in [table, *table.xpath('.//s:autoFilter | .//s:sortState | .//s:sortCondition', namespaces=NS)]:
                if node.get('ref'):
                    node.set('ref', _address(node.get('ref'), cut, delta, single_expand=True))
        for formula in table.xpath('.//s:calculatedColumnFormula | .//s:totalsRowFormula', namespaces=NS):
            _require(formula.get('array') not in {'1', 'true'}, '표 배열 계산 열 미지원')
            original_text = formula.text
            formula.text = _formula(formula.text, owner, selected_name, cut, delta, names)
            protected_owner = next(sheet for sheet in sheets if sheet['name'] == owner)['root'].find(f'{{{S}}}sheetProtection') is not None
            _require(not protected_owner or formula.text == original_text, '다른 시트의 보호된 표 참조 변경 필요')
        roots[part] = table
    for node in workbook.findall(f'{{{S}}}definedNames/{{{S}}}definedName'):
        scope = node.get('localSheetId')
        _require(scope is None or re.fullmatch(r'[0-9]+', scope) and int(scope) < len(sheets), '잘못된 정의 이름의 시트 범위')
        context = sheets[int(scope)]['name'] if scope is not None else None
        node.text = _formula(node.text, context, selected_name, cut, delta, names)
    relations = _rels(parts, target)
    for drawing in root.findall(f'{{{S}}}drawing'):
        part = relations.get(drawing.get(f'{{{R}}}id'))
        _require(part in parts and part.startswith('xl/drawings/'), '그림 부품 관계 오류')
        drawing_root = _xml(parts[part])
        _require(drawing_root.tag == f'{{{XDR}}}wsDr', '그림 부품 형식 오류')
        for anchor in drawing_root:
            _require(anchor.tag in {f'{{{XDR}}}twoCellAnchor', f'{{{XDR}}}oneCellAnchor', f'{{{XDR}}}absoluteAnchor'},
                     '그림 좌표 구조 미지원')
            points = anchor.xpath('./xdr:from/xdr:row | ./xdr:to/xdr:row', namespaces=NS)
            _require(all(re.fullmatch(r'[0-9]+', node.text or '') for node in points), '그림 행 좌표 오류')
            numbers = [int(node.text) for node in points]
            _require(not numbers or not min(numbers) <= cut - 1 <= max(numbers), '원본 행을 가로지르는 그림·차트')
            _require(all(0 <= value < MAX_ROW and value + (delta if value >= cut else 0) < MAX_ROW for value in numbers),
                     '그림 행 좌표가 Excel 범위를 넘음')
            if anchor.tag == f'{{{XDR}}}oneCellAnchor' and numbers and numbers[0] < cut - 1:
                offset = anchor.find(f'{{{XDR}}}from/{{{XDR}}}rowOff')
                _require(offset is not None and re.fullmatch(r'[0-9]+', offset.text or ''), '그림 행 offset 미확인')
                height = _drawing_dimensions(anchor.find(f'{{{XDR}}}ext'), 'cy')
                _require(int(offset.text) + height <= _height_emu(root, numbers[0], cut - 1),
                         '위쪽 그림이 원본 반복행까지 걸치므로 확장 미지원')
            if anchor.tag == f'{{{XDR}}}absoluteAnchor':
                top = _drawing_dimensions(anchor.find(f'{{{XDR}}}pos'), 'y')
                height = _drawing_dimensions(anchor.find(f'{{{XDR}}}ext'), 'cy')
                _require(top + height <= _height_emu(root, 0, cut - 1),
                         '행 연결이 불명확한 절대 위치 그림이 삽입 영역에 걸침')
            for node in points:
                if int(node.text) >= cut:
                    node.text = str(int(node.text) + delta)
        roots[part] = drawing_root
    for part, content in parts.items():
        if re.fullmatch(r'xl/charts/chart[0-9]+\.xml', part):
            chart = _xml(content)
            changed = False
            for formula in chart.findall(f'.//{{{C}}}f'):
                value = _formula(formula.text, None, selected_name, cut, delta, names)
                changed |= value != formula.text
                formula.text = value
            if changed:
                for cache in chart.xpath('.//c:numCache | .//c:strCache', namespaces=NS):
                    cache.getparent().remove(cache)
                roots[part] = chart
    calc = workbook.find(f'{{{S}}}calcPr')
    if calc is None:
        calc = etree.SubElement(workbook, f'{{{S}}}calcPr')
    calc.set('fullCalcOnLoad', '1')
    calc.set('forceFullCalc', '1')
    deleted = {part for part in parts if re.fullmatch(r'xl/calcChain[0-9]*\.xml', part)}
    if deleted:
        _require(all(_xml(parts[part]).tag == f'{{{S}}}calcChain' for part in deleted), 'calcChain 이름을 위장한 비대상 부품')
        for part in ('xl/_rels/workbook.xml.rels', '[Content_Types].xml'):
            xml = _xml(parts[part])
            for node in list(xml):
                linked = _relation_target('xl/workbook.xml', node.get('Target')) if part.endswith('.rels') and node.get('TargetMode') != 'External' else node.get('PartName', '').lstrip('/')
                if linked in deleted:
                    if part.endswith('.rels'):
                        _require(node.get('Type', '').endswith('/calcChain'), 'calcChain을 위장한 다른 관계')
                    else:
                        _require(node.get('ContentType') == 'application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml', 'calcChain을 위장한 다른 content type')
                    xml.remove(node)
            roots[part] = xml
    return {part: xml for part, xml in roots.items() if _canonical(xml) != _canonical(_xml(parts[part]))}, deleted


def _verify_xlsx_repeat(original_path, prepared_path, plan):
    """Compare an expansion to its immutable source; raise on any deviation.

    A single original prototype is retained, followed by count-1 identical row
    copies with only declared addresses/formulas changed. No cached calculation,
    native rendering, legal compliance or completed submission is certified.
    """
    _require(isinstance(plan, dict) and set(plan) == {'table_id', 'row', 'count'}, '잘못된 계획 항목')
    _require(isinstance(plan['table_id'], str) and re.fullmatch(r'xlsx:xl/worksheets/sheet[0-9]+\.xml', plan['table_id']), '잘못된 시트 식별자')
    _require(type(plan['row']) is int and 1 <= plan['row'] <= MAX_ROW
             and type(plan['count']) is int and 1 <= plan['count'] <= 200, '행 번호·count 범위 오류')
    _require(isinstance(original_path, (str, Path)) and isinstance(prepared_path, (str, Path)), '파일 경로 형식 오류')
    original, prepared = Path(original_path).resolve(), Path(prepared_path).resolve()
    _require(original != prepared, '원본과 준비본은 다른 파일이어야 함')
    _require(original.suffix.lower() == prepared.suffix.lower() == '.xlsx', '계획과 파일 형식 불일치')
    if original.exists() and prepared.exists():
        _require(not original.samefile(prepared), '원본과 준비본이 같은 파일임')
    raw, parts = _read(original)
    output, actual = _read(prepared)
    expected, deleted = _inspect_expected(parts, plan['table_id'][5:], plan['row'], plan['count'])
    _require(set(actual) == set(parts) - deleted, '부품 추가·삭제가 승인 범위를 벗어남')
    for part, content in actual.items():
        if part in expected:
            _require(_canonical(_xml(content)) == _canonical(expected[part]), '원본 기준 행·수식·주소·서식 대조 불일치: ' + part)
        else:
            _require(content == parts[part], '비대상 부품 또는 변경 불필요 부품 변조: ' + part)
    _require(original.read_bytes() == raw and prepared.read_bytes() == output, '검사 중 입력 파일이 변경됨')
    source_rows = len(_xml(parts[plan['table_id'][5:]]).find(f'{{{S}}}sheetData'))
    checks = ['immutable_source', 'prototype_and_row_order', 'cell_styles_values_and_formulas',
              'source_bound_A1_dependencies', 'merges_and_ranges', 'protected_parts_preserved',
              'cache_invalidation_and_exact_package_scope']
    return {'status': 'passed', 'kind': 'xlsx', 'format': 'xlsx', 'plan': deepcopy(plan),
            'sha': {'original': sha256(raw).hexdigest(), 'prepared': sha256(output).hexdigest()},
            'original_sha256': sha256(raw).hexdigest(), 'prepared_sha256': sha256(output).hexdigest(),
            'added_rows': plan['count'] - 1, 'changed_parts': sorted(expected), 'deleted_parts': sorted(deleted),
            'rows': {'original': source_rows, 'prepared': source_rows + plan['count'] - 1, 'added': plan['count'] - 1},
            'checks': [{'name': check, 'status': 'passed'} for check in checks],
            'native_visual_qa': 'pending', 'native_formula_recalculation': 'pending',
            'scope': '원본 ZIP/XML 대비 단일 시트 반복행·명시적 A1 의존성 검사',
            'legal_compliance_certified': False, 'full_submission_ready': False}


def verify_xlsx_repeat(original_path, prepared_path, plan):
    """Return independent source-bound evidence or a ValueError; never write inputs."""
    try:
        return _verify_xlsx_repeat(original_path, prepared_path, plan)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f'XLSX 반복행 독립 검수 실패: 원본·준비본 재열기 또는 XML 구조 오류 ({exc})') from exc
