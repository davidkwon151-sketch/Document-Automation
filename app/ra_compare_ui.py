"""Small RA change-comparison workbench, embeddable without page configuration."""
from hashlib import sha256
import json

import streamlit as st
from agent.ra_change_compare import DEFAULT_LABELS, compare_ra_changes, comparison_csv


def render_change_compare(before_sources=None, after_sources=None, parser=None):
    st.subheader('변경 전·후 원자료 대비표')
    st.caption('실제 AI 추론을 호출하지 않고 명시 항목과 정확한 원문을 비교합니다. 변경 승인·법정 제출용 양식이 아닙니다.')
    st.info('제품명과 제형·함량이 원자료에 명시되어야 합니다. 다른 제품·함량·조건, 여러 후보는 자동 합치지 않습니다.')
    product = st.text_input('원문 제품명', key='rc_product')
    variant = st.text_input('원문 제형·함량 (예: 정제 10 mg)', key='rc_variant')
    labels_text = st.text_area('비교 항목명 (원문과 같은 이름, 한 줄에 하나)', '\n'.join(DEFAULT_LABELS), key='rc_labels')
    labels = [line.strip() for line in labels_text.splitlines() if line.strip()]
    try:
        if parser is None:
            from app.ra_workflow_ui import parse_uploads
            parser = parse_uploads
        sources = {}
        for side, injected in [('before', before_sources), ('after', after_sources)]:
            title = '변경 전' if side == 'before' else '변경 후'
            if injected is None:
                files = st.file_uploader(title + ' 원자료', type=['pdf', 'docx', 'hwpx', 'hwp', 'xlsx', 'txt', 'csv', 'tsv'],
                                         accept_multiple_files=True, key='rc_files_' + side) or []
                signature = sha256(json.dumps([(file.name, sha256(file.getvalue()).hexdigest()) for file in files]).encode()).hexdigest()
                if st.session_state.get('rc_upload_' + side) != signature:
                    st.session_state['rc_sources_' + side] = parser(files) if files else []
                    st.session_state['rc_upload_' + side] = signature
                sources[side] = st.session_state.get('rc_sources_' + side, [])
            else:
                sources[side] = injected
            st.caption(f'{title}: 원자료 조각 {len(sources[side])}개')
        selections, editors = {}, []
        pending_confirmation = False
        with st.expander('모호한 항목을 정확한 원문 인용으로 지정 (선택 사항)'):
            st.caption('기본은 동일 항목명: 값의 자동 비교입니다. 인용 지정은 전체 원문에서 정확히 선택하고 항목 의미를 직접 확인해야 합니다.')
            for label in labels[:100]:
                token = sha256(label.encode()).hexdigest()[:12]
                for side in ('before', 'after'):
                    by_id = {source['source_id']: source for source in sources[side]}
                    identity = sha256(json.dumps({'sources': sources[side], 'product': product,
                                                  'variant': variant, 'labels': labels, 'side': side},
                                                 ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
                    identifier = st.selectbox(label + (' · 변경 전' if side == 'before' else ' · 변경 후'), ['', *by_id],
                                             format_func=lambda value, records=by_id: '명시 항목 자동 비교' if not value else records[value]['filename'] + ' / ' + value,
                                             key=f'rc_select_{side}_{token}_{identity}')
                    if not identifier:
                        continue
                    source = by_id[identifier]
                    st.code(source['text'], language=None)
                    quote = st.text_area('기입할 정확한 전체 인용', source['text'], key=f'rc_quote_{side}_{token}_{identity}_{identifier}')
                    binding = {'source_id': identifier, 'quote': quote}
                    if quote and source['text'].count(quote) > 1:
                        binding['start'] = st.number_input('인용 시작 문자 위치 (0부터)', min_value=0, max_value=len(source['text']),
                                                           key=f'rc_start_{side}_{token}_{identity}_{identifier}')
                    confirmation_key = sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
                    confirmed = st.checkbox('이 인용이 해당 항목의 전체 내용임을 확인함', key=f'rc_confirm_{side}_{token}_{identity}_{confirmation_key}')
                    editors.append({'side': side, 'label': label, 'binding': binding, 'confirmed': confirmed})
                    pending_confirmation = pending_confirmation or not confirmed
                    if confirmed:
                        selections.setdefault(label, {})[side] = binding
        current = sha256(json.dumps({'sources': sources, 'product': product, 'variant': variant,
                                    'labels': labels, 'selections': selections, 'editors': editors}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if st.session_state.get('rc_result_input') != current:
            st.session_state.pop('rc_result', None)
            st.session_state.pop('rc_result_input', None)
        if pending_confirmation:
            st.warning('직접 지정한 인용의 항목·전체 범위를 확인해야 비교할 수 있습니다.')
        if st.button('원문 변경 비교', key='rc_compare', disabled=pending_confirmation or not bool(sources['before'] and sources['after'] and product.strip() and variant.strip())):
            st.session_state.pop('rc_result', None)
            st.session_state.pop('rc_result_input', None)
            result = compare_ra_changes(sources['before'], sources['after'], labels,
                                        product_name=product, variant=variant, selections=selections)
            st.session_state['rc_result'] = result
            st.session_state['rc_result_input'] = current
        result = st.session_state.get('rc_result')
        if result:
            st.dataframe([{'항목': row['label'], '변경 전': row['before'], '변경 후': row['after'],
                           '구분': row['change_kind'], '확인 필요': '; '.join(row['confirmation_notes'])} for row in result['rows']], hide_index=True)
            for issue in result['issues']:
                st.warning(issue['label'] + ': ' + issue['kind'] + ' — ' + '; '.join(issue['notes']))
            st.caption('동일/차이는 원문 문자 비교입니다. 모든 항목은 담당자 확인이 필요하며, 모호·자료없음 항목은 그대로 표시됩니다. JSON에 원문·출처·범위·SHA를 보관합니다.')
            st.caption('CSV의 수식 시작 문자는 실행 방지를 위해 앞에 작은따옴표를 붙입니다. 정확한 원문은 JSON에서 확인하세요.')
            st.download_button('비교·확인대기 CSV 다운로드', comparison_csv(result), file_name='RA_변경비교_확인대기.csv', mime='text/csv', key='rc_csv')
            st.download_button('전체 원문·출처 JSON 다운로드', json.dumps(result, ensure_ascii=False, indent=2).encode(),
                               file_name='RA_변경비교_근거.json', mime='application/json', key='rc_json')
    except Exception as exc:
        st.session_state.pop('rc_result', None)
        st.session_state.pop('rc_result_input', None)
        st.error(str(exc) if isinstance(exc, ValueError) else '원자료 비교를 완료하지 못했습니다. 첨부 파일과 정확한 항목·인용을 확인해 주세요.')
