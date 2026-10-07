"""Real Korean product title strength; not a dosage or company-role approval."""
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path

import pytest

from agent.ra import inspect_ra_draft, _korean_strength_heading
from agent.ra_workflows import prepare_ra_workflow
from agent.retrieve import load_documents, chunk_documents


@lru_cache(maxsize=1)
def originals():
    path = Path(__file__).resolve().parents[1] / 'data/ra_public_validation/kr/hanmi_hanmiflu.pdf'
    record = json.loads(path.with_suffix('.pdf.source.json').read_text(encoding='utf-8'))
    assert sha256(path.read_bytes()).hexdigest() == record['sha256']
    return chunk_documents(load_documents([path], allow_ocr=False))


def original(strength):
    quote = f'한미플루 {strength}mg'
    return deepcopy(next(s for s in originals() if s['page'] == 1 and s['text'] == quote))


def profile(strength):
    return {'ra_workflow': 'product_approval', 'ra_product_name': '한미플루',
            'ra_product_variant': f'{strength}mg', 'fields': [
                {'id': '제품명', 'value_key': '제품명', 'label': '제품명',
                 'evidence_role': 'product_name', 'input_required': False, 'required': True}]}


@pytest.mark.parametrize('strength', [30, 45, 75])
def test_actual_title_from_actual_parser_unique_source_is_ready(strength):
    source = original(strength)
    result = prepare_ra_workflow(profile(strength), [source],
        {'제품명': {'source_id': source['source_id'], 'quote': source['text']}})
    assert result['ready_for_output_check'], result['review']
    evidence = result['evidence']['제품명']
    assert evidence['source']['page'] == 1
    assert evidence['source']['document_sha256'] == source['document_sha256']
    assert evidence['source']['context_text'][source['context_start']:source['context_end']] == source['text']
    assert result['actual_model_requests'] == 0 and not result['submission_ready']


@pytest.mark.parametrize('strength,selected', [(30,45),(30,75),(45,30),(45,75),(75,30),(75,45)])
def test_actual_wrong_selected_strength_remains_blocked(strength, selected):
    source = original(strength)
    result = prepare_ra_workflow(profile(selected), [source],
        {'제품명': {'source_id': source['source_id'], 'quote': source['text']}})
    assert result['review']['blocking']
    assert any(i['code'] in {'ra_variant_mismatch', 'ra_quantity_mismatch'} for i in result['review']['issues'])


@pytest.mark.parametrize('strength', [30,45,75])
def test_invented_wrong_dose_cannot_use_real_title_as_support(strength):
    source = original(strength)
    p = profile(strength)
    p['fields'] = [{'id':'dose','value_key':'용법 용량','label':'용법 용량','required':True}]
    result = inspect_ra_draft({'용법 용량': f'권장 투여 용량 999 mg [{source["source_id"]}]'}, [source], profile=p)
    assert result['blocking']
    assert any(i['code'] == 'ra_quantity_mismatch' for i in result['issues'])


def test_company_role_not_supported_by_title_or_other_unrelated_section():
    source = original(30)
    # Actual public source registration declares publisher, not manufacturer.
    # This metadata must not turn the page-1 product heading into role evidence.
    source.update(company_name='한미약품(주)', company_role='document_publisher')
    result = inspect_ra_draft({'제조원':f'한미약품(주) [{source["source_id"]}]'}, [source],
        profile={'ra_workflow':'product_approval','fields':[{'value_key':'제조원','label':'제조원'}]})
    assert result['blocking']
    assert any(i['code'] == 'ra_company_role_unconfirmed' for i in result['issues'])


def test_title_context_scope_forgery_not_accepted():
    source = original(30)
    source['context_end'] += 1
    with pytest.raises(ValueError, match='문자 범위'):
        prepare_ra_workflow(profile(30), [source],
            {'제품명': {'source_id':source['source_id'],'quote':source['text']}})


@pytest.mark.parametrize('line', ['한미플루 권장 용량 30mg', '한미플루 30mg 복용',
                                 '다른약품 30mg', '한미플루 30mg 45mg', '한미플루 함량 30mg'])
def test_only_known_single_strength_title_is_classified(line):
    assert not _korean_strength_heading(line, {'한미플루'})
