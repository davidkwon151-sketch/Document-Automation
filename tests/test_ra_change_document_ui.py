"""Offline Streamlit interaction; scripted confirmation is not a human KPI."""
from hashlib import sha256
import json

from streamlit.testing.v1 import AppTest


def _source(side):
    text = '제품명: SyntheticDrugA\n제형·함량: 정제 10 mg\n주소: ' + ('서울' if side == 'before' else '부산')
    return {'source_id': 'S1', 'filename': side + '.txt', 'text': text, 'page': 1,
            'document_sha256': sha256((side + text).encode()).hexdigest()}


def _app():
    code = ('import json\nfrom app.ra_change_document_ui import render_change_document\n'
            'render_change_document(before_sources=json.loads(' + repr(json.dumps([_source('before')]))
            + '),after_sources=json.loads(' + repr(json.dumps([_source('after')])) + '))')
    return AppTest.from_string(code, default_timeout=25).run()


def _clean(app):
    assert not app.exception, [item.message for item in app.exception]


def _compared():
    app = _app()
    app.text_input(key='rcd_product').set_value('SyntheticDrugA').run()
    app.text_input(key='rcd_variant').set_value('정제 10 mg').run()
    app.text_area(key='rcd_labels').set_value('주소').run()
    app.button(key='rcd_compare').click().run()
    _clean(app)
    return app


def _confirmed():
    app = _compared()
    app.multiselect[0].select('주소').run()
    app.text_area[1].set_value('주소 변경 요청').run()
    next(item for item in app.checkbox if item.label.startswith('주소:')).check().run()
    app.button(key='rcd_prepare').click().run()
    _clean(app)
    return app


def test_no_comparison_before_identity_and_no_row_or_reason_autofilled():
    app = _app()
    _clean(app)
    assert app.button(key='rcd_compare').disabled
    app = _compared()
    assert app.multiselect[0].value == []
    assert app.button(key='rcd_prepare').disabled
    app.multiselect[0].select('주소').run()
    assert app.text_area[1].value == ''
    assert app.button(key='rcd_prepare').disabled


def test_missing_reason_group_blocks_output_even_with_scripted_row_confirmation():
    app = _compared()
    app.multiselect[0].select('주소').run()
    next(item for item in app.checkbox if item.label.startswith('주소:')).check().run()
    app.button(key='rcd_prepare').click().run()
    _clean(app)
    assert app.button(key='rcd_export_button').disabled
    assert any('그룹의 일부' in item.value for item in app.warning)
    assert not list(app.get('download_button'))


def test_actual_original_export_after_confirmation_then_reason_edit_removes_downloads():
    app = _confirmed()
    assert app.button(key='rcd_export_button').disabled
    next(item for item in app.checkbox if item.label.startswith('위 항목')).check().run()
    app.button(key='rcd_export_button').click().run()
    _clean(app)
    labels = [item.proto.label for item in app.get('download_button')]
    assert '변경 신청서 PDF 다운로드' in labels and '작업 패키지 ZIP 다운로드' in labels
    app.text_area[1].set_value('담당자 사유 정정').run()
    _clean(app)
    assert not list(app.get('download_button'))
    assert app.button(key='rcd_prepare').disabled


def test_source_identity_edit_clears_comparison_and_all_document_downloads():
    app = _confirmed()
    app.text_input(key='rcd_product').set_value('OtherDrug').run()
    _clean(app)
    assert not list(app.get('download_button'))
    assert not list(app.multiselect)


def test_shared_multimodal_intake_deferred_source_cannot_compare(monkeypatch):
    from app import multimodal_ui
    monkeypatch.setattr(multimodal_ui, 'render_multimodal_upload',
                        lambda **kwargs: {'sources': [_source('before')], 'ready': False, 'changed': True})
    app = AppTest.from_string('from app.ra_change_document_ui import render_change_document\nrender_change_document()', default_timeout=20).run()
    app.text_input(key='rcd_product').set_value('SyntheticDrugA').run()
    app.text_input(key='rcd_variant').set_value('정제 10 mg').run()
    _clean(app)
    assert app.button(key='rcd_compare').disabled
    assert any('미확인 전사문' in item.value for item in app.warning)
