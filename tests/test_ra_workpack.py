"""Synthetic offline work-package checks; not native or legal RA validation."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile

import pytest
from agent.ra_workflows import prepare_ra_workflow
from agent.ra_workpack import build_ra_workpack, _csv


@pytest.fixture
def case():
    source_sha='a'*64
    profile={'format':'pdf','supported':True,'citation_mode':'sidecar','source_sha256':source_sha,
             'domain':'pharmaceutical_ra','ra_workflow':'product_approval','ra_product_name':'한미플루 30mg','ra_product_variant':'30mg',
             'fields':[{'id':'product','value_key':'제품명','label':'제품명','required':False,'input_required':False,'narrative_style_required':False},
                       {'id':'person','value_key':'신청인','label':'신청인','required':False,'input_required':True,'narrative_style_required':False},
                       {'id':'note','value_key':'비고','label':'비고','required':False,'input_required':True,'narrative_style_required':False}]}
    sources=[{'source_id':'S1','filename':'합성원자료.pdf','text':'한미플루 30mg','page':1,'document_sha256':'b'*64}]
    prepared=prepare_ra_workflow(profile,sources,source_bindings={'제품명':{'source_id':'S1','quote':'한미플루 30mg'}},direct_values={'신청인':'합성 사용자'})
    assert prepared['ready_for_output_check'],prepared['review']
    entry={'id':'product_approval','title':'합성 시험','source_sha256':source_sha,'profile_sha256':'c'*64,'field_count':3,
           'law_effective_date':'2026-03-05','form_printed_revision_date':'2024-10-04',
           'attachments':[{'id':'A1','title':'등록 안내 1','condition':'해당 시 직접 확인','source_quote':'제출서류 원문 합성 안내',
                           'source_sha256':source_sha,'page':2,'source_page':'https://example.invalid/official','review_required':True},
                          {'id':'A2','title':'등록 안내 2','condition':'해당 시 직접 확인','source_quote':'별도 첨부자료 원문 합성 안내',
                           'source_sha256':source_sha,'page':3,'source_page':'https://example.invalid/official','review_required':True}]}
    document=b'Explicit mock checked PDF bytes; no native PDF claim'
    proof={'status':'passed','format':'pdf','sha':{'template':source_sha,'output':sha256(document).hexdigest()}}
    sidecar={**deepcopy(prepared),'workflow_id':entry['id'],'output_verification':proof}
    export={'document':document,'filename':'합성_작성본.pdf','evidence':json.dumps(sidecar,ensure_ascii=False).encode()}
    return entry,profile,prepared,export


def build(case,**kwargs):
    return build_ra_workpack(*case,record_kind='synthetic_test',**kwargs)


def test_package_matches_registered_denominator_and_contains_bound_members(case):
    package=build(case)
    summary=package['summary']
    assert summary['registered_fields']==3 and summary['filled_fields']==2 and summary['unfilled_fields']==1
    assert summary['direct_fields']==2 and summary['filled_direct_fields']==1
    assert summary['filled_source_fields']==1 and summary['source_fields']==1
    assert summary['rows'][0]['source_locations']==['합성원자료.pdf / 1']
    assert not summary['submission_ready'] and not summary['human_kpi_measured'] and summary['user_edit_ratio'] is None
    assert summary['record_kind']=='synthetic_test' and summary['actual_model_requests']==0
    assert summary['attachments_pending']==2
    with ZipFile(BytesIO(package['zip_bytes'])) as archive:
        assert set(archive.namelist())=={'document/합성_작성본.pdf','evidence.json','work-status.json','work-status.csv','attachments.json','manifest.json'}
        manifest=json.loads(archive.read('manifest.json'))
        for name,proof in manifest['members'].items():
            actual=archive.read(name)
            assert proof['sha256']==sha256(actual).hexdigest() and proof['bytes']==len(actual)
        assert archive.read('document/합성_작성본.pdf')==case[3]['document']
    assert package['sha256']==sha256(package['zip_bytes']).hexdigest()


def test_package_deterministic_and_does_not_mutate_inputs(case):
    before=deepcopy(case)
    assert build(case)['zip_bytes']==build(case)['zip_bytes']
    assert case==before


@pytest.mark.parametrize('state',['checked','not_applicable'])
def test_nonpending_attachment_requires_actual_user_confirmation(case,state):
    with pytest.raises(ValueError,match='사용자 확인'):
        build(case,attachment_checks={'A1':{'status':state}})
    package=build(case,attachment_checks={'A1':{'status':state,'confirmed_by_user':True}})
    assert package['attachments'][0]['status']==state
    assert package['summary']['attachments_pending']==1
    assert not package['attachments'][0]['regulatory_applicability_determined']
    assert not package['summary']['submission_ready']


@pytest.mark.parametrize('checks',[{'unknown':{'status':'checked','confirmed_by_user':True}},
                                    {'A1':{'status':'approved','confirmed_by_user':True}},
                                    {'A1':{'status':'checked','confirmed_by_user':'yes'}},
                                    {'A1':{'status':'pending','comment':'private value'}},[]])
def test_unknown_attachment_or_invalid_state_blocked(case,checks):
    with pytest.raises(ValueError):build(case,attachment_checks=checks)


def test_same_source_notice_deduplicated_but_registered_ids_preserved(case):
    entry,*_=case
    duplicate=deepcopy(entry['attachments'][0]);duplicate['id']='A3'
    entry['attachments'].append(duplicate)
    package=build(case)
    assert package['summary']['attachments_registered']==3 and package['summary']['attachments_unique']==2
    assert package['attachments'][0]['registered_ids']==['A1','A3']
    assert package['attachments'][0]['source_quote']==entry['attachments'][0]['source_quote']
    assert package['attachments'][0]['version']['law_effective_date']=='2026-03-05'
    with pytest.raises(ValueError,match='상충'):
        build(case,attachment_checks={'A3':{'status':'checked','confirmed_by_user':True}})


@pytest.mark.parametrize('filename',['../leak.pdf','sub/leak.pdf',r'sub\leak.pdf','C:leak.pdf','CON.pdf','NUL.pdf','bad\n.pdf','bad.','bad '])
def test_member_path_injection_blocked(case,filename):
    case[3]['filename']=filename
    with pytest.raises(ValueError,match='파일명'):build(case)


def test_workflow_id_cannot_escape_zip_path(case):
    case[0]['id']='../escape'
    sidecar=json.loads(case[3]['evidence']);sidecar['workflow_id']='../escape'
    case[3]['evidence']=json.dumps(sidecar,ensure_ascii=False).encode()
    with pytest.raises(ValueError,match='파일명'):build(case)


@pytest.mark.parametrize('mutation',['document','fingerprint','source','profile','blocking','draft','product','denominator','output_sha','workflow'])
def test_stale_or_mismatched_preparation_and_output_blocked(case,mutation):
    entry,profile,prepared,export=case
    if mutation=='document':export['document']+=b'changed'
    elif mutation=='fingerprint':prepared['fingerprint']='f'*64
    elif mutation=='source':prepared['sources'][0]['text']='다른 제품'
    elif mutation=='profile':profile['fields'][0]['label']='다른 항목'
    elif mutation=='blocking':prepared['review']['blocking']=True
    elif mutation=='draft':prepared['draft']['제품명']='다른 제품 [S1]'
    elif mutation=='product':
        with pytest.raises(ValueError,match='제품'):build(case,selected_product={'product_name':'다른 제품','product_variant':'30mg'})
        return
    elif mutation=='denominator':entry['field_count']=999
    else:
        sidecar=json.loads(export['evidence'])
        if mutation=='output_sha':sidecar['output_verification']['sha']['output']='d'*64
        else:sidecar['workflow_id']='another'
        export['evidence']=json.dumps(sidecar,ensure_ascii=False).encode()
    with pytest.raises(ValueError):build(case)


def test_csv_formula_metadata_is_escaped_without_changing_json_labels():
    rows=[{'field_id':'=evil()','label':' +danger','status':'unfilled','input_kind':'source','registered_required':False,
           'source_ids':[],'source_locations':['@file.pdf / 1']}]
    output=_csv(rows).decode('utf-8-sig')
    assert "'=evil()" in output and "' +danger" in output and "'@file.pdf" in output
    assert rows[0]['label']==' +danger'


def test_no_cache_or_disk_write_or_external_model_request(case,monkeypatch):
    from pathlib import Path
    from llm.client import LLMClient
    def forbidden(*args,**kwargs):raise AssertionError('Work package must stay in memory')
    monkeypatch.setattr(Path,'write_bytes',forbidden)
    monkeypatch.setattr(Path,'write_text',forbidden)
    monkeypatch.setattr(LLMClient,'generate_json',forbidden)
    monkeypatch.setattr(LLMClient,'read_image_json',forbidden)
    monkeypatch.setattr(LLMClient,'embed',forbidden)
    assert build(case)['zip_bytes'].startswith(b'PK')

def test_selected_product_ui_names_and_variant_contract(case):
    package=build(case,selected_product={'name':'한미플루 30mg','variant':'30mg'})
    assert package['summary']['selected_product']=={'product_name':'한미플루 30mg','product_variant':'30mg'}


def test_missing_notice_ids_get_deterministic_workflow_local_names(case):
    from agent.ra_workpack import attachment_checklist
    entry=case[0]
    del entry['attachments'][0]['id']
    rows=attachment_checklist(entry)
    assert rows[0]['id']=='product_approval:attachment:1' and rows[0]['status']=='pending'
    package=build(case,attachment_checks={'product_approval:attachment:1':{'status':'checked','confirmed_by_user':True}})
    assert package['attachments'][0]['confirmed_by_user']
    assert 'id' not in entry['attachments'][0]


def test_invented_missing_fields_cannot_change_status_summary(case):
    case[2]['missing_fields']=[{'label':'임의 필수 조건'}]
    with pytest.raises(ValueError,match='현재 입력'):build(case)

@pytest.mark.parametrize('field,value',[('actual_model_requests',1),('mode','actual_ai'),('submission_ready',True)])
def test_sidecar_cannot_claim_actual_ai_requests_or_submission_readiness(case,field,value):
    sidecar=json.loads(case[3]['evidence']);sidecar[field]=value
    case[3]['evidence']=json.dumps(sidecar,ensure_ascii=False).encode()
    with pytest.raises(ValueError,match='현재 준비'):build(case)
