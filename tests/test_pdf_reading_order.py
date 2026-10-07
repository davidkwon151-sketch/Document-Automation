"""PDF 내용 순서를 회사명 예외 없이 보존하며 실제 원문과 대조함."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re

import pdfplumber
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.constants import UserAccessPermissions
from reportlab.pdfgen import canvas

from parsers import parse_file
from parsers import extract

ROOT = Path(__file__).resolve().parents[1]


def normalized(text):
    return re.sub(r"\s+", "", text)


def test_two_columns_keep_population_dose_and_interval_together(tmp_path):
    path = tmp_path / 'unrelated_two_columns.pdf'
    pdf = canvas.Canvas(str(path))
    for x, lines in ((40, ['Adult dose', 'Initial 8 mg/kg', 'Maintenance 6 mg/kg', 'Every 3 weeks.']),
                     (310, ['Child dose', 'Initial 4 mg/kg', 'Maintenance 2 mg/kg', 'Every 1 week.'])):
        for index, text in enumerate(lines):
            pdf.drawString(x, 760 - index * 20, text)
    pdf.showPage()
    pdf.drawString(40, 760, 'Storage: 20 degrees C')
    pdf.showPage()
    pdf.save()
    original = sha256(path.read_bytes()).hexdigest()
    parsed = parse_file(path)
    first, second = parsed['페이지/시트 정보']
    adult = 'Adult dose\nInitial 8 mg/kg\nMaintenance 6 mg/kg\nEvery 3 weeks.'
    child = 'Child dose\nInitial 4 mg/kg\nMaintenance 2 mg/kg\nEvery 1 week.'
    assert adult in first['본문'] and child in first['본문']
    assert first['본문'].index(adult) < first['본문'].index(child)
    metadata = first['PDF 읽기순서']
    assert normalized(adult) not in normalized(metadata['물리적 줄순서 본문'])
    assert Counter(normalized(metadata['물리적 줄순서 본문'])) == Counter(normalized(first['본문']))
    assert metadata['콘텐츠 스트림 본문'] == first['본문']
    assert metadata['선택 방식'] == 'PDF 콘텐츠 스트림'
    assert metadata['문자와 빈도 보존'] is True and metadata['확인 필요'] is True
    assert [first['페이지'], second['페이지']] == [1, 2]
    assert second['본문'] == 'Storage: 20 degrees C'
    assert sha256(path.read_bytes()).hexdigest() == original


def test_single_column_contract_remains_unchanged():
    result = parse_file(ROOT / 'samples/source.pdf')
    assert set(result) == {'파일명', '본문', '표 목록', '페이지/시트 정보'}
    assert all(set(block) == {'페이지', '시트', '위치', '본문', '표 목록'}
               for block in result['페이지/시트 정보'])
    assert result['표 목록'][0]['행'] == [['항목', '실적'], ['교육 참석', '95명']]


@pytest.mark.parametrize('physical,flow,warning', [
    ('Adult 8 mg/kg', 'Adult 8', '문자와 빈도'),
    ('Adult 8 mg/kg', '', '문자와 빈도'),
    ('Adult 8 mg/kg', 'Adult 8 mg/kg mg/kg', '문자와 빈도'),
    ('Adult 8 mg/kg\x00', 'Adult 8 mg/kg\x00', '제어문자'),
])
def test_text_flow_loss_duplication_or_control_is_not_accepted(tmp_path, monkeypatch, physical, flow, warning):
    path = tmp_path / 'reading_order.pdf'
    pdf = canvas.Canvas(str(path))
    pdf.drawString(40, 760, 'Original input')
    pdf.save()
    class Page:
        def extract_text(self, *, use_text_flow=False):
            return flow if use_text_flow else physical
        def extract_tables(self):
            return [[['항목', '값'], ['함량', '8 mg/kg']]]
    class Document:
        pages = [Page()]
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
    monkeypatch.setattr(extract.pdfplumber, 'open', lambda _: Document())
    with pytest.warns(UserWarning, match=warning):
        result = parse_file(path)
    block = result['페이지/시트 정보'][0]
    assert block['본문'] == physical
    assert block['PDF 읽기순서']['선택 방식'] == '물리적 줄순서'
    assert (not block['PDF 읽기순서']['문자와 빈도 보존']
            or not block['PDF 읽기순서']['제어문자 없음'])
    assert block['PDF 읽기순서']['콘텐츠 스트림 본문'] == flow
    assert block['검증 필요'] is True and block['불확실한 항목']
    assert all(table['검증 필요'] for table in result['표 목록'])
    from agent.retrieve import chunk_documents
    from agent.pipeline import review_result, build_downloads
    sources = chunk_documents([result])
    assert sources and all(source['requires_verification'] for source in sources)
    first = sources[0]
    sentence = f"{first['text']}임 [{first['source_id']}]"
    output = {'status': 'needs_revision', 'sources': sources,
              'draft': {'제목': '원문 확인', '요약': '□ ' + sentence, '본문': '○ ' + sentence}}
    checked = review_result(output)
    assert checked['blocking']
    assert any(issue['code'] == 'ocr_unverified' for issue in checked['warnings'])
    with pytest.raises(ValueError, match='다운로드'):
        build_downloads(output, confirmed=True)
    if '\x00' not in physical:
        output['verified_source_ids'] = [source['source_id'] for source in sources]
        assert not review_result(output)['blocking']


def records(manifest):
    path = ROOT / 'evals' / manifest
    return json.loads(path.read_text(encoding='utf-8'))['sources']


@pytest.mark.parametrize('record', records('ra_korean_sources.json'), ids=lambda item: item['id'])
def test_actual_korean_insert_complete_quotes_and_pages_survive(record):
    path = ROOT / record['path']
    if not path.is_file():
        pytest.skip('Git 제외된 공식 원본 스냅샷이 현재 환경에 없음')
    reader = PdfReader(path)
    if reader.is_encrypted and not ((reader.user_access_permissions or 0) & UserAccessPermissions.EXTRACT):
        pytest.skip('공개 열람은 가능하나 원본 일반 EXTRACT 권한이 없어 자동 대조를 보류함')
    result = parse_file(path)
    blocks = result['페이지/시트 정보']
    assert [block['페이지'] for block in blocks] == list(range(1, record['page_count'] + 1))
    for fact in record['facts']:
        assert normalized(fact['exact_quote']) in normalized(blocks[fact['page'] - 1]['본문'])
    company = record['company']
    assert normalized(company['role_exact_quote']) in normalized(blocks[company['role_heading_page'] - 1]['본문'])
    assert sha256(path.read_bytes()).hexdigest() == record['sha256']
    if any('PDF 읽기순서' in block for block in blocks):
        assert all(block['PDF 읽기순서']['문자와 빈도 보존']
                   for block in blocks if 'PDF 읽기순서' in block)


def test_actual_protected_korean_snapshot_remains_blocked_in_evaluator():
    record = next(item for item in records('ra_korean_sources.json') if item['id'] == 'ra-kr-aerius-5')
    path = ROOT / record['path']
    if not path.is_file():
        pytest.skip('Git 제외된 공식 원본 스냅샷이 현재 환경에 없음')
    reader = PdfReader(path)
    assert reader.is_encrypted
    assert not ((reader.user_access_permissions or 0) & UserAccessPermissions.EXTRACT)
    from evals.ra_public import read_source
    with pytest.raises(ValueError, match='추출.*보류'):
        read_source(record)
    from parsers import ParseError
    with pytest.raises(ParseError, match='추출.*보류') as caught:
        parse_file(path)
    assert caught.value.code == 'protected_document'
    assert sha256(path.read_bytes()).hexdigest() == record['sha256']


@pytest.mark.parametrize('password,allow_extraction', [('', True), ('', False), ('secret', True), ('secret', False)])
def test_pdf_protection_requires_normal_public_read_access_and_extract_permission(tmp_path, monkeypatch, password, allow_extraction):
    from parsers import ParseError
    from parsers.extended import parse_extended
    base = tmp_path / 'original.pdf'
    pdf = canvas.Canvas(str(base))
    pdf.drawString(40, 760, 'Product 75 mg capsule')
    pdf.save()
    path = tmp_path / 'protected.pdf'
    writer = PdfWriter(clone_from=base)
    permissions = UserAccessPermissions.PRINT | UserAccessPermissions.EXTRACT_TEXT_AND_GRAPHICS
    if allow_extraction:
        permissions |= UserAccessPermissions.EXTRACT
    writer.encrypt(user_password=password, owner_password='owner-secret', permissions_flag=permissions)
    with path.open('wb') as stream:
        writer.write(stream)
    original_sha = sha256(path.read_bytes()).hexdigest()
    if not password and allow_extraction:
        parsed = parse_file(path)
        assert '75 mg' in parsed['본문']
    else:
        # No text extraction, OCR fallback or native conversion is attempted.
        monkeypatch.setattr(extract.pdfplumber, 'open', lambda _: pytest.fail('Forbidden PDF extraction'))
        with pytest.raises(ParseError, match='보호.*보류') as caught:
            parse_extended(path, client=None, allow_ocr=True)
        assert caught.value.code == 'protected_document'
    assert sha256(path.read_bytes()).hexdigest() == original_sha


@pytest.mark.parametrize('record', records('ra_public_sources.json'), ids=lambda item: item['id'])
def test_actual_ema_selected_full_paragraphs_survive_same_generic_method(record):
    path = ROOT / record['path']
    if not path.is_file():
        pytest.skip('Git 제외된 공식 원본 스냅샷이 현재 환경에 없음')
    wanted_pages = {fact['page'] for fact in record['facts']}
    # Read only the referenced pages here: full-file parsing is checked by the
    # existing RA integration evaluator and avoids repeating 300+ page parsing.
    with pdfplumber.open(path) as document:
        texts = {number: document.pages[number-1].extract_text(use_text_flow=True) or ''
                 for number in wanted_pages}
    for fact in record['facts']:
        assert normalized(fact['exact_quote']) in normalized(texts[fact['page']])
    assert sha256(path.read_bytes()).hexdigest() == record['sha256']
