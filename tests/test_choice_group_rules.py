"""Closed selections and optional-row completeness on the flat string contract."""
from copy import deepcopy
import json

import pytest

from templates.value_rules import inspect_form_values, mapped_rule_profile, plain_form_value, validate_rule_profile


def field(key, validation=None, **metadata):
    result = dict(id=metadata.pop('id', key), value_key=key, label=key, required=False, **metadata)
    if validation is not None:
        result['validation'] = validation
    return result


def profile(*fields, groups=None, relations=None):
    return {'fields': list(fields), 'constraints': {'groups': groups or [], 'relations': relations or []}}


@pytest.mark.parametrize('value', ['승인', '보류', '승인 [S1]', '승인 [Sdoc_2-A] [S2]', '  승인  '])
def test_declared_choice_uses_exact_option_text_and_keeps_original_input(value):
    p = profile(field('결정', {'type': 'choice', 'options': ['승인', '보류']}))
    values = {'결정': value}
    original = deepcopy((p, values))
    assert not inspect_form_values(values, p)
    assert (p, values) == original


@pytest.mark.parametrize('value', ['승인함', '승 인', '승인/보류', '승[S1]인', '승인 [S]', True, 1, ['승인']])
def test_unknown_incomplete_or_non_string_choice_is_blocked(value):
    issues = inspect_form_values({'결정': value}, profile(field('결정', {'type': 'choice', 'options': ['승인', '보류']})))
    assert issues[0]['code'] == 'form_value_choice' and issues[0]['field'] == '결정'


def test_choice_is_case_sensitive_and_preserves_distinct_number_codes():
    p = profile(field('분류', {'type': 'choice', 'options': ['Yes', 'yes', '001', '1']}))
    assert not inspect_form_values({'분류': '001'}, p)
    assert not inspect_form_values({'분류': 'yes'}, p)
    assert inspect_form_values({'분류': 'YES'}, p)
    assert inspect_form_values({'분류': '01'}, p)


def test_internal_spaces_in_option_labels_are_kept_exact():
    p = profile(field('분류', {'type': 'choice', 'options': ['제품 승인', '변경 승인']}))
    assert not inspect_form_values({'분류': '제품 승인'}, p)
    assert inspect_form_values({'분류': '제품  승인'}, p)


def test_optional_choice_blank_is_allowed_but_required_choice_is_not():
    p = profile(field('결정', {'type': 'choice', 'options': ['승인']}))
    assert not inspect_form_values({'결정': ' [S1] '}, p)
    p['fields'][0]['required'] = True
    assert inspect_form_values({}, p)[0]['code'] == 'form_value_required'


@pytest.mark.parametrize('options', [None, [], '', [''], [' '], ['승인', '승인'], [' 승인'], ['승인 '], [1], [True], [[]]])
def test_malformed_choice_options_are_rejected(options):
    with pytest.raises(ValueError):
        validate_rule_profile(profile(field('결정', {'type': 'choice', 'options': options})))


@pytest.mark.parametrize('extra', [{'unit': '건'}, {'unit_location': 'label'}, {'min': 0}, {'max': 1},
                                  {'decimal_places': 0}, {'date_formats': ['YYYY-MM-DD']}, {'guess': True}])
def test_choice_cannot_mix_numeric_date_or_unknown_metadata(extra):
    with pytest.raises(ValueError):
        validate_rule_profile(profile(field('결정', {'type': 'choice', 'options': ['승인'], **extra})))


@pytest.mark.parametrize('kind', ['integer', 'number', 'date'])
def test_options_are_not_accepted_on_other_validation_types(kind):
    with pytest.raises(ValueError):
        validate_rule_profile(profile(field('결정', {'type': kind, 'options': ['승인']})))


@pytest.mark.parametrize('control', ['choice', 'radio'])
def test_native_options_supply_the_closed_rule_without_explicit_validation(control):
    p = profile(field('결정', control_type=control, options=['승인', '보류']))
    original = deepcopy(p)
    assert not inspect_form_values({'결정': '승인 [S1]'}, p)
    assert inspect_form_values({'결정': '거절'}, p)[0]['code'] == 'form_value_choice'
    assert p == original and 'validation' not in p['fields'][0]
    renamed = mapped_rule_profile(p, {'결정': '최종 선택'})
    assert renamed['fields'][0]['validation'] == {'type': 'choice', 'options': ['승인', '보류']}
    assert not inspect_form_values({'최종 선택': '보류'}, renamed)
    assert inspect_form_values({'최종 선택': '거절'}, renamed)


def test_explicit_native_choice_set_is_order_agnostic_but_cannot_widen_narrow_or_change():
    p = profile(field('결정', {'type': 'choice', 'options': ['보류', '승인']}, control_type='choice', options=['승인', '보류']))
    assert not inspect_form_values({'결정': '승인'}, p)
    for rule in [{'type': 'choice', 'options': ['승인']},
                 {'type': 'choice', 'options': ['승인', '보류', '거절']},
                 {'type': 'choice', 'options': ['승인', '거절']}, {'type': 'integer'}]:
        p['fields'][0]['validation'] = rule
        with pytest.raises(ValueError, match='원본 control'):
            validate_rule_profile(p)


def test_shared_choice_keys_have_the_same_set_regardless_of_option_order():
    p = profile(field('분류', {'type': 'choice', 'options': ['A', 'B']}, id='one'),
                field('분류', {'type': 'choice', 'options': ['B', 'A']}, id='two'))
    assert not inspect_form_values({'분류': 'A'}, p)
    p['fields'][1]['validation']['options'] = ['A', 'C']
    with pytest.raises(ValueError, match='상충'):
        validate_rule_profile(p)


def test_native_checkbox_keeps_existing_renderer_semantics():
    p = profile(field('동의', control_type='checkbox', options=['/Yes']))
    assert not inspect_form_values({'동의': 'true'}, p)
    assert not inspect_form_values({'동의': 'false'}, p)
    assert 'validation' not in mapped_rule_profile(p)['fields'][0]


@pytest.mark.parametrize('options', [[], [''], ['A', 'A'], [True]])
def test_invalid_native_option_metadata_cannot_silently_disable_the_closed_rule(options):
    with pytest.raises(ValueError):
        validate_rule_profile(profile(field('결정', control_type='choice', options=options)))


def test_invalid_native_control_type_has_a_schema_error_instead_of_hashing_failure():
    with pytest.raises(ValueError, match='control_type'):
        validate_rule_profile(profile(field('결정', control_type=[], options=['A'])))


def test_different_native_option_sets_cannot_share_the_same_logical_value():
    p = profile(field('결정', id='one', control_type='choice', options=['A', 'B']),
                field('결정', id='two', control_type='radio', options=['B', 'C']))
    with pytest.raises(ValueError, match='상충'):
        validate_rule_profile(p)


def row_profile(*, required_fields=None):
    group = {'kind': 'all_or_none', 'fields': ['품목1', '수량1', '금액1', '설명1']}
    if required_fields is not None:
        group['required_fields'] = required_fields
    return profile(field('품목1'), field('수량1', {'type': 'integer', 'min': 0}),
                   field('금액1', {'type': 'number', 'unit': '원', 'unit_location': 'label', 'min': 0}),
                   field('설명1'), groups=[group])


def test_optional_all_blank_row_is_skipped_including_citation_only_strings():
    p = row_profile()
    assert not inspect_form_values({}, p)
    assert not inspect_form_values({'품목1': ' [S1]', '수량1': ' ', '금액1': '', '설명1': '\n'}, p)


def test_partly_filled_row_requires_every_member_by_default_and_zero_counts_as_input():
    issues = inspect_form_values({'수량1': '0'}, row_profile())
    assert {issue['field'] for issue in issues} == {'품목1', '금액1', '설명1'}
    assert {issue['code'] for issue in issues} == {'form_group_required'}
    assert all(issue['severity'] == 'error' and issue['line'] == 1 for issue in issues)


def test_explicit_required_subset_allows_optional_description_but_activates_on_it():
    p = row_profile(required_fields=['품목1', '수량1', '금액1'])
    assert not inspect_form_values({'품목1': '시험부품 [S1]', '수량1': '2', '금액1': '0.1'}, p)
    issues = inspect_form_values({'설명1': '추가 검토함'}, p)
    assert {issue['field'] for issue in issues} == {'품목1', '수량1', '금액1'}


def test_group_free_narrative_has_no_numeric_limit_and_keeps_internal_citations():
    p = row_profile(required_fields=['설명1'])
    values = {'설명1': '앞 문장 [S1] 다음 문장 ' + '긴 설명 ' * 3000 + '[S2]'}
    original = deepcopy(values)
    assert not inspect_form_values(values, p)
    assert values == original
    assert plain_form_value(values['설명1']).startswith('앞 문장 [S1] 다음 문장')


@pytest.mark.parametrize('bad', [None, 1, True, []])
def test_group_referenced_free_values_still_require_flat_strings(bad):
    issues = inspect_form_values({'설명1': bad}, row_profile(required_fields=['설명1']))
    assert any(issue['code'] == 'form_group_value' and issue['field'] == '설명1' for issue in issues)


@pytest.mark.parametrize('group', [None, [], {'kind': 'unknown', 'fields': ['품목1']},
    {'kind': 'all_or_none'}, {'kind': 'all_or_none', 'fields': []},
    {'kind': 'all_or_none', 'fields': '품목1'}, {'kind': 'all_or_none', 'fields': ['품목1', '품목1']},
    {'kind': 'all_or_none', 'fields': ['없는항목']}, {'kind': 'all_or_none', 'fields': [True]},
    {'kind': 'all_or_none', 'fields': ['품목1'], 'required_fields': []},
    {'kind': 'all_or_none', 'fields': ['품목1'], 'required_fields': ['수량1']},
    {'kind': 'all_or_none', 'fields': ['품목1'], 'required_fields': ['품목1', '품목1']},
    {'kind': 'all_or_none', 'fields': ['품목1'], 'required_fields': True},
    {'kind': 'all_or_none', 'fields': ['품목1'], 'auto_fill': True}])
def test_malformed_unknown_or_duplicate_group_metadata_is_rejected(group):
    p = row_profile()
    p['constraints']['groups'] = [group]
    with pytest.raises(ValueError):
        validate_rule_profile(p)


def test_duplicate_groups_are_rejected_independently_of_field_order_or_required_subset():
    p = row_profile()
    p['constraints']['groups'].append({'kind': 'all_or_none', 'fields': list(reversed(p['constraints']['groups'][0]['fields'])),
                                     'required_fields': ['품목1']})
    with pytest.raises(ValueError, match='중복'):
        validate_rule_profile(p)
    p['constraints']['groups'] = None
    with pytest.raises(ValueError):
        validate_rule_profile(p)


def test_groups_and_relations_rebind_together_without_mutation_or_values_in_metadata():
    p = row_profile(required_fields=['품목1', '수량1', '금액1'])
    p['fields'].append(field('상한', {'type': 'integer', 'min': 0}))
    p['constraints']['relations'] = [{'kind': 'less_equal', 'left': '수량1', 'right': '상한'}]
    original = deepcopy(p)
    mapping = {'품목1': '품목', '수량1': '수량', '금액1': '금액', '설명1': '비고', '상한': '한도'}
    new = mapped_rule_profile(p, mapping)
    assert new['constraints']['groups'] == [{'kind': 'all_or_none', 'fields': ['품목', '수량', '금액', '비고'],
                                            'required_fields': ['품목', '수량', '금액']}]
    assert new['constraints']['relations'] == [{'kind': 'less_equal', 'left': '수량', 'right': '한도'}]
    values = {'품목': '공개 부품 [S1]', '수량': '2 [S1]', '금액': '0.1 [S2]', '한도': '3'}
    assert not inspect_form_values(values, new)
    assert p == original and new == json.loads(json.dumps(new, ensure_ascii=False))
    assert '공개 부품' not in json.dumps(new, ensure_ascii=False)
    values['한도'] = '1'
    assert inspect_form_values(values, new)[0]['code'] == 'form_relation_less_equal'
    del values['금액']
    assert any(issue['code'] == 'form_group_required' and issue['field'] == '금액'
               for issue in inspect_form_values(values, new))


@pytest.mark.parametrize('mapping', [{'품목1': '품목', '수량1': '수량', '금액1': '금액'},
                                    {'품목1': '값', '수량1': '값', '금액1': '금액', '설명1': '비고'}])
def test_group_member_omission_or_collapse_cannot_bypass_completeness(mapping):
    with pytest.raises(ValueError):
        mapped_rule_profile(row_profile(), mapping)


def test_repeated_group_member_cannot_split_destinations_or_drop_physical_mirrors():
    p = row_profile()
    p['fields'].append(field('품목1', id='copy'))
    mapping = {field['id']: field['value_key'] for field in p['fields']}
    mapping['copy'] = '다른 품목'
    with pytest.raises(ValueError, match='나눌'):
        mapped_rule_profile(p, mapping)
    del mapping['copy']
    with pytest.raises(ValueError, match='제외'):
        mapped_rule_profile(p, mapping)


def test_default_required_group_members_remain_default_when_remapped():
    p = row_profile()
    new = mapped_rule_profile(p, {field['id']: '새 ' + field['value_key'] for field in p['fields']})
    assert 'required_fields' not in new['constraints']['groups'][0]
    issues = inspect_form_values({'새 품목1': '부품'}, new)
    assert {issue['field'] for issue in issues} == {'새 수량1', '새 금액1', '새 설명1'}
