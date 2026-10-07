from copy import deepcopy

import pytest

from agent.completeness import check_completeness, completeness_fingerprint

DRAFT={'제목':'분기 영업 결과','요약':'□ 핵심 성과를 보고함 [S1]','본문':'□ 영업 실적을 정리함 [S1]\n○ 개선 과제를 제안함 [S1]'}
BRIEF={'목적':'분기 성과 보고','보고서 유형':'결과보고서','부족한 정보':[]}


class Client:
    def __init__(self,response):self.response=response;self.calls=[]
    def generate_json(self,name,payload):
        self.calls.append((name,deepcopy(payload)))
        if isinstance(self.response,Exception):raise self.response
        return deepcopy(self.response)


def test_offline_deterministic_checks_do_not_certify_semantics_or_mutate():
    draft=deepcopy(DRAFT);brief=deepcopy(BRIEF)
    result=check_completeness(draft,brief)
    assert not result['blocking'] and not result['semantic_checked']
    assert result['mode']=='deterministic' and len(result['fingerprint'])==64
    assert draft==DRAFT and brief==BRIEF


def test_standard_report_title_is_not_empty_content():
    result = check_completeness({**DRAFT, '제목': '주간업무보고'}, BRIEF)
    assert not result['blocking']


@pytest.mark.parametrize('value',['','   ','본문','□ 추가 확인 필요함','TODO'])
def test_empty_or_contentless_required_body_blocks(value):
    result=check_completeness({**DRAFT,'본문':value},BRIEF)
    assert result['blocking'] and any(i['kind']=='empty_content' for i in result['issues'])


def test_missing_required_metadata_blocks_without_forcing_metadata_report_style():
    profile={'fields':[{'value_key':'신청인','label':'신청인','input_required':True}]}
    assert check_completeness(DRAFT,BRIEF,profile)['blocking']
    draft={**DRAFT,'신청인':'홍길동','주소':'경기도 성남시 분당구'}
    profile['fields'].append({'value_key':'주소','label':'주소'})
    result=check_completeness(draft,BRIEF,profile)
    assert not result['blocking']


def test_duplicate_typo_and_truncated_sentence_get_precise_positions():
    body='□ 프로잭트 결과를 정리함 [S1]\n□ 프로잭트 결과를 정리함 [S2]\n○ 비용을 검토하여, [S1]'
    issues=check_completeness({**DRAFT,'본문':body},BRIEF)['issues']
    assert any(i['kind']=='typo' and i['line']==1 for i in issues)
    assert any(i['kind']=='duplicate' and i['line']==2 for i in issues)
    assert any(i['kind']=='truncated' and i['line']==3 for i in issues)


def test_unclosed_parentheses_and_unresolved_questions_block():
    result=check_completeness({**DRAFT,'본문':'□ 추가 비용(승인 요청함 [S1]'}, {**BRIEF,'부족한 정보':['승인자']})
    assert {i['kind'] for i in result['issues']}=={'truncated','unresolved_information'}
    assert result['blocking']


def test_normal_report_heading_with_colon_is_not_a_truncated_sentence():
    draft={**DRAFT,'본문':'□ 결론:\n○ 개선 과제를 제안함 [S1]\n□ 향후 계획:\n○ 후속 검토를 진행함 [S1]'}
    assert not check_completeness(draft,BRIEF)['blocking']


def test_full_model_coverage_and_contradiction_result_never_apply_suggestion():
    issue={'field':'본문','line':1,'kind':'contradiction','severity':'error','message':'승인 여부가 앞뒤로 다름','suggestion':'승인 사실을 사용자에게 확인함'}
    client=Client({'checked_fields':list(DRAFT),'issues':[issue]});draft=deepcopy(DRAFT)
    result=check_completeness(draft,BRIEF,client=client)
    assert result['semantic_checked'] and result['blocking']
    assert result['issues']==[issue] and draft==DRAFT
    assert client.calls[0][0]=='completeness'


def test_warning_is_displayed_without_blocking():
    issue={'field':'본문','line':1,'kind':'instruction_mismatch','severity':'warning','message':'핵심 결론 위치 확인 필요','suggestion':''}
    result=check_completeness(DRAFT,BRIEF,client=Client({'checked_fields':list(DRAFT),'issues':[issue]}))
    assert not result['blocking'] and result['warnings']==[issue]


@pytest.mark.parametrize('response',[
    RuntimeError('secret API payload'),
    {'issues':[]},
    {'issues':[],'checked_fields':['제목','요약']},
    {'issues':[],'checked_fields':['제목','요약','본문','본문']},
    {'issues':[],'checked_fields':['제목','요약','본문','임의항목']},
    {'issues':[{'field':'본문'}],'checked_fields':list(DRAFT)},
    {'issues':[],'checked_fields':list(DRAFT),'draft':{'본문':'바꾼 값'}},
])
def test_model_failure_malformed_or_partial_response_fails_closed(response):
    result=check_completeness(DRAFT,BRIEF,client=Client(response))
    assert result['blocking'] and result['inspection_failed'] and not result['semantic_checked']
    assert result['issues'][-1]['kind']=='inspection_failure'
    assert 'secret' not in repr(result)


@pytest.mark.parametrize('change',[{'field':'서명'},{'line':999},{'line':True},{'severity':'pass'},{'kind':'approved'},{'message':''},{'suggestion':{'작성자':'김영희'}}])
def test_issue_validation_rejects_unregistered_positions_or_generated_values(change):
    issue={'field':'본문','line':1,'kind':'typo','severity':'error','message':'오타 확인 필요','suggestion':''}
    result=check_completeness(DRAFT,BRIEF,client=Client({'checked_fields':list(DRAFT),'issues':[{**issue,**change}]}))
    assert result['inspection_failed'] and result['blocking']


def test_fingerprint_changes_for_draft_instruction_or_profile():
    initial=completeness_fingerprint(DRAFT,BRIEF)
    assert initial!=completeness_fingerprint({**DRAFT,'본문':'수정함'},BRIEF)
    assert initial!=completeness_fingerprint(DRAFT,{**BRIEF,'목적':'예산 검토'})
    assert initial!=completeness_fingerprint(DRAFT,BRIEF,{'fields':[]})


def test_foreign_field_and_nonstring_input_are_rejected_before_network():
    client=Client({'checked_fields':list(DRAFT),'issues':[]})
    for change in ({'임의필드':'x'},{'본문':3}):
        with pytest.raises(ValueError):check_completeness({**DRAFT,**change},BRIEF,client=client)
    assert client.calls==[]
