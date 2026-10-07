"""Official snapshots + selected-region filling; no LLM calls or submission claims."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pdfplumber
import pytest

from agent.output_check import verify_output
from agent.brief import model_profile
from templates import fill_compatible_template, load_form_profile
from templates.value_rules import inspect_form_values

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'templates/ra_workflow_catalog.json'
NEW_IDS = ('ra_workflow_variation_three_rows_pdf', 'ra_workflow_renewal_contacts_pdf')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def test_official_workflow_catalog_counts_source_and_profile_hashes():
    catalog = read(CATALOG)
    entries = catalog['workflows']
    assert len(entries) == 11 == catalog['counts']['workflow_count']
    assert len({entry['id'] for entry in entries}) == len(entries)
    assert len({entry['source_sha256'] for entry in entries}) == catalog['counts']['unique_form_sha_count']
    assert catalog['counts']['new_form_original_sha_count'] == 0
    assert catalog['counts']['new_profile_count'] == 2
    assert catalog['actual_llm_generation_count'] == 0
    assert not catalog['human_edit_ratio_measured']
    for entry in entries:
        source, profile_file = ROOT / entry['source_path'], ROOT / entry['profile_path']
        assert sha256(source.read_bytes()).hexdigest() == entry['source_sha256']
        assert sha256(profile_file.read_bytes()).hexdigest() == entry['profile_sha256']
        profile = read(profile_file)
        assert profile['source_sha256'] == entry['source_sha256']
        assert entry['field_count'] == len(profile['fields'])
        assert not entry['current_version_verified']
        assert not entry['full_submission_ready']
        assert not entry['legal_compliance_certified']
        assert entry['jurisdiction'] == 'KR'
        assert 'law.go.kr/' in entry['source_url']
        assert set(entry['source_based_keys']) == {f['value_key'] for f in profile['fields'] if f['input_mode'] == 'source_grounded'}
        assert set(entry['user_input_keys']) == {f['value_key'] for f in profile['fields'] if f['input_mode'] == 'user_provided'}


@pytest.mark.parametrize('workflow', ['variation', 'renewal', 'dmf', 'gmp', 'api_gmp', 'overseas_manufacturer', 'prior_consultation'])
def test_printed_attachment_passages_bind_exact_original_page_and_conditions(workflow):
    entry = next(item for item in read(CATALOG)['workflows'] if item['id'] == workflow)
    assert entry['attachments']
    with pdfplumber.open(ROOT / entry['source_path']) as pdf:
        for attachment in entry['attachments']:
            assert attachment['review_required'] is True
            assert attachment['source_sha256'] == entry['source_sha256']
            quote = attachment['source_quote']
            assert quote in (pdf.pages[attachment['page'] - 1].extract_text() or '')
            assert attachment['condition']


def test_two_new_guides_are_official_guidance_not_product_evidence_or_blank_forms():
    catalog = read(CATALOG)
    guides = catalog['guidance_documents']
    assert len(guides) == 2
    assert len({g['source_sha256'] for g in guides}) == catalog['counts']['new_guidance_original_sha_count']
    for guide in guides:
        original = ROOT / guide['path']
        assert original.read_bytes().startswith(b'%PDF-')
        assert sha256(original.read_bytes()).hexdigest() == guide['source_sha256']
        assert guide['source_url'].startswith('https://mfds.go.kr/')
        assert guide['resource_kind'] == 'guideline'
        assert guide['blank_form'] is False
        assert guide['current_version_verified'] is False
        assert guide['full_submission_ready'] is False
        with pdfplumber.open(original) as pdf:
            assert len(pdf.pages) == guide['page_count']
            assert guide['first_page_text_excerpt'] in (pdf.pages[0].extract_text() or '')


@pytest.mark.parametrize('profile_id', NEW_IDS)
def test_new_selected_fields_preserve_original_and_pass_independent_position_review(profile_id, tmp_path):
    declared = read(ROOT / 'templates/profiles' / (profile_id + '.json'))
    original = ROOT / declared['source_path']
    original_bytes = original.read_bytes()
    profile = load_form_profile(original, profile_id)
    assert profile is not None
    values = declared['demo_values']
    mapping = {f['id']: f['value_key'] for f in profile['fields']}
    result = fill_compatible_template(original, values, tmp_path / original.name, mapping, profile)
    assert original.read_bytes() == original_bytes
    checked = verify_output(original, result, values, profile=profile, mapping=mapping)
    assert checked['status'] == 'passed'
    assert checked['legal_compliance_certified'] is False
    with pdfplumber.open(original) as source, pdfplumber.open(result) as filled:
        assert len(source.pages) == len(filled.pages)


@pytest.mark.parametrize('row', [1, 2, 3])
def test_fixed_change_rows_reject_partial_but_allow_unused_empty_rows(row):
    profile = read(ROOT / 'templates/profiles/ra_workflow_variation_three_rows_pdf.json')
    values = {f['value_key']: '' for f in profile['fields']}
    assert not inspect_form_values(values, profile)
    values['변경 항목 ' + str(row)] = '포장단위'
    issues = inspect_form_values(values, profile)
    assert issues
    for key in ('변경 전 ', '변경 후 ', '변경 사유 '):
        values[key + str(row)] = '시험값'
    assert not inspect_form_values(values, profile)


def test_new_contacts_are_direct_input_and_fields_do_not_overlap_existing_text():
    profile = read(ROOT / 'templates/profiles/ra_workflow_renewal_contacts_pdf.json')
    by_key = {f['value_key']: f for f in profile['fields']}
    for key in ('담당자 성명', '담당자 전화번호'):
        assert by_key[key]['input_required']
        assert by_key[key]['input_mode'] == 'user_provided'
        assert not by_key[key]['narrative_style_required']
    with pdfplumber.open(ROOT / profile['source_path']) as source:
        for key in ('갱신 비고', '담당자 성명', '담당자 전화번호'):
            field = by_key[key]
            overlaps = [char for char in source.pages[field['page'] - 1].chars
                        if char['x0'] < field['x'] + field['width'] and char['x1'] > field['x']
                        and char['top'] < field['y'] + field['height'] and char['bottom'] > field['y']]
            assert not overlaps


@pytest.mark.parametrize('profile_id', NEW_IDS)
def test_qa_values_never_enter_model_metadata_or_replace_default_original_profile(profile_id):
    profile = read(ROOT / 'templates/profiles' / (profile_id + '.json'))
    before = deepcopy(profile)
    filtered = model_profile(profile)
    assert 'demo_values' not in filtered
    assert 'demo_notice' not in filtered
    assert 'verification' not in filtered
    assert profile == before
    assert filtered['fields'] == profile['fields']
    assert load_form_profile(ROOT / profile['source_path'])['entry_id'] == profile['parent_profile_id']


@pytest.mark.parametrize(('workflow', 'required_passages'), [
    ('dmf', ['적합판정서 사본을 제출할 수 없는 불가피한 사유',
             '사유 및 제출가능일', '시험용 원료의약품']),
    ('gmp', ['제조소 총람', '품질(보증)체계 관련 자료', '제품표준서',
             '밸리데이션 자료', '경우에만 제출합니다']),
    ('api_gmp', ['제조소 총람', '품질(보증)체계 관련 자료', '제품표준서',
                 '밸리데이션 자료', '변경사유서 및 그 근거서류']),
])
def test_back_page_attachment_lists_keep_initial_items_and_application_exceptions(workflow, required_passages):
    entry = next(item for item in read(CATALOG)['workflows'] if item['id'] == workflow)
    quote = '\n'.join(a['source_quote'] for a in entry['attachments'] if a['page'] == 2)
    compact = ''.join(quote.split())
    for passage in required_passages:
        assert ''.join(passage.split()) in compact
