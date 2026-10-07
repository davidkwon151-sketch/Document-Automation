"""Explicit row selection for uploaded Word/Hancom/Excel forms."""

from hashlib import sha256
import json
from pathlib import Path

import streamlit as st

from templates.repeat_rows import inspect_repeat_tables, prepare_repeat_template, expansion_binding


def restore_repeat_selection(expansion):
    """Restore the source selection, never expand an already expanded copy."""
    original = Path(expansion['original_path'])
    digest = sha256(original.read_bytes()).hexdigest()
    if digest != expansion['original_sha256']:
        raise ValueError('저장한 반복 행 원본이 변경됨')
    plan = expansion['plan']
    for suffix, value in [('enabled', True), ('table', plan['table_id']),
                          ('row', plan['row']), ('count', plan['count'])]:
        st.session_state[f'repeat_{suffix}_{digest}'] = value
    return original


def repeat_options(path, data_root):
    path = Path(path)
    if path.suffix.lower() not in {'.docx', '.hwpx', '.xlsx'}:
        return path, None, None
    digest = sha256(path.read_bytes()).hexdigest()
    with st.expander('반복 표의 행 수 조정'):
        if not st.checkbox('작성할 표의 행 수 늘리기', key=f'repeat_enabled_{digest}'):
            return path, None, None
        records = inspect_repeat_tables(path)
        available = {item['id']: item for item in records if any(row['editable'] for row in item['rows'])}
        if not available:
            for item in records:
                reasons = list(dict.fromkeys(row['reason'] for row in item['rows']))
                st.caption(item['label'] + ': ' + ' / '.join(reasons))
            raise ValueError('이 양식에서 안전하게 확장할 입력 행을 찾지 못함')
        st.caption('반복할 빈 업무 행을 직접 선택함. 원본은 유지하며 준비본의 서식·병합·행 수를 따로 검사함.')
        identifier = st.selectbox('반복할 표', list(available),
            format_func=lambda key: available[key]['label'] or key, key=f'repeat_table_{digest}')
        record = available[identifier]
        rows = {row['index']: row for row in record['rows'] if row['editable']}
        row = st.selectbox('복제할 원본 행', list(rows),
            format_func=lambda index: f"{index}행 · " + ' / '.join(rows[index]['columns'])[:120],
            key=f'repeat_row_{digest}')
        count = st.number_input('작성할 행 수 (원본 행 포함)', min_value=1, max_value=200, value=1,
                                step=1, key=f'repeat_count_{digest}')
        st.caption('명시적 {{자리표시자}}는 작성할 행마다 필수임. 필요 없는 행은 행 수를 줄임. 빈 셀의 업무상 필수 조건은 검사 규칙에서 지정함.')
        plan = {'table_id': identifier, 'row': row, 'count': count}
        token = sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
        prepared = Path(data_root) / 'prepared_templates' / digest / token / path.name
        expansion = prepare_repeat_template(path, prepared, plan)
        from templates.repeat_fields import repeat_profile
        profile = repeat_profile(prepared, plan, expansion_binding(expansion))
        from templates import load_form_profile
        from agent.template_learning import load_learned_profile
        source_profile = load_learned_profile(path, Path(data_root) / 'template_mappings') or load_form_profile(path)
        if source_profile:
            from templates.repeat_rules import inherit_repeat_rules
            profile = inherit_repeat_rules(path, profile, source_profile, prepared_path=prepared)
        st.caption(f"원본 {record['row_count']}행 → 준비본 {record['row_count'] + count - 1}행 · 구조 검증 통과. 실제 Word/한글/Excel의 페이지 배치와 수식 재계산은 별도 확인 대상임.")
        return prepared, profile, expansion
