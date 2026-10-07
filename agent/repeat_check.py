"""Independently inspect row expansion; never call a row transformer or filler.

Structural success does not certify pagination or native Word/Hancom rendering.
Only a single declared table may acquire copies of its unchanged prototype row.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
import stat
from zipfile import ZipFile, ZIP_STORED

from lxml import etree

from parsers.extract import _check_zip, _xml


MAX_BYTES = 64 * 1024 * 1024
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
W14 = 'http://schemas.microsoft.com/office/word/2010/wordml'
HP = 'http://www.hancom.co.kr/hwpml/2011/paragraph'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS = {'w': W, 'w14': W14, 'hp': HP}
PLACEHOLDER = re.compile(r'\{\{\s*[^{}]+\s*\}\}')
TOTAL = re.compile(r'^(?:합계|총계|소계|총\s*합계|total|subtotal)(?:\s|[:：]|$)', re.I)
XPATH = re.compile(r'(?:/(?:[A-Za-z_][\w.-]*:)?[A-Za-z_][\w.-]*(?:\[[1-9][0-9]*\])?)+')


def _require(condition, message):
    if not condition:
        raise ValueError('반복행 확장 검수 실패: ' + message)


def _plan(plan):
    _require(isinstance(plan, dict) and set(plan) == {'table_id', 'row', 'count'}, '계획 항목이 올바르지 않음')
    _require(isinstance(plan['table_id'], str) and plan['table_id'] == plan['table_id'].strip(), '표 식별자는 문자열이어야 함')
    _require(type(plan['row']) is int and plan['row'] >= 1 and type(plan['count']) is int and 1 <= plan['count'] <= 200,
             '행은 1부터, count는 1..200의 정수이어야 함')
    tokens = plan['table_id'].split(':', 2)
    _require(len(tokens) == 3 and tokens[0] in {'docx', 'hwpx'}, '표 식별자 형식이 올바르지 않음')
    kind, part, path = tokens
    allowed = r'word/(document|header[0-9]+|footer[0-9]+|footnotes|endnotes)\.xml' if kind == 'docx' else r'Contents/section[0-9]+\.xml'
    _require(bool(re.fullmatch(allowed, part)) and bool(XPATH.fullmatch(path)), '표 부품 또는 XPath가 올바르지 않음')
    return kind, part, path


def _read_package(path, kind):
    _require(path.is_file() and 0 < path.stat().st_size <= MAX_BYTES, '파일 크기 제한(64MiB) 또는 파일 존재 확인 실패')
    raw = path.read_bytes()
    _require(len(raw) <= MAX_BYTES, '파일 크기 제한(64MiB) 위반')
    with ZipFile(path) as archive:
        _check_zip(archive)
        infos = archive.infolist()
        _require(len(infos) <= 10000 and sum(item.file_size for item in infos) <= MAX_BYTES, 'ZIP 해제 크기·항목 수 제한 위반')
        for item in infos:
            for name in {item.filename, item.orig_filename}:
                _require(name and '\\' not in name and not name.startswith('/') and not re.search(r'[\x00-\x1f:]', name)
                         and '..' not in PurePosixPath(name).parts, '안전하지 않은 ZIP 부품 경로')
            _require(not item.flag_bits & 1 and stat.S_IFMT(item.external_attr >> 16) != stat.S_IFLNK, '암호·링크 ZIP 부품은 지원하지 않음')
        parts = {item.filename: archive.read(item) for item in infos}
        _require(not any(name.lower().startswith(('_xmlsignatures/', 'signatures/'))
                         or 'signature' in name.lower() and name.startswith('META-INF/') for name in parts), '전자 서명된 원본은 확장할 수 없음')
        _require('word/document.xml' in parts if kind == 'docx' else parts.get('mimetype', b'').strip() == b'application/hwp+zip', '본문 또는 HWPX mimetype이 올바르지 않음')
        if kind == 'hwpx':
            _require(infos[0].filename == 'mimetype' and infos[0].compress_type == ZIP_STORED, 'HWPX mimetype 배치·압축 변경')
            _require(not any(re.search(r'encrypt|signature|distribut|drm', name, re.I) for name in parts), '암호·배포용·서명 보호 패키지')
            for name, data in parts.items():
                if name.endswith('.xml'):
                    root = _xml(data)
                    _require(not any(etree.QName(node).localname.lower() in {'encryption-data', 'encrypteddata', 'signature',
                                     'docsecurity', 'documentprotection', 'trackchangeprotect'} for node in root.iter() if isinstance(node.tag, str)), '원본 문서 보안·보호를 유지해야 함')
    return raw, parts


def _canonical(node):
    return etree.tostring(node, method='c14n', exclusive=True, with_comments=True)


def _text(node):
    return ''.join(''.join(item.itertext()) for item in node.iter() if isinstance(item.tag, str)
                   and etree.QName(item).localname == 't')


def _table(root, path, kind):
    namespaces = {key: value for key, value in root.nsmap.items() if key}
    namespaces.update(NS)
    nodes = root.xpath(path, namespaces=namespaces)
    _require(len(nodes) == 1 and nodes[0].tag == f'{{{W if kind == "docx" else HP}}}tbl', '지정한 표가 없거나 모호함')
    return nodes[0]


def _docx_safe(parts, root, table, prototype, following):
    settings = _xml(parts['word/settings.xml']) if 'word/settings.xml' in parts else None
    _require(settings is None or not settings.xpath('.//w:documentProtection[@w:enforcement="1" or @w:enforcement="true" or @w:enforcement="on"] | .//w:writeProtection', namespaces=NS), '원본 문서 보호를 유지해야 함')
    _require(not table.xpath('ancestor::w:sdt/w:sdtPr/w:lock | ancestor::w:sdt/w:sdtPr/w:dataBinding', namespaces=NS), '원본 표 잠금 또는 연결된 참조')
    _require(all(child.tag in {f'{{{W}}}{name}' for name in ['tblPr', 'tblGrid', 'tr']} for child in table if isinstance(child.tag, str)), '지원하지 않는 표 행 래퍼')
    _require(not prototype.xpath('./w:trPr/w:tblHeader | .//w:vMerge', namespaces=NS)
             and (following is None or not following.xpath('.//w:vMerge[not(@w:val) or @w:val="continue"]', namespaces=NS)), '표 머리글 또는 세로 병합 경계')
    forbidden = {'fldChar', 'instrText', 'fldSimple', 'bookmarkStart', 'bookmarkEnd', 'commentRangeStart', 'commentRangeEnd',
                 'commentReference', 'footnoteReference', 'endnoteReference', 'drawing', 'pict', 'object', 'altChunk', 'hyperlink',
                 'customXml', 'permStart', 'permEnd', 'ins', 'del', 'moveFrom', 'moveTo', 'moveFromRangeStart', 'moveFromRangeEnd',
                 'moveToRangeStart', 'moveToRangeEnd', 'tbl'}
    _require(not any(etree.QName(item).localname in forbidden or any(etree.QName(key).namespace == REL for key in item.attrib)
                     for item in prototype.iter() if isinstance(item.tag, str)), '중첩 표·필드·그림·행별 참조는 복제할 수 없음')
    _require(not prototype.xpath('.//w:sdtPr/w:lock | .//w:sdtPr/w:dataBinding | .//w:sdtPr/w:date | .//w:sdtPr/w:picture | .//w:sdtPr/w:group | .//w:sdtPr/*[local-name()="repeatingSection" or local-name()="repeatingSectionItem"]', namespaces=NS), '잠금·연결 또는 미지원 입력 컨트롤')
    for item in prototype.iter():
        if not isinstance(item.tag, str):
            continue
        for key, value in item.attrib.items():
            _require(not etree.QName(key).localname.lower().endswith('id') or key in {f'{{{W14}}}paraId', f'{{{W14}}}textId'}, '알 수 없는 행별 식별자')
            if key in {f'{{{W14}}}paraId', f'{{{W14}}}textId'}:
                _require(bool(re.fullmatch(r'[0-9A-Fa-f]{8}', value)), '원본 문단 ID 형식 불일치')
    for identifier in prototype.xpath('.//w:sdtPr/w:id', namespaces=NS):
        _require(bool(re.fullmatch(r'-?[0-9]+', identifier.get(f'{{{W}}}val', ''))), '원본 선택 ID 형식 불일치')
    for sdt in prototype.xpath('.//w:sdt', namespaces=NS):
        properties = sdt.find(f'{{{W}}}sdtPr'); content = sdt.find(f'{{{W}}}sdtContent')
        _require(properties is not None and content is not None, '선택 컨트롤 속성·본문 누락')
        choices = properties.xpath('./w:dropDownList | ./w:comboBox', namespaces=NS)
        _require(len(choices) <= 1, '원본 선택 정의 중복')
        for choice in choices:
            _require(all(item.tag == f'{{{W}}}listItem' for item in choice), '알 수 없는 원본 선택 항목')
            options = [item.get(f'{{{W}}}value') for item in choice]
            labels = [item.get(f'{{{W}}}displayText', item.get(f'{{{W}}}value')) for item in choice]
            _require((options or choice.tag == f'{{{W}}}comboBox')
                     and all(isinstance(value, str) and value == value.strip() and value
                             and isinstance(label, str) and label.strip() and not any(c in value + label for c in '\r\n\t')
                             for value, label in zip(options, labels))
                     and len(set(options)) == len(options) and len(set(labels)) == len(labels), '원본 선택 옵션이 비어 있거나 중복됨')
            _require(len(content.xpath('.//w:r', namespaces=NS)) == 1 and content.xpath('.//w:t', namespaces=NS)
                     and not content.xpath('.//w:br | .//w:tab | .//w:tbl | .//w:sdt | .//w:drawing | .//w:fldChar', namespaces=NS)
                     and len(content.xpath('.//w:p', namespaces=NS)) <= 1, '원본 선택 표시 구조가 모호함')
        checkbox = properties.find(f'{{{W14}}}checkbox')
        if checkbox is not None:
            checked = checkbox.find(f'{{{W14}}}checked')
            _require(checked is not None and checked.get(f'{{{W14}}}val') in {'0', 'false', 'off'}, '원본 체크 상태가 미선택으로 확인되지 않음')
        else:
            value = _text(content).strip()
            _require(not value or PLACEHOLDER.search(value) or properties.find(f'{{{W}}}showingPlcHdr') is not None, '이미 입력한 선택·구조화 값은 반복할 수 없음')
    columns = [_text(cell).strip() for cell in prototype.findall(f'{{{W}}}tc')]
    _require(not any(TOTAL.match(text) for text in columns) and not (columns and re.fullmatch(r'[0-9]+[.)]?', columns[0])), '합계·소계·고정 순번 행은 반복할 수 없음')
    _require(any(not text or PLACEHOLDER.search(text) for text in columns) or prototype.xpath('.//w:sdtPr/w:showingPlcHdr | .//w:sdtPr/w14:checkbox', namespaces=NS), '빈 칸 또는 자리표시자가 없는 행')
    grid = table.findall(f'{{{W}}}tblGrid/{{{W}}}gridCol')
    if grid:
        for row in table.findall(f'{{{W}}}tr'):
            total = 0
            for cell in row.findall(f'{{{W}}}tc'):
                span = cell.find(f'{{{W}}}tcPr/{{{W}}}gridSpan')
                value = span.get(f'{{{W}}}val', '1') if span is not None else '1'
                _require(value.isdecimal() and int(value) >= 1, '잘못된 가로 병합 폭')
                total += int(value)
            for name in ['gridBefore', 'gridAfter']:
                gap = row.find(f'{{{W}}}trPr/{{{W}}}{name}')
                value = gap.get(f'{{{W}}}val', '0') if gap is not None else '0'
                _require(value.isdecimal(), '잘못된 표 열 범위')
                total += int(value)
            _require(total == len(grid), '원본 병합·표 열 범위 불일치')


def _docx_ids(parts):
    used = {'sdt': set(), 'hex': set()}
    for name, data in parts.items():
        if name.startswith('word/') and name.endswith('.xml'):
            root = _xml(data)
            used['sdt'].update(int(value) for value in root.xpath('.//w:sdtPr/w:id/@w:val', namespaces=NS) if re.fullmatch(r'-?[0-9]+', value))
            used['hex'].update(int(value, 16) for value in root.xpath('//@w14:paraId | //@w14:textId', namespaces=NS) if re.fullmatch(r'[0-9A-Fa-f]{8}', value))
    return used


def _docx_clone(prototype, cloned, used):
    normalized = deepcopy(cloned)
    old_nodes, new_nodes = list(prototype.iter()), list(normalized.iter())
    _require(len(old_nodes) == len(new_nodes), '추가행의 원본 구조·내용 변경')
    for old, new in zip(old_nodes, new_nodes):
        _require(old.tag == new.tag, '추가행의 노드 변경')
        if not isinstance(old.tag, str):
            continue
        attributes = [f'{{{W14}}}paraId', f'{{{W14}}}textId']
        if old.tag == f'{{{W}}}id' and old.getparent().tag == f'{{{W}}}sdtPr':
            attributes.append(f'{{{W}}}val')
        for attribute in attributes:
            if attribute not in old.attrib:
                continue
            value = new.get(attribute, '')
            group = 'sdt' if attribute == f'{{{W}}}val' else 'hex'
            _require(bool(re.fullmatch(r'[0-9]+' if group == 'sdt' else r'[0-9A-Fa-f]{8}', value)), '복제 ID 형식 변경')
            number = int(value, 10 if group == 'sdt' else 16)
            _require(0 < number < 0x80000000 and number not in used[group], '복제 ID 충돌·범위 위반')
            used[group].add(number)
            new.set(attribute, old.get(attribute))
    _require(_canonical(normalized) == _canonical(prototype), '추가행의 원본 글꼴·병합·속성·문구 변경')


def _integer(value, name, *, minimum=0, maximum=0xFFFFFFFF):
    _require(isinstance(value, str) and len(value) <= 11 and bool(re.fullmatch(r'-?[0-9]+', value)), name + ' 정수 형식 오류')
    number = int(value)
    _require(minimum <= number <= maximum, name + ' 범위 오류')
    return number


def _hwpx_geometry(table):
    rows = table.findall(f'{{{HP}}}tr')
    row_count = _integer(table.get('rowCnt'), 'rowCnt', minimum=1)
    col_count = _integer(table.get('colCnt'), 'colCnt', minimum=1)
    _require(row_count == len(rows) and row_count * col_count <= 200000, '원본 행수·열수·병합 범위 불일치')
    occupied = set()
    cells = []
    for row_number, row in enumerate(rows):
        previous_col = -1
        for cell in row.findall(f'{{{HP}}}tc'):
            addresses = cell.findall(f'{{{HP}}}cellAddr'); spans = cell.findall(f'{{{HP}}}cellSpan')
            sizes = cell.findall(f'{{{HP}}}cellSz')
            _require(len(addresses) == len(spans) == len(sizes) == 1, '셀 주소·크기 또는 병합 정보 누락·중복')
            _integer(sizes[0].get('width'), 'cellSz 폭', minimum=1)
            _integer(sizes[0].get('height'), 'cellSz 높이')
            address, span = addresses[0], spans[0]
            r = _integer(address.get('rowAddr'), 'rowAddr'); c = _integer(address.get('colAddr'), 'colAddr')
            rs = _integer(span.get('rowSpan'), 'rowSpan', minimum=1); cs = _integer(span.get('colSpan'), 'colSpan', minimum=1)
            _require(r == row_number and c > previous_col and r + rs <= row_count and c + cs <= col_count, '원본 셀 주소·병합 범위·순서 오류')
            previous_col = c
            region = {(y, x) for y in range(r, r + rs) for x in range(c, c + cs)}
            _require(not occupied & region, '원본 셀 병합 겹침')
            occupied.update(region)
            cells.append((cell, r, rs))
    _require(len(occupied) == row_count * col_count, '원본 병합·열 범위에 빈 위치가 있음')
    return rows, cells


def _hwpx_height(table, prototype):
    heights = []
    for cell in prototype.findall(f'{{{HP}}}tc'):
        sizes = cell.findall(f'{{{HP}}}cellSz')
        _require(len(sizes) == 1, '원본 셀 높이 누락·중복')
        height = _integer(sizes[0].get('height'), 'cellSz 높이')
        lines = cell.xpath('.//hp:linesegarray/hp:lineseg', namespaces=NS)
        if lines:
            bottom = max(_integer(line.get('vertpos'), 'lineseg 위치')
                         + _integer(line.get('vertsize'), 'lineseg 높이') for line in lines)
            _require(cell.get('hasMargin', '0') in {'0', '1'}, '셀 여백 위치 미확인')
            margins = cell.findall(f'{{{HP}}}cellMargin') if cell.get('hasMargin') == '1' else table.findall(f'{{{HP}}}inMargin')
            _require(len(margins) == 1, '유효 셀 여백 누락·중복')
            margin = sum(_integer(margins[0].get(name), '셀 여백') for name in ['top', 'bottom'])
            height = max(height, bottom + margin)
        heights.append(height)
    _require(bool(heights) and max(heights) > 0, '원본 행에 유효한 셀 높이가 없음')
    return max(heights)


def _hwpx_ids(parts):
    used = set()
    for name, data in parts.items():
        if name.endswith('.xml'):
            root = _xml(data)
            for item in root.iter():
                for attribute, value in item.attrib.items():
                    if etree.QName(attribute).localname.lower() == 'id' and re.fullmatch(r'[0-9]+', value):
                        used.add(int(value))
    return used


def _hwpx_clone(prototype, cloned, row_number, used):
    normalized = deepcopy(cloned)
    old_nodes, new_nodes = list(prototype.iter()), list(normalized.iter())
    _require(len(old_nodes) == len(new_nodes), '추가행의 원본 구조·내용 변경')
    for old, new in zip(old_nodes, new_nodes):
        _require(old.tag == new.tag, '추가행의 노드 변경')
        if old.tag in {f'{{{HP}}}p', f'{{{HP}}}subList'} and old.get('id', ''):
            _integer(old.get('id'), '원본 문단·목록 ID')
            number = _integer(new.get('id'), '복제 문단·목록 ID', minimum=1)
            _require(number not in used, '복제 문단·목록 ID 충돌')
            used.add(number)
            new.set('id', old.get('id'))
        if old.tag == f'{{{HP}}}cellAddr':
            _require(_integer(new.get('rowAddr'), '복제 셀 주소') == row_number, '복제 행의 실제 셀 주소 불일치')
            new.set('rowAddr', old.get('rowAddr'))
    _require(_canonical(normalized) == _canonical(prototype), '추가행의 원본 글꼴·병합·속성·문구 변경')


def _hwpx_check(parts, old_root, new_root, old_table, new_table, index, delta):
    old_rows, cells = _hwpx_geometry(old_table)
    prototype = old_rows[index]
    _require(all(child.tag in {f'{{{HP}}}{name}' for name in ['sz', 'pos', 'outMargin', 'inMargin', 'tr']} for child in old_table), '표 캡션·셀 영역·미지원 범위 참조')
    _require(not any(ancestor.tag == f'{{{HP}}}tbl' for ancestor in old_table.iterancestors()), '중첩 표 높이·참조 미지원')
    _require(old_table.get('lock', '0') in {'0', '', 'false', 'none'}
             and all(cell.get('protect', '0') in {'0', '', 'false', 'none'} for cell in old_table.xpath('./hp:tr/hp:tc', namespaces=NS)), '원본 표·셀 보호를 유지해야 함')
    size = old_table.find(f'{{{HP}}}sz')
    _require(size is not None and size.get('protect', '0') in {'0', '', 'false', 'none'} and size.get('heightRelTo') == 'ABSOLUTE', '표 보호 또는 상대 높이 미지원')
    _require(not any(r <= index < r + rs - 1 for _, r, rs in cells)
             and not any(rs > 1 for _, r, rs in cells if r == index), '삽입 경계를 지나는 세로 병합')
    plain_tags = {'tr', 'tc', 'subList', 'p', 'run', 't', 'lineBreak', 'tab', 'nbSpace', 'fwSpace', 'hyphen',
                  'cellAddr', 'cellSpan', 'cellSz', 'cellMargin', 'linesegarray', 'lineseg'}
    safe_refs = {'paraPrIDRef', 'styleIDRef', 'charPrIDRef', 'borderFillIDRef'}
    for item in prototype.iter():
        _require(isinstance(item.tag, str), '알 수 없는 행별 XML 구조')
        local = etree.QName(item).localname
        _require(etree.QName(item).namespace == HP and local in plain_tags, '중첩 표·그림·제어·행별 참조는 복제할 수 없음')
        _require(item.get('protect', '0') in {'0', '', 'false', 'none'} and item.get('lock', '0') in {'0', '', 'false', 'none'}, '원본 셀·문단 보호를 유지해야 함')
        _require(not (local == 'tc' and (item.get('header', '0') not in {'0', '', 'false', 'none'} or item.get('name', ''))), '표 머리글 또는 이름이 붙은 참조 셀')
        _require(not any(name in item.attrib for name in ['paraTcId', 'charTcId']), '변경 추적 참조 미지원')
        flags = ['pageBreak', 'columnBreak', 'merged'] if local == 'p' else ['hasTextRef', 'hasNumRef'] if local == 'subList' else []
        _require(all(item.get(name, '0') in {'0', '', 'false', 'none'} for name in flags), '페이지·연결 문단·외부 본문 참조 미지원')
        for attribute, value in item.attrib.items():
            name = etree.QName(attribute).localname
            if name.lower() == 'id':
                _require(local in {'p', 'subList'}, '알 수 없는 행별 식별자')
            elif name.lower().endswith('ref') and name not in safe_refs:
                _require(value in {'', '0'}, '행별 연결 참조를 복제할 수 없음')
        if local == 'p':
            _integer(item.get('id'), '원본 문단 ID')
    columns = [_text(cell).strip() for cell in prototype.findall(f'{{{HP}}}tc')]
    _require(not any(TOTAL.match(text) for text in columns) and not (columns and re.fullmatch(r'[0-9]+[.)]?', columns[0])), '합계·소계·고정 순번 행은 반복할 수 없음')
    for value in columns:
        remaining = PLACEHOLDER.sub('', value).strip()
        _require(not remaining or PLACEHOLDER.search(value) and re.fullmatch(r'[\s()\[\]:/\-]*(?:원|천원|백만원|명|개|건|%|kg|mg|년|월|일)[\s()\[\]:/\-]*', remaining), '원본 문구·실적이 있는 행은 복제하지 않음')
    new_rows = new_table.findall(f'{{{HP}}}tr')
    _require(_integer(new_table.get('rowCnt'), '준비 rowCnt') == len(new_rows), '준비 rowCnt·실제 행수 불일치')
    used = _hwpx_ids(parts)
    for row_number, clone in enumerate(new_rows[index + 1:index + 1 + delta], index + 1):
        _hwpx_clone(prototype, clone, row_number, used)
    for position, old_row in enumerate(old_rows):
        actual = new_rows[position if position <= index else position + delta]
        old_addresses = old_row.xpath('./hp:tc/hp:cellAddr', namespaces=NS)
        new_addresses = actual.xpath('./hp:tc/hp:cellAddr', namespaces=NS)
        _require(len(old_addresses) == len(new_addresses), '원본 행의 셀 주소 손실')
        for old, new in zip(old_addresses, new_addresses):
            expected = position if position <= index else position + delta
            _require(_integer(new.get('rowAddr'), '이동 셀 주소') == expected, '뒤행 이동 셀 주소 불일치')
            new.set('rowAddr', old.get('rowAddr'))
    new_table.set('rowCnt', old_table.get('rowCnt'))
    sizes = old_table.findall(f'{{{HP}}}sz'); new_sizes = new_table.findall(f'{{{HP}}}sz')
    _require(len(sizes) == len(new_sizes) == 1, '표 높이 정보 누락·중복')
    expected_height = _integer(sizes[0].get('height'), '원본 표 높이') + delta * (_hwpx_height(old_table, prototype) + _integer(old_table.get('cellSpacing', '0'), '셀 간격'))
    _require(_integer(new_sizes[0].get('height'), '준비 표 높이') == expected_height, '복제 행 높이·표 높이 산식 불일치')
    new_sizes[0].set('height', sizes[0].get('height'))
    if delta:
        ancestor = old_table
        while ancestor.getparent() is not None and ancestor.getparent() is not old_root:
            ancestor = ancestor.getparent()
        _require(ancestor.getparent() is old_root and ancestor.tag == f'{{{HP}}}p', '캐시 재배치 기준 문단을 확인할 수 없음')
        start = list(old_root).index(ancestor)
        for old_child, new_child in zip(list(old_root)[start:], list(new_root)[start:]):
            if old_child.tag == f'{{{HP}}}p':
                _require(not new_child.findall(f'{{{HP}}}linesegarray'), '표와 뒤 문단의 재배치 캐시가 남아 있음')
                for cache in old_child.findall(f'{{{HP}}}linesegarray'):
                    old_child.remove(cache)


def verify_repeat_expansion(original_path, prepared_path, plan):
    """Return passed evidence; raise ValueError on unsafe or undeclared changes."""
    if isinstance(plan, dict) and isinstance(plan.get('table_id'), str) and plan['table_id'].startswith('xlsx:'):
        from agent.repeat_xlsx_check import verify_xlsx_repeat
        return verify_xlsx_repeat(original_path, prepared_path, plan)
    kind, part, path = _plan(plan)
    _require(isinstance(original_path, (str, Path)) and isinstance(prepared_path, (str, Path)), '파일 경로 형식이 올바르지 않음')
    original, prepared = Path(original_path), Path(prepared_path)
    _require(original.resolve() != prepared.resolve() and not (original.is_file() and prepared.is_file() and original.samefile(prepared)), '원본과 준비 파일이 같음')
    _require(original.suffix.lower() == prepared.suffix.lower() == '.' + kind, '계획과 파일 형식 불일치')
    try:
        original_raw, before = _read_package(original, kind)
        prepared_raw, after = _read_package(prepared, kind)
        _require(set(before) == set(after), 'ZIP 부품 추가·삭제')
        _require(part in before, '계획에 지정한 부품이 없음')
        changed = [name for name in before if before[name] != after[name]]
        _require(set(changed) <= {part}, '표 확장 대상 이외의 부품 변경')
        old_root, new_root = _xml(before[part]), _xml(after[part])
        old_table, new_table = _table(old_root, path, kind), _table(new_root, path, kind)
        row_tag = f'{{{W if kind == "docx" else HP}}}tr'
        old_rows, new_rows = old_table.findall(row_tag), new_table.findall(row_tag)
        index, delta = plan['row'] - 1, plan['count'] - 1
        _require(index < len(old_rows) and len(new_rows) == len(old_rows) + delta, '행 삭제·불법 행수 또는 원본 행 없음')
        prototype = old_rows[index]
        if kind == 'docx':
            _docx_safe(before, old_root, old_table, prototype, old_rows[index + 1] if index + 1 < len(old_rows) else None)
            used = _docx_ids(before)
            for clone in new_rows[index + 1:index + 1 + delta]:
                _docx_clone(prototype, clone, used)
        else:
            _hwpx_check(before, old_root, new_root, old_table, new_table, index, delta)
        for clone in new_rows[index + 1:index + 1 + delta]:
            new_table.remove(clone)
        _require(_canonical(old_root) == _canonical(new_root), '원본 prototype·뒤행·다른 표·표 밖 내용 변경')
        _require(original.read_bytes() == original_raw and prepared.read_bytes() == prepared_raw, '검수 중 파일 변경')
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f'반복행 확장 검수 실패: 파일 재열기 오류 ({exc})') from exc
    return {'status': 'passed', 'kind': kind, 'format': kind, 'plan': deepcopy(plan),
            'sha': {'original': sha256(original_raw).hexdigest(), 'prepared': sha256(prepared_raw).hexdigest()},
            'original_sha256': sha256(original_raw).hexdigest(), 'prepared_sha256': sha256(prepared_raw).hexdigest(),
            'checks': [{'name': name, 'status': 'passed'} for name in ['package_parts_preserved', 'only_declared_part_changed',
                       'prototype_and_original_rows_preserved', 'exact_cloned_text_properties_and_ids', 'other_content_preserved', 'inputs_unchanged']],
            'rows': {'original': len(old_rows), 'prepared': len(new_rows), 'added': delta},
            'changed_parts': changed, 'native_visual_qa': 'pending', 'legal_compliance_certified': False,
            'scope': '선언한 반복행의 ZIP/XML 구조·문구·속성·허용 주소 및 ID만 독립 대조함. 원본 프로그램의 페이지 배치·렌더링 검증은 별도임.'}
