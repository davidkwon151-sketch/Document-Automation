"""Internal RA examples keep source bindings and start a fresh document."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import ra_presets as presets

ROOT = Path(__file__).resolve().parents[1]


def test_exactly_two_internal_presets_have_no_generated_facts_or_personal_defaults():
    specs = presets.list_ra_presets()
    assert [spec['id'] for spec in specs] == ['ra_internal_review', 'ra_change_impact']
    assert [spec['report_type'] for spec in specs] == ['결과보고서', '품의서']
    assert [spec['ra_workflow'] for spec in specs] == ['product_approval', 'variation']
    assert all(spec['document_kind'] == 'report' and spec['domain'] == 'pharmaceutical_ra' for spec in specs)
    assert all(set(spec['templates']) == {'docx', 'hwpx'} for spec in specs)
    assert all('자료' in ' '.join(spec['source_requirements']) for spec in specs)
    assert not {'draft', 'values', 'field_values', 'demo_values', 'company', 'signature'} & set(specs[0])
    specs[0]['templates']['docx'] = ('changed.docx', '0' * 64)
    assert presets.list_ra_presets()[0]['templates']['docx'][0] == 'generic_document.docx'


@pytest.mark.parametrize('preset_id', ['ra_internal_review', 'ra_change_impact'])
@pytest.mark.parametrize('format', ['docx', 'hwpx'])
def test_prepare_reads_real_original_three_slots_without_modifying_it(preset_id, format):
    path, profile, spec = presets.prepare_ra_preset(preset_id, format=format, root=ROOT)
    original = path.read_bytes()
    assert profile['source_sha256'] == sha256(original).hexdigest()
    assert {field['value_key'] for field in profile['fields']} == {'제목', '요약', '본문'}
    assert all(field['required'] for field in profile['fields'])
    assert not profile['is_official_submission_form']
    assert profile['template_origin'] == 'project_example'
    assert profile['citation_mode'] == 'inline'
    assert profile['constraints']['summary_max_lines'] == 3
    assert profile['format'] == format
    assert profile['domain'] == spec['domain'] and profile['ra_workflow'] == spec['ra_workflow']
    assert '공식 제출 양식' in spec['notice'] and '아님' in spec['notice']
    assert path.read_bytes() == original


@pytest.mark.parametrize('preset_id,format', [('unknown', 'docx'), ('ra_internal_review', 'pdf'), (None, 'docx')])
def test_unknown_preset_or_unreviewed_format_is_rejected(preset_id, format):
    with pytest.raises(ValueError, match='등록된'):
        presets.prepare_ra_preset(preset_id, format=format, root=ROOT)


def test_modified_or_missing_template_does_not_apply_a_new_policy(tmp_path):
    state = {'result': {'draft': 'old'}, 'confirmed': True}
    original = deepcopy(state)
    with pytest.raises(ValueError, match='SHA'):
        presets.apply_ra_preset(state, 'ra_internal_review', root=tmp_path)
    assert state == original
    target = tmp_path / 'templates/generic_document.docx'
    target.parent.mkdir()
    target.write_bytes((ROOT / 'templates/generic_document.docx').read_bytes() + b'changed')
    with pytest.raises(ValueError, match='SHA'):
        presets.apply_ra_preset(state, 'ra_internal_review', root=tmp_path)
    assert state == original


def test_apply_starts_new_document_and_clears_old_answers_kpi_uploads_and_outputs():
    state = {'result': {'draft': 'old', 'metrics': {'baseline_draft': 'old'}},
             'active_run': {'run_id': 'old'}, 'answers': {'old question': 'private answer'},
             'exports': {'pdf': b'old'}, 'resume_input_paths': ['old-private-source'],
             'form_input_작성자': 'old author', 'answer_0': 'old answer', 'draft_본문': 'old draft',
             'repeat_plan_old': {'count': 8}, 'mapping_old': 'old permissions',
             'attachments': 'old upload', 'attachments_2': 'old upload', 'attachment_epoch': 2,
             'custom_template_epoch': 4, 'confirmed': True, 'document_cache': {'old': 'private'},
             'native_download_context': {'old': 'proof'}, 'office_workflow': 'old',
             'preferences': {'preferred_terms': {}}, 'unrelated_preference': True}
    path, profile, spec = presets.apply_ra_preset(state, 'ra_change_impact', format='hwpx', root=ROOT)
    assert not {'result', 'exports', 'active_run', 'answers', 'resume_input_paths', 'attachments',
                'document_cache', 'native_download_context', 'office_workflow'} & state.keys()
    assert not any(key.startswith(('draft_', 'answer_', 'form_input_', 'repeat_', 'mapping_', 'attachments_')) for key in state)
    assert state['attachment_epoch'] == 3 and state['custom_template_epoch'] == 5
    assert state['confirmed'] is False
    assert state['instruction'] == spec['instruction']
    assert state['template_choice'] == path.name == 'approval_request.hwpx'
    assert state['resume_template_profile'] == profile
    assert state['work_domain'] == 'pharmaceutical_ra' and state['ra_workflow'] == 'variation'
    assert state['unrelated_preference'] is True and 'preferences' in state


def test_switching_preset_and_format_never_reuses_the_old_baseline_or_mapping():
    state = {}
    _, first, _ = presets.apply_ra_preset(state, 'ra_internal_review', root=ROOT)
    state.update(active_run={'run_id': 'first', 'metrics': {'baseline_draft': 'first'}},
                 answers={'old': 'old'}, exports={'docx': b'old'})
    _, second, _ = presets.apply_ra_preset(state, 'ra_change_impact', format='hwpx', root=ROOT)
    assert 'active_run' not in state and 'answers' not in state and 'exports' not in state
    assert first['source_sha256'] != second['source_sha256']
    assert state['resume_template_profile']['format'] == 'hwpx'
    assert state['resume_template_profile']['ra_workflow'] == 'variation'
    assert state['ra_preset_id'] == 'ra_change_impact'


def test_existing_form_options_restores_only_the_same_template_policy(tmp_path, monkeypatch):
    monkeypatch.setenv('REPORT_AGENT_DATA_DIR', str(tmp_path))
    path, prepared, _ = presets.prepare_ra_preset('ra_internal_review', root=ROOT)
    script = ('import streamlit as st\nfrom app.ui import form_options\nfrom pathlib import Path\n'
              'profile, mapping, values = form_options(Path(st.session_state["path"]), '
              'Path(st.session_state["data_root"]))\nst.session_state["observed"] = profile')
    ui = AppTest.from_string(script, default_timeout=30)
    for key, value in {'path': str(path), 'data_root': str(tmp_path), 'resume_template_profile': prepared}.items():
        ui.session_state[key] = value
    ui.run()
    assert not ui.exception
    assert ui.session_state['observed']['domain'] == 'pharmaceutical_ra'
    other = ROOT / 'templates/approval_request.docx'
    ui.session_state['path'] = str(other)
    ui.run()
    assert not ui.exception
    assert ui.session_state['observed'].get('preset_id') is None
    assert ui.session_state['observed'].get('domain') != 'pharmaceutical_ra'
    assert ui.session_state['observed']['source_sha256'] == sha256(other.read_bytes()).hexdigest()


def test_mvp_pdf_instruction_change_and_regeneration_require_fresh_confirmation(tmp_path, monkeypatch):
    """Explicit frontend reset is required, not just a missing backend key."""
    from app.ra_mvp_service import load_mvp_forms
    from llm.client import LLMClient
    first = load_mvp_forms()[0]
    if not (ROOT / first['source_path']).is_file():
        pytest.skip('The separately acquired official source corpus is absent')
    monkeypatch.setenv('REPORT_AGENT_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(LLMClient, 'generate_json', lambda *a, **k: pytest.fail('No real model call allowed'))
    ui = AppTest.from_file(str(ROOT / 'app/ra_mvp_ui.py'), default_timeout=60).run()
    ui.button(key='mvp_generate').click().run()
    ui.checkbox(key='mvp_confirmed').check().run()
    ui.button(key='mvp_export').click().run()
    assert not ui.exception and 'mvp_exports' in ui.session_state
    ui.text_area(key='mvp_instruction').set_value('변경한 작성 지시').run()
    assert not ui.exception and 'mvp_exports' not in ui.session_state
    assert ui.checkbox(key='mvp_confirmed').disabled
    assert ui.checkbox(key='mvp_confirmed').value is False
    # A missing backend key makes AppTest show False, but does not tell the
    # browser to reset its existing checked value. Explicit state assignment
    # must emit set_value=True, even while the checkbox is disabled.
    assert ui.checkbox(key='mvp_confirmed').proto.set_value is True
    ui.button(key='mvp_generate').click().run()
    assert not ui.exception
    assert ui.checkbox(key='mvp_confirmed').value is False
    assert ui.button(key='mvp_export').disabled
