"""Offline provenance and condition checks for two new EMA snapshots.

Source binaries are ignored and optional on another checkout. Missing originals
are explicit skips, never replaced by mock PDFs or remote downloads.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlsplit

from pypdf import PdfReader
import pytest

from evals.ra_public import cited, compare_values, normalized, read_source, validate_source_parser


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / 'evals/ra_extended_public_sources.json'
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
RECORDS = MANIFEST['sources']


def available(record):
    path = ROOT / record['path']
    if not path.is_file():
        pytest.skip('신규 공식 원자료 PDF 로컬 스냅샷 없음; 검증 성공으로 대체하지 않음')
    return path


def test_denominators_and_new_companies_are_not_reused_originals():
    assert MANIFEST['source_count'] == len(RECORDS) == 2
    assert MANIFEST['company_count'] == len({record['company']['name'] for record in RECORDS}) == 2
    assert MANIFEST['fact_count'] == sum(len(record['facts']) for record in RECORDS) == 17
    assert MANIFEST['source_page_count'] == sum(record['page_count'] for record in RECORDS) == 345
    assert MANIFEST['new_unique_original_count'] == len({record['sha256'] for record in RECORDS}) == 2
    assert MANIFEST['reused_sha_count'] == 0
    assert MANIFEST['actual_api_call_count'] == MANIFEST['human_kpi_observation_count'] == 0
    assert MANIFEST['model_training_performed'] is MANIFEST['live_llm_evaluated'] is False
    previous = []
    for path in (ROOT / 'evals').glob('ra_*sources.json'):
        if path != MANIFEST_PATH:
            previous.extend(json.loads(path.read_text(encoding='utf-8')).get('sources', []))
    assert not {record['sha256'] for record in RECORDS} & {record.get('sha256') for record in previous}
    assert not {record['company']['name'] for record in RECORDS} & {record.get('company', {}).get('name') for record in previous}


def test_official_urls_scope_and_complete_single_variant_annotations():
    for record in RECORDS:
        for key in ('source_page', 'source_url', 'final_url'):
            parsed = urlsplit(record[key])
            assert parsed.scheme == 'https' and parsed.hostname == 'www.ema.europa.eu'
        assert record['jurisdiction'] == 'EU'
        assert record['company']['role'] == 'marketing_authorisation_holder'
        assert record['current_version_verified'] is False
        assert record['scope']['korean_authorisation_verified'] is False
        assert record['scope']['domestic_applicant_inferred'] is False
        assert record['scope']['full_submission_ready'] is False
        assert record['pdf_access']['protection_bypass_performed'] is False
        assert len({fact['field_key'] for fact in record['facts']}) == len(record['facts'])
        assert [fact['value'] for fact in record['facts'] if fact['role'] == 'product_name'] == [record['product_variant']]
        for fact in record['facts']:
            assert fact['value'] == fact['exact_quote']
            assert fact['product_variant'] == record['product_variant']
            assert fact['complete_selected_paragraph'] is True
            assert fact['overflow_policy'] == 'preserve_in_annex_or_block_without_truncation'


@pytest.mark.parametrize('record', RECORDS, ids=lambda record: record['id'])
def test_real_source_role_page_and_quote_chain_is_immutable(record):
    path = available(record)
    before = sha256(path.read_bytes()).hexdigest()
    facts, sources, document = read_source(record, ROOT)
    assert before == record['sha256'] == sha256(path.read_bytes()).hexdigest()
    assert len(facts) == len(sources) == len(record['facts'])
    assert document['document_sha256'] == before
    assert all(source['company_name'] == record['company']['name'] for source in sources)
    assert all(source['company_role'] == 'marketing_authorisation_holder' for source in sources)
    assert all(source['product_variant'] == record['product_variant'] for source in sources)
    assert len({source['source_id'] for source in sources}) == len(facts)
    draft = {fact['field_key']: cited(fact['value'], fact['source_id']) for fact in facts}
    scores = compare_values(draft, facts, sources)
    assert scores['full_value_preserved_count'] == scores['cited_field_count'] == len(facts)
    assert scores['numeric_exact_count'] == scores['numeric_field_count']


@pytest.mark.parametrize('record', RECORDS, ids=lambda record: record['id'])
def test_actual_decoded_page_and_text_hashes_match_annotations(record):
    reader = PdfReader(available(record))
    for evidence in record['page_evidence']:
        page = reader.pages[evidence['page'] - 1]
        text_hash = sha256((page.extract_text() or '').encode('utf-8')).hexdigest()
        content_hash = sha256(page.get_contents().get_data()).hexdigest()
        assert text_hash == evidence['extracted_text_sha256']
        assert content_hash == evidence['decoded_content_stream_sha256']
        for fact in record['facts']:
            if fact['page'] == evidence['page']:
                assert fact['page_text_sha256'] == text_hash
                assert fact['page_content_stream_sha256'] == content_hash


@pytest.mark.parametrize('record', RECORDS, ids=lambda record: record['id'])
def test_production_m1_preserves_every_page_and_selected_quote_without_llm(record, monkeypatch):
    available(record)
    from llm.client import LLMClient

    def forbidden(*args, **kwargs):
        pytest.fail('원자료 검증에서 외부 모델을 호출하면 안 됨')

    monkeypatch.setattr(LLMClient, 'generate_json', forbidden)
    monkeypatch.setattr(LLMClient, 'embed', forbidden)
    result = validate_source_parser(record, ROOT)
    assert result['passed'] is True
    assert result['page_count'] == record['page_count']
    assert result['fact_count'] == len(record['facts'])
    assert result['original_unchanged'] is True


@pytest.mark.parametrize('record', RECORDS, ids=lambda record: record['id'])
def test_changed_amount_and_company_role_are_not_validated_as_verbatim(record):
    available(record)
    changed = deepcopy(record)
    composition = next(fact for fact in changed['facts'] if fact['field_key'] == '원료약품 및 분량')
    old, new = ('1.34 mg', '13.4 mg') if record['product_name'] == 'Ozempic' else ('2.5 mg', '25 mg')
    assert old in composition['value']
    composition['value'] = composition['exact_quote'] = composition['value'].replace(old, new)
    with pytest.raises(ValueError, match='전체 원문'):
        read_source(changed, ROOT)
    changed = deepcopy(record)
    changed['company']['role'] = 'manufacturer'
    with pytest.raises(ValueError, match='제조·수입·판매 역할'):
        read_source(changed, ROOT)


@pytest.mark.parametrize('record', RECORDS, ids=lambda record: record['id'])
def test_wrong_sha_page_variant_and_company_section_fail_closed(record):
    available(record)
    changed = deepcopy(record)
    changed['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='SHA-256'):
        read_source(changed, ROOT)
    changed = deepcopy(record)
    next(fact for fact in changed['facts'] if fact['field_key'] == '원료약품 및 분량')['page'] = 1
    with pytest.raises(ValueError, match='전체 원문'):
        read_source(changed, ROOT)
    changed = deepcopy(record)
    changed['product_variant'] = changed['product_variant'].replace('pen', 'syringe')
    with pytest.raises(ValueError, match='선택 제형'):
        read_source(changed, ROOT)
    changed = deepcopy(record)
    changed['company']['role_heading_page'] = 2
    with pytest.raises(ValueError, match='판매허가권자'):
        read_source(changed, ROOT)


def test_complete_dose_and_storage_conditions_keep_distinct_states_and_populations():
    ozempic, mounjaro = RECORDS
    oz = {fact['field_key']: fact for fact in ozempic['facts']}
    mj = {fact['field_key']: fact for fact in mounjaro['facts']}
    assert '0.25mgisnotamaintenancedose' in normalized(oz['용법 용량']['value'])
    assert 'atleast4weeks' in normalized(oz['용법 용량']['value'])
    assert 'Beforefirstuse3years' in normalized(oz['사용기간']['value'])
    assert '(4dosepens)6weeks' in normalized(oz['개봉 후 사용기간']['value'])
    assert '(8dosepens)' not in normalized(oz['개봉 후 사용기간']['value'])
    assert 'Storebelow30°C' in normalized(oz['개봉 후 보관방법']['value'])
    assert 'DonotfreezeOzempic' in normalized(oz['개봉 후 보관방법']['value'])
    assert 'protectitfromlight' in normalized(oz['개봉 후 보관방법']['value'])
    assert 'minimumof4weeksonthecurrentdose' in normalized(mj['용법 용량']['value'])
    assert 'Adults' in mj['용법 용량']['value'] and 'Paediatric population aged 10 years and above' in mj['용법 용량']['value']
    assert '15mgonceweekly' in normalized(mj['용법 용량']['value'])
    assert '10mgonceweekly' in normalized(mj['용법 용량']['value'])
    assert 'Beforeuse2years' in normalized(mj['사용기간']['value'])
    assert '21cumulativedays' in normalized(mj['냉장 외 보관기간']['value'])
    assert 'mustbediscarded' in normalized(mj['냉장 외 보관기간']['value'])
    assert 'KwikPen' not in mj['냉장 외 보관기간']['value']
    assert '2.4ml' not in mj['원료약품 및 분량']['value']
    assert '0.6ml' not in mj['원료약품 및 분량']['value']
