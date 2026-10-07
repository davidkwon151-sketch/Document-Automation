from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json

import pytest

from templates import catalog, business


def test_business_sources_are_distinct_official_forms_or_labeled_guides():
    entries = business.list_business_templates()
    assert len(entries) >= 12
    assert len({entry['id'] for entry in entries}) == len(entries)
    assert len({entry['sha256'] for entry in entries}) == len(entries)
    assert set().union(*(set(entry['workflows']) for entry in entries)) == set(business.BUSINESS_WORKFLOWS)
    for entry in entries:
        assert entry['download_status'] == 'verified' and entry['version_label'] and entry['checked_at']
        assert (entry['resource_kind'], entry['source_kind'], entry['use_classification']) in {
            ('blank_form', 'public_form', 'public_submission'), ('layout_reference', 'public_guide', 'reference_guidance')}
        assert entry['company_internal'] is entry['legal_compliance_certified'] is entry['mandatory_requirements_verified'] is entry['full_submission_ready'] is entry['fill_verified'] is False
        assert entry['license']['redistribution_permitted'] is False
    assert next(e for e in entries if e['id']=='business_smartfactory_guide_2026')['resource_kind']=='layout_reference'
    assert next(e for e in entries if e['id']=='business_kotra_salesforce_2026')['application_status']=='closed'


def test_workflow_filters_return_copies_and_reject_unknown_keys():
    result = business.list_business_templates('rd_project')
    assert result and all('rd_project' in entry['workflows'] for entry in result)
    result[0]['workflows'].append('fake')
    assert 'fake' not in business.list_business_templates('rd_project')[0]['workflows']
    with pytest.raises(catalog.CatalogError):
        business.list_business_templates('unknown')


@pytest.mark.parametrize('change', [
    {'id': 'unknown'}, {'source_url': 'https://evil.example/form.pdf'}, {'filename': '../escape.pdf'},
    {'sha256': '0'*64}, {'format': 'HWPX'}, {'allowed_hosts': ['evil.example']},
])
def test_caller_cannot_change_pinned_source(change, tmp_path):
    entry = business.list_business_templates()[2]
    with pytest.raises(catalog.CatalogError):
        business.download_business_template({**entry, **change}, tmp_path)


def mock_entry(payload):
    return dict(id='mock', source_url='https://www.kotra.or.kr/kmodule/file/fileDown.do?storFileId=MOCK&fileSn=0',
                filename='mock.pdf', format='PDF', allowed_hosts=['www.kotra.or.kr'],
                sha256=sha256(payload).hexdigest(), download_status='verified')


def test_mock_download_keeps_provenance(monkeypatch, tmp_path):
    payload = b'%PDF-1.7\nmock official form'
    entry = mock_entry(payload)
    monkeypatch.setattr(business, 'list_business_templates', lambda workflow=None: [deepcopy(entry)])
    class Response(BytesIO):
        headers = {}
        def geturl(self): return entry['source_url']
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == entry['source_url'] and timeout == 30
            return Response(payload)
    monkeypatch.setattr(catalog, 'build_opener', lambda *args: Opener())
    output = business.download_business_template(entry, tmp_path)
    assert output.read_bytes() == payload
    assert json.loads(output.with_suffix('.pdf.source.json').read_text(encoding='utf-8'))['sha256']==entry['sha256']


def test_cache_does_not_download_or_overwrite_changed_original(monkeypatch, tmp_path):
    payload = b'%PDF-1.7\npinned cache'
    entry = mock_entry(payload)
    monkeypatch.setattr(business, 'list_business_templates', lambda workflow=None: [entry])
    monkeypatch.setattr(business, '_download_verified_entry', lambda *a: pytest.fail('Cache must not download'))
    path = tmp_path / entry['filename']; path.write_bytes(payload)
    assert business.download_business_template(entry, tmp_path)==path
    path.write_bytes(b'changed')
    with pytest.raises(catalog.CatalogError, match='해시'):
        business.download_business_template(entry, tmp_path)
    assert path.read_bytes()==b'changed'


@pytest.mark.parametrize('change', [
    {'fill_verified': True}, {'company_internal': True}, {'application_status': 'open'},
    {'legal_compliance_certified': True}, {'mandatory_requirements_verified': True},
    {'full_submission_ready': True}, {'source_url': 'http://www.kotra.or.kr/file'},
    {'allowed_hosts': ['www.kotra.or.kr.evil.example']}, {'source_kind': 'public_guide'},
    {'workflows': ['fake']}, {'sha256': None}, {'size_bytes': 0},
])
def test_unverified_claims_and_bad_provenance_are_rejected(change, monkeypatch, tmp_path):
    data = business.load_business_catalog(); data['documents'][0].update(change)
    path=tmp_path/'catalog.json';path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    monkeypatch.setattr(business,'CATALOG_PATH',path)
    with pytest.raises(catalog.CatalogError):business.load_business_catalog()


def test_html_cannot_be_published_as_pdf(monkeypatch,tmp_path):
    payload=b'<html>Login required</html>';entry=mock_entry(payload)
    monkeypatch.setattr(business,'list_business_templates',lambda workflow=None:[entry])
    class Response(BytesIO):
        headers={}
        def geturl(self):return entry['source_url']
    class Opener:
        def open(self,*a,**k):return Response(payload)
    monkeypatch.setattr(catalog,'build_opener',lambda *a:Opener())
    with pytest.raises(catalog.CatalogError):business.download_business_template(entry,tmp_path)
    assert not (tmp_path/entry['filename']).exists()
