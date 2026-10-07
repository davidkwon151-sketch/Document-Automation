from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
import json

import httpx
import pytest
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from parsers.extended import parse_extended, render_pdf_page
from parsers.extract import ParseError
from llm.client import LLMClient
from agent.retrieve import chunk_documents
from agent.pipeline import review_result, build_downloads

ROOT = Path(__file__).resolve().parents[1]


def image_bytes():
    buffer = BytesIO()
    Image.new('RGB', (120, 50), 'white').save(buffer, format='PNG')
    return buffer.getvalue()


def test_csv_cp949_and_text_preserve_rows_and_source_location(tmp_path):
    path = tmp_path / '원자료.csv'
    path.write_bytes('항목,금액\n매출,120만원'.encode('cp949'))
    result = parse_extended(path)
    assert result['표 목록'][0]['행'][1] == ['매출', '120만원']
    assert result['페이지/시트 정보'][1]['위치'] == '행 2'
    text = tmp_path / '지시.txt'
    text.write_text('첫 줄\n\n매출 120만원임', encoding='utf-8')
    assert parse_extended(text)['페이지/시트 정보'][1]['위치'] == '줄 3'


def test_scanned_pdf_requires_reading_and_marks_every_ocr_source_for_verification(tmp_path):
    path = tmp_path / '스캔.pdf'
    pdf = canvas.Canvas(str(path), pagesize=(200, 200))
    pdf.drawImage(ImageReader(BytesIO(image_bytes())), 10, 10, 150, 100)
    pdf.save()
    with pytest.raises(ParseError, match='스캔'):
        parse_extended(path)
    client = Mock()
    client.read_image_json.return_value = {'본문': '매출 120만원임', '표 목록': [[['항목', '금액'], ['매출', '120만원']]], '불확실한 항목': ['작은 글씨']}
    result = parse_extended(path, client=client)
    assert set(result) == {'파일명', '본문', '표 목록', '페이지/시트 정보'}
    assert result['페이지/시트 정보'][0]['페이지'] == 1
    assert render_pdf_page(path).startswith(b'\x89PNG')
    assert all(source['requires_verification'] for source in chunk_documents([result]))
    assert client.read_image_json.call_count == 1
    with pytest.raises(ParseError):
        render_pdf_page(path, 2)


def test_image_missing_tables_or_malformed_cells_fails(tmp_path):
    path = tmp_path / '사진.png'
    path.write_bytes(image_bytes())
    client = Mock()
    client.read_image_json.return_value = {'본문': '매출', '표 목록': [{'value': 'bad'}]}
    with pytest.raises(ParseError, match='표'):
        parse_extended(path, client=client)
    with pytest.raises(ParseError, match='꺼져'):
        parse_extended(path, client=client, allow_ocr=False)


def test_pdf_form_values_are_evidence_even_when_not_in_text_stream(tmp_path):
    from templates import analyze_template, fill_compatible_template
    path = ROOT / 'samples' / 'sample_company_form.pdf'
    result_path = tmp_path / '작성.pdf'
    profile = analyze_template(path)
    fill_compatible_template(path, {'제목': '성과', '요약': '완료', '본문': '매출 120만원임'}, result_path, profile=profile)
    parsed = parse_extended(result_path)
    assert '120만원' in parsed['본문']
    assert any('PDF 입력칸' in block['위치'] for block in parsed['페이지/시트 정보'])


def test_image_api_uses_central_retry_boundary_and_does_not_log_pixels(caplog):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'id': 'resp_ocr', 'object': 'response', 'created_at': 1, 'model': 'mock', 'status': 'completed', 'output': [{'id': 'm1', 'type': 'message', 'status': 'completed', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': '{"본문":"매출 120만원임"}', 'annotations': []}]}]})
    client = LLMClient(api_key='mock-key', transport=httpx.MockTransport(handler))
    assert client.read_image_json(image_bytes())['본문'].startswith('매출')
    assert requests[0]['input'][0]['content'][1]['image_url'].startswith('data:image/png;base64,')
    assert requests[0]['store'] is False
    assert 'base64' not in caplog.text
    with pytest.raises(ValueError):
        client.read_image_json(b'not-an-image')


def test_unverified_ocr_cannot_be_exported_until_explicitly_checked():
    source = {'source_id': 'S1', 'filename': '스캔.pdf', 'text': '매출 120만원임', 'page': 1, 'location': '1쪽', 'requires_verification': True}
    draft = {'제목': '결과보고서', '요약': '□ 매출 120만원임 [S1]', '본문': '○ 매출 120만원임 [S1]'}
    result = {'status': 'needs_revision', 'draft': draft, 'sources': [source]}
    assert review_result(result)['blocking']
    with pytest.raises(ValueError):
        build_downloads(result, confirmed=True)
    result['verified_source_ids'] = ['S1']
    assert not review_result(result)['blocking']
    assert set(build_downloads(result, confirmed=True)) == {'docx', 'hwpx'}


def test_parallel_scanned_pages_keep_source_page_order(tmp_path):
    path = tmp_path / '여러쪽.pdf'
    pdf = canvas.Canvas(str(path), pagesize=(200, 200))
    for _ in range(5):
        pdf.drawImage(ImageReader(BytesIO(image_bytes())), 10, 10, 150, 100)
        pdf.showPage()
    pdf.save()
    client = Mock()
    client.read_image_json.side_effect = lambda image, *, mime, payload: {'본문': f"{payload['페이지']}쪽 실적", '표 목록': [], '불확실한 항목': []}
    parsed = parse_extended(path, client=client)
    assert [block['본문'] for block in parsed['페이지/시트 정보']] == [f'{n}쪽 실적' for n in range(1, 6)]
    assert client.read_image_json.call_count == 5
