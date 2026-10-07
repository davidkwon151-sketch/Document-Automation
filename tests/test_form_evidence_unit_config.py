"""Confirmed numeric source units survive ordinary mapping/rule editing."""
from copy import deepcopy

import pytest

from app.form_config import (configure_profile, configure_value_rules, mapping_rows,
                             validation_rows, value_rule_help)


def count_profile():
    return {'configured': True, 'fields': [{'id': 'count', 'kind': 'pdf_overlay', 'label': '누적 대상자 수',
            'value_key': '인원', 'required': False, 'input_required': False,
            'narrative_style_required': False,
            'validation': {'type': 'integer', 'min': 0, 'evidence_unit': '명'}}]}


def test_source_unit_survives_rule_edit_and_value_key_remapping():
    profile = count_profile()
    before = deepcopy(profile)
    rows = validation_rows(profile)
    rows[0]['최댓값'] = '1000'
    updated = configure_value_rules(profile, rows)
    assert updated['fields'][0]['validation']['evidence_unit'] == '명'
    mapped = mapping_rows(updated)
    mapped[0]['채울 값'] = '누적 인원'
    remapped, _ = configure_profile(updated, mapped)
    assert remapped['fields'][0]['validation']['evidence_unit'] == '명'
    assert profile == before
    help_text = value_rule_help(remapped['fields'][0])
    assert '기입은 숫자만' in help_text and '원자료 대조 단위: 명' in help_text
    assert '양식에 인쇄' not in help_text


@pytest.mark.parametrize('kind', ['일반 문자', '날짜', '목록 선택'])
def test_registered_evidence_unit_cannot_be_silently_removed(kind):
    profile = count_profile()
    rows = validation_rows(profile)
    rows[0]['값 형식'] = kind
    with pytest.raises(ValueError, match='근거 단위'):
        configure_value_rules(profile, rows)


def test_new_display_unit_must_not_conflict_with_registered_source_unit():
    profile = count_profile()
    rows = validation_rows(profile)
    rows[0]['단위'] = 'mL'
    with pytest.raises(ValueError, match='evidence_unit'):
        configure_value_rules(profile, rows)
