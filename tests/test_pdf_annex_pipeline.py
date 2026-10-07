"""공식 RA 원본과 동결한 EMA 자료로 별첨의 검수 경계를 독립 검사함.

실모델·네트워크 호출과 제출 적합성 인증은 수행하지 않음.
"""

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re

from pypdf import PdfReader, PdfWriter
import pytest
from streamlit.testing.v1 import AppTest

from agent.completeness import check_completeness
from agent.grounding import inspect_grounding
from agent.pipeline import build_downloads, review_result
from agent.review import CITATION_PATTERN
from templates import load_form_profile


ROOT = Path(__file__).resolve().parents[1]


def normalize(text):
    return re.sub(r'\s+', '', text)


class OfflineInspection:
    """동결한 실제 원문 일치만 mock 승인하여 변경 후 검수 무효화를 검사함."""

    def generate_json(self, name, payload):
        if name == 'grounding':
            source_map = {source['source_id']: source['text'] for source in payload['sources']}
            claims = []
            for item in payload['claims']:
                identifier = CITATION_PATTERN.findall(item['text'])[0]
                quote = CITATION_PATTERN.sub('', item['text']).strip()
                assert quote in source_map[identifier]
                claims.append({'field': item['field'], 'line': item['line'], 'status': 'supported',
                               'evidence': [{'source_id': identifier, 'quote': quote}]})
            return {'claims': claims}
        assert name == 'completeness'
        return {'issues': [], 'checked_fields': sorted(payload['draft'])}


@pytest.fixture
def ra_case():
    source = ROOT / 'data/public_templates/ra/ra_law_form_4_20260305.pdf'
    manifest = ROOT / 'evals/ra_public_sources.json'
    if not source.is_file() or not manifest.is_file():
        pytest.skip('공식 원본 corpus는 별도로 확보함')
    entry = next(entry for entry in json.loads(manifest.read_text(encoding='utf-8'))['sources']
                 if entry['id'] == 'ra-public-benepali')
    medical = ROOT / entry['path']
    if not medical.is_file():
        pytest.skip('EMA 원본 corpus는 별도로 확보함')
    assert sha256(medical.read_bytes()).hexdigest() == entry['sha256']
    facts = {fact['field_key']: fact for fact in entry['facts']}
    keys = ['제품명', '효능 효과']
    material_page = normalize(PdfReader(medical).pages[1].extract_text())
    assert all(normalize(facts[key]['value']) in material_page for key in keys)
    profile = deepcopy(load_form_profile(source, 'ra_law_form_4_pdf'))
    profile.update(overflow_mode='annex', citation_mode='sidecar', document_kind='application')
    sources = [{'source_id': f'S{index}', 'text': facts[key]['value'], 'filename': entry['filename'],
                'page': facts[key]['page'], 'sheet': None, 'location': f"{facts[key]['page']}쪽",
                'source_url': entry['source_url'], 'document_sha256': entry['sha256'],
                'product_name': entry['product_name'], 'product_variant': entry['product_variant'],
                'jurisdiction': 'EU'} for index, key in enumerate(keys, 1)]
    def cite(value, identifier):
        return '\n'.join(f'{line} [{identifier}]' if line.strip() else line for line in value.splitlines())
    draft = {'제목': 'EMA 공개 자료 검토', '요약': cite(facts['제품명']['value'], 'S1'),
             '본문': cite(facts['효능 효과']['value'], 'S2')}
    draft.update({key: cite(facts[key]['value'], f'S{index}') for index, key in enumerate(keys, 1)})
    result = {'status': 'ready', 'draft': draft, 'sources': sources, 'template_profile': profile,
              'domain': 'pharmaceutical_ra', 'ra_workflow': 'product_approval',
              'brief': {'부족한 정보': []}, 'instruction': 'EU 공개 자료의 선택 제형 원문을 보존하여 검토함',
              'answers': {}, 'semantic_required': True, 'completeness_required': True}
    fake = OfflineInspection()
    result['grounding'] = inspect_grounding(draft, sources, fake)
    result['completeness'] = check_completeness(draft, result['brief'], profile, fake,
                                               instruction=result['instruction'], answers={})
    check = review_result(result)
    assert not check['blocking'], check['warnings']
    return source, medical, entry, result


def export(source, result, profile=None, mapping=None):
    return build_downloads(result, confirmed=True, template_paths={'pdf': source},
                           template_profiles={'pdf': profile or result['template_profile']},
                           mappings={'pdf': mapping} if mapping is not None else None)


def test_ra_download_keeps_complete_actual_paragraph_sources_and_every_original_page(ra_case):
    source, medical, entry, result = ra_case
    before, medical_before = source.read_bytes(), medical.read_bytes()
    output = export(source, result)['pdf']
    original, written = PdfReader(source), PdfReader(BytesIO(output))
    original_count = len(original.pages)
    assert len(written.pages) > original_count
    for previous, completed in zip(original.pages, written.pages):
        assert normalize(previous.extract_text()) in normalize(completed.extract_text())
    assert '별첨 1 참조' in written.pages[0].extract_text()
    annex = '\n'.join(page.extract_text() for page in written.pages[original_count:])
    for source_record in result['sources']:
        assert normalize(source_record['text']) in normalize(annex)
        assert f"[{source_record['source_id']}]" in annex
        assert source_record['filename'] in annex
        assert source_record['source_url'] in normalize(annex)
    assert '[S1]' not in written.pages[0].extract_text()
    check = result['output_verification']['pdf']
    assert check['status'] == 'passed'
    assert next(item for item in check['checks'] if item['name'] == 'pdf_annex_complete_content')['fields'] == 2
    assert source.read_bytes() == before
    assert medical.read_bytes() == medical_before
    assert entry['scope']['korean_authorisation_verified'] is False


def test_herzuma_complete_loading_and_maintenance_paragraph_exports_without_false_conflict(ra_case):
    source, _, _, result = ra_case
    metadata = json.loads((ROOT / 'evals/ra_public_sources.json').read_text(encoding='utf-8'))
    entry = next(item for item in metadata['sources'] if item['id'] == 'ra-public-herzuma')
    medical = ROOT / entry['path']
    if not medical.is_file():
        pytest.skip('EMA Herzuma 원본 corpus를 별도로 확보해야 함')
    before = medical.read_bytes()
    assert sha256(before).hexdigest() == entry['sha256']
    facts = {fact['field_key']: fact for fact in entry['facts']}
    keys = ['제품명', '용법 용량']
    reader = PdfReader(medical)
    for key in keys:
        fact = facts[key]
        assert normalize(fact['value']) in normalize(reader.pages[fact['page'] - 1].extract_text())
    result['sources'] = [{'source_id': f'S{index}', 'text': facts[key]['value'], 'filename': entry['filename'],
                          'page': facts[key]['page'], 'sheet': None, 'location': f"{facts[key]['page']}쪽",
                          'source_url': entry['source_url'], 'document_sha256': entry['sha256'],
                          'product_name': entry['product_name'], 'product_variant': entry['product_variant'],
                          'jurisdiction': 'EU'} for index, key in enumerate(keys, 1)]
    def cite(key, identifier):
        return '\n'.join(f'{line} [{identifier}]' if line.strip() else line for line in facts[key]['value'].splitlines())
    result['draft'] = {'제목': 'EMA 공개 원문 검토', '요약': cite('제품명', 'S1'),
                       '본문': cite('용법 용량', 'S2'), '제품명': cite('제품명', 'S1'),
                       '용법 용량': cite('용법 용량', 'S2')}
    fake = OfflineInspection()
    result['grounding'] = inspect_grounding(result['draft'], result['sources'], fake)
    result['completeness'] = check_completeness(result['draft'], result['brief'], result['template_profile'], fake,
                                               instruction=result['instruction'], answers={})
    check = review_result(result)
    assert not check['blocking'], check['warnings']
    completed = PdfReader(BytesIO(export(source, result)['pdf']))
    annex = '\n'.join(page.extract_text() for page in completed.pages[len(PdfReader(source).pages):])
    assert normalize(facts['제품명']['value']) in normalize(annex)
    assert normalize(facts['용법 용량']['value']) in normalize(annex)
    assert '8 mg/kg' in annex and '6 mg/kg' in annex
    assert entry['source_url'] in normalize(annex)
    assert medical.read_bytes() == before


def test_actual_rule_error_cannot_be_bypassed_by_enabled_annex(ra_case):
    source, _, _, result = ra_case
    result['draft']['제품명'] = result['draft']['제품명'].replace('25 mg', '250 mg')
    result['semantic_required'] = False
    result['completeness_required'] = False
    check = review_result(result)
    assert check['blocking']
    assert any(issue['code'] in {'ra_quantity_mismatch', 'ra_product_unverified', 'number_mismatch'}
               for issue in check['warnings'])
    with pytest.raises(ValueError, match='오류'):
        export(source, result)
    assert 'output_verification' not in result


@pytest.mark.parametrize('mutation', ['summary', 'original_evidence', 'profile'])
def test_changed_summary_evidence_or_annex_permission_invalidates_stored_inspection(ra_case, mutation):
    source, _, _, result = ra_case
    if mutation == 'summary':
        result['draft']['요약'] = 'Benepali 25 mg [S1]'
    elif mutation == 'original_evidence':
        # 같은 수치가 남아도 원자료 본문이 바뀌면 이전 의미 검수를 무효화함.
        result['sources'][1]['text'] += '\nEvidence source updated.'
    else:
        result['template_profile']['overflow_mode'] = None
    check = review_result(result)
    assert check['blocking']
    assert any(issue['code'] in {'semantic_stale', 'completeness_stale'} for issue in check['warnings'])
    with pytest.raises(ValueError, match='오류'):
        export(source, result)


def test_direct_identity_input_cannot_be_replaced_by_an_annex(ra_case):
    source, _, _, result = ra_case
    field = next(field for field in result['template_profile']['fields'] if field['label'] == '신청인 성명')
    value = 'TEST USER PROVIDED IDENTITY ' * 8
    result['sources'].append({'source_id': 'SU1', 'text': value, 'filename': '사용자 입력',
                              'page': None, 'sheet': None, 'location': '신청인 성명'})
    result['draft'][field['value_key']] = value + ' [SU1]'
    result['locked_fields'] = {field['value_key']: {'value': value, 'source_id': 'SU1'}}
    result['semantic_required'] = False
    result['completeness_required'] = False
    check = review_result(result)
    assert check['blocking']
    assert any(issue['code'] == 'template_required' for issue in check['warnings'])
    with pytest.raises(ValueError, match='오류'):
        export(source, result)


def test_registered_sidecar_default_keeps_direct_identity_short_and_source_connected(ra_case):
    source, _, _, result = ra_case
    registered = load_form_profile(source, 'ra_law_form_4_pdf')
    assert registered['citation_mode'] == 'sidecar'
    result['template_profile']['citation_mode'] = registered['citation_mode']
    result['draft']['신청인 성명'] = '홍길동 [SUIdentity]'
    result['sources'].append({'source_id': 'SUIdentity', 'text': '홍길동', 'filename': '사용자 입력',
                              'page': None, 'sheet': None, 'location': '신청인 성명'})
    result['locked_fields'] = {'신청인 성명': {'value': '홍길동', 'source_id': 'SUIdentity'}}
    result['semantic_required'] = False
    result['completeness_required'] = False
    assert not review_result(result)['blocking']
    output = PdfReader(BytesIO(export(source, result)['pdf']))
    assert '홍길동' in output.pages[0].extract_text()
    assert '[SUIdentity]' not in output.pages[0].extract_text()
    assert '[SUIdentity]' in result['draft']['신청인 성명']


def test_download_independent_check_rejects_missing_annex_pages(ra_case, monkeypatch):
    import templates
    source, _, _, result = ra_case
    fill = templates.fill_compatible_template
    def damaged(template, values, output, **kwargs):
        path = fill(template, values, output, **kwargs)
        reader = PdfReader(path)
        writer = PdfWriter()
        for page in reader.pages[:len(PdfReader(template).pages)]:
            writer.add_page(page)
        with path.open('wb') as stream:
            writer.write(stream)
        return path
    monkeypatch.setattr(templates, 'fill_compatible_template', damaged)
    with pytest.raises(ValueError, match='별첨'):
        export(source, result)
    assert 'output_verification' not in result


def test_export_profile_cannot_unlock_registered_direct_input_or_remap_facts_into_identity(ra_case):
    source, _, _, result = ra_case
    altered = deepcopy(result['template_profile'])
    field = next(field for field in altered['fields'] if field['label'] == '신청인 성명')
    field.update(input_required=False, input_mode='source_grounded', value_key='본문')
    # 원본 SHA가 같아도 직접 입력 보호·매핑 권한이 바뀌면 재검수가 필요함.
    with pytest.raises(ValueError):
        export(source, result, profile=altered)


@pytest.mark.parametrize('enabled', [False, True])
def test_old_and_new_annex_export_profiles_cannot_silently_mix_review_permissions(ra_case, enabled):
    source, _, _, result = ra_case
    altered = deepcopy(result['template_profile'])
    if not enabled:
        altered.pop('overflow_mode')
    else:
        altered['fields'] = [field for field in altered['fields'] if field['value_key'] == '제품명']
    with pytest.raises(ValueError):
        export(source, result, profile=altered)


@pytest.mark.parametrize('property', ['required', 'max_chars', 'scope', 'input_required'])
def test_export_profile_permission_changes_require_a_new_review(ra_case, property):
    source, _, _, result = ra_case
    altered = deepcopy(result['template_profile'])
    product = next(field for field in altered['fields'] if field['value_key'] == '제품명')
    if property == 'scope':
        altered['ra_product_variant'] = 'Benepali 50 mg solution for injection'
    elif property == 'input_required':
        direct = next(field for field in altered['fields'] if field['label'] == '신청인 성명')
        direct.update(input_required=False, input_mode='source_grounded')
    elif property == 'required':
        product['required'] = not product['required']
    else:
        product['max_chars'] = 1000
    with pytest.raises(ValueError):
        export(source, result, profile=altered)


def test_export_mapping_cannot_silently_replace_user_identity_with_the_generated_title(ra_case):
    source, _, _, result = ra_case
    profile = result['template_profile']
    mapping = {field['id']: field['value_key'] for field in profile['fields']}
    result['template_mapping'] = deepcopy(mapping)
    direct = next(field for field in profile['fields'] if field['label'] == '신청인 성명')
    mapping[direct['id']] = '제목'
    with pytest.raises(ValueError):
        export(source, result, mapping=mapping)


def test_shortened_export_mapping_cannot_omit_a_reviewed_field(ra_case):
    source, _, _, result = ra_case
    mapping = {field['id']: field['value_key'] for field in result['template_profile']['fields']}
    mapping.pop(next(identifier for identifier, key in mapping.items() if key == '효능 효과'))
    with pytest.raises(ValueError, match='매핑'):
        export(source, result, mapping=mapping)


def test_production_enriched_review_profile_accepts_the_matching_raw_ui_export_profile(ra_case):
    from agent.documents import document_context
    from agent.pipeline import _workflow_context
    source, _, _, result = ra_case
    raw = deepcopy(result['template_profile'])
    raw.pop('document_kind')
    # M6는 선택한 모든 항목을 포함하며 미제공 optional 항목은 빈 문자열임.
    for field in raw['fields']:
        result['draft'].setdefault(field['value_key'], '')
    enriched, context = _workflow_context(raw, ra='product_approval')
    enriched, _ = document_context(enriched, 'application')
    assert 'ra_context' in enriched and 'document_context' in enriched
    result['template_profile'] = enriched
    result.update(context)
    result['grounding'] = inspect_grounding(result['draft'], result['sources'], OfflineInspection())
    result['completeness'] = check_completeness(result['draft'], result['brief'], enriched, OfflineInspection(),
                                               instruction=result['instruction'], answers={})
    mapping = {field['id']: field['value_key'] for field in raw['fields']}
    outputs = export(source, result, profile=raw, mapping=mapping)
    assert len(PdfReader(BytesIO(outputs['pdf'])).pages) > len(PdfReader(source).pages)
    assert result['output_verification']['pdf']['status'] == 'passed'


@pytest.mark.parametrize('missing_kind', ['optional_key_absent', 'required_empty'])
def test_explicit_mapping_rejects_missing_key_and_explicitly_required_blank(ra_case, missing_kind):
    source, _, _, result = ra_case
    profile = result['template_profile']
    for field in profile['fields']:
        result['draft'].setdefault(field['value_key'], '')
    field = next(field for field in profile['fields'] if field['label'] == '신청인 성명')
    if missing_kind == 'optional_key_absent':
        result['draft'].pop(field['value_key'])
    else:
        field['required'] = True
    result['semantic_required'] = False
    result['completeness_required'] = False
    mapping = {field['id']: field['value_key'] for field in profile['fields']}
    with pytest.raises(ValueError, match='매핑된 값|오류'):
        export(source, result, mapping=mapping)


def test_omitted_export_profile_uses_the_original_reviewed_annex_permissions(ra_case):
    source, _, _, result = ra_case
    outputs = build_downloads(result, confirmed=True, template_paths={'pdf': source})
    assert len(PdfReader(BytesIO(outputs['pdf'])).pages) > len(PdfReader(source).pages)
    assert result['output_verification']['pdf']['status'] == 'passed'


def test_ui_annex_checkbox_changes_profile_and_invalidates_previous_request(ra_case, monkeypatch, tmp_path):
    import agent.pipeline as pipeline
    source, _, _, _ = ra_case
    captured = []
    def offline(*args, **kwargs):
        captured.append(deepcopy(kwargs['template_profile']))
        return {'status': 'needs_evidence', 'brief': {}, 'sources': [], 'message': '오프라인 양식 검증임'}
    monkeypatch.setattr(pipeline, 'run_pipeline', offline)
    monkeypatch.setenv('REPORT_AGENT_DATA_DIR', str(tmp_path))
    ui = AppTest.from_file(str(ROOT / 'app/ui.py'), default_timeout=20)
    ui.session_state['public_template_path'] = str(source)
    ui.run()
    assert not ui.exception
    ui.text_area(key='instruction').set_value('공식 원문을 보존하여 검토해줘')
    ui.button(key='generate').click().run()
    assert not ui.exception
    assert captured[-1].get('overflow_mode') is None
    previous = ui.session_state['request_fingerprint']
    digest = sha256(source.read_bytes()).hexdigest()
    ui.checkbox(key=f'annex_{digest}').check().run()
    assert not ui.exception
    assert ui.session_state['request_fingerprint'] != previous
    assert 'result' not in ui.session_state
    ui.button(key='generate').click().run()
    assert not ui.exception
    assert captured[-1]['overflow_mode'] == 'annex'
    previous = ui.session_state['request_fingerprint']
    ui.checkbox(key=f'annex_{digest}').uncheck().run()
    assert ui.session_state['request_fingerprint'] != previous
    assert 'result' not in ui.session_state
