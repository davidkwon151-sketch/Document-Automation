"""현행 공식 RA 빈 서식의 부분 기입 검증임. 제출 적합성 인증이 아님."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from pypdf import PdfReader
import pytest

from agent.output_check import verify_output
from templates import TemplateError, fill_compatible_template, load_form_profile


ROOT = Path(__file__).resolve().parents[1]
FORMS = [('16', 'dmf', 18), ('20', 'product_approval', 16), ('32', 'safety_management', 14)]


def _source(number):
    path = ROOT / f'data/public_templates/ra/ra_law_form_{number}_20260305.pdf'
    if not path.is_file():
        pytest.skip('공식 원본 corpus를 별도로 내려받아야 함')
    return path


def _normalized(text):
    return re.sub(r'\s+', '', text)


@pytest.mark.parametrize('number,workflow,count', FORMS)
def test_new_ra_profiles_separate_verified_source_facts_and_direct_inputs(number, workflow, count):
    profile = json.loads((ROOT / f'templates/profiles/ra_law_form_{number}_pdf.json').read_text(encoding='utf-8'))
    assert profile['domain'] == 'pharmaceutical_ra'
    assert profile['ra_workflow'] == workflow
    assert len(profile['fields']) == count
    assert profile['law_effective_date'] == '2026-03-05'
    assert profile['full_form_filled'] is False
    assert profile['submission_ready'] is False
    assert profile['attachments_review_required'] is True
    assert profile['verification']['legal_compliance_certified'] is False
    assert profile['verification']['visual_qa']['reviewed_pages'] == [1]
    assert 'overflow_mode' not in profile
    assert len({field['id'] for field in profile['fields']}) == count
    for field in profile['fields']:
        assert field['kind'] == 'pdf_overlay'
        assert field['input_required'] == (field['input_mode'] == 'user_provided')
        assert field['narrative_style_required'] is False
        assert field['max_chars'] > 0
        assert field['location_verified'] is True
        assert field['label'] not in {'서명', '생년월일', '접수번호'}
    if number == '32':
        summaries = [field for field in profile['fields'] if field.get('evidence_scope')]
        assert len(summaries) == 6
        assert all('DSUR' in field['evidence_scope'] for field in summaries)
        # EU 판매허가권자를 한국 임상시험 승인자로 자동 대입하지 않음.
        owner = next(field for field in profile['fields'] if field['label'] == '임상시험 승인받은 자 명칭')
        assert owner['input_mode'] == 'user_provided'


@pytest.mark.parametrize('number,workflow,count', FORMS)
def test_new_ra_partial_fill_preserves_every_original_page_and_guide(number, workflow, count, tmp_path):
    source = _source(number)
    before = source.read_bytes()
    profile = load_form_profile(source, f'ra_law_form_{number}_pdf')
    assert profile is not None
    assert sha256(before).hexdigest() == profile['source_sha256']
    output = fill_compatible_template(source, profile['demo_values'], tmp_path / f'{number}.pdf', profile=profile)
    assert verify_output(source, output, profile['demo_values'], profile=profile)['status'] == 'passed'
    original, written = PdfReader(source), PdfReader(output)
    assert len(original.pages) == len(written.pages)
    for source_page, result_page in zip(original.pages, written.pages):
        assert _normalized(source_page.extract_text()) in _normalized(result_page.extract_text())
    assert source.read_bytes() == before
    if number == '20':
        assert '0.00 mg' in written.pages[0].extract_text()


def _herzuma():
    metadata = json.loads((ROOT / 'evals/ra_public_sources.json').read_text(encoding='utf-8'))
    entry = next(item for item in metadata['sources'] if item['id'] == 'ra-public-herzuma')
    source = ROOT / entry['path']
    if not source.is_file():
        pytest.skip('EMA 제품정보 corpus를 별도로 확보해야 함')
    assert sha256(source.read_bytes()).hexdigest() == entry['sha256']
    page = PdfReader(source).pages[1].extract_text()
    name = next(fact['value'] for fact in entry['facts'] if fact['field_key'] == '제품명')
    appearance = next(fact['value'] for fact in entry['facts'] if fact['field_key'] == '성상')
    assert _normalized(name) in _normalized(page)
    assert _normalized(appearance) in _normalized(page)
    assert entry['scope']['foreign_authorisation_only'] is True
    assert entry['scope']['korean_authorisation_verified'] is False
    return source, name, appearance


def test_actual_short_brand_strength_and_appearance_fit_without_report_style_rewrite(tmp_path):
    medical, full_name, appearance = _herzuma()
    source = _source('4')
    before, medical_before = source.read_bytes(), medical.read_bytes()
    profile = load_form_profile(source, 'ra_law_form_4_pdf')
    # 브랜드·함량만 선택한 구조화 값임. 전체 공식 제품명을 잘랐다고 표시하지 않음.
    brand_strength = 'Herzuma 150 mg'
    assert full_name.startswith(brand_strength + ' ')
    values = {'제품명': brand_strength, '성상': appearance}
    output = fill_compatible_template(source, values, tmp_path / 'selected_actual_values.pdf', profile=profile)
    assert verify_output(source, output, values, profile=profile)['status'] == 'passed'
    text = PdfReader(output).pages[0].extract_text()
    assert brand_strength in text
    assert appearance in text
    assert '150 mg함' not in text
    assert source.read_bytes() == before
    assert medical.read_bytes() == medical_before


def test_full_official_product_name_overflow_is_blocked_by_default_without_output_loss(tmp_path):
    medical, full_name, _ = _herzuma()
    source = _source('4')
    before, medical_before = source.read_bytes(), medical.read_bytes()
    profile = deepcopy(load_form_profile(source, 'ra_law_form_4_pdf'))
    assert profile.get('overflow_mode') is None
    field = next(item for item in profile['fields'] if item['value_key'] == '제품명')
    assert len(full_name) > field['max_chars']
    output = tmp_path / 'existing_output.pdf'
    output.write_bytes(b'PREVIOUS USER OUTPUT MUST SURVIVE')
    with pytest.raises(TemplateError, match='길이|제한|넘'):
        fill_compatible_template(source, {'제품명': full_name}, output, profile=profile)
    assert output.read_bytes() == b'PREVIOUS USER OUTPUT MUST SURVIVE'
    assert source.read_bytes() == before
    assert medical.read_bytes() == medical_before


def test_new_ra_profile_cannot_be_reused_on_a_different_workflow_source(tmp_path):
    dmf, safety = _source('16'), _source('32')
    profile = load_form_profile(dmf, 'ra_law_form_16_pdf')
    assert load_form_profile(safety, 'ra_law_form_16_pdf') is None
    with pytest.raises(TemplateError, match='달라짐|다시 분석'):
        fill_compatible_template(safety, profile['demo_values'], tmp_path / 'wrong_workflow.pdf', profile=profile)
