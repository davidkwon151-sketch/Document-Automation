"""Real EMA snapshots + synthetic mutations, without network or model calls."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

import pytest

from agent.ra import inspect_ra_draft
from agent.review import CITATION_PATTERN
from evals import ra_adversarial, ra_public


MANIFEST = json.loads(ra_public.DEFAULT_MANIFEST.read_text(encoding='utf-8'))
FACTS = [(record['id'], fact['role']) for record in MANIFEST['sources'] for fact in record['facts']]
FAMILIES = [
    'wrong_known_citation', 'missing_citation', 'unknown_citation', 'missing_selected_field',
    'changed_numeric_value', 'wrong_selected_strength', 'wrong_unopened_state',
    'loading_maintenance_mixed', 'wrong_dose_interval', 'wrong_dose_unit',
    'no_automatic_unit_conversion', 'other_company_product_quote', 'wrong_container',
    'wrong_quantity_unit', 'invented_concentration', 'weekly_frequency_mixed',
    'daily_weekly_mixed', 'wrong_population', 'selected_condition_omitted',
    'other_company_same_number', 'lower_initial_primary_mixed', 'wrong_population_age',
    'selected_lower_initial_condition_omitted',
]


@pytest.fixture(scope='module')
def actual():
    loaded = {}
    for record in MANIFEST['sources']:
        path = ra_public.ROOT / record['path']
        if path.is_file():
            before = sha256(path.read_bytes()).hexdigest()
            facts, sources, _ = ra_public.read_source(record)
            assert sha256(path.read_bytes()).hexdigest() == before == record['sha256']
            loaded[record['id']] = (record, facts, sources)
    return loaded


@pytest.fixture(scope='module')
def report(actual):
    if len(actual) != len(MANIFEST['sources']):
        pytest.skip('공식 원본 일부 미확보: 3개사 전체 오류 시험을 합성 성공으로 대체하지 않음')
    return ra_adversarial.evaluate(MANIFEST)


@pytest.mark.parametrize('record_id,role', FACTS, ids=[record_id + ':' + role for record_id, role in FACTS])
def test_whitespace_normalization_preserves_every_full_real_fact_and_is_not_auto_corrected(actual, record_id, role):
    if record_id not in actual:
        pytest.skip('이 회사의 실제 원자료 스냅샷 미확보')
    record, facts, _ = actual[record_id]
    sources = [source for _, _, material in actual.values() for source in material]
    fact = next(fact for fact in facts if fact['role'] == role)
    draft = ra_adversarial.baseline_draft(facts)
    draft[fact['field_key']] = ra_public.cited(re.sub(r'\s+', ' ', fact['value']), fact['source_id'])
    before = deepcopy(draft)
    checked = ra_adversarial.check_draft(draft, facts, sources, ra_adversarial.selected_profile(record, facts))
    assert not checked['blocked_by_any'], checked
    assert checked['scores']['full_value_preserved_count'] == len(facts)
    assert checked['scores']['cited_field_count'] == len(facts)
    assert checked['original_draft_unchanged'] and draft == before


@pytest.mark.parametrize('family', FAMILIES)
def test_real_record_mutation_family_is_detected_without_conversion_or_exception(report, family):
    rows = [row for row in report['results'] if row['family'] == family]
    assert rows, family
    assert all(row['status'] == 'detected' for row in rows), rows
    assert all(row['compare_blocking'] for row in rows), rows
    assert all(row['original_draft_unchanged'] for row in rows)
    assert all('checker_error' != row['status'] for row in rows)


def test_actual_report_has_honest_snapshot_fact_layer_and_model_denominators(report):
    assert report['mode'] == 'synthetic_mutations_of_real_records'
    assert report['verified_snapshot_count'] == report['expected_snapshot_count'] == 3
    assert report['verified_company_count'] == report['expected_company_count'] == 3
    assert report['checked_fact_count'] == report['declared_fact_count'] == 14
    assert report['positive_passed_count'] == report['positive_case_count'] == 17
    assert report['exact_positive_count'] == 3 and report['whitespace_positive_count'] == 14
    assert report['mutation_detected_count'] == report['mutation_checked_count'] == report['mutation_case_count']
    assert report['mutation_case_count'] >= 92
    assert report['mutation_escaped_count'] == report['checker_error_count'] == 0
    assert report['detection_rate'] == 1
    assert all(snapshot['original_unchanged'] for snapshot in report['snapshots'])
    assert report['layer_detected_count']['compare'] == report['mutation_case_count']
    # These selected mutations are covered; this is not a universal clinical guarantee.
    assert report['layer_detected_count']['generic'] < report['mutation_case_count']
    assert report['layer_detected_count']['ra'] == report['mutation_case_count']
    assert report['model_evaluated'] is False
    assert report['model_training_performed'] is False
    assert report['network_calls'] == 0
    assert report['complete'] and report['passed']


def test_wrong_but_known_non_numeric_citation_requires_exact_fact_provenance(actual):
    if 'ra-public-herzuma' not in actual:
        pytest.skip('Herzuma 실제 원자료 미확보')
    _, facts, sources = actual['ra-public-herzuma']
    fact = next(fact for fact in facts if fact['field_key'] == '성상')
    wrong_id = next(source['source_id'] for source in sources if source['source_id'] != fact['source_id'])
    wrong = {fact['field_key']: ra_public.cited(fact['value'], wrong_id)}
    known = {source['source_id'] for source in sources}
    assert set(CITATION_PATTERN.findall(wrong[fact['field_key']])) <= known  # Former ID-membership check accepted this.
    for proof in [known, sources]:
        scores = ra_public.compare_values(wrong, [fact], proof)
        assert scores['full_value_preserved_count'] == 1
        assert scores['cited_field_count'] == 0


def test_extra_foreign_known_citation_is_rejected_even_alongside_correct_id(actual):
    if len(actual) != 3:
        pytest.skip('3개사 실제 원자료 미확보')
    _, facts, sources = actual['ra-public-benepali']
    fact = next(fact for fact in facts if '3 years' in fact['value'])
    foreign = next(source for source in actual['ra-public-keppra'][2] if '3 years' in source['text'])
    value = ra_public.cited(fact['value'], fact['source_id']) + f" [{foreign['source_id']}]"
    scores = ra_public.compare_values({fact['field_key']: value}, [fact], sources + [foreign])
    assert scores['numeric_exact_count'] == scores['numeric_field_count'] == 1
    assert scores['full_value_preserved_count'] == 1
    assert scores['cited_field_count'] == 0


@pytest.mark.parametrize('key,new_value', [
    ('document_sha256', 'f' * 64), ('page', 999), ('product_variant', 'Other 25 mg tablet'),
    ('regulatory_role', 'other_claim'), ('filename', 'different_company.pdf'), ('jurisdiction', 'KR'),
])
def test_retrieval_id_requires_matching_original_provenance(actual, key, new_value):
    if 'ra-public-benepali' not in actual:
        pytest.skip('Benepali 실제 원자료 미확보')
    _, facts, sources = actual['ra-public-benepali']
    fact = next(fact for fact in facts if fact['field_key'] == '원료약품 및 분량')
    source = deepcopy(next(source for source in sources if source['source_id'] == fact['source_id']))
    source['source_id'] = 'Sretrieval_id'
    draft = {fact['field_key']: ra_public.cited(fact['value'], source['source_id'])}
    assert ra_public.compare_values(draft, [fact], [source])['cited_field_count'] == 1
    source[key] = new_value
    assert ra_public.compare_values(draft, [fact], [source])['cited_field_count'] == 0


@pytest.mark.parametrize('record_id', ['ra-public-benepali', 'ra-public-keppra'])
def test_explicit_actual_composition_container_overrides_selected_metadata(actual, record_id):
    if record_id not in actual:
        pytest.skip('이 회사의 실제 원자료 스냅샷 미확보')
    record, facts, sources = actual[record_id]
    fact = next(fact for fact in facts if fact['field_key'] == '원료약품 및 분량')
    good = {fact['field_key']: ra_public.cited(fact['value'], fact['source_id'])}
    old, new = ('syringe', 'tablet') if record['product_name'] == 'Benepali' else ('tablet', 'syringe')
    wrong = {fact['field_key']: good[fact['field_key']].replace(old, new)}
    profile = ra_adversarial.selected_profile(record, [fact])
    assert not inspect_ra_draft(good, sources, profile=profile)['blocking']
    checked = inspect_ra_draft(wrong, sources, profile=profile)
    assert checked['blocking']
    assert {issue['code'] for issue in checked['issues']} & {'ra_variant_mismatch', 'ra_quantity_mismatch'}


def test_explicit_composition_in_second_clause_is_bound_to_its_quantity():
    # Supplemental synthetic context guard; this is not an internet fact fixture.
    profile = {'ra_product_name': 'Alpha', 'ra_product_variant': 'Alpha 25 mg pre-filled syringe'}
    sources = [{'source_id': 'S1', 'product_name': 'Alpha',
                'product_variant': profile['ra_product_variant'],
                'text': 'Each tablet contains 5 mg and each pre-filled syringe contains 25 mg.'}]
    good = {'원료약품 및 분량': 'Each pre-filled syringe contains 25 mg. [S1]'}
    bad = {'원료약품 및 분량': 'Each tablet contains 25 mg. [S1]'}
    assert not inspect_ra_draft(good, sources, profile=profile)['blocking']
    assert inspect_ra_draft(bad, sources, profile=profile)['blocking']


def test_missing_snapshots_are_unchecked_without_fabricated_success(tmp_path):
    report = ra_adversarial.evaluate(MANIFEST, root=tmp_path)
    assert report['expected_snapshot_count'] == 3 and report['declared_fact_count'] == 14
    assert report['verified_snapshot_count'] == report['checked_fact_count'] == 0
    assert report['positive_case_count'] == report['mutation_checked_count'] == report['mutation_detected_count'] == 0
    assert report['detection_rate'] is None
    assert all(row['status'] == 'missing_snapshot' for row in report['snapshots'])
    assert not report['complete'] and not report['passed']


def test_source_validation_exception_is_reported_and_not_a_detection(actual, monkeypatch):
    if not actual:
        pytest.skip('실제 원자료 스냅샷 미확보')
    record = next(iter(actual.values()))[0]
    def fail_source(*args, **kwargs):
        raise ValueError('의도적인 원자료 검증 실패')
    monkeypatch.setattr(ra_adversarial, 'read_source', fail_source)
    report = ra_adversarial.evaluate({'sources': [record]})
    assert report['snapshots'][0]['status'] == 'source_validation_error'
    assert report['snapshots'][0]['error'] == '의도적인 원자료 검증 실패'
    assert report['verified_snapshot_count'] == report['mutation_detected_count'] == 0
    assert report['snapshots'][0]['original_unchanged']
    assert not report['passed']


def test_checker_exception_is_separately_reported_without_inflating_detected_count(actual, monkeypatch):
    if not actual:
        pytest.skip('실제 원자료 스냅샷 미확보')
    record, facts, _ = next(iter(actual.values()))
    def fail_check(*args, **kwargs):
        raise RuntimeError('의도적인 내용 검사 실패')
    monkeypatch.setattr(ra_adversarial, 'check_draft', fail_check)
    report = ra_adversarial.evaluate({'sources': [record]})
    assert report['verified_snapshot_count'] == 1
    assert report['checked_fact_count'] == len(facts)
    assert report['checker_error_count'] == report['mutation_case_count'] > 0
    assert report['mutation_detected_count'] == report['mutation_checked_count'] == 0
    assert report['positive_passed_count'] == 0
    assert report['detection_rate'] is None
    assert all(row['error'] == '의도적인 내용 검사 실패' for row in report['results'])
    assert not report['passed']
