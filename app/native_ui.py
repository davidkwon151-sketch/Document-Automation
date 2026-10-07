"""Download options and local previews for document-program output checks."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import streamlit as st


def native_review_options() -> str:
    enabled = st.checkbox(
        '실제 문서 프로그램 출력 확인', value=True, key='native_review_enabled',
        help='문서 프로그램에서 만든 미리보기로 입력 내용과 페이지 누락을 확인합니다.',
    )
    required = st.checkbox(
        '출력 확인이 완료되어야 다운로드', value=False, key='native_review_required',
        disabled=not enabled,
        help='확인할 수 없는 형식이 하나라도 있으면 모든 다운로드 파일 준비를 보류합니다.',
    )
    return 'off' if not enabled else 'required' if required else 'auto'


def native_download_context(result, paths, profiles, mappings, mode) -> dict:
    """Keep downloaded bytes bound to the current contents and actual templates."""
    templates = {suffix: {'path': str(path), 'sha256': sha256(Path(path).read_bytes()).hexdigest()}
                 for suffix, path in paths.items()}
    contents = {key: result.get(key) for key in (
        'run_id', 'instruction', 'answers', 'draft', 'sources', 'verified_source_ids',
        'brief', 'domain', 'document_kind', 'template_profile', 'template_expansion',
        'locked_fields', 'review', 'grounding', 'completeness', 'semantic_required',
        'completeness_required', 'ra_workflow', 'business_workflow', 'office_workflow',
        'ra_checks', 'business_checks', 'office_checks',
    )}
    contents.update(templates=templates, profiles=profiles, mappings=mappings, native_review=mode)
    return {'fingerprint': sha256(json.dumps(contents, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            'template_sha256': {suffix: entry['sha256'] for suffix, entry in templates.items()}}


def clear_native_outputs(result, state=None):
    state = st.session_state if state is None else state
    state.pop('exports', None)
    for key in ('output_verification', 'output_hashes', 'native_output_verification',
                '_native_preview_bytes', 'state'):
        result.pop(key, None)


def sync_native_outputs(result, context, *, unsaved=False, blocked=False, state=None):
    """Invalidate previews together with exports, including unsaved or reverted edits."""
    state = st.session_state if state is None else state
    previous = state.get('native_download_context')
    exports = state.get('exports', {})
    reports = result.get('native_output_verification', {})
    current_sources = context.get('template_sha256') if isinstance(context, dict) else None
    legacy_context = not isinstance(current_sources, dict) or not current_sources or not isinstance(context.get('fingerprint'), str)
    hash_mismatch = any(
        sha256(data).hexdigest() != result.get('output_hashes', {}).get(suffix)
        or (reports.get(suffix, {}).get('output_sha256') is not None
            and sha256(data).hexdigest() != reports[suffix]['output_sha256'])
        for suffix, data in exports.items()
    )
    hash_mismatch |= any(report.get('output_sha256') != expected
                         for suffix, expected in result.get('output_hashes', {}).items()
                         if (report := reports.get(suffix)) is not None)
    # Failed attempts have no published files or preview. Keep their diagnostic
    # old/current SHA evidence; it can explain the source change being rejected.
    published = bool(exports or result.get('output_hashes') or result.get('_native_preview_bytes'))
    source_mismatch = not legacy_context and any(
        suffix not in current_sources or report.get('source_sha256') != current_sources[suffix]
        for suffix, report in reports.items() if published or report.get('status') != 'failed'
    )
    invalid = unsaved or blocked or previous != context or hash_mismatch or legacy_context or source_mismatch
    if invalid:
        clear_native_outputs(result, state)
    state['native_download_context'] = context
    return invalid


def downloadable_native_exports(result, exports, mode):
    reports = result.get('native_output_verification', {})
    if any(report.get('blocking') or report.get('status') == 'failed' for report in reports.values()):
        return {}
    if mode == 'required' and any(reports.get(suffix, {}).get('status') != 'passed' for suffix in exports):
        return {}
    return exports


def _preview_details(data, report):
    from pypdf import PdfReader

    if not isinstance(data, bytes) or sha256(data).hexdigest() != report.get('pdf_sha256'):
        raise ValueError('미리보기 확인 기록이 파일과 다릅니다. 다운로드 파일을 다시 준비해 주세요.')
    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted:
        raise ValueError('이 미리보기는 표시할 수 없습니다. 다운로드 파일을 다시 준비해 주세요.')
    pages = len(reader.pages)
    if type(report.get('page_count')) is not int or not pages or pages != report['page_count']:
        raise ValueError('미리보기 쪽수가 확인 기록과 다릅니다. 다운로드 파일을 다시 준비해 주세요.')
    return pages


def render_native_results(result):
    reports = result.get('native_output_verification', {})
    if not reports:
        return
    names = {'word': 'Word', 'excel': 'Excel', 'powerpoint': 'PowerPoint',
             'libreoffice': 'LibreOffice', 'hancom': '한글', 'hwp': '한글',
             'microsoft_word': 'Word', 'microsoft_excel': 'Excel', 'microsoft_powerpoint': 'PowerPoint'}
    labels = {'passed': '출력 확인 통과', 'warning': '확인이 필요한 항목이 있음',
              'unavailable': '현재 환경에서 출력 확인 미평가', 'failed': '출력 확인 실패'}
    st.subheader('문서 프로그램 출력 확인')
    for suffix, report in reports.items():
        status = report.get('status')
        notice = {'passed': st.success, 'warning': st.warning, 'unavailable': st.warning,
                  'failed': st.error}.get(status, st.info)
        notice(f"{suffix.upper()} · {labels.get(status, '출력 확인 기록을 확인해 주세요')}")
        engine = report.get('engine')
        if isinstance(engine, str) and engine:
            st.caption(f"확인 프로그램: {names.get(engine.lower(), engine)}")
        if type(report.get('page_count')) is int and report['page_count'] > 0:
            st.caption(f"미리보기 전체 {report['page_count']}쪽")
        for issue in report.get('issues', []):
            message = issue.get('message') if isinstance(issue, dict) else issue
            if isinstance(message, str) and message:
                (st.error if isinstance(issue, dict) and issue.get('severity') == 'error' else st.warning)(message)
        data = result.get('_native_preview_bytes', {}).get(suffix)
        if data is None:
            st.caption('미리보기가 없습니다. 현재 내용으로 다운로드 파일을 다시 준비해 주세요.')
            continue
        try:
            count = _preview_details(data, report)
            with st.expander(f'{suffix.upper()} 출력 미리보기'):
                st.caption('기본 1쪽을 보여줍니다. 쪽 번호를 바꿔 전체 내용을 확인할 수 있습니다. '
                           '표가 빠졌다면 원본의 인쇄 영역을 확인해 주세요.')
                page = st.number_input('미리보기 쪽', min_value=1, max_value=count, value=1, step=1,
                                       key=f"native_page_{suffix}_{report['pdf_sha256'][:12]}")
                # PDFium stays sequential: only the requested page is rendered.
                from parsers.extended import render_pdf_page
                with TemporaryDirectory(prefix='report-preview-') as directory:
                    path = Path(directory) / 'preview.pdf'
                    path.write_bytes(data)
                    image = render_pdf_page(path, page)
                st.image(image, caption=f'{suffix.upper()} 출력 {page}/{count}쪽', use_container_width=True)
                st.download_button(f'{suffix.upper()} 출력 미리보기 PDF 다운로드', data=data,
                                   file_name=f'보고서_{suffix}_미리보기.pdf', mime='application/pdf',
                                   key=f'native_preview_download_{suffix}')
        except Exception:
            st.warning('출력 미리보기를 표시하지 못했습니다. 다운로드 파일을 다시 준비해 주세요.')
