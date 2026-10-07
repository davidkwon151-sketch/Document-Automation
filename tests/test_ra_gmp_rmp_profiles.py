"""실제 RA PDF 입력칸을 확인함. 법적 제출 적합성을 인증하지 않음."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pdfplumber
from pypdf import PdfReader
import pytest

from agent.output_check import verify_output
from templates import TemplateError, fill_compatible_template, load_form_profile


ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ('ra_law_form_6_pdf', 'ra_law_form_6_20260305.pdf', 25, 2),
    ('ra_law_form_81_pdf', 'ra_law_form_81_20260305.pdf', 31, 2),
    ('ra_law_form_82_pdf', 'ra_law_form_82_20260305.pdf', 31, 2),
    ('ra_law_rmp_outline_pdf', 'ra_law_rmp_outline_annex9_3_observed.pdf', 16, 1),
]


def registered(entry_id):
    return json.loads((ROOT / 'templates/profiles' / (entry_id + '.json')).read_text(encoding='utf-8'))


def original(filename):
    source = ROOT / 'data/public_templates/ra' / filename
    if not source.is_file():
        pytest.skip('공식 원본은 별도 공개 corpus에서 확보함')
    return source


@pytest.mark.parametrize('entry_id,filename,count,pages', CASES)
def test_registered_ra_profiles_preserve_provenance_direct_input_and_unfilled_limits(entry_id, filename, count, pages):
    profile = registered(entry_id)
    catalog = json.loads((ROOT / 'templates/ra_catalog.json').read_text(encoding='utf-8'))
    entry = next(item for item in catalog['documents'] if item['id'] == entry_id)
    assert profile['source_filename'] == filename
    assert profile['source_sha256'] == entry['sha256']
    assert profile['source_url'] == entry['source_url']
    assert profile['source_page'] == entry['source_page']
    assert len(profile['pages']) == pages
    assert len(profile['fields']) == count
    assert profile['domain'] == 'pharmaceutical_ra'
    assert profile['ra_workflow'] == ('safety_management' if 'rmp' in entry_id else 'gmp' if '8' in entry_id else 'product_approval')
    assert profile['citation_mode'] == 'sidecar'
    assert profile.get('overflow_mode') is None
    assert profile['submission_ready'] is False
    assert profile['full_form_filled'] is False
    assert profile['legal_compliance_certified'] is False
    assert profile['attachments_review_required'] is True
    assert profile['mandatory_requirements_verified'] is False
    assert profile['unsupported_fields']
    assert '실제 제출용' in profile['demo_notice']
    assert set(profile['demo_values']) == {field['value_key'] for field in profile['fields']}
    assert {field['input_mode'] for field in profile['fields']} == {'user_provided', 'source_grounded'}
    for field in profile['fields']:
        assert field['input_required'] == (field['input_mode'] == 'user_provided')
        assert field['narrative_style_required'] is False
        assert field['kind'] == 'pdf_overlay'
        assert field['location_verified'] is True
        assert field['max_chars'] > 0
        assert '서명' not in field['label']
        assert '적합판정 완료' not in profile['demo_values'][field['value_key']]
        if any(word in field['label'] for word in ('성명', '책임자', '대표자', '업체명', '제조업소명', '번호', '연도', '연월일', '예상일')):
            assert field['input_mode'] == 'user_provided'


@pytest.mark.parametrize('entry_id,filename,count,pages', CASES)
def test_registered_rectangles_do_not_overwrite_printed_text_or_each_other(entry_id, filename, count, pages):
    profile = registered(entry_id)
    with pdfplumber.open(original(filename)) as document:
        for index, field in enumerate(profile['fields']):
            page = document.pages[field['page'] - 1]
            assert 0 <= field['x'] < field['x'] + field['width'] <= page.width
            assert 0 <= field['y'] < field['y'] + field['height'] <= page.height
            for word in page.extract_words():
                intersects = (max(field['x'], word['x0']) < min(field['x'] + field['width'], word['x1'])
                              and max(field['y'], word['top']) < min(field['y'] + field['height'], word['bottom']))
                assert not intersects, (field['label'], word['text'])
            for other in profile['fields'][index + 1:]:
                if other['page'] == field['page']:
                    assert not (max(field['x'], other['x']) < min(field['x'] + field['width'], other['x'] + other['width'])
                                and max(field['y'], other['y']) < min(field['y'] + field['height'], other['y'] + other['height']))


@pytest.mark.parametrize('entry_id,filename,count,pages', CASES)
def test_actual_fill_passes_independent_full_page_preservation_and_all_registered_values(entry_id, filename, count, pages, tmp_path):
    source = original(filename)
    before = source.read_bytes()
    profile = load_form_profile(source, entry_id)
    assert profile is not None
    output = fill_compatible_template(source, profile['demo_values'], tmp_path / 'fake_filled.pdf', profile=profile)
    report = verify_output(source, output, profile['demo_values'], profile=profile)
    assert report['status'] == 'passed'
    assert source.read_bytes() == before
    assert sha256(before).hexdigest() == profile['source_sha256']
    assert len(PdfReader(output).pages) == pages
    assert any(check['name'] == 'pdf_overlay_outside_regions_unchanged' and check['pages'] == pages for check in report['checks'])
    with pdfplumber.open(output) as document:
        for field in profile['fields']:
            page = document.pages[field['page'] - 1]
            region = page.crop((field['x'], field['y'], field['x'] + field['width'], field['y'] + field['height']))
            assert ''.join(profile['demo_values'][field['value_key']].split()) in ''.join((region.extract_text() or '').split())


@pytest.mark.parametrize('entry_id,filename,count,pages', CASES)
def test_overflow_and_wrong_source_are_rejected_without_truncating_or_overwriting(entry_id, filename, count, pages, tmp_path):
    source = original(filename)
    profile = registered(entry_id)
    field = next(item for item in profile['fields'] if item['input_mode'] == 'source_grounded' and not item.get('validation'))
    values = {field['value_key']: '가' * (field['max_chars'] + 1)}
    output = tmp_path / 'overflow.pdf'
    output.write_bytes(b'previous-output')
    with pytest.raises(TemplateError, match='길이|넘침'):
        fill_compatible_template(source, values, output, profile=profile)
    assert output.read_bytes() == b'previous-output'
    assert len(values[field['value_key']]) == field['max_chars'] + 1
    other_filename = next(case[1] for case in CASES if case[1] != filename)
    other = original(other_filename)
    assert load_form_profile(other, entry_id) is None
    with pytest.raises(TemplateError, match='변경|다시 분석'):
        fill_compatible_template(other, profile['demo_values'], tmp_path / 'wrong.pdf', profile=profile)


@pytest.mark.parametrize('entry_id,filename,count,pages', CASES)
def test_line_break_overflow_is_blocked_even_when_character_count_fits(entry_id, filename, count, pages, tmp_path):
    profile = registered(entry_id)
    field = next(item for item in profile['fields'] if item['input_mode'] == 'source_grounded' and not item.get('validation'))
    value = '가\n' * (int(field['height'] // (field['font_size'] * 1.3)) + 1)
    assert len(value) <= field['max_chars']
    target = tmp_path / 'lines.pdf'
    with pytest.raises(TemplateError, match='넘침'):
        fill_compatible_template(original(filename), {field['value_key']: value}, target, profile=profile)
    assert not target.exists()


def test_rmp_observed_version_remains_unknown_and_printed_safety_table_is_unfilled():
    profile = registered('ra_law_rmp_outline_pdf')
    assert profile['law_effective_date'] is None
    assert profile['form_version'] is None
    assert profile['currentness'] == 'unknown'
    assert '미확인' in profile['version_label']
    assert any('⑬' in item for item in profile['unsupported_fields'])
    assert any('추정하지 않음' in warning for warning in profile['warnings'])
    assert all(not (field['y'] < 532.1 and field['y'] + field['height'] > 420.4) for field in profile['fields'])


def test_gmp_wrong_totals_are_blocked_without_recomputing_user_values(tmp_path):
    source = original('ra_law_form_81_20260305.pdf')
    profile = registered('ra_law_form_81_pdf')
    values = {'제조소 작업소 면적': '10', '제조소 보관소 면적': '20', '제조소 시험실 면적': '0',
              '제조소 기타 부대시설 면적': '0', '제조소 합계 면적': '99',
              '작업인원 제조부서': '1', '작업인원 품질 보증부서': '2', '작업인원 기타': '0', '작업인원 합계': '9'}
    original_values = deepcopy(values)
    with pytest.raises(TemplateError, match='합계'):
        fill_compatible_template(source, values, tmp_path / 'wrong_totals.pdf', profile=profile)
    assert values == original_values
    assert not (tmp_path / 'wrong_totals.pdf').exists()
    values = values | {'제조소 합계 면적': '30', '작업인원 합계': '3'}
    output = fill_compatible_template(source, values, tmp_path / 'no_computation.pdf', profile=profile)
    assert verify_output(source, output, values, profile=profile)['status'] == 'passed'
    with pdfplumber.open(output) as document:
        for label in ['제조소 합계 면적', '작업인원 합계']:
            field = next(item for item in profile['fields'] if item['value_key'] == label)
            region = document.pages[0].crop((field['x'], field['y'], field['x'] + field['width'], field['y'] + field['height']))
            assert region.extract_text().strip() == values[label]


def test_missing_selected_required_value_stays_blocking(tmp_path):
    profile = deepcopy(registered('ra_law_form_82_pdf'))
    field = profile['fields'][0]
    field['required'] = True
    source = original('ra_law_form_82_20260305.pdf')
    with pytest.raises(TemplateError, match='누락'):
        fill_compatible_template(source, {}, tmp_path / 'missing.pdf', profile=profile)
