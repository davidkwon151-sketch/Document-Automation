"""공식 기업 공개 서식의 읽기·부분 기입 검사. 공급/승인·제출 인증이 아님."""

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from pypdf import PdfReader
import pytest

from agent.output_check import verify_output
from parsers import parse_file
from templates import TemplateError, analyze_template, fill_compatible_template, load_form_profile


ROOT = Path(__file__).resolve().parents[1]


def _manifest():
    return json.loads((ROOT/'evals/ra_corporate_sources.json').read_text(encoding='utf-8'))


def _source(entry):
    path = ROOT/entry['source_path']
    if not path.is_file():
        pytest.skip('공식 기업 원본 corpus를 별도로 확보해야 함')
    return path


def _assert_page_text_preserved(parsed, reader):
    """독립 PDF 판독과 쪽별 문자/빈도를 대조하되 공백·읽기순서는 허용함."""
    blocks = parsed['페이지/시트 정보']
    assert len(blocks) == len(reader.pages)
    assert [block['페이지'] for block in blocks] == list(range(1, len(reader.pages) + 1))
    for block, page in zip(blocks, reader.pages):
        actual = re.sub(r'\s+', '', block['본문'])
        original = re.sub(r'\s+', '', page.extract_text() or '')
        assert Counter(actual) == Counter(original)
        order = block.get('PDF 읽기순서')
        if order:
            # 읽기순서가 다른 실제 원본은 의미 연결 확인 경고도 보존해야 함.
            assert order['확인 필요'] is True
            assert order['문자와 빈도 보존'] is True
    assert parsed['본문'] == '\n'.join(block['본문'] for block in blocks if block['본문'].strip())


@pytest.mark.parametrize('company,pages,fields', [('Pfizer Canada',12,95), ('Roche',6,0), ('Terumo Medical Corporation',3,0)])
def test_three_official_blank_forms_keep_all_pages_and_canonical_fields(company,pages,fields):
    entry=next(item for item in _manifest()['documents'] if item['company']==company)
    source=_source(entry)
    before=source.read_bytes()
    assert sha256(before).hexdigest()==entry['sha256']
    parsed=parse_file(source)
    assert set(parsed)=={'파일명','본문','표 목록','페이지/시트 정보'}
    assert len(parsed['페이지/시트 정보'])==pages
    reader=PdfReader(source)
    assert len(reader.pages)==pages
    _assert_page_text_preserved(parsed, reader)
    canonical=reader.get_fields() or {}
    assert len(canonical)==fields==entry['canonical_field_count']
    assert set(canonical)==set(entry['canonical_fields'])
    assert source.read_bytes()==before
    assert entry['resource_kind']=='blank_form'
    assert entry['corporate_form'] is True
    assert entry['private_internal_template_verified'] is False
    assert entry['submission_ready'] is False
    assert entry['full_form_filled'] is False


@pytest.mark.parametrize('damage', ['missing_page', 'changed_version_digit', 'moved_between_pages'])
def test_independent_corporate_text_check_detects_loss_changes_and_wrong_page(damage):
    entry = next(item for item in _manifest()['documents'] if item['company'] == 'Roche')
    source = _source(entry)
    parsed = deepcopy(parse_file(source))
    blocks = parsed['페이지/시트 정보']
    if damage == 'missing_page':
        blocks.pop()
    elif damage == 'changed_version_digit':
        assert '2023' in blocks[0]['본문']
        blocks[0]['본문'] = blocks[0]['본문'].replace('2023', '2024', 1)
    else:
        blocks[0]['본문'], blocks[1]['본문'] = blocks[1]['본문'], blocks[0]['본문']
    parsed['본문'] = '\n'.join(block['본문'] for block in blocks if block['본문'].strip())
    with pytest.raises(AssertionError):
        _assert_page_text_preserved(parsed, PdfReader(source))


def test_research_counts_do_not_turn_downloads_into_submission_or_private_internal_proof():
    manifest=_manifest()
    summary=manifest['summary']
    assert summary['downloaded_corporate_public_blank_forms']==3
    assert summary['unique_sha256']==len({entry['sha256'] for entry in manifest['documents']})==3
    assert summary['partial_fill_verified_forms']==1
    assert summary['registered_fields']==33
    assert summary['full_form_filled']==summary['private_internal_template_verified']==0
    assert {item['company'] for item in manifest['research']}=={
        'Pfizer','Roche','Novartis','Sanofi','Yuhan','Hanmi','Celltrion','Terumo'}
    assert all(item['queries'] and item['search_urls'] and item['result'] for item in manifest['research'])


def test_pfizer_usage_rights_certification_remains_intact_and_blocks_current_filler(tmp_path):
    entry=next(item for item in _manifest()['documents'] if item['company']=='Pfizer Canada')
    source=_source(entry)
    before=source.read_bytes()
    assert '/UR3' in PdfReader(source).trailer['/Root']['/Perms']
    profile=analyze_template(source)
    assert profile['render_mode']=='signed'
    assert profile['supported'] is False
    result=tmp_path/'cannot_rewrite_signed.pdf'
    with pytest.raises(TemplateError):
        fill_compatible_template(source, {'Name':'TEST ONLY'}, result, profile=profile)
    assert not result.exists()
    assert source.read_bytes()==before


def test_roche_profile_only_exposes_supplier_text_not_internal_decision_or_signature(tmp_path):
    entry=next(item for item in _manifest()['documents'] if item['company']=='Roche')
    source=_source(entry)
    profile=load_form_profile(source, entry['profile_id'])
    assert profile['source_path']==entry['source_path']
    assert profile['citation_mode']=='sidecar'
    assert profile['current_version_verified'] is False
    assert len(profile['fields'])==33
    assert {field['page'] for field in profile['fields']}=={1,2,5,6}
    assert all(field['author_role']=='supplier' for field in profile['fields'])
    assert all(field['narrative_style_required'] is False for field in profile['fields'])
    assert not any(token in field['label'] for field in profile['fields'] for token in ['Signature','Decision','Accepted','Rejected'])
    assert all(field['y']+field['height']<234 for field in profile['fields'] if field['page']==6)
    assert all(field['input_mode']=='user_provided' for field in profile['fields'] if field.get('identifier_role') or field['label'].startswith('Supplier Contact'))
    assert profile['verification']['visual_qa']['reviewed_pages']==[1,2,3,4,5,6]
    result=fill_compatible_template(source,profile['demo_values'],tmp_path/'roche_fake.pdf',profile=profile)
    report=verify_output(source,result,profile['demo_values'],profile=profile)
    assert report['status']=='passed'
    assert next(check for check in report['checks'] if check['name']=='pdf_overlay_outside_regions_unchanged')['pages']==6


def test_three_complete_ema_names_cross_fill_without_inferring_a_roche_supply_relationship(tmp_path):
    entry=next(item for item in _manifest()['documents'] if item['company']=='Roche')
    source=_source(entry)
    profile=load_form_profile(source,entry['profile_id'])
    sidecar=json.loads((ROOT/'outputs/ra_corporate_form_qa/roche_ema_cross_fill_sidecar.json').read_text(encoding='utf-8'))
    assert sidecar['proves_supply_or_change'] is False
    assert sidecar['submission_ready'] is False
    values={}
    for citation in sidecar['citations']:
        original=ROOT/citation['source_path']
        if not original.is_file():
            pytest.skip('공식 EMA 제품정보 corpus를 별도로 확보해야 함')
        assert sha256(original.read_bytes()).hexdigest()==citation['source_sha256']
        page=PdfReader(original).pages[citation['page']-1].extract_text()
        assert re.sub(r'\s+','',citation['exact_quote']) in re.sub(r'\s+','',page)
        assert citation['original_scope']['korean_authorisation_verified'] is False
        assert citation['proves_supply_or_change'] is False
        values[citation['field_key']]=citation['value']
    assert set(values)=={'제품명','제품명 2','제품명 3'}
    before=source.read_bytes()
    result=fill_compatible_template(source,values,tmp_path/'names_reference_only.pdf',profile=profile)
    assert verify_output(source,result,values,profile=profile)['status']=='passed'
    text=PdfReader(result).pages[0].extract_text()
    assert all(value in text for value in values.values())
    assert 'TEST-000' not in text
    assert source.read_bytes()==before


def test_long_corporate_text_blocks_instead_of_truncating_or_overwriting(tmp_path):
    entry=next(item for item in _manifest()['documents'] if item['company']=='Roche')
    source=_source(entry)
    profile=load_form_profile(source,entry['profile_id'])
    assert 'overflow_mode' not in profile
    before=source.read_bytes()
    output=tmp_path/'previous.pdf'
    output.write_bytes(b'PREVIOUS USER OUTPUT')
    with pytest.raises(TemplateError,match='길이|제한|넘'):
        fill_compatible_template(source,{'제품명':'A'*81},output,profile=profile)
    assert output.read_bytes()==b'PREVIOUS USER OUTPUT'
    assert source.read_bytes()==before
