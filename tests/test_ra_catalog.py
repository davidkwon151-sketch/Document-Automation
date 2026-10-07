from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json

import pytest

from templates import catalog, ra


def test_official_ra_sources_have_distinct_canonical_forms_workflows_and_limits():
    entries = ra.list_ra_templates()
    assert len(entries) >= 14
    assert len({entry['id'] for entry in entries}) == len(entries)
    assert len({entry['canonical_form_id'] for entry in entries}) == len(entries)
    assert set().union(*(set(entry['workflows']) for entry in entries)) == set(ra.RA_WORKFLOWS)
    for entry in entries:
        assert entry['download_status'] == 'verified' and len(entry['sha256']) == 64
        assert entry['resource_kind'] == 'blank_form' and entry['source_kind'] == 'public_form'
        assert entry['fill_verified'] is entry['legal_compliance_certified'] is entry['mandatory_requirements_verified'] is entry['full_submission_ready'] is False
        assert entry['license']['redistribution_permitted'] is False
        assert entry['version_label'] and entry['source_page'] and entry['checked_at']


def test_workflow_filters_are_independent_copies_and_unknown_key_is_rejected():
    result = ra.list_ra_templates('dmf')
    assert result and all('dmf' in entry['workflows'] for entry in result)
    result[0]['workflows'].append('fake')
    assert 'fake' not in ra.list_ra_templates('dmf')[0]['workflows']
    with pytest.raises(catalog.CatalogError):
        ra.list_ra_templates('unknown')


@pytest.mark.parametrize('change', [
    {'id': 'unknown'}, {'source_url': 'https://evil.example/form.pdf'}, {'filename': '../escape.pdf'},
    {'sha256': '0'*64}, {'format': 'HWPX'}, {'allowed_hosts': ['evil.example']},
])
def test_caller_cannot_change_observed_download_source(change, tmp_path):
    entry = ra.list_ra_templates()[0]
    with pytest.raises(catalog.CatalogError):
        ra.download_ra_template({**entry, **change}, tmp_path)


def test_offline_download_checks_pinned_bytes_and_records_provenance(monkeypatch, tmp_path):
    payload = b'%PDF-1.7\nverified mock official form'
    entry = dict(id='mock', source_url='https://law.go.kr/LSW/flDownload.do?flSeq=1', filename='mock.pdf', format='PDF',
                 allowed_hosts=['law.go.kr'], sha256=sha256(payload).hexdigest(), download_status='verified')
    monkeypatch.setattr(ra, 'list_ra_templates', lambda workflow=None: [deepcopy(entry)])
    class Response(BytesIO):
        headers = {}
        def geturl(self): return entry['source_url']
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == entry['source_url'] and timeout == 30
            return Response(payload)
    monkeypatch.setattr(catalog, 'build_opener', lambda *args: Opener())
    output = ra.download_ra_template(entry, tmp_path)
    assert output.read_bytes() == payload
    assert json.loads(output.with_suffix('.pdf.source.json').read_text(encoding='utf-8'))['sha256'] == entry['sha256']


def test_cached_original_avoids_network_and_tampered_original_is_preserved(monkeypatch, tmp_path):
    payload = b'%PDF-1.7\nverified cached file'
    entry = dict(id='seed', source_url='https://law.go.kr/LSW/flDownload.do?flSeq=1', filename='seed.pdf', format='PDF',
                 allowed_hosts=['law.go.kr'], sha256=sha256(payload).hexdigest(), download_status='verified')
    monkeypatch.setattr(ra, 'list_ra_templates', lambda workflow=None: [deepcopy(entry)])
    monkeypatch.setattr(ra, '_download_verified_entry', lambda *args: pytest.fail('Existing pinned originals must not redownload'))
    path = tmp_path / entry['filename']; path.write_bytes(payload)
    assert ra.download_ra_template(entry, tmp_path) == path
    path.write_bytes(b'changed')
    with pytest.raises(catalog.CatalogError, match='해시'):
        ra.download_ra_template(entry, tmp_path)
    assert path.read_bytes() == b'changed'


@pytest.mark.parametrize('change', [
    {'fill_verified': True}, {'legal_compliance_certified': True}, {'mandatory_requirements_verified': True},
    {'full_submission_ready': True}, {'source_url': 'http://law.go.kr/file'}, {'allowed_hosts': ['law.go.kr.evil.example']},
    {'source_kind': 'public_guide'}, {'workflows': ['fake']}, {'sha256': None}, {'size_bytes': 0},
])
def test_catalog_rejects_unperformed_legal_or_form_assertions(change, monkeypatch, tmp_path):
    data = ra.load_ra_catalog(); data['documents'][0].update(change)
    path = tmp_path / 'catalog.json'; path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(ra, 'CATALOG_PATH', path)
    with pytest.raises(catalog.CatalogError):
        ra.load_ra_catalog()


def test_wrong_download_signature_does_not_publish_a_fake_pdf(monkeypatch, tmp_path):
    body = b'<html>login required</html>'
    entry = dict(id='bad', source_url='https://law.go.kr/LSW/flDownload.do?flSeq=1', filename='bad.pdf', format='PDF',
                 allowed_hosts=['law.go.kr'], sha256=sha256(body).hexdigest(), download_status='verified')
    monkeypatch.setattr(ra, 'list_ra_templates', lambda workflow=None: [entry])
    class Response(BytesIO):
        headers = {}
        def geturl(self): return entry['source_url']
    class Opener:
        def open(self, *args, **kwargs): return Response(body)
    monkeypatch.setattr(catalog, 'build_opener', lambda *args: Opener())
    with pytest.raises(catalog.CatalogError):
        ra.download_ra_template(entry, tmp_path)
    assert not (tmp_path / 'bad.pdf').exists()
