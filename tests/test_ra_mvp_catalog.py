"""Six source-bound MVP forms, not completed legal submissions."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil

from pypdf import PdfReader
import pytest

from agent.documents import DOCUMENT_KINDS
from agent.ra import RA_WORKFLOWS
from agent.output_check import verify_output
from templates import TemplateError, fill_compatible_template, load_form_profile

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT/'templates/ra_mvp_catalog.json').read_text(encoding='utf-8'))
FORMS = CATALOG['forms']


def profile_for(entry):
    source = ROOT/entry['source_path']
    if not source.is_file():
        pytest.skip('The independently acquired official form corpus is missing')
    assert sha256(source.read_bytes()).hexdigest() == entry['source_sha256']
    profile = load_form_profile(source, entry['profile_id'])
    assert profile is not None
    return source, profile


def demo_for(entry):
    manifest = json.loads((ROOT/entry['demo_manifest']).read_text(encoding='utf-8'))
    evidence = next(source for source in manifest['sources'] if source['id'] == entry['demo_source_id'])
    source = ROOT/evidence['path']
    if not source.is_file():
        pytest.skip('The independently acquired public product-information corpus is missing')
    assert sha256(source.read_bytes()).hexdigest() == evidence['sha256']
    reader = PdfReader(source)
    normalize = lambda text: re.sub(r'\s+', '', text)
    selected = {}
    for key in entry['demo_field_keys']:
        source_key = entry.get('demo_field_map', {}).get(key, key)
        fact = next(fact for fact in evidence['facts'] if fact['field_key'] == source_key)
        assert normalize(fact['exact_quote']) in normalize(reader.pages[fact['page']-1].extract_text() or '')
        assert fact['value'] == fact['exact_quote']
        selected[key] = fact['value']
    return source, evidence, selected


def test_catalog_is_exactly_the_small_mvp_and_does_not_embed_personal_or_fake_values():
    assert CATALOG['schema_version'] == 1
    assert {entry['id'] for entry in FORMS} == {
        'ra_law_form_4_pdf', 'ra_law_form_20_pdf', 'corporate_ra_eurofins_sample_submission',
        'ra_law_form_8_pdf', 'ra_law_form_23_pdf', 'ra_law_form_32_pdf',
        'ra_law_form_23_pdf_v2_second_page', 'ra_law_form_32_pdf_v2_second_page'}
    assert len(FORMS) == CATALOG['summary']['form_count'] == 8
    assert CATALOG['summary']['registered_field_count'] == 117
    assert CATALOG['summary']['user_input_field_count'] == 44
    assert CATALOG['summary']['source_based_field_count'] == 73
    assert CATALOG['summary']['default_demo_selected_fields'] == 10
    assert sum(entry['source_page_count'] for entry in FORMS) == 16
    assert len({entry['source_sha256'] for entry in FORMS}) == 6
    assert CATALOG['summary']['unique_registered_physical_field_count'] == 84
    assert CATALOG['summary']['unique_source_based_field_count'] == 53
    assert CATALOG['summary']['unique_user_input_field_count'] == 31
    assert CATALOG['summary']['unique_public_demo_fields'] == 8
    assert not CATALOG['summary']['current_version_verified']
    assert not CATALOG['summary']['full_submission_ready']
    def inspect(value):
        if isinstance(value, dict):
            assert not {'demo_values', 'user_values', 'answers', 'field_values', 'qa_values', 'test_values'} & value.keys()
            for item in value.values(): inspect(item)
        elif isinstance(value, list):
            for item in value: inspect(item)
    inspect(CATALOG)


@pytest.mark.parametrize('entry', FORMS, ids=lambda entry: entry['id'])
def test_form_binding_supported_fields_and_submission_limits_match_registered_source(entry):
    source, profile = profile_for(entry)
    assert entry['id'] == entry['profile_id']
    assert entry['ra_workflow'] in RA_WORKFLOWS
    assert entry['document_kind'] == ('report' if entry['id'].startswith('ra_law_form_32_pdf') else 'application')
    assert entry['document_kind'] in DOCUMENT_KINDS
    assert entry['profile_sha256'] == sha256((ROOT/entry['profile_path']).read_bytes()).hexdigest()
    assert entry['field_count'] == len(profile['fields'])
    assert entry['source_page_count'] == len(PdfReader(source).pages)
    assert set(entry['source_based_keys']) == {field['value_key'] for field in profile['fields'] if not field['input_required']}
    assert set(entry['user_input_keys']) == {field['value_key'] for field in profile['fields'] if field['input_required']}
    assert set(entry['source_based_keys']).isdisjoint(entry['user_input_keys'])
    assert entry['registered_required_fields'] == [field['value_key'] for field in profile['fields'] if field.get('required')]
    assert entry['citation_mode'] == profile['citation_mode'] == 'sidecar'
    assert all(entry[key] is False for key in ['company_internal', 'current_version_verified', 'full_submission_ready',
                                             'mandatory_requirements_verified', 'legal_compliance_certified'])
    assert entry['coverage_note'] and entry['version_note'] and entry['required_scope_note']


@pytest.mark.parametrize('entry', FORMS, ids=lambda entry: entry['id'])
def test_demo_is_only_a_whole_selected_public_fact_not_an_applicant_or_signature(entry):
    _, profile = profile_for(entry)
    _, evidence, values = demo_for(entry)
    assert set(values) == set(entry['demo_field_keys'])
    assert set(values) <= set(entry['source_based_keys'])
    assert not set(values) & set(entry['user_input_keys'])
    for field in profile['fields']:
        if field['value_key'] in values:
            assert len(values[field['value_key']]) <= field['max_chars']
            assert field['input_required'] is False
    assert evidence['scope']['korean_authorisation_verified'] is False
    assert not any('signature' in key.lower() or '서명' in key or '동의' in key for key in values)


@pytest.mark.parametrize('form_id,slot,kind,workflow', [
    ('ra_law_form_23_pdf', '시험약 제품명', 'application', 'clinical_trial'),
    ('ra_law_form_32_pdf', '제품명 성분명', 'report', 'safety_management'),
])
def test_clinical_and_safety_alias_preserve_entire_quote_and_original_source_id(
        form_id, slot, kind, workflow, tmp_path, monkeypatch):
    from app.ra_mvp_service import generate_mvp
    from agent.pipeline import build_downloads, review_result
    from evals.ra_public import read_source
    from llm.client import LLMClient

    def forbidden(*args, **kwargs):
        raise AssertionError('Whole quote-copy QA must not call a model')

    monkeypatch.setattr(LLMClient, 'generate_json', forbidden)
    monkeypatch.setattr(LLMClient, 'read_image_json', forbidden)
    entry = next(form for form in FORMS if form['id'] == form_id)
    template, profile = profile_for(entry)
    product, evidence, values = demo_for(entry)
    originals = {path: path.read_bytes() for path in (template, product, ROOT/entry['profile_path'])}
    facts, sources, _ = read_source(evidence)
    fact = next(fact for fact in facts if fact['field_key'] == '제품명')
    result = generate_mvp('공식 제품명 원문만 참고용으로 기입', form_id, demo=True)
    assert result['status'] == 'ready' and not review_result(result)['blocking']
    assert (result['document_kind'], result['ra_workflow']) == (kind, workflow)
    assert entry['demo_field_map'] == {slot: '제품명'}
    assert result['draft'][slot] == fact['value'] + f" [{fact['source_id']}]"
    source = next(source for source in result['sources'] if source['source_id'] == fact['source_id'])
    assert source == next(source for source in sources if source['source_id'] == fact['source_id'])
    assert source['text'] == fact['value'] == fact['exact_quote'] == values[slot]
    assert source['regulatory_role'] == 'product_name' and source['company_role'] == 'document_publisher'
    assert source['document_sha256'] == evidence['sha256']
    assert result['actual_model_requests'] == result['actual_model_responses'] == 0
    assert all(result['draft'][key] == '' for key in set(entry['source_based_keys'] + entry['user_input_keys']) - {slot})
    assert not result['locked_fields'] and not result['submission_ready']
    payload = build_downloads(result, confirmed=True, template_paths={'pdf': template},
                              template_profiles={'pdf': result['template_profile']}, native_review='off')
    written = tmp_path/'clinical-safety-demo.pdf'
    written.write_bytes(payload['pdf'])
    assert verify_output(template, written, values, profile=profile)['status'] == 'passed'
    assert len(PdfReader(written).pages) == entry['source_page_count'] == 2
    assert originals == {path: path.read_bytes() for path in originals}


@pytest.mark.parametrize('form_id', ['ra_law_form_23_pdf', 'ra_law_form_32_pdf'])
def test_registered_positions_accept_only_explicit_fake_qa_and_preserve_full_document(form_id, tmp_path):
    """Fake fixture values test positions; they are not clinical facts or defaults."""
    entry = next(form for form in FORMS if form['id'] == form_id)
    source, profile = profile_for(entry)
    values = profile['demo_values']
    assert set(values) == set(entry['source_based_keys'] + entry['user_input_keys'])
    original = source.read_bytes()
    output = fill_compatible_template(source, values, tmp_path/'fake-position-qa.pdf', profile=profile)
    checked = verify_output(source, output, values, profile=profile)
    assert checked['status'] == 'passed'
    assert len(PdfReader(output).pages) == len(PdfReader(source).pages) == 2
    assert source.read_bytes() == original


@pytest.mark.parametrize('entry', FORMS, ids=lambda entry: entry['id'])
def test_selected_demo_fills_without_annex_and_preserves_source_and_unselected_inputs(entry, tmp_path):
    source, profile = profile_for(entry)
    product_source, _, values = demo_for(entry)
    before, product_before = source.read_bytes(), product_source.read_bytes()
    target = fill_compatible_template(source, values, tmp_path/'review.pdf', profile=profile)
    check = verify_output(source, target, values, profile=profile)
    assert check['status'] == 'passed'
    original, written = PdfReader(source), PdfReader(target)
    assert len(original.pages) == len(written.pages) == entry['source_page_count']
    if original.get_fields():
        fields = written.get_fields()
        selected_names = {field['original_field_name']: values[field['value_key']]
                          for field in profile['fields'] if field['value_key'] in values}
        for name, value in selected_names.items():
            assert str(fields[name].get('/V', '')) == value
        for name, field in original.get_fields().items():
            if name not in selected_names:
                assert str(fields[name].get('/V', '')) == str(field.get('/V', ''))
    assert source.read_bytes() == before and product_source.read_bytes() == product_before


@pytest.mark.parametrize('entry', FORMS, ids=lambda entry: entry['id'])
def test_wrong_source_sha_does_not_overwrite_a_user_file(entry, tmp_path):
    source, profile = profile_for(entry)
    target = tmp_path/'existing.pdf'; target.write_bytes(b'KEEP USER OUTPUT')
    wrong = dict(profile, source_sha256='0'*64)
    with pytest.raises(TemplateError):
        fill_compatible_template(source, {entry['demo_field_keys'][0]: 'QA'}, target, profile=wrong)
    assert target.read_bytes() == b'KEEP USER OUTPUT'


def test_variation_demo_copies_whole_product_name_without_inventing_a_change(tmp_path, monkeypatch):
    from app.ra_mvp_service import generate_mvp
    from agent.pipeline import build_downloads, review_result
    from llm.client import LLMClient

    def forbidden(*args, **kwargs):
        raise AssertionError('Public quote-copy QA must not call a model')

    monkeypatch.setattr(LLMClient, 'generate_json', forbidden)
    monkeypatch.setattr(LLMClient, 'read_image_json', forbidden)
    entry = next(form for form in FORMS if form['id'] == 'ra_law_form_8_pdf')
    template, profile = profile_for(entry)
    product, _, selected = demo_for(entry)
    before = {path: path.read_bytes() for path in (template, product)}
    result = generate_mvp('공식 제품명만 검토용으로 기입', entry['id'], demo=True)
    assert result['status'] == 'ready' and not review_result(result)['blocking']
    assert result['ra_workflow'] == 'variation' and entry['demo_field_keys'] == ['제품명']
    assert result['actual_model_requests'] == result['actual_model_responses'] == 0
    assert selected == {'제품명': '한미플루 75mg'}
    assert result['draft']['제품명'].startswith(selected['제품명'] + ' [S')
    remaining = set(entry['source_based_keys'] + entry['user_input_keys']) - {'제품명'}
    assert len(remaining) == 9
    assert all(result['draft'][key] == '' for key in remaining)
    assert not result['locked_fields'] and not result['submission_ready']
    payload = build_downloads(result, confirmed=True, template_paths={'pdf': template},
                              template_profiles={'pdf': result['template_profile']}, native_review='off')
    written = tmp_path / 'variation-demo.pdf'
    written.write_bytes(payload['pdf'])
    assert verify_output(template, written, selected, profile=profile)['status'] == 'passed'
    assert len(PdfReader(written).pages) == 1
    assert result['output_verification']['pdf']['status'] == 'passed'
    assert before == {path: path.read_bytes() for path in before}


@pytest.mark.parametrize('tamper', ['source_sha', 'profile_sha', 'promote_direct', 'omit_grounded'])
def test_variation_catalog_sha_and_input_permissions_cannot_be_relaxed(tmp_path, tamper):
    from app.ra_mvp_service import resolve_mvp_form

    catalog = deepcopy(CATALOG)
    entry = next(form for form in catalog['forms'] if form['id'] == 'ra_law_form_8_pdf')
    template, _ = profile_for(entry)
    original = template.read_bytes()
    for relative in (entry['source_path'], entry['profile_path']):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    if tamper == 'source_sha':
        entry['source_sha256'] = '0' * 64
    elif tamper == 'profile_sha':
        entry['profile_sha256'] = '0' * 64
    elif tamper == 'promote_direct':
        entry['user_input_keys'].remove('품목허가번호')
        entry['source_based_keys'].append('품목허가번호')
    else:
        entry['source_based_keys'].remove('변경 사유 1')
    target = tmp_path / 'templates/ra_mvp_catalog.json'
    target.write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ValueError, match='SHA|권한'):
        resolve_mvp_form(entry['id'], root=tmp_path)
    assert template.read_bytes() == original


def test_variation_is_exposed_by_the_existing_ui_catalog_without_automatic_values(tmp_path, monkeypatch):
    from llm.client import LLMClient
    from streamlit.testing.v1 import AppTest

    entry = next(form for form in FORMS if form['id'] == 'ra_law_form_8_pdf')
    profile_for(entry)
    monkeypatch.setenv('REPORT_AGENT_DATA_DIR', str(tmp_path))

    def forbidden(*args, **kwargs):
        raise AssertionError('Rendering the catalog must not call a model')

    monkeypatch.setattr(LLMClient, 'generate_json', forbidden)
    monkeypatch.setattr(LLMClient, 'read_image_json', forbidden)
    ui = AppTest.from_file(str(ROOT / 'app/ra_mvp_ui.py'), default_timeout=30).run()
    assert not ui.exception
    assert entry['title'] in ui.selectbox(key='mvp_form').options
    ui.selectbox(key='mvp_form').select(entry['id']).run()
    assert not ui.exception
    assert ui.session_state['mvp_form'] == entry['id']
    assert entry['coverage_note'] in {item.value for item in ui.info}
    assert all(ui.text_input(key=f"mvp_input_{entry['id']}_{key}").value == ''
               for key in entry['user_input_keys'])
    assert 'mvp_result' not in ui.session_state
