from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path

from PIL import Image
from pypdf import PdfWriter
import pytest
from streamlit.testing.v1 import AppTest

from app.native_ui import (downloadable_native_exports, native_download_context,
                           sync_native_outputs)
from agent.pipeline import build_downloads as production_build_downloads


ROOT = Path(__file__).resolve().parents[1]


def pdf_bytes(pages=2):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def download_labels(app):
    return [element.proto.label for element in app.get('download_button')]


def test_native_options_defaults_and_modes():
    app = AppTest.from_string('from app.native_ui import native_review_options\nimport streamlit as st\nst.write(native_review_options())').run()
    assert not app.exception
    assert app.checkbox(key='native_review_enabled').value is True
    assert app.checkbox(key='native_review_required').value is False
    assert app.markdown[-1].value == 'auto'
    app.checkbox(key='native_review_required').check().run()
    assert app.markdown[-1].value == 'required'
    app.checkbox(key='native_review_enabled').uncheck().run()
    assert app.markdown[-1].value == 'off'
    assert app.checkbox(key='native_review_required').disabled


def test_native_preview_all_pages_and_distinct_download_target(monkeypatch):
    import parsers.extended as parser

    calls = []
    image = BytesIO()
    Image.new('RGB', (4, 4), 'white').save(image, format='PNG')

    def render(path, page):
        calls.append((Path(path).is_file(), page))
        return image.getvalue()

    monkeypatch.setattr(parser, 'render_pdf_page', render)
    data = pdf_bytes(203)
    report = {'status': 'passed', 'engine': 'word', 'page_count': 203,
              'pdf_sha256': sha256(data).hexdigest(), 'issues': []}
    app = AppTest.from_string('import streamlit as st\nfrom app.native_ui import render_native_results\nrender_native_results(st.session_state["result"])')
    app.session_state['result'] = {'native_output_verification': {'docx': report},
                                   '_native_preview_bytes': {'docx': data}}
    app.run()
    assert not app.exception
    assert calls == [(True, 1)]
    assert 'DOCX 출력 미리보기 PDF 다운로드' in download_labels(app)
    assert any(element.proto.id.endswith('-native_preview_download_docx') for element in app.get('download_button'))
    app.number_input[0].set_value(203).run()
    assert not app.exception
    assert calls == [(True, 1), (True, 203)]


@pytest.mark.parametrize('change', ['bytes', 'hash', 'page_count'])
def test_inconsistent_preview_is_not_downloadable(change, monkeypatch):
    import parsers.extended as parser

    monkeypatch.setattr(parser, 'render_pdf_page', lambda *args: pytest.fail('invalid preview must not render'))
    data = pdf_bytes()
    report = {'status': 'passed', 'page_count': 2, 'pdf_sha256': sha256(data).hexdigest(), 'issues': []}
    if change == 'bytes':
        data = pdf_bytes(3)
    elif change == 'hash':
        report['pdf_sha256'] = '0' * 64
    else:
        report['page_count'] = 1
    app = AppTest.from_string('import streamlit as st\nfrom app.native_ui import render_native_results\nrender_native_results(st.session_state["result"])')
    app.session_state['result'] = {'native_output_verification': {'docx': report}, '_native_preview_bytes': {'docx': data}}
    app.run()
    assert not app.exception
    assert app.warning
    assert not download_labels(app)


@pytest.mark.parametrize('status', ['passed', 'warning', 'unavailable', 'failed'])
def test_native_status_and_concrete_issues_are_shown_without_false_success(status):
    app = AppTest.from_string('import streamlit as st\nfrom app.native_ui import render_native_results\nrender_native_results(st.session_state["result"])')
    app.session_state['result'] = {'native_output_verification': {'xlsx': {
        'status': status, 'engine': 'excel', 'page_count': 2,
        'issues': [{'code': 'print_area', 'message': '인쇄 영역에 표의 마지막 행이 포함되지 않았습니다.', 'severity': 'warning'}],
    }}}
    app.run()
    assert not app.exception
    assert bool(app.success) == (status == 'passed')
    assert any('마지막 행' in warning.value for warning in app.warning)
    assert any('Excel' in item.value for item in app.caption)
    assert any('다시 준비' in item.value for item in app.caption)


def test_strict_mode_requires_every_requested_format_and_auto_failure_blocks_all():
    exports = {'docx': b'word', 'hwpx': b'hangul'}
    result = {'native_output_verification': {'docx': {'status': 'passed'}, 'hwpx': {'status': 'unavailable'}}}
    assert downloadable_native_exports(result, exports, 'auto') == exports
    assert downloadable_native_exports(result, exports, 'required') == {}
    result['native_output_verification']['hwpx'] = {'status': 'failed', 'blocking': True}
    assert downloadable_native_exports(result, exports, 'auto') == {}


def test_saved_native_proof_must_match_current_output_hash_even_without_preview(tmp_path):
    template = tmp_path / 'form.docx'
    template.write_bytes(b'current-template')
    result = {'output_hashes': {'docx': 'a' * 64},
              'native_output_verification': {'docx': {'status': 'passed', 'source_sha256': sha256(template.read_bytes()).hexdigest(), 'output_sha256': 'b' * 64}}}
    context = native_download_context(result, {'docx': template}, {}, {}, 'auto')
    state = {'native_download_context': context}
    assert sync_native_outputs(result, context, state=state)
    assert 'native_output_verification' not in result


def test_current_failed_attempt_retains_diagnostics_without_published_output(tmp_path):
    template = tmp_path / 'form.docx'
    template.write_bytes(b'changed-template')
    result = {'native_output_verification': {'docx': {'status': 'failed', 'blocking': True,
                'source_sha256': sha256(b'previous-template').hexdigest(),
                'issues': [{'message': '입력한 마지막 문단이 출력에 없습니다.'}]}}}
    context = native_download_context(result, {'docx': template}, {}, {}, 'auto')
    state = {'native_download_context': context}
    assert not sync_native_outputs(result, context, state=state)
    assert result['native_output_verification']['docx']['status'] == 'failed'


@pytest.mark.parametrize('change', ['draft', 'source', 'mapping', 'template', 'mode', 'unsaved', 'blocked', 'output_bytes',
                                  'instruction', 'answers', 'completeness', 'grounding', 'ra_workflow', 'ra_checks',
                                  'business_workflow', 'business_checks', 'office_workflow', 'office_checks', 'verified_source_ids'])
def test_native_exports_and_proof_expire_together(change, tmp_path):
    template = tmp_path / 'form.docx'
    template.write_bytes(b'original')
    result = {'run_id': 'one', 'draft': {'본문': '원문'}, 'sources': [{'text': '근거'}],
              'native_output_verification': {'docx': {'status': 'passed', 'output_sha256': sha256(b'output').hexdigest()}},
              '_native_preview_bytes': {'docx': b'preview'}, 'output_hashes': {'docx': sha256(b'output').hexdigest()},
              'output_verification': {'docx': {'status': 'passed'}}}
    paths, mappings, mode = {'docx': template}, {'docx': {'cell': '본문'}}, 'auto'
    context = native_download_context(result, paths, {}, mappings, mode)
    result['native_output_verification']['docx']['source_sha256'] = context['template_sha256']['docx']
    state = {'native_download_context': context, 'exports': {'docx': b'output'}}
    if change == 'draft':
        result['draft']['본문'] = '새 내용'
    elif change == 'source':
        result['sources'][0]['text'] = '새 근거'
    elif change == 'mapping':
        mappings['docx']['cell'] = '제목'
    elif change == 'template':
        template.write_bytes(b'replaced')
    elif change == 'mode':
        mode = 'required'
    elif change == 'output_bytes':
        state['exports']['docx'] = b'tampered'
    elif change not in {'unsaved', 'blocked'}:
        result[change] = {'changed': True}
    updated = native_download_context(result, paths, {}, mappings, mode)
    assert sync_native_outputs(result, updated, state=state, unsaved=change == 'unsaved', blocked=change == 'blocked')
    assert 'exports' not in state
    assert 'native_output_verification' not in result
    assert '_native_preview_bytes' not in result
    assert 'output_verification' not in result
    assert 'output_hashes' not in result


@pytest.fixture
def native_app(monkeypatch, tmp_path):
    import agent.pipeline as pipeline
    import parsers.extended as parser
    from evals.run import MockEvaluationClient

    brief = {'목적': '매출', '보고 대상': '팀장', '보고서 유형': '결과보고서', '마감': '', '분량': '1쪽', '부족한 정보': [], '질문': []}
    text = '매출 120만원을 달성함'
    documents = [{'파일명': '자료.xlsx', '본문': text, '표 목록': [],
                  '페이지/시트 정보': [{'본문': text, '표 목록': [], '페이지': None, '시트': '실적', '위치': '실적!A1'}]}]
    ready = pipeline.run_pipeline('매출 결과보고서', documents=documents, client=MockEvaluationClient({'mock_brief': brief}))
    monkeypatch.setattr(pipeline, 'run_pipeline', lambda *args, **kwargs: deepcopy(ready))
    calls = []
    preview = pdf_bytes()
    image = BytesIO()
    Image.new('RGB', (4, 4), 'white').save(image, format='PNG')
    monkeypatch.setattr(parser, 'render_pdf_page', lambda *args: image.getvalue())

    def build(result, *, native_review, **kwargs):
        calls.append(native_review)
        exports = {'docx': b'word-output', 'hwpx': b'hangul-output'}
        result['output_verification'] = {suffix: {'status': 'passed'} for suffix in exports}
        if native_review != 'off':
            result['native_output_verification'] = {
                'docx': {'status': 'passed', 'engine': 'word', 'source_sha256': sha256((ROOT / 'templates/result_report.docx').read_bytes()).hexdigest(), 'output_sha256': sha256(exports['docx']).hexdigest(),
                         'page_count': 2, 'pdf_sha256': sha256(preview).hexdigest(), 'issues': []},
                'hwpx': {'status': 'unavailable', 'engine': 'hancom', 'source_sha256': sha256((ROOT / 'templates/result_report.hwpx').read_bytes()).hexdigest(), 'output_sha256': sha256(exports['hwpx']).hexdigest(),
                         'issues': [{'message': '한글 출력 확인을 현재 환경에서 실행할 수 없습니다.', 'severity': 'warning'}]},
            }
        if native_review == 'required':
            result.pop('output_verification', None)
            raise ValueError('모든 형식의 출력 확인이 필요합니다.')
        if native_review != 'off':
            result['_native_preview_bytes'] = {'docx': preview}
        return exports

    monkeypatch.setattr(pipeline, 'build_downloads', build)
    monkeypatch.setenv('REPORT_AGENT_DATA_DIR', str(tmp_path))
    app = AppTest.from_file(str(ROOT / 'app/ui.py'), default_timeout=15).run()
    app.text_area(key='instruction').set_value('팀장에게 매출 결과보고서를 작성해줘')
    app.button(key='generate').click().run()
    assert not app.exception
    return app, calls


def test_native_ui_confirmation_download_and_reverted_edit_keep_no_stale_proof(native_app):
    app, calls = native_app
    assert app.button(key='prepare').disabled
    assert not download_labels(app)
    baseline = deepcopy(app.session_state['result']['metrics']['baseline_draft'])
    app.checkbox(key='confirmed').check().run()
    app.button(key='prepare').click().run()
    assert not app.exception
    assert calls == ['auto']
    assert {'DOCX 다운로드', 'HWPX 다운로드'} <= set(download_labels(app))
    assert 'DOCX 출력 미리보기 PDF 다운로드' in download_labels(app)
    assert app.session_state['result']['metrics']['baseline_draft'] == baseline
    folder = Path(os.environ['REPORT_AGENT_DATA_DIR']) / 'runs' / app.session_state['result']['run_id']
    pointer = (folder / 'latest_record_id').read_text(encoding='utf-8')
    saved = json.loads((folder / 'records' / f'{pointer}.json').read_text(encoding='utf-8'))
    assert '_native_preview_bytes' not in saved
    assert saved['native_output_verification']['docx']['pdf_sha256']
    original = app.session_state['result']['draft']['본문']
    app.text_area(key='draft_body').set_value(original + '\n새 문장').run()
    assert not app.exception
    assert 'exports' not in app.session_state
    assert 'native_output_verification' not in app.session_state['result']
    assert '_native_preview_bytes' not in app.session_state['result']
    assert not download_labels(app)
    app.text_area(key='draft_body').set_value(original).run()
    assert not download_labels(app)


@pytest.mark.parametrize('mode', ['auto', 'required'])
def test_refreshed_context_does_not_hide_a_stale_native_source_sha(tmp_path, mode):
    template = tmp_path / 'form.docx'
    template.write_bytes(b'original-template')
    data = b'current-output'
    result = {'output_hashes': {'docx': sha256(data).hexdigest()},
              'native_output_verification': {'docx': {'status': 'passed', 'blocking': False,
                    'source_sha256': sha256(template.read_bytes()).hexdigest(), 'output_sha256': sha256(data).hexdigest()}},
              '_native_preview_bytes': {'docx': pdf_bytes()}}
    paths = {'docx': template}
    context = native_download_context(result, paths, {}, {}, mode)
    state = {'native_download_context': context, 'exports': {'docx': data}}
    assert not sync_native_outputs(result, context, state=state)
    template.write_bytes(b'changed-after-earlier-format-review')
    updated = native_download_context(result, paths, {}, {}, mode)
    state['native_download_context'] = updated  # UI refresh cannot bless old evidence.
    assert sync_native_outputs(result, updated, state=state)
    assert not any(key in result for key in ('output_hashes', 'native_output_verification', '_native_preview_bytes'))
    assert 'exports' not in state


@pytest.mark.parametrize('published', ['exports', 'preview'])
def test_legacy_string_context_never_reuses_download_or_preview(published):
    data = b'old-output'
    result = {'output_hashes': {'docx': sha256(data).hexdigest()},
              'native_output_verification': {'docx': {'status': 'passed', 'output_sha256': sha256(data).hexdigest()}}}
    state = {'native_download_context': 'old-hash-only-context'}
    if published == 'exports':
        state['exports'] = {'docx': data}
    else:
        result['_native_preview_bytes'] = {'docx': pdf_bytes()}
    assert sync_native_outputs(result, 'old-hash-only-context', state=state)
    assert 'exports' not in state and '_native_preview_bytes' not in result
    assert 'native_output_verification' not in result


def test_native_ui_required_failure_retains_diagnostics_and_mode_change_clears(native_app):
    app, calls = native_app
    app.checkbox(key='confirmed').check().run()
    app.checkbox(key='native_review_required').check().run()
    app.button(key='prepare').click().run()
    assert not app.exception
    assert calls == ['required']
    assert 'exports' not in app.session_state
    assert app.session_state['result']['native_output_verification']['hwpx']['status'] == 'unavailable'
    assert any('현재 환경' in item.value for item in app.warning)
    assert not download_labels(app)
    app.checkbox(key='native_review_enabled').uncheck().run()
    assert 'native_output_verification' not in app.session_state['result']
    app.button(key='prepare').click().run()
    assert not app.exception
    assert calls == ['required', 'off']
    assert {'DOCX 다운로드', 'HWPX 다운로드'} <= set(download_labels(app))


def test_native_download_uses_fresh_production_completeness_after_preparation(native_app, monkeypatch, native_unavailable):
    import agent.pipeline as pipeline

    app, _ = native_app
    monkeypatch.setattr(pipeline, 'build_downloads', production_build_downloads)
    before = deepcopy(app.session_state['result']['completeness'])
    app.checkbox(key='confirmed').check().run()
    app.button(key='prepare').click().run()
    assert not app.exception
    result = app.session_state['result']
    assert result['completeness'] != before
    assert set(app.session_state['exports']) == {'docx', 'hwpx'}
    assert app.session_state['native_download_context'] == native_download_context(
        result, {suffix: ROOT / 'templates' / f'result_report.{suffix}' for suffix in ('docx', 'hwpx')}, {}, {}, 'auto')
    assert {'DOCX 다운로드', 'HWPX 다운로드'} <= set(download_labels(app))
