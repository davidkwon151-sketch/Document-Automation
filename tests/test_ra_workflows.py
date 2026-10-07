"""Deterministic RA copy preparation; synthetic fixtures unless explicitly public."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from agent.ra_workflows import prepare_ra_workflow


def profile():
    return {'domain': 'pharmaceutical_ra', 'ra_workflow': 'product_approval',
            'ra_product_name': 'SyntheticDrugA', 'document_kind': 'application',
            'fields': [{'id': 'drug', 'value_key': '제품명', 'label': '제품명',
                        'required': True, 'input_required': False, 'evidence_role': 'product_name'},
                       {'id': 'person', 'value_key': '신청인', 'label': '신청인',
                        'required': True, 'input_required': True}]}


def source():
    return {'source_id': 'S1', 'filename': 'synthetic.pdf', 'page': 1,
            'document_sha256': 'a' * 64, 'text': '제품명: SyntheticDrugA',
            'product_name': 'SyntheticDrugA'}


def prepare(p=None, s=None, b=None, d=None):
    return prepare_ra_workflow(p or profile(), [source()] if s is None else s,
        {'제품명': {'source_id': 'S1', 'quote': 'SyntheticDrugA'}} if b is None else b,
        {'신청인': '테스트 담당자'} if d is None else d)


def test_exact_copy_preparation_ready_but_not_submission_approval():
    result = prepare()
    assert result['ready_for_output_check'], result['review']
    assert result['status'] == 'ready_for_output_check'
    assert not result['submission_ready'] and result['actual_model_requests'] == 0
    assert result['draft']['제품명'] == 'SyntheticDrugA [S1]'
    assert result['evidence']['제품명']['source'] == source()
    assert result['evidence']['제품명']['start'] == len('제품명: ')
    assert result['sources'][-1]['filename'] == '사용자 입력'
    assert result['locked_fields']['신청인']['value'] == '테스트 담당자'


def test_no_mutation_and_qa_values_not_promoted():
    p, s = profile(), [source()]
    p['qa_values'] = {'제품명': 'WrongDrug'}
    p['fields'][0]['default_value'] = 'WrongDrug'
    before = deepcopy((p, s))
    result = prepare(p, s)
    assert (p, s) == before
    assert 'qa_values' not in result['template_profile']
    assert 'default_value' not in result['template_profile']['fields'][0]


def test_missing_information_is_all_reported_with_at_most_two_questions():
    p = profile()
    p['fields'].extend({'id': str(i), 'value_key': f'추가{i}', 'label': f'추가{i}',
                        'input_required': True} for i in range(4))
    result = prepare(p, b={}, d={})
    assert len(result['missing_fields']) == 6 and len(result['questions']) == 2
    assert result['status'] == 'needs_information' and not result['ready_for_output_check']


@pytest.mark.parametrize('change', ['unknown', 'changed', 'missing_sha', 'user', 'ocr', 'position', 'context'])
def test_source_integrity_and_origin_blocked(change):
    s = source()
    b = {'제품명': {'source_id': 'S1', 'quote': 'SyntheticDrugA'}}
    if change == 'unknown': b['제품명']['source_id'] = 'S99'
    if change == 'changed': b['제품명']['quote'] = 'SyntheticDrugB'
    if change == 'missing_sha': s.pop('document_sha256')
    if change == 'user': s['filename'] = '사용자 입력'
    if change == 'ocr': s['requires_verification'] = True
    if change == 'position': s.pop('page')
    if change == 'context': s.update(context_text='wrong', context_start=0, context_end=5)
    with pytest.raises(ValueError): prepare(s=[s], b=b)


@pytest.mark.parametrize('bindings,direct', [
    ({'신청인': {'source_id': 'S1', 'quote': 'SyntheticDrugA'}}, {}),
    ({}, {'제품명': 'SyntheticDrugA'}), ({}, {'신청인': 1}),
    ({'없는칸': {'source_id': 'S1', 'quote': 'SyntheticDrugA'}}, {}),
    ({'제품명': {'source_id': 'S1', 'quote': 'SyntheticDrugA'}}, {'제품명': 'SyntheticDrugA'})])
def test_mapping_and_direct_input_permissions(bindings, direct):
    with pytest.raises(ValueError): prepare(b=bindings, d=direct)


def test_duplicate_source_ids_and_field_keys_rejected():
    with pytest.raises(ValueError): prepare(s=[source(), source()])
    p = profile()
    p['fields'].append(deepcopy(p['fields'][0]))
    with pytest.raises(ValueError): prepare(p)


def test_ambiguous_quote_requires_exact_start():
    s = source()
    s['text'] += '\nSyntheticDrugA'
    with pytest.raises(ValueError): prepare(s=[s])
    b = {'제품명': {'source_id': 'S1', 'quote': 'SyntheticDrugA', 'start': len('제품명: ')}}
    assert prepare(s=[s], b=b)['ready_for_output_check']
    b['제품명']['start'] = True
    with pytest.raises(ValueError): prepare(s=[s], b=b)


def test_wrong_product_is_not_approved_even_when_quote_is_exact():
    p = profile()
    p['ra_product_name'] = 'OtherDrug'
    result = prepare(p)
    assert result['review']['blocking'] and result['status'] == 'needs_revision'


def test_closed_direct_choice_not_auto_selected_or_relaxed():
    p = profile()
    p['fields'][1].update(control_type='choice', options=['동의', '미동의'],
                           validation={'type': 'choice', 'options': ['동의', '미동의']})
    assert prepare(p, d={})['status'] == 'needs_information'
    assert prepare(p, d={'신청인': ''})['draft']['신청인'] == ''
    assert prepare(p, d={'신청인': '임의값'})['review']['blocking']
    assert prepare(p, d={'신청인': '미동의'})['ready_for_output_check']


def test_native_selection_code_looking_like_citation_stays_data():
    p = profile()
    p['fields'][1].update(control_type='choice', options=['[S999]', 'N'],
                           validation={'type': 'choice', 'options': ['[S999]', 'N']})
    result = prepare(p, d={'신청인': '[S999]'})
    assert result['ready_for_output_check'], result['review']
    assert result['locked_fields']['신청인']['value'] == '[S999]'


def test_source_bound_native_choice_code_is_data_and_retains_actual_source():
    p = profile()
    p['fields'][0].pop('evidence_role')
    p['fields'][0].update(control_type='choice', options=['[S999]', 'N'],
                           validation={'type': 'choice', 'options': ['[S999]', 'N']})
    s = source()
    s['text'] = '코드 [S999]'
    result = prepare(p, [s], {'제품명': {'source_id': 'S1', 'quote': '[S999]'}})
    assert result['ready_for_output_check'], result['review']
    assert result['draft']['제품명'] == '[S999] [S1]'
    assert result['evidence']['제품명']['source_id'] == 'S1'


def test_numeric_rule_and_declared_length_block_without_fixing_value():
    p = profile()
    p['fields'][1]['validation'] = {'type': 'integer', 'min': 0, 'max': 5}
    result = prepare(p, d={'신청인': '6'})
    assert result['review']['blocking'] and result['locked_fields']['신청인']['value'] == '6'
    p['fields'][0]['max_chars'] = 2
    assert any(i['code'] == 'ra_field_length' for i in prepare(p)['review']['issues'])


def test_multiline_quote_preserves_newlines_and_cites_every_used_line():
    p = profile()
    p['fields'][0].update(value_key='자료 요약', label='자료 요약')
    p['fields'][0].pop('evidence_role')
    s = source()
    s['text'] = '용량 5 mg\n\n보관 20 °C'
    result = prepare(p, [s], {'자료 요약': {'source_id': 'S1', 'quote': s['text']}})
    assert result['draft']['자료 요약'] == '용량 5 mg [S1]\n\n보관 20 °C [S1]'
    assert result['evidence']['자료 요약']['quote'] == s['text']


def test_fingerprint_binds_original_profile_product_and_exact_origin():
    a = prepare()['fingerprint']
    p = profile()
    p['fields'][0]['max_chars'] = 99
    assert prepare(p)['fingerprint'] != a
    s = source()
    s['document_sha256'] = 'b' * 64
    assert prepare(s=[s])['fingerprint'] != a


def test_public_full_protocol_parser_source_and_registered_field_preparation():
    """Actual public original parse; deterministic exact copy, no model request."""
    import json
    from parsers.extract import parse_pdf
    from agent.retrieve import chunk_documents
    root = Path(__file__).resolve().parents[1]
    path = root / 'tests/fixtures/ra_protocol_public/full-original-protocol.pdf'
    document = parse_pdf(path)
    document['document_sha256'] = sha256(path.read_bytes()).hexdigest()
    chunks = chunk_documents([document])
    quote = 'To evaluate the immunogenicity of REGN3918'
    chunk = next(item for item in chunks if quote in item['text'] and item['page'] == 17)
    raw = json.loads((root / 'templates/profiles/ra_law_form_23_pdf_v2_second_page.json').read_text(encoding='utf-8'))
    field = next(item for item in raw['fields'] if item['label'] == '임상시험 목적')
    p = {'ra_workflow': 'clinical_trial', 'ra_product_name': 'REGN3918', 'fields': [field]}
    result = prepare_ra_workflow(p, [chunk], {field['value_key']: {'source_id': chunk['source_id'], 'quote': quote}})
    assert result['ready_for_output_check'], result['review']
    assert result['actual_model_requests'] == 0
    assert result['evidence'][field['value_key']]['source']['page'] == 17
