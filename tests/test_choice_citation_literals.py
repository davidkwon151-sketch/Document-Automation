"""Native codes are data; their actual provenance remains mandatory."""
from copy import deepcopy

import pytest

from agent.draft import create_draft
from agent.field_citations import split_field_citations
from agent.grounding import evidence_fingerprint, factual_lines, inspect_grounding
from agent.review import inspect_draft, review_draft


def field(*, multi=False, custom=False):
    return {'id': 'pdf:selection', 'kind': 'pdf_form', 'value_key': '선택', 'label': '선택',
            'control_type': 'combobox' if custom else 'choice',
            'options': ['[S1]', '[S1, S2]', '리포트'],
            'choice_items': [{'value': code, 'label': code} for code in ['[S1]', '[S1, S2]', '리포트']],
            'allow_custom': custom, 'multiselect': multi,
            **({'selection_encoding': 'json_array'} if multi else {}),
            'required': True, 'input_required': True, 'max_chars': 100,
            'narrative_style_required': False}


def profile(**kwargs):
    return {'citation_mode': 'sidecar', 'fields': [field(**kwargs)]}


def draft(value):
    return {'제목': '선택 확인', '요약': '추가 확인 필요', '본문': '추가 확인 필요', '선택': value}


def sources(value='[S1]'):
    return [{'source_id': 'SUreal', 'text': f'선택 {value}', 'filename': '사용자 입력'}]


@pytest.mark.parametrize('value,multi', [('[S1]', False), ('[S1, S2]', False),
                                      ('["[S1]","[S1, S2]"]', True)])
def test_exact_codes_and_json_members_are_preserved_before_citations(value, multi):
    metadata = field(multi=multi)
    original = deepcopy(metadata)
    assert split_field_citations(value, metadata) == (value, [])
    assert split_field_citations(value + ' [SUreal] [Ssecond]', metadata) == (value, ['SUreal', 'Ssecond'])
    assert metadata == original


def test_editable_literal_needs_actual_input_to_disambiguate_citation_syntax():
    metadata = field(custom=True)
    assert split_field_citations('[S42] [SUreal]', metadata, literal='[S42]') == ('[S42]', ['SUreal'])
    assert split_field_citations('user text [S42] [SUreal]', metadata, literal='user text [S42]') == ('user text [S42]', ['SUreal'])
    assert split_field_citations('different [Sghost]', metadata, literal='[S42]') == ('different', ['Sghost'])


@pytest.mark.parametrize('value', ['[S1]', '[S1, S2]'])
def test_literals_do_not_supply_their_own_provenance_even_if_id_exists(value):
    issues = inspect_draft(draft(value), sources() + [{'source_id': 'S1', 'text': 'unrelated'}], template_profile=profile())
    assert any(item['code'] == 'missing_source' and item['field'] == '선택' for item in issues)
    assert not any(item['code'] == 'unknown_source' and item['field'] == '선택' for item in issues)


def test_true_and_unknown_suffix_citations_are_checked_independently_of_code():
    assert inspect_draft(draft('[S1] [SUreal]'), sources(), template_profile=profile()) == []
    issues = inspect_draft(draft('[S1] [Sghost]'), sources(), template_profile=profile())
    assert [item['message'] for item in issues if item['code'] == 'unknown_source'] == ['알 수 없는 출처 ID: Sghost']


@pytest.mark.parametrize('value,expected', [
    ('○ 매출 120원을 달성함 [S1]', None),
    ('○ 매출 120원을 달성함', 'missing_source'),
    ('○ 매출 120원을 달성함 [Sghost]', 'unknown_source'),
    ('○ 매출 130원을 달성함 [S1]', 'number_mismatch'),
])
def test_ordinary_narrative_citations_and_numeric_checks_are_unchanged(value, expected):
    current = draft('')
    current['본문'] = value
    evidence = [{'source_id': 'S1', 'text': '매출 120원을 달성함'}]
    issues = inspect_draft(current, evidence)
    assert (any(item['code'] == expected for item in issues) if expected else not issues)


class DraftClient:
    def generate_json(self, name, payload):
        return draft('[S1, S2]')


def test_mock_draft_overrides_ai_choice_with_user_value_and_preserves_preference_literals():
    metadata = profile()
    brief = {'보고서 유형': '결과보고서', '부족한 정보': [],
             '양식 항목': {'선택': '리포트'}, '양식 항목 출처': {'선택': 'SUreal'}}
    actual = create_draft(brief, sources('리포트'), DraftClient(), metadata,
                          {'preferred_terms': {'리포트': '보고서'}})
    assert actual['선택'] == '리포트 [SUreal]'


def test_literal_length_cannot_disappear_from_sidecar_limit():
    metadata = profile()
    metadata['fields'][0]['max_chars'] = 3
    brief = {'보고서 유형': '결과보고서', '부족한 정보': [],
             '양식 항목': {'선택': '[S1]'}, '양식 항목 출처': {'선택': 'SUreal'}}
    with pytest.raises(ValueError, match='분량'):
        create_draft(brief, sources(), DraftClient(), metadata)


def test_unknown_real_citation_is_still_rejected_during_draft_creation():
    class UnknownClient:
        def generate_json(self, name, payload):
            return draft('[S1] [Sghost]')
    metadata = profile()
    metadata['fields'][0]['input_required'] = False
    with pytest.raises(ValueError, match='존재하지 않는 출처'):
        create_draft({'보고서 유형': '결과보고서', '부족한 정보': []}, sources(), UnknownClient(), metadata)


def test_optional_native_consent_is_not_chosen_by_model_without_user_input():
    metadata = profile()
    metadata['fields'][0]['required'] = False
    assert create_draft({'보고서 유형': '결과보고서', '부족한 정보': []}, sources(), DraftClient(), metadata)['선택'] == ''


def test_closed_choice_rule_checks_preserve_literal_and_cited_codes():
    from templates.value_rules import inspect_form_values
    assert inspect_form_values({'선택': '[S1] [SUreal]'}, profile()) == []
    assert inspect_form_values({'선택': '[S1, S2] [SUreal]'}, profile()) == []
    assert inspect_form_values({'선택': '["[S1]","[S1, S2]"] [SUreal]'}, profile(multi=True)) == []
    assert inspect_form_values({'선택': '[Sunknown] [SUreal]'}, profile())


def test_editable_rule_uses_actual_literal_without_relaxing_other_fields():
    from templates.value_rules import inspect_form_values
    values = {'선택': '[Scustom] [SUreal]'}
    assert inspect_form_values(values, profile(custom=True), literal_values={'선택': '[Scustom]'}) == []
    bad = draft('[Sunknown] [SUreal]')
    assert any(item['code'] == 'unknown_source' for item in inspect_draft(bad, sources(), template_profile=profile(custom=True)))


def test_review_never_corrects_or_ai_replaces_actual_native_decisions():
    current = draft('[S1] [SUreal]')
    current['본문'] = '매출 130원 [Sbusiness]'
    evidence = sources() + [{'source_id': 'Sbusiness', 'text': '매출 120원'}]
    result = review_draft(current, evidence, template_profile=profile(), literal_values={'선택': '[S1]'})
    assert result['draft']['선택'] == current['선택']
    assert '120원' in result['draft']['본문']


def test_model_review_cannot_replace_user_choice_while_repairing_other_field():
    class CorrectionClient:
        def generate_json(self, name, payload):
            assert name == 'review'
            return {'draft': {**payload['draft'], '선택': '[S1, S2] [SUreal]',
                              '본문': '○ 사용자 선택을 확인함 [SUreal]'}}
    current = draft('[S1] [SUreal]')
    current['본문'] = '○ 사용자 선택을 확인함'
    result = review_draft(current, sources(), CorrectionClient(), template_profile=profile(),
                          literal_values={'선택': '[S1]'})
    assert result['corrected'] and not result['blocking']
    assert result['draft']['선택'] == current['선택']


def test_grounding_requires_actual_suffix_source_not_option_that_looks_like_id():
    class MeaningClient:
        def __init__(self, identifier):
            self.identifier = identifier
        def generate_json(self, name, payload):
            assert len(payload['claims']) == 1
            assert payload['claims'][0]['text'] == '[S1] [SUreal]'
            return {'claims': [{'field': '선택', 'line': 1, 'status': 'supported',
                                'evidence': [{'source_id': self.identifier, 'quote': '선택 [S1]'}]}]}
    current = draft('[S1] [SUreal]')
    evidence = sources() + [{'source_id': 'S1', 'text': '선택 [S1]'}]
    assert factual_lines(current, template_profile=profile())[0]['field'] == '선택'
    assert not inspect_grounding(current, evidence, MeaningClient('SUreal'), template_profile=profile())['blocking']
    assert inspect_grounding(current, evidence, MeaningClient('S1'), template_profile=profile())['blocking']


def test_choice_interpretation_change_invalidates_semantic_fingerprint():
    current, evidence, metadata = draft('[S1] [SUreal]'), sources(), profile()
    before = evidence_fingerprint(current, evidence, template_profile=metadata, literal_values={'선택': '[S1]'})
    other = deepcopy(metadata)
    other['fields'][0]['options'] = ['Other']
    assert evidence_fingerprint(current, evidence, template_profile=other, literal_values={'선택': '[S1]'}) != before
