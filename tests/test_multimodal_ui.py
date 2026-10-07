from hashlib import sha256
from io import BytesIO

from PIL import Image
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from app import multimodal_ui as ui


class Upload:
    def __init__(self, name='원문.txt', content=b'Quantity: 10'):
        self.name, self.content = name, content

    def getvalue(self):
        return self.content


def image_upload():
    buffer = BytesIO()
    Image.new('RGB', (100, 60), 'white').save(buffer, format='PNG')
    return Upload('사진.png', buffer.getvalue())


def app():
    return AppTest.from_string('''
import streamlit as st
from app.multimodal_ui import render_multimodal_upload
result = render_multimodal_upload(key_prefix='test', context=st.session_state.get('context'))
st.session_state['result'] = result
''', default_timeout=20).run()


@pytest.mark.parametrize('names', [['../bad.txt'], ['bad\\file.txt'], ['same.txt', 'same.txt']])
def test_private_upload_rejects_paths_and_duplicate_names(names):
    with pytest.raises(ValueError, match='파일명'):
        ui.intake_uploads([Upload(name) for name in names])


def test_ui_text_upload_returns_sources_and_change_signature(monkeypatch):
    files = [Upload()]
    monkeypatch.setattr(st, 'file_uploader', lambda *a, **k: files)
    screen = app()
    assert not screen.exception
    result = screen.session_state['result']
    assert result['ready'] and result['changed']
    assert result['sources'][0]['filename'] == '원문.txt'
    assert not result['deferred_files'] and not result['pending_sources']
    screen.run()
    assert not screen.session_state['result']['changed']
    files[0].content = b'Quantity: 11'
    screen.run()
    assert screen.session_state['result']['changed']
    assert screen.session_state['result']['sources'][0]['text'] == 'Quantity: 11'


def test_image_transcription_is_explicit_and_check_uncheck_invalidates(monkeypatch):
    upload = image_upload()
    monkeypatch.setattr(st, 'file_uploader', lambda *a, **k: [upload])
    screen = app()
    assert not screen.exception
    assert not screen.session_state['result']['ready']
    assert not screen.session_state['result']['sources']
    assert len(screen.session_state['result']['deferred_files']) == 1
    next(widget for widget in screen.text_area if '전사' in widget.label).set_value('제품명: 시험정\n수량: 10개')
    screen.run()
    next(widget for widget in screen.button if '등록/수정' in widget.label).click()
    screen.run()
    assert not screen.exception
    assert not screen.session_state['result']['sources']
    assert len(screen.session_state['result']['pending_sources']) == 2
    confirmations = [widget for widget in screen.checkbox if '대조했음' in widget.label]
    for widget in confirmations:
        widget.check()
    screen.run()
    result = screen.session_state['result']
    assert result['ready'] and result['changed']
    assert len(result['sources']) == 2
    assert all(source['verification_receipt']['confirmed'] for source in result['sources'])
    first = next(widget for widget in screen.checkbox if '대조했음' in widget.label)
    first.uncheck()
    screen.run()
    assert not screen.session_state['result']['ready']
    assert screen.session_state['result']['changed']
    assert len(screen.session_state['result']['sources']) == 1


def test_form_product_context_change_clears_transcription_and_confirmation(monkeypatch):
    upload = image_upload()
    monkeypatch.setattr(st, 'file_uploader', lambda *a, **k: [upload])
    screen = app()
    next(widget for widget in screen.text_area if '전사' in widget.label).set_value('제품명: 시험정')
    screen.run()
    next(widget for widget in screen.button if '등록/수정' in widget.label).click()
    screen.run()
    next(widget for widget in screen.checkbox if '대조했음' in widget.label).check()
    screen.run()
    assert screen.session_state['result']['ready']
    screen.session_state['context'] = {'product': '다른제품', 'template_sha': '0' * 64}
    screen.run()
    assert not screen.exception
    assert not screen.session_state['result']['ready']
    assert not screen.session_state['result']['sources']
    assert not screen.session_state['result']['intake']['confirmations']
    assert len(screen.session_state['result']['deferred_files']) == 1


def test_manual_transcription_edit_requires_new_confirmation(monkeypatch):
    upload = image_upload()
    monkeypatch.setattr(st, 'file_uploader', lambda *a, **k: [upload])
    screen = app()
    next(widget for widget in screen.text_area if '전사' in widget.label).set_value('수량: 10개')
    screen.run()
    next(widget for widget in screen.button if '등록/수정' in widget.label).click()
    screen.run()
    next(widget for widget in screen.checkbox if '대조했음' in widget.label).check()
    screen.run()
    old = screen.session_state['result']['signature']
    next(widget for widget in screen.text_area if '전사' in widget.label).set_value('수량: 11개')
    screen.run()
    assert not screen.session_state['result']['ready']
    assert not screen.session_state['result']['sources']
    next(widget for widget in screen.button if '등록/수정' in widget.label).click()
    screen.run()
    result = screen.session_state['result']
    assert not screen.exception
    assert old != result['signature'] and not result['ready']
    assert result['pending_sources'][0]['text'] == '수량: 11개'


def test_ui_does_not_construct_a_model_without_image_opt_in(monkeypatch):
    monkeypatch.setattr(st, 'file_uploader', lambda *a, **k: [image_upload()])
    monkeypatch.setattr(ui, 'LLMClient', lambda: pytest.fail('unexpected image model'))
    screen = app()
    assert not screen.exception
    assert not screen.session_state['result']['ready']
