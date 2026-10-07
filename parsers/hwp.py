"""Read-only HWP 5 text and table extraction, without launching Hancom.

본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
Specification: https://www.hancom.com/support/downloadCenter/hwpOwpml
Physical page numbers require rendering and are deliberately left unknown.
"""

import re
import struct
import zlib

import olefile

from .extract import ParseError, _path, _block, _table, _result, MAX_PACKAGE_BYTES


def _records(data):
    offset = 0
    while offset < len(data):
        if len(data) - offset < 4:
            raise ParseError('HWP 레코드 헤더가 잘림')
        value = struct.unpack_from('<I', data, offset)[0]
        offset += 4
        size = value >> 20
        if size == 4095:
            if len(data) - offset < 4:
                raise ParseError('HWP 확장 길이가 잘림')
            size = struct.unpack_from('<I', data, offset)[0]
            offset += 4
        if offset + size > len(data):
            raise ParseError('HWP 레코드 본문이 잘림')
        yield value & 1023, (value >> 10) & 1023, data[offset:offset + size]
        offset += size


def _text(data):
    if len(data) % 2:
        raise ParseError('HWP 텍스트 길이가 잘못됨')
    pieces, offset = [], 0
    while offset < len(data):
        code = struct.unpack_from('<H', data, offset)[0]
        if 1 <= code <= 23 and code not in {10, 13}:
            if offset + 16 > len(data):
                raise ParseError('HWP 제어 문자 정보가 잘림')
            if code == 9:
                pieces.append(b'\t\x00')
            offset += 16
        else:
            if code in {10, 13}:
                pieces.append(b'\n\x00')
            elif code in {30, 31}:
                pieces.append(b' \x00')
            elif code == 24:
                pieces.append(b'-\x00')
            elif code >= 32:
                pieces.append(data[offset:offset + 2])
            offset += 2
    try:
        return b''.join(pieces).decode('utf-16-le').strip()
    except UnicodeError as exc:
        raise ParseError('HWP 유니코드가 잘못됨') from exc


def _section(data, section):
    blocks, tables, stack = [], [], []
    paragraph = 0
    for tag, level, payload in _records(data):
        while stack and level <= stack[-1]['level']:
            stack.pop()
        if tag == 71 and payload[:4] == b' lbt':
            stack.append({'level': level, 'table': None, 'cell': None, 'cell_level': None})
        if tag == 77 and stack:
            if len(payload) < 8:
                raise ParseError('HWP 표 정보가 잘림')
            rows, cols = struct.unpack_from('<HH', payload, 4)
            if not rows or not cols or rows * cols > 100_000:
                raise ParseError('HWP 표 크기가 잘못됨', 'too_large')
            location = f'구역 {section}/표 {len(tables) + 1}'
            table = _table([[''] * cols for _ in range(rows)], location)
            table['병합'] = []
            tables.append(table)
            block = _block('', location, tables=[table])
            blocks.append(block)
            stack[-1].update(table=table, block=block, cell=None)
        if tag == 72 and stack and stack[-1]['table'] is not None:
            item = stack[-1]
            # Common HWP writers use a 32-bit paragraph count (8-byte list header).
            # Also accept the 6-byte header described in the published table 65.
            candidates = []
            for offset in (8, 6):
                if len(payload) < offset + 26:
                    continue
                col, row, col_span, row_span = struct.unpack_from('<HHHH', payload, offset)
                matrix = item['table']['행']
                if 1 <= col_span and 1 <= row_span and row + row_span <= len(matrix) and col + col_span <= len(matrix[0]):
                    candidates.append((row, col, row_span, col_span))
            if not candidates:
                item['cell'] = None  # Caption lists are not table cells.
            else:
                row, col, rs, cs = candidates[0]
                item.update(cell=(row, col), cell_level=level)
                if rs > 1 or cs > 1:
                    item['table']['병합'].append({'행': row + 1, '열': col + 1, '행 병합': rs, '열 병합': cs})
        elif tag == 66:
            paragraph += 1
        elif tag == 67:
            text = _text(payload)
            if not text:
                continue
            cells = [item for item in stack if item['table'] is not None and item['cell'] is not None and level > item['cell_level']]
            if cells:
                for item in cells:
                    row, col = item['cell']
                    previous = item['table']['행'][row][col]
                    item['table']['행'][row][col] = previous + ('\n' if previous else '') + text
            else:
                blocks.append(_block(text, f'구역 {section}/문단 {paragraph or 1}'))
    for block in blocks:
        if block['표 목록']:
            block['본문'] = '\n'.join(' | '.join(row) for row in block['표 목록'][0]['행'])
        block['추출 방식'] = 'HWP 5 원문 레코드'
    return blocks, tables


def parse_hwp(path):
    path = _path(path)
    if not olefile.isOleFile(str(path)):
        raise ParseError('HWP 5 복합 파일이 아님. HWPX로 저장한 원본이 필요함', 'unsupported_format')
    try:
        with olefile.OleFileIO(str(path)) as document:
            if not document.exists('FileHeader'):
                raise ParseError('HWP 파일 인식 정보가 없음')
            header = document.openstream('FileHeader').read(256)
            if len(header) < 40 or header[:32].rstrip(b'\x00') != b'HWP Document File':
                raise ParseError('HWP 파일 인식 정보가 잘못됨')
            version, flags = struct.unpack_from('<II', header, 32)
            if version >> 24 != 5:
                raise ParseError('HWP 5 형식만 원문 추출을 지원함', 'unsupported_format')
            if flags & ((1 << 1) | (1 << 2) | (1 << 4) | (1 << 8) | (1 << 10) | (1 << 13)):
                raise ParseError('암호·DRM·보호/배포용 HWP는 보안을 해제한 원본 또는 HWPX가 필요함', 'protected_document')
            sections = [name for name in document.listdir() if len(name) == 2 and name[0] == 'BodyText' and re.fullmatch(r'Section\d+', name[1])]
            sections.sort(key=lambda name: int(name[1][7:]))
            if not sections or len(sections) > 1000:
                raise ParseError('HWP 본문 구역이 없거나 지나치게 많음')
            blocks, tables, total = [], [], 0
            for number, name in enumerate(sections, 1):
                raw = document.openstream(name).read(MAX_PACKAGE_BYTES + 1)
                if len(raw) > MAX_PACKAGE_BYTES:
                    raise ParseError('HWP 구역 크기가 너무 큼', 'too_large')
                if flags & 1:
                    decoder = zlib.decompressobj(-15)
                    raw = decoder.decompress(raw, MAX_PACKAGE_BYTES - total + 1)
                    if decoder.unconsumed_tail or len(raw) + total > MAX_PACKAGE_BYTES:
                        raise ParseError('HWP 압축 해제 크기는 100 MiB 이하이어야 함', 'too_large')
                    if not decoder.eof:
                        raise ParseError('HWP 압축 본문이 잘림')
                total += len(raw)
                if total > MAX_PACKAGE_BYTES:
                    raise ParseError('HWP 본문 크기는 100 MiB 이하이어야 함', 'too_large')
                section_blocks, section_tables = _section(raw, number)
                blocks.extend(section_blocks)
                tables.extend(section_tables)
            return _result(path, blocks, tables)
    except ParseError:
        raise
    except (OSError, ValueError, struct.error, zlib.error) as exc:
        raise ParseError(f'HWP 구조를 읽을 수 없음: {path.name}') from exc
