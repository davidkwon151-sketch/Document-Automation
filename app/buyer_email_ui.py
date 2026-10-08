"""Overseas-sales inbox to source-backed reply and trade-document handoff."""
from hashlib import sha256
import json

import streamlit as st

from agent.buyer_email import REPLY_LANGUAGES, draft_buyer_reply, parse_buyer_email
from app.multimodal_ui import render_multimodal_upload
from llm.client import LLMClient


def _signature(email, intake, language, notes):
    data = {'email': email, 'intake': intake['signature'], 'language': language, 'notes': notes}
    return sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def link_trade_sources(state, files, documents):
    """Explicitly reuse the same uploaded originals in the trade-form tab."""
    if not files or not documents:
        raise ValueError('연결할 원자료와 요청 문서가 필요함')
    state['gw_prefill_files'] = files
    state['gw_workflow'] = documents[0]
    return documents[0]


def render_buyer_email():
    st.subheader('바이어·파트너 이메일 회신')
    st.write('받은 이메일과 회사 원자료를 넣으면 요청별 근거를 대조해 답변 초안을 만듭니다.')
    st.caption('이 화면은 이메일을 발송하지 않습니다. 수신인·거래조건·첨부할 문서는 발송 전에 담당자가 확인합니다.')
    eml = st.file_uploader('받은 이메일 EML (선택)', type=['eml'], key='sales_eml')
    pasted = st.text_area('받은 이메일 본문', height=180, key='sales_email_text',
                          help='EML을 올리면 이 입력 대신 EML 본문을 사용합니다.')
    language = st.selectbox('회신 언어', list(REPLY_LANGUAGES), format_func=lambda code: REPLY_LANGUAGES[code][0],
                            key='sales_language')
    try:
        email = parse_buyer_email(pasted, eml=eml.getvalue() if eml else None) if eml or pasted.strip() else None
    except ValueError as exc:
        st.error(str(exc))
        email = None
    if email:
        st.caption('받은 이메일 원문 SHA: ' + email['email_sha256'])
        with st.expander('분석할 이메일 원문 확인'):
            st.text(email['body'])
    uploads = st.file_uploader('답변 근거 · 회사 자료와 거래 문서',
                               type=['pdf', 'docx', 'hwpx', 'hwp', 'xlsx', 'txt', 'csv', 'tsv',
                                     'pptx', 'png', 'jpg', 'jpeg'], accept_multiple_files=True, key='sales_sources') or []
    try:
        intake = render_multimodal_upload(uploads, key_prefix='buyer_email',
                                         context={'email_sha256': email['email_sha256'] if email else None})
    except Exception as exc:
        st.session_state.pop('sales_result', None)
        st.error(str(exc) if isinstance(exc, ValueError) else '원자료를 읽지 못했습니다. 파일을 확인해 주세요.')
        return
    notes = st.text_area('담당자 추가 정보 · 선택', key='sales_user_notes',
                         help='회사 원본과 구분해 기록합니다. 확인이 필요한 내용은 단정하지 마세요.')
    current = _signature(email, intake, language, notes)
    if st.session_state.get('sales_result_signature') != current:
        st.session_state.pop('sales_result', None)
        st.session_state['sales_result_signature'] = current
    if not intake['ready']:
        st.warning('사진·스캔·전사문을 원본과 대조해 확인해야 답변 근거로 사용할 수 있습니다.')
    if st.button('요청 분석 · 회신 초안 작성/업데이트', key='sales_draft',
                 disabled=not email or not intake['ready']):
        try:
            with st.spinner('바이어 요청과 회사 원자료를 두 번 대조하고 있습니다.'):
                st.session_state['sales_result'] = draft_buyer_reply(email, intake['sources'],
                                                                      LLMClient(provider='gemini', model='gemini-3.5-flash',
                                                                                timeout=30, max_retries=0),
                                                                      language=language,
                                                                      user_notes=[notes] if notes.strip() else [])
        except Exception as exc:
            st.session_state.pop('sales_result', None)
            st.error(str(exc) if isinstance(exc, ValueError) else '실제 AI 작성을 완료하지 못했습니다. 모델 연결과 API 한도를 확인해 주세요.')
    result = st.session_state.get('sales_result')
    if not result:
        return
    if result['review'].get('coverage_check_required'):
        st.info('확인된 요청 범위의 초안입니다. 발송 전에 이메일 전체 요청을 대조해 주세요.')
    st.write('요청별 답변·근거')
    for item in result['requests']:
        with st.expander(f"{item['index']}. {item['buyer_quote'][:100]} · " +
                         ('원자료 확인' if item['status'] == 'draft' else
                          '일반 회신' if item['status'] == 'general' else '추가 확인 필요'), expanded=True):
            st.text(item['buyer_quote'])
            if item['answer']:
                st.write(item['answer'])
            for proof in item['evidence']:
                st.caption(f"{proof['source_id']} · {proof['filename']} · {proof['location']} · SHA {proof['document_sha256']}")
                st.text(proof['quote'])
    if result['email']:
        st.write('회신 초안 · 발송 전 검토')
        st.code('Subject: ' + result['subject'] + '\n\n' + result['email'], language=None)
        st.download_button('이메일 초안 TXT', 'Subject: ' + result['subject'] + '\n\n' + result['email'],
                           file_name='buyer_reply_draft.txt', mime='text/plain', key='sales_reply_download')
        st.download_button('요청·출처·검수 기록 JSON', json.dumps(result, ensure_ascii=False, indent=2),
                           file_name='buyer_reply_evidence.json', mime='application/json', key='sales_evidence_download')
    if result['documents']:
        names = {'proforma_invoice': '견적송장', 'commercial_invoice': '상업송장', 'packing_list': '포장명세서'}
        st.info('요청 문서: ' + ', '.join(names[name] for name in result['documents']) +
                '. 문서 양식의 수량·단가·통화·결제조건은 원자료와 별도로 확인합니다.')
        if st.button('같은 첨부자료를 글로벌 문서 탭에 연결', key='sales_to_global'):
            chosen = link_trade_sources(st.session_state, uploads, result['documents'])
            st.success(names[chosen] + ' 양식을 선택했습니다. 견적·무역 문서 탭에서 거래번호와 항목 원문을 확인해 주세요.')
