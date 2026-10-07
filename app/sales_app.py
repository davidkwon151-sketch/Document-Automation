"""Standalone overseas sales and business-development workspace."""
import streamlit as st

from app.buyer_email_ui import render_buyer_email
from app.global_workflow_ui import render_global_workflow


def render():
    st.set_page_config(page_title='해외영업·해외사업개발 작업실', layout='wide')
    st.title('문서 표준화 AI AGENT · 해외영업·해외사업개발')
    st.caption('받은 문의와 회사 원자료 → 요청별 답변 → 필요한 견적·무역 문서의 원문 기입과 검수')
    email_tab, document_tab = st.tabs(['바이어·파트너 이메일 회신', '견적·무역 문서'])
    with email_tab:
        render_buyer_email()
    with document_tab:
        render_global_workflow(files=st.session_state.get('gw_prefill_files'))


if __name__ == '__main__':
    render()
