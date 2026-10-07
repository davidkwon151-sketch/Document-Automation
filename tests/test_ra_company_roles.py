"""Operational RA role checks must not turn an issuer into a manufacturer."""

import pytest

from agent.ra import inspect_ra_draft


def source(role='document_publisher', text='Alpha tablets'):
    return {'source_id': 'S1', 'text': text, 'company_name': 'Example Pharma Ltd.',
            'company_role': role, 'product_name': 'Alpha', 'product_variant': 'Alpha tablets'}


@pytest.mark.parametrize(('label', 'role'), [
    ('제조원', 'manufacturer'), ('수입원', 'importer'), ('판매원', 'distributor'),
    ('판매허가권자', 'marketing_authorisation_holder'), ('문서 발행사', 'document_publisher'),
    ('Marketing authorization holder', 'marketing_authorisation_holder')])
def test_correct_single_company_role_and_publisher_misclassification(label, role):
    draft = {label: 'Example Pharma Ltd. [S1]'}
    assert not inspect_ra_draft(draft, [source(role)])['blocking']
    other = 'manufacturer' if role == 'document_publisher' else 'document_publisher'
    result = inspect_ra_draft(draft, [source(other)])
    assert result['blocking']
    assert any(issue['code'] == 'ra_company_role_unconfirmed' for issue in result['issues'])


def test_explicit_prose_role_mismatch_is_checked_without_numbers():
    draft = {'본문': 'Example Pharma Ltd.가 제조원임 [S1]'}
    result = inspect_ra_draft(draft, [source('marketing_authorisation_holder')])
    assert result['blocking'] and result['issues'][0]['code'] == 'ra_company_role_unconfirmed'


@pytest.mark.parametrize('line', ['Example Pharma Ltd. 제조원 여부 추가 확인 필요함',
                                 'Example Pharma Ltd. is not the manufacturer',
                                 'Example Pharma Ltd. 제조원이 아님',
                                 'Example Pharma Ltd. 제조원인지 미확인임'])
def test_negated_and_pending_statements_are_not_positive_role_assertions(line):
    assert not inspect_ra_draft({'본문': line + ' [S1]'}, [source()])['blocking']


def test_multiple_roles_do_not_infer_which_role_belongs_to_which_company():
    result = inspect_ra_draft({'본문': 'Example Pharma Ltd. 제조원과 판매허가권자 비교 검토함 [S1]'}, [source()])
    assert not any(issue['code'] == 'ra_company_role_unconfirmed' for issue in result['issues'])


def test_role_proof_from_uncited_other_company_is_not_used():
    other = {**source('manufacturer'), 'source_id': 'S2'}
    result = inspect_ra_draft({'제조원': 'Example Pharma Ltd. [S1]'}, [source(), other])
    assert result['blocking']


def test_exact_cited_role_statement_has_priority_over_broad_document_role():
    text = 'Manufacturer: Example Pharma Ltd.'
    assert not inspect_ra_draft({'본문': text + ' [S1]'}, [source(text=text)])['blocking']


def test_missing_metadata_does_not_manufacture_an_inferred_company_role():
    result = inspect_ra_draft({'제조원': 'Example Pharma Ltd. [S1]'}, [{'source_id': 'S1', 'text': 'Alpha tablets'}])
    assert not any(issue['code'] == 'ra_company_role_unconfirmed' for issue in result['issues'])


def test_bare_company_name_copy_does_not_prove_the_wrong_field_role():
    evidence = source(text='문서 발행사: Example Pharma Ltd.')
    result = inspect_ra_draft({'제조원': 'Example Pharma Ltd. [S1]'}, [evidence])
    assert result['blocking']
    assert any(issue['code'] == 'ra_company_role_unconfirmed' for issue in result['issues'])


@pytest.mark.parametrize('line', [
    'Example Pharma Ltd. 제조원임; 허가 상태는 미확인임',
    'Example Pharma Ltd. 제조원임 허가 상태는 미확인임',
    'Example Pharma Ltd. is the manufacturer; approval status is unknown',
    'Example Pharma Ltd. 제조원임; 수입원은 미확인임',
])
def test_other_pending_claim_cannot_hide_an_affirmative_company_role(line):
    result = inspect_ra_draft({'본문': line + ' [S1]'}, [source(text='문서 발행사: Example Pharma Ltd.')])
    assert any(issue['code'] == 'ra_company_role_unconfirmed' for issue in result['issues'])


@pytest.mark.parametrize('text', ['Manufacturer: Example Pharma Ltd.', 'Example Pharma Ltd. 제조원'])
def test_bare_role_field_can_use_adjacent_explicit_role_evidence(text):
    result = inspect_ra_draft({'제조원': 'Example Pharma Ltd. [S1]'}, [source(text=text)])
    assert not any(issue['code'] == 'ra_company_role_unconfirmed' for issue in result['issues'])


def test_separate_role_and_company_in_source_are_not_combined_as_proof():
    text = 'Manufacturer: Other Company; 문서 발행사: Example Pharma Ltd.'
    result = inspect_ra_draft({'제조원': 'Example Pharma Ltd. [S1]'}, [source(text=text)])
    assert any(issue['code'] == 'ra_company_role_unconfirmed' for issue in result['issues'])


@pytest.mark.parametrize('other', ['Example Pharma Ltd. Korea Branch', 'Other Example Pharma Ltd.'])
def test_longer_corporate_name_is_not_a_same_company_role_proof(other):
    text = f'Manufacturer: {other}; 문서 발행사: Example Pharma Ltd.'
    result = inspect_ra_draft({'제조원': 'Example Pharma Ltd. [S1]'}, [source(text=text)])
    assert any(issue['code'] == 'ra_company_role_unconfirmed' for issue in result['issues'])


@pytest.mark.parametrize('line', ['Example Pharma Ltd. is not a manufacturer',
                                 "Example Pharma Ltd. isn't the manufacturer",
                                 'Example Pharma Ltd. is not an importer',
                                 'Example Pharma Ltd. isn’t a distributor'])
def test_local_english_article_and_contraction_negation_is_preserved(line):
    result = inspect_ra_draft({'본문': line + ' [S1]'}, [source()])
    assert not any(issue['code'] == 'ra_company_role_unconfirmed' for issue in result['issues'])
