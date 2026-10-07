"""공식 사무·재무·품질 서식의 실제 지정 입력칸과 비기입 위치를 확인함."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZipFile

from lxml import etree
from pypdf import PdfReader
import pytest

from agent.output_check import verify_output
from templates import TemplateError, fill_compatible_template, load_form_profile

ROOT=Path(__file__).resolve().parents[1]
IDS=('office_treasury_voucher','office_medical_balance','office_bosch_change_proposal')


def _source_profile(entry_id):
    profile=json.loads((ROOT/f'templates/profiles/{entry_id}.json').read_text(encoding='utf-8'))
    directory='international' if profile['format']=='pptx' else 'office'
    source=ROOT/'data/public_templates'/directory/profile['source_filename']
    if not source.is_file():
        pytest.skip('공식 원본 별도 corpus')
    return source,profile


@pytest.mark.parametrize('entry_id',IDS)
def test_office_profiles_use_exact_source_and_safe_fixed_field_contract(entry_id):
    source,profile=_source_profile(entry_id)
    assert load_form_profile(source,entry_id)==profile
    assert sha256(source.read_bytes()).hexdigest()==profile['source_sha256']
    assert profile['domain']=='office_finance'
    assert profile['office_workflow'] in {'office_planning','financial_report','daily_approval','industrial_quality'}
    assert profile['resource_kind']=='blank_form'
    assert profile['submission_ready'] is False and profile['full_form_filled'] is False
    assert '실제 제출용' in profile['demo_notice']
    assert profile['verification']['legal_compliance_certified'] is False
    for field in profile['fields']:
        assert field['input_required']==(field['input_mode']=='user_provided')
        assert field['narrative_style_required'] is False
        assert field['value_key'] in profile['demo_values']


@pytest.mark.parametrize('entry_id',IDS)
def test_office_partial_fill_independently_preserves_original_and_values(entry_id,tmp_path):
    source,profile=_source_profile(entry_id)
    before=source.read_bytes()
    output=fill_compatible_template(source,profile['demo_values'],tmp_path/source.name,profile=profile)
    assert verify_output(source,output,profile['demo_values'],profile=profile)['status']=='passed'
    assert source.read_bytes()==before
    if profile['format']=='pdf':
        original,result=PdfReader(source),PdfReader(output)
        assert len(original.pages)==len(result.pages)==1
        original_text=original.pages[0].extract_text()
        result_text=result.pages[0].extract_text()
        assert original_text in result_text
        assert '0.00' in result_text
    else:
        with ZipFile(source) as original,ZipFile(output) as result:
            for part in original.namelist():
                if part!='ppt/slides/slide2.xml':
                    assert original.read(part)==result.read(part),part
            root=etree.fromstring(result.read('ppt/slides/slide2.xml'))
            ns={k:v for k,v in root.nsmap.items() if k}
            # 승인자 성명·서명은 생성하지 않으며 원본 승인 표의 결과 칸은 비워 둠.
            approval=root.xpath('//p:graphicFrame[2]//a:tr[2]/a:tc[2]//a:t/text() | //p:graphicFrame[2]//a:tr[4]/a:tc[2]//a:t/text()',namespaces=ns)
            assert not ''.join(approval).strip()


def test_financial_period_currency_and_decimal_text_are_not_rewritten(tmp_path):
    source,profile=_source_profile('office_medical_balance')
    assert profile['amount_unit']=='원'
    assert {x['reporting_period'] for x in profile['fields']}=={'당기','전기'}
    assert all(x['data_type']=='amount' and x['amount_unit']=='원' for x in profile['fields'])
    values=deepcopy(profile['demo_values'])
    key=profile['fields'][0]['value_key']
    values[key]='000.00'
    output=fill_compatible_template(source,values,tmp_path/'amount.pdf',profile=profile)
    assert '000.00' in PdfReader(output).pages[0].extract_text()
    assert verify_output(source,output,values,profile=profile)['status']=='passed'


def test_office_pdf_rejects_overflow_before_saving(tmp_path):
    source,profile=_source_profile('office_treasury_voucher')
    field=next(x for x in profile['fields'] if x['label']=='지출결의 작성일자')
    values=deepcopy(profile['demo_values']); values[field['value_key']]='긴'*(field['max_chars']+1)
    target=tmp_path/'overflow.pdf'
    with pytest.raises(TemplateError,match='길이|글자|초과|넘침'):
        fill_compatible_template(source,values,target,profile=profile)
    assert not target.exists()
