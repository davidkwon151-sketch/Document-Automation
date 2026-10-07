"""Source contract schema, position binding, and real UI configuration contracts."""

from copy import deepcopy

import pytest

from templates import value_rules
from tests.test_ra_source_scope_cache import profile


@pytest.mark.parametrize('bad', [None, '', 'DSUR 원자료', [], [None], [1], [{}],
    [''], [' '], [' DSUR 원자료'], ['DSUR 원자료 '], ['DSUR 원자료', 'DSUR 원자료'],
    ['DSUR\n원자료'], ['DSUR\t원자료']])
def test_new_document_labels_require_nonempty_unique_exact_strings(bad):
    proposed = profile()
    proposed['fields'][0]['evidence_document_labels'] = bad
    with pytest.raises(ValueError):
        value_rules.validate_rule_profile(proposed)


@pytest.mark.parametrize('attribute', ['evidence_scope', 'label'])
@pytest.mark.parametrize('bad', [None, '', ' ', 5, [], {}])
def test_new_document_labels_require_scope_and_original_label(attribute, bad):
    proposed = profile()
    proposed['fields'][0][attribute] = bad
    with pytest.raises(ValueError):
        value_rules.validate_rule_profile(proposed)


@pytest.mark.parametrize('attribute', ['evidence_scope', 'label'])
def test_new_document_labels_cannot_lack_scope_or_label(attribute):
    proposed = profile()
    proposed['fields'][0].pop(attribute)
    with pytest.raises(ValueError):
        value_rules.validate_rule_profile(proposed)


@pytest.mark.parametrize('difference', ['all', 'scope', 'label', 'document-labels', 'unscoped'])
@pytest.mark.parametrize('reverse', [False, True])
def test_mapping_distinct_registered_source_items_to_one_value_is_rejected(difference, reverse):
    proposed = profile()
    first, second = proposed['fields']
    if difference != 'all':
        for key in ('evidence_scope', 'evidence_document_labels', 'label'):
            second[key] = deepcopy(first[key])
        if difference == 'scope':
            second['evidence_scope'] += ' 다른 근거임'
        elif difference == 'label':
            second['label'] = '서론'
        elif difference == 'document-labels':
            second['evidence_document_labels'] = ['DSUR 원자료']
        else:
            second.pop('evidence_document_labels')
            second.pop('evidence_scope')
    if reverse:
        proposed['fields'].reverse()
    mapping = {field['id']: '동일 항목' for field in proposed['fields']}
    before = deepcopy(proposed)
    with pytest.raises(ValueError, match='원문 범위'):
        value_rules.mapped_rule_profile(proposed, mapping)
    assert proposed == before


def test_same_declared_source_contract_can_repeat_without_changing_position_ids():
    proposed = profile()
    first, second = proposed['fields']
    for key in ('evidence_scope', 'evidence_document_labels', 'label'):
        second[key] = deepcopy(first[key])
    result = value_rules.mapped_rule_profile(proposed, {field['id']: '임상 목적' for field in proposed['fields']})
    assert [field['id'] for field in result['fields']] == ['input-purpose', 'input-introduction']
    assert len(result['fields']) == 2


def test_existing_prose_scope_without_new_labels_preserves_existing_behavior():
    proposed = profile()
    for field in proposed['fields']:
        field.pop('evidence_document_labels')
    result = value_rules.mapped_rule_profile(proposed, {field['id']: '기존 공통 내용' for field in proposed['fields']})
    assert [field['id'] for field in result['fields']] == ['input-purpose', 'input-introduction']
    assert all(field['value_key'] == '기존 공통 내용' for field in result['fields'])
    assert [field['evidence_scope'] for field in result['fields']] == [field['evidence_scope'] for field in proposed['fields']]


def test_normal_confirmed_renaming_preserves_declared_original_metadata():
    proposed = profile()
    mapping = {'input-purpose': '목적 수정', 'input-introduction': '서론 수정'}
    result = value_rules.mapped_rule_profile(proposed, mapping)
    for original, field in zip(proposed['fields'], result['fields']):
        for key in ('id', 'label', 'evidence_scope', 'evidence_document_labels'):
            assert field[key] == original[key]
    assert [field['value_key'] for field in result['fields']] == ['목적 수정', '서론 수정']
    assert [field['value_key'] for field in proposed['fields']] == ['임상시험 목적', '서론']


def test_registered_label_linebreaks_are_not_normalized():
    proposed = profile()
    proposed['fields'][0]['label'] = '임상시험\n목적'
    value_rules.validate_rule_profile(proposed)
    assert proposed['fields'][0]['label'] == '임상시험\n목적'


@pytest.mark.parametrize('merge', [False, True])
def test_user_configuration_path_uses_shared_source_contract(merge):
    from app import form_config
    proposed = profile()
    rows = form_config.mapping_rows(proposed)
    for index, row in enumerate(rows):
        row['채울 값'] = '공통 항목' if merge else f'확인 항목{index}'
    if merge:
        with pytest.raises(ValueError, match='원문 범위'):
            form_config.configure_profile(proposed, rows)
    else:
        result, mapping = form_config.configure_profile(proposed, rows)
        assert [field['id'] for field in result['fields']] == [field['id'] for field in proposed['fields']]
        assert list(mapping.values()) == ['확인 항목0', '확인 항목1']
        for original, field in zip(proposed['fields'], result['fields']):
            for key in ('label', 'evidence_scope', 'evidence_document_labels'):
                assert field[key] == original[key]


def test_combined_cache_guard_rejects_same_key_scoped_item_merge():
    from agent import template_learning
    authority = profile()
    proposed = deepcopy(authority)
    for field in proposed['fields']:
        field['value_key'] = '공통 항목'
    assert not template_learning.reusable_profile(proposed, authority)
