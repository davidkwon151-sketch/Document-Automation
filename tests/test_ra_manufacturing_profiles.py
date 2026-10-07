"""공식 RA 제조·수입 PDF의 등록 칸만 검증함. 전항목·제출 적합성 인증이 아님."""

from hashlib import sha256
import json
from pathlib import Path
import re

import pdfplumber
from pypdf import PdfReader
import pytest

from agent.output_check import verify_output
from app.form_config import configure_profile, mapping_rows
from templates import TemplateError, fill_compatible_template, load_form_profile

ROOT = Path(__file__).resolve().parents[1]
FORMS = [('1', 'manufacturing_import', 17, 1), ('3', 'manufacturing_import', 22, 9),
         ('7', 'variation', 22, 12), ('7_2', 'manufacturing_import', 28, 9)]
CATALOG = json.loads((ROOT / 'templates/ra_catalog.json').read_text(encoding='utf-8'))


def profile(number):
    return json.loads((ROOT / f'templates/profiles/ra_law_form_{number}_pdf.json').read_text(encoding='utf-8'))


def source(number):
    path = ROOT / f'data/public_templates/ra/ra_law_form_{number}_20260305.pdf'
    if not path.is_file():
        pytest.skip('공식 원본은 재배포하지 않음. 별도 공개 corpus 확보 후 실행함')
    return path


@pytest.mark.parametrize('number,workflow,count,grounded_count', FORMS)
def test_manufacturing_profiles_bind_catalog_and_keep_direct_roles(number, workflow, count, grounded_count):
    record = profile(number)
    entry = next(item for item in CATALOG['documents'] if item['id'] == record['entry_id'])
    assert record['source_sha256'] == entry['sha256']
    assert record['source_filename'] == entry['filename']
    assert record['source_page'] == entry['source_page']
    assert record['source_url'] == entry['source_url']
    assert record['law_effective_date'] == '2026-03-05'
    assert record['domain'] == 'pharmaceutical_ra'
    assert record['ra_workflow'] == workflow
    assert record['citation_mode'] == 'sidecar'
    assert record.get('overflow_mode') is None
    assert record['submission_ready'] is False
    assert record['full_form_filled'] is False
    assert record['mandatory_requirements_verified'] is False
    assert record['legal_compliance_certified'] is False
    assert record['attachments_review_required'] is True
    assert record['verification']['visual_qa']['status'] == 'passed'
    assert record['verification']['visual_qa']['reviewed_pages'] == [1]
    assert len(record['pages']) == 1
    assert len(record['fields']) == count
    assert len({field['id'] for field in record['fields']}) == count
    assert len({field['value_key'] for field in record['fields']}) == count
    grounded = [field for field in record['fields'] if not field['input_required']]
    assert len(grounded) == grounded_count
    assert all(field['value_key'].startswith(('변경 ', '품목명')) for field in grounded)
    for field in record['fields']:
        assert field['kind'] == 'pdf_overlay'
        assert field['input_mode'] == ('user_provided' if field['input_required'] else 'source_grounded')
        assert field['narrative_style_required'] is False
        assert field['location_verified'] is True
        assert field['font_size'] == 7
        assert field['height'] >= field['font_size'] * 1.3
        assert field['max_chars'] > 0
        assert not re.search(r'서명|접수|발급|동의', field['label'])
        if re.search(r'성명|주소|소재지|등록|번호|자격|겸업|생년월일|제조소 명칭|영업소 명칭', field['value_key']):
            assert field['input_required']
    omissions = ' '.join(record['unregistered_fields'])
    assert all(word in omissions for word in ['접수', '서명', '동의', '년·월·일', '제출요건'])
    if number in {'3', '7_2'}:
        assert '긴 인쇄 라벨 옆의 좁은 공간' in omissions
        assert not any(field['value_key'] == '신고인 주민등록 외국인등록번호' for field in record['fields'])
    rows = mapping_rows(record)
    for row in rows:
        row['직접 입력'] = False
    configured, mapping = configure_profile(record, rows)
    assert len(mapping) == count
    assert [field['input_required'] for field in configured['fields']] == [field['input_required'] for field in record['fields']]


@pytest.mark.parametrize('number,workflow,count,grounded_count', FORMS)
def test_registered_rectangles_do_not_cover_original_labels_and_instructions(number, workflow, count, grounded_count):
    record = profile(number)
    with pdfplumber.open(source(number)) as document:
        assert len(document.pages) == 1
        for field in record['fields']:
            assert field['page'] == 1
            page = document.pages[0]
            assert 0 <= field['x'] < field['x'] + field['width'] <= page.width
            assert 0 <= field['y'] < field['y'] + field['height'] <= page.height
            overlap = [char['text'] for char in page.chars if char['text'].strip()
                       and min(char['x1'], field['x'] + field['width']) > max(char['x0'], field['x']) + 0.1
                       and min(char['bottom'], field['y'] + field['height']) > max(char['top'], field['y']) + 0.1]
            assert not overlap, (field['label'], overlap)


@pytest.mark.parametrize('number,workflow,count,grounded_count', FORMS)
def test_partial_fill_is_independently_verified_and_every_original_page_is_preserved(number, workflow, count, grounded_count, tmp_path):
    template = source(number)
    before = template.read_bytes()
    record = load_form_profile(template, f'ra_law_form_{number}_pdf')
    assert record is not None
    assert sha256(before).hexdigest() == record['source_sha256']
    result = fill_compatible_template(template, record['demo_values'], tmp_path / f'{number}.pdf', profile=record)
    report = verify_output(template, result, record['demo_values'], profile=record)
    assert report['status'] == 'passed'
    assert any(check['name'] == 'pdf_overlay_outside_regions_unchanged' for check in report['checks'])
    original, filled = PdfReader(template), PdfReader(result)
    assert len(original.pages) == len(filled.pages) == 1
    normalize = lambda text: re.sub(r'\s+', '', text)
    assert normalize(original.pages[0].extract_text()) in normalize(filled.pages[0].extract_text())
    assert template.read_bytes() == before
    assert '시험값' in filled.pages[0].extract_text()


@pytest.mark.parametrize('number,workflow,count,grounded_count', FORMS)
def test_character_overflow_blocks_without_truncation_or_overwriting_existing_output(number, workflow, count, grounded_count, tmp_path):
    template, record = source(number), profile(number)
    narrative = next(field for field in record['fields'] if not field['input_required'])
    destination = tmp_path / 'existing.pdf'
    destination.write_bytes(b'PREVIOUS USER OUTPUT')
    with pytest.raises(TemplateError, match='길이|제한|넘'):
        fill_compatible_template(template, {narrative['value_key']: '원' * (narrative['max_chars'] + 1)}, destination, profile=record)
    assert destination.read_bytes() == b'PREVIOUS USER OUTPUT'


@pytest.mark.parametrize('number,workflow,count,grounded_count', FORMS)
def test_multiple_lines_exceeding_the_actual_cell_height_are_blocked(number, workflow, count, grounded_count, tmp_path):
    template, record = source(number), profile(number)
    narrative = next(field for field in record['fields'] if not field['input_required'])
    value = '시험\n시험\n시험'
    assert len(value) <= narrative['max_chars']
    with pytest.raises(TemplateError, match='넘침'):
        fill_compatible_template(template, {narrative['value_key']: value}, tmp_path / 'overflow.pdf', profile=record)


def test_manufacturing_profile_cannot_be_used_for_an_import_form(tmp_path):
    manufacturing, importing = source('1'), source('7_2')
    record = load_form_profile(manufacturing, 'ra_law_form_1_pdf')
    assert load_form_profile(importing, 'ra_law_form_1_pdf') is None
    with pytest.raises(TemplateError, match='변경됨|다시 분석'):
        fill_compatible_template(importing, record['demo_values'], tmp_path / 'wrong.pdf', profile=record)
