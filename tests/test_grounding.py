from copy import deepcopy
from unittest.mock import Mock

import pytest

from agent.grounding import inspect_grounding, evidence_fingerprint
from agent.pipeline import run_pipeline, review_result, build_downloads
from agent.review import CITATION_PATTERN
from evals.run import MockEvaluationClient


def draft():
    return {'제목': '성과 결과보고서', '요약': '□ 매출 120만원을 달성함 [S1]', '본문': '○ 매출 120만원을 달성함 [S1]'}


def sources():
    return [{'source_id': 'S1', 'text': '매출 120만원을 달성함', 'filename': '실적.pdf', 'page': 1, 'sheet': None, 'location': '1쪽'}]


def claims(status='supported', quote='매출 120만원을 달성함'):
    return {'claims': [{'field': field, 'line': 1, 'status': status, 'evidence': [{'source_id': 'S1', 'quote': quote}]} for field in ('요약', '본문')]}


def test_grounding_requires_exact_quotes_and_complete_coverage():
    client = Mock()
    client.generate_json.return_value = claims()
    result = inspect_grounding(draft(), sources(), client)
    assert not result['blocking'] and result['claim_count'] == 2
    client.generate_json.return_value = claims(quote='매출 120만원으로 크게 성공함')
    assert inspect_grounding(draft(), sources(), client)['blocking']
    client.generate_json.return_value = {'claims': claims()['claims'][:1]}
    assert inspect_grounding(draft(), sources(), client)['warnings'][0]['code'] == 'semantic_missing'


def test_inverted_meaning_is_blocked_even_with_valid_citation_and_same_number():
    current = draft()
    current['본문'] = '○ 매출 120만원을 미달함 [S1]'
    client = Mock()
    client.generate_json.return_value = claims(status='unsupported')
    proof = inspect_grounding(current, sources(), client)
    result = {'draft': current, 'sources': sources(), 'semantic_required': True, 'grounding': proof}
    assert review_result(result)['blocking']
    assert any(item['code'] == 'semantic_grounding' for item in review_result(result)['warnings'])


def test_manual_fact_edits_invalidate_old_semantic_proof():
    client = Mock()
    client.generate_json.return_value = claims()
    result = {'status': 'ready', 'draft': draft(), 'sources': sources(), 'semantic_required': True,
              'grounding': inspect_grounding(draft(), sources(), client)}
    assert not review_result(result)['blocking']
    result['draft']['본문'] = '○ 매출 120만원임 [S1]'
    assert evidence_fingerprint(result['draft'], sources()) != result['grounding']['fingerprint']
    with pytest.raises(ValueError, match='오류'):
        build_downloads(result, confirmed=True)
    result['draft'] = draft()
    result['sources'][0]['text'] = '자료 변경됨'
    assert review_result(result)['blocking']


@pytest.mark.parametrize('invalid', [None, {'claims': {}}, {'claims': [{'field': '본문', 'line': True}]}, {'claims': claims()['claims'] * 2}])
def test_invalid_grounding_response_fails_closed(invalid):
    client = Mock()
    client.generate_json.return_value = invalid
    with pytest.raises(ValueError):
        inspect_grounding(draft(), sources(), client)


def test_source_identity_information_cannot_be_manufactured_by_brief_model():
    case = {'mock_brief': {'목적': '제출', '보고 대상': '담당자', '보고서 유형': '결과보고서', '마감': '', '분량': '', '부족한 정보': [], '질문': [], '양식 항목': {'작성자': '없는 이름'}}}
    profile = {'fields': [{'value_key': '작성자', 'label': '작성자', 'required': True, 'input_required': True}]}
    result = run_pipeline('제출 보고서 작성', documents=[], client=MockEvaluationClient(case), template_profile=profile)
    assert result['status'] == 'needs_information' and len(result['questions']) <= 2
    assert 'draft' not in result


def test_direct_form_inputs_can_supply_evidence_without_file_and_keep_origin():
    class FormClient(MockEvaluationClient):
        def generate_json(self, name, payload):
            if name == 'draft':
                source = payload['sources'][0]
                line = f"□ {source['text']}임 [{source['source_id']}]"
                return {'제목': '제출 결과보고서', '요약': line, '본문': line, '작성자': f"홍길동 [{source['source_id']}]"}
            return super().generate_json(name, payload)
    case = {'mock_brief': {'목적': '제출', '보고 대상': '담당자', '보고서 유형': '결과보고서', '마감': '', '분량': '', '부족한 정보': [], '질문': []}}
    profile = {'fields': [{'value_key': '작성자', 'label': '작성자', 'required': True, 'input_required': True}]}
    result = run_pipeline('제출 보고서', documents=[], client=FormClient(case), template_profile=profile, field_values={'작성자': '홍길동'})
    assert result['sources'][0]['filename'] == '사용자 입력'
    assert result['sources'][0]['text'] == '작성자 홍길동'
    assert result['draft']['작성자'].startswith('홍길동')
    assert result['metrics']['user_edit_ratio'] == 0
