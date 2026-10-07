"""Offline RA comparison UI checks, including stale-result removal."""
from hashlib import sha256
import pytest

from streamlit.testing.v1 import AppTest
from app import ra_compare_ui as ui
from tests.test_ra_change_compare import source


def app(before=None, after=None):
    return AppTest.from_string('from app.ra_compare_ui import render_change_compare\n'
                              + f'render_change_compare(before_sources={repr([source()] if before is None else before)}, '
                              + f'after_sources={repr([source("after",value="회사B")] if after is None else after)})',
                              default_timeout=20).run()


def ready(at):
    at.text_input(key='rc_product').set_value('약A')
    at.text_input(key='rc_variant').set_value('정제 10 mg')
    at.text_area(key='rc_labels').set_value('제조원')
    at.run()
    assert not at.exception
    return at


def test_ui_real_comparison_downloads_exact_csv_and_evidence_without_model():
    at=ready(app());at.button(key='rc_compare').click().run()
    assert not at.exception and at.dataframe
    r=at.session_state['rc_result']
    assert r['rows'][0]['change_kind']=='차이' and r['actual_model_requests']==0
    assert not r['submission_ready'] and len(at.get('download_button'))==2
    assert all('rc_' in str(w.key) for w in list(at.button)+list(at.text_input)+list(at.text_area)+list(at.selectbox))


def test_ui_output_invalidated_by_product_variant_labels_and_exact_quote_changes():
    for key,value,kind in [('rc_product','약B','text_input'),('rc_variant','정제 20 mg','text_input'),('rc_labels','주소','text_area')]:
        at=ready(app());at.button(key='rc_compare').click().run()
        getattr(at,kind)(key=key).set_value(value).run()
        assert not at.exception and not at.get('download_button')
        with pytest.raises(KeyError):
            at.session_state['rc_result']


def test_ui_mismatch_and_missing_are_visible_not_approved():
    at=ready(app(after=[source('after',product='약B')]))
    at.button(key='rc_compare').click().run()
    assert not at.exception and at.warning and at.session_state['rc_result']['rows'][0]['change_kind']=='모호'
    # Downloads are diagnostic comparison evidence, explicitly not submission documents.
    assert all('다운로드' in w.label for w in at.get('download_button'))


def test_ui_manual_exact_selection_is_confirmed_and_stale_removed_when_unchecked():
    b=source(extra='\n제조원: 회사Z');at=ready(app(before=[b]))
    select=next(s for s in at.selectbox if '변경 전' in s.label)
    select.select('S1').run()
    assert at.button(key='rc_compare').disabled
    q=next(x for x in at.text_area if x.label=='기입할 정확한 전체 인용');q.set_value('회사Z').run()
    at.checkbox[0].check().run();at.button(key='rc_compare').click().run()
    assert not at.exception and at.session_state['rc_result']['rows'][0]['before']=='회사Z'
    at.checkbox[0].uncheck().run()
    assert not at.get('download_button')


def test_ui_unconfirmed_new_editor_invalidates_existing_automatic_result():
    at=ready(app());at.button(key='rc_compare').click().run()
    assert at.get('download_button')
    next(s for s in at.selectbox if '변경 전' in s.label).select('S1').run()
    assert not at.exception and not at.get('download_button') and at.button(key='rc_compare').disabled


@pytest.mark.parametrize('kind,key,value',[('text_input','rc_product','약B'),('text_input','rc_variant','정제 20 mg'),('text_area','rc_labels','제조원\n주소')])
def test_ui_product_variant_or_label_context_does_not_inherit_quote_confirmation(kind,key,value):
    at=ready(app())
    next(s for s in at.selectbox if '변경 전' in s.label).select('S1').run()
    next(t for t in at.text_area if t.label=='기입할 정확한 전체 인용').set_value('회사A').run()
    at.checkbox[0].check().run();at.button(key='rc_compare').click().run()
    assert at.get('download_button')
    getattr(at,kind)(key=key).set_value(value).run()
    assert not at.exception and not at.get('download_button')
    assert all(s.value=='' for s in at.selectbox)
    assert not at.checkbox


def test_ui_parser_injection_and_real_txt_upload_preserve_file_sha(monkeypatch):
    class Upload:
        def __init__(self,name,text):self.name=name;self.data=text.encode()
        def getvalue(self):return self.data
    b=Upload('before.txt','제품명: 약A\n제형·함량: 정제 10 mg\n제조원: 회사A')
    a=Upload('after.txt','제품명: 약A\n제형·함량: 정제 10 mg\n제조원: 회사B')
    monkeypatch.setattr(ui.st,'file_uploader',lambda label,**kw:[b] if label.startswith('변경 전') else [a])
    at=AppTest.from_string('from app.ra_compare_ui import render_change_compare\nrender_change_compare()',default_timeout=20).run()
    ready(at);at.button(key='rc_compare').click().run()
    assert not at.exception, [e.message for e in at.exception]
    row=at.session_state['rc_result']['rows'][0]
    assert row['change_kind']=='차이'
    assert row['before_refs'][0]['source']['document_sha256']==sha256(b.data).hexdigest()
    assert row['after_refs'][0]['source']['document_sha256']==sha256(a.data).hexdigest()


def test_ui_parser_failure_does_not_reuse_success_or_leak_raw_error(monkeypatch):
    class Upload:
        name='bad.txt'
        def getvalue(self):return b'text'
    monkeypatch.setattr(ui.st,'file_uploader',lambda *a,**kw:[Upload()])
    def fail(files):raise RuntimeError('provider-private-secret')
    monkeypatch.setattr(ui,'compare_ra_changes',fail)
    # Parser injection takes precedence; no backend error text is displayed.
    at=AppTest.from_string('from app.ra_compare_ui import render_change_compare\n'
                          'def parser(files):\n raise RuntimeError("provider-private-secret")\n'
                          'render_change_compare(parser=parser)',default_timeout=20).run()
    assert not at.exception and at.error and 'provider-private-secret' not in at.error[0].value
    assert not at.get('download_button')


def test_ui_no_input_disables_compare_and_uses_no_page_config():
    at=app(before=[],after=[])
    assert not at.exception and at.button(key='rc_compare').disabled
    assert not at.get('download_button')
