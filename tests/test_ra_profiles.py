"""공식 RA PDF의 입력 위치와 시험값을 검증함. 제출 적합성을 인증하지 않음."""

from hashlib import sha256
import json
from pathlib import Path

from pypdf import PdfReader
import pytest

from agent.output_check import verify_output
from templates import TemplateError, fill_compatible_template, load_form_profile


ROOT = Path(__file__).resolve().parents[1]
NUMBERS = ('4', '8', '23')


@pytest.mark.parametrize('number', NUMBERS)
def test_ra_profiles_bind_current_official_source_and_protect_direct_fields(number):
    profile = json.loads((ROOT / f'templates/profiles/ra_law_form_{number}_pdf.json').read_text(encoding='utf-8'))
    assert profile['domain'] == 'pharmaceutical_ra'
    assert profile['ra_workflow'] in {'product_approval', 'variation', 'clinical_trial'}
    assert profile['law_effective_date'] == '2026-03-05'
    assert profile['source_url'].startswith('https://www.law.go.kr/LSW/flDownload.do?flSeq=')
    assert profile['submission_ready'] is False
    assert '실제 제출용' in profile['demo_notice']
    assert any(field['input_mode'] == 'source_grounded' for field in profile['fields'])
    assert any(field['input_mode'] == 'user_provided' for field in profile['fields'])
    for field in profile['fields']:
        assert field['input_required'] == (field['input_mode'] == 'user_provided')
        assert field['narrative_style_required'] is False
        assert field['value_key'] in profile['demo_values']
        assert '서명' not in field['label']


@pytest.mark.parametrize('number', NUMBERS)
def test_ra_official_pdf_fake_fill_preserves_source_and_literal_numbers(number, tmp_path):
    source = ROOT / f'data/public_templates/ra/ra_law_form_{number}_20260305.pdf'
    if not source.is_file():
        pytest.skip('공식 원본은 별도 공개 corpus에서 확보함')
    profile = load_form_profile(source, f'ra_law_form_{number}_pdf')
    assert profile is not None
    before = source.read_bytes()
    assert sha256(before).hexdigest() == profile['source_sha256']
    output = fill_compatible_template(source, profile['demo_values'], tmp_path / 'fake_filled.pdf', profile=profile)
    report = verify_output(source, output, profile['demo_values'], profile=profile)
    assert report['status'] == 'passed'
    assert source.read_bytes() == before
    assert len(PdfReader(output).pages) == len(PdfReader(source).pages)
    combined = ''.join(page.extract_text() for page in PdfReader(output).pages)
    if number in {'4', '8'}:
        assert '0.00 mg' in combined
    if number == '23':
        assert 'TEST-000' in combined
    # 원본의 안내·첨부 목록은 그대로 유지함.
    assert '첨부서류' in combined
    assert '신청인' in combined


def test_ra_profile_rejects_another_official_form(tmp_path):
    approval = ROOT / 'data/public_templates/ra/ra_law_form_4_20260305.pdf'
    change = ROOT / 'data/public_templates/ra/ra_law_form_8_20260305.pdf'
    if not approval.is_file() or not change.is_file():
        pytest.skip('공식 원본 별도 corpus')
    profile = load_form_profile(approval, 'ra_law_form_4_pdf')
    assert load_form_profile(change, 'ra_law_form_4_pdf') is None
    with pytest.raises(TemplateError, match='달라짐|다시 분석'):
        fill_compatible_template(change, profile['demo_values'], tmp_path / 'bad.pdf', profile=profile)
