from copy import deepcopy

import pytest

from agent.business import BUSINESS_WORKFLOWS, inspect_business_draft


def inspect(text, source):
    return inspect_business_draft({'본문': text}, [{'source_id': 'S1', 'text': source}])


@pytest.mark.parametrize('currency', ['USD', 'EUR', 'JPY', 'CNY', 'KRW', 'GBP'])
def test_same_currency_and_value(currency):
    text = f'계약금액 {currency} 1,000.50'
    assert not inspect(text + ' [S1]', text)['blocking']


def test_currency_suffix_and_krw_unit_normalization():
    assert not inspect('계약금액 1,000 USD [S1]', '계약금액 USD 1000')['blocking']
    assert not inspect('정부지원금 KRW 10000000 [S1]', '정부지원금 1000만원')['blocking']


@pytest.mark.parametrize('draft,source', [('USD 1000', 'EUR 1000'), ('USD 1000', 'USD 100'), ('130000원', 'USD 100')])
def test_currency_or_amount_changes_block(draft, source):
    assert inspect('계약금액 ' + draft + ' [S1]', '계약금액 ' + source)['blocking']


def test_ambiguous_dollar_is_not_inferred():
    result = inspect('계약금액 $100 [S1]', '계약금액 $100')
    assert not result['blocking']
    assert any(item['code'] == 'business_ambiguous_currency' for item in result['issues'])


def test_project_scope_same_money_in_other_project_not_supported():
    source = '과제번호: P001 정부지원금 100만원; 과제번호: P002 정부지원금 200만원'
    assert inspect('과제번호: P001 정부지원금 200만원 [S1]', source)['blocking']
    assert not inspect('과제번호: P001 정부지원금 100만원 [S1]', source)['blocking']


def test_grant_and_contribution_not_swapped():
    source = '정부지원금 100만원; 자부담 현금 50만원; 자부담 현물 30만원'
    assert inspect('정부지원금 50만원 [S1]', source)['blocking']
    assert inspect('자부담 현물 50만원 [S1]', source)['blocking']
    assert not inspect('자부담 현금 50만원 [S1]', source)['blocking']


def test_vat_inclusion_preserved_and_unspecified_warns():
    assert inspect('계약금액 USD 100 VAT 포함 [S1]', '계약금액 USD 100 VAT 제외')['blocking']
    result = inspect('계약금액 USD 100 [S1]', '계약금액 USD 100 VAT 제외')
    assert not result['blocking']
    assert any(item['code'] == 'business_vat_unspecified' for item in result['issues'])


def test_plan_is_not_export_actual():
    assert inspect('수출 실적 USD 100 [S1]', '수출 목표 USD 100')['blocking']
    assert not inspect('수출 목표 USD 100 [S1]', '수출 목표 USD 100')['blocking']
    assert inspect('성과 목표 3건 달성함 [S1]', '성과 목표 3건 계획함')['blocking']


def test_incoterm_and_named_place():
    assert not inspect('조건 FOB Busan [S1]', '조건 FOB Busan')['blocking']
    assert inspect('조건 CIF Busan [S1]', '조건 FOB Busan')['blocking']
    assert inspect('조건 FOB Shanghai [S1]', '조건 FOB Busan')['blocking']


def test_hs_punctuation_normalizes_without_reclassification():
    assert not inspect('HS 코드: 8471.30 [S1]', 'HS CODE: 847130')['blocking']
    assert inspect('HS 코드: 8471.30 [S1]', 'HS CODE: 847150')['blocking']


def test_countries_and_common_aliases():
    assert not inspect('거래국가: 미국 [S1]', 'Country: United States')['blocking']
    assert inspect('거래국가: 일본 [S1]', '거래국가: 미국')['blocking']


def test_dates_do_not_use_other_project_or_other_event():
    source = '과제번호: P001 신청마감 2026-10-12; 과제번호: P002 신청마감 2026-10-13; 납기 2026-10-14'
    assert inspect('과제번호: P001 신청마감 2026-10-13 [S1]', source)['blocking']
    assert inspect('신청마감 2026-10-14 [S1]', source)['blocking']
    assert not inspect('과제번호: P001 신청마감 2026년 10월 12일 [S1]', source)['blocking']


@pytest.mark.parametrize('claim', ['승인 완료함', '선정됨', '보조금 수령함', '수출 완료함'])
def test_unsupported_positive_claims_block(claim):
    assert inspect(claim + ' [S1]', '사업 신청 예정임')['blocking']


def test_status_paraphrase_supported_but_negation_and_other_project_rejected():
    assert not inspect('사업 선정 완료함 [S1]', '지원대상으로 선정됨')['blocking']
    assert inspect('승인 완료함 [S1]', '승인 완료되지 않음')['blocking']
    assert inspect('Selected [S1]', 'Not selected')['blocking']
    assert not inspect('Not selected [S1]', 'Not selected')['blocking']
    assert inspect('과제번호: P001 선정됨 [S1]', '과제번호: P001 선정되지 않음; 과제번호: P002 선정됨')['blocking']


def test_signature_and_recipient_are_not_fabricated():
    result = inspect_business_draft({'서명': '홍길동 [S1]'}, [{'source_id': 'S1', 'text': '작성자 김철수'}])
    assert result['blocking']
    result = inspect_business_draft({'수취인': '홍길동 [S1]'}, [{'source_id': 'S1', 'text': '수취인 홍길동', 'filename': '사용자 입력'}])
    assert not result['blocking']


def test_cited_sources_only_and_no_mutations_or_calculation():
    draft = {'정부지원금': '100만원 [S1]', '합계': '150만원 [S1]'}
    sources = [{'source_id': 'S1', 'text': '정부지원금 100만원; 자부담 50만원'}, {'source_id': 'S2', 'text': '합계 150만원'}]
    original = deepcopy((draft, sources))
    result = inspect_business_draft(draft, sources)
    assert result['blocking']  # No inferred sum, and uncited S2 does not support it.
    assert (draft, sources) == original


def test_workflows_are_advisory_not_eligibility_certification():
    assert set(BUSINESS_WORKFLOWS) == {'trade_sales', 'overseas_business', 'government_grant', 'rd_project'}
    for workflow in BUSINESS_WORKFLOWS:
        result = inspect_business_draft({}, [], profile={'business_workflow': workflow})
        assert not result['blocking']
        assert all('추가 확인사항' in item for item in result['additional_checks'])
        assert any(check['status'] == 'not_certified' for check in result['checks'])


@pytest.mark.parametrize('draft,sources,profile', [({'본문': 3}, [], None), ({}, {}, None), ({}, [], []),
    ({}, [{'source_id': 'S1', 'text': ''}, {'source_id': 'S1', 'text': ''}], None), ({}, [], {'business_workflow': []})])
def test_invalid_inputs_rejected(draft, sources, profile):
    with pytest.raises(ValueError):
        inspect_business_draft(draft, sources, profile=profile)
