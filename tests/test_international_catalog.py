from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json

import pytest

from templates import catalog,international


def test_observed_official_forms_preserve_format_provenance_and_limits():
    entries=international.list_international_templates()
    original = [entry for entry in entries if entry['id'].startswith('international_')]
    assert len(original)==5 and len({entry['company'] for entry in original})==3
    ra = [entry for entry in entries if entry.get('domain')=='pharmaceutical_ra']
    assert len(ra)==3 and len(entries)==8 and len({entry['company'] for entry in ra})==2
    assert sum(entry['mapping_analysis']['field_count'] for entry in ra)==14
    assert {entry['format'] for entry in entries}=={'DOCX','PDF','PPTX','XLSX'}
    assert {entry['language'] for entry in entries}=={'en','de'}
    for entry in entries:
        assert entry['resource_kind']=='blank_form' and entry['source_kind']=='public_form'
        assert entry['use_classification']=='company_public_submission'
        assert entry['download_status']=='verified' and len(entry['sha256'])==64
        assert entry['fill_verified'] is entry['internal_forms_verified'] is entry['korean_entity_employment_verified'] is False
        assert entry['license']['redistribution_permitted'] is False
        assert entry['mapping_analysis']['source_sha256']==entry['sha256']


def test_company_alias_filters_and_results_are_copied():
    result=international.list_international_templates('Bosch')
    assert len(result)==2 and result==international.list_international_templates('보쉬')
    result[0]['license']['status']='changed'
    assert international.list_international_templates('Bosch')[0]['license']['status']=='unknown'
    assert international.list_international_templates('unknown')==[]


@pytest.mark.parametrize('change',[{'id':'unknown'},{'source_url':'https://evil.example/file.pdf'},
    {'filename':'../escape.pdf'},{'format':'PDF'},{'sha256':'0'*64},{'allowed_hosts':['evil.example']}])
def test_caller_cannot_substitute_download_source(change,tmp_path):
    entry=international.list_international_templates()[0]
    with pytest.raises(catalog.CatalogError):international.download_international_template({**entry,**change},tmp_path)


def test_offline_download_verifies_bytes_and_writes_provenance(monkeypatch,tmp_path):
    payload=b'%PDF-1.7\nmock official form'
    entry=dict(id='mock',source_url='https://official.example/file.pdf',filename='mock.pdf',format='PDF',
               allowed_hosts=['official.example'],sha256=sha256(payload).hexdigest(),download_status='verified')
    monkeypatch.setattr(international,'list_international_templates',lambda company=None:[deepcopy(entry)])
    class Response(BytesIO):
        headers={}
        def geturl(self):return entry['source_url']
    class Opener:
        def open(self,request,timeout):
            assert timeout==30 and request.full_url==entry['source_url'];return Response(payload)
    monkeypatch.setattr(catalog,'build_opener',lambda *args:Opener())
    output=international.download_international_template(entry,tmp_path)
    assert output.read_bytes()==payload
    assert json.loads(output.with_suffix('.pdf.source.json').read_text(encoding='utf-8'))['sha256']==entry['sha256']


def test_verified_existing_seed_does_not_redownload_and_changed_cache_blocks(monkeypatch,tmp_path):
    payload=b'%PDF-1.7\nverified cached seed'
    entry=dict(id='seed',source_url='https://official.example/file.pdf',filename='seed.pdf',format='PDF',
               allowed_hosts=['official.example'],sha256=sha256(payload).hexdigest(),download_status='verified')
    monkeypatch.setattr(international,'list_international_templates',lambda company=None:[deepcopy(entry)])
    def network(*args,**kwargs):raise AssertionError('검증된 기존 원본은 다시 다운로드하지 않아야 함')
    monkeypatch.setattr(international,'_download_verified_entry',network)
    path=tmp_path/entry['filename'];path.write_bytes(payload)
    assert international.download_international_template(entry,tmp_path)==path
    path.write_bytes(b'changed')
    with pytest.raises(catalog.CatalogError,match='해시'):international.download_international_template(entry,tmp_path)
    assert path.read_bytes()==b'changed'


@pytest.mark.parametrize('change',[{'fill_verified':True},{'internal_forms_verified':True},
    {'korean_entity_employment_verified':True},{'resource_kind':'layout_reference'},{'use_classification':'company_internal'},
    {'source_url':'http://localhost/secret'},{'sha256':None}])
def test_metadata_cannot_claim_unperformed_company_compatibility(change,monkeypatch,tmp_path):
    data=international.load_international_catalog();data['documents'][0].update(change)
    path=tmp_path/'catalog.json';path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    monkeypatch.setattr(international,'CATALOG_PATH',path)
    with pytest.raises(catalog.CatalogError):international.load_international_catalog()
