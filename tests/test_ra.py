"""약품 단위·식별자·주장 grounding 검증; 네트워크/실제 모델 호출 없음."""

from copy import deepcopy

import pytest

from agent.ra import RA_WORKFLOWS, inspect_ra_draft


def inspect(text, source, **kwargs):
    return inspect_ra_draft({'본문': text}, [{'source_id': 'S1', 'text': source}], **kwargs)


@pytest.mark.parametrize('unit', ['mg', 'µg', 'μg', 'mcg', '%', 'mg/mL', 'mg/kg'])
def test_grounded_drug_units(unit):
    result = inspect(f'용량 5 {unit} [S1]', f'용량 5 {unit}')
    assert not result['blocking'], result


@pytest.mark.parametrize('draft,source', [('용량 5 mg', '용량 50 mg'), ('농도 5 mg/mL', '농도 5 mg'),
                                         ('용량 5 mg/kg', '용량 5 mg'), ('용량 1 mg', '용량 1000 mcg')])
def test_wrong_drug_quantity_or_conversion_blocks(draft, source):
    assert inspect(draft + ' [S1]', source)['blocking']


def test_micro_symbol_typography_does_not_convert_value():
    assert not inspect('용량 5 µg [S1]', '용량 5 mcg')['blocking']


def test_same_quantity_from_wrong_batch_is_not_supported():
    result = inspect('Batch No: B001 용량 10 mg [S1]', 'Batch No: B001 용량 5 mg; Batch No: B002 용량 10 mg')
    assert result['blocking']
    assert any(item['code'] == 'ra_quantity_mismatch' for item in result['issues'])


def test_protocol_and_batch_exact_grounding():
    text = 'Protocol ID: P-2026-01 Batch No: B001 용량 5 mg'
    assert not inspect(text + ' [S1]', text)['blocking']
    assert inspect(text.replace('P-2026-01', 'P-2026-02') + ' [S1]', text)['blocking']


def test_unknown_or_missing_source_blocks():
    assert inspect('용량 5 mg [S999]', '용량 5 mg')['blocking']
    assert inspect('용량 5 mg', '용량 5 mg')['blocking']


def test_source_other_than_cited_source_not_used():
    result = inspect_ra_draft({'본문': '용량 10 mg [S1]'}, [
        {'source_id': 'S1', 'text': '용량 5 mg'}, {'source_id': 'S2', 'text': '용량 10 mg'}])
    assert result['blocking']


@pytest.mark.parametrize('claim', ['안전성 확인됨', '효능 입증됨', '승인 완료함', '임상시험 완료함', 'GMP 인증 취득함',
                                  'Safety established', 'Product approved', 'Clinical trial completed'])
def test_unsupported_claim_blocks(claim):
    assert inspect(claim + ' [S1]', '시험 계획 검토 중')['blocking']


def test_exact_claim_supported_and_negated_source_does_not_support():
    assert not inspect('임상시험 완료함 [S1]', '임상시험 완료함')['blocking']
    assert inspect('임상시험 완료함 [S1]', '임상시험 완료되지 않음')['blocking']
    assert not inspect('추가 확인 필요함', '')['blocking']
    assert not inspect('승인 전 확인 필요함', '')['blocking']


def test_full_dates_normalize_format_only():
    assert not inspect('승인일 2026-09-17 [S1]', '승인일 2026년 9월 17일')['blocking']
    assert inspect('승인일 2026-09-18 [S1]', '승인일 2026-09-17')['blocking']
    assert inspect('승인일 2026-02-30 [S1]', '승인일 2026-02-30')['blocking']


def test_same_date_from_other_batch_does_not_support():
    result = inspect('Batch No: B001 시험일 2026-09-18 [S1]',
                     'Batch No: B001 시험일 2026-09-17; Batch No: B002 시험일 2026-09-18')
    assert result['blocking']


def test_pending_other_claim_does_not_hide_positive_claim():
    result = inspect('안전성 확인됨, 효능 확인 필요함 [S1]', '효능 확인 필요함')
    assert result['blocking']
    assert inspect('안전성 확인됨 [S1]', '안전성 확인되지 않음')['blocking']
    assert inspect('Product approved [S1]', 'Product has not been approved')['blocking']
    assert not inspect('Product has not been approved [S1]', 'Product has not been approved')['blocking']


def test_effective_date_label_is_not_efficacy_claim():
    assert not inspect('Effective date: 2026-09-17 [S1]', 'Effective date: 2026-09-17')['blocking']


def test_ambiguous_date_warns_without_guessing():
    result = inspect('시험일 03/04/26 [S1]', '시험일 03/04/26')
    assert not result['blocking']
    assert any(item['code'] == 'ra_ambiguous_date' for item in result['issues'])


def test_regimen_and_route_are_grounded():
    source = '용법 경구 1일 2회'
    assert not inspect(source + ' [S1]', source)['blocking']
    assert inspect('용법 정맥 1일 2회 [S1]', source)['blocking']
    assert inspect('용법 경구 1일 3회 [S1]', source)['blocking']


def test_counts_not_hidden_by_special_unit_checker():
    assert not inspect('이상사례 3건 [S1]', '이상사례 3건')['blocking']
    assert inspect('이상사례 0건 [S1]', '이상사례 3건')['blocking']


def test_fixed_fields_and_ctd_original_not_styled_or_mutated():
    draft = {'Dose': '5 mg [S1]', 'Protocol': 'Protocol ID: P01 [S1]', 'Heading': 'Module 2 overview [S1]'}
    sources = [{'source_id': 'S1', 'text': '5 mg; Protocol ID: P01; Module 2 overview'}]
    baseline = deepcopy((draft, sources))
    result = inspect_ra_draft(draft, sources, profile={'domain': 'pharmaceutical_ra', 'ra_workflow': 'product_approval'})
    assert not result['blocking'], result
    assert (draft, sources) == baseline
    assert result['additional_checks']
    assert not any('style' in item['code'] for item in result['issues'])
    assert any(item['status'] == 'not_certified' for item in result['checks'])


def test_checklists_are_advisory_not_missing_content_errors():
    assert set(RA_WORKFLOWS) == {'product_approval', 'variation', 'clinical_trial', 'dmf', 'gmp', 'manufacturing_import', 'safety_management', 'testing_support'}
    for workflow in RA_WORKFLOWS:
        result = inspect_ra_draft({}, [], profile={'ra_workflow': workflow})
        assert not result['blocking']
        assert all('추가 확인사항' in item for item in result['additional_checks'])
        assert all(url.startswith('https://') for url in RA_WORKFLOWS[workflow]['source_urls'])


@pytest.mark.parametrize('draft,sources,profile', [({'본문': 1}, [], None), ({}, {}, None), ({}, [], []),
    ({}, [{'source_id': 'S1', 'text': 'a'}, {'source_id': 'S1', 'text': 'b'}], None), ({}, [], {'ra_workflow': 'fake'})])
def test_invalid_inputs_rejected(draft, sources, profile):
    with pytest.raises(ValueError):
        inspect_ra_draft(draft, sources, profile=profile)


@pytest.mark.parametrize('good,bad,source', [
    ('Product: Alpha 용량 50 mg', 'Product: Alpha 용량 100 mg',
     'Product: Alpha 용량 50 mg; Product: Beta 용량 100 mg'),
    ('성인 권장용량 50 mg', '성인 권장용량 100 mg',
     '성인 권장용량 50 mg; 소아 권장용량 100 mg'),
    ('Alpha 100 mg every 2 weeks', 'Alpha 100 mg every 4 weeks',
     'Alpha 100 mg every 2 weeks; Beta 50 mg every 4 weeks'),
    ('Alpha 100 mg every 2 weeks', 'Alpha 100 mg every 4 weeks',
     'Alpha 100 mg every 2 weeks; Alpha 50 mg every 4 weeks'),
])
def test_scope_and_interval_paired_synthetic_counterexamples(good, bad, source):
    assert not inspect(good + ' [S1]', source)['blocking']
    assert inspect(bad + ' [S1]', source)['blocking']


def test_volume_denominator_and_spacing_are_compared_without_unit_conversion():
    source = 'KEYTRUDA 100 mg/4 mL (25 mg/mL)'
    assert not inspect('KEYTRUDA 100 mg / 4 mL [S1]', source)['blocking']
    assert inspect('KEYTRUDA 100 mg/5 mL [S1]', source)['blocking']
    assert inspect('KEYTRUDA 100 mg/mL [S1]', source)['blocking']


def test_interval_word_and_digit_are_same_not_days_to_weeks_conversion():
    assert not inspect('Alpha 100 mg every 4 weeks [S1]', 'Alpha 100 mg every four weeks')['blocking']
    assert inspect('Alpha 100 mg every 28 days [S1]', 'Alpha 100 mg every four weeks')['blocking']


def test_fixed_population_label_is_used_even_when_value_has_no_population_words():
    sources = [{'source_id': 'S1', 'text': 'Adult recommended dose: 50 mg; Pediatric recommended dose: 100 mg'}]
    assert not inspect_ra_draft({'Adult dose': '50 mg [S1]'}, sources)['blocking']
    assert inspect_ra_draft({'Adult dose': '100 mg [S1]'}, sources)['blocking']


def test_selected_variant_does_not_turn_clinical_dose_into_vial_strength():
    profile = {'ra_product_name': 'Alpha', 'ra_product_variant': 'Alpha 50 mg vial'}
    sources = [{'source_id': 'S1', 'text': 'Alpha 50 mg vial\nRecommended dose: 100 mg',
                'product_name': 'Alpha', 'product_variant': 'Alpha 50 mg vial'}]
    assert not inspect_ra_draft({'Recommended dose': '100 mg [S1]'}, sources, profile=profile)['blocking']
    assert inspect_ra_draft({'제품명': 'Alpha 100 mg vial [S1]'}, sources, profile=profile)['blocking']


@pytest.mark.parametrize('bad', ['5 mgg', '5 mg/ML', '5 ng', '5'])
def test_unit_typo_missing_unit_and_same_number_different_mass_unit_are_blocked(bad):
    assert not inspect('용량 5 mg [S1]', '용량 5 mg')['blocking']
    result = inspect('용량 ' + bad + ' [S1]', '용량 5 mg')
    assert result['blocking']
    assert any(item['code'] in {'ra_unit_unrecognized', 'ra_quantity_mismatch', 'ra_unit_missing'} for item in result['issues'])


def test_explicit_form_requirements_are_checked_without_promoting_advisory_checklists():
    profile = {'ra_workflow': 'product_approval', 'fields': [
        {'value_key': '함량', 'required': True}, {'value_key': '비고', 'required': False}]}
    assert inspect_ra_draft({}, [], profile=profile)['blocking']
    result = inspect_ra_draft({'함량': '5 mg [S1]'}, [{'source_id': 'S1', 'text': '5 mg'}], profile=profile)
    assert not result['blocking']
    assert result['additional_checks']


def test_selected_product_scope_applies_to_dates_and_non_numeric_claims():
    source = {'source_id': 'S1', 'product_name': 'Alpha', 'text': '승인일 2026-09-17; 안전성 확인됨'}
    draft = {'승인일': '2026-09-17 [S1]', '안전성': '안전성 확인됨 [S1]'}
    assert not inspect_ra_draft(draft, [source], profile={'ra_product_name': 'Alpha'})['blocking']
    wrong = inspect_ra_draft(draft, [source], profile={'ra_product_name': 'Beta'})
    assert wrong['blocking']
    assert {'ra_date_mismatch', 'ra_claim_unverified'} <= {issue['code'] for issue in wrong['issues']}
