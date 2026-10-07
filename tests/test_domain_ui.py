"""UI domain controls use the real pipeline with an explicit offline transport."""

from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from evals.run import MockEvaluationClient

ROOT = Path(__file__).resolve().parents[1]


class UIClient(MockEvaluationClient):
    def __init__(self, purpose='계약금액'):
        super().__init__({'mock_brief': {
            '목적': purpose, '보고 대상': '팀장', '보고서 유형': '결과보고서',
            '마감': '', '분량': '1쪽', '부족한 정보': [], '질문': []}})

    def generate_json(self, name, payload):
        if name == 'grounding':
            from agent.review import CITATION_PATTERN
            by_id = {source['source_id']: source['text'] for source in payload['sources']}
            return {'claims': [{'field': item['field'], 'line': item['line'], 'status': 'supported',
                               'evidence': [{'source_id': identifier, 'quote': by_id[identifier]}
                                            for identifier in CITATION_PATTERN.findall(item['text']) if identifier in by_id]}
                              for item in payload['claims']]}
        if name == 'completeness':
            return {'issues': [], 'checked_fields': list(payload['draft'])}
        return super().generate_json(name, payload)


@pytest.fixture
def domain_ui(monkeypatch, tmp_path, native_unavailable):
    import agent.pipeline as pipeline
    import llm.client
    run = pipeline.run_pipeline
    calls = []

    def generate(instruction, paths, **kwargs):
        calls.append(deepcopy(kwargs))
        if kwargs.get('office_workflow'):
            text = '연결 매출 실적 100억원임'
            purpose = '매출'
        elif kwargs.get('business_workflow') == 'government_grant':
            text = '정부지원금 100만원임'
            purpose = '정부지원금'
        else:
            text = '계약금액 USD 1000임'
            purpose = '계약금액'
        document = {'파일명': '합성자료.txt', '본문': text, '표 목록': [],
                    '페이지/시트 정보': [{'본문': text, '페이지': 1, '시트': None, '표 목록': []}]}
        return run(instruction, paths, documents=[document], client=UIClient(purpose), **kwargs)

    monkeypatch.setattr(pipeline, 'run_pipeline', generate)
    monkeypatch.setattr(llm.client, 'LLMClient', UIClient)
    monkeypatch.setenv('REPORT_AGENT_DATA_DIR', str(tmp_path))
    app = AppTest.from_file(str(ROOT / 'app/ui.py'), default_timeout=20)
    return app, calls, tmp_path


def choose(app, domain, workflow, kind='report'):
    app.run()
    app.selectbox(key='work_domain').select(domain).run()
    prefix = 'business' if domain == 'business_support' else 'office'
    app.selectbox(key=prefix + '_workflow').select(workflow)
    app.selectbox(key='document_kind').select(kind)
    app.text_area(key='instruction').set_value('팀장에게 첨부 근거로 문서를 작성해줘')
    app.button(key='generate').click().run()
    assert not app.exception


def test_saved_business_application_resumes_its_workflow_kind_answers_and_kpi(domain_ui):
    app, calls, _ = domain_ui
    app.session_state['answers'] = {'제출 목적': '정부 지원 신청'}
    choose(app, 'business_support', 'government_grant', 'application')
    saved = deepcopy(app.session_state['result'])
    assert saved['domain'] == 'business_support' and saved['document_kind'] == 'application'
    assert saved['business_workflow'] == 'government_grant'
    assert saved['status'] == 'ready', saved['review']
    app.selectbox(key='work_domain').select('office_finance').run()
    app.selectbox(key='office_workflow').select('financial_report').run()
    app.selectbox(key='document_kind').select('plan').run()
    app.session_state['answers'] = {'다른 문서 질문': '다른 답변'}
    app.button(key='resume_report').click().run()
    assert not app.exception
    assert app.selectbox(key='work_domain').value == 'business_support'
    assert app.selectbox(key='business_workflow').value == 'government_grant'
    assert app.selectbox(key='document_kind').value == 'application'
    assert app.session_state['answers'] == saved['answers']
    assert app.session_state['result']['metrics']['baseline_draft'] == saved['metrics']['baseline_draft']
    app.button(key='generate').click().run()
    assert calls[-1]['business_workflow'] == 'government_grant'
    assert calls[-1]['office_workflow'] is None
    assert calls[-1]['ra_workflow'] is None
    assert calls[-1]['answers'] == saved['answers']
    assert app.session_state['result']['run_id'] == saved['run_id']
    app.button(key='new_report').click().run()
    assert not app.exception
    assert app.selectbox(key='work_domain').value == 'general'
    assert app.selectbox(key='document_kind').value == 'report'
    assert 'result' not in app.session_state and 'active_run' not in app.session_state


@pytest.mark.parametrize('domain,workflow,before,after,prefix', [
    ('business_support', 'trade_sales', 'USD 1000', 'EUR 1000', 'business'),
    ('office_finance', 'financial_report', '연결 매출', '별도 매출', 'office'),
])
def test_saved_domain_edits_invalidate_download_and_preserve_first_draft(domain_ui, domain, workflow, before, after, prefix):
    app, _, _ = domain_ui
    choose(app, domain, workflow)
    initial = deepcopy(app.session_state['result'])
    assert initial['status'] == 'ready', initial['review']
    app.checkbox(key='confirmed').check().run()
    app.button(key='prepare').click().run()
    assert not app.exception and app.session_state['exports']
    assert all(report['status'] == 'passed' for report in app.session_state['result']['output_verification'].values())
    app.text_area(key='draft_body').set_value(initial['draft']['본문'].replace(before, after)).run()
    assert app.button(key='prepare').disabled
    next(item for item in app.button if item.label == '수정 내용 저장·재검수').click().run()
    assert not app.exception
    result = app.session_state['result']
    assert result['review']['blocking'] and result[prefix + '_checks']['blocking']
    assert after in result['draft']['본문'] and not result['review']['corrected']
    assert result['metrics']['baseline_draft'] == initial['metrics']['baseline_draft']
    assert result['metrics']['user_edit_ratio'] > 0
    assert 'exports' not in app.session_state and 'output_verification' not in result
    assert app.button(key='prepare').disabled
    app.text_area(key='draft_body').set_value(initial['draft']['본문'])
    next(item for item in app.button if item.label == '수정 내용 저장·재검수').click().run()
    assert not app.exception
    assert not app.session_state['result']['review']['blocking']
    assert app.session_state['result']['metrics']['baseline_draft'] == initial['metrics']['baseline_draft']


def test_ui_upload_fallback_survives_absent_optional_office_catalog(domain_ui, monkeypatch):
    import templates.office
    app, _, _ = domain_ui
    def missing(workflow=None):
        raise FileNotFoundError('optional office catalog not provisioned')
    monkeypatch.setattr(templates.office, 'list_office_templates', missing)
    app.run()
    assert not app.exception
    assert app.selectbox(key='work_domain').value == 'general'
    assert any(item.label == '회사·기관 양식 업로드' for item in app.get('file_uploader'))
