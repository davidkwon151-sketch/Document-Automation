"""Real EMA snapshot mutations, with separately labelled synthetic scope controls.

The original PDF is read locally and SHA checked. No network/model is used;
these tests assess operating rules, not clinical correctness certification.
"""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from pypdf import PdfReader
import pytest

from agent.ra import RA_WORKFLOWS, inspect_ra_draft


ROOT = Path(__file__).resolve().parents[1]


def test_testing_support_has_its_own_advisory_scope_without_approval_or_gmp_claim():
    workflow = RA_WORKFLOWS['testing_support']
    assert workflow['title'] == '시험 의뢰·품질자료 지원'
    assert len(workflow['checks']) == 2
    assert workflow['source_urls'] == ['https://www.pacelabs.com/life-sciences/submit-a-sample/',
                                       'https://www.cambrex.com/forms-and-certificates/']
    result = inspect_ra_draft({}, [], profile={'ra_workflow': 'testing_support'})
    assert not result['blocking']
    assert result['additional_checks'] == workflow['checks']
    assert any(check['status'] == 'not_certified' for check in result['checks'])


@pytest.fixture(scope='module')
def actual_pages():
    manifest = json.loads((ROOT / 'evals/ra_public_sources.json').read_text(encoding='utf-8'))
    pages = {}
    for record in manifest['sources']:
        path = ROOT / record['path']
        if not path.is_file():
            continue
        before = sha256(path.read_bytes()).hexdigest()
        assert before == record['sha256']
        reader = PdfReader(path)
        selected = {'Herzuma': [2, 4, 29], 'Benepali': [4], 'Keppra': [2, 5]}[record['product_name']]
        pages[record['product_name']] = (record, {page: reader.pages[page - 1].extract_text() for page in selected})
        assert sha256(path.read_bytes()).hexdigest() == before
    return pages


def real_source(actual_pages, product, page):
    if product not in actual_pages:
        pytest.skip('EMA original snapshot missing; synthetic evidence does not replace it')
    record, pages = actual_pages[product]
    source = {'source_id': 'S1', 'text': pages[page], 'filename': record['filename'], 'page': page,
              'document_sha256': record['sha256'], 'product_name': product,
              'product_variant': record['product_variant']}
    profile = {'ra_product_name': product, 'ra_product_variant': record['product_variant']}
    return record, source, profile


@pytest.fixture(scope='module')
def new_actual_pages():
    path = ROOT / 'evals/ra_extended_public_sources.json'
    if not path.is_file():
        pytest.skip('New EMA snapshot manifest missing')
    records = json.loads(path.read_text(encoding='utf-8'))['sources']
    result = {}
    for record in records:
        original = ROOT / record['path']
        if not original.is_file():
            continue
        before = sha256(original.read_bytes()).hexdigest()
        assert before == record['sha256']
        selected = [2, 3] if record['product_name'] == 'Ozempic' else [2, 4]
        reader = PdfReader(original)
        result[record['product_name']] = (record, {page: reader.pages[page - 1].extract_text() for page in selected})
        assert sha256(original.read_bytes()).hexdigest() == before
    return result


@pytest.mark.parametrize('product,page', [('Benepali', 4), ('Keppra', 5)])
@pytest.mark.parametrize('field', ['안전성', '본문'])
def test_real_unestablished_safety_cannot_be_rewritten_as_established(actual_pages, product, page, field):
    _, source, profile = real_source(actual_pages, product, page)
    quote = re.search(r'The safety and efficacy[^.]*not been established\.', re.sub(r'\s+', ' ', source['text']))[0]
    good = inspect_ra_draft({field: quote + ' [S1]'}, [source], profile=profile)
    assert not good['blocking'], good['issues']
    bad = inspect_ra_draft({field: quote.replace('not been established', 'been established') + ' [S1]'},
                           [source], profile=profile)
    assert bad['blocking'] and any(issue['code'] == 'ra_claim_unverified' for issue in bad['issues'])


@pytest.mark.parametrize('suffix', ['regardless of physician assessment', 'in clinical practice'])
def test_real_regimen_small_rewording_does_not_bypass_connected_conditions(actual_pages, suffix):
    _, source, profile = real_source(actual_pages, 'Keppra', 2)
    core = 'The initial therapeutic dose is 500 mg twice daily'
    assert core in re.sub(r'\s+', ' ', source['text'])
    draft = {'용법 용량': core + ', ' + suffix + '. [S1]'}
    before = deepcopy((draft, source))
    result = inspect_ra_draft(draft, [source], profile=profile)
    assert result['blocking'] and any(issue['code'] == 'ra_condition_omitted' for issue in result['issues'])
    assert (draft, source) == before


def test_real_complete_regimen_allows_benign_prefix_without_dropping_conditions(actual_pages):
    record, source, profile = real_source(actual_pages, 'Keppra', 2)
    value = next(fact['value'] for fact in record['facts'] if fact['field_key'] == '용법 용량')
    # A prefix destroys whole-value substring matching but changes no clinical clause.
    result = inspect_ra_draft({'용법 용량': 'Regimen: ' + re.sub(r'\s+', ' ', value) + ' [S1]'},
                              [source], profile=profile)
    assert not result['blocking'], result['issues']


def test_real_scalar_initial_dose_does_not_require_full_regimen_copy(actual_pages):
    _, source, profile = real_source(actual_pages, 'Keppra', 2)
    result = inspect_ra_draft({'Initial dose': '500 mg twice daily [S1]'}, [source], profile=profile)
    assert not result['blocking'], result['issues']


def test_real_reworded_chunk_uses_only_its_validated_original_span_for_conditions(actual_pages):
    _, source, profile = real_source(actual_pages, 'Keppra', 2)
    anchor = 'The initial therapeutic dose is 500 mg twice daily.'
    start = source['text'].find(anchor)
    assert start >= 0
    chunk = {**source, 'text': anchor, 'context_text': source['text'], 'context_start': start,
             'context_end': start + len(anchor)}
    result = inspect_ra_draft({'Dosage': anchor[:-1] + ', regardless of physician assessment. [S1]'},
                              [chunk], profile=profile)
    assert result['blocking'] and any(issue['code'] == 'ra_condition_omitted' for issue in result['issues'])
    # Nearby original text must not supply a quantity missing from the cited chunk itself.
    unsupported = inspect_ra_draft({'Initial dose': '250 mg twice daily [S1]'}, [chunk], profile=profile)
    assert unsupported['blocking'] and any(issue['code'] == 'ra_quantity_mismatch' for issue in unsupported['issues'])


@pytest.mark.parametrize('label,blocking', [('Unopened vial shelf life', False),
                                         ('Reconstituted shelf life', True),
                                         ('재구성 후 사용기간', True)])
def test_real_mapped_field_label_preserves_unopened_versus_reconstituted_scope(actual_pages, label, blocking):
    _, source, profile = real_source(actual_pages, 'Herzuma', 29)
    assert re.search(r'Unopened vial\s+6 years\.', source['text'])
    profile['fields'] = [{'value_key': '입력값_1', 'label': label, 'required': True}]
    result = inspect_ra_draft({'입력값_1': '6 years [S1]'}, [source], profile=profile)
    assert result['blocking'] is blocking, result['issues']
    if blocking:
        assert any(issue['code'] == 'ra_number_mismatch' for issue in result['issues'])


@pytest.mark.parametrize('label,amount,blocking', [('Initial dose', 8, False), ('Initial dose', 6, True),
                                               ('Maintenance dose', 6, False), ('Maintenance dose', 8, True)])
def test_real_mapped_field_label_preserves_loading_versus_maintenance(actual_pages, label, amount, blocking):
    _, source, profile = real_source(actual_pages, 'Herzuma', 4)
    profile['fields'] = [{'value_key': '정해진칸', 'label': label, 'required': True}]
    result = inspect_ra_draft({'정해진칸': f'{amount} mg/kg [S1]'}, [source], profile=profile)
    assert result['blocking'] is blocking, result['issues']
    if blocking:
        assert any(issue['code'] == 'ra_quantity_mismatch' for issue in result['issues'])


@pytest.mark.parametrize('other', [
    'Product: Veldra\nVeldra initial therapeutic dose is 10 mg twice daily. However, a lower dose of 5 mg twice daily may be used based on physician assessment.',
    'Novara maintenance dose is 20 mg twice daily. However, a lower dose of 15 mg twice daily may be used based on physician assessment.',
])
def test_synthetic_other_product_or_other_phase_dose_conditions_are_not_required(other):
    source = {'source_id': 'S1', 'text': 'Novara initial therapeutic dose is 10 mg twice daily. ' + other,
              'product_name': 'Novara', 'product_variant': 'Novara 10 mg tablet'}
    # Exact core has a harmless prefix, forcing the same anchor path as the bad real case.
    result = inspect_ra_draft({'Dosage': 'Regimen: Novara initial therapeutic dose is 10 mg twice daily. [S1]'},
                              [source], profile={'ra_product_name': 'Novara', 'ra_product_variant': 'Novara 10 mg tablet'})
    assert not result['blocking'], result['issues']


def test_synthetic_established_claim_requires_matching_positive_scope_but_preserves_negative_quote():
    source = {'source_id': 'S1', 'text': 'The safety and efficacy of Novara in adults has been established. '
              'The safety and efficacy of Novara in children has not been established.', 'product_name': 'Novara'}
    for quote in ['The safety and efficacy of Novara in adults has been established.',
                  'The safety and efficacy of Novara in children has not been established.']:
        checked = inspect_ra_draft({'Safety': quote + ' [S1]'}, [source])
        assert not checked['blocking'], checked['issues']
    changed = inspect_ra_draft({'Safety': 'The safety and efficacy of Novara in children has been established. [S1]'}, [source])
    assert changed['blocking'] and any(issue['code'] == 'ra_claim_unverified' for issue in changed['issues'])


@pytest.mark.parametrize('compact', [False, True])
def test_new_real_active_ingredient_sentence_is_not_a_different_product_heading(new_actual_pages, compact):
    record, source, profile = real_source(new_actual_pages, 'Ozempic', 3)
    value = next(fact['value'] for fact in record['facts'] if fact['field_key'] == '용법 용량')
    assert ''.join(value.split()) in ''.join(source['text'].split())
    if compact:
        value = re.sub(r'\s+', ' ', value)
    draft = {'Dosage': '\n'.join(line + ' [S1]' if line.strip() else '' for line in value.splitlines())}
    before = deepcopy((draft, source))
    checked = inspect_ra_draft(draft, [source], profile=profile)
    assert not checked['blocking'], checked['issues']
    assert (draft, source) == before


def test_new_real_not_a_maintenance_dose_is_not_positive_maintenance_evidence(new_actual_pages):
    _, source, profile = real_source(new_actual_pages, 'Ozempic', 3)
    quote = 'Semaglutide 0.25 mg is not a maintenance dose.'
    assert quote in re.sub(r'\s+', ' ', source['text'])
    good = inspect_ra_draft({'Dosage': quote + ' [S1]'}, [source], profile=profile)
    assert not good['blocking'], good['issues']
    bad = inspect_ra_draft({'Dosage': quote.replace('not ', '') + ' [S1]'}, [source], profile=profile)
    assert bad['blocking'] and any(issue['code'] == 'ra_statement_polarity_unverified' for issue in bad['issues'])


@pytest.mark.parametrize('product,page', [('Benepali', 4), ('Keppra', 5)])
def test_real_visual_line_wrap_does_not_hide_literal_negation_reversal(actual_pages, product, page):
    _, source, profile = real_source(actual_pages, product, page)
    quote = re.search(r'The safety and efficacy[^.]*not been established\.', source['text'])[0]
    # Benepali prints this short sentence on one line; exercise another harmless wrap too.
    if '\n' not in quote:
        quote = quote.replace('in children', 'in\nchildren')
    for value, expected in [(quote, False), (quote.replace('not been established', 'been established'), True)]:
        draft = {'Safety': '\n'.join(line + ' [S1]' for line in value.splitlines())}
        result = inspect_ra_draft(draft, [source], profile=profile)
        assert result['blocking'] is expected, result['issues']


def test_synthetic_other_product_negative_dose_does_not_negate_the_supported_product():
    sources = [{'source_id': 'S1', 'text': 'Novara 5 mg is a maintenance dose.', 'product_name': 'Novara'},
               {'source_id': 'S2', 'text': 'Veldra 5 mg is not a maintenance dose.', 'product_name': 'Veldra'}]
    checked = inspect_ra_draft({'Dosage': 'Novara 5 mg is a maintenance dose. [S1] [S2]'}, sources,
                               profile={'ra_product_name': 'Novara'})
    assert not checked['blocking'], checked['issues']


def test_new_real_per_ml_amount_cannot_be_copied_as_per_dose_without_conversion(new_actual_pages):
    record, source, profile = real_source(new_actual_pages, 'Ozempic', 2)
    quote = next(fact['value'] for fact in record['facts'] if fact['field_key'] == '원료약품 및 분량')
    assert ''.join(quote.split()) in ''.join(source['text'].split())
    for value in [quote, 'One ml of solution contains 1.34 mg of semaglutide*.']:
        checked = inspect_ra_draft({'원료약품 및 분량': re.sub(r'\s+', ' ', value) + ' [S1]'}, [source], profile=profile)
        assert not checked['blocking'], checked['issues']
    draft = {'원료약품 및 분량': 'Each dose contains 1.34 mg of semaglutide. [S1]'}
    before = deepcopy((draft, source))
    checked = inspect_ra_draft(draft, [source], profile=profile)
    assert checked['blocking'] and any(issue['code'] == 'ra_quantity_mismatch' for issue in checked['issues'])
    assert (draft, source) == before


def test_new_real_appearance_semicolon_is_not_a_reason_to_reject_the_exact_quote(new_actual_pages):
    record, source, profile = real_source(new_actual_pages, 'Ozempic', 3)
    value = next(fact['value'] for fact in record['facts'] if fact['field_key'] == '성상')
    assert ';' in value and 'pH=7.4' in value
    good = inspect_ra_draft({'성상': value + ' [S1]'}, [source], profile=profile)
    assert not good['blocking'], good['issues']
    bad = inspect_ra_draft({'성상': value.replace('pH=7.4', 'pH=7.5') + ' [S1]'}, [source], profile=profile)
    assert bad['blocking'], bad['issues']


@pytest.mark.parametrize('label,value,blocking', [('Adults maximum dose', '15 mg', False),
                                               ('Adults maximum dose', '10 mg', True),
                                               ('Paediatric maximum dose', '10 mg', False),
                                               ('Paediatric maximum dose', '15 mg', True)])
def test_new_real_maximum_cannot_use_maintenance_or_other_population_amount(new_actual_pages, label, value, blocking):
    _, source, profile = real_source(new_actual_pages, 'Mounjaro', 4)
    profile['fields'] = [{'value_key': 'max_value', 'label': label, 'required': True}]
    before = deepcopy((source, profile))
    result = inspect_ra_draft({'max_value': value + ' [S1]'}, [source], profile=profile)
    assert result['blocking'] is blocking, result['issues']
    if blocking:
        assert any(issue['code'] == 'ra_quantity_mismatch' for issue in result['issues'])
    assert (source, profile) == before
