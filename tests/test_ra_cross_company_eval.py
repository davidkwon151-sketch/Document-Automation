"""Actual snapshot identity checks and explicit synthetic denominator boundaries."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from evals import ra_public


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    from llm import client
    import parsers.native

    monkeypatch.setattr(client, 'LLMClient', lambda *a, **k: pytest.fail('actual API must not run'))
    monkeypatch.setattr(parsers.native, 'render_native_pdf', lambda *a, **k: pytest.fail('actual COM must not run'))


@pytest.fixture(scope='module')
def actual():
    records = json.loads(ra_public.DEFAULT_MANIFEST.read_text(encoding='utf-8'))['sources']
    missing = [record['id'] for record in records if not (ra_public.ROOT / record['path']).is_file()]
    if missing:
        pytest.skip('공식 EMA snapshot 미확보: ' + ', '.join(missing))
    hashes = {record['path']: sha256((ra_public.ROOT / record['path']).read_bytes()).hexdigest()
              for record in records}
    assert all(hashes[record['path']] == record['sha256'] for record in records)
    result = {record['id']: (record, *ra_public.read_source(record)) for record in records}
    yield result
    assert {name: sha256((ra_public.ROOT / name).read_bytes()).hexdigest() for name in hashes} == hashes


@pytest.fixture
def synthetic(tmp_path):
    path = tmp_path / 'alpha.pdf'
    pdf = canvas.Canvas(str(path))
    for y, text in [(750, '7. MARKETING AUTHORISATION HOLDER'), (730, 'Holder Alpha Ltd.'),
                    (700, '8. MANUFACTURER'), (680, 'Maker Beta Ltd.'),
                    (650, 'Alpha 25 mg tablets'), (630, 'Shelf life: 3 years.')]:
        pdf.drawString(30, y, text)
    pdf.save()
    record = {'id': 'synthetic-role', 'path': path.name, 'filename': path.name,
              'sha256': sha256(path.read_bytes()).hexdigest(), 'page_count': 1,
              'company': {'name': 'Holder Alpha Ltd.', 'exact_quote': 'Holder Alpha Ltd.',
                          'page': 1, 'role_heading_page': 1, 'role': 'marketing_authorisation_holder'},
              'product_name': 'Alpha', 'product_variant': 'Alpha 25 mg tablets', 'jurisdiction': 'EU',
              'source_url': 'https://files.alpha.example/alpha.pdf', 'source_page': 'https://alpha.example/products',
              'facts': [{'field_key': '제품명', 'value': 'Alpha 25 mg tablets',
                         'exact_quote': 'Alpha 25 mg tablets', 'page': 1, 'role': 'product_name'},
                        {'field_key': '저장방법 및 유효기간', 'value': 'Shelf life: 3 years.',
                         'exact_quote': 'Shelf life: 3 years.', 'page': 1, 'role': 'shelf_life'}]}
    return tmp_path, record


@pytest.mark.parametrize('identifier', ['ra-public-herzuma', 'ra-public-benepali', 'ra-public-keppra'])
def test_actual_ema_holders_preserve_all_facts_and_retrieval_company_scope(actual, identifier):
    from agent.retrieve import chunk_documents

    record, facts, sources, document = actual[identifier]
    assert len(facts) == len(record['facts'])
    assert all(source['company_name'] == record['company']['name'] for source in sources)
    assert all(source['company_role'] == 'marketing_authorisation_holder' for source in sources)
    assert document['company_name'] == record['company']['name']
    draft = {fact['field_key']: ra_public.cited(fact['value'], fact['source_id']) for fact in facts}
    scores = ra_public.compare_values(draft, facts, sources)
    assert scores['cited_field_count'] == scores['full_value_preserved_count'] == len(facts)
    assert scores['numeric_exact_count'] == scores['numeric_field_count']
    fact = next(fact for fact in facts if fact['role'] == 'product_name')
    chunk = next(chunk for chunk in chunk_documents([document]) if chunk['regulatory_role'] == 'product_name')
    assert chunk['company_name'] == record['company']['name']
    assert chunk['company_role'] == record['company']['role']
    assert ra_public.compare_values({fact['field_key']: ra_public.cited(fact['value'], chunk['source_id'])},
                                    [fact], [chunk])['cited_field_count'] == 1


def test_actual_manufacturer_cannot_be_relabelled_with_an_unrelated_mah_heading(actual):
    record = deepcopy(actual['ra-public-herzuma'][0])
    record['company'].update(name='CELLTRION INC.', exact_quote='CELLTRION INC.', page=34,
                             role_heading_page=31, role='marketing_authorisation_holder')
    with pytest.raises(ValueError, match='판매허가권자'):
        ra_public.read_source(record)


def test_same_page_company_from_next_section_is_not_the_holder(synthetic):
    root, record = synthetic
    assert ra_public.read_source(record, root)[0]
    record['company'].update(name='Maker Beta Ltd.', exact_quote='Maker Beta Ltd.')
    with pytest.raises(ValueError, match='판매허가권자'):
        ra_public.read_source(record, root)


def test_role_heading_page_boolean_is_not_a_real_page(synthetic):
    root, record = synthetic
    record['company']['role_heading_page'] = True
    with pytest.raises(ValueError, match='페이지'):
        ra_public.read_source(record, root)


@pytest.mark.parametrize('product_name', ['', 'Bene', 'Keppra'])
def test_actual_variant_cannot_give_a_partial_or_different_product_name_a_scope(actual, product_name):
    record = deepcopy(actual['ra-public-benepali'][0])
    record['product_name'] = product_name
    with pytest.raises(ValueError, match='제품명'):
        ra_public.read_source(record)


@pytest.mark.parametrize('key,value', [('product_name', 'Keppra'), ('company_name', 'UCB Pharma SA'),
                                      ('company_role', 'manufacturer')])
def test_actual_same_text_number_and_id_cannot_hide_another_company_or_role(actual, key, value):
    _, facts, original, _ = actual['ra-public-benepali']
    fact = next(fact for fact in facts if fact['role'] == 'shelf_life')
    sources = deepcopy(original)
    source = next(source for source in sources if source['source_id'] == fact['source_id'])
    source[key] = value
    scores = ra_public.compare_values({fact['field_key']: ra_public.cited(fact['value'], fact['source_id'])},
                                     [fact], sources)
    assert scores['full_value_preserved_count'] == scores['numeric_exact_count'] == 1
    assert scores['cited_field_count'] == 0


@pytest.mark.parametrize('key', ['product_name', 'company_name', 'company_role'])
@pytest.mark.parametrize('location', ['fact', 'reference'])
def test_annotated_scope_cannot_silently_fall_back_when_identity_is_missing(actual, key, location):
    _, originals, original_sources, _ = actual['ra-public-benepali']
    fact = deepcopy(next(fact for fact in originals if fact['role'] == 'shelf_life'))
    sources = deepcopy(original_sources)
    source = next(source for source in sources if source['source_id'] == fact['source_id'])
    (fact if location == 'fact' else source).pop(key)
    scores = ra_public.compare_values({fact['field_key']: ra_public.cited(fact['value'], fact['source_id'])},
                                     [fact], sources)
    assert scores['cited_field_count'] == 0


def test_actual_other_product_with_same_three_years_is_not_the_cited_fact(actual):
    _, facts, sources, _ = actual['ra-public-benepali']
    fact = next(fact for fact in facts if fact['role'] == 'shelf_life')
    foreign = next(source for source in actual['ra-public-keppra'][2] if source['regulatory_role'] == 'shelf_life')
    assert [token['key'] for token in ra_public.number_tokens(foreign['text'])] == [
        token['key'] for token in ra_public.number_tokens(fact['value'])]
    scores = ra_public.compare_values({fact['field_key']: ra_public.cited(fact['value'], foreign['source_id'])},
                                     [fact], sources + [foreign])
    assert scores['full_value_preserved_count'] == scores['numeric_exact_count'] == 1
    assert scores['cited_field_count'] == 0


def test_selected_empty_or_citation_only_values_are_not_success(actual):
    _, facts, sources, _ = actual['ra-public-benepali']
    fact = next(fact for fact in facts if fact['role'] == 'shelf_life')
    for value in ('', '   ', '[' + fact['source_id'] + ']'):
        scores = ra_public.compare_values({fact['field_key']: value}, [fact], sources)
        assert scores['selected_field_count'] == scores['numeric_field_count'] == 1
        assert scores['full_value_preserved_count'] == scores['cited_field_count'] == scores['numeric_exact_count'] == 0


def test_publisher_role_is_kept_separate_from_manufacturer(synthetic):
    root, record = synthetic
    record['company'].update(role='document_publisher', official_domain='alpha.example')
    facts, sources, document = ra_public.read_source(record, root)
    assert document['company_role'] == 'document_publisher'
    assert all(source['company_role'] == 'document_publisher' for source in sources)
    fact = facts[0]
    draft = {fact['field_key']: ra_public.cited(fact['value'], fact['source_id'])}
    assert ra_public.compare_values(draft, [fact], sources)['cited_field_count'] == 1
    sources[0]['company_role'] = 'manufacturer'
    assert ra_public.compare_values(draft, [fact], sources)['cited_field_count'] == 0


def test_legacy_unannotated_comparison_keeps_its_limited_contract():
    fact = {'field_key': '기간', 'value': 'Shelf life: 3 years.'}
    scores = ra_public.compare_values({'기간': ra_public.cited(fact['value'], 'S1')}, [fact], {'S1'})
    assert scores['cited_field_count'] == scores['full_value_preserved_count'] == 1


@pytest.mark.parametrize('manifest,profiles', [
    ({'sources': []}, ('profile',)), ({'sources': [{'id': 'a'}]}, ()),
    ({'sources': [{'id': 'a'}, {'id': 'a'}]}, ('profile',)),
    ({'sources': [{'id': 'a'}]}, ('profile', 'profile')),
])
def test_empty_or_duplicate_denominators_fail_before_any_case_or_model_work(monkeypatch, manifest, profiles):
    monkeypatch.setattr(ra_public, 'evaluate_case', lambda *a, **k: pytest.fail('invalid denominator must not run'))
    with pytest.raises(ValueError):
        ra_public.evaluate(manifest, profiles=profiles)


def test_same_snapshot_for_distinct_records_does_not_merge_case_denominators(monkeypatch):
    records = [{'id': 'variant-a', 'sha256': '0' * 64}, {'id': 'variant-b', 'sha256': '0' * 64}]
    for record in records:
        record.update(company={'name': 'Same company', 'role': 'document_publisher'}, jurisdiction='EU',
                      product_variant=record['id'], source_url='https://example.test/source.pdf')
    monkeypatch.setattr(ra_public, 'evaluate_case', lambda *a, **k: {'passed': True})
    report = ra_public.evaluate({'sources': records}, profiles=('form-a', 'form-b'))
    assert report['case_count'] == report['passed_count'] == 4
    assert report['company_count'] == 1
    assert len({row['id'] for row in report['results']}) == 4
    assert report['model_response_count'] == 0 and not report['model_evaluated']


@pytest.mark.parametrize('identifier', ['ra-public-herzuma', 'ra-public-benepali', 'ra-public-keppra'])
@pytest.mark.parametrize('profile_id', ['ra_law_form_4_pdf', 'ra_law_form_20_pdf',
                                        'corporate_roche_supplier_change_request'])
def test_existing_actual_profiles_keep_their_selected_quote_output_success(actual, tmp_path, identifier, profile_id):
    row = ra_public.evaluate_case(actual[identifier][0], profile_id, mode='rules', artifact_dir=tmp_path)
    assert row['passed'], row
    assert row['selected_fact_count'] == row['scores']['cited_field_count']
    assert row['scores']['numeric_exact_count'] == row['scores']['numeric_field_count']
    assert row['output_verification']['status'] == 'passed'
    assert not row['model_response_received'] and not row['submission_ready']


def test_missing_optional_ra_workflow_still_runs_generic_ra_checks_and_verified_output(actual, tmp_path, monkeypatch):
    original = ra_public.load_form_profile

    def no_special_workflow(*args, **kwargs):
        profile = original(*args, **kwargs)
        profile.pop('ra_workflow', None)
        return profile

    monkeypatch.setattr(ra_public, 'load_form_profile', no_special_workflow)
    row = ra_public.evaluate_case(actual['ra-public-keppra'][0], 'ra_law_form_4_pdf',
                                  mode='rules', artifact_dir=tmp_path)
    assert row['passed'] and row['output_verification']['status'] == 'passed'
    assert not row['ra_issues'] and not row['model_response_received']


@pytest.mark.parametrize('profile_id', ['corporate_ra_eurofins_sample_submission', 'corporate_ra_sgs_sample_submission'])
def test_actual_native_form_keeps_its_non_annex_policy_and_optional_workflow(actual, tmp_path, profile_id):
    profile_path = ra_public.ROOT / 'templates/profiles' / (profile_id + '.json')
    if not profile_path.is_file():
        pytest.skip('새 공식 기업 프로파일 미확보: 실제 기입 검증 미실행')
    original = json.loads(profile_path.read_text(encoding='utf-8'))
    template = ra_public.ROOT / original['source_path']
    if not template.is_file():
        pytest.skip('새 공식 기업 양식 원본 미확보: 실제 기입 검증 미실행')
    before = sha256(template.read_bytes()).hexdigest()
    row = ra_public.evaluate_case(actual['ra-public-keppra'][0], profile_id, mode='rules', artifact_dir=tmp_path)
    assert row['passed'], row
    assert row['selected_fact_count'] == row['scores']['full_value_preserved_count'] == 1
    assert row['output_verification']['status'] == 'passed'
    assert row['original_pages'] == row['output_pages']
    assert not row['submission_ready'] and not row['model_response_received']
    assert sha256(template.read_bytes()).hexdigest() == before == original['source_sha256']
