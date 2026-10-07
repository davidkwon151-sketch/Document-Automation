"""Confirmed comparison -> three fixed official change rows -> checked package."""
from hashlib import sha256
import json

import streamlit as st

from agent.ra_change_compare import DEFAULT_LABELS, compare_ra_changes, comparison_csv
from agent.ra_change_document import prepare_ra_change_document, export_ra_change_document


def _signature(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _clear():
    for key in ('rcd_prepared', 'rcd_prepared_input', 'rcd_export'):
        st.session_state.pop(key, None)


def _sources(side, injected, context):
    if injected is not None:
        return {'sources': injected, 'ready': True, 'changed': False}
    from app.multimodal_ui import render_multimodal_upload
    return render_multimodal_upload(key_prefix='rcd_' + side, context={**context, 'purpose': side})


def render_change_document(before_sources=None, after_sources=None, reason_sources=None):
    """Injected sources are for offline UI tests; normal UI uses confirmed intake."""
    from app.ra_workflow_ui import load_workflows, resolve_workflow

    st.subheader('변경 전·후 자료로 공식 변경 신청서 기입')
    st.caption('명시 원문 차이와 담당자가 제공한 사유를 고정 3행에 연결합니다. 임의 승인·변경 사유·서명은 생성하지 않습니다.')
    try:
        entry = next(row for row in load_workflows() if row['id'] == 'variation')
        _, profile = resolve_workflow(entry)
        st.info('선정 입력칸 18개와 원본 고정 변경사항 3행을 지원합니다. 미등록칸·선택·서명·첨부·최신 접수 요건은 별도로 확인해야 합니다.')
        st.caption('원본: ' + entry['title'] + ' · 서식 개정일 ' + str(entry.get('form_printed_revision_date', '미확인')))
        if entry.get('source_url'):
            st.link_button('공식 원본 출처', entry['source_url'])
        product = st.text_input('원문 제품명', key='rcd_product')
        variant = st.text_input('원문 제형·함량', key='rcd_variant')
        context = {'product': product, 'variant': variant, 'form_sha256': entry['source_sha256']}
        st.write('1. 변경 전 원자료')
        before = _sources('before', before_sources, context)
        st.write('2. 변경 후 원자료')
        after = _sources('after', after_sources, context)
        with st.expander('원자료 작성 예시 · 실제 자료의 원문만 사용'):
            st.code('제품명: <원문의 정확한 제품명>\n제형·함량: <원문의 정확한 제형·함량>\n주소: <원문 주소>\n포장: <원문 포장>', language=None)
            st.caption('사진·스캔 원자료의 전사문은 원본 전체 대조 확인 후 사용합니다. 추출 제한 문서를 우회하지 않습니다.')
        labels_text = st.text_area('비교 항목명 (원문과 같은 이름, 한 줄에 하나)', '\n'.join(DEFAULT_LABELS), key='rcd_labels')
        labels = [line.strip() for line in labels_text.splitlines() if line.strip()]
        comparison_input = _signature({'entry': entry, 'profile': profile, 'context': context, 'labels': labels,
                                       'before': before['sources'], 'after': after['sources'],
                                       'before_ready': before['ready'], 'after_ready': after['ready']})
        if st.session_state.get('rcd_comparison_input') != comparison_input:
            st.session_state.pop('rcd_comparison', None)
            _clear()
        ready = before['ready'] and after['ready'] and bool(before['sources'] and after['sources'] and product.strip() and variant.strip())
        if not before['ready'] or not after['ready']:
            st.warning('보류 첨부 또는 미확인 전사문을 해결해야 변경 문서를 작성할 수 있습니다.')
        if st.button('3. 원문 변경 비교', key='rcd_compare', disabled=not ready):
            st.session_state.pop('rcd_comparison', None)
            _clear()
            comparison = compare_ra_changes(before['sources'], after['sources'], labels,
                                            product_name=product, variant=variant)
            st.session_state['rcd_comparison'] = comparison
            st.session_state['rcd_comparison_input'] = comparison_input
        comparison = st.session_state.get('rcd_comparison')
        if not comparison:
            return
        st.dataframe([{'항목': row['label'], '변경 전': row['before'], '변경 후': row['after'], '구분': row['change_kind']}
                      for row in comparison['rows']], hide_index=True)
        for issue in comparison['issues']:
            st.warning(issue['label'] + ': ' + '; '.join(issue['notes']))
        candidates = [row['label'] for row in comparison['rows'] if row['change_kind'] == '차이']
        selected = st.multiselect('4. 담당자가 변경 신청에 사용할 항목 (최대 3개, 선택 순서대로 기입)', candidates,
                                  max_selections=3, key='rcd_selected_' + comparison_input[:16])
        st.caption('동일·모호·자료없음 항목은 자동 기입하지 않습니다. 4개 이상은 행을 잘라내지 않으며 별도 양식/업무로 처리해야 합니다.')
        reason_mode = st.radio('변경 사유 제공 방법', ['담당자가 직접 작성', '사유 원자료의 정확한 인용'],
                               key='rcd_reason_mode_' + comparison_input[:16])
        source_reasons, direct_reasons, direct_values = {}, {}, {}
        reason = {'sources': [], 'ready': True, 'changed': False}
        if reason_mode == '사유 원자료의 정확한 인용':
            st.caption('사유 원자료에도 제품명과 제형·함량을 명시해야 합니다. 임의 추론이나 문구 생성 없이 전체 원문을 사용합니다.')
            reason = _sources('reason', reason_sources, context)
        for index, label in enumerate(selected, 1):
            token = _signature((comparison_input, label, reason_mode, reason['sources']))[:16]
            st.write(f'{index}행 · {label}')
            if reason_mode == '담당자가 직접 작성':
                direct_reasons[label] = st.text_area(label + '의 실제 변경 사유 (담당자 직접 입력)', key='rcd_reason_' + token)
            else:
                source_map = {item['source_id']: item for item in reason['sources']}
                identifier = st.selectbox(label + ' 사유 출처', ['', *source_map],
                                          format_func=lambda value: '선택하세요' if not value else source_map[value]['filename'] + ' / ' + value,
                                          key='rcd_reason_id_' + token)
                if identifier:
                    source = source_map[identifier]
                    st.code(source['text'], language=None)
                    quote = st.text_area(label + ' 사유의 정확한 전체 인용', key='rcd_reason_quote_' + token + identifier)
                    if quote:
                        binding = {'source_id': identifier, 'quote': quote}
                        if source['text'].count(quote) > 1:
                            binding['start'] = st.number_input('인용 시작 문자 위치 (0부터)', min_value=0,
                                                               max_value=len(source['text']), key='rcd_reason_start_' + token + identifier)
                        source_reasons[label] = binding
        with st.expander('신청인·제조소·품목허가번호 직접 입력 (선택 사항)'):
            st.caption('본인이 확인한 값만 입력합니다. 개인·허가 번호를 AI가 추정하지 않으며 빈 값은 그대로 남습니다.')
            for field in profile['fields']:
                if field.get('input_required'):
                    key = field['value_key']
                    direct_values[key] = st.text_input(field['label'], key='rcd_direct_' + _signature((comparison_input, key))[:16])
        current = _signature({'comparison_input': comparison_input, 'selected': selected, 'reason_mode': reason_mode,
                              'source_reasons': source_reasons, 'reason_sources': reason['sources'],
                              'reason_ready': reason['ready'], 'direct_reasons': direct_reasons, 'direct_values': direct_values})
        confirmed = []
        for label in selected:
            if st.checkbox(label + ': 변경 전후 제품·함량·버전·조건과 제공 사유를 확인함',
                           key='rcd_confirm_' + _signature((current, label))[:16]):
                confirmed.append(label)
        input_signature = _signature((current, confirmed))
        if st.session_state.get('rcd_prepared_input') != input_signature:
            _clear()
        if st.button('5. 확인한 차이·사유를 공식 3행에 연결하고 검수', key='rcd_prepare',
                     disabled=not ready or not reason['ready'] or not selected or len(confirmed) != len(selected)):
            _clear()
            result = prepare_ra_change_document(profile, before['sources'], after['sources'], product_name=product, variant=variant,
                selected_labels=selected, confirmed_labels=confirmed, comparison_labels=labels,
                reason_sources=reason['sources'], source_reasons=source_reasons,
                direct_reasons=direct_reasons, direct_values=direct_values)
            st.session_state['rcd_prepared'] = result
            st.session_state['rcd_prepared_input'] = input_signature
        result = st.session_state.get('rcd_prepared')
        if not result:
            return
        prepared = result['prepared']
        st.json(prepared['draft'])
        for issue in prepared['review']['issues']:
            st.warning(str(issue.get('field', '')) + ': ' + issue['message'])
        for question in prepared['questions'][:2]:
            st.warning(question)
        st.caption(f"현재 준비: {result['row_count']}/3행 · 전체 등록 {len(profile['fields'])}칸 · 문서 작성 모델 호출 0회 (원자료 이미지 읽기는 별도) · 법정 제출 완료는 별도 확인")
        output_confirmed = st.checkbox('위 항목과 사유가 실제 원문/직접 입력과 같음을 확인하고 원본 위치에 기입함',
                                       key='rcd_output_confirm_' + result['fingerprint'][:16])
        if not output_confirmed:
            st.session_state.pop('rcd_export', None)
        if st.button('6. 공식 PDF 기입·저장 후 독립 검수·작업 패키지', key='rcd_export_button',
                     disabled=not output_confirmed or not prepared['ready_for_output_check'] or not reason['ready']):
            st.session_state.pop('rcd_export', None)
            with st.spinner('원본 SHA, 변경 근거와 입력 위치를 다시 대조하고 있습니다.'):
                st.session_state['rcd_export'] = export_ra_change_document(entry, result)
        export = st.session_state.get('rcd_export')
        if export:
            st.success('원본 위치·서식 보존 및 저장 후 값 검수를 통과했습니다. 미기입칸·서명·첨부·법정 적용 여부는 제출 전에 확인하세요.')
            st.download_button('변경 신청서 PDF 다운로드', export['document'], file_name=export['filename'], mime='application/pdf', key='rcd_pdf')
            st.download_button('변경 비교·전체 출처·검수 JSON', export['evidence'], file_name='RA_변경문서_근거.json', mime='application/json', key='rcd_evidence')
            st.download_button('작업 패키지 ZIP 다운로드', export['workpack']['zip_bytes'], file_name=export['workpack']['filename'], mime='application/zip', key='rcd_zip')
            st.download_button('원문 비교 CSV 다운로드', comparison_csv(result['comparison']), file_name='RA_변경비교.csv', mime='text/csv', key='rcd_csv')
            summary = export['workpack']['summary']
            st.caption(f"등록 {summary['registered_fields']}칸 중 {summary['filled_fields']}칸 기입 · 미기입 {summary['unfilled_fields']}칸 · 미확인 첨부 안내 {summary['attachments_pending']}개 · 사람 수정률 미측정")
    except Exception as exc:
        _clear()
        st.session_state.pop('rcd_comparison', None)
        st.error(str(exc) if isinstance(exc, ValueError) else '변경 문서를 준비하지 못했습니다. 원자료·제품 범위·사유·양식을 확인해 주세요.')
