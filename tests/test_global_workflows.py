"""Synthetic trade fixtures; no network, invented commercial facts or legal claims."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile

from docx import Document
from lxml import etree
import pytest

from agent.global_workflows import (GLOBAL_WORKFLOWS, blank_global_input, create_global_template,
                                    export_global_workflow, prepare_global_workflow, propose_global_bindings,
                                    workflow_spec)
from agent.retrieve import chunk_documents, load_documents


def setup(tmp_path, workflow='commercial_invoice', rows=2):
    path = tmp_path / (workflow + '.docx')
    profile = create_global_template(workflow, path, rows=rows)
    profile['global_transaction_id'] = 'SYNTHETIC-001'
    values = {'Order No.': 'SYNTHETIC-001', 'Document Date': '2026-10-04', 'Seller': 'Synthetic Export Co',
              'Buyer': 'Synthetic Buyer Ltd', 'Destination': 'Japan', 'Currency': 'USD',
              'Incoterms': 'FOB', 'Named Place': 'Busan', 'Payment Terms': 'Advance payment',
              'Description[1]': 'Synthetic Widget A', 'Quantity[1]': '3', 'Quantity Unit[1]': 'PCS',
              'Unit Price[1]': '10.20', 'Amount[1]': '30.60', 'Total Amount': '30.60',
              'Packages[1]': '1', 'Net Weight[1]': '2', 'Gross Weight[1]': '3', 'Weight Unit[1]': 'kg',
              '보고 기간': '2026-10-01 ~ 2026-10-04', '결론': '□ 실제 합의가 아닌 시험 자료를 정리함',
              '주요 활동': '○ 시험 자료의 진행 상황을 확인함', '다음 계획': '- 담당자 확인을 계획함'}
    text = '\n'.join(f'{field["label"]}: {values[field["value_key"]]}' for field in profile['fields']
                     if field['value_key'] in values)
    source_path = tmp_path / 'synthetic.txt'
    source_path.write_text(text, encoding='utf-8')
    sources = chunk_documents(load_documents([source_path], allow_ocr=False))
    proposal = propose_global_bindings(profile, sources)
    return path, profile, sources, proposal['source_bindings']


@pytest.mark.parametrize('workflow', GLOBAL_WORKFLOWS)
def test_representative_forms_ready_copy_export_with_original_layout_and_sidecar(tmp_path, workflow):
    template, profile, sources, bindings = setup(tmp_path, workflow)
    direct = {'보고 대상': '테스트 담당자'} if workflow == 'overseas_activity' else {}
    original = template.read_bytes()
    result = prepare_global_workflow(profile, sources, bindings, direct)
    assert result['ready_for_output_check'], result['review']
    assert result['mode'] == 'verified_field_copy' and result['actual_model_requests'] == 0
    assert not result['submission_ready']
    assert result['plain_values']['Order No.'] == 'SYNTHETIC-001'
    exported = export_global_workflow(template, profile, sources, bindings, direct)
    assert template.read_bytes() == original
    proof = exported['output_verification']
    assert proof['status'] == 'passed' and proof['sha']['template'] == sha256(original).hexdigest()
    sidecar = json.loads(exported['evidence'])
    assert sidecar['evidence']['Order No.']['source']['document_sha256'] == sources[0]['document_sha256']
    with ZipFile(BytesIO(exported['document'])) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
        text = '\n'.join(root.xpath('.//*[local-name()="t"]/text()'))
    assert 'SYNTHETIC-001' in text and '[S' not in text
    # Empty optional positions stay unchanged; they are not filled with fake zeros.
    if workflow != 'overseas_activity':
        second = next(field for field in profile['fields'] if field['value_key'] == 'Description[2]')
        node = root.xpath(second['id'].split(':', 2)[2], namespaces=root.nsmap)[0]
        assert not ''.join(node.itertext()).strip()
    assert sidecar['native_visual_review'] == 'not_run'


def test_no_mutation_qa_removal_and_row_positions_unique(tmp_path):
    _, profile, sources, bindings = setup(tmp_path)
    profile['qa_values'] = {'Buyer': 'Wrong Buyer'}
    profile['fields'][0]['default_value'] = 'Wrong Order'
    before = deepcopy((profile, sources, bindings))
    result = prepare_global_workflow(profile, sources, bindings)
    assert (profile, sources, bindings) == before
    assert 'qa_values' not in result['template_profile']
    assert 'default_value' not in result['template_profile']['fields'][0]
    rows = profile['global_item_rows']
    assert set(rows[0].values()).isdisjoint(rows[1].values())


@pytest.mark.parametrize('change', ['other_transaction', 'mixed_transactions', 'sha', 'quote', 'ocr', 'context', 'user_input'])
def test_source_guards_block_stale_or_wrong_transaction_and_unconfirmed_ocr(tmp_path, change):
    _, profile, sources, bindings = setup(tmp_path)
    if change == 'other_transaction': profile['global_transaction_id'] = 'OTHER-001'
    if change == 'mixed_transactions':
        extra = deepcopy(sources[0]); extra.update(source_id='Sother', text='Order No.: OTHER-001',
                                                  context_text='Order No.: OTHER-001', context_start=0, context_end=20)
        sources.append(extra)
    bound = next(source for source in sources if source['source_id'] == bindings['Buyer']['source_id'])
    if change == 'sha': bound['document_sha256'] = 'missing'
    if change == 'quote': bindings['Buyer']['quote'] = 'Wrong Buyer'
    if change == 'ocr': bound['requires_verification'] = True
    if change == 'context': bound['context_text'] = 'Wrong text'
    if change == 'user_input': bound['filename'] = '사용자 입력'
    with pytest.raises(ValueError): prepare_global_workflow(profile, sources, bindings)


def test_duplicate_positions_ambiguous_no_auto_selection_and_direct_signature(tmp_path):
    _, profile, sources, bindings = setup(tmp_path)
    extra = deepcopy(next(source for source in sources if source['source_id'] == bindings['Buyer']['source_id']))
    extra['source_id'] = 'SotherBuyer'
    sources.append(extra)
    proposal = propose_global_bindings(profile, sources)
    buyer = next(field for field in proposal['fields'] if field['value_key'] == 'Buyer')
    assert buyer['status'] == 'ambiguous' and len(buyer['candidates']) == 2
    assert 'Buyer' not in proposal['source_bindings']
    assert proposal['requires_confirmation'] and not proposal['confirmed']
    bindings['Signed By'] = deepcopy(bindings['Buyer'])
    with pytest.raises(ValueError): prepare_global_workflow(profile, sources, bindings)


@pytest.mark.parametrize('field,value,code', [('Amount[1]', '30.61', 'global_line_amount'),
                                           ('Total Amount', '40.60', 'global_total_amount'),
                                           ('Currency', '$', 'global_currency')])
def test_wrong_provided_arithmetic_and_ambiguous_currency_block_without_correction(tmp_path, field, value, code):
    _, profile, sources, bindings = setup(tmp_path)
    source = next(source for source in sources if source['source_id'] == bindings[field]['source_id'])
    source.update(text=field + ': ' + value, context_text=field + ': ' + value,
                  context_start=0, context_end=len(field + ': ' + value))
    bindings[field] = {'source_id': source['source_id'], 'quote': value}
    result = prepare_global_workflow(profile, sources, bindings)
    assert result['review']['blocking']
    assert any(issue['code'] == code for issue in result['review']['issues'])
    assert result['plain_values'][field] == value


def test_partial_row_and_missing_fields_no_generated_zeros_max_two_questions(tmp_path):
    _, profile, sources, bindings = setup(tmp_path)
    bindings.pop('Quantity[1]')
    result = prepare_global_workflow(profile, sources, bindings)
    assert not result['ready_for_output_check']
    assert any(issue['code'] == 'form_group_required' for issue in result['review']['issues'])
    assert result['plain_values']['Quantity[1]'] == ''
    empty = prepare_global_workflow(profile, sources, {})
    assert len(empty['questions']) == 2 and len(empty['missing_fields']) > 2


def test_cross_document_transaction_metadata_cannot_replace_actual_order(tmp_path):
    _, profile, sources, bindings = setup(tmp_path)
    original = next(source for source in sources if source['source_id'] == bindings['Buyer']['source_id'])
    original.update(document_sha256='a' * 64, global_transaction_id='SYNTHETIC-001')
    assert 'Buyer' not in propose_global_bindings(profile, sources)['source_bindings']
    with pytest.raises(ValueError): prepare_global_workflow(profile, sources, bindings)


def test_blank_input_and_spec_limits_do_not_invent_facts():
    blank = blank_global_input('commercial_invoice').decode('utf-8')
    assert 'Order No.: ' in blank and 'Currency: ' in blank
    assert 'SYNTHETIC' not in blank and 'Signed By:' not in blank
    for rows in (0, 21, True):
        with pytest.raises(ValueError): workflow_spec('commercial_invoice', rows)


def test_original_sha_or_source_changes_prevent_reusing_download(tmp_path):
    template, profile, sources, bindings = setup(tmp_path)
    before = prepare_global_workflow(profile, sources, bindings)['fingerprint']
    sources[0]['location'] += ' changed'
    assert prepare_global_workflow(profile, sources, bindings)['fingerprint'] != before
    profile['source_sha256'] = 'a' * 64
    with pytest.raises(ValueError): export_global_workflow(template, profile, sources, bindings)
    with pytest.raises(ValueError): create_global_template('commercial_invoice', template)


def test_real_csv_header_rows_multimodal_parser_keeps_transaction_and_full_columns(tmp_path):
    _, profile, _, _ = setup(tmp_path)
    source = tmp_path / 'actual-structure-synthetic.csv'
    source.write_text('Order No.,Currency,Description[1],Quantity[1],Quantity Unit[1],Unit Price[1],Amount[1],Total Amount\n'
                      'SYNTHETIC-001,USD,Widget A,3,PCS,10.20,30.60,30.60\n', encoding='utf-8')
    parsed = chunk_documents(load_documents([source], allow_ocr=False))
    proposal = propose_global_bindings(profile, parsed)
    assert proposal['source_bindings']['Currency']['quote'] == 'USD'
    assert proposal['source_bindings']['Description[1]']['quote'] == 'Widget A'
    assert proposal['source_bindings']['Amount[1]']['quote'] == '30.60'


def test_plain_prose_semicolon_is_not_silently_removed_from_company_name(tmp_path):
    _, profile, sources, bindings = setup(tmp_path)
    source = next(source for source in sources if source['source_id'] == bindings['Seller']['source_id'])
    source.update(text='Seller: Synthetic Co; Tokyo Branch', context_text='Seller: Synthetic Co; Tokyo Branch',
                  context_start=0, context_end=len('Seller: Synthetic Co; Tokyo Branch'))
    bindings['Seller'] = propose_global_bindings(profile, sources)['source_bindings']['Seller']
    assert bindings['Seller']['quote'] == 'Synthetic Co; Tokyo Branch'
    assert prepare_global_workflow(profile, sources, bindings)['ready_for_output_check']


def test_intake_confirmed_fingerprint_revalidated_before_proposal_preparation_and_export(tmp_path):
    from agent.multimodal_intake import collect_multimodal, generation_sources
    template, profile, _, _ = setup(tmp_path)
    intake = collect_multimodal([tmp_path / 'synthetic.txt'], allow_ocr=False)
    sources = generation_sources(intake)
    bindings = propose_global_bindings(profile, sources)['source_bindings']
    assert prepare_global_workflow(profile, sources, bindings)['ready_for_output_check']
    sources[0]['location'] += ' changed after confirmation'
    with pytest.raises(ValueError): propose_global_bindings(profile, sources)
    with pytest.raises(ValueError): prepare_global_workflow(profile, sources, bindings)
    with pytest.raises(ValueError): export_global_workflow(template, profile, sources, bindings)


@pytest.mark.parametrize('target,origin', [('Buyer', 'Seller'), ('Seller', 'Buyer'),
                                         ('Amount[1]', 'Unit Price[1]'), ('Quantity[2]', 'Quantity[1]')])
def test_exact_quote_cannot_cross_party_or_amount_role_or_item_row(tmp_path, target, origin):
    _, profile, sources, bindings = setup(tmp_path)
    bindings[target] = deepcopy(bindings[origin])
    with pytest.raises(ValueError, match='항목'):
        prepare_global_workflow(profile, sources, bindings)


@pytest.mark.parametrize('target,label,value', [('Buyer', 'UnknownField', 'Synthetic Buyer Ltd'),
                                               ('Quantity[1]', 'Quantity[3]', '3')])
def test_unregistered_source_role_and_outside_item_row_cannot_fill_known_field(tmp_path, target, label, value):
    _, profile, sources, bindings = setup(tmp_path)
    extra = deepcopy(sources[0])
    text = label + ': ' + value
    extra.update(source_id='SoutsideRole', text=text, context_text=text, context_start=0,
                 context_end=len(text), location='확인할 원문 행')
    sources.append(extra)
    bindings[target] = {'source_id': extra['source_id'], 'quote': value, 'start': len(label) + 2}
    with pytest.raises(ValueError, match='항목'):
        prepare_global_workflow(profile, sources, bindings)


@pytest.mark.parametrize('key,value', [('origin', 'model'), ('source_origin', 'model'),
                                     ('origin', 'mock'), ('source_type', 'user_input'),
                                     ('source_kind', 'ai_generated')])
def test_generated_source_origin_cannot_become_verified_commercial_evidence(tmp_path, key, value):
    template, profile, sources, bindings = setup(tmp_path)
    for source in sources:
        source[key] = value
    with pytest.raises(ValueError): propose_global_bindings(profile, sources)
    with pytest.raises(ValueError): prepare_global_workflow(profile, sources, bindings)
    with pytest.raises(ValueError): export_global_workflow(template, profile, sources, bindings)


@pytest.mark.parametrize('key,short', [('Seller', 'Synthetic Export'), ('Buyer', 'Synthetic Buyer'),
                                    ('Unit Price[1]', '10'), ('Description[1]', 'Synthetic Widget')])
def test_source_field_full_value_cannot_be_silently_shortened(tmp_path, key, short):
    template, profile, sources, bindings = setup(tmp_path)
    bindings[key]['quote'] = short
    with pytest.raises(ValueError, match='전체'):
        prepare_global_workflow(profile, sources, bindings)
    with pytest.raises(ValueError): export_global_workflow(template, profile, sources, bindings)
