"""Employee-facing Streamlit app. Run: streamlit run app/ui.py."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from agent.pipeline import build_downloads, run_pipeline, review_result
from agent.metrics import start_metrics, record_revision, record_rejection, record_rework, finalize_metrics, summarize_metrics
from agent.preferences import derive_preferences, validate_preferences
from app.form_config import (mapping_rows, configure_profile, validation_rows, configure_value_rules,
                             relation_rows, value_rule_help, VALUE_TYPES, RELATION_TYPES,
                             group_rows, configure_groups, input_group_issues, selection_options, selection_label)
from templates.value_rules import inspect_form_values
from app.storage import save_record, LocalStore
from templates import analyze_template
from app.ra_presets import apply_ra_preset, list_ra_presets, NOTICE as RA_PRESET_NOTICE


def clear_draft_widgets():
    for key in list(st.session_state):
        if key.startswith('draft_'):
            st.session_state.pop(key, None)
    st.session_state['confirmed'] = False


def invalidate_outputs(result):
    st.session_state.pop('exports', None)
    result.pop('output_verification', None)
    result.pop('output_hashes', None)
    result.pop('native_output_verification', None)
    result.pop('_native_preview_bytes', None)
    result.pop('state', None)



def result_profile_is_current(result, profile, template_path=None):
    """A restored result must obey the currently selected writing authority."""
    from agent.brief import model_profile
    from templates.profiles import load_form_profile
    from templates.value_rules import _validation, mapped_rule_profile

    def obeys(candidate, authority, *, selected=False):
        # Generated profiles have no AI-mapping confidence/length requirement.
        # Compare writing contracts without imposing the cache proposal schema.
        try:
            candidate, authority = model_profile(candidate), model_profile(authority)
            if any(candidate.get(key) != authority.get(key) for key in
                   ('source_sha256', 'format', 'repeat_expansion', 'repeat_source_profile')):
                return False
            if selected and any(candidate.get(key) != authority.get(key) for key in
                                ('render_mode', 'citation_mode', 'overflow_mode')):
                return False
            if any(candidate.get(key) != authority[key] for key in
                   ('domain', 'ra_workflow', 'business_workflow', 'office_workflow',
                    'ra_product_name', 'ra_product_variant', 'product_name', 'product_variant')
                   if key in authority):
                return False
            fields = {field['id']: field for field in candidate['fields']}
            if not fields or len(fields) != len(candidate['fields']):
                return False
            expected = mapped_rule_profile(authority, {key: field['value_key'] for key, field in fields.items()})
            originals = {field['id']: field for field in authority['fields']}
            if selected and fields.keys() != originals.keys():
                return False
            for key, field in fields.items():
                original = originals[key]
                if selected:
                    left, right = dict(field), dict(original)
                    for value in (left, right):
                        value['validation'] = _validation(value['validation']) if value.get('validation') else {}
                    if left != right:
                        return False
                else:
                    adjustable = {'value_key', 'required', 'input_required', 'input_mode',
                                  'max_chars', 'confidence', 'validation', 'narrative_style_required', 'suggested_mapping'}
                    if any(field.get(name) != value for name, value in original.items() if name not in adjustable):
                        return False
                    if ((original.get('required') and not field.get('required'))
                            or ((original.get('input_required') or original.get('input_mode') == 'user_provided') and
                                (not field.get('input_required') or field.get('input_mode') != 'user_provided'))):
                        return False
                    registered = _validation(original['validation']) if original.get('validation') else {}
                    restored = _validation(field['validation']) if field.get('validation') else {}
                    if any(restored.get(name) != value for name, value in registered.items()):
                        return False
                    if original.get('max_chars') and (not field.get('max_chars') or field['max_chars'] > original['max_chars']):
                        return False
            for name in ('relations', 'groups'):
                required = expected.get('constraints', {}).get(name, [])
                restored = candidate.get('constraints', {}).get(name, [])
                if (selected and required != restored) or any(item not in restored for item in required):
                    return False
        except (KeyError, TypeError, ValueError):
            return False
        return True

    previous = result.get('template_profile') or {}
    selected = profile or {}
    if not previous.get('fields') and not selected.get('fields'):
        return True
    if not obeys(previous, selected, selected=True):
        return False
    if template_path is not None:
        try:
            if selected.get('source_sha256') != hashlib.sha256(Path(template_path).read_bytes()).hexdigest():
                return False
            registered = load_form_profile(template_path)
            if registered and not obeys(previous, registered):
                return False
        except (OSError, ValueError):
            return False
    return True


def invalidate_profile_mismatch(result, profile, template_path=None, *, defer_widgets=False):
    if not result or result_profile_is_current(result, profile, template_path):
        return False
    if defer_widgets:
        st.session_state['_reset_profile_widgets'] = True
    else:
        clear_draft_widgets()
    invalidate_outputs(result)
    st.session_state.pop('result', None)
    st.session_state.pop('native_download_context', None)
    st.warning('등록된 양식의 입력·검수 규칙이 바뀌었습니다. 원자료와 보완 답변을 유지했으니 초안을 다시 작성해 주세요.')
    return True


def save_run(result, data_root):
    save_record(result, data_root / 'runs' / result['run_id'])
    if result.get('metrics'):
        st.session_state['active_run'] = {'run_id': result['run_id'], 'metrics': result['metrics']}


def form_options(path, data_root, seed_profile=None):
    """Review the original and choose actual input locations before generation."""
    raw = seed_profile if seed_profile is not None else analyze_template(path)
    registered = None
    try:
        from templates.profiles import load_form_profile
        registered = load_form_profile(path) if seed_profile is None else None
        if registered:
            raw = registered
    except ImportError:
        pass
    from agent.template_learning import learn_template, load_learned_profile, save_learned_profile, reusable_profile
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if seed_profile is not None:
        policy = {'expansion': seed_profile.get('repeat_expansion'), 'rules': seed_profile.get('repeat_source_profile')}
        digest = hashlib.sha256((digest + json.dumps(policy, sort_keys=True)).encode()).hexdigest()
    cache_dir = data_root / 'template_mappings'
    cached = load_learned_profile(path, cache_dir,
                                 repeat_expansion=(seed_profile or {}).get('repeat_expansion'))
    proposed = st.session_state.get(f'learned_profile_{digest}')
    authority = registered or seed_profile
    compatible = lambda value: reusable_profile(value, authority) if authority else True
    if (cached and compatible(cached) and cached.get('repeat_expansion') == raw.get('repeat_expansion')
            and cached.get('repeat_source_profile') == raw.get('repeat_source_profile')):
        raw = cached
        st.caption('확인해 저장한 양식 매핑을 재사용함')
    elif proposed and compatible(proposed):
        raw = proposed
    with st.expander('처음 보는 양식 AI 분석·재사용'):
        st.caption('새 양식의 항목·입력 위치·분량을 분석함. 입력값과 개인정보는 매핑 학습에 저장하지 않음.')
        page_count = max(1, len(raw.get('pages', [])))
        image_pages = st.number_input('이미지 양식 분석할 쪽 수', min_value=1, max_value=page_count,
                                     value=page_count, key=f'learning_pages_{digest}') if path.suffix.lower() == '.pdf' else 3
        if path.suffix.lower() == '.pdf' and image_pages < page_count:
            st.caption(f'전체 {page_count}쪽 중 앞 {image_pages}쪽을 분석함. 나머지 쪽은 직접 확인해야 함.')
        if st.button('새 양식 AI 분석', key=f'learn_template_{digest}'):
            try:
                with st.spinner('원본 양식의 항목과 작성 위치를 분석하고 있습니다.'):
                    proposed = learn_template(path, cache_dir=cache_dir, force=True, max_image_pages=image_pages,
                                              base_profile=seed_profile)
                st.session_state[f'learned_profile_{digest}'] = proposed
                for key in (f'mapping_{digest[:12]}', f'pdf_regions_{digest[:12]}',
                            f'value_rules_{digest[:12]}', f'value_relations_{digest[:12]}', f'value_groups_{digest[:12]}'):
                    st.session_state.pop(key, None)
                st.rerun()
            except Exception as exc:
                st.error(f'양식 분석을 완료하지 못했습니다: {exc}')
    restored = st.session_state.get('resume_template_profile')
    if (restored and compatible(restored) and restored.get('source_sha256') == hashlib.sha256(path.read_bytes()).hexdigest()
            and restored.get('repeat_expansion') == raw.get('repeat_expansion')
            and restored.get('repeat_source_profile') == raw.get('repeat_source_profile')):
        raw = restored
    if raw.get('format') == 'pdf':
        from parsers.extended import render_pdf_page
        with st.expander('양식 원본·작성 영역 확인', expanded=raw.get('render_mode') == 'overlay'):
            page = st.number_input('미리보기 페이지', min_value=1, max_value=max(len(raw.get('pages', [])), 1), value=1, key='template_page')
            try:
                st.image(render_pdf_page(path, page), caption=f'{page}쪽 원본')
            except Exception as exc:
                st.warning(f'미리보기를 표시하지 못함: {exc}')
            manual = st.checkbox('작성 영역 직접 지정', value=raw.get('render_mode') == 'overlay', key='manual_pdf')
            if manual:
                st.caption('위치는 페이지 왼쪽 위에서 잼. 단위는 pt이며 72pt = 2.54cm임. 영역을 벗어나는 내용은 저장을 차단함.')
                initial = [{"입력칸 ID": f['id'], "항목": f['label'], "페이지": f['page'], "왼쪽": f['x'], "위쪽": f['y'], "너비": f['width'], "높이": f['height'], "글자 크기": f.get('font_size', 10.0)} for f in raw['fields'] if f['kind'] == 'pdf_overlay']
                if not initial:
                    initial = [{"입력칸 ID": 'pdf_manual_0', "항목": "본문", "페이지": 1, "왼쪽": 80.0, "위쪽": 150.0, "너비": 420.0, "높이": 450.0, "글자 크기": 10.0}]
                regions = st.data_editor(initial, num_rows='dynamic', disabled=['입력칸 ID'],
                                         column_config={'입력칸 ID': None}, key=f'pdf_regions_{digest[:12]}', hide_index=True)
                originals = {field['id']: field for field in raw['fields']}
                manual_fields = []
                for index, region in enumerate(regions):
                    identifier = region.get('입력칸 ID') or f'pdf_manual_{index}'
                    original = originals.get(identifier, {})
                    manual_fields.append({**original, 'id': identifier, 'label': region['항목'], 'value_key': original.get('value_key', region['항목']), 'kind': 'pdf_overlay', 'page': int(region['페이지']), 'x': region['왼쪽'], 'y': region['위쪽'], 'width': region['너비'], 'height': region['높이'], 'font_size': region['글자 크기'], 'required': original.get('required', True)})
                raw = {**raw, **analyze_template(path, manual_fields=manual_fields)}
    for warning in raw.get('warnings', []):
        st.caption(warning)
    if not raw.get('supported'):
        raise ValueError('양식에서 작성할 입력칸을 지정해야 함')
    with st.expander('입력칸 매핑·분량 제한', expanded=True):
        st.caption('채울 값에 제목·요약·본문 또는 예산·작성자 같은 항목명을 입력함. 빈 값은 해당 칸을 사용하지 않음.')
        rows = st.data_editor(mapping_rows(raw), disabled=['입력칸 ID', '항목'], hide_index=True,
                              column_config={'입력칸 ID': None}, key=f'mapping_{digest[:12]}')
        profile, mapping = configure_profile(raw, rows)
        rule_count = sum(bool(field.get('validation')) or field.get('control_type') in {'choice', 'radio'} for field in profile['fields'])
        relations_count = len(profile.get('constraints', {}).get('relations', []))
        groups_count = len(profile.get('constraints', {}).get('groups', []))
        if rule_count or relations_count or groups_count:
            st.caption(f'입력 형식 검사 {rule_count}칸 · 합계·항목 비교 {relations_count}개 · 함께 입력 검사 {groups_count}개를 작성·출력 때 확인함')
        if st.checkbox('입력값·날짜·합계 검사 규칙 편집', key=f'edit_value_rules_{digest}'):
            st.caption('원본에 명시된 형식과 단위만 설정함. 기간 범위·등록번호를 숫자로 바꾸지 않음.')
            rule_rows = st.data_editor(validation_rows(profile), disabled=['입력칸 ID', '항목'], hide_index=True,
                column_config={'입력칸 ID': None,
                    '값 형식': st.column_config.SelectboxColumn(options=list(VALUE_TYPES), required=True),
                    '단위 위치': st.column_config.SelectboxColumn(options=['입력값에 표시', '양식에 인쇄됨']),
                    '날짜 형식': st.column_config.TextColumn(help='YYYY-MM-DD / YYYY.MM.DD / YYYY년 M월 D일. 복수 형식은 | 로 나눔'),
                    '선택값 JSON': st.column_config.TextColumn(help='예: ["국내", "해외"]. 원본의 저장 선택값만 지정함'),
                    '소수 자릿수': st.column_config.TextColumn(help='0 이상 정수. 비워 두면 자릿수 제한 없음')},
                key=f'value_rules_{digest[:12]}')
            st.caption('합계: 기준 항목 = 비교 항목의 합. 이하: 기준 항목 ≤ 비교 항목. 날짜 순서: 기준 날짜 ≤ 비교 날짜. 비교할 여러 항목은 | 로 나눔.')
            st.caption('년·월·일: 기준 항목에 연도 항목명, 비교 항목에 월 항목명 | 일 항목명을 적어 실제 달력 날짜인지 확인함.')
            related_rows = st.data_editor(relation_rows(profile), num_rows='dynamic', hide_index=True,
                column_config={'검사': st.column_config.SelectboxColumn(options=list(RELATION_TYPES)),
                               '허용 오차': st.column_config.TextColumn(help='합계 검사에만 적용. 비우면 정확히 일치해야 함')},
                key=f'value_relations_{digest[:12]}')
            profile = configure_value_rules(profile, rule_rows, relation_items=related_rows)
            st.caption('반복 행·연관 항목: 하나라도 채우면 사용 시 필수 항목을 모두 입력함. 전부 비운 선택 행은 유지함. 행 수는 반복 표 설정에서 별도로 지정함.')
            grouped_rows = st.data_editor(group_rows(profile), num_rows='dynamic', hide_index=True,
                column_config={'함께 입력할 항목 JSON': st.column_config.TextColumn(help='예: ["품목1", "수량1", "금액1"]'),
                               '사용 시 필수 항목 JSON': st.column_config.TextColumn(help='비우면 함께 입력할 항목 전체가 필수임')},
                key=f'value_groups_{digest[:12]}')
            profile = configure_groups(profile, grouped_rows)
        citation_mode = st.selectbox('출처 기록 방식', ['문서에 표시', '별도 출처 기록'], index=1 if raw.get('citation_mode') == 'sidecar' else 0, key=f'citation_mode_{digest[:12]}')
        profile['citation_mode'] = 'sidecar' if citation_mode == '별도 출처 기록' else 'inline'
        if profile.get('format') == 'pdf' and profile.get('render_mode') == 'overlay':
            allow_annex = st.checkbox('긴 내용은 원문을 보존한 별첨으로 작성',
                                     value=raw.get('overflow_mode') == 'annex', key=f'annex_{digest}')
            if allow_annex:
                profile['overflow_mode'] = 'annex'
                st.caption('원본 모든 페이지를 유지하고 넘치는 업무 내용만 뒤에 추가함. 제출기관의 별첨 허용 여부를 확인해야 함. 직접 입력·서명·선택 항목은 대신하지 않음.')
            else:
                profile.pop('overflow_mode', None)
        if st.button('확인한 매핑 저장·재사용', key=f'save_mapping_{digest}'):
            try:
                for field in profile['fields']:
                    field.setdefault('input_mode', 'user_provided' if field.get('input_required') else 'source_grounded')
                    field.setdefault('max_chars', 10000)
                    if field.get('confidence') is None:
                        field['confidence'] = 1.0
                save_learned_profile(path, profile, mapping, cache_dir=cache_dir, user_confirmed=True)
                st.session_state.pop(f'learned_profile_{digest}', None)
                st.rerun()
            except Exception as exc:
                st.error(f'매핑을 저장하지 못했습니다: {exc}')
        if raw.get('learning', {}).get('needs_confirmation'):
            raise ValueError('AI가 제안한 입력 위치·항목을 확인하고 매핑 저장을 눌러야 함')
        values = {}
        input_issues = []
        for field in profile['fields']:
            if field.get('input_required') and field['value_key'] not in values:
                if field.get('control_type') == 'choice_unresolved':
                    raise ValueError(f"{field['label']}: 원본 선택 목록을 확인할 수 없어 기입을 보류함")
                widget_key = f'form_input_{field["value_key"]}'
                options = selection_options(field)
                if options is not None:
                    multiple = bool(field.get('multiselect'))
                    invalid = False
                    if multiple and widget_key in st.session_state:
                        previous = st.session_state[widget_key]
                        if isinstance(previous, str):
                            try:
                                from templates.pdf_choices import parse_choice_value
                                st.session_state[widget_key] = parse_choice_value(field, previous) if previous else []
                            except ValueError:
                                invalid = True
                        else:
                            invalid = (not isinstance(previous, list) or any(value not in options for value in previous)
                                       or len(set(previous)) != len(previous))
                    elif widget_key in st.session_state:
                        invalid = st.session_state[widget_key] not in options
                    if invalid:
                        st.error(f"{field['label']}: 이전 선택값이 현재 양식 목록에 없어 다시 선택해야 함")
                        # An invalid restored value is not silently replaced by a new decision.
                        if not st.button('선택값 비우기', key=f'reset_choice_{field["value_key"]}'):
                            raise ValueError('이전 선택값을 확인하고 선택값 비우기를 눌러야 함')
                        st.session_state[widget_key] = [] if multiple else ''
                    if multiple:
                        from templates.pdf_choices import encode_choice_values
                        chosen = st.multiselect(field['label'], options,
                            format_func=lambda value, selected_field=field: selection_label(selected_field, value),
                            key=widget_key, help=value_rule_help(field))
                        # PDF selection indices use the source option order.
                        values[field['value_key']] = encode_choice_values([value for value in options if value in chosen]) if chosen else ''
                    else:
                        values[field['value_key']] = st.selectbox(field['label'], options,
                            format_func=lambda value, selected_field=field: selection_label(selected_field, value),
                            key=widget_key, help=value_rule_help(field))
                else:
                    values[field['value_key']] = st.text_input(field['label'], key=widget_key, help=value_rule_help(field))
                    if field.get('control_type') == 'combobox':
                        st.caption('직접 입력 가능한 목록: ' + ' / '.join(selection_label(field, value) for value in field.get('options', [])))
                issues = inspect_form_values(values, {'fields': [field]})
                for issue in issues:
                    st.error(issue['message'])
                input_issues.extend(issues)
        grouped_issues = input_group_issues(values, profile)
        for issue in grouped_issues:
            if issue not in input_issues:
                st.error(f"{issue['field']}: {issue['message']}")
                input_issues.append(issue)
        if input_issues:
            raise ValueError('표시된 입력값·연관 항목 오류를 수정해야 함')
    return profile, mapping, values


def render():
    if st.session_state.pop('_reset_profile_widgets', False):
        clear_draft_widgets()
    data_root = Path(os.environ.get("REPORT_AGENT_DATA_DIR", ROOT / "data"))
    if 'preferences' not in st.session_state:
        try:
            st.session_state['preferences'] = validate_preferences(json.loads((data_root / 'preferences.json').read_text(encoding='utf-8')))
        except (FileNotFoundError, ValueError):
            st.session_state['preferences'] = validate_preferences(None)
    if st.session_state.pop("reset_draft_widgets", False):
        clear_draft_widgets()
    st.set_page_config(page_title="문서 표준화 AI AGENT", layout="wide")
    st.title("문서 표준화 AI AGENT")
    st.caption("자료를 근거로 사내외 양식에 맞는 보고서·기안·신청서·공문 등 문서를 작성함")
    from app.catalog_ui import render_catalog
    render_catalog(data_root)
    with st.sidebar.expander('저장한 보고서·KPI'):
        from app.storage import latest_runs
        from evals.work_kpis import summarize_runs
        saved = latest_runs(data_root)
        historical = summarize_runs(saved)
        ratio = historical['median_user_edit_ratio']
        st.caption(f"확정 {historical['finalized_count']}건 · 수정 비율 중앙값 {ratio:.1%}" if ratio is not None else '아직 확정 보고서 KPI가 없습니다.')
        if saved:
            selected_run = st.selectbox('이전 보고서', saved, format_func=lambda r: f"{r.get('draft', {}).get('제목', '보고서')} · {r['run_id'][:8]}", key='saved_run')
            if st.button('보고서 이어서 검토', key='resume_report'):
                st.session_state['result'] = selected_run
                st.session_state['active_run'] = {'run_id': selected_run['run_id'], 'metrics': selected_run['metrics']}
                st.session_state['instruction'] = selected_run.get('instruction', '')
                st.session_state['answers'] = dict(selected_run.get('answers', {}))
                st.session_state['work_domain'] = selected_run.get('domain', 'general')
                st.session_state['document_kind'] = selected_run.get('document_kind', 'report')
                for prefix in ('ra', 'business', 'office'):
                    if selected_run.get(prefix + '_workflow'):
                        st.session_state[prefix + '_workflow'] = selected_run[prefix + '_workflow']
                st.session_state['attachment_epoch'] = st.session_state.get('attachment_epoch', 0) + 1
                st.session_state['custom_template_epoch'] = st.session_state.get('custom_template_epoch', 0) + 1
                st.session_state.pop('attachments', None)
                st.session_state.pop('custom_template', None)
                st.session_state['template_choice'] = '기본 결과보고서 (DOCX + HWPX)'
                st.session_state['resume_template_profile'] = selected_run.get('template_profile')
                run_folder = (data_root / 'runs' / selected_run['run_id']).resolve()
                resume_paths = [(run_folder / relative).resolve() for relative in selected_run.get('input_paths', [])]
                if any(not path.is_relative_to(run_folder) or not path.is_file() for path in resume_paths):
                    st.error('저장한 근거 자료를 찾을 수 없습니다. 파일을 다시 첨부해 주세요.')
                    resume_paths = []
                st.session_state['resume_input_paths'] = [str(path) for path in resume_paths]
                for key in list(st.session_state):
                    if key.startswith(('mapping_', 'citation_mode_', 'pdf_regions_', 'form_input_')):
                        st.session_state.pop(key, None)
                if selected_run.get('template_path'):
                    if selected_run.get('template_expansion'):
                        from app.repeat_ui import restore_repeat_selection
                        try:
                            restored_path = restore_repeat_selection(selected_run['template_expansion'])
                            st.session_state['public_template_path'] = str(restored_path)
                        except (ValueError, KeyError, OSError) as exc:
                            st.error(f'반복 행 양식을 복원하지 못함: {exc}')
                            st.session_state.pop('public_template_path', None)
                    else:
                        st.session_state['public_template_path'] = selected_run['template_path']
                    st.session_state['public_template_provenance'] = selected_run.get('template_provenance', {})
                else:
                    st.session_state.pop('public_template_path', None)
                    st.session_state.pop('public_template_provenance', None)
                for field in (selected_run.get('template_profile') or {}).get('fields', []):
                    key = field.get('value_key')
                    previous_value = selected_run.get('answers', {}).get(key)
                    if field.get('input_required') and isinstance(previous_value, str):
                        st.session_state[f'form_input_{key}'] = previous_value
                clear_draft_widgets()
                st.session_state.pop('request_fingerprint', None)
                st.session_state.pop('exports', None)
                st.rerun()
    left, right = st.columns([1, 1.4], gap="large")
    with left:
        st.subheader("작성 요청")
        if st.button('새 보고서 시작', key='new_report'):
            clear_draft_widgets()
            for key in ('result', 'exports', 'answers', 'active_run', 'instruction', 'attachments', 'custom_template', 'request_fingerprint', 'resume_template_profile', 'resume_input_paths', 'rejection_reason', 'public_template_path', 'public_template_provenance', 'ra_preset_id', 'ra_preset_format'):
                st.session_state.pop(key, None)
            st.session_state['template_choice'] = '기본 결과보고서 (DOCX + HWPX)'
            st.session_state['instruction'] = ''
            st.session_state['work_domain'] = 'general'
            st.session_state['document_kind'] = 'report'
            st.session_state['attachment_epoch'] = st.session_state.get('attachment_epoch', 0) + 1
            st.session_state['custom_template_epoch'] = st.session_state.get('custom_template_epoch', 0) + 1
            for key in list(st.session_state):
                if key.startswith(('answer_', 'form_input_', 'verify_', 'repeat_')):
                    st.session_state.pop(key, None)
            st.rerun()
        with st.expander('RA 내부 검토 예제'):
            presets = list_ra_presets()
            preset_id = st.selectbox('내부 검토 문서', [item['id'] for item in presets],
                                    format_func=lambda value: next(item['title'] for item in presets if item['id'] == value),
                                    key='ra_preset_choice')
            preset_format = st.selectbox('예제 출력 형식', ['docx', 'hwpx'], format_func=str.upper,
                                        key='ra_preset_format_choice')
            st.caption(RA_PRESET_NOTICE)
            st.caption('예제로 새 문서를 시작하면 이전 초안·답변·첨부·확인 기록을 초기화합니다.')
            if st.button('예제로 새 문서 시작', key='apply_ra_preset'):
                try:
                    apply_ra_preset(st.session_state, preset_id, format=preset_format, root=ROOT)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        instruction = st.text_area("한 줄 지시", key="instruction", placeholder="팀장에게 이번 주 영업 실적을 주간업무보고 1쪽으로 작성해줘", height=100)
        from agent.documents import DOCUMENT_KINDS
        document_kind = st.selectbox('문서 종류', list(DOCUMENT_KINDS),
                                     format_func=lambda key: DOCUMENT_KINDS[key]['title'], key='document_kind')
        from agent.pipeline import DOMAIN_MODULES
        from importlib import import_module
        domain_labels = {'general': '일반 보고서', 'pharmaceutical_ra': '의약품·제약 RA',
                         'business_support': '무역·해외사업·정부 지원사업', 'office_finance': '기획·금융·일상 업무·산업 품질'}
        work_domain = st.selectbox('업무 분야', list(domain_labels), format_func=domain_labels.get, key='work_domain')
        workflow_args = {'ra_workflow': None, 'business_workflow': None, 'office_workflow': None}
        if work_domain in DOMAIN_MODULES:
            module_name, constant, prefix = DOMAIN_MODULES[work_domain]
            workflows = getattr(import_module(module_name), constant)
            workflow = st.selectbox('작성 업무', list(workflows),
                                   format_func=lambda key: workflows[key]['title'], key=prefix + '_workflow')
            workflow_args[prefix + '_workflow'] = workflow
            st.caption('금액·단위·식별자·업무 상태를 원자료와 추가 대조함. 부족한 근거를 추정하여 채우지 않음.')
            with st.expander('작성 전 확인할 자료'):
                for check in workflows[workflow]['checks']:
                    st.write('• ' + check)
                for url in workflows[workflow]['source_urls']:
                    st.link_button('공식 업무·작성 안내', url)
        attachment_epoch = st.session_state.get('attachment_epoch', 0)
        files = st.file_uploader("근거 자료", type=["pdf", "xlsx", "docx", "hwpx", "pptx", "txt", "csv", "tsv", "png", "jpg", "jpeg", "doc", "xls", "hwp", "rtf", "odt"], accept_multiple_files=True,
                                key=f'attachments_{attachment_epoch}' if attachment_epoch else 'attachments')
        resumed = st.session_state.get('resume_input_paths', [])
        if resumed:
            st.caption(f'저장한 근거 자료 {len(resumed)}개를 재사용하며 새 첨부 자료를 함께 반영함')
            if st.button('저장 근거 자료 해제', key='clear_resumed_files'):
                st.session_state.pop('resume_input_paths', None)
                st.rerun()
        template_files = sorted(path for path in (ROOT / "templates").glob("*") if path.suffix.lower() in {".docx", ".hwpx"})
        names = [path.name for path in template_files]
        default_choices = ('기본 결과보고서 (DOCX + HWPX)', '기본 사내외 문서 (DOCX + HWPX)')
        if st.session_state.get('template_choice', default_choices[0]) in default_choices:
            st.session_state['template_choice'] = default_choices[0 if document_kind == 'report' else 1]
        selected = st.selectbox("사내외 양식", list(default_choices) + names, key="template_choice")
        template_epoch = st.session_state.get('custom_template_epoch', 0)
        custom = st.file_uploader('회사·기관 양식 업로드', type=['docx', 'hwpx', 'xlsx', 'pdf', 'pptx', 'doc', 'hwp', 'xls', 'odt', 'rtf'],
                                 key=f'custom_template_{template_epoch}' if template_epoch else 'custom_template')
        template_path, profile, mapping, field_values = None, None, None, {}
        template_expansion = None
        template_error = None
        try:
            if custom:
                digest = hashlib.sha256(custom.getvalue()).hexdigest()
                template_path = data_root / 'templates' / digest / Path(custom.name).name
                template_path.parent.mkdir(parents=True, exist_ok=True)
                template_path.write_bytes(custom.getvalue())
                if template_path.suffix.lower() in {'.doc', '.hwp', '.xls', '.odt', '.rtf'}:
                    from app.template_conversion import prepare_form_template
                    template_path = prepare_form_template(template_path)
            elif selected not in default_choices:
                template_path = ROOT / 'templates' / selected
            elif st.session_state.get('public_template_path'):
                template_path = Path(st.session_state['public_template_path'])
                if template_path.suffix.lower() in {'.doc', '.hwp', '.xls', '.odt', '.rtf'}:
                    from app.template_conversion import prepare_form_template
                    template_path = prepare_form_template(template_path)
            if template_path:
                from app.repeat_ui import repeat_options
                template_path, seed_profile, template_expansion = repeat_options(template_path, data_root)
                profile, mapping, field_values = form_options(template_path, data_root, seed_profile)
                if profile.get('domain') in DOMAIN_MODULES:
                    prefix = DOMAIN_MODULES[profile['domain']][2]
                    workflow_args = {key: None for key in workflow_args}
                    workflow_args[prefix + '_workflow'] = profile.get(prefix + '_workflow')
                    st.caption(f"등록된 양식의 추가 검수를 적용함: {domain_labels[profile['domain']]}")
        except Exception as exc:
            template_error = str(exc)
            st.error(f'양식을 확인해 주세요: {exc}')
        invalidate_profile_mismatch(st.session_state.get('result'), profile, template_path)
        request_fingerprint = hashlib.sha256(json.dumps({
            "instruction": instruction,
            "files": [(file.name, hashlib.sha256(file.getvalue()).hexdigest()) for file in files or []] + [(str(path), hashlib.sha256(Path(path).read_bytes()).hexdigest()) for path in resumed if Path(path).is_file()],
            'template': str(template_path), 'profile': profile, 'field_values': field_values, 'workflow': workflow_args, 'document_kind': document_kind,
        }, ensure_ascii=False).encode()).hexdigest()
        if st.session_state.get("request_fingerprint") not in (None, request_fingerprint):
            clear_draft_widgets()
            for key in ("result", "exports"):
                st.session_state.pop(key, None)
            for key in list(st.session_state):
                if key.startswith("answer_"):
                    st.session_state.pop(key, None)
        st.session_state["request_fingerprint"] = request_fingerprint
        if st.session_state.get("export_template") != selected:
            st.session_state.pop("exports", None)
            st.session_state["export_template"] = selected
        result = st.session_state.get("result")
        questions = result.get("questions", []) if result and result.get("status") == "needs_information" else []
        if st.session_state.get('displayed_questions') != questions:
            for key in list(st.session_state):
                if key.startswith('answer_'):
                    st.session_state.pop(key, None)
            st.session_state['displayed_questions'] = questions
            for index, question in enumerate(questions):
                st.session_state[f'answer_{index}'] = st.session_state.get('answers', {}).get(question, '')
        answers = {}
        if questions:
            st.info("작성 전에 아래 정보를 확인해 주세요.")
            for index, question in enumerate(questions):
                answers[question] = st.text_input(question, key=f"answer_{index}")
        if st.button("답변 반영 후 작성" if questions else "초안 작성", type="primary", key="generate", disabled=bool(template_error)):
            if not instruction.strip():
                st.error("지시를 입력해 주세요.")
            elif questions and any(not answer.strip() for answer in answers.values()):
                st.error("표시된 질문에 답변해 주세요.")
            else:
                try:
                    with st.spinner("지시 해석과 자료 검색·검수를 진행하고 있습니다."):
                        previous = st.session_state.get('result', {})
                        active = st.session_state.get('active_run', previous)
                        run_id = active.get('run_id') if active.get('metrics') else uuid.uuid4().hex
                        folder = data_root / "runs" / run_id
                        paths = [Path(path) for path in resumed]
                        for index, upload in enumerate(files or []):
                            destination = folder / "inputs" / hashlib.sha256(upload.getvalue()).hexdigest()[:16] / str(index) / Path(upload.name).name
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            destination.write_bytes(upload.getvalue())
                            paths.append(destination)
                        # Tests replace the pipeline, never a hidden production mock switch.
                        effective_answers = {**st.session_state.get('answers', {}), **answers}
                        generated = run_pipeline(instruction, paths, answers=effective_answers, template_profile=profile,
                                                 field_values=field_values, preferences=st.session_state.get('preferences'), previous_metrics=active.get('metrics'), semantic_review=True, document_cache=st.session_state.setdefault('document_cache', {}), document_kind=document_kind, **workflow_args)
                        generated["run_id"] = run_id
                        generated['instruction'] = instruction
                        generated["input_paths"] = [str(path.relative_to(folder)) for path in paths]
                        if active.get('metrics') and generated.get('draft'):
                            generated['metrics'] = record_revision(active['metrics'], generated['draft'])
                        elif active.get('metrics'):
                            generated['metrics'] = active['metrics']
                        if template_path:
                            generated.update(template_path=str(template_path), template_profile=generated.get('template_profile') or profile, template_mapping=mapping)
                            if template_expansion:
                                generated['template_expansion'] = template_expansion
                            generated['template_provenance'] = st.session_state.get('public_template_provenance') if not custom and selected in default_choices else {'kind': 'uploaded_or_local', 'source_sha256': profile.get('source_sha256')}
                        folder.mkdir(parents=True, exist_ok=True)
                        save_run(generated, data_root)
                        st.session_state["result"] = generated
                        st.session_state["answers"] = effective_answers
                        st.session_state.pop("exports", None)
                        clear_draft_widgets()
                    st.rerun()
                except Exception as exc:
                    st.error(f"작성하지 못했습니다: {exc}")
    with right:
        st.subheader("초안과 검토")
        result = st.session_state.get("result")
        if not result or "draft" not in result:
            st.info(result.get("message", "요청과 자료를 입력하면 이곳에서 초안을 확인할 수 있습니다.") if result else "요청과 자료를 입력하면 이곳에서 초안을 확인할 수 있습니다.")
            return
        st.caption(f"상태: {'보완 필요' if result['review']['blocking'] else '최종 확인 대기'}")
        if result.get('generation_seconds') is not None:
            st.caption(f"초안 생성·AI 검수 소요: {result['generation_seconds']:.1f}초")
        for prefix, label in (('ra', '의약품 RA'), ('business', '무역·정부 지원사업'), ('office', '기획·금융·일상 업무')):
            if result.get(prefix + '_checks'):
                with st.expander(label + ' 추가 검수 결과', expanded=result[prefix + '_checks']['blocking']):
                    st.json(result[prefix + '_checks'])
        if 'metrics' not in result:
            result['metrics'] = start_metrics(result['draft'])
        with st.form("edit_draft"):
            title = st.text_input("제목", value=result["draft"]["제목"], key="draft_title")
            summary = st.text_area("핵심 요약 (3줄 이내)", value=result["draft"]["요약"], key="draft_summary", height=100)
            body = st.text_area("본문", value=result["draft"]["본문"], key="draft_body", height=300)
            draft = {"제목": title, "요약": summary, "본문": body}
            for field, value in result['draft'].items():
                if field not in draft:
                    draft[field] = st.text_area(field, value=value, key=f'draft_extra_{field}', height=75, disabled=field in result.get('locked_fields', {}))
                    for issue in result['review'].get('warnings', []):
                        if issue.get('field') == field and issue.get('code', '').startswith('form_'):
                            st.error(issue['message'])
            if st.form_submit_button("수정 내용 저장·재검수"):
                try:
                    client = None
                    if result.get('semantic_required'):
                        from llm.client import LLMClient
                        client = LLMClient()
                    checked = review_result(result, draft, client=client)
                    result["draft"], result["review"] = checked["draft"], checked
                    result['metrics'] = record_revision(result['metrics'], checked['draft'])
                    result["status"] = "needs_revision" if checked["blocking"] else "ready"
                    invalidate_outputs(result)
                    st.session_state['confirmed'] = False
                    save_run(result, data_root)
                    st.session_state["reset_draft_widgets"] = True
                    st.rerun()
                except ValueError as exc:
                    st.error(f"수정 내용을 확인해 주세요: {exc}")
        unsaved = draft != result["draft"]
        if unsaved:
            st.info("수정 내용을 저장·재검수한 후 내려받을 수 있습니다.")
        metrics = summarize_metrics(result['metrics'], draft)
        st.subheader('작성 KPI')
        a, b, c = st.columns(3)
        a.metric('최초 AI 초안 대비 수정 비율', f"{metrics['user_edit_ratio']:.1%}")
        b.metric('초안 → 최종본', f"{metrics['elapsed_seconds'] / 60:.1f}분" if metrics['elapsed_seconds'] is not None else '작성 중')
        c.metric('상사 반려 / 재작업', f"{metrics['rejection_count']} / {metrics['rework_count']}회")
        st.caption('내부 AI 검수 후 처음 보여준 초안을 기준으로 비교함. 다시 생성해도 최초 기준은 유지함. 최종본 확정 시 소요 시간을 기록함.')
        if metrics['edit_ratio_is_approximate']:
            st.caption('큰 변경의 수정 비율은 블록 단위 근사치임.')
        with st.expander('항목별 수정·실제 반려 기록'):
            st.write({key: f'{value:.1%}' for key, value in metrics['field_edit_ratios'].items()})
            reason = st.text_input('실제 상사 반려 사유', key='rejection_reason')
            reject, rework = st.columns(2)
            action = None
            if reject.button('상사 반려 1회 기록', key='record_rejection'):
                result['metrics'] = record_rejection(result['metrics'], reason)
                action = 'rejection'
            if rework.button('재작업 1회 기록', key='record_rework'):
                result['metrics'] = record_rework(result['metrics'])
                action = 'rework'
            if action:
                invalidate_outputs(result)
                st.session_state['confirmed'] = False
                save_run(result, data_root)
                st.rerun()
            if st.button('내가 바꾼 용어를 다음 초안에 적용', key='save_preferences', disabled=unsaved):
                preferences = derive_preferences(result['metrics']['baseline_draft'], result['draft'], st.session_state['preferences'])
                LocalStore(data_root)._write(data_root / 'preferences.json', json.dumps(preferences, ensure_ascii=False, indent=2))
                st.session_state['preferences'] = preferences
                st.success('허용된 동의 표현만 저장했습니다. 수치·회사명·본문은 선호 설정에 저장하지 않습니다.')
        for warning in result["review"]["warnings"]:
            st.warning(warning["message"] if isinstance(warning, dict) else warning)
        with st.expander('이중 검증 결과·완결성 확인'):
            proof = result.get('completeness', {})
            st.caption('내용 검수: 의미·오탈자·완결성' if proof.get('semantic_checked') else '내용 검수: 기본 규칙 검사. 실제 모델 검수 여부는 별도 기록함.')
            for issue in proof.get('issues', []):
                st.write(issue['message'])
                if issue.get('suggestion'):
                    st.caption(f"수정 제안: {issue['suggestion']}")
            for suffix, report in result.get('output_verification', {}).items():
                st.success(f"{suffix.upper()} 저장 후 재열기·값·구조 검사 통과")
                st.caption('문서 프로그램별 전체 페이지 배치 확인은 별도 검증 대상임.')
        with st.expander("상사 예상 질문·추가 확인", expanded=True):
            for question in result["boss_review"]["questions"]:
                st.write(f"• {question}")
            for question in result["boss_review"]["additional_checks"]:
                st.warning(question)
        with st.expander("원자료·출처 확인"):
            for source in result["sources"]:
                st.write(f"[{source['source_id']}] {source['filename']} · {source['location']}")
                st.text(source["text"])
                if source.get('requires_verification'):
                    index = source.get('document_index')
                    paths = result.get('input_paths', [])
                    if type(index) is int and index < len(paths):
                        original = data_root / 'runs' / result['run_id'] / paths[index]
                        try:
                            if original.suffix.lower() == '.pdf':
                                from parsers.extended import render_pdf_page
                                st.image(render_pdf_page(original, source.get('page') or 1), caption=f"{source['filename']} {source.get('page')}쪽 원본")
                            else:
                                st.image(str(original), caption='원본 이미지')
                        except Exception as exc:
                            st.warning(f'원본 미리보기를 확인해 주세요: {exc}')
                    for uncertainty in source.get('uncertain_items', []):
                        st.warning(f'판독 확인: {uncertainty}')
                    st.checkbox(f"[{source['source_id']}] 원본과 추출 내용을 대조했습니다.", value=source['source_id'] in result.get('verified_source_ids', []), key=f"verify_{source['source_id']}")
            if any(source.get('requires_verification') for source in result['sources']) and st.button('출처 확인 반영·재검수', key='verify_sources'):
                result['verified_source_ids'] = [source['source_id'] for source in result['sources'] if source.get('requires_verification') and st.session_state.get(f"verify_{source['source_id']}")]
                result['review'] = review_result(result)
                result['status'] = 'needs_revision' if result['review']['blocking'] else 'ready'
                invalidate_outputs(result)
                st.session_state['confirmed'] = False
                save_run(result, data_root)
                st.rerun()
        from app.native_ui import (native_review_options, native_download_context, sync_native_outputs,
                                   clear_native_outputs, downloadable_native_exports, render_native_results)
        native_mode = native_review_options()
        if template_path:
            suffix = template_path.suffix[1:].lower()
            export_paths = {suffix: template_path}
            export_profiles = {suffix: result.get('template_profile') or profile}
            export_mappings = {suffix: mapping}
        else:
            name = {'주간업무보고': 'weekly_report', '품의서': 'approval_request'}.get(result.get('brief', {}).get('보고서 유형'), 'result_report')
            if result.get('document_kind', document_kind) != 'report':
                name = 'generic_document'
            export_paths = {suffix: ROOT / 'templates' / f'{name}.{suffix}' for suffix in ('docx', 'hwpx')}
            export_profiles, export_mappings = {}, {}
        try:
            native_context = native_download_context(result, export_paths, export_profiles, export_mappings, native_mode)
            sync_native_outputs(result, native_context, unsaved=unsaved, blocked=result['review']['blocking'])
        except (OSError, ValueError, TypeError):
            native_context = None
            clear_native_outputs(result)
            st.error('원본 양식을 확인할 수 없습니다. 양식을 다시 선택해 주세요.')
        confirmed = st.checkbox("내용과 출처를 확인했습니다.", key="confirmed")
        if st.button("다운로드 파일 준비", disabled=result["review"]["blocking"] or not confirmed or unsaved or native_context is None, key="prepare"):
            if invalidate_profile_mismatch(result, profile, template_path, defer_widgets=True):
                st.rerun()
            clear_native_outputs(result)
            try:
                with st.spinner('다운로드 파일과 문서 프로그램 출력을 확인하고 있습니다.' if native_mode != 'off' else '다운로드 파일을 준비하고 있습니다.'):
                    exports = build_downloads(result, confirmed=confirmed, template_paths=export_paths,
                                              template_profiles=export_profiles, mappings=export_mappings,
                                              native_review=native_mode)
                # Output preparation refreshes current content/domain checks.
                st.session_state['native_download_context'] = native_download_context(
                    result, export_paths, export_profiles, export_mappings, native_mode)
                result['metrics'] = finalize_metrics(result['metrics'], result['draft'])
                result['state'] = 'confirmed'
                result['output_hashes'] = {suffix: hashlib.sha256(content).hexdigest() for suffix, content in exports.items()}
                output_dir = data_root / 'runs' / result['run_id'] / 'outputs'
                output_dir.mkdir(parents=True, exist_ok=True)
                for suffix, content in exports.items():
                    (output_dir / f'보고서.{suffix}').write_bytes(content)
                save_run(result, data_root)
                st.session_state['exports'] = exports
                st.rerun()
            except Exception as exc:
                if result.get('native_output_verification'):
                    st.session_state['native_download_context'] = native_download_context(
                        result, export_paths, export_profiles, export_mappings, native_mode)
                st.error(f"출력하지 못했습니다: {exc}")
        render_native_results(result)
        available_exports = downloadable_native_exports(result, st.session_state.get('exports', {}), native_mode)
        for suffix, data in (available_exports if not unsaved and confirmed else {}).items():
            mime = {'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'hwpx': 'application/hwp+zip', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'pdf': 'application/pdf', 'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'}[suffix]
            st.download_button(f"{suffix.upper()} 다운로드", data=data, file_name=f"보고서.{suffix}", mime=mime, key=f"download_{suffix}")
        if available_exports and not unsaved and confirmed:
            provenance = {'draft': result['draft'], 'sources': result['sources'], 'grounding': result.get('grounding'), 'completeness': result.get('completeness'),
                          'domain': result.get('domain'), 'document_kind': result.get('document_kind'), **{prefix + suffix: result.get(prefix + suffix) for prefix in ('ra', 'business', 'office') for suffix in ('_workflow', '_checks')},
                          'output_verification': result.get('output_verification'),
                          'native_output_verification': result.get('native_output_verification'),
                          'metrics': result['metrics'], 'output_hashes': result.get('output_hashes')}
            st.download_button('출처·검수·KPI 기록 다운로드', data=json.dumps(provenance, ensure_ascii=False, indent=2), file_name='보고서_출처기록.json', mime='application/json', key='download_provenance')


if __name__ == "__main__":
    render()
