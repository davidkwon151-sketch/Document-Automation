from copy import deepcopy

import pytest

from agent.office import OFFICE_WORKFLOWS, inspect_office_draft


def inspect(text, source):
    return inspect_office_draft({'본문': text}, [{'source_id': 'S1', 'text': source}])


@pytest.mark.parametrize('amount', ['1000000원', '1000천원', '1백만원'])
def test_won_scale_is_equivalent_without_modifying(amount):
    assert not inspect('매출 ' + amount + ' [S1]', '매출 1백만원')['blocking']


def test_currency_mismatch_blocks_no_forex_conversion():
    assert inspect('매출 USD 100 [S1]', '매출 EUR 100')['blocking']
    assert inspect('매출 130000원 [S1]', '매출 USD 100')['blocking']


def test_consolidated_and_separate_are_distinct():
    source = '연결 매출 100억원; 별도 매출 80억원'
    assert not inspect('연결 매출 100억원 [S1]', source)['blocking']
    assert inspect('별도 매출 100억원 [S1]', source)['blocking']


def test_wrong_year_and_quarter_cannot_reuse_same_number():
    source = '2025년 1분기 매출 100억원; 2026년 2분기 매출 200억원'
    assert inspect('2026년 2분기 매출 100억원 [S1]', source)['blocking']
    assert not inspect('2026년 2분기 매출 200억원 [S1]', source)['blocking']
    assert not inspect('2026년 2분기 매출 200억원 [S1]', 'FY2026 Q2 매출 200억원')['blocking']


def test_revenue_operating_and_net_profit_not_swapped():
    source = '매출 100억원; 영업이익 20억원; 순이익 10억원'
    assert inspect('영업이익 100억원 [S1]', source)['blocking']
    assert inspect('매출 20억원 [S1]', source)['blocking']
    assert not inspect('당기순이익 10억원 [S1]', source)['blocking']


def test_budget_is_not_actual_spend_or_confirmed_revenue():
    assert inspect('집행액 100만원 [S1]', '예산 100만원')['blocking']
    assert inspect('매출 확정 실적 100억원 [S1]', '매출 전망 100억원')['blocking']
    assert not inspect('예산 100만원 [S1]', '예산 100만원')['blocking']
    assert inspect('매출 확정 실적 100억원 [S1]', '매출 잠정 100억원')['blocking']
    assert inspect('매출 확정 실적 100억원 [S1]', '매출 100억원')['blocking']


@pytest.mark.parametrize('unit', ['%', '%p', 'bp', 'bps', '퍼센트포인트'])
def test_grounded_rate_units(unit):
    source = f'금리 2 {unit}'
    assert not inspect(source + ' [S1]', source)['blocking']


def test_percent_percentage_point_and_bp_are_not_interchanged():
    assert inspect('금리 2% [S1]', '금리 2%p')['blocking']
    assert inspect('금리 2%p [S1]', '금리 2bp')['blocking']
    assert inspect('금리 1%p [S1]', '금리 100bp')['blocking']
    assert not inspect('금리 2bp [S1]', '금리 2bps')['blocking']


def test_request_is_not_approval_and_negative_source_not_supporting():
    assert not inspect('구매 승인 요청함 [S1]', '구매 승인 요청함')['blocking']
    assert inspect('구매 승인 완료함 [S1]', '구매 승인 요청함')['blocking']
    assert inspect('구매 승인 완료함 [S1]', '구매 승인 완료되지 않음')['blocking']
    assert not inspect('구매 승인 완료함 [S1]', '구매 승인됨')['blocking']


def test_quality_plan_is_not_actual_and_batch_matters():
    assert inspect('검사 실적 100건 [S1]', '검사 계획 100건')['blocking']
    source = 'Batch No: B001 검사 결과 10건; Batch No: B002 검사 결과 20건'
    assert inspect('Batch No: B001 검사 결과 20건 [S1]', source)['blocking']
    assert not inspect('Batch No: B001 검사 결과 10건 [S1]', source)['blocking']


def test_capa_completion_needs_actual_evidence():
    assert inspect('시정조치 완료함 [S1]', '시정조치 계획함')['blocking']
    assert not inspect('시정조치 완료함 [S1]', '시정조치 완료됨')['blocking']
    assert inspect('시험번호: T001 검사 완료함 [S1]', '시험번호: T001 검사 계획함; 시험번호: T002 검사 완료함')['blocking']
    assert inspect('시정조치 완료함 [S1]', '검사 완료함')['blocking']


def test_identifier_kind_and_guarantee_project_preserved():
    assert inspect('과제번호: P001 [S1]', '시험번호: P001')['blocking']
    assert inspect('과제번호: P001 수익 보장됨 [S1]', '과제번호: P001 수익 보장되지 않음; 과제번호: P002 수익 보장됨')['blocking']


def test_guarantee_unsupported_blocks_and_source_claim_remains_warning():
    assert inspect('수익 보장됨 [S1]', '투자 검토 중')['blocking']
    assert inspect('수익 보장됨 [S1]', '수익 보장되지 않음')['blocking']
    assert not inspect('수익 보장되지 않음 [S1]', '수익 보장되지 않음')['blocking']
    result = inspect('수익 보장됨 [S1]', '수익 보장됨')
    assert not result['blocking']
    assert any(item['code'] == 'office_guarantee_conditions' for item in result['issues'])


def test_no_inferred_totals_uncited_sources_or_input_mutation():
    draft = {'합계': '150만원 [S1]'}
    sources = [{'source_id': 'S1', 'text': '출장비 100만원; 구매비 50만원'}, {'source_id': 'S2', 'text': '합계 150만원'}]
    original = deepcopy((draft, sources))
    assert inspect_office_draft(draft, sources)['blocking']
    assert (draft, sources) == original


def test_fixed_field_and_english_preserved():
    draft = {'Operating profit': 'USD 20 [S1]'}
    assert not inspect_office_draft(draft, [{'source_id': 'S1', 'text': 'Operating income USD 20'}])['blocking']


def test_workflow_checklists_are_advisory():
    assert set(OFFICE_WORKFLOWS) == {'office_planning', 'financial_report', 'daily_approval', 'industrial_quality'}
    for workflow in OFFICE_WORKFLOWS:
        result = inspect_office_draft({}, [], profile={'office_workflow': workflow})
        assert not result['blocking']
        assert all('추가 확인사항' in item for item in result['additional_checks'])
        assert any(item['status'] == 'not_certified' for item in result['checks'])


@pytest.mark.parametrize('draft,sources,profile', [({'본문': 1}, [], None), ({}, {}, None), ({}, [], []),
    ({}, [{'source_id': 'S1', 'text': ''}, {'source_id': 'S1', 'text': ''}], None), ({}, [], {'office_workflow': []})])
def test_invalid_inputs_rejected(draft, sources, profile):
    with pytest.raises(ValueError):
        inspect_office_draft(draft, sources, profile=profile)
