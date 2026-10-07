"""Exact bulk proposals: real public Hanmi titles and synthetic labelled data."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest

from agent.ra_autofill import propose_ra_bindings
from agent.ra_workflows import prepare_ra_workflow
from agent.retrieve import load_documents, chunk_documents


def profile():
    return {'ra_workflow':'product_approval', 'ra_product_name':'SyntheticDrugA',
            'fields':[{'id':'drug','value_key':'제품명','label':'제품명','evidence_role':'product_name'},
                      {'id':'appearance','value_key':'성상','label':'성상'},
                      {'id':'person','value_key':'신청인','label':'신청인','input_required':True}]}


def source(text, identifier='S1', **extra):
    return {'source_id':identifier,'filename':'synthetic.txt','document_sha256':'a'*64,
            'location':'문단 1','text':text, **extra}


def row(result, key):
    return next(item for item in result['fields'] if item['value_key']==key)


def test_bulk_labelled_exact_values_are_proposals_not_confirmed_output():
    p = profile()
    s = [source('제품명: SyntheticDrugA\n성상: 흰색 분말')]
    before = deepcopy((p,s))
    result = propose_ra_bindings(p,s)
    assert result['requires_confirmation'] and not result['confirmed']
    assert not result['submission_ready'] and result['actual_model_requests']==0
    assert row(result,'제품명')['status']=='proposed'
    assert row(result,'성상')['status']=='proposed'
    assert row(result,'신청인')['status']=='direct_input'
    for binding in result['source_bindings'].values():
        assert s[0]['text'][binding['start']:binding['start']+len(binding['quote'])]==binding['quote']
    assert (p,s)==before
    prepared=prepare_ra_workflow(p,s,result['source_bindings'],{})
    assert prepared['status']=='needs_information'  # Applicant is not guessed.


@pytest.mark.parametrize('text',['제품명 | SyntheticDrugA\n성상 | 흰색 분말',
                                '"제품명": "SyntheticDrugA",\n"성상": "흰색 분말"'])
def test_exact_table_and_simple_json_lines(text):
    result=propose_ra_bindings(profile(),[source(text)])
    assert row(result,'성상')['status']=='proposed'
    assert result['source_bindings']['성상']['quote']=='흰색 분말'


def test_alias_or_fuzzy_label_is_not_inferred():
    result=propose_ra_bindings(profile(),[source('제품명: SyntheticDrugA\n외관: 흰색 분말')])
    assert row(result,'성상')['status']=='missing'


@pytest.mark.parametrize('printed', ['효능·효과', '효능/효과', '효능및효과', '효 능 및 효 과'])
def test_explicit_standard_label_variants_preserve_original_quote_offsets(printed):
    from agent.ra_autofill import _candidates
    s = source('제품명: SyntheticDrugA\n' + printed + ': 원문 선정 문구\n저장방법: 실온')
    candidates = _candidates({'label': '효능·효과'}, s, 'SyntheticDrugA')
    assert len(candidates) == 1 and candidates[0]['quote'] == '원문 선정 문구'
    item = candidates[0]
    assert s['text'][item['start']:item['start'] + len(item['quote'])] == item['quote']
    assert not _candidates({'label': '적응증'}, s, 'SyntheticDrugA')


def test_horizontal_spacing_label_does_not_change_fact_or_infer_other_role():
    p = profile()
    p['fields'].append({'id': 'storage', 'value_key': '저장방법', 'label': '저장방법'})
    s = source('제품명: SyntheticDrugA\n저 장 방 법: 실온 보관')
    result = propose_ra_bindings(p, [s])
    assert result['source_bindings']['저장방법']['quote'] == '실온 보관'
    assert row(result, '성상')['status'] == 'missing'


def test_multiline_whole_value_keeps_crlf_conditions_and_stops_before_next_field():
    from agent.ra_autofill import _candidates
    quote = '처음에는 원문 기준을 적용함.\r\n  다만 추가 확인이 필요한 경우에는 담당자가 확인함.'
    s = source('제품명: SyntheticDrugA\r\n용법 및 용량: ' + quote + '\r\n저장방법: 실온')
    found = _candidates({'label': '용법·용량'}, s, 'SyntheticDrugA')
    assert len(found) == 1 and found[0]['quote'] == quote
    assert s['text'][found[0]['start']:found[0]['start'] + len(quote)] == quote


@pytest.mark.parametrize('boundary', ['\n\nOtherDrug 새 문단', '\n제품명: OtherDrug\n성상: 다른 제품',
                                     '\n포장단위: 30정'])
def test_multiline_value_ends_at_explicit_field_product_or_blank_block(boundary):
    p = profile()
    p['fields'].append({'id': 'storage', 'value_key': '저장방법', 'label': '저장방법'})
    quote = '실온 보관\n빛을 피하여 원포장에 보관'
    s = source('제품명: SyntheticDrugA\n저장방법: ' + quote + boundary)
    result = propose_ra_bindings(p, [s])
    assert result['source_bindings']['저장방법']['quote'] == quote


def test_undeclared_colon_condition_is_not_cropped_into_success():
    from agent.ra_autofill import _candidates
    s = source('제품명: SyntheticDrugA\n용법·용량: 첫 번째 조건\n의사 판단: 반드시 함께 확인해야 하는 조건')
    assert _candidates({'label': '용법·용량'}, s, 'SyntheticDrugA') == []


@pytest.mark.parametrize('label,value,next_field', [('성상', '흰색 분말', '포장: 병 아님'),
                                                 ('포장단위', '30정', '용량: 30')])
def test_distinct_explicit_packaging_or_dose_heading_is_a_scalar_boundary(label, value, next_field):
    from agent.ra_autofill import _candidates
    text = f'제품명: SyntheticDrugA\n{label}: {value}'
    s = source(text, context_text=text + '\n' + next_field, context_start=0, context_end=len(text))
    found = _candidates({'label': label}, s, 'SyntheticDrugA')
    assert len(found) == 1 and found[0]['quote'] == value


@pytest.mark.parametrize('label', ['용법', '용법·용량', '용법및용량'])
def test_dose_subheading_cannot_crop_the_rest_of_a_regimen(label):
    from agent.ra_autofill import _candidates
    text = f'제품명: SyntheticDrugA\n{label}: 원문의 초기 조건'
    continuation = '\n용량: 의사 판단에 따른 원문의 조건을 함께 확인함'
    whole = source(text + continuation)
    fragment = source(text, context_text=text + continuation, context_start=0, context_end=len(text))
    assert _candidates({'label': label}, whole, 'SyntheticDrugA') == []
    assert _candidates({'label': label}, fragment, 'SyntheticDrugA') == []


@pytest.mark.parametrize('continuation', [' 반드시 전체 문장을 확인함', '\n다만 원문 조건을 먼저 확인함'])
def test_fragment_of_longer_original_value_is_not_proposed(continuation):
    from agent.ra_autofill import _candidates
    text = '제품명: SyntheticDrugA\n저장방법: 실온 보관'
    s = source(text, context_text=text + continuation, context_start=0, context_end=len(text))
    assert _candidates({'label': '저장방법'}, s, 'SyntheticDrugA') == []


def test_chunk_edge_does_not_turn_mid_sentence_label_into_a_field():
    from agent.ra_autofill import _candidates
    text = '저장방법: 실온 보관'
    prefix = '제품명: SyntheticDrugA\n작성 안내에서 예로 든 '
    s = source(text, context_text=prefix + text, context_start=len(prefix),
               context_end=len(prefix + text))
    assert _candidates({'label': '저장방법'}, s, 'SyntheticDrugA') == []


def test_actual_parser_continuation_is_deferred_without_partial_copy(tmp_path):
    path = tmp_path / 'multiline-synthetic.txt'
    path.write_text('제품명: SyntheticDrugA\n저장방법: 실온 보관\n빛을 피하여 원포장에 보관', encoding='utf-8')
    p = profile()
    p['fields'].append({'id': 'storage', 'value_key': '저장방법', 'label': '저장방법'})
    sources = chunk_documents(load_documents([path], allow_ocr=False))
    result = propose_ra_bindings(p, sources)
    assert '저장방법' not in result['source_bindings']
    # Parser fragments retain the full original context but are not whole values.
    assert row(result, '저장방법')['status'] == 'missing'


def test_multiline_candidate_still_obeys_product_and_original_length_authority():
    p = profile()
    p['fields'].append({'id': 'storage', 'value_key': '저장방법', 'label': '저장방법', 'max_chars': 5})
    s = source('제품명: SyntheticDrugA\n저장방법: 실온 보관\n빛을 피하여 보관')
    assert row(propose_ra_bindings(p, [s]), '저장방법')['status'] == 'blocked'
    wrong = deepcopy(s)
    wrong['text'] = wrong['text'].replace('SyntheticDrugA', 'OtherDrug')
    assert not propose_ra_bindings(p, [wrong])['source_bindings']


def test_multiple_valid_locations_equal_or_different_are_ambiguous():
    for second in ['제품명: SyntheticDrugA\n성상: 흰색 분말','제품명: SyntheticDrugA\n성상: 노란 분말']:
        result=propose_ra_bindings(profile(),[source('제품명: SyntheticDrugA\n성상: 흰색 분말'),source(second,'S2')])
        assert row(result,'성상')['status']=='ambiguous'
        assert '성상' not in result['source_bindings']


@pytest.mark.parametrize('change',['wrong_product','sha','ocr','user','context'])
def test_false_origins_wrong_products_and_unconfirmed_ocr_block(change):
    s=source('제품명: SyntheticDrugA\n성상: 흰색 분말')
    if change=='wrong_product':s['text']=s['text'].replace('SyntheticDrugA','OtherDrug')
    if change=='sha':s.pop('document_sha256')
    if change=='ocr':s['requires_verification']=True
    if change=='user':s['filename']='사용자 입력'
    if change=='context':s.update(context_text='wrong',context_start=0,context_end=5)
    result=propose_ra_bindings(profile(),[s])
    assert not result['source_bindings']
    assert row(result,'성상')['status']=='blocked'


def test_missing_explicit_product_blocks_even_with_product_metadata():
    s=source('성상: 흰색 분말',product_name='SyntheticDrugA')
    result=propose_ra_bindings(profile(),[s])
    assert row(result,'성상')['status']=='blocked'
    p=profile();p.pop('ra_product_name')
    assert row(propose_ra_bindings(p,[s]),'성상')['status']=='blocked'


def test_company_roles_and_personal_values_never_inferred():
    p=profile()
    p['fields'].extend({'id':key,'value_key':key,'label':key} for key in ['제조원','서명','동의 여부'])
    result=propose_ra_bindings(p,[source('제품명: SyntheticDrugA\n제조원: Example Ltd.\n서명: Person\n동의 여부: 동의')])
    assert row(result,'제조원')['status']=='blocked'
    assert row(result,'서명')['status']=='direct_input'
    assert row(result,'동의 여부')['status']=='direct_input'
    assert not {'제조원','서명','동의 여부'} & result['source_bindings'].keys()


def test_clinical_statement_is_not_product_title():
    p=profile();p['fields']=[p['fields'][0]]
    result=propose_ra_bindings(p,[source('SyntheticDrugA 30mg is safe and effective')])
    assert not result['source_bindings']


def test_too_long_labelled_value_and_unknown_native_choice_remain_blocked():
    p=profile();p['fields'][1]['max_chars']=2
    assert row(propose_ra_bindings(p,[source('제품명: SyntheticDrugA\n성상: 흰색 분말')]),'성상')['status']=='blocked'
    p=profile();p['fields'][1].update(control_type='choice',options=['허용'],
        validation={'type':'choice','options':['허용']})
    assert row(propose_ra_bindings(p,[source('제품명: SyntheticDrugA\n성상: 다른값')]),'성상')['status']=='blocked'


def test_complete_constraints_not_bypassed_by_candidate_proposal():
    p=profile();p['fields']=p['fields'][:1]+[
        {'id':'a','value_key':'A','label':'A','validation':{'type':'integer'}},
        {'id':'b','value_key':'B','label':'B','validation':{'type':'integer'}}]
    p['constraints']={'relations':[{'kind':'less_equal','left':'A','right':'B'}]}
    s=[source('제품명: SyntheticDrugA\nA: 5\nB: 1')]
    proposals=propose_ra_bindings(p,s)
    result=prepare_ra_workflow(p,s,proposals['source_bindings'],{})
    assert result['review']['blocking'] and not result['ready_for_output_check']


def test_actual_synthetic_csv_file_bulk_preserves_sha_and_row_location(tmp_path):
    """Synthetic values in an actual parsed CSV, not actual drug facts."""
    from hashlib import sha256
    path=tmp_path/'synthetic-ra-data.csv'
    path.write_text('제품명,항목,값\nSyntheticDrugA,제품명,SyntheticDrugA\n'
                    'SyntheticDrugA,성상,흰색 분말\nSyntheticDrugA,보관 방법,원문 보관 방법\n',encoding='utf-8-sig')
    p=profile()
    p['fields'].append({'id':'storage','value_key':'보관 방법','label':'보관 방법'})
    chunks=chunk_documents(load_documents([path],allow_ocr=False))
    result=propose_ra_bindings(p,chunks)
    assert set(result['source_bindings'])=={'제품명','성상','보관 방법'},result
    for binding in result['source_bindings'].values():
        actual=next(s for s in chunks if s['source_id']==binding['source_id'])
        assert actual['document_sha256']==sha256(path.read_bytes()).hexdigest()
        assert actual['sheet']=='synthetic-ra-data' and actual['location'].startswith('행 ')
        assert actual['text'][binding['start']:binding['start']+len(binding['quote'])]==binding['quote']


def test_structured_row_other_product_does_not_inherit_selected_scope():
    result=propose_ra_bindings(profile(),[source('OtherDrug | 성상 | SyntheticDrugA의 흰색 분말')])
    assert not result['source_bindings']
    assert row(result,'성상')['status']=='missing'


@lru_cache(maxsize=1)
def actual_hanmi():
    path=Path(__file__).resolve().parents[1]/'data/ra_public_validation/kr/hanmi_hanmiflu.pdf'
    return chunk_documents(load_documents([path],allow_ocr=False))


@pytest.mark.parametrize('strength',[30,45,75])
def test_actual_all_uploaded_hanmi_titles_select_only_exact_strength(strength):
    p={'ra_workflow':'product_approval','ra_product_name':'한미플루','ra_product_variant':f'{strength}mg',
       'fields':[{'id':'drug','value_key':'제품명','label':'제품명','evidence_role':'product_name'}]}
    result=propose_ra_bindings(p,actual_hanmi())
    item=row(result,'제품명')
    assert item['status']=='proposed',item
    assert item['candidate_count']==1 and item['binding']['quote']==f'한미플루 {strength}mg'
    source_record=next(s for s in actual_hanmi() if s['source_id']==item['binding']['source_id'])
    assert source_record['page']==1 and len(source_record['document_sha256'])==64
    assert prepare_ra_workflow(p,actual_hanmi(),result['source_bindings'],{})['ready_for_output_check']
