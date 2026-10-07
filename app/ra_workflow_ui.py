"""Explicit source-confirmed RA entry, with offline checks and guarded downloads."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st
from agent.field_citations import profile_field, split_field_citations
from agent.output_check import verify_output
from agent.ra_workflows import prepare_ra_workflow
from agent.ra_workpack import attachment_checklist
from agent.multimodal_intake import validate_generation_source
from agent.retrieve import chunk_documents, load_documents, search_chunks
from app.form_config import selection_label, selection_options, value_rule_help
from app.multimodal_ui import render_multimodal_upload
from templates.compatibility import fill_compatible_template

CATALOG = ROOT / 'templates/ra_workflow_catalog.json'


def fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _within_root(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        raise ValueError('등록된 양식 파일 경로를 확인할 수 없음')
    return path


def load_workflows():
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    entries = catalog['workflows']
    if not entries or len({row['id'] for row in entries}) != len(entries):
        raise ValueError('RA 업무 목록이 비어 있거나 중복됨')
    return entries


def resolve_workflow(entry):
    template = _within_root(entry['source_path'])
    profile_path = _within_root(entry['profile_path'])
    if sha256(template.read_bytes()).hexdigest() != entry['source_sha256']:
        raise ValueError('공식 원본이 등록된 SHA와 다름')
    if sha256(profile_path.read_bytes()).hexdigest() != entry['profile_sha256']:
        raise ValueError('등록된 양식 매핑이 변경됨')
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    if profile.get('source_sha256') != entry['source_sha256'] or profile.get('domain') != 'pharmaceutical_ra':
        raise ValueError('양식과 등록된 RA 원본의 연결이 다름')
    return template, profile


def parse_uploads(files):
    """Discard private parser copies after preserving actual file SHA/location in chunks."""
    if len(files) > 20:
        raise ValueError('한 번에 원자료를 20개 이하로 첨부해 주세요')
    names = [file.name for file in files]
    if len(set(names)) != len(names) or any(Path(name).name != name or '/' in name or '\\' in name for name in names):
        raise ValueError('첨부 파일명 중복이나 경로를 허용하지 않음')
    with tempfile.TemporaryDirectory(prefix='ra-workflow-input-') as directory:
        paths = []
        for file in files:
            data = file.getvalue()
            if not 0 < len(data) <= 50 * 1024 * 1024:
                raise ValueError('원자료는 비어 있지 않은 50 MiB 이하 파일이어야 함')
            path = Path(directory) / file.name
            path.write_bytes(data); paths.append(path)
        return chunk_documents(load_documents(paths, allow_ocr=False))


def export_workflow(entry, profile, sources, bindings, direct):
    """Re-prepare current values immediately before fill and independent re-open checks."""
    template, original = resolve_workflow(entry)
    if (set(profile) - set(original) - {'ra_product_name', 'ra_product_variant'}
            or any(profile.get(key) != original.get(key) for key in original if key not in {'ra_product_name', 'ra_product_variant'})):
        raise ValueError('등록 매핑과 검수한 양식이 다름')
    for source in sources:
        if 'verification_fingerprint' in source or 'verification_receipt' in source:
            validate_generation_source(source)
    prepared = prepare_ra_workflow(profile, sources, source_bindings=bindings, direct_values=direct)
    if not prepared['ready_for_output_check'] or prepared['review']['blocking']:
        raise ValueError('출처·제품·수치·필수 항목 오류를 해결해야 함')
    used_profile = prepared['template_profile']
    literals = {key: value['value'] for key, value in prepared['locked_fields'].items()}
    values = prepared['draft']
    if used_profile.get('citation_mode') == 'sidecar':
        values = {key: split_field_citations(value, profile_field(used_profile, key), literal=literals.get(key))[0]
                  for key, value in values.items()}
    with tempfile.TemporaryDirectory(prefix='ra-workflow-output-') as directory:
        output = Path(directory) / ('작성본' + template.suffix)
        fill_compatible_template(template, values, output, profile=used_profile)
        proof = verify_output(template, output, values, profile=used_profile)
        data = output.read_bytes()
        if (proof.get('status') != 'passed' or proof.get('sha', {}).get('output') != sha256(data).hexdigest()
                or proof.get('sha', {}).get('template') != entry['source_sha256']):
            raise ValueError('현재 출력과 독립 검수 증거가 다름')
    sidecar = {**prepared, 'output_verification': proof, 'workflow_id': entry['id'],
               'source_url': entry.get('source_url'), 'submission_ready': False,
               'scope': '확인한 원자료의 지정 입력칸 기입; 실제 AI 생성·법정 제출 적합성 인증 아님'}
    return {'document': data, 'filename': entry['id'] + template.suffix,
            'evidence': json.dumps(sidecar, ensure_ascii=False, indent=2).encode('utf-8')}


def clear_outputs():
    for key in ('rw_exports', 'rw_result', 'rw_result_signature', 'rw_workpack'):
        st.session_state.pop(key, None)


def _error(exc):
    st.error(str(exc) if isinstance(exc, ValueError) else '작업을 완료하지 못했습니다. 양식과 첨부 파일을 확인해 주세요.')


def rank_field_sources(field, sources, query=''):
    """Rank the entire attachment set without making later pages inaccessible."""
    ranked = search_chunks((field['label'] + ' ' + query).strip(), sources,
                           lambda texts: [[0.0] for _ in texts], top_k=40) if sources else []
    seen = {source['source_id'] for source in ranked}
    return [*ranked, *[source for source in sources if source['source_id'] not in seen]]


def render_suggestions(profile, sources, context_signature):
    """A proposal is never a confirmed source binding until the user applies it."""
    from agent.ra_autofill import propose_ra_bindings
    if st.session_state.get('rw_proposal_context') != context_signature:
        for key in ('rw_proposals', 'rw_confirmed_suggestions'):
            st.session_state.pop(key, None)
        st.session_state['rw_proposal_context'] = context_signature
    st.subheader('3. 원자료에서 기입 후보 찾기')
    st.caption('항목명·제품·함량이 명확한 원문을 찾아 제안합니다. 후보를 선택해 확인하면 여러 칸에 함께 적용합니다.')
    with st.expander('여러 항목을 한꺼번에 기입하기 좋은 원자료 형식'):
        st.write('CSV·TSV는 제품명, 항목, 값 열로 정리할 수 있습니다. 항목은 아래 양식의 원문 항목명과 같게 적고, 실제 근거가 있는 값만 넣어 주세요.')
        st.code('제품명,항목,값\n<실제 제품명>,<양식의 항목명>,<근거 원문 값>', language=None)
        st.caption('이 정리 파일도 별도 원자료로 기록합니다. 공개 허가자료의 지위·승인·임상 근거를 대신하지 않습니다.')
    if st.button('첨부자료에서 기입 후보 찾기', disabled=not sources, key='rw_suggest'):
        try:
            with st.spinner('첨부 전체에서 원문 항목과 제품 범위를 대조하고 있습니다.'):
                st.session_state['rw_proposals'] = propose_ra_bindings(profile, sources)
        except Exception as exc:
            st.session_state.pop('rw_proposals', None); _error(exc)
    proposal = st.session_state.get('rw_proposals')
    if proposal:
        names = {field['value_key']: field['label'] for field in profile['fields']}
        labels = {'proposed': '확인할 후보', 'missing': '자료를 찾지 못함', 'ambiguous': '여러 후보·확인 필요',
                  'blocked': '검수로 보류', 'direct_input': '직접 입력'}
        st.dataframe([{'항목': names.get(row['value_key'], row['value_key']),
                       '상태': labels.get(row['status'], row['status']),
                       '기입 후보': (row.get('binding') or {}).get('quote', ''),
                       '확인 사항': row.get('reason', '')} for row in proposal['fields']], hide_index=True)
        candidates = proposal.get('source_bindings', {})
        selected = st.multiselect('확인해서 적용할 후보 항목', list(candidates),
                                  format_func=lambda key: names.get(key, key),
                                  key='rw_proposal_selection_' + context_signature[:16])
        with st.expander('선택 후보의 원문·위치 확인', expanded=bool(selected)):
            by_id = {source['source_id']: source for source in sources}
            for key in selected:
                binding = candidates[key]; source = by_id[binding['source_id']]
                st.write(names.get(key, key) + ' → ' + binding['quote'])
                st.caption(f"{source['filename']} · {source.get('location') or source.get('page') or source.get('sheet')}")
                st.code(source['text'], language=None)
        confirm_key = fingerprint((context_signature, selected))[:16]
        confirmed = st.checkbox('선택한 후보의 제품·항목·원문 위치를 확인함', key='rw_proposal_confirm_' + confirm_key)
        if st.button('확인한 후보를 함께 적용', disabled=not selected or not confirmed, key='rw_apply_suggestions'):
            st.session_state['rw_confirmed_suggestions'] = {
                **st.session_state.get('rw_confirmed_suggestions', {}),
                **{key: deepcopy(candidates[key]) for key in selected}}
            clear_outputs()
    return deepcopy(st.session_state.get('rw_confirmed_suggestions', {}))


def render_workflow():
    try:
        entries = load_workflows()
        selected = st.selectbox('1. 업무·공식 양식 선택', [row['id'] for row in entries],
                                format_func=lambda value: next(row['title'] for row in entries if row['id'] == value), key='rw_workflow')
        entry = next(row for row in entries if row['id'] == selected)
        template, profile = resolve_workflow(entry)
    except Exception as exc:
        clear_outputs(); _error(exc); return
    if st.session_state.get('rw_identity') != selected:
        for key in list(st.session_state):
            if key.startswith('rw_') and key != 'rw_workflow':
                st.session_state.pop(key, None)
        st.session_state['rw_identity'] = selected
    st.info(entry.get('coverage_note', '등록된 입력칸만 작성합니다. 남은 입력칸은 직접 확인해야 합니다.'))
    st.caption('기관: ' + str(entry.get('authority', '미확인')) + ' · 최신 접수 가능 여부는 별도 확인 필요')
    if entry.get('source_url'):
        st.link_button('공식 출처 확인', entry['source_url'])
    st.download_button('빈 공식 양식 다운로드', template.read_bytes(), file_name=template.name, key='rw_blank')
    product = st.text_input('검수할 제품명 (원자료에 있는 정확한 이름)', key='rw_product')
    variant = st.text_input('선택 제형·함량 (해당 시)', key='rw_variant')
    profile = {**profile, 'ra_product_name': product.strip(), 'ra_product_variant': variant.strip()}
    files = st.file_uploader('2. 업무 원자료 첨부', type=['pdf', 'docx', 'hwpx', 'hwp', 'xlsx', 'txt', 'csv', 'tsv', 'pptx', 'png', 'jpg', 'jpeg'],
                            accept_multiple_files=True, key='rw_uploads') or []
    try:
        intake = render_multimodal_upload(files, key_prefix='rw_mm', context={'entry': entry, 'profile': profile})
    except Exception as exc:
        st.session_state.pop('rw_sources', None)
        clear_outputs(); _error(exc); return
    if intake['changed']:
        clear_outputs()
    sources = intake['sources']
    st.session_state['rw_sources'] = sources
    if not intake['ready']:
        clear_outputs()
        st.warning('보류된 첨부나 미확인 이미지 원문을 해결해야 양식 기입을 진행할 수 있습니다.')
        return
    st.caption(f'확인 가능한 원문 조각 {len(sources)}개 · 이미지/OCR은 원본 대조 확인 후 사용합니다. 첨부에 없는 사실은 만들지 않습니다.')
    query = st.text_input('원자료 키워드 검색', key='rw_query')
    context_signature = fingerprint({'entry': entry, 'profile': profile, 'sources': sources})
    attachment_checks = {}
    with st.expander('첨부서류·남은 제출 확인 사항'):
        st.caption('아래 상태는 담당자의 확인 기록입니다. 최신 법정 요건이나 첨부파일 내용의 자동 검증 결과가 아닙니다.')
        for item in attachment_checklist(entry):
            st.write('• ' + item['title'] + ' — ' + item.get('condition', '기관 요건 확인'))
            identifier = item['id']
            state = st.selectbox('담당자 확인 상태', ['pending', 'checked', 'not_applicable'],
                                 format_func=lambda value: {'pending': '미확인', 'checked': '담당자가 확인함',
                                                            'not_applicable': '담당자가 해당 없음으로 기록함'}[value],
                                 key='rw_attachment_' + fingerprint((context_signature, identifier))[:16])
            for registered_id in item['registered_ids']:
                attachment_checks[registered_id] = {'status': state, 'confirmed_by_user': state != 'pending'}
            if item.get('source_quote'):
                with st.expander(f"원본 안내문 · {item.get('page', '?')}쪽"):
                    st.text(item['source_quote'])
        st.write(entry.get('unsupported_scope', '서명·필수 첨부·현재 서식과 법정 요건은 제출 전에 확인해야 합니다.'))
    bindings, direct = render_suggestions(profile, sources, context_signature), {}
    st.subheader('기입 후보 확인·수동 근거 보완')
    for field in profile['fields']:
        if field.get('input_required'):
            continue
        value_key = field['value_key']; token = sha256((selected + field['id']).encode()).hexdigest()[:16]
        with st.expander(field['label']):
            proposed = bindings.get(value_key)
            if proposed:
                st.write('확인해서 적용한 원문: ' + proposed['quote'])
                if not st.checkbox('확인한 후보 사용', value=True,
                                   key='rw_use_proposal_' + fingerprint((context_signature, value_key, proposed))[:16]):
                    bindings.pop(value_key, None)
            ranked = rank_field_sources(field, sources, query)
            options = ['', *[source['source_id'] for source in ranked]]
            by_id = {source['source_id']: source for source in ranked}
            def label(identifier):
                source = by_id.get(identifier)
                return '기입하지 않음' if source is None else f"{source['filename']} / {source.get('page') or source.get('sheet') or source.get('location')} / {source['text'][:65]}"
            identifier = st.selectbox('사용할 원문 조각', options, format_func=label,
                                      key='rw_source_' + token + '_' + fingerprint((context_signature, query))[:10])
            if identifier:
                # Editing another source supersedes an earlier batch candidate.
                # Until the new quote is confirmed this field must stay empty.
                bindings.pop(value_key, None)
                source = by_id[identifier]
                st.code(source['text'], language=None)
                quote = st.text_area('그대로 기입할 원문 (문구를 바꾸지 마세요)', value=source['text'],
                                     key='rw_quote_' + token + identifier + context_signature[:10])
                start = None
                if quote and source['text'].count(quote) > 1:
                    start = st.number_input('원문 조각 안의 시작 문자 위치 (0부터)', min_value=0, max_value=len(source['text']),
                                            key='rw_start_' + token + identifier)
                confirmed = st.checkbox('이 문구의 항목·제품·범위와 기입 위치를 확인함',
                                        key='rw_quote_confirm_' + token + fingerprint((context_signature, identifier, quote, start))[:12])
                if confirmed:
                    bindings[value_key] = {'source_id': identifier, 'quote': quote}
                    if start is not None: bindings[value_key]['start'] = int(start)
    st.subheader('4. 신청인·담당자·선택 항목 직접 입력')
    for field in profile['fields']:
        if not field.get('input_required'): continue
        key = 'rw_direct_' + sha256((selected + field['id']).encode()).hexdigest()[:16]
        options = selection_options(field)
        if options is None:
            value = st.text_input(field['label'], key=key, help=value_rule_help(field))
        elif field.get('multiselect'):
            choices = st.multiselect(field['label'], options, key=key, format_func=lambda value, f=field: selection_label(f, value))
            value = json.dumps(choices, ensure_ascii=False) if choices else ''
        else:
            value = st.selectbox(field['label'], options, key=key, format_func=lambda value, f=field: selection_label(f, value))
        direct[field['value_key']] = value
    grounded = [field for field in profile['fields'] if not field.get('input_required')]
    manual = [field for field in profile['fields'] if field.get('input_required')]
    filled = len(bindings) + sum(bool(value.strip()) for value in direct.values())
    progress = st.columns(3)
    progress[0].metric('확인한 원자료 기입 항목', f'{len(bindings)} / {len(grounded)}')
    progress[1].metric('직접 입력한 항목', f'{filled - len(bindings)} / {len(manual)}')
    progress[2].metric('등록된 미기입 항목', len(profile['fields']) - filled)
    st.caption('등록된 입력칸의 작업 현황입니다. 미등록칸·서명·선택·법정 첨부와 제출 요건은 별도로 확인해야 합니다.')
    current_signature = fingerprint({'entry': entry, 'profile': profile, 'sources': sources, 'bindings': bindings,
                                     'direct': direct, 'attachment_checks': attachment_checks})
    if st.session_state.get('rw_current_signature') != current_signature:
        clear_outputs(); st.session_state['rw_current_signature'] = current_signature
    if st.button('5. 출처·제품·수치·누락 검수', key='rw_prepare'):
        try:
            if not bindings and not any(value.strip() for value in direct.values()):
                raise ValueError('원자료 항목이나 직접 입력값을 한 개 이상 확인해 주세요')
            with st.spinner('확인한 값의 제품·출처·수치·필수 항목을 검수하고 있습니다.'):
                result = prepare_ra_workflow(profile, sources, source_bindings=bindings, direct_values=direct)
            st.session_state['rw_result'] = result
            st.session_state['rw_result_signature'] = current_signature
        except Exception as exc:
            clear_outputs(); _error(exc)
    result = st.session_state.get('rw_result')
    if result:
        st.dataframe([{'항목': key, '기입할 내용·출처': value} for key, value in result['draft'].items()], hide_index=True)
        for question in result.get('questions', [])[:2]: st.warning(question)
        for issue in result['review']['issues']:
            st.warning(str(issue.get('message', issue)))
        if result['ready_for_output_check']: st.success('원자료 기입 검수를 통과했습니다. 저장 후 원위치·서식 검수를 추가로 수행합니다.')
    confirmation = st.checkbox('기입 항목과 직접 입력값을 확인함 (제출 요건·서명·첨부는 별도 확인)',
                               key='rw_export_confirm_' + current_signature[:16])
    enabled = bool(result and result['ready_for_output_check'] and not result['review']['blocking'] and confirmation)
    if not enabled:
        st.session_state.pop('rw_exports', None)
    if st.button('6. 양식 기입·독립 검수 후 다운로드 준비', disabled=not enabled, key='rw_export'):
        st.session_state.pop('rw_exports', None)
        try:
            with st.spinner('양식 사본에 기입한 뒤 원래 위치와 서식을 다시 확인하고 있습니다.'):
                exports = export_workflow(entry, profile, sources, bindings, direct)
            st.session_state['rw_exports'] = exports
        except Exception as exc:
            st.session_state.pop('rw_exports', None); _error(exc)
    exports = st.session_state.get('rw_exports')
    if exports and enabled:
        st.success('저장 후 원본 보존·기입 위치 검수까지 완료했습니다. 기관 제출 적합성 인증은 아닙니다.')
        st.download_button('작성 문서 다운로드', exports['document'], file_name=exports['filename'], key='rw_document')
        st.download_button('출처·검수 기록 다운로드', exports['evidence'], file_name='RA_출처검수.json', mime='application/json', key='rw_evidence')
        try:
            from agent.ra_workpack import build_ra_workpack
            package = build_ra_workpack(entry, profile, result, exports,
                                       selected_product={'name': product.strip(), 'variant': variant.strip()},
                                       attachment_checks=attachment_checks)
            st.download_button('문서·근거·작업 현황·첨부 확인 기록 묶음', package['zip_bytes'],
                               file_name=package['filename'], mime='application/zip', key='rw_package')
        except Exception as exc:
            _error(exc)


def render():
    st.set_page_config(page_title='RA·글로벌 문서 작업실', layout='wide')
    st.title('문서 표준화 AI AGENT · RA 문서 작업실')
    st.caption('내 양식과 기존·신규 데이터 → 작성 대상 선택 → 일괄 작성 → 근거·누락 검수 → 원본 양식 사본 저장. 원문 일괄 기입과 실제 AI 작성을 구분합니다.')
    ctd, qos, automatic, forms, changes, change_document, conversion = st.tabs([
        'CTD Module 1·3', 'DMF → CTD 2.3.S', '데이터로 자동 작성', '공식 양식 기입·작업 패키지', '변경 전후 대비표', 'RA 변경 공식 기입', 'HWP 자동 변환'])
    with ctd:
        from app.ctd_ui import render_ctd
        render_ctd()
    with qos:
        from app.qos_ui import render_qos
        render_qos()
    with automatic:
        from app.ra_auto_ui import render_ra_auto
        render_ra_auto()
    with forms:
        render_workflow()
    with changes:
        from app.ra_compare_ui import render_change_compare
        render_change_compare(parser=parse_uploads)
    with change_document:
        from app.ra_change_document_ui import render_change_document
        render_change_document()
    with conversion:
        from app.hwp_ui import render_hwp_converter
        render_hwp_converter()
    st.link_button('AI 보고서·기안 작성 시험 화면', 'http://127.0.0.1:8503/')
    st.caption('실제 AI 작성은 설정한 모델 연결이 필요합니다. 선택 항목의 작성·검수와 전체 법정 제출 요건, 실제 직원의 수정률 평가는 별도입니다.')


if __name__ == '__main__':
    render()
