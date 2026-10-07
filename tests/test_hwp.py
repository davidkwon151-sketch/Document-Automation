from io import BytesIO
import struct
import zlib

import pytest

import parsers.hwp as hwp
from parsers.extended import parse_extended
from parsers.extract import ParseError


def record(tag, level, payload):
    size = len(payload)
    return struct.pack('<I', tag | (level << 10) | (min(size, 4095) << 20)) + (struct.pack('<I', size) if size >= 4095 else b'') + payload


def text(value, level=1):
    return record(66, level - 1, bytes(24)) + record(67, level, (value + '\r').encode('utf-16-le'))


def table():
    data = record(71, 1, b' lbt') + record(77, 2, struct.pack('<IHH', 0, 2, 2))
    for row, col, value in ((0, 0, '항목'), (0, 1, '실적'), (1, 0, '매출'), (1, 1, '120만원')):
        cell = struct.pack('<IIHHHH', 1, 0, col, row, 1, 1) + bytes(18)
        data += record(72, 2, cell) + text(value, 4)
    return data


@pytest.fixture
def container(monkeypatch, tmp_path):
    path = tmp_path / '공공기관자료.hwp'
    path.write_bytes(b'fixture-container')
    streams = {'FileHeader': b'HWP Document File'.ljust(32, b'\x00') + struct.pack('<II', 0x05000302, 0) + bytes(216),
               'BodyText/Section0': text('성과보고서') + table() + text('사업 완료함')}

    class Container:
        def __init__(self, path): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def exists(self, name): return name in streams
        def openstream(self, name): return BytesIO(streams['/'.join(name) if isinstance(name, list) else name])
        def listdir(self): return [name.split('/') for name in streams]

    monkeypatch.setattr(hwp.olefile, 'isOleFile', lambda _: True)
    monkeypatch.setattr(hwp.olefile, 'OleFileIO', Container)
    return path, streams


def test_hwp_source_table_context_and_unknown_physical_page(container):
    path, _ = container
    original = path.read_bytes()
    parsed = parse_extended(path)
    assert set(parsed) == {'파일명', '본문', '표 목록', '페이지/시트 정보'}
    assert parsed['표 목록'][0]['행'] == [['항목', '실적'], ['매출', '120만원']]
    assert '사업 완료함' in parsed['본문']
    assert all(block['페이지'] is None for block in parsed['페이지/시트 정보'])
    assert '구역 1/표 1' in [block['위치'] for block in parsed['페이지/시트 정보']]
    assert path.read_bytes() == original


def test_compressed_sections_preserve_numeric_section_order(container):
    path, streams = container
    streams['FileHeader'] = streams['FileHeader'][:36] + struct.pack('<I', 1) + streams['FileHeader'][40:]
    for key, value in [('BodyText/Section10', text('최종 30만원임')), ('BodyText/Section2', text('중간 20만원임')), ('BodyText/Section0', streams['BodyText/Section0'])]:
        compressor = zlib.compressobj(wbits=-15)
        streams[key] = compressor.compress(value) + compressor.flush()
    parsed = hwp.parse_hwp(path)
    assert parsed['본문'].index('중간') < parsed['본문'].index('최종')


@pytest.mark.parametrize('flag', [2, 4, 16, 256, 1024, 8192])
def test_protected_hwp_is_not_opened_through_body_fallback(container, flag):
    path, streams = container
    streams['FileHeader'] = streams['FileHeader'][:36] + struct.pack('<I', flag) + streams['FileHeader'][40:]
    with pytest.raises(ParseError, match='보호') as caught:
        hwp.parse_hwp(path)
    assert caught.value.code == 'protected_document'


def test_extended_record_and_inline_control_do_not_leak_control_identifiers():
    raw = struct.pack('<H', 2) + b'dces' + bytes(8) + struct.pack('<H', 2)
    assert hwp._text(raw + '매출\t'.encode('utf-16-le')[:4]) == '매출'
    body = '가' * 3000
    assert list(hwp._records(record(67, 1, body.encode('utf-16-le'))))[0][2].decode('utf-16-le') == body
    with pytest.raises(ParseError, match='잘림'):
        list(hwp._records(record(67, 1, b'a' * 5)[:-1]))


def test_decompression_bomb_and_invalid_control_are_rejected(container, monkeypatch):
    path, streams = container
    streams['FileHeader'] = streams['FileHeader'][:36] + struct.pack('<I', 1) + streams['FileHeader'][40:]
    compressor = zlib.compressobj(wbits=-15)
    streams['BodyText/Section0'] = compressor.compress(bytes(2000)) + compressor.flush()
    monkeypatch.setattr(hwp, 'MAX_PACKAGE_BYTES', 1000)
    with pytest.raises(ParseError, match='압축 해제'):
        hwp.parse_hwp(path)
    with pytest.raises(ParseError, match='제어'):
        hwp._text(struct.pack('<H', 9))


def test_non_hwp_and_old_version_are_actionable(container, monkeypatch):
    path, streams = container
    streams['FileHeader'] = streams['FileHeader'][:32] + struct.pack('<I', 0x03000000) + streams['FileHeader'][36:]
    with pytest.raises(ParseError, match='HWP 5'):
        hwp.parse_hwp(path)
    monkeypatch.setattr(hwp.olefile, 'isOleFile', lambda _: False)
    with pytest.raises(ParseError, match='HWPX'):
        hwp.parse_hwp(path)
