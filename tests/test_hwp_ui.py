from pathlib import Path
import shutil
from io import BytesIO

import pytest

from app import hwp_ui

ROOT = Path(__file__).resolve().parents[1]


def test_uploaded_hwp_yields_reopened_hwpx_without_exposing_temporary_path(monkeypatch):
    calls = []

    def converter(source, output_dir):
        calls.append(source)
        output_dir.mkdir()
        target = output_dir / 'uploaded.hwpx'
        shutil.copyfile(ROOT / 'samples/sample_company_form.hwpx', target)
        return target

    monkeypatch.setattr(hwp_ui, 'convert_hwp', converter)
    data = (ROOT / 'data/public_templates/ra/ra_law_form_23_20260305.hwp').read_bytes()
    result = hwp_ui.convert_uploaded_hwp('신청서.hwp', data)
    assert result['filename'] == '신청서.hwpx' and result['data'].startswith(b'PK')
    assert set(result) == {'filename', 'data', 'source_sha256', 'output_sha256'}
    assert not calls[0].exists()


def test_failed_native_activation_never_returns_download(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('RegisterModule 활성화 실패')

    monkeypatch.setattr(hwp_ui, 'convert_hwp', fail)
    with pytest.raises(RuntimeError, match='활성화'):
        hwp_ui.convert_uploaded_hwp('신청서.hwp', b'HWP')


def test_invalid_output_and_changed_input_are_rejected(monkeypatch):
    def bad(source, output_dir):
        source.write_bytes(b'changed')
        target = source.with_suffix('.hwpx')
        target.write_bytes(b'not valid')
        return target

    monkeypatch.setattr(hwp_ui, 'convert_hwp', bad)
    with pytest.raises(ValueError, match='입력 파일'):
        hwp_ui.convert_uploaded_hwp('신청서.hwp', b'input')


def test_valid_source_with_invalid_hwpx_output_is_rejected(monkeypatch):
    def bad(source, output_dir):
        target = source.with_suffix('.hwpx')
        target.write_bytes(b'not valid')
        return target

    monkeypatch.setattr(hwp_ui, 'convert_hwp', bad)
    with pytest.raises(ValueError, match='HWPX'):
        hwp_ui.convert_uploaded_hwp('신청서.hwp', b'input')


@pytest.mark.parametrize('filename,data', [('file.pdf', b'HWP'), ('file.hwp', 'not bytes')])
def test_conversion_accepts_only_uploaded_hwp_bytes(filename, data):
    with pytest.raises(ValueError, match='HWP'):
        hwp_ui.convert_uploaded_hwp(filename, data)


def _uploaded_app(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    upload = BytesIO(b'opaque mock HWP upload')
    upload.name = '양식.hwp'
    current = [upload]
    original = hwp_ui.st.file_uploader
    monkeypatch.setattr(hwp_ui.st, 'file_uploader',
                        lambda *args, **kwargs: current[0] if kwargs.get('key') == 'hwp_conversion_upload'
                        else original(*args, **kwargs))
    monkeypatch.setenv('REPORT_AGENT_DATA_DIR', str(tmp_path))
    return AppTest.from_file(str(ROOT / 'app/ra_mvp_ui.py'), default_timeout=30), current


def test_native_failure_removes_previous_conversion_download(monkeypatch, tmp_path):
    app, _ = _uploaded_app(monkeypatch, tmp_path)

    def failure(*args):
        raise RuntimeError('RegisterModule 활성화 실패')

    monkeypatch.setattr(hwp_ui, 'convert_uploaded_hwp', failure)
    app.run()
    app.session_state['hwp_conversion_result'] = {'filename': 'old.hwpx', 'data': b'old'}
    app.button(key='hwp_conversion_run').click().run()
    assert not app.exception
    assert any('활성화 실패' in item.value for item in app.error)
    assert 'hwp_conversion_result' not in app.session_state
    assert not any(item.key == 'hwp_conversion_download' for item in app.get('download_button'))


def test_upload_change_clears_previous_success_without_converting(monkeypatch, tmp_path):
    app, current = _uploaded_app(monkeypatch, tmp_path)
    calls = []

    def success(name, data):
        calls.append(name)
        return {'filename': '양식.hwpx', 'data': b'validated mock output'}

    monkeypatch.setattr(hwp_ui, 'convert_uploaded_hwp', success)
    app.run()
    app.button(key='hwp_conversion_run').click().run()
    assert not app.exception and any(item.key == 'hwp_conversion_download' for item in app.get('download_button'))
    replacement = BytesIO(b'different source upload')
    replacement.name = '새양식.hwp'
    current[0] = replacement
    app.run()
    assert not app.exception and not any(item.key == 'hwp_conversion_download' for item in app.get('download_button'))
    assert calls == ['양식.hwp']
