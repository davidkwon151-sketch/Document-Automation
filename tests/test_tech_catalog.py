from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json

import pytest

from templates import catalog, tech


def test_profiles_keep_internal_forms_unknown_and_addresses_sourced():
    data=tech.load_tech_catalog()
    assert 10 <= len(data['companies']) <= 20
    assert len({p['id'] for p in data['companies']})==len(data['companies'])
    for profile in data['companies']:
        assert profile['internal_forms_status']=='unknown'
        location=profile['location']
        if location['status']=='verified':
            assert location['address'] and location['source_url'].startswith('https://')
            assert location['kind'] in {'hq','office'}
    assert next(p for p in data['companies'] if p['id']=='deepx')['location']['kind']=='hq'
    assert next(p for p in data['companies'] if p['id']=='nc')['location']['kind']=='office'
    assert tech.list_tech_templates('네이버')==[]
    assert tech.list_tech_templates('카카오')==[]


def test_public_originals_have_honest_hash_format_rights_and_resource_kind():
    entries=tech.list_tech_templates()
    assert len(entries)>=10
    verified=[e for e in entries if e['download_status']=='verified']
    assert len(verified)>=8
    assert all(len(e['sha256'])==64 and e['size_bytes']>0 for e in verified)
    for entry in entries:
        assert entry['company_internal'] is False
        assert entry['license']['redistribution_permitted'] is False
        assert entry['compatibility_status'] in {'unverified','unavailable'}
        assert entry['checked_at'] and entry['source_page'] and entry['affiliate']
        if entry['resource_kind']=='blank_form':
            assert entry['source_kind']=='public_form'
        else:
            assert entry['use_classification']=='reference_report'
    assert next(e for e in entries if e['id']=='smilegate_foundation_2017')['year']==2017


def test_startup_submissions_are_not_internal_company_or_blank_esg_forms():
    entries=tech.list_tech_templates()
    startup=[e for e in entries if e['use_classification']=='startup_submission']
    assert len(startup)==3
    assert all(e['resource_kind']=='blank_form' and e['source_kind']=='public_form' for e in startup)
    assert sum(e['download_status']=='verified' for e in startup)==2
    paju=next(p for p in tech.load_tech_catalog()['startup_programs'] if p['id']=='gbsa-paju-prestar')
    assert paju['eligible_region']=='파주시'
    assert '판교 소재만' in paju['notes']


def test_filter_aliases_returns_copies():
    entries=tech.list_tech_templates('NCSOFT')
    assert entries==tech.list_tech_templates('엔씨소프트')==tech.list_tech_templates('nc')
    entries[0]['license']['status']='changed'
    assert tech.list_tech_templates('nc')[0]['license']['status']=='unknown'
    assert tech.list_tech_templates('missing')==[]


@pytest.mark.parametrize('change',[{'source_url':'https://untrusted.example/file.pdf'},{'filename':'../escape.pdf'},{'sha256':'0'*64},{'allowed_hosts':['untrusted.example']},{'id':'unknown'}])
def test_caller_cannot_substitute_original(change,tmp_path):
    entry=next(e for e in tech.list_tech_templates() if e['download_status']=='verified')
    with pytest.raises(catalog.CatalogError):
        tech.download_tech_template({**entry,**change},tmp_path)


def test_download_reuses_bounded_exact_hash_downloader(monkeypatch,tmp_path):
    payload=b'%PDF-1.7\nverified mock'
    entry={'id':'mock','source_url':'https://official.example/file.pdf','filename':'mock.pdf','format':'PDF','allowed_hosts':['official.example'],'sha256':sha256(payload).hexdigest(),'download_status':'verified'}
    monkeypatch.setattr(tech,'list_tech_templates',lambda company=None:[deepcopy(entry)])
    class Response(BytesIO):
        headers={}
        def geturl(self):return entry['source_url']
    class Opener:
        def open(self,request,timeout):
            assert request.full_url==entry['source_url'] and timeout==30
            return Response(payload)
    monkeypatch.setattr(catalog,'build_opener',lambda handler:Opener())
    path=tech.download_tech_template(entry,tmp_path)
    assert path.read_bytes()==payload
    assert json.loads(path.with_suffix('.pdf.source.json').read_text(encoding='utf-8'))['sha256']==entry['sha256']


def test_unavailable_sources_block_without_attempting_private_access(tmp_path):
    for entry in tech.list_tech_templates():
        if entry['download_status']!='verified':
            with pytest.raises(catalog.CatalogError,match='검증'):
                tech.download_tech_template(entry,tmp_path)
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('case',['internal','reference_is_blank','fake_verified_form','address_without_source','wrong_format'])
def test_catalog_rejects_false_compatibility_or_origin_claims(case,monkeypatch,tmp_path):
    data=tech.load_tech_catalog();profile=data['companies'][0];entry=profile['documents'][0]
    if case=='internal':profile['internal_forms_status']='verified'
    elif case=='reference_is_blank':entry['resource_kind']='blank_form'
    elif case=='fake_verified_form':profile['status']='verified_public_form'
    elif case=='address_without_source':profile['location']['source_url']='http://untrusted.example'
    elif case=='wrong_format':entry['format']='DOCX'
    path=tmp_path/'tech.json';path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    monkeypatch.setattr(tech,'CATALOG_PATH',path)
    with pytest.raises(catalog.CatalogError):tech.load_tech_catalog()


def test_cli_lists_by_company_without_network(capsys):
    assert tech.main(['list','--company','넥슨'])==0
    entries=json.loads(capsys.readouterr().out)
    assert len(entries)==1 and entries[0]['resource_kind']=='layout_reference'
