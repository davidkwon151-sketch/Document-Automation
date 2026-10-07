"""Headless UI gates and registered, offline public-fact PDF demonstrations.

Mocked gate cases exercise UI state only. The integration cases use the
production service, filler, independent output check, and PDF native-copy path.
No model calls or human KPI observations are made by these tests.
"""

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from uuid import uuid4

from pypdf import PdfReader
import pytest
from streamlit.testing.v1 import AppTest

from agent.metrics import record_revision, start_metrics
from app import ra_mvp_service as service
from llm.client import ConfigurationError, LLMClient

ROOT = Path(__file__).resolve().parents[1]
FORMS = service.load_mvp_forms()


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv('REPORT_AGENT_DATA_DIR', str(tmp_path))

    def forbidden(*args, **kwargs):
        raise AssertionError('UI QA must not invoke a real model')

    monkeypatch.setattr(LLMClient, 'generate_json', forbidden)
    monkeypatch.setattr(LLMClient, 'read_image_json', forbidden)


def app():
    return AppTest.from_file(str(ROOT / 'app/ra_mvp_ui.py'), default_timeout=60).run()


def clean(ui):
    assert not ui.exception, [item.message for item in ui.exception]
    assert not any('created with a default value' in item.value for item in ui.warning)


def downloads(ui):
    return {item.proto.label for item in ui.get('download_button')}


def generate(ui):
    ui.button(key='mvp_generate').click().run()
    clean(ui)
    return ui.session_state['mvp_result']


def prepare(ui):
    assert ui.button(key='mvp_export').disabled
    ui.checkbox(key='mvp_confirmed').check().run()
    assert not ui.button(key='mvp_export').disabled
    ui.button(key='mvp_export').click().run()
    clean(ui)
    assert 'mvp_exports' in ui.session_state
    assert {'작성한 PDF 내려받기', '출처·검수 기록 내려받기'} <= downloads(ui)


@pytest.fixture
def mocked(monkeypatch, tmp_path):
    """Real catalog and controls; explicit stand-ins for generation/export only."""
    import agent.pipeline as pipeline
    calls = []
    exports = []
    # Gate tests remain runnable without the separately downloaded corpus.
    # Their blank download is explicitly a synthetic stand-in, not a source QA.
    from pypdf import PdfWriter
    stand_in = tmp_path / 'ui-state-only.pdf'
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    writer.write(stand_in)

    def fake_resolve(form_id):
        record = deepcopy(next(form for form in FORMS if form['id'] == form_id))
        profile = json.loads((ROOT / record['profile_path']).read_text(encoding='utf-8'))
        return stand_in, profile, record

    def fake_generate(instruction, form_id, **kwargs):
        path, profile, record = service.resolve_mvp_form(form_id)
        prior = kwargs.get('previous_metrics')
        draft = {'제목': 'UI 동작 시험', '요약': '선정 항목 시험함', '본문': '선정 항목 시험함'}
        draft.update({field['value_key']: '' for field in profile['fields']})
        metrics = record_revision(prior, draft) if prior else start_metrics(draft)
        metrics['run_id'] = (prior or {}).get('run_id') or uuid4().hex
        result = {'status': 'ready', 'instruction': instruction, 'draft': draft,
                  'review': {'draft': draft, 'blocking': False, 'warnings': []},
                  'template_profile': profile, 'sources': [], 'questions': [],
                  'metrics': metrics, 'run_id': metrics['run_id'], 'locked_fields': {},
                  'mode': 'rules_demo' if kwargs['demo'] else 'live',
                  'submission_ready': False, 'actual_model_requests': 0,
                  'actual_model_responses': 0}
        calls.append(deepcopy(kwargs))
        return result

    def fake_build(result, **kwargs):
        exports.append(deepcopy(kwargs))
        result.update(output_verification={'pdf': {'status': 'passed'}},
                      output_hashes={'pdf': 'fixture'},
                      native_output_verification={'pdf': {'status': 'warning', 'issues': []}})
        return {'pdf': b'%PDF-UI-STATE-FIXTURE'}

    def fake_review(result, edited, **kwargs):
        return {'draft': deepcopy(edited), 'blocking': False, 'warnings': []}

    monkeypatch.setattr(service, 'generate_mvp', fake_generate)
    monkeypatch.setattr(service, 'resolve_mvp_form', fake_resolve)
    monkeypatch.setattr(pipeline, 'build_downloads', fake_build)
    monkeypatch.setattr(pipeline, 'review_result', fake_review)
    return calls, exports


@pytest.mark.parametrize('form', FORMS, ids=lambda item: item['id'])
def test_real_demo_three_forms_review_confirm_and_prepare_pdf(form):
    paths = [ROOT / form['source_path'], ROOT / form['profile_path']]
    manifest = json.loads((ROOT / form['demo_manifest']).read_text(encoding='utf-8'))
    evidence = next(row for row in manifest['sources'] if row['id'] == form['demo_source_id'])
    paths.append(ROOT / evidence['path'])
    if not all(path.is_file() for path in paths):
        pytest.skip('The locally acquired official public corpus is absent')
    before = {str(path): sha256(path.read_bytes()).hexdigest() for path in paths}
    ui = app()
    ui.selectbox(key='mvp_form').select(form['id']).run()
    result = generate(ui)
    assert result['mode'] == 'rules_demo' and not result['submission_ready']
    assert result['actual_model_requests'] == result['actual_model_responses'] == 0
    assert result['status'] == 'ready' and not result['review']['blocking']
    assert not {'mvp_rejection', 'mvp_rework'} & {button.key for button in ui.button}
    assert any('실사용 KPI 제외' in item.value for item in ui.caption)
    # A harmless title edit is still an unsaved edit requiring a fresh review.
    ui.text_area(key='mvp_draft_제목').set_value('RA 공개 원자료 검토 결과').run()
    assert ui.button(key='mvp_export').disabled
    ui.button(key='mvp_review').click().run()
    clean(ui)
    assert not ui.session_state['mvp_result']['review']['blocking']
    prepare(ui)
    result = ui.session_state['mvp_result']
    output = ui.session_state['mvp_exports']['pdf']
    assert output.startswith(b'%PDF-')
    reader = PdfReader(BytesIO(output))
    assert len(reader.pages) == form['source_page_count']
    assert result['output_verification']['pdf']['status'] == 'passed'
    assert result['metrics']['finalized_at'] is None  # A demo is not a human submission.
    assert result['metrics']['rejection_count'] == result['metrics']['rework_count'] == 0
    assert before == {str(path): sha256(path.read_bytes()).hexdigest() for path in paths}
    artifact_dir = os.environ.get('RA_MVP_UI_QA_DIR')
    if artifact_dir:
        directory = Path(artifact_dir) / form['id']
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'selected-demo.pdf').write_bytes(output)
        proof = {key: value for key, value in result.items() if key != '_native_preview_bytes'}
        (directory / 'evidence.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.mark.parametrize('change', ['instruction', 'direct_input', 'annex', 'unsaved_draft'])
def test_request_or_unsaved_edit_clears_export_proofs_and_disables_download(mocked, change):
    ui = app()
    generate(ui)
    prepare(ui)
    if change == 'instruction':
        ui.text_area(key='mvp_instruction').set_value('다른 작성 지시').run()
    elif change == 'direct_input':
        form = FORMS[0]
        ui.text_input(key=f"mvp_input_{form['id']}_{form['user_input_keys'][0]}").set_value('직접 입력 시험').run()
    elif change == 'annex':
        ui.checkbox(key='mvp_annex').check().run()
    else:
        ui.text_area(key='mvp_draft_제목').set_value('검수 전 변경').run()
    clean(ui)
    assert 'mvp_exports' not in ui.session_state
    assert ui.button(key='mvp_export').disabled
    assert ui.checkbox(key='mvp_confirmed').disabled
    assert '작성한 PDF 내려받기' not in downloads(ui)
    for key in ('output_verification', 'output_hashes', 'native_output_verification'):
        assert key not in ui.session_state['mvp_result']


@pytest.mark.parametrize('change', ['form', 'mode'])
def test_form_or_mode_change_discards_previous_result_and_metrics(mocked, change):
    ui = app()
    generate(ui)
    prepare(ui)
    if change == 'form':
        ui.selectbox(key='mvp_form').select(FORMS[1]['id']).run()
    else:
        ui.radio(key='mvp_mode').set_value('실제 AI 작성').run()
    clean(ui)
    assert 'mvp_result' not in ui.session_state
    assert 'mvp_metrics' not in ui.session_state
    assert 'mvp_exports' not in ui.session_state
    assert '작성한 PDF 내려받기' not in downloads(ui)
    assert 'mvp_export' not in {button.key for button in ui.button}


def test_new_report_resets_metrics_while_regeneration_keeps_baseline(mocked):
    calls, _ = mocked
    ui = app()
    first = deepcopy(generate(ui))
    ui.text_area(key='mvp_draft_제목').set_value('사용자가 고친 제목').run()
    ui.button(key='mvp_review').click().run()
    ui.text_area(key='mvp_instruction').set_value('보완하여 작성').run()
    second = generate(ui)
    assert calls[-1]['previous_metrics']['baseline_draft'] == first['metrics']['baseline_draft']
    assert second['run_id'] == first['run_id']
    assert second['metrics']['baseline_draft'] == first['metrics']['baseline_draft']
    ui.button(key='mvp_new').click().run()
    clean(ui)
    assert 'mvp_result' not in ui.session_state and 'mvp_metrics' not in ui.session_state
    third = generate(ui)
    assert calls[-1]['previous_metrics'] is None
    assert third['run_id'] != first['run_id']
    assert third['metrics']['revision_count'] == third['metrics']['rejection_count'] == third['metrics']['rework_count'] == 0


def test_prepared_pdf_remains_safe_on_the_next_browser_rerun(mocked):
    ui = app()
    generate(ui)
    prepare(ui)
    # AppTest reads all live widget values to send the next browser event.
    # Removing an already-rendered checkbox key must not strand this widget.
    ui.run()
    clean(ui)
    assert 'mvp_exports' in ui.session_state


def test_blocking_review_cannot_be_confirmed_or_exported(mocked):
    _, export_calls = mocked
    ui = app()
    generate(ui)
    ui.session_state['mvp_result']['review'].update(
        blocking=True, warnings=[{'severity': 'error', 'message': '원자료와 다른 수치를 수정해야 함'}])
    ui.run()
    clean(ui)
    assert any('다른 수치' in item.value for item in ui.error)
    assert ui.checkbox(key='mvp_confirmed').disabled
    assert ui.button(key='mvp_export').disabled
    assert not export_calls and 'mvp_exports' not in ui.session_state


def test_real_direct_input_is_locked_and_recorded_as_user_evidence():
    form = FORMS[0]
    if not (ROOT / form['source_path']).is_file():
        pytest.skip('The official public form corpus is absent')
    ui = app()
    key = form['user_input_keys'][0]
    ui.text_input(key=f"mvp_input_{form['id']}_{key}").set_value('시험 신청인').run()
    result = generate(ui)
    assert result['locked_fields'][key]['value'] == '시험 신청인'
    assert result['locked_fields'][key]['source_id'].startswith('SU')
    widget = next(item for item in ui.text_area if item.label == key + ' · 직접 입력값')
    assert widget.disabled
    assert key not in {item.key.removeprefix('mvp_draft_') for item in ui.text_area if item.key.startswith('mvp_draft_')}
    assert any(source['filename'] == '사용자 입력' and source['source_id'] == result['locked_fields'][key]['source_id']
               for source in result['sources'])
    assert not result['submission_ready']


def test_live_configuration_error_is_sanitized_and_does_not_offer_download(monkeypatch):
    sentinel = 'TEST-SECRET-API-KEY-MUST-NOT-APPEAR'

    def fail(*args, **kwargs):
        raise ConfigurationError(sentinel)

    monkeypatch.setattr(service, 'generate_mvp', fail)
    ui = app()
    ui.radio(key='mvp_mode').set_value('실제 AI 작성').run()
    ui.button(key='mvp_generate').click().run()
    clean(ui)
    assert ui.error and any('API 키' in item.value for item in ui.error)
    visible = '\n'.join(item.value for kind in ('error', 'warning', 'caption', 'text') for item in getattr(ui, kind))
    assert sentinel not in visible
    assert 'mvp_exports' not in ui.session_state
    assert '작성한 PDF 내려받기' not in downloads(ui)


@pytest.mark.parametrize('change', ['validation', 'source_mapping', 'workflow'])
def test_registered_binding_change_invalidates_confirmed_export(monkeypatch, mocked, change):
    ui = app()
    result = generate(ui)
    first_metrics = deepcopy(result['metrics'])
    prepare(ui)
    resolve = service.resolve_mvp_form

    def changed(form_id):
        path, profile, record = resolve(form_id)
        if change == 'validation':
            profile['fields'][0]['validation'] = {'type': 'integer', 'min': 0}
        elif change == 'source_mapping':
            record['demo_field_map'] = {'registered_contract_test': 'changed_source_key'}
        else:
            record['ra_workflow'] = 'clinical_trial'
        return path, profile, record

    monkeypatch.setattr(service, 'resolve_mvp_form', changed)
    ui.run()
    clean(ui)
    assert 'mvp_exports' not in ui.session_state
    assert not ui.checkbox(key='mvp_confirmed').value
    assert ui.button(key='mvp_export').disabled
    assert ui.button(key='mvp_review').disabled
    assert '작성한 PDF 내려받기' not in downloads(ui)
    assert ui.session_state['mvp_result']['metrics'] == first_metrics


def test_follow_up_questions_are_limited_to_two_and_answers_accumulate(monkeypatch):
    calls = []
    rounds = [['질문 하나', '질문 둘', '표시하면 안 되는 셋'], ['다음 질문', '질문 둘'], []]

    def ask(*args, **kwargs):
        calls.append(deepcopy(kwargs['answers']))
        return {'status': 'needs_information', 'questions': rounds[min(len(calls)-1, 2)]}

    monkeypatch.setattr(service, 'generate_mvp', ask)
    ui = app()
    generate(ui)
    assert len([item for item in ui.text_input if item.key.startswith('mvp_answer_')]) == 2
    ui.text_input(key='mvp_answer_' + sha256('질문 하나'.encode()).hexdigest()[:12]).set_value('첫 답변')
    ui.text_input(key='mvp_answer_' + sha256('질문 둘'.encode()).hexdigest()[:12]).set_value('둘째 답변')
    generate(ui)
    ui.text_input(key='mvp_answer_' + sha256('다음 질문'.encode()).hexdigest()[:12]).set_value('추가 답변')
    generate(ui)
    assert calls[-1] == {'질문 하나': '첫 답변', '질문 둘': '둘째 답변', '다음 질문': '추가 답변'}


@pytest.mark.parametrize('binding', ['source', 'profile'])
def test_source_or_profile_binding_failure_removes_previous_export(mocked, monkeypatch, binding):
    ui = app()
    generate(ui)
    prepare(ui)

    def changed(*args, **kwargs):
        raise ValueError(f'MVP {binding} SHA가 등록 기록과 다름')

    monkeypatch.setattr(service, 'resolve_mvp_form', changed)
    ui.run()
    clean(ui)
    assert ui.error and 'SHA' in ui.error[0].value
    assert '작성한 PDF 내려받기' not in downloads(ui)
    assert 'mvp_exports' not in ui.session_state
    assert 'output_verification' not in ui.session_state['mvp_result']


@pytest.mark.parametrize('form_id,required_terms', [
    ('ra_law_form_23_pdf', ('계획서 버전', '제안/승인/실시 상태', '추정하지 마세요')),
    ('ra_law_form_32_pdf', ('자료 마감 시점', '보고 기간', '대상자 수', '만들지 마세요')),
])
def test_new_clinical_or_safety_form_starts_with_its_own_evidence_instruction(mocked, form_id, required_terms):
    ui = app()
    ui.text_area(key='mvp_instruction').set_value('이전 제품 허가의 별도 지시').run()
    generate(ui)
    prepare(ui)
    previous = ui.session_state['mvp_result']['run_id']
    ui.selectbox(key='mvp_form').select(form_id).run()
    clean(ui)
    instruction = ui.text_area(key='mvp_instruction').value
    assert all(term in instruction for term in required_terms)
    assert '이전 제품 허가' not in instruction
    assert 'mvp_result' not in ui.session_state
    assert 'mvp_exports' not in ui.session_state
    assert '작성한 PDF 내려받기' not in downloads(ui)
    current = generate(ui)
    assert current['run_id'] != previous
    assert not ui.checkbox(key='mvp_confirmed').value
    assert ui.button(key='mvp_export').disabled


def test_form_switch_before_generation_replaces_previous_instruction(mocked):
    ui = app()
    for form_id, term in [('ra_law_form_23_pdf', '계획서 버전'), ('ra_law_form_32_pdf', '대상자 수'),
                          ('ra_law_form_4_pdf', '선택 양식')]:
        ui.text_area(key='mvp_instruction').set_value('이전 양식에만 쓰는 지시').run()
        ui.selectbox(key='mvp_form').select(form_id).run()
        clean(ui)
        instruction = ui.text_area(key='mvp_instruction').value
        assert term in instruction
        assert '이전 양식에만' not in instruction
        assert 'mvp_result' not in ui.session_state

