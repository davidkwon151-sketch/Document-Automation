"""Registered RA forms, explicit demo/live modes, current export proofs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from agent.metrics import (finalize_metrics, record_rejection, record_revision,
                           record_rework, summarize_metrics)
from agent.pipeline import build_downloads, review_result
from app.form_config import selection_label, selection_options, value_rule_help
from app.ra_mvp_service import generate_mvp, load_mvp_forms, resolve_mvp_form
from app.storage import save_record
from llm.client import ConfigurationError, LLMClient, LLMError


def clear_outputs(*, reset_confirmation=True):
    st.session_state.pop('mvp_exports', None)
    if reset_confirmation:
        st.session_state['mvp_confirmed'] = False
    for key in ('output_verification', 'output_hashes', 'native_output_verification', '_native_preview_bytes'):
        st.session_state.get('mvp_result', {}).pop(key, None)


def reset_report():
    for key in list(st.session_state):
        if key.startswith('mvp_'):
            st.session_state.pop(key, None)


def request_signature(form_id, mode, instruction, uploads, values, answers, annex, *, form_binding=None):
    payload = {'form': form_id, 'mode': mode, 'instruction': instruction,
               'uploads': [(file.name, hashlib.sha256(file.getvalue()).hexdigest()) for file in uploads],
               'values': values, 'answers': answers, 'annex': annex,
               'form_binding': form_binding}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def show_error(exc):
    if isinstance(exc, ConfigurationError):
        st.error('AI 연결 설정을 확인해 주세요. OpenAI 모드는 API 키, 로컬 모드는 로컬 서버·작성 모델·임베딩 모델이 필요합니다. 키 값은 화면에 표시하지 않습니다.')
    elif isinstance(exc, LLMError):
        st.error(str(exc))
    elif isinstance(exc, ValueError):
        st.error(str(exc))
    else:
        st.error(f'작업을 완료하지 못했습니다({type(exc).__name__}). API 연결과 첨부 파일을 확인해 주세요.')


def render():
    st.set_page_config(page_title='문서 표준화 AI AGENT · RA MVP', layout='centered')
    st.title('문서 표준화 AI AGENT')
    from app.hwp_ui import render_hwp_converter
    render_hwp_converter()
    st.link_button('RA 주요 업무 11종 · 확인한 원자료 자동 기입', 'http://127.0.0.1:8504/')
    st.caption('RA MVP · 원자료 첨부 → 초안 → 이중 검수 → 수정 → 양식 다운로드')
    with st.sidebar:
        if st.button('새 문서 시작', key='mvp_new'):
            reset_report()
            st.rerun()
        mode = st.radio('작성 모드', ['공개자료 기입 데모', '실제 AI 작성'], key='mvp_mode')
        st.caption('데모는 공식 원문의 선정 항목을 복사·검수합니다. 실제 AI가 작성한 결과나 실사용 KPI로 집계하지 않습니다.')
    demo = mode == '공개자료 기입 데모'
    try:
        forms = load_mvp_forms()
        form_id = st.selectbox('1. 사용할 양식', [form['id'] for form in forms],
                               format_func=lambda value: next(form['title'] for form in forms if form['id'] == value),
                               key='mvp_form')
        template_path, profile, record = resolve_mvp_form(form_id)
    except Exception as exc:
        clear_outputs()
        show_error(exc)
        return
    identity = (form_id, demo)
    identity_changed = st.session_state.get('mvp_identity') != identity
    if identity_changed:
        for key in list(st.session_state):
            if key not in {'mvp_new', 'mvp_mode', 'mvp_form'} and key.startswith('mvp_'):
                st.session_state.pop(key, None)
        st.session_state['mvp_identity'] = identity
    st.info(record['coverage_note'])
    with st.expander('양식 원본·버전·작성 범위'):
        st.write(record['version_note'])
        st.write('원자료로 작성할 항목: ' + ', '.join(record['source_based_keys']))
        st.write('사용자가 직접 입력할 항목: ' + ', '.join(record['user_input_keys']))
        st.download_button('빈 양식 내려받기', template_path.read_bytes(), file_name=template_path.name,
                           mime='application/pdf', key='mvp_blank')
    initial_instruction = {
        'clinical_trial': ('첨부 임상시험계획서·변경 전후 자료를 근거로 확인 가능한 신청 항목을 작성해 주세요. '
                           '시험 식별번호·계획서 버전·제안/승인/실시 상태를 그대로 유지하고, '
                           '제품 설명서만으로 실제 임상시험이나 국내 승인을 추정하지 마세요.'),
        'safety_management': ('첨부 안전성 보고 원자료를 근거로 확인 가능한 보고 항목을 작성해 주세요. '
                              '제품·자료 마감 시점·보고 기간·대상자 수와 실제 평가 결론을 유지하고, '
                              '자료에 없는 안전성 결론이나 승인 이력을 만들지 마세요.'),
    }.get(record['ra_workflow'], '첨부 제품 자료를 근거로 선택 양식의 확인 가능한 항목을 작성하고, 원문 조건과 출처를 유지해 주세요.')
    if identity_changed or 'mvp_instruction' not in st.session_state:
        st.session_state['mvp_instruction'] = initial_instruction
    instruction = st.text_area('2. 작성 지시', key='mvp_instruction', height=100)
    uploads = st.file_uploader('제품 설명서·업무 원자료 첨부', type=['pdf', 'docx', 'hwpx', 'xlsx', 'txt', 'csv'],
                               accept_multiple_files=True, key='mvp_uploads') or []
    if demo:
        st.caption('데모는 제공된 공식 샘플 자료의 선정 항목만 사용합니다. 작성 지시와 새 첨부로 AI 초안을 생성하려면 실제 AI 작성을 선택해 주세요.')
    else:
        st.caption('OpenAI API 또는 .env의 LLM_PROVIDER=local로 로컬 모델을 연결합니다. 로컬 작성은 API 잔액이 필요 없으며 로컬 서버와 모델 설치가 필요합니다. 첨부에 없는 사실을 임의로 기입하지 않습니다.')
    values = {}
    with st.expander('신청인·회사·담당자 정보 직접 입력'):
        for field in profile['fields']:
            key = field['value_key']
            if key not in record['user_input_keys'] or key in values:
                continue
            options = selection_options(field)
            widget_key = f'mvp_input_{form_id}_{key}'
            if options is None:
                values[key] = st.text_input(field['label'], key=widget_key, help=value_rule_help(field))
            elif field.get('multiselect'):
                selected = st.multiselect(field['label'], options, key=widget_key,
                                          format_func=lambda value, field=field: selection_label(field, value))
                values[key] = json.dumps(selected, ensure_ascii=False) if selected else ''
            else:
                values[key] = st.selectbox(field['label'], options, key=widget_key,
                                          format_func=lambda value, field=field: selection_label(field, value))
        st.caption('서명·동의·날짜·기관 필수 첨부와 남은 빈칸은 제출 전에 직접 확인해야 합니다.')
    annex = st.checkbox('긴 원문을 자르지 않고 별첨으로 유지하는 것을 허용함', key='mvp_annex',
                         help='원래 칸에 별첨 참조를 넣습니다. 기관의 별첨 허용 여부는 별도로 확인해야 합니다.')
    current = st.session_state.get('mvp_result', {})
    answers = dict(st.session_state.get('mvp_answers', {}))
    if current.get('status') == 'needs_information':
        st.warning('초안 작성에 필요한 정보를 보완해 주세요.')
        for question in current.get('questions', [])[:2]:
            answer_key = 'mvp_answer_' + hashlib.sha256(question.encode()).hexdigest()[:12]
            answer = st.text_input(question, key=answer_key)
            if answer.strip():
                answers[question] = answer.strip()
    signature = request_signature(form_id, mode, instruction, uploads, values, answers, annex,
                                  form_binding={'record': record, 'profile': profile})
    changed = bool(current and st.session_state.get('mvp_request_signature') != signature)
    if changed:
        clear_outputs()
        st.caption('양식·작성 규칙·지시·자료·직접 입력이 바뀌었습니다. 초안을 다시 작성해야 다운로드할 수 있습니다.')
    if st.button('공개자료 데모 실행' if demo else 'AI 초안 작성·검수', type='primary', key='mvp_generate'):
        clear_outputs()
        try:
            data_root = Path(os.environ.get('REPORT_AGENT_DATA_DIR', ROOT / 'data')) / 'ra_mvp'
            data_root.mkdir(parents=True, exist_ok=True)
            with st.spinner('자료와 양식을 확인하고 초안을 검수하고 있습니다.'):
                with tempfile.TemporaryDirectory(prefix='input-', dir=data_root) as directory:
                    paths = []
                    for index, upload in enumerate(uploads):
                        path = Path(directory) / f'{index}_{Path(upload.name).name}'
                        path.write_bytes(upload.getvalue())
                        paths.append(path)
                    result = generate_mvp(instruction, form_id, paths=paths, field_values=values, answers=answers,
                                          demo=demo, allow_annex=annex,
                                          previous_metrics=st.session_state.get('mvp_metrics'))
            st.session_state['mvp_result'] = result
            st.session_state['mvp_answers'] = answers
            st.session_state['mvp_request_signature'] = signature
            if result.get('metrics'):
                st.session_state['mvp_metrics'] = result['metrics']
            for key in list(st.session_state):
                if key.startswith('mvp_draft_'):
                    st.session_state.pop(key, None)
            st.rerun()
        except Exception as exc:
            show_error(exc)
    result = st.session_state.get('mvp_result')
    if not result:
        return
    if result.get('status') == 'needs_information':
        return
    if not result.get('draft'):
        st.warning(result.get('message', '자료를 보완한 후 다시 작성해 주세요.'))
        return
    st.subheader('3. 초안 미리보기·수정')
    if demo:
        st.caption('공식 원문 기입 데모입니다. 남은 빈칸을 포함한 완전한 제출서류나 AI 작성 품질 인증이 아닙니다.')
    edited = dict(result['draft'])
    for key, value in result['draft'].items():
        if key in result.get('locked_fields', {}):
            st.text_area(key + ' · 직접 입력값', value=value, disabled=True,
                         key='mvp_locked_' + hashlib.sha256((form_id + key + value).encode()).hexdigest()[:16])
        else:
            edited[key] = st.text_area(key, value=value, key='mvp_draft_' + key, height=100,
                                       help='사실과 수치는 출처 ID를 유지하세요. 수정한 내용은 다시 검수해야 합니다.')
    unsaved = edited != result['draft']
    if unsaved:
        clear_outputs()
    if st.button('수정본 다시 검수', key='mvp_review', disabled=changed):
        clear_outputs()
        try:
            with st.spinner('수치·출처·조건·완결성을 다시 확인하고 있습니다.'):
                checked = review_result(result, edited, client=None if demo else LLMClient())
            result.update(draft=checked['draft'], review=checked,
                          status='needs_revision' if checked['blocking'] else 'ready')
            result['metrics'] = record_revision(result['metrics'], result['draft'])
            st.session_state['mvp_metrics'] = result['metrics']
            st.rerun()
        except Exception as exc:
            show_error(exc)
    warnings = result.get('review', {}).get('warnings', [])
    for warning in warnings:
        (st.error if warning.get('severity') == 'error' else st.warning)(warning['message'])
    with st.expander('근거·예상 질문·추가 확인 필요'):
        for question in result.get('boss_review', {}).get('questions', []):
            st.write(question)
        for item in result.get('boss_review', {}).get('additional_checks', []):
            st.write(item)
        for source in result.get('sources', []):
            location = source.get('page') or source.get('sheet') or source.get('location') or ''
            st.write(f"[{source['source_id']}] {source['filename']} / {location}")
            st.text(source['text'])
    if result.get('metrics'):
        score = summarize_metrics(result['metrics'], edited)
        st.caption(('데모 변경 확인 · 실사용 KPI 제외' if demo else '최초 AI 초안 대비 사용자 수정')
                   + f" · 수정 비율 {score['user_edit_ratio']:.1%} · 반려 {score['rejection_count']}회 · 재작업 {score['rework_count']}회")
        if not demo:
            left, right = st.columns(2)
            if left.button('실제 상사 반려 기록', key='mvp_rejection', disabled=changed or unsaved):
                clear_outputs()
                result['metrics'] = record_rejection(result['metrics'])
                st.session_state['mvp_metrics'] = result['metrics']
                st.rerun()
            if right.button('실제 재작업 기록', key='mvp_rework', disabled=changed or unsaved):
                clear_outputs()
                result['metrics'] = record_rework(result['metrics'])
                st.session_state['mvp_metrics'] = result['metrics']
                st.rerun()
    st.subheader('4. 확인·다운로드')
    blocked = changed or unsaved or result.get('review', {}).get('blocking', True)
    confirmed = st.checkbox('초안 내용과 원자료를 확인함', key='mvp_confirmed', disabled=blocked)
    if st.button('양식 파일 준비', key='mvp_export', disabled=blocked or not confirmed):
        clear_outputs(reset_confirmation=False)
        try:
            with st.spinner('양식을 작성하고 저장된 파일을 독립적으로 다시 확인하고 있습니다.'):
                outputs = build_downloads(result, confirmed=True, template_paths={'pdf': template_path},
                                           template_profiles={'pdf': result['template_profile']}, native_review='auto')
            if not demo:
                result['metrics'] = finalize_metrics(result['metrics'], result['draft'])
                st.session_state['mvp_metrics'] = result['metrics']
            data_root = Path(os.environ.get('REPORT_AGENT_DATA_DIR', ROOT / 'data')) / 'ra_mvp'
            save_record(result, data_root / ('demo_runs' if demo else 'live_runs') / result['run_id'])
            st.session_state['mvp_exports'] = outputs
            st.success('원본 보존·기입 위치·출처·수치 검수를 완료했습니다.')
        except Exception as exc:
            show_error(exc)
    if st.session_state.get('mvp_exports') and not blocked:
        st.download_button('작성한 PDF 내려받기', st.session_state['mvp_exports']['pdf'],
                           file_name=f'RA_MVP_{form_id}.pdf', mime='application/pdf', key='mvp_download')
        sidecar = {key: value for key, value in result.items() if key != '_native_preview_bytes'}
        st.download_button('출처·검수 기록 내려받기', json.dumps(sidecar, ensure_ascii=False, indent=2),
                           file_name='RA_MVP_evidence.json', mime='application/json', key='mvp_evidence')
        for proof in result.get('native_output_verification', {}).values():
            for issue in proof.get('issues', []):
                st.warning(issue['message'])


if __name__ == '__main__':
    render()
