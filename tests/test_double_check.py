from copy import deepcopy

import pytest

from agent.pipeline import run_pipeline, review_result, build_downloads
from evals.run import MockEvaluationClient


class CheckedClient(MockEvaluationClient):
    def __init__(self, *, fail_completion=False):
        super().__init__({'mock_brief': {'목적': '매출', '보고 대상': '팀장', '보고서 유형': '결과보고서', '마감': '', '분량': '1쪽', '부족한 정보': [], '질문': []}})
        self.fail_completion = fail_completion
        self.inspections = []

    def generate_json(self, name, payload):
        if name == 'grounding':
            source = payload['sources'][0]
            return {'claims': [{'field': claim['field'], 'line': claim['line'], 'status': 'supported', 'evidence': [{'source_id': source['source_id'], 'quote': source['text']}]} for claim in payload['claims']]}
        if name == 'completeness':
            self.inspections.append(deepcopy(payload))
            if self.fail_completion:
                return {'issues': [], 'checked_fields': ['제목']}
            return {'issues': [], 'checked_fields': list(payload['draft'])}
        return super().generate_json(name, payload)


def result(client):
    doc = {'파일명': '성과.txt', '본문': '매출 120만원을 달성함', '표 목록': [], '페이지/시트 정보': [{'본문': '매출 120만원을 달성함', '위치': '줄 1', '페이지': None, '시트': None, '표 목록': []}]}
    return run_pipeline('매출과 위험 원인 세 가지 및 개선 계획을 비교해줘', documents=[doc], client=client, answers={'확인 답변': '팀장에게 보고함'}, semantic_review=True)


def test_original_instruction_and_follow_up_answers_are_checked_and_changes_go_stale():
    client = CheckedClient()
    current = result(client)
    assert current['status'] == 'ready'
    assert '위험 원인 세 가지' in client.inspections[0]['instruction']
    assert client.inspections[0]['answers'] == {'확인 답변': '팀장에게 보고함'}
    for field, value in [('instruction', '승인자와 예산을 비교해줘'), ('answers', {'확인 답변': '임원에게 보고함'})]:
        changed = deepcopy(current)
        changed[field] = value
        check = review_result(changed)
        assert check['blocking'] and any(warning['code'] == 'completeness_stale' for warning in check['warnings'])
        with pytest.raises(ValueError):
            build_downloads(changed, confirmed=True)


def test_semantic_success_cannot_bypass_incomplete_editorial_inspection():
    current = result(CheckedClient(fail_completion=True))
    assert not current['grounding']['blocking']
    assert current['completeness']['inspection_failed']
    assert current['review']['blocking'] and current['status'] == 'needs_revision'
    with pytest.raises(ValueError):
        build_downloads(current, confirmed=True)
