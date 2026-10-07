"""Public real PDF/parser positives; all DSUR positive fixtures are synthetic."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from functools import lru_cache
from agent.ra_scope import qualify_natural_scope
from agent.retrieve import chunk_documents
from parsers.extract import parse_pdf
from pypdf import PdfReader

HERE = Path(__file__).resolve().parent / 'fixtures/ra_protocol_public'
PRODUCT = 'SyntheticDrugA'
LINE = PRODUCT + ' is under evaluation; no final safety conclusion is established.'


def source(context, quote=LINE, **extra):
    start = context.index(quote)
    return {'filename': 'synthetic-public-format.pdf', 'page': 1, 'document_sha256': 'a' * 64,
            'text': quote, 'context_text': context, 'context_start': start,
            'context_end': start + len(quote), **extra}


def protocol(section='2. Study Objectives', body=LINE, trailing='3. Study Design\nOther text'):
    return source(f'Clinical Study Protocol TEST-0001 Original\n{section}\n{body}\n{trailing}', body)


def dsur(section='1. Introduction', body=LINE, prefix=''):
    return source(f'{prefix}Development Safety Update Report\nDSUR number: 4\n'
                  f'Reporting period: 2025-01-01 to 2025-12-31\n{section}\n{body}\n'
                  '2. Worldwide Marketing Approval Status\nOther text', body)


def qualify(record, label='임상시험 목적', **kwargs):
    return qualify_natural_scope(record, label, expected_product=PRODUCT, **kwargs)


@pytest.mark.parametrize('section,label', [('2. Study Objectives', '임상시험 목적'),
    ('2.1. Primary Objective', '임상시험 목적'), ('2. 임상시험 목적', '임상시험 목적'),
    ('8.2. Drug Administration', '투여 방법'), ('8.2. Dose and Schedule', '투여 방법'),
    ('8.2. 시험약 투여 방법', '투여 방법')])
def test_natural_protocol_section_aliases_without_literal_tags(section, label):
    record = protocol(section)
    before = deepcopy(record)
    result = qualify(record, label, expected_study='TEST-0001')
    assert result and result['family'] == 'protocol' and result['study_id'] == 'TEST-0001'
    assert record == before
    for anchor in result['anchors']:
        assert 0 <= anchor['start'] < anchor['end'] <= len(record['context_text'])


@pytest.mark.parametrize('section,label', [('1. Introduction', '서론'), ('1. 서론', '서론'),
    ('7. Data in Line Listings and Summary Tabulations', '일련 목록(Line listing)과 요약표의 데이터'),
    ('7.2. Line Listings of Serious Adverse Reactions during the Reporting Period', '일련 목록(Line listing)과 요약표의 데이터'),
    ('7.3. Cumulative Summary Tabulations of Serious Adverse Events', '일련 목록(Line listing)과 요약표의 데이터'),
    ('7. 일련 목록', '일련 목록(Line listing)과 요약표의 데이터')])
def test_synthetic_completed_dsur_body_aliases_require_real_report_identity(section, label):
    result = qualify(dsur(section), label, expected_period=['2025-01-01', '2025-12-31'])
    assert result and result['family'] == 'dsur'
    assert result['reporting_period'] == ['2025-01-01', '2025-12-31']


@pytest.mark.parametrize('change', ['other-section', 'header-after-quote', 'only-filename', 'only-tags',
    'user-input', 'other-product', 'other-study', 'unknown-page', 'missing-sha', 'invalid-span',
    'ocr-unconfirmed', 'header-missing-in-this-block', 'forged-context'])
def test_unqualified_origins_scope_and_context_are_rejected(change):
    record = protocol()
    kwargs = {}
    if change == 'other-section':
        record = source('Clinical Study Protocol TEST-0001 Original\n2. Study Objectives\n'
                        + PRODUCT + ' objectives are pending.\n3. Study Design\n' + LINE)
    elif change == 'header-after-quote':
        record = source('2. Study Design\n' + LINE + '\n2. Study Objectives\n' + PRODUCT)
    elif change == 'only-filename':
        record = source(LINE, filename='Clinical Study Protocol TEST-0001.pdf')
    elif change == 'only-tags':
        record = source('자료 유형: 임상시험계획서\n항목: 임상시험 목적\n' + LINE)
    elif change == 'user-input':
        record['source_origin'] = 'user_input'
    elif change == 'other-product':
        record = protocol(body=LINE.replace(PRODUCT, 'SyntheticDrugB'))
    elif change == 'other-study':
        kwargs['expected_study'] = 'OTHER-0001'
    elif change == 'unknown-page':
        record['page'] = None
    elif change == 'missing-sha':
        record.pop('document_sha256')
    elif change == 'invalid-span':
        record['context_start'] += 1
    elif change == 'ocr-unconfirmed':
        record['requires_verification'] = True
    elif change == 'header-missing-in-this-block':
        record = source('2. Study Objectives\n' + LINE)
        record['claimed_previous_header'] = 'Clinical Study Protocol TEST-0001'
    else:
        record['text'] = 'Invented quote'
    assert qualify(record, **kwargs) is None


def test_quote_cannot_cross_next_section_or_use_later_scope_marker():
    context = 'Clinical Study Protocol TEST-0001\n2. Study Objectives\n' + LINE + '\n3. Study Design\nOther text'
    record = source(context, LINE + '\n3. Study Design\nOther text')
    assert qualify(record) is None


@pytest.mark.parametrize('prefix', ['Guidance for Industry\n', 'ICH Guideline\n', 'DSUR Template\n',
                                 'Sample DSUR\n', 'Table of Contents\n', '작성 예시\n'])
def test_dsur_guidance_template_examples_and_toc_are_not_actual_safety_reports(prefix):
    assert qualify(dsur(prefix=prefix), '서론') is None


@pytest.mark.parametrize('replace', [('DSUR number: 4\n', ''),
    ('Reporting period: 2025-01-01 to 2025-12-31\n', ''),
    ('2025-01-01', '2025-02-30'), ('2025-12-31', '2024-12-31')])
def test_dsur_identifiers_and_calendar_period_cannot_be_missing_or_invalid(replace):
    context = dsur()['context_text'].replace(*replace)
    assert qualify(source(context), '서론') is None


def test_dsur_reporting_period_selection_is_not_inferred_or_replaced():
    assert qualify(dsur(), '서론', expected_period=['2024-01-01', '2024-12-31']) is None


def test_empty_template_placeholder_cannot_supply_actual_body_evidence():
    body = PRODUCT + ' [Insert report text]'
    assert qualify(dsur(body=body), '서론') is None


def test_explicit_other_study_reference_in_quote_is_not_merged_with_document_header():
    body = PRODUCT + ': Protocol Number: OTHER-0001. Trial objective is pending.'
    assert qualify(protocol(body=body), expected_study='TEST-0001') is None


def test_other_known_product_in_same_section_cannot_borrow_selected_product_anchor():
    body = PRODUCT + ' is the investigational drug.\nSyntheticDrugB is being evaluated separately.'
    record = protocol(body=body)
    quote = 'SyntheticDrugB is being evaluated separately.'
    record['text'] = quote
    record['context_start'] = record['context_text'].index(quote)
    record['context_end'] = record['context_start'] + len(quote)
    assert qualify(record, known_products=[PRODUCT, 'SyntheticDrugB']) is None


def test_trial_header_cannot_qualify_a_registered_dsur_only_field_contract():
    assert qualify(protocol(), allowed_document_labels=['DSUR 원자료']) is None
    assert qualify(protocol(), allowed_document_labels=['임상시험계획서'])


def test_actual_protocol_header_after_quote_cannot_retroactively_qualify_it():
    record = source('2. Study Objectives\n' + LINE + '\nClinical Study Protocol TEST-0001 Original')
    assert qualify(record) is None


@pytest.mark.parametrize('anchor', ['title', 'number', 'period'])
def test_dsur_identity_anchor_after_quote_is_not_borrowed(anchor):
    record = dsur()
    headers = {'title': 'Development Safety Update Report', 'number': 'DSUR number: 4',
               'period': 'Reporting period: 2025-01-01 to 2025-12-31'}
    context = record['context_text'].replace(headers[anchor] + '\n', '') + '\n' + headers[anchor]
    assert qualify(source(context), '서론') is None


@pytest.mark.parametrize('extra', ['DSUR number: 5', 'Reporting period: 2024-01-01 to 2024-12-31',
    'Clinical Study Protocol OTHER-0001 Original'])
def test_same_block_conflicting_document_identity_is_not_merged(extra):
    record = dsur()
    if 'Clinical Study' in extra:
        # A protocol identity mixed with DSUR is ambiguous, despite report headers.
        record['context_text'] += '\n' + extra
        assert qualify(record, '서론') is None
    else:
        record['context_text'] += '\n' + extra
        assert qualify(record, '서론') is None


def test_implicit_product_sentence_requires_explicit_distinct_range_policy():
    body = 'Investigational drug: ' + PRODUCT + '\nThe investigation is ongoing.'
    record = protocol(body=body)
    quote = 'The investigation is ongoing.'
    record['text'] = quote
    record['context_start'] = record['context_text'].index(quote)
    record['context_end'] = record['context_start'] + len(quote)
    assert qualify(record) is None
    result = qualify(record, product_policy='declared_single_product_section')
    assert result and result['product_binding_policy'] == 'declared_single_product_section'
    record['context_text'] += '\nSyntheticDrugB is also considered.'
    # An unrelated section does not broaden the earlier explicitly declared section.
    assert qualify(record, product_policy='declared_single_product_section', known_products=[PRODUCT, 'SyntheticDrugB'])
    record['context_text'] = record['context_text'].replace('\n3. Study Design', '\nSyntheticDrugB is also considered.\n3. Study Design')
    assert qualify(record, product_policy='declared_single_product_section', known_products=[PRODUCT, 'SyntheticDrugB']) is None


@pytest.mark.parametrize('body', [PRODUCT, 'Product name: ' + PRODUCT, 'Investigational drug: ' + PRODUCT])
@pytest.mark.parametrize('family', ['protocol', 'dsur'])
def test_product_identity_alone_even_under_real_section_is_not_actual_field_content(body, family):
    if family == 'protocol':
        assert qualify(protocol(body=body)) is None
    else:
        assert qualify(dsur(body=body), '서론') is None


@lru_cache(maxsize=1)
def public_records():
    provenance = json.loads((HERE / 'provenance.json').read_text(encoding='utf-8'))
    assert provenance['original_page_count'] == 68
    assert provenance['fixture_scope'].startswith('Three selected exact pages')
    records = []
    for page, evidence in provenance['selected_original_pages'].items():
        path = HERE / evidence['fixture_file']
        assert sha256(path.read_bytes()).hexdigest() == evidence['excerpt_sha256']
        reader = PdfReader(path)
        assert len(reader.pages) == 1
        assert sha256(reader.pages[0].get_contents().get_data()).hexdigest() == evidence['content_stream_sha256']
        parsed = parse_pdf(path)
        parsed['document_sha256'] = provenance['original_sha256']
        parsed['source_url'] = provenance['source_url']
        for block in parsed['페이지/시트 정보']:
            assert block['페이지'] == 1
            block['페이지'] = int(page)
            block['위치'] = f'원본 페이지 {page}'
        fresh = chunk_documents([parsed])
        for source in fresh:
            assert source['document_sha256'] == provenance['original_sha256']
            assert source['context_text'][source['context_start']:source['context_end']] == source['text']
        records.extend(fresh)
    return records, provenance


@pytest.mark.parametrize('page,label', [(17, '임상시험 목적'), (31, '투여 방법')])
def test_real_public_clinicaltrials_protocol_parser_spans_qualify_without_artificial_tags(page, label):
    records, provenance = public_records()
    successes = []
    for record in records:
        if record['page'] != page:
            continue
        assert '자료 유형:' not in record['context_text'] and '항목:' not in record['context_text']
        result = qualify_natural_scope(record, label, expected_product='REGN3918', expected_study='R3918-PNH-1868')
        if result:
            successes.append(result)
            assert result['document_sha256'] == provenance['original_sha256']
            assert result['page'] == page
    assert successes


def test_real_public_protocol_toc_other_sections_product_and_study_do_not_qualify():
    records, _ = public_records()
    for record in records:
        if record['page'] == 8:
            assert qualify_natural_scope(record, '임상시험 목적', expected_product='REGN3918') is None
        if record['page'] == 31:
            assert qualify_natural_scope(record, '임상시험 목적', expected_product='REGN3918') is None
        assert qualify_natural_scope(record, '투여 방법', expected_product='OtherDrug') is None
        assert qualify_natural_scope(record, '임상시험 목적', expected_product='REGN3918', expected_study='OTHER-0001') is None


INDEPENDENT = json.loads((HERE / 'synthetic-counterexamples.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', INDEPENDENT, ids=[case['name'] for case in INDEPENDENT])
def test_independent_exact_other_product_study_and_report_period_counterexamples(case):
    assert qualify_natural_scope(case['source'], case['field_label'], expected_product=PRODUCT, **case['kwargs']) is None


@pytest.mark.parametrize('case', INDEPENDENT, ids=[case['name'] for case in INDEPENDENT])
def test_independent_counterexamples_are_blocked_by_full_ra_inspection(case):
    from agent.ra import inspect_ra_draft

    label = case['field_label']
    dsur_case = label == '서론'
    profile = {'domain': 'pharmaceutical_ra', 'format': 'pdf', 'source_sha256': 'b' * 64,
               'ra_workflow': 'safety_management' if dsur_case else 'clinical_trial',
               'ra_product_name': PRODUCT, 'configured': True,
               'fields': [{'id': 'registered-source-field', 'value_key': label, 'label': label,
                           'kind': 'pdf_overlay', 'required': False, 'input_required': False,
                           'input_mode': 'source_grounded', 'narrative_style_required': False,
                           'evidence_scope': 'Registered original document and body section scope.',
                           'evidence_document_labels': ['DSUR 원자료'] if dsur_case else ['임상시험계획서']}]}
    evidence = deepcopy(case['source']) | {'source_id': 'SIndependent'}
    result = inspect_ra_draft({label: evidence['text'] + ' [SIndependent]'}, [evidence], profile=profile)
    assert result['blocking']
    assert any(issue['code'] == 'ra_document_scope_unverified' for issue in result['issues'])


def test_public_excerpt_provenance_identifies_partial_original_without_runtime_dependencies():
    records, provenance = public_records()
    assert provenance['original_sha256'] == '52c3066161ceee95cf4ecfea4656a81c36260bfcdb8e645ee669c8c9694d8007'
    assert provenance['full_document_parsed'] is False
    assert provenance['original_to_excerpt_content_stream_verified'] is True
    assert provenance['offline_test_http_requests'] == 0
    assert len(provenance['selected_original_pages']) == 3
    assert {record['page'] for record in records} == {8, 17, 31}
    assert all(not record['requires_verification'] for record in records)


def test_docx_real_paragraph_location_does_not_invent_pdf_page():
    record = protocol()
    record.update(filename='synthetic-protocol.docx', page=None, location='본문/문단 1, 항목 3')
    result = qualify(record)
    assert result and result['page'] is None
    record.pop('location')
    assert qualify(record) is None


def test_declared_item_is_only_section_anchor_and_cannot_supply_docx_document_identity():
    record = source('Clinical Study Protocol TEST-0001 Original\n항목: 임상시험 목적\n' + LINE)
    record.update(filename='synthetic-protocol.docx', page=None, location='본문/문단 1, 항목 3')
    assert qualify(record)
    record = source('자료 유형: 임상시험계획서\n항목: 임상시험 목적\n' + LINE,
                    filename='synthetic-protocol.docx', page=None, location='본문/문단 1, 항목 3')
    assert qualify(record) is None


LITERAL_CASES = json.loads((HERE / 'synthetic-literal-bypass.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', LITERAL_CASES, ids=[case['name'] for case in LITERAL_CASES])
def test_literal_tags_cannot_override_natural_document_or_provenance_requirements(case):
    from agent.ra import inspect_ra_draft

    profile = {'domain': 'pharmaceutical_ra', 'format': 'pdf', 'source_sha256': 'b' * 64,
               'ra_workflow': 'clinical_trial', 'ra_product_name': PRODUCT,
               'fields': [{'id': 'purpose', 'value_key': '임상시험 목적', 'label': '임상시험 목적',
                           'kind': 'pdf_overlay', 'narrative_style_required': False,
                           'evidence_scope': 'Original clinical protocol identity and applicable section.',
                           'evidence_document_labels': ['임상시험계획서']}]}
    evidence = deepcopy(case['source']) | {'source_id': 'SLiteralAudit'}
    helper = qualify(evidence)
    assert bool(helper) is case['expected_qualified']
    result = inspect_ra_draft({'임상시험 목적': evidence['text'] + ' [SLiteralAudit]'}, [evidence], profile=profile)
    scope_errors = [issue for issue in result['issues'] if issue['code'] == 'ra_document_scope_unverified']
    assert bool(scope_errors) is not case['expected_qualified']
    if not case['expected_qualified']:
        assert result['blocking']
