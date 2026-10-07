"""Public corporate RA forms keep their verified workflow when imported."""

from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture
def corporate_catalog(monkeypatch, tmp_path):
    from templates import international

    entry = {'id': 'mock_ra_public', 'company': 'QA Company',
             'title': 'QA public sample submission', 'source_page': 'https://example.test/forms',
             'source_url': 'https://example.test/form.pdf', 'filename': 'form.pdf',
             'checked_at': '2026-10-03', 'version_label': 'QA snapshot; currentness unverified',
             'domain': 'pharmaceutical_ra', 'ra_workflow': 'testing_support'}
    source = tmp_path / 'form.pdf'
    source.write_bytes(b'%PDF mock; no document generation')
    downloads = []
    monkeypatch.setattr(international, 'list_international_templates', lambda: [deepcopy(entry)])

    def download(chosen, directory):
        downloads.append(deepcopy(chosen))
        return source

    monkeypatch.setattr(international, 'download_international_template', download)
    script = f"""
from pathlib import Path
import streamlit as st
from app.catalog_ui import render_catalog
render_catalog(Path({str(tmp_path)!r}))
"""
    return AppTest.from_string(script, default_timeout=20), entry, source, downloads


def test_corporate_ra_import_selects_registered_workflow_and_preserves_provenance(corporate_catalog):
    app, entry, source, downloads = corporate_catalog
    app.session_state['work_domain'] = 'office_finance'
    app.session_state['custom_template'] = 'old-upload'
    app.run()
    app.button(key='import_international_form').click().run()
    assert not app.exception
    assert len(downloads) == 1
    assert app.session_state['public_template_path'] == str(source)
    assert app.session_state['public_template_provenance'] == entry
    assert app.session_state['work_domain'] == 'pharmaceutical_ra'
    assert app.session_state['ra_workflow'] == 'testing_support'
    assert app.session_state['document_kind'] == 'application'
    assert 'custom_template' not in app.session_state
    assert any(entry['version_label'] in caption.value for caption in app.caption)
    assert any('전체 의뢰·제출 완료를 뜻하지' in caption.value for caption in app.caption)


@pytest.mark.parametrize('workflow', ['unknown', None])
def test_invalid_ra_workflow_does_not_download_or_change_selected_form(corporate_catalog, workflow):
    app, entry, _, downloads = corporate_catalog
    entry['ra_workflow'] = workflow
    app.session_state['work_domain'] = 'general'
    app.session_state['custom_template'] = 'old-upload'
    app.run()
    app.button(key='import_international_form').click().run()
    assert not app.exception
    assert downloads == [] and 'public_template_path' not in app.session_state
    assert app.session_state['custom_template'] == 'old-upload'
    assert app.session_state['work_domain'] == 'general'
    assert any('등록 업무' in error.value for error in app.error)


def test_general_corporate_form_import_keeps_existing_domain(corporate_catalog):
    app, entry, source, downloads = corporate_catalog
    entry.pop('domain')
    entry.pop('ra_workflow')
    app.session_state['work_domain'] = 'general'
    app.run()
    app.button(key='import_international_form').click().run()
    assert not app.exception and len(downloads) == 1
    assert app.session_state['public_template_path'] == str(source)
    assert app.session_state['work_domain'] == 'general'
