"""Declared scalar/relationship constraints, not guesses from form captions."""
from copy import deepcopy
from decimal import getcontext
import json

import pytest

from templates.value_rules import inspect_form_values, mapped_rule_profile, validate_rule_profile


def field(key, rule, *, required=False, identifier=None):
    return {'id': identifier or key, 'value_key': key, 'label': key,
            'validation': rule, 'required': required}


def profile(*fields, relations=None):
    return {'fields': list(fields), 'constraints': {'relations': relations or []}}


def scalar(rule, value, **kwargs):
    return inspect_form_values({'값': value}, profile(field('값', rule, **kwargs)))


@pytest.mark.parametrize('value', ['0명', '10명', '1,234 명', '+12명', '001명', '1,234명 [Sabc_1-2] [S2]'])
def test_integer_unit_and_valid_trailing_citations(value):
    assert not scalar({'type': 'integer', 'unit': '명', 'min': 0}, value)


@pytest.mark.parametrize('value', ['-1명', '1,00명', '1234,000명', '0,000명', '01,000명', '1.0명', '1e3명',
                                 '1_000명', 'NaN명', 'Infinity명', '１０명', '인원 10명임', '10 명 증가',
                                 '10건', '10', '1[S1]0명', '10명 [S]', '10명 [unknown]', 10, True, None])
def test_invalid_integer_or_hidden_citation_blocks_without_extracting_a_substring(value):
    assert scalar({'type': 'integer', 'unit': '명', 'min': 0}, value)


@pytest.mark.parametrize('value', ['0.1원', '1,000.25 원', '-0.25원'])
def test_decimal_values_remain_exact(value):
    assert not scalar({'type': 'number', 'unit': '원', 'min': '-0.25', 'max': '1000.25', 'decimal_places': 2}, value)


@pytest.mark.parametrize('value,code', [('100.001원', 'form_value_decimal_places'), ('-0.01원', 'form_value_min'),
                                     ('101원', 'form_value_max')])
def test_range_and_written_fraction_precision(value, code):
    issues = scalar({'type': 'number', 'unit': '원', 'min': 0, 'max': 100, 'decimal_places': 2}, value)
    assert code in {issue['code'] for issue in issues}
    assert all(set(issue) == {'code', 'field', 'line', 'message', 'severity'} for issue in issues)
    assert all(issue['severity'] == 'error' and issue['line'] == 1 for issue in issues)


def test_written_trailing_zeroes_are_precision_too():
    assert scalar({'type': 'number', 'decimal_places': 1}, '1.00')
    assert not scalar({'type': 'number', 'decimal_places': 2}, '1.00')


@pytest.mark.parametrize('value', ['5㎡', '5m2', '0.0005 km²', '면적 5m²', '5 m² 및 부속토지'])
def test_units_are_literal_and_never_converted(value):
    assert scalar({'type': 'number', 'unit': 'm²'}, value)
    assert not scalar({'type': 'number', 'unit': 'm²'}, '5 m²')


def test_label_unit_is_not_printed_twice_but_still_has_relation_units():
    rule = {'type': 'integer', 'unit': '명', 'unit_location': 'label', 'min': 0}
    assert not scalar(rule, '1,000 [S1]')
    assert scalar(rule, '1,000명') and scalar(rule, '1,000건')
    p = profile(field('이수', rule), field('전체', {'type': 'integer', 'unit': '명'}),
                relations=[{'kind': 'less_equal', 'left': '이수', 'right': '전체'}])
    assert not inspect_form_values({'이수': '10', '전체': '12명'}, p)
    assert inspect_form_values({'이수': '13', '전체': '12명'}, p)[0]['code'] == 'form_relation_less_equal'


@pytest.mark.parametrize('value,formats', [('2024-02-29', ['YYYY-MM-DD']),
                                        ('2024.02.29', ['YYYY.MM.DD']),
                                        ('2024년 2월 29일', ['YYYY년 M월 D일']),
                                        ('2000년 02월 29일 [S1]', ['YYYY년 M월 D일'])])
def test_declared_dates_and_leap_years(value, formats):
    assert not scalar({'type': 'date', 'date_formats': formats}, value)


@pytest.mark.parametrize('value', ['2023-02-29', '1900-02-29', '2024-13-01', '2024-04-31', '0000-01-01',
                                 '10/03/26', '26-10-03', '2024-2-29', '2024.02.29', '2024-02-29T00:00:00',
                                 '2024-02[S1]-29', '마감 2024-02-29', 20240229, False])
def test_invalid_or_ambiguous_dates_are_not_guessed(value):
    assert scalar({'type': 'date'}, value)


def money_profile(tolerance=0):
    rule = {'type': 'number', 'unit': '원'}
    return profile(*(field(key, rule) for key in ['합계', 'a', 'b']),
                   relations=[{'kind': 'sum', 'total': '합계', 'parts': ['a', 'b'], 'tolerance': tolerance}])


def test_decimal_sum_has_no_float_rounding_and_preserves_input_and_context():
    p = money_profile()
    values = {'합계': '0.3원 [S1]', 'a': '0.1원 [S2]', 'b': '0.2원 [S3]', '본문': '일반 텍스트'}
    original = deepcopy((values, p))
    precision = getcontext().prec
    assert not inspect_form_values(values, p)
    assert (values, p) == original and getcontext().prec == precision
    values['합계'] = '0.3000000000000000000000000001원'
    assert inspect_form_values(values, p)[0]['code'] == 'form_relation_sum'


def test_huge_exact_sum_does_not_silently_use_default_decimal_precision():
    values = {'합계': ('1' + '0' * 80 + '.3원'), 'a': ('1' + '0' * 80 + '.1원'), 'b': '0.2원'}
    assert not inspect_form_values(values, money_profile())
    values['합계'] = '1' + '0' * 80 + '.30000000000000000000000000001원'
    assert inspect_form_values(values, money_profile())[0]['code'] == 'form_relation_sum'


def test_sum_absolute_tolerance_is_in_declared_unit():
    values = {'합계': '0.31원', 'a': '0.1원', 'b': '0.2원'}
    assert not inspect_form_values(values, money_profile('0.01'))
    assert inspect_form_values(values, money_profile('0.009'))


def test_optional_blanks_and_all_blank_relations_are_allowed_but_partial_missing_is_not():
    assert not scalar({'type': 'integer'}, '')
    assert scalar({'type': 'integer'}, ' [S1]', required=True)[0]['code'] == 'form_value_required'
    assert not inspect_form_values({}, money_profile())
    assert not inspect_form_values({'합계': ' [S1]', 'a': ' ', 'b': ''}, money_profile())
    assert inspect_form_values({'합계': '1원', 'a': '1원'}, money_profile())[0]['code'] == 'form_relation_missing'


def test_date_order_compares_calendar_dates_with_different_explicit_formats():
    p = profile(field('시작', {'type': 'date'}), field('끝', {'type': 'date', 'date_formats': ['YYYY년 M월 D일']}),
                relations=[{'kind': 'date_order', 'start': '시작', 'end': '끝'}])
    assert not inspect_form_values({'시작': '2024-02-29', '끝': '2024년 3월 1일'}, p)
    assert inspect_form_values({'시작': '2024-03-02', '끝': '2024년 3월 1일'}, p)[0]['code'] == 'form_relation_date_order'
    assert inspect_form_values({'시작': '2024-03-01'}, p)[0]['code'] == 'form_relation_missing'


@pytest.mark.parametrize('rule', [None, [], {}, {'type': True}, {'type': []}, {'type': 'text'},
    {'type': 'number', 'guess_unit': '원'}, {'type': 'number', 'unit': ''}, {'type': 'number', 'unit': True},
    {'type': 'number', 'unit': '원\n'}, {'type': 'number', 'unit_location': 'label'},
    {'type': 'number', 'unit': '원', 'unit_location': 'unknown'}, {'type': 'number', 'unit': '원', 'unit_location': []},
    {'type': 'number', 'min': True}, {'type': 'number', 'max': float('inf')}, {'type': 'number', 'min': float('nan')},
    {'type': 'number', 'min': 'NaN'}, {'type': 'number', 'max': '1e3'}, {'type': 'number', 'min': '1,000'},
    {'type': 'number', 'min': 10, 'max': 1}, {'type': 'number', 'decimal_places': True},
    {'type': 'number', 'decimal_places': -1}, {'type': 'integer', 'decimal_places': 1},
    {'type': 'integer', 'date_formats': ['YYYY-MM-DD']}, {'type': 'date', 'unit': '일'},
    {'type': 'date', 'min': 20200101}, {'type': 'date', 'date_formats': []},
    {'type': 'date', 'date_formats': ['M/D/YY']}, {'type': 'date', 'date_formats': ['YYYY-MM-DD', 'YYYY-MM-DD']}])
def test_invalid_rule_schema_fails_closed(rule):
    p = profile(field('값', rule))
    with pytest.raises(ValueError):
        validate_rule_profile(p)
    with pytest.raises(ValueError):
        inspect_form_values({'값': '1'}, p)


@pytest.mark.parametrize('relation', [None, [], {'kind': []}, {'kind': 'unknown'},
    {'kind': 'sum', 'total': '합계', 'parts': []}, {'kind': 'sum', 'total': '합계', 'parts': ['a', 'a']},
    {'kind': 'sum', 'total': '합계', 'parts': ['a', '합계']}, {'kind': 'sum', 'total': '합계', 'parts': ['missing']},
    {'kind': 'sum', 'total': '합계', 'parts': ['a'], 'tolerance': -1},
    {'kind': 'sum', 'total': '합계', 'parts': ['a'], 'tolerance': True},
    {'kind': 'less_equal', 'left': 'a', 'right': 'a'}, {'kind': 'less_equal', 'left': 'a', 'right': 'b', 'tolerance': 1},
    {'kind': 'date_order', 'start': 'a', 'end': 'b'}])
def test_malformed_unknown_or_wrong_type_relations_fail_closed(relation):
    p = money_profile()
    p['constraints']['relations'] = [relation]
    with pytest.raises(ValueError):
        validate_rule_profile(p)


def test_relation_requires_declared_types_and_same_literal_units():
    p = money_profile()
    p['fields'][2]['validation'] = {'type': 'number', 'unit': '만원'}
    with pytest.raises(ValueError, match='단위'):
        validate_rule_profile(p)
    p['constraints']['relations'] = [{'kind': 'less_equal', 'left': 'a', 'right': 'b'}]
    with pytest.raises(ValueError, match='단위'):
        validate_rule_profile(p)
    p['fields'][2].pop('validation')
    with pytest.raises(ValueError, match='등록된 validation'):
        validate_rule_profile(p)


def test_shared_keys_share_identical_rules_but_conflicts_and_duplicate_relations_fail():
    p = profile(field('인원', {'type': 'integer', 'unit': '명'}, identifier='x'),
                field('인원', {'type': 'integer', 'unit': '명'}, identifier='y'))
    assert not inspect_form_values({'인원': '3명'}, p)
    p['fields'][1]['validation']['unit'] = '건'
    with pytest.raises(ValueError, match='상충'):
        validate_rule_profile(p)
    p = money_profile()
    p['constraints']['relations'].append({'kind': 'sum', 'total': '합계', 'parts': ['b', 'a']})
    with pytest.raises(ValueError, match='중복'):
        validate_rule_profile(p)


@pytest.mark.parametrize('p', [False, [], '', {'fields': None}, {'fields': ['bad']}, {'fields': [{'value_key': ''}]},
                              {'constraints': []}, {'constraints': {'relations': None}}])
def test_malformed_profile_schema_has_clear_errors(p):
    with pytest.raises(ValueError):
        validate_rule_profile(p)


def test_ordinary_unvalidated_text_and_none_profile_stay_compatible():
    p = {'fields': [{'value_key': '본문', 'kind': 'placeholder', 'other_metadata': 'keep'}], 'custom_metadata': {'keep': 1}}
    assert not inspect_form_values({'본문': '숫자 1e3은 설명 문구임', '제목': '일반 제목', '미지 텍스트': '유지'}, p)
    assert not inspect_form_values({'본문': '일반 문장'}, None)
    validate_rule_profile({})
    with pytest.raises(ValueError):
        inspect_form_values([], p)


def test_id_mapping_rebinds_numeric_and_date_relations_without_mutating_original():
    p = money_profile()
    original = deepcopy(p)
    new = mapped_rule_profile(p, {'합계': '총액', 'a': '재료비', 'b': '인건비'})
    assert p == original
    assert new['constraints']['relations'][0]['total'] == '총액'
    assert new['constraints']['relations'][0]['parts'] == ['재료비', '인건비']
    assert not inspect_form_values({'총액': '0.3원', '재료비': '0.1원', '인건비': '0.2원'}, new)
    assert inspect_form_values({'총액': '1원', '재료비': '0.1원', '인건비': '0.2원'}, new)
    assert json.loads(json.dumps(new, ensure_ascii=False)) == new
    copied = mapped_rule_profile(p)
    copied['fields'][0]['value_key'] = 'different'
    assert p == original


@pytest.mark.parametrize('mapping', [{'합계': '총액', 'a': '재료비'},
                                    {'합계': '총액', 'a': '비용', 'b': '비용'},
                                    {'missing': '값'}, {'합계': ' '}, {'합계': True}, []])
def test_unknown_invalid_omitted_or_collapsed_relation_mappings_fail(mapping):
    with pytest.raises(ValueError):
        mapped_rule_profile(money_profile(), mapping)


def test_shared_relation_key_cannot_split_or_drop_any_original_input():
    p = money_profile()
    p['fields'].append(field('a', {'type': 'number', 'unit': '원'}, identifier='a-copy'))
    with pytest.raises(ValueError, match='나눌'):
        mapped_rule_profile(p, {'합계': '총', 'a': '재료비', 'a-copy': '복제비', 'b': '인건비'})
    with pytest.raises(ValueError, match='제외'):
        mapped_rule_profile(p, {'합계': '총', 'a': '재료비', 'b': '인건비'})
    mapped = mapped_rule_profile(p, {'합계': '총', 'a': '재료비', 'a-copy': '재료비', 'b': '인건비'})
    assert not inspect_form_values({'총': '3원', '재료비': '1원', '인건비': '2원'}, mapped)


def test_shared_nonrelation_key_can_split_but_keeps_same_rule_on_every_destination():
    p = profile(field('인원', {'type': 'integer', 'unit': '명'}, identifier='one'),
                field('인원', {'type': 'integer', 'unit': '명'}, identifier='two'))
    result = mapped_rule_profile(p, {'one': '본사', 'two': '지사'})
    assert not inspect_form_values({'본사': '3명', '지사': '4명'}, result)
    issues = inspect_form_values({'본사': '3명', '지사': '4건'}, result)
    assert issues[0]['field'] == '지사'


@pytest.mark.parametrize('kind', ['less_equal', 'date_order'])
def test_remapping_comparison_relations_keeps_the_actual_values_under_check(kind):
    if kind == 'less_equal':
        rule = {'type': 'integer', 'unit': '명', 'unit_location': 'label'}
        relation = {'kind': kind, 'left': 'a', 'right': 'b'}
        good = {'first': '10', 'second': '11'}
        bad = {'first': '12', 'second': '11'}
    else:
        rule = {'type': 'date'}
        relation = {'kind': kind, 'start': 'a', 'end': 'b'}
        good = {'first': '2024-02-29', 'second': '2024-03-01'}
        bad = {'first': '2024-03-02', 'second': '2024-03-01'}
    p = profile(field('a', rule), field('b', rule), relations=[relation])
    result = mapped_rule_profile(p, {'a': 'first', 'b': 'second'})
    assert not inspect_form_values(good, result)
    assert inspect_form_values(bad, result)[0]['code'] == 'form_relation_' + kind


def test_mapping_does_not_lose_a_shared_rule_when_an_untyped_mirror_is_selected():
    p = profile(field('인원', {'type': 'integer', 'unit': '명'}, identifier='rule'),
                {'id': 'mirror', 'value_key': '인원', 'label': '반복 표시'})
    result = mapped_rule_profile(p, {'mirror': '다시 표시'})
    assert not inspect_form_values({'다시 표시': '3명'}, result)
    assert inspect_form_values({'다시 표시': '3건'}, result)


def test_required_untyped_mirror_still_makes_the_shared_validated_value_required():
    p = profile(field('인원', {'type': 'integer', 'unit': '명'}, identifier='rule'),
                {'id': 'mirror', 'value_key': '인원', 'label': '반복 표시', 'required': True})
    assert inspect_form_values({'인원': ''}, p)[0]['code'] == 'form_value_required'
    assert not inspect_form_values({'인원': '3명'}, p)


def test_remapping_an_optional_mirror_keeps_the_shared_required_value_rule():
    p = profile(field('인원', {'type': 'integer', 'unit': '명'}, required=True, identifier='rule'),
                {'id': 'mirror', 'value_key': '인원', 'label': '반복 표시'})
    with pytest.raises(ValueError, match='필수'):
        mapped_rule_profile(p, {'mirror': '다시 표시'})
    mapped = mapped_rule_profile(p, {'rule': '다시 표시', 'mirror': '다시 표시'})
    assert inspect_form_values({'다시 표시': ''}, mapped)[0]['code'] == 'form_value_required'
    assert not inspect_form_values({'다시 표시': '3명'}, mapped)


def test_merge_into_same_value_key_cannot_drop_conflicting_units():
    p = profile(field('인원', {'type': 'integer', 'unit': '명'}), field('건수', {'type': 'integer', 'unit': '건'}))
    with pytest.raises(ValueError, match='상충'):
        mapped_rule_profile(p, {'인원': '동일 값', '건수': '동일 값'})


def test_bounded_numeric_input_fails_without_float_overflow_or_decimal_rounding():
    assert scalar({'type': 'number'}, '9' * 4097)


def calendar_profile(location=None):
    fields = []
    for key, unit in [('year', '년'), ('month', '월'), ('day', '일')]:
        rule = {'type': 'integer'}
        if location:
            rule.update(unit=unit, unit_location=location)
        fields.append(field(key, rule))
    return profile(*fields, relations=[{'kind': 'calendar_date', 'year': 'year', 'month': 'month', 'day': 'day'}])


@pytest.mark.parametrize('year,month,day', [('2024', '2', '29'), ('2000', '2', '29'), ('1', '1', '1'),
                                          ('9999', '12', '31'), ('2026', '4', '30')])
def test_separate_calendar_date_checks_real_calendar_without_modifying_values(year, month, day):
    values = dict(year=year, month=month, day=day)
    original = deepcopy(values)
    assert not inspect_form_values(values, calendar_profile())
    assert values == original


@pytest.mark.parametrize('year,month,day', [('2026', '2', '30'), ('2025', '2', '29'), ('1900', '2', '29'),
                                          ('2026', '4', '31'), ('2026', '13', '1'), ('2026', '0', '1'),
                                          ('2026', '1', '0'), ('0', '1', '1'), ('-1', '1', '1'),
                                          ('10000', '1', '1'), ('9' * 100, '1', '1')])
def test_invalid_separate_calendar_date_points_to_day(year, month, day):
    issues = inspect_form_values(dict(year=year, month=month, day=day), calendar_profile())
    assert len(issues) == 1
    assert issues[0]['code'] == 'form_relation_calendar_date' and issues[0]['field'] == 'day'


@pytest.mark.parametrize('missing', ['year', 'month', 'day'])
def test_separate_calendar_date_partial_missing_points_to_day(missing):
    values = {'year': '2024', 'month': '2', 'day': '29'}
    del values[missing]
    issues = inspect_form_values(values, calendar_profile())
    assert issues[0]['code'] == 'form_relation_missing' and issues[0]['field'] == 'day'
    assert not inspect_form_values({}, calendar_profile())


@pytest.mark.parametrize('location', ['label', 'value'])
def test_separate_calendar_date_accepts_only_declared_unit_position(location):
    values = {'year': '2024', 'month': '2', 'day': '29'}
    if location == 'value':
        values = {key: values[key] + unit for key, unit in [('year', '년'), ('month', '월'), ('day', '일')]}
    values['day'] += ' [S1]'
    assert not inspect_form_values(values, calendar_profile(location))
    values['day'] = '29월' if location == 'value' else '29일'
    assert inspect_form_values(values, calendar_profile(location))[0]['code'] == 'form_value_integer'


@pytest.mark.parametrize('change', ['number_type', 'wrong_unit', 'missing_type', 'unknown_key', 'same_key',
                                   'missing_key', 'missing_role', 'duplicate_relation'])
def test_malformed_calendar_relation_is_rejected(change):
    p = calendar_profile('label')
    relation = p['constraints']['relations'][0]
    if change == 'number_type':
        p['fields'][0]['validation']['type'] = 'number'
    elif change == 'wrong_unit':
        p['fields'][1]['validation']['unit'] = '년'
    elif change == 'missing_type':
        p['fields'][2].pop('validation')
    elif change == 'unknown_key':
        relation['tolerance'] = 1
    elif change == 'same_key':
        relation['day'] = 'month'
    elif change == 'missing_key':
        relation['day'] = 'unknown'
    elif change == 'missing_role':
        del relation['year']
    else:
        p['constraints']['relations'].append(deepcopy(relation))
    with pytest.raises(ValueError):
        validate_rule_profile(p)


def test_calendar_relation_remaps_all_roles_and_cannot_drop_a_dependency():
    p = calendar_profile('label')
    original = deepcopy(p)
    renamed = mapped_rule_profile(p, {'year': '신청 연도', 'month': '신청 월', 'day': '신청 일'})
    assert renamed['constraints']['relations'] == [{'kind': 'calendar_date', 'year': '신청 연도',
                                                   'month': '신청 월', 'day': '신청 일'}]
    assert not inspect_form_values({'신청 연도': '2024', '신청 월': '2', '신청 일': '29'}, renamed)
    issues = inspect_form_values({'신청 연도': '2026', '신청 월': '2', '신청 일': '30'}, renamed)
    assert issues[0]['field'] == '신청 일'
    with pytest.raises(ValueError):
        mapped_rule_profile(p, {'year': '신청 연도', 'month': '신청 월'})
    assert p == original


@pytest.mark.parametrize('value', ['1 000원', '1\t000원', '1\n000원', '1\r000원', '1000\n원', '1000\r원'])
def test_internal_numeric_whitespace_cannot_hide_digits_or_create_a_sentence(value):
    assert scalar({'type': 'integer', 'unit': '원'}, value)


def test_unit_separator_whitespace_is_formatting_only_and_original_is_preserved():
    values = {'값': '  1,000 \t  원 [S1]  '}
    original = values.copy()
    assert not inspect_form_values(values, profile(field('값', {'type': 'integer', 'unit': '원'})))
    assert values == original


def test_thousand_digit_sum_and_tiny_difference_are_exact_even_under_low_ambient_precision():
    precision = getcontext().prec
    try:
        getcontext().prec = 2
        values = {'합계': '1' + '0' * 1000 + '.3원', 'a': '1' + '0' * 1000 + '.1원', 'b': '0.2원'}
        assert not inspect_form_values(values, money_profile())
        values['합계'] = '1' + '0' * 1000 + '.3' + '0' * 1000 + '1원'
        assert inspect_form_values(values, money_profile())[0]['code'] == 'form_relation_sum'
        assert getcontext().prec == 2
    finally:
        getcontext().prec = precision
