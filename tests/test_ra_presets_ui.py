"""Actual preset controls use the production pipeline and writer with mock LLMs.

Source content is explicitly synthetic; these tests are not real AI accuracy,
human KPI, company-specific template, or native visual-layout observations.
"""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent.review import CITATION_PATTERN
from app.ra_presets import list_ra_presets, prepare_ra_preset
from evals.run import MockEvaluationClient

ROOT = Path(__file__).resolve().parents[1]
LINES = ('시험 함량은 5 mg임', '검토 범위는 합성 자료임', '변경 영향은 추가 확인 필요함')


class PresetUIClient(MockEvaluationClient):
    def __init__(self, report_type='결과보고서'):
        super().__init__({'mock_brief': {'목적': '합성 자료 함량·검토 범위·변경 영향 검토',
            '보고 대상': 'RA 팀장', '보고서 유형': report_type, '마감': '', '분량': '1쪽',
            '부족한 정보': [], '질문': []}})

    def generate_json(self, name, payload):
        if name == 'draft':
            lines = [f"□ {text} [{next(source['source_id'] for source in payload['sources'] if text in source['text'])}]"
                     for text in LINES]
            return {'제목': 'RA 합성 자료 내부 검토', '요약': lines[1], '본문': '\n'.join(lines)}
        if name == 'grounding':
            by_id = {source['source_id']: source['text'] for source in payload['sources']}
            return {'claims': [{'field': item['field'], 'line': item['line'], 'status': 'supported',
                'evidence': [{'source_id': identifier, 'quote': by_id[identifier]}
                    for identifier in CITATION_PATTERN.findall(item['text']) if identifier in by_id]}
                for item in payload['claims']]}
        if name == 'completeness':
            return {'issues': [], 'checked_fields': list(payload['draft'])}
        return super().generate_json(name, payload)


@pytest.fixture
def ui(monkeypatch, tmp_path, native_unavailable):
    import agent.pipeline as pipeline
    import llm.client
    actual_run = pipeline.run_pipeline
    calls = []
    document = {'파일명': '합성 RA 시험자료.txt', '본문': '\n'.join(LINES), '표 목록': [],
                '페이지/시트 정보': [{'본문': '\n'.join(LINES), '페이지': None, '시트': None,
                                       '위치': '합성 시험 문단', '표 목록': []}]}

    def run(instruction, paths=None, **kwargs):
        calls.append(deepcopy(kwargs))
        preset_id = (kwargs.get('template_profile') or {}).get('preset_id')
        spec = next((item for item in list_ra_presets() if item['id'] == preset_id), None)
        client = PresetUIClient((spec or {}).get('report_type', '결과보고서'))
        result = actual_run(instruction, paths, documents=[deepcopy(document)], client=client, **kwargs)
        result.update(mode='mock', actual_model_requests=0, actual_model_responses=0,
                      human_kpi_observations=0, submission_ready=False)
        return result

    monkeypatch.setattr(pipeline, 'run_pipeline', run)
    monkeypatch.setattr(llm.client, 'LLMClient', PresetUIClient)
    monkeypatch.setenv('REPORT_AGENT_DATA_DIR', str(tmp_path))
    return AppTest.from_file(str(ROOT / 'app/ui.py'), default_timeout=45), calls


def apply(ui, preset_id, format):
    ui.selectbox(key='ra_preset_choice').select(preset_id)
    ui.selectbox(key='ra_preset_format_choice').select(format)
    ui.button(key='apply_ra_preset').click().run()
    assert not ui.exception, [item.message for item in ui.exception]


@pytest.mark.parametrize('preset_id', ['ra_internal_review', 'ra_change_impact'])
@pytest.mark.parametrize('format', ['docx', 'hwpx'])
def test_actual_ui_selects_bound_preset_generates_and_checks_selected_format(ui, preset_id, format):
    app, calls = ui
    path, profile, spec = prepare_ra_preset(preset_id, format=format, root=ROOT)
    original = path.read_bytes()
    app.run()
    apply(app, preset_id, format)
    assert app.selectbox(key='document_kind').value == 'report'
    assert app.selectbox(key='work_domain').value == 'pharmaceutical_ra'
    assert app.selectbox(key='ra_workflow').value == spec['ra_workflow']
    assert app.selectbox(key='template_choice').value == path.name
    assert app.text_area(key='instruction').value == spec['instruction']
    assert app.session_state['resume_template_profile'] == profile
    assert any('공식 제출 양식' in caption.value for caption in app.caption)
    app.button(key='generate').click().run()
    assert not app.exception, [item.message for item in app.exception]
    result = app.session_state['result']
    assert result['status'] == 'ready', result.get('review')
    assert result['template_profile']['source_sha256'] == sha256(original).hexdigest()
    assert result['template_profile']['format'] == format
    assert result['template_profile']['preset_id'] == preset_id
    assert not result['template_profile']['is_official_submission_form']
    assert result['brief']['보고서 유형'] == spec['report_type']
    assert calls[-1]['answers'] == {} and calls[-1]['previous_metrics'] is None
    assert calls[-1]['document_kind'] == 'report' and calls[-1]['ra_workflow'] == spec['ra_workflow']
    assert result['metrics']['baseline_draft'] == result['draft']
    app.checkbox(key='confirmed').check().run()
    app.button(key='prepare').click().run()
    assert not app.exception, [item.message for item in app.exception]
    assert set(app.session_state['exports']) == {format}
    assert app.session_state['exports'][format].startswith(b'PK')
    assert result['output_verification'][format]['status'] == 'passed'
    assert path.read_bytes() == original


def test_new_preset_apply_resets_prior_baseline_answers_uploads_and_confirmation(ui, tmp_path):
    app, calls = ui
    app.run()
    apply(app, 'ra_internal_review', 'docx')
    app.button(key='generate').click().run()
    app.checkbox(key='confirmed').check().run()
    assert app.checkbox(key='confirmed').value is True
    previous = deepcopy(app.session_state['result'])
    old_attachment_epoch = app.session_state['attachment_epoch']
    old_template_epoch = app.session_state['custom_template_epoch']
    app.session_state['answers'] = {'previous question': 'previous private answer'}
    app.session_state['form_input_작성자'] = 'previous private author'
    app.session_state['confirmed'] = True
    app.session_state['exports'] = {'docx': b'previous'}
    old_file = tmp_path / 'old-private-material.txt'
    old_file.write_text('previous private evidence', encoding='utf-8')
    app.session_state['resume_input_paths'] = [str(old_file)]
    apply(app, 'ra_change_impact', 'hwpx')
    for key in ('result', 'active_run', 'answers', 'exports', 'resume_input_paths', 'form_input_작성자'):
        assert key not in app.session_state
    assert app.session_state['confirmed'] is False
    assert app.session_state['attachment_epoch'] == old_attachment_epoch + 1
    assert app.session_state['custom_template_epoch'] == old_template_epoch + 1
    assert any(item.proto.id.endswith(f"attachments_{old_attachment_epoch + 1}") for item in app.get('file_uploader'))
    app.button(key='generate').click().run()
    assert not app.exception
    current = app.session_state['result']
    assert app.checkbox(key='confirmed').value is False
    assert app.checkbox(key='confirmed').proto.set_value is True
    assert app.checkbox(key='confirmed').proto.value is False
    assert app.button(key='prepare').disabled
    assert calls[-1]['answers'] == {} and calls[-1]['previous_metrics'] is None
    assert current['run_id'] != previous['run_id']
    assert current['metrics']['draft_started_at'] != previous['metrics']['draft_started_at']
    assert 'previous private' not in str(current['sources'])
    assert old_file.read_text(encoding='utf-8') == 'previous private evidence'


def test_other_template_does_not_inherit_internal_preset_scope(ui):
    app, calls = ui
    app.run()
    apply(app, 'ra_internal_review', 'docx')
    app.selectbox(key='template_choice').select('result_report.docx').run()
    assert not app.exception
    app.button(key='generate').click().run()
    assert not app.exception
    profile = app.session_state['result']['template_profile']
    assert profile.get('preset_id') is None
    assert profile.get('template_origin') != 'project_example'
    assert profile['source_sha256'] == sha256((ROOT / 'templates/result_report.docx').read_bytes()).hexdigest()
    assert calls[-1]['template_profile'].get('preset_id') is None


def test_picker_changes_without_apply_do_not_silently_replace_a_document(ui):
    app, _ = ui
    app.run()
    apply(app, 'ra_internal_review', 'docx')
    app.button(key='generate').click().run()
    original = deepcopy(app.session_state['result'])
    app.selectbox(key='ra_preset_choice').select('ra_change_impact').run()
    app.selectbox(key='ra_preset_format_choice').select('hwpx').run()
    assert not app.exception
    assert app.selectbox(key='template_choice').value == 'generic_document.docx'
    assert app.session_state['result']['run_id'] == original['run_id']
    assert app.session_state['result']['template_profile'] == original['template_profile']


@pytest.mark.parametrize('action', ['new_report', 'instruction'])
def test_new_document_or_changed_instruction_explicitly_resets_browser_confirmation(ui, action):
    app, _ = ui
    app.run()
    apply(app, 'ra_internal_review', 'docx')
    app.button(key='generate').click().run()
    app.checkbox(key='confirmed').check().run()
    assert app.checkbox(key='confirmed').value is True
    if action == 'new_report':
        app.button(key='new_report').click().run()
        assert 'ra_preset_id' not in app.session_state
        assert 'ra_preset_format' not in app.session_state
        assert 'resume_template_profile' not in app.session_state
        app.text_area(key='instruction').input('합성 자료를 검토하는 결과보고서를 작성해줘').run()
    else:
        app.text_area(key='instruction').input(app.text_area(key='instruction').value + '\n검토 범위도 확인해줘').run()
    app.button(key='generate').click().run()
    assert not app.exception
    checkbox = app.checkbox(key='confirmed')
    assert checkbox.value is False and checkbox.proto.set_value is True and checkbox.proto.value is False
    assert app.button(key='prepare').disabled
