"""공식 RA 빈 서식의 부분 기입·역할 보존 검사임. 제출 적합성 인증이 아님."""

from hashlib import sha256
import json
from pathlib import Path
import re

import pdfplumber
from pypdf import PdfReader
import pytest

from agent.output_check import verify_output
from templates import TemplateError, fill_compatible_template, load_form_profile


ROOT = Path(__file__).resolve().parents[1]
FORMS = [('32_2', 'clinical_trial', 69, 2), ('41', 'product_approval', 19, 1),
         ('57_2', 'manufacturing_import', 26, 1)]


def _profile(number):
    return json.loads((ROOT / f'templates/profiles/ra_law_form_{number}_pdf.json').read_text(encoding='utf-8'))


def _source(number):
    path = ROOT / f'data/public_templates/ra/ra_law_form_{number}_20260305.pdf'
    if not path.is_file():
        pytest.skip('별도 수집한 공식 원본 corpus가 필요함')
    return path


@pytest.mark.parametrize('number,workflow,count,pages', FORMS)
def test_registered_ra_profiles_bind_source_and_mark_partial_scope(number, workflow, count, pages):
    profile = _profile(number)
    assert profile['domain'] == 'pharmaceutical_ra'
    assert profile['ra_workflow'] == workflow
    assert profile['resource_kind'] == 'blank_form'
    assert profile['citation_mode'] == 'sidecar'
    assert profile['law_effective_date'] == '2026-03-05'
    assert len(profile['fields']) == count
    assert len({field['id'] for field in profile['fields']}) == count
    assert len(profile['pages']) == pages
    assert profile['full_form_filled'] is False
    assert profile['submission_ready'] is False
    assert profile['attachments_review_required'] is True
    assert profile['verification']['legal_compliance_certified'] is False
    assert profile['verification']['visual_qa']['status'] == 'passed'
    assert profile['verification']['visual_qa']['reviewed_pages'] == list(range(1, pages + 1))
    assert 'overflow_mode' not in profile
    assert '실제 제출용이 아니' in profile['demo_notice']
    assert any('서명' in warning for warning in profile['warnings'])
    assert any('칸을 넘는 전체 원문' in warning for warning in profile['warnings'])
    for field in profile['fields']:
        assert field['kind'] == 'pdf_overlay'
        assert field['input_required'] == (field['input_mode'] == 'user_provided')
        assert field['narrative_style_required'] is False
        assert field['location_verified'] is True
        assert field['max_chars'] > 0
        assert len(profile['demo_values'][field['value_key']]) <= field['max_chars']
        assert not any(text in field['label'] for text in ['서명', '등록코드', '접수번호'])
        if field.get('identifier_role') or field.get('value_type') == 'date_literal':
            assert field['input_mode'] == 'user_provided'


@pytest.mark.parametrize('number,workflow,count,pages', FORMS)
def test_ra_partial_fill_keeps_every_original_page_guide_and_pixels(number, workflow, count, pages, tmp_path):
    source = _source(number)
    before = source.read_bytes()
    profile = load_form_profile(source, f'ra_law_form_{number}_pdf')
    assert profile == _profile(number)
    assert sha256(before).hexdigest() == profile['source_sha256']
    result = fill_compatible_template(source, profile['demo_values'], tmp_path / f'{number}.pdf', profile=profile)
    check = verify_output(source, result, profile['demo_values'], profile=profile)
    assert check['status'] == 'passed'
    pixel_check = next(item for item in check['checks'] if item['name'] == 'pdf_overlay_outside_regions_unchanged')
    assert pixel_check['pages'] == pages
    original, written = PdfReader(source), PdfReader(result)
    assert len(original.pages) == len(written.pages) == pages
    for first, second in zip(original.pages, written.pages):
        assert re.sub(r'\s+', '', first.extract_text()) in re.sub(r'\s+', '', second.extract_text())
    assert source.read_bytes() == before


@pytest.mark.parametrize('number,workflow,count,pages', FORMS)
def test_long_grounded_content_is_blocked_without_changing_source_or_existing_output(number, workflow, count, pages, tmp_path):
    source = _source(number)
    before = source.read_bytes()
    profile = _profile(number)
    field = next(item for item in profile['fields'] if not item['input_required'] and not item.get('validation'))
    output = tmp_path / 'previous.pdf'
    output.write_bytes(b'PREVIOUS USER DOCUMENT')
    with pytest.raises(TemplateError, match='길이|제한|넘'):
        fill_compatible_template(source, {field['value_key']: 'A' * (field['max_chars'] + 1)}, output, profile=profile)
    assert output.read_bytes() == b'PREVIOUS USER DOCUMENT'
    assert source.read_bytes() == before


def test_clinical_counts_keep_phase_state_training_and_original_unit_roles(tmp_path):
    source = _source('32_2')
    profile = _profile('32_2')
    count_fields = [field for field in profile['fields'] if field.get('trial_state')]
    assert len(count_fields) == 18
    assert {field['trial_state'] for field in count_fields} == {'IRB 당해년도 승인', '진행중', '당해년도 종료'}
    for state in {field['trial_state'] for field in count_fields}:
        subset = [field for field in count_fields if field['trial_state'] == state]
        assert {field['phase'] for field in subset if field['sponsor_kind'] == '의뢰자주도'} == {'1상', '2상', '3상', '4상'}
        assert {field['approval_scope'] for field in subset if field['sponsor_kind'] == '연구자주도'} == {'식약처승인대상', '식약처승인비대상'}
    assert all(field['display_unit'] == '건' and field['input_mode'] == 'source_grounded' for field in count_fields)
    physician = [field for field in profile['fields'] if field.get('qualification') == '의사']
    assert {(field['investigator_role'], field['count_role']) for field in physician} == {
        ('시험책임자', '전체'), ('시험책임자', '교육이수'), ('시험담당자', '전체'), ('시험담당자', '교육이수')}
    assert all(field['display_unit'] == '명' for field in physician)
    crc = [field for field in profile['fields'] if field.get('personnel_group') == 'CRC']
    assert {field['employment'] for field in crc} == {'정규직', '비정규직'}
    cycle = next(field for field in profile['fields'] if field['value_key'] == 'IRB 정규심의주기')
    assert cycle['display_unit'] == '주'
    selected = physician + count_fields
    values = {field['value_key']: str(index + 1) for index, field in enumerate(selected)}
    # Distinct valid headcounts preserve the education-subset relationship.
    for index, field in enumerate(physician):
        values[field['value_key']] = str(4 + index // 2 if field['count_role'] == '전체' else 1 + index // 2)
    result = fill_compatible_template(source, values, tmp_path / 'distinct_clinical_counts.pdf', profile=profile)
    assert verify_output(source, result, values, profile=profile)['status'] == 'passed'
    with pdfplumber.open(result) as document:
        for field in selected:
            crop = document.pages[field['page'] - 1].crop((field['x'], field['y'], field['x'] + field['width'], field['y'] + field['height']))
            assert crop.extract_text().strip() == values[field['value_key']]


def test_material_requests_and_foreign_site_change_rows_remain_distinct():
    materials = [field for field in _profile('41')['fields'] if field.get('material_kind')]
    assert len(materials) == 6
    assert len({field['value_key'] for field in materials}) == 6
    assert all(field['input_mode'] == 'source_grounded' for field in materials)
    foreign = _profile('57_2')
    assert foreign['supported_ra_workflows'] == ['manufacturing_import', 'variation']
    changes = [field for field in foreign['fields'] if field.get('change_row')]
    assert {(field['change_row'], field['change_column']) for field in changes} == {
        (row, column) for row in [1, 2] for column in ['종전 내용', '변경 내용', '변경 사유']}
    assert all(field['input_mode'] == 'source_grounded' for field in changes)
    assert all(field['input_mode'] == 'user_provided' for field in foreign['fields'] if '해외제조소' in field['label'])
    assert all(field['input_mode'] == 'user_provided' for field in foreign['fields'] if field.get('identifier_role'))
    assert any('기입금지' in warning for warning in foreign['warnings'])


def test_profile_for_foreign_site_cannot_target_prior_review_form(tmp_path):
    profile = _profile('57_2')
    source = _source('41')
    before = source.read_bytes()
    assert load_form_profile(source, profile['entry_id']) is None
    with pytest.raises(TemplateError, match='달라짐|다시 분석'):
        fill_compatible_template(source, profile['demo_values'], tmp_path / 'mismatched.pdf', profile=profile)
    assert source.read_bytes() == before
