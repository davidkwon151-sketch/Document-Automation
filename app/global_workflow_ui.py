"""Representative trade forms connected to confirmed multimodal source intake."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from agent.global_workflows import (GLOBAL_WORKFLOWS, blank_global_input, create_global_template,
                                    export_global_workflow, prepare_global_workflow, propose_global_bindings)
from app.multimodal_ui import render_multimodal_upload


def _fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def example_for_context(workflow, rows, state):
    """Keep exact example bytes stable across reruns; a form change starts new inputs."""
    context = (workflow, rows)
    if state.get('gw_example_context') != context:
        with tempfile.TemporaryDirectory(prefix='global-template-') as directory:
            path = Path(directory) / (workflow + '.docx')
            profile = create_global_template(workflow, path, rows=rows)
            state['gw_example'] = {'document': path.read_bytes(), 'profile': profile}
        for key in list(state):
            if key.startswith('gw_') and key not in {'gw_example', 'gw_workflow', 'gw_rows'}:
                state.pop(key, None)
        state['gw_example_context'] = context
    return deepcopy(state['gw_example'])


def sync_global_outputs(state, signature):
    """Content/confirmation changes invalidate proposals and download bytes together."""
    changed = state.get('gw_source_signature') != signature
    if changed:
        for key in ('gw_proposal', 'gw_exports', 'gw_prepared', 'gw_prepare_signature'):
            state.pop(key, None)
        state['gw_source_signature'] = signature
    return changed


def build_global_downloads(example, profile, sources, bindings, direct):
    with tempfile.TemporaryDirectory(prefix='global-example-output-') as directory:
        template = Path(directory) / 'template.docx'
        template.write_bytes(example['document'])
        if sha256(example['document']).hexdigest() != example['profile']['source_sha256']:
            raise ValueError('현재 예제 양식과 확인 매핑의 SHA가 다름')
        original = example['profile']
        if (set(profile) - set(original) - {'global_transaction_id'}
                or any(profile.get(key) != value for key, value in original.items())):
            raise ValueError('원본 양식·입력 위치·검수 계약이 변경됨')
        return export_global_workflow(template, profile, sources, bindings, direct)


def _error(exc):
    st.error(str(exc) if isinstance(exc, ValueError) else '문서를 준비하지 못했습니다. 원자료와 선택 항목을 확인해 주세요.')


def render_global_workflow(files=None):
    st.subheader('글로벌 직무 문서 자동화')
    st.write('거래자료·표·사진의 확인한 항목을 견적송장·상업송장·포장명세서·해외영업 보고 양식에 기입합니다.')
    st.caption('대표 우선 업무 4종입니다. 사용 빈도 순위는 미측정이며, 아래 양식은 프로젝트 제작 예제입니다.')
    left, right = st.columns([3, 1])
    with left:
        workflow = st.selectbox('작성할 문서', list(GLOBAL_WORKFLOWS),
                                format_func=lambda key: GLOBAL_WORKFLOWS[key]['label'], key='gw_workflow')
    with right:
        rows = int(st.number_input('품목 행 수', min_value=1, max_value=20, value=1,
                                   disabled=workflow == 'overseas_activity', key='gw_rows'))
    example = example_for_context(workflow, rows, st.session_state)
    transaction = st.text_input('원자료와 대조할 거래번호', key='gw_transaction',
                                help='원문 Order No. 또는 거래번호와 정확히 같게 입력합니다. 사진·스캔도 거래번호를 확인합니다.')
    profile = {**example['profile'], 'global_transaction_id': transaction.strip()}
    st.download_button('빈 항목별 원자료 정리 파일', blank_global_input(workflow, rows),
                       file_name=workflow + '_blank.txt', mime='text/plain', key='gw_blank')
    st.caption('원자료에 있는 값만 정리합니다. 품목은 Description[1]·Quantity[1]처럼 행 번호를 유지하고 단위·통화·금액을 직접 확인합니다.')
    try:
        if files is not None:
            st.info('바이어 이메일 작업에서 선택한 회사 원자료를 이어서 사용합니다.')
            if st.button('연결한 첨부자료 해제', key='gw_clear_prefill'):
                st.session_state.pop('gw_prefill_files', None)
                st.rerun()
        intake = render_multimodal_upload(files, key_prefix='global_sources',
                                         context={'workflow': workflow, 'rows': rows, 'transaction': transaction,
                                                  'template_sha256': profile['source_sha256']})
    except Exception as exc:
        for key in ('gw_exports', 'gw_prepared', 'gw_proposal'):
            st.session_state.pop(key, None)
        _error(exc)
        return
    source_signature = _fingerprint((intake['signature'], profile))
    sync_global_outputs(st.session_state, source_signature)
    sources = intake['sources']
    if not intake['ready']:
        st.warning('첨부 파일·스캔 원문 확인을 완료해야 기입·다운로드할 수 있습니다.')
    if st.button('원자료에서 항목 후보 찾기', disabled=not transaction.strip() or not sources or not intake['ready'],
                 key='gw_find'):
        try:
            st.session_state['gw_proposal'] = propose_global_bindings(profile, sources)
            st.session_state.pop('gw_exports', None)
        except Exception as exc:
            _error(exc)
    proposal = st.session_state.get('gw_proposal')
    if not proposal:
        return
    status_labels = {'proposed': '원문 후보 1개', 'ambiguous': '복수 원문·선택 필요',
                     'missing': '자료 없음', 'direct_input': '직접 입력'}
    st.dataframe([{'항목': field['value_key'], '상태': status_labels[field['status']],
                   '후보': field['candidates'][0]['quote'] if len(field['candidates']) == 1 else ''}
                  for field in proposal['fields']], hide_index=True)
    keyed = {field['value_key']: field for field in profile['fields']}
    by_id = {source['source_id']: source for source in sources}
    bindings, direct = {}, {}
    st.write('기입할 원문을 선택하고 확인합니다')
    for item in proposal['fields']:
        key, field = item['value_key'], keyed[item['value_key']]
        token = _fingerprint((source_signature, key))[:20]
        if item['status'] == 'direct_input':
            direct[key] = st.text_input(key + (' · 필수 직접 입력' if field['required'] else ' · 선택 직접 입력'),
                                        key='gw_direct_' + token)
            continue
        with st.expander(key + ' · ' + status_labels[item['status']], expanded=False):
            candidates = item['candidates']
            selected = st.selectbox('원문 위치 선택', [None, *range(len(candidates))],
                index=1 if len(candidates) == 1 else 0,
                format_func=lambda index: '기입하지 않음' if index is None else
                    candidates[index]['quote'] + ' · ' + by_id[candidates[index]['source_id']]['location'],
                key='gw_candidate_' + token)
            if selected is not None:
                bindings[key] = deepcopy(candidates[selected])
                source = by_id[bindings[key]['source_id']]
                st.text(source['context_text'])
                st.caption(source['filename'] + ' · ' + source['location'] + ' · ' + source['source_id'])
            if not candidates:
                st.caption('확인 매핑의 source_labels와 일치하는 원문 항목의 전체 값을 연결합니다. 항목명이 다르면 양식 학습에서 먼저 원문 항목명을 확인 매핑해야 합니다.')
                identifier = st.selectbox('직접 연결할 원자료', [None, *by_id],
                    format_func=lambda identifier: '선택하지 않음' if identifier is None else
                        by_id[identifier]['filename'] + ' · ' + by_id[identifier]['location'], key='gw_source_' + token)
                if identifier:
                    st.text(by_id[identifier]['context_text'])
                    quote = st.text_area('이 칸에 넣을 원문 전체 인용', key='gw_quote_' + token)
                    if quote.strip():
                        bindings[key] = {'source_id': identifier, 'quote': quote}
    preparation_signature = _fingerprint((source_signature, bindings, direct))
    if st.session_state.get('gw_prepare_signature') != preparation_signature:
        st.session_state.pop('gw_exports', None)
        st.session_state.pop('gw_prepared', None)
        st.session_state['gw_prepare_signature'] = preparation_signature
    confirmed = st.checkbox('선택 원문의 거래번호·품목 행·단위·통화·위치와 직접 입력을 확인함',
                            key='gw_confirm_' + preparation_signature[:20])
    if not confirmed or not intake['ready']:
        st.session_state.pop('gw_exports', None)
        return
    try:
        prepared = prepare_global_workflow(profile, sources, bindings, direct)
        st.session_state['gw_prepared'] = prepared
        st.dataframe([{'항목': key, '기입값': value} for key, value in prepared['plain_values'].items()], hide_index=True)
        for question in prepared['questions']:
            st.info(question)
        if prepared['review']['issues']:
            st.dataframe(prepared['review']['issues'], hide_index=True)
        if st.button('양식 기입 · 저장 후 독립 검수', disabled=not prepared['ready_for_output_check'], key='gw_export'):
            with st.spinner('원본 SHA·수치·품목 관계·원위치와 저장본을 확인하고 있습니다.'):
                st.session_state['gw_exports'] = build_global_downloads(example, profile, sources, bindings, direct)
    except Exception as exc:
        st.session_state.pop('gw_exports', None)
        _error(exc)
    exported = st.session_state.get('gw_exports')
    if exported:
        st.success('확인한 항목의 기입과 저장 후 원위치 검수를 완료했습니다.')
        st.download_button('기입한 DOCX', exported['document'], file_name=exported['filename'],
                           mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document', key='gw_docx')
        st.download_button('출처·검수 기록 JSON', exported['evidence'], file_name='global_evidence.json',
                           mime='application/json', key='gw_evidence')
        st.caption('문서 프로그램 시각 검수·실제 AI 작성 품질·통관/계약 승인·사용자 수정률은 별도 확인 대상입니다.')


if __name__ == '__main__':
    st.set_page_config(page_title='글로벌 문서 자동화', layout='wide')
    render_global_workflow()
