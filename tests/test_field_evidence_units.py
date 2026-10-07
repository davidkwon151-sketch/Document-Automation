"""Declared review-only scalar units and exact product-name roles; no API calls."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from agent.brief import profile_fields
from agent.ra import inspect_ra_draft
from agent.review import inspect_draft, number_tokens, review_draft
from templates.value_rules import (field_evidence_scalar, inspect_form_values,
                                   mapped_rule_profile, parse_scalar, validate_rule_profile)

ROOT = Path(__file__).resolve().parents[1]
KEY = '누적 투약 임상시험 대상자 수'


def fixed_field(key=KEY, **updates):
    return dict({'id': 'count', 'kind': 'pdf_overlay', 'label': key, 'value_key': key,
                 'required': False, 'input_required': False, 'input_mode': 'source_grounded',
                 'narrative_style_required': False,
                 'validation': {'type': 'integer', 'min': 0, 'evidence_unit': '명'}}, **updates)


def draft(value, key=KEY):
    return {'제목': '검토', '요약': '○ 추가 확인 필요함', '본문': '○ 추가 확인 필요함', key: value}


def sources(value='12명', key=KEY):
    return [{'source_id': 'S1', 'text': f'{key} {value}'}]


def profile(field=None, **metadata):
    return {'fields': [field or fixed_field()], 'domain': 'pharmaceutical_ra',
            'ra_workflow': 'safety_management', **metadata}


@pytest.mark.parametrize('value', ['12', '12 [S1]', '12 [S1] [S2]', '+12 [S1]', '0012 [S1]'])
def test_review_unit_does_not_change_valid_bare_output(value):
    field = fixed_field()
    before = deepcopy((value, field))
    assert parse_scalar(value, field['validation']) == Decimal(12)
    assert field_evidence_scalar(value, field) == (Decimal(12), '명')
    assert number_tokens(value, field_meta=field)[0]['key'] == (Decimal(12), '명')
    assert not inspect_form_values({KEY: value}, profile(field))
    assert (value, field) == before


def test_matching_count_passes_both_reviews_without_rewriting_anything():
    p, d, s = profile(), draft('12 [S1]'), sources()
    before = deepcopy((p, d, s))
    assert not inspect_draft(d, s, template_profile=p)
    assert not inspect_ra_draft(d, s, profile=p)['blocking']
    result = review_draft(d, s, template_profile=p)
    assert result['draft'] == d and not result['blocking'] and not result['corrected']
    assert (p, d, s) == before


@pytest.mark.parametrize('value', ['120 [S1]', '12mL [S1]', '12명 [S1]', '12건 [S1]',
                                  '12.0 [S1]', '대상자 12 [S1]', '1[S1]2', '-12 [S1]'])
def test_wrong_value_unit_or_non_scalar_blocks_and_is_not_auto_corrected(value):
    d, p, s = draft(value), profile(), sources()
    result = review_draft(d, s, template_profile=p)
    assert result['blocking'] and result['draft'] == d and not result['corrected']
    assert inspect_ra_draft(d, s, profile=p)['blocking']


@pytest.mark.parametrize('source_value', ['12mL', '12건', '120명', '12'])
def test_source_must_keep_declared_evidence_unit_and_exact_value(source_value):
    p, d, s = profile(), draft('12 [S1]'), sources(source_value)
    assert inspect_draft(d, s, template_profile=p)
    assert inspect_ra_draft(d, s, profile=p)['blocking']


def test_llm_correction_cannot_silently_fix_declared_scalar():
    class MockClient:
        def generate_json(self, prompt, payload):
            return {'draft': draft('12 [S1]')}
    result = review_draft(draft('120 [S1]'), sources(), MockClient(), template_profile=profile())
    assert result['blocking'] and result['draft'][KEY] == '120 [S1]'


@pytest.mark.parametrize('key', ['제목', '요약', '본문'])
def test_review_unit_cannot_be_transplanted_to_narrative_or_title(key):
    with pytest.raises(ValueError):
        validate_rule_profile(profile(fixed_field(key)))


@pytest.mark.parametrize('updates', [{'kind': 'placeholder'}, {'kind': 'unknown'},
                                   {'narrative_style_required': True},
                                   {'control_type': 'choice', 'options': ['12']}])
def test_review_unit_requires_a_fixed_numeric_input(updates):
    with pytest.raises(ValueError):
        validate_rule_profile(profile(fixed_field(**updates)))


@pytest.mark.parametrize('unit', [None, '', ' 명', '명 ', True, [], '[S1]', '명\n'])
def test_malformed_evidence_unit_is_rejected(unit):
    with pytest.raises(ValueError):
        validate_rule_profile(profile(fixed_field(validation={'type': 'integer', 'evidence_unit': unit})))


@pytest.mark.parametrize('rule', [{'type': 'date', 'evidence_unit': '명'},
                                {'type': 'choice', 'options': ['12'], 'evidence_unit': '명'},
                                {'type': 'integer', 'unit': 'mL', 'evidence_unit': '명'}])
def test_evidence_unit_does_not_relax_date_choices_or_other_units(rule):
    with pytest.raises(ValueError):
        validate_rule_profile(profile(fixed_field(validation=rule)))


def test_undeclared_numeric_field_and_narrative_do_not_gain_an_evidence_unit():
    field = fixed_field(validation={'type': 'integer'})
    assert number_tokens('12 [S1]', field_meta=field)[0]['key'][1] == ''
    assert field_evidence_scalar('12', field) is None
    assert inspect_draft(draft('12 [S1]'), sources(), template_profile=profile(field))
    assert number_tokens(f'{KEY} 12 [S1]')[0]['key'][1] == ''


@pytest.mark.parametrize('expected,actual', [('소아', '성인'), ('성인', '소아')])
def test_scalar_unit_bridge_preserves_explicit_population_scope(expected, actual):
    key = f'{expected} {KEY}'
    p = profile(fixed_field(key))
    result = inspect_ra_draft(draft('12 [S1]', key), sources('12명', f'{actual} {KEY}'), profile=p)
    assert result['blocking']
    assert 'ra_number_mismatch' in {item['code'] for item in result['issues']}
    assert not inspect_ra_draft(draft('12 [S1]', key), sources('12명', key), profile=p)['blocking']


def test_scalar_unit_bridge_preserves_selected_product_scope():
    p = profile(ra_product_name='ProductA')
    s = sources()
    s[0]['product_name'] = 'ProductB'
    assert inspect_ra_draft(draft('12 [S1]'), s, profile=p)['blocking']
    s[0]['product_name'] = 'ProductA'
    assert not inspect_ra_draft(draft('12 [S1]'), s, profile=p)['blocking']


def test_remapping_preserves_review_unit_without_adding_printed_unit():
    renamed = mapped_rule_profile(profile(), {'count': '시험 대상자 총수'})
    assert renamed['fields'][0]['validation'] == fixed_field()['validation']
    assert 'unit' not in renamed['fields'][0]['validation']
    assert not inspect_form_values({'시험 대상자 총수': '12 [S1]'}, renamed)


@pytest.mark.parametrize('kind', ['sum', 'less_equal'])
def test_relation_cannot_combine_different_review_measurement_units(kind):
    fields = [fixed_field('총원', id='total'), fixed_field('인원', id='people'),
              fixed_field('용량', id='amount', validation={'type': 'integer', 'evidence_unit': 'mL'})]
    relation = ({'kind': 'sum', 'total': '총원', 'parts': ['인원', '용량']} if kind == 'sum'
                else {'kind': 'less_equal', 'left': '인원', 'right': '용량'})
    with pytest.raises(ValueError):
        validate_rule_profile({'fields': fields, 'constraints': {'relations': [relation]}})


def test_same_review_units_sum_exact_bare_values_without_conversion():
    p = {'fields': [fixed_field(key, id=key) for key in ['합계', '성인 인원', '소아 인원']],
         'constraints': {'relations': [{'kind': 'sum', 'total': '합계', 'parts': ['성인 인원', '소아 인원']}]}}
    values = {'합계': '12 [S1]', '성인 인원': '10 [S1]', '소아 인원': '2 [S1]'}
    before = deepcopy(values)
    assert not inspect_form_values(values, p) and values == before
    values['합계'] = '120 [S1]'
    assert any(i['code'] == 'form_relation_sum' for i in inspect_form_values(values, p))


def test_same_unit_and_value_from_another_scalar_context_is_not_supported():
    d, p, s = draft('12 [S1]'), profile(), sources('12명', '연구자')
    assert inspect_draft(d, s, template_profile=p)
    assert inspect_ra_draft(d, s, profile=p)['blocking']


@pytest.mark.parametrize('number,key', [('23', '시험약 제품명'), ('32', '제품명 성분명')])
def test_registered_product_alias_rejects_a_known_citation_for_another_product(number, key):
    p = json.loads((ROOT / f'templates/profiles/ra_law_form_{number}_pdf.json').read_text(encoding='utf-8'))
    assert next(f for f in p['fields'] if f['value_key'] == key)['evidence_role'] == 'product_name'
    wrong = inspect_ra_draft(draft('합성약A [S1]', key), [{'source_id': 'S1', 'text': '합성약B 시험 함량 5mg'}], profile=p)
    assert wrong['blocking'] and any(i['code'] == 'ra_product_unverified' for i in wrong['issues'])
    assert not inspect_ra_draft(draft('합성약A [S1]', key), [{'source_id': 'S1', 'text': '제품명 합성약A'}], profile=p)['blocking']


@pytest.mark.parametrize('key', ['시험약 제품명', '제품명 성분명'])
def test_product_role_keeps_selected_product_strength_scope(key):
    f = {'id': key, 'value_key': key, 'label': key, 'kind': 'pdf_overlay',
         'required': False, 'evidence_role': 'product_name'}
    p = profile(f, ra_product_name='DrugA', ra_product_variant='10 mg tablet')
    s = [{'source_id': 'S1', 'text': 'DrugA 5 mg tablet', 'product_name': 'DrugA',
          'product_variant': '5 mg tablet'}]
    result = inspect_ra_draft(draft('DrugA 5 mg tablet [S1]', key), s, profile=p)
    assert result['blocking'] and any(i['code'] == 'ra_variant_mismatch' for i in result['issues'])


def test_real_ra32_bare_count_saves_without_printing_review_unit_or_citation(tmp_path):
    from agent.output_check import verify_output
    from templates import fill_compatible_template
    from pypdf import PdfReader
    p = json.loads((ROOT / 'templates/profiles/ra_law_form_32_pdf.json').read_text(encoding='utf-8'))
    source = ROOT / 'data/public_templates/ra/ra_law_form_32_20260305.pdf'
    before = source.read_bytes()
    values = {KEY: '12'}
    output = fill_compatible_template(source, values, tmp_path / 'review.pdf', profile=p)
    assert verify_output(source, output, values, profile=p)['status'] == 'passed'
    assert len(PdfReader(output).pages) == 2 and source.read_bytes() == before


@pytest.mark.parametrize('role', [None, '', 'patient_count', True, ['product_name']])
def test_malformed_or_unknown_product_role_is_rejected_before_model_input(role):
    p = {'fields': [{'id': 'product', 'value_key': '시험약 제품명', 'evidence_role': role}]}
    with pytest.raises(ValueError):
        profile_fields(p)


def test_conflicting_roles_for_the_same_value_key_are_rejected():
    p = {'fields': [{'id': 'one', 'value_key': '제품', 'evidence_role': 'product_name'},
                    {'id': 'two', 'value_key': '제품'}]}
    with pytest.raises(ValueError):
        validate_rule_profile(p)


def test_updated_catalog_profile_binding_and_raw_source_are_exact():
    catalog = json.loads((ROOT / 'templates/ra_mvp_catalog.json').read_text(encoding='utf-8'))
    for number in ('23', '32'):
        record = next(f for f in catalog['forms'] if f['id'] == f'ra_law_form_{number}_pdf')
        assert hashlib.sha256((ROOT / record['profile_path']).read_bytes()).hexdigest() == record['profile_sha256']
        assert hashlib.sha256((ROOT / record['source_path']).read_bytes()).hexdigest() == record['source_sha256']
    p = json.loads((ROOT / 'templates/profiles/ra_law_form_32_pdf.json').read_text(encoding='utf-8'))
    field = next(f for f in p['fields'] if f['value_key'] == KEY)
    assert field['validation'] == {'type': 'integer', 'min': 0, 'evidence_unit': '명'}
    assert p['demo_values'][KEY] == '12'
    assert len(p['fields']) == 14
    assert '명' not in p['validation_evidence']['fields'][KEY]['printed_labels']
