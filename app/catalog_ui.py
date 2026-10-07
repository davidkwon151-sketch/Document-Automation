"""Official-source form discovery and revenue-list import for the employee UI."""

from datetime import datetime, timezone
import json
from pathlib import Path

import streamlit as st


def render_work_catalog(data_root, prefix, domain, title):
    from importlib import import_module
    try:
        module = import_module('templates.' + prefix)
    except ImportError:
        return
    workflows = getattr(module, prefix.upper() + '_WORKFLOWS')
    with st.expander(title):
        workflow = st.selectbox(title + ' 업무', list(workflows), format_func=workflows.get, key=prefix + '_catalog_workflow')
        try:
            entries = getattr(module, 'list_' + prefix + '_templates')(workflow)
        except (OSError, ValueError) as exc:
            st.warning(f'공개 양식 목록을 읽지 못했습니다. 원본 양식을 직접 업로드할 수 있습니다: {exc}')
            return
        forms = [entry for entry in entries if entry['resource_kind'] == 'blank_form' and entry['download_status'] == 'verified']
        for entry in entries:
            if entry['resource_kind'] == 'layout_reference':
                st.link_button('참고: ' + entry['title'], entry['source_page'])
        if not forms:
            st.caption('확인된 공개 빈 양식이 없습니다. 원본 양식을 직접 업로드해 사용할 수 있습니다.')
            return
        entry = st.selectbox('작성할 ' + title, forms, format_func=lambda e: e['title'], key=prefix + '_catalog_form')
        st.link_button('공식 원본과 공고 확인', entry['source_page'])
        st.caption(f"{entry['version_label']} · 확인 {entry['checked_at'][:10]}")
        if entry.get('application_status') == 'closed':
            st.caption('신청 기한이 지난 공고의 양식입니다. 새 사업에는 해당 공고의 새 원본을 사용해야 합니다.')
        elif entry.get('application_status') == 'not_assessed':
            st.caption('현재 접수 가능 여부는 공고에서 확인해야 합니다.')
        if st.button(title + ' 가져오기', key='import_' + prefix + '_form'):
            try:
                path = getattr(module, 'download_' + prefix + '_template')(entry, data_root / 'public_templates' / prefix)
                st.session_state['public_template_path'] = str(path)
                st.session_state['public_template_provenance'] = entry
                st.session_state['template_choice'] = '기본 결과보고서 (DOCX + HWPX)'
                st.session_state['work_domain'] = domain
                st.session_state[prefix + '_workflow'] = workflow
                st.session_state['document_kind'] = 'application'
                st.session_state.pop('custom_template', None)
                st.session_state['custom_template_epoch'] = st.session_state.get('custom_template_epoch', 0) + 1
                st.rerun()
            except Exception as exc:
                st.error(f'양식을 가져오지 못했습니다: {exc}')
        st.caption('원본 항목과 등록된 입력 위치를 확인해 작성합니다. 서명·선택사항·첨부자료는 담당자 확인이 필요합니다.')


def render_catalog(data_root):
    from templates.catalog import load_catalog, list_public_templates, download_public_template
    from templates.company_registry import load_registry, import_registry, save_registry, list_companies, registry_summary

    with st.sidebar:
        st.subheader('공개 양식 찾기')
        catalog = load_catalog()
        with st.expander('30대 기업집단 공식 자료'):
            group = st.selectbox('기업집단', [f"{g['rank']}. {g['name']}" for g in catalog['groups']], key='catalog_group')
            selected = catalog['groups'][int(group.split('.')[0]) - 1]
            st.caption(f"2026년 공정위 자산 기준 · 확인 상태: {selected['status']}")
            entries = list_public_templates(selected['name'])
            for entry in entries:
                st.link_button(entry['title'], entry.get('source_page') or entry['source_url'])
            forms = [entry for entry in entries if entry['source_kind'] == 'public_form' and entry['download_status'] == 'verified']
            if forms:
                chosen = st.selectbox('작성할 공개 양식', forms, format_func=lambda e: e['title'], key='enterprise_form')
                if st.button('공개 양식 가져오기', key='import_enterprise_form'):
                    try:
                        path = download_public_template(chosen, data_root / 'public_templates')
                        st.session_state['public_template_path'] = str(path)
                        st.session_state['public_template_provenance'] = chosen
                        st.session_state['template_choice'] = '기본 결과보고서 (DOCX + HWPX)'
                        st.session_state.pop('custom_template', None)
                        st.session_state['custom_template_epoch'] = st.session_state.get('custom_template_epoch', 0) + 1
                        st.rerun()
                    except Exception as exc:
                        st.error(f'원본을 가져오지 못했습니다: {exc}')
            else:
                st.caption('확인된 빈 양식이 없습니다. 해당 회사 양식을 직접 업로드할 수 있습니다.')
            st.caption('완성된 공개 보고서는 배치 참고 자료이며 사내 작성 양식으로 인증된 자료가 아님.')
        try:
            from templates.government import list_government_templates, download_government_template
            government = [entry for entry in list_government_templates() if entry.get('resource_kind') == 'blank_form' and entry.get('download_status') == 'verified']
        except ImportError:
            government = []
        if government:
            with st.expander('관공서 제출 양식'):
                search = st.text_input('기관·양식명 찾기', key='agency_search')
                entries = [entry for entry in government if search.casefold() in json.dumps(entry, ensure_ascii=False).casefold()]
                if entries:
                    entry = st.selectbox('공식 공개 양식', entries, format_func=lambda e: f"{e.get('agency', e.get('owner', '기관'))} · {e['title']}", key='government_form')
                    st.link_button('공식 안내·원본 확인', entry.get('source_page') or entry['source_url'])
                    st.caption(f"현행성: {entry.get('version_status', '확인 필요')}. 제출 직전 기관 안내와 대조해야 함.")
                    if st.button('관공서 양식 가져오기', key='import_government_form'):
                        try:
                            path = download_government_template(entry, data_root / 'public_templates' / 'government')
                            st.session_state['public_template_path'] = str(path)
                            st.session_state['public_template_provenance'] = entry
                            st.session_state['template_choice'] = '기본 결과보고서 (DOCX + HWPX)'
                            st.session_state.pop('custom_template', None)
                            st.session_state['custom_template_epoch'] = st.session_state.get('custom_template_epoch', 0) + 1
                            st.rerun()
                        except Exception as exc:
                            st.error(f'양식을 가져오지 못했습니다: {exc}')
                else:
                    st.caption('찾은 양식이 없습니다. 기관 원본 양식을 직접 업로드해 주세요.')
        try:
            from templates.tech import list_tech_templates, download_tech_template
            tech = list_tech_templates()
        except ImportError:
            tech = []
        if tech:
            with st.expander('판교 IT·벤처·기술 기업 자료'):
                search = st.text_input('기술 기업·지원사업 양식 찾기', key='tech_search')
                entries = [entry for entry in tech if search.casefold() in json.dumps(entry, ensure_ascii=False).casefold()]
                for entry in entries:
                    st.link_button(entry['title'], entry.get('source_page') or entry['source_url'])
                forms = [entry for entry in entries if entry.get('resource_kind') == 'blank_form' and entry.get('download_status') == 'verified']
                if forms:
                    chosen = st.selectbox('작성할 기술기업·사업 제출 양식', forms, format_func=lambda e: e['title'], key='tech_form')
                    st.caption('공개 외부 제출·위임·지원사업 양식임. 비공개 사내 기안 양식은 직접 업로드해 사용할 수 있음.')
                    if st.button('기술기업 양식 가져오기', key='import_tech_form'):
                        try:
                            path = download_tech_template(chosen, data_root / 'public_templates' / 'tech')
                            st.session_state['public_template_path'] = str(path)
                            st.session_state['public_template_provenance'] = chosen
                            st.session_state['template_choice'] = '기본 결과보고서 (DOCX + HWPX)'
                            st.session_state.pop('custom_template', None)
                            st.session_state['custom_template_epoch'] = st.session_state.get('custom_template_epoch', 0) + 1
                            st.rerun()
                        except Exception as exc:
                            st.error(f'양식을 가져오지 못했습니다: {exc}')
                elif entries:
                    st.caption('현재 확인한 자료는 배치 참고 보고서임. 작성용 원본 양식을 직접 업로드할 수 있음.')
        from templates.international import list_international_templates, download_international_template
        with st.expander('글로벌 기업 공식 제출 양식'):
            search = st.text_input('글로벌 기업·양식명 찾기', key='international_search')
            international = [entry for entry in list_international_templates()
                             if search.casefold() in json.dumps(entry, ensure_ascii=False).casefold()]
            if international:
                chosen = st.selectbox('글로벌 기업 공개 양식', international,
                                      format_func=lambda e: f"{e['company']} · {e['title']}", key='international_form')
                st.link_button('기업 공식 안내·원본 확인', chosen['source_page'])
                st.caption('공개 외부 제출 양식임. 사내 기안 양식이나 국내 법인 전체 호환 인증은 아님. 입력 위치를 확인해야 함.')
                if chosen.get('version_label'):
                    st.caption(f"{chosen['version_label']} · 확인 {chosen['checked_at'][:10]}")
                if chosen.get('domain') == 'pharmaceutical_ra':
                    st.caption('RA 업무 양식입니다. 확인된 일부 입력칸의 매핑을 적용하며 전체 의뢰·제출 완료를 뜻하지 않습니다.')
                if st.button('글로벌 기업 양식 가져오기', key='import_international_form'):
                    try:
                        if chosen.get('domain') == 'pharmaceutical_ra':
                            from agent.ra import RA_WORKFLOWS
                            if chosen.get('ra_workflow') not in RA_WORKFLOWS:
                                raise ValueError('공개 RA 양식의 등록 업무를 확인해야 함')
                        path = download_international_template(chosen, data_root / 'public_templates' / 'international')
                        st.session_state['public_template_path'] = str(path)
                        st.session_state['public_template_provenance'] = chosen
                        st.session_state['template_choice'] = '기본 결과보고서 (DOCX + HWPX)'
                        if chosen.get('domain') == 'pharmaceutical_ra':
                            st.session_state['work_domain'] = 'pharmaceutical_ra'
                            st.session_state['ra_workflow'] = chosen['ra_workflow']
                            st.session_state['document_kind'] = 'application'
                        st.session_state.pop('custom_template', None)
                        st.session_state['custom_template_epoch'] = st.session_state.get('custom_template_epoch', 0) + 1
                        st.rerun()
                    except Exception as exc:
                        st.error(f'양식을 가져오지 못했습니다: {exc}')
            else:
                st.caption('찾은 공개 양식이 없습니다. 회사 원본 양식을 직접 업로드할 수 있습니다.')
        from templates.ra import list_ra_templates, download_ra_template, RA_WORKFLOWS
        with st.expander('식약처·의약품 RA 제출 양식'):
            workflow = st.selectbox('RA 공개 양식 업무', list(RA_WORKFLOWS),
                                    format_func=lambda key: RA_WORKFLOWS[key], key='ra_catalog_workflow')
            entries = [entry for entry in list_ra_templates(workflow) if entry['download_status'] == 'verified']
            if entries:
                entry = st.selectbox('공식 RA 양식', entries, format_func=lambda e: e['title'], key='ra_catalog_form')
                st.link_button('식약처·법제처 원본과 안내 확인', entry['source_page'])
                st.caption(f"{entry['version_label']} · 확인 {entry['checked_at'][:10]}. 제출 전 최신 안내·첨부자료를 확인해야 함.")
                if st.button('RA 양식 가져오기', key='import_ra_form'):
                    try:
                        path = download_ra_template(entry, data_root / 'public_templates' / 'ra')
                        st.session_state['public_template_path'] = str(path)
                        st.session_state['public_template_provenance'] = entry
                        st.session_state['template_choice'] = '기본 결과보고서 (DOCX + HWPX)'
                        st.session_state['work_domain'] = 'pharmaceutical_ra'
                        st.session_state['ra_workflow'] = workflow
                        st.session_state['document_kind'] = 'application'
                        st.session_state.pop('custom_template', None)
                        st.session_state['custom_template_epoch'] = st.session_state.get('custom_template_epoch', 0) + 1
                        st.rerun()
                    except Exception as exc:
                        st.error(f'RA 양식을 가져오지 못했습니다: {exc}')
            st.caption('입력 위치를 확인한 후 작성함. 일부 양식은 추가 매핑이 필요하며 서명·선택사항·첨부는 담당자 확인이 필요함.')
        render_work_catalog(data_root, 'business', 'business_support', '무역·해외사업·정부 지원사업 양식')
        render_work_catalog(data_root, 'office', 'office_finance', '기획·금융·일상 업무·산업 품질 양식')
        if st.session_state.get('public_template_path'):
            st.caption(f"가져온 양식: {Path(st.session_state['public_template_path']).name}")
            if st.button('가져온 양식 해제', key='clear_public_form'):
                st.session_state.pop('public_template_path', None)
                st.session_state.pop('public_template_provenance', None)
                st.rerun()
        with st.expander('매출 1000대 기업 범위'):
            registry_path = data_root / 'company_registry.json'
            registry = load_registry(registry_path) if registry_path.is_file() else load_registry()
            summary = registry_summary(registry)
            st.caption(f"확보한 기업 목록: {summary.get('company_count', len(registry['companies']))}개. 기업 목록 확보와 개별 양식 검증은 별개임.")
            st.caption(f"회계연도 {registry['metadata']['fiscal_year']} · {registry['metadata']['accounting_basis']} · {registry['metadata']['coverage']}")
            query = st.text_input('기업명 검색', key='company_search')
            if query:
                st.dataframe([{'순위': c['rank'], '기업': c['name'], '회계연도': c['year']} for c in list_companies(registry, query=query)[:100]], hide_index=True)
            upload = st.file_uploader('출처가 있는 기업 목록 가져오기', type=['json', 'csv'], key='registry_upload')
            metadata = None
            if upload and upload.name.lower().endswith('.csv'):
                st.caption('CSV 열: rank, name, revenue, year, source_url. 같은 회계연도·매출 기준의 목록만 등록함.')
                fiscal_year = st.number_input('매출 회계연도', min_value=1900, max_value=datetime.now().year, value=2025, key='registry_year')
                source_url = st.text_input('목록 원자료 URL', key='registry_source')
                owner = st.text_input('자료 제공 기관', key='registry_owner')
                basis = st.selectbox('연결·별도 매출 기준', ['unknown', 'consolidated', 'separate', 'mixed'], key='registry_basis')
                financial = st.selectbox('금융업 포함 여부', ['unknown', 'included', 'excluded'], key='registry_financial')
                unit = st.selectbox('매출 단위', ['KRW', 'KRW_million', 'KRW_billion'], key='registry_unit')
                definition = st.text_input('순위 산정 대상·기준', placeholder='예: 국내 외감기업 별도 매출 내림차순', key='registry_definition')
                metadata = {'dataset_name': upload.name, 'source_url': source_url, 'source_owner': owner, 'fiscal_year': int(fiscal_year),
                            'accounting_basis': basis, 'financial_sector': financial, 'universe': definition, 'ranking_definition': definition,
                            'revenue_unit': unit, 'verification_status': 'unverified', 'checked_at': datetime.now(timezone.utc).isoformat(),
                            'coverage': 'partial', 'expected_count': 1000}
            if upload and st.button('기업 목록 등록', key='import_registry'):
                try:
                    folder = data_root / 'imports'
                    folder.mkdir(parents=True, exist_ok=True)
                    path = folder / Path(upload.name).name
                    path.write_bytes(upload.getvalue())
                    imported = import_registry(path, metadata=metadata)
                    save_registry(imported, registry_path)
                    st.success(f"{len(imported['companies'])}개 기업 목록을 등록했습니다.")
                except Exception as exc:
                    st.error(f'목록을 확인해 주세요: {exc}')
