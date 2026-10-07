"""Actual TXT parser -> exact RA proposals -> preparation; synthetic drug data."""
from copy import deepcopy
from hashlib import sha256

import pytest

from agent.ra import inspect_ra_draft
from agent.ra_autofill import propose_ra_bindings
from agent.ra_workflows import prepare_ra_workflow
from agent.retrieve import chunk_documents, load_documents
from parsers.extended import parse_extended


def _profile():
    return {'ra_workflow': 'product_approval', 'ra_product_name': 'SyntheticDrugA',
            'fields': [{'id': 'product', 'value_key': '제품명', 'label': '제품명',
                        'evidence_role': 'product_name'},
                       {'id': 'appearance', 'value_key': '성상', 'label': '성상'}]}


def _read(tmp_path, text, name='synthetic-ra.txt'):
    path = tmp_path / name
    path.write_bytes(text.encode('utf-8'))
    return path, chunk_documents(load_documents([path], allow_ocr=False))


@pytest.mark.parametrize('encoding,newline', [('utf-8', '\n'), ('utf-8-sig', '\r\n'), ('cp949', '\r\n')])
def test_original_block_chars_blank_boundary_and_physical_line_numbers_are_preserved(tmp_path, encoding, newline):
    block = newline.join(['  제품명: SyntheticDrugA ', '  성상: 흰색 분말  ', '포장: 병']) + newline
    text = newline + block + ' \t' + newline + '다른 절' + newline
    path = tmp_path / 'synthetic-ra.txt'
    path.write_bytes(text.encode(encoding))
    parsed = parse_extended(path)
    assert parsed['본문'] == text
    blocks = parsed['페이지/시트 정보']
    assert [item['위치'] for item in blocks] == ['줄 2~4', '줄 6']
    assert blocks[0]['본문'] == block and blocks[1]['본문'] == '다른 절' + newline
    sources = chunk_documents(load_documents([path], allow_ocr=False))
    first = sources[:3]
    assert [item['location'] for item in first] == [f'줄 2~4, 항목 {i}' for i in range(1, 4)]
    for number, source in enumerate(first, 2):
        assert source['document_sha256'] == sha256(path.read_bytes()).hexdigest()
        assert source['context_text'] == block
        assert block[source['context_start']:source['context_end']] == source['text']
        assert source['text'] == text.splitlines()[number - 1].strip()


def test_actual_multiline_txt_proposes_and_prepares_exact_values_with_independent_spans(tmp_path):
    path, sources = _read(tmp_path, '제품명: SyntheticDrugA\n성상: 흰색 분말\n')
    profile = _profile()
    before = deepcopy((profile, sources))
    proposals = propose_ra_bindings(profile, sources)
    assert set(proposals['source_bindings']) == {'제품명', '성상'}
    assert proposals['requires_confirmation'] and not proposals['confirmed']
    prepared = prepare_ra_workflow(profile, sources, proposals['source_bindings'], {})
    assert prepared['ready_for_output_check'], prepared['review']
    assert not prepared['submission_ready'] and prepared['actual_model_requests'] == 0
    appearance = prepared['evidence']['성상']
    assert appearance['quote'] == '흰색 분말'
    assert appearance['source']['text'][appearance['start']:appearance['end']] == appearance['quote']
    assert appearance['source']['document_sha256'] == sha256(path.read_bytes()).hexdigest()
    assert (profile, sources) == before


@pytest.mark.parametrize('other_value', ['노란 분말', '흰색 분말'])
def test_multiple_products_in_one_original_block_cannot_supply_other_product_appearance(tmp_path, other_value):
    _, sources = _read(tmp_path, '제품명: SyntheticDrugA\n성상: 흰색 분말\n'
                      '제품명: OtherDrug\n성상: ' + other_value)
    profile = _profile()
    proposals = propose_ra_bindings(profile, sources)
    assert proposals['source_bindings']['성상']['source_id'] == sources[1]['source_id']
    assert next(row for row in proposals['fields'] if row['label'] == '성상')['candidate_count'] == 1
    wrong = {**proposals['source_bindings'], '성상': {'source_id': sources[3]['source_id'], 'quote': other_value}}
    prepared = prepare_ra_workflow(profile, sources, wrong, {})
    assert not prepared['ready_for_output_check']
    assert any(row['code'] == 'ra_field_evidence_unverified' for row in prepared['review']['issues'])


def test_blank_line_boundary_never_inherits_a_previous_product(tmp_path):
    _, sources = _read(tmp_path, '제품명: SyntheticDrugA\n\n성상: 흰색 분말')
    proposals = propose_ra_bindings(_profile(), sources)
    assert '성상' not in proposals['source_bindings']
    assert next(row for row in proposals['fields'] if row['label'] == '성상')['status'] == 'blocked'
    assert sources[1]['context_text'] == '성상: 흰색 분말'


@pytest.mark.parametrize('damage', ['shift', 'text', 'missing_end'])
def test_tampered_context_does_not_replace_the_real_quote_or_product_scope(tmp_path, damage):
    _, sources = _read(tmp_path, '제품명: SyntheticDrugA\n성상: 흰색 분말')
    proposals = propose_ra_bindings(_profile(), sources)
    if damage == 'shift':
        sources[1]['context_start'] -= 1
    elif damage == 'text':
        sources[1]['context_text'] = sources[1]['context_text'].replace('흰색', '노란')
    else:
        sources[1].pop('context_end')
    with pytest.raises(ValueError, match='문자 범위'):
        prepare_ra_workflow(_profile(), sources, proposals['source_bindings'], {})


def test_a_value_only_in_other_context_line_is_not_new_quote_evidence(tmp_path):
    _, sources = _read(tmp_path, '제품명: SyntheticDrugA\n성상: 흰색 분말\n포장: 갈색 병')
    with pytest.raises(ValueError, match='인용'):
        prepare_ra_workflow(_profile(), sources, {'성상': {'source_id': sources[1]['source_id'], 'quote': '갈색 병'}}, {})


def test_negative_original_is_preserved_without_silent_correction(tmp_path):
    _, sources = _read(tmp_path, '제품명: SyntheticDrugA\n성상: 흰색 분말이 아님')
    proposals = propose_ra_bindings(_profile(), sources)
    assert proposals['source_bindings']['성상']['quote'] == '흰색 분말이 아님'
    prepared = prepare_ra_workflow(_profile(), sources, proposals['source_bindings'], {})
    assert prepared['ready_for_output_check'], prepared['review']
    assert prepared['draft']['성상'].startswith('흰색 분말이 아님 [')


@pytest.mark.parametrize('original,partial', [('흰색 분말 아님', '흰색 분말'),
    ('흰색 분말이 아님', '흰색 분말'), ('흰색 분말 아니다', '흰색 분말'),
    ('not white powder', 'white powder'), ('white powder is not present', 'white powder')])
def test_manual_partial_quote_cannot_remove_its_adjacent_explicit_negation(tmp_path, original, partial):
    _, sources = _read(tmp_path, '제품명: SyntheticDrugA\n성상: ' + original)
    proposals = propose_ra_bindings(_profile(), sources)
    complete = prepare_ra_workflow(_profile(), sources, proposals['source_bindings'], {})
    assert complete['ready_for_output_check'], complete['review']
    bindings = {**proposals['source_bindings'], '성상': {'source_id': sources[1]['source_id'], 'quote': partial}}
    checked = prepare_ra_workflow(_profile(), sources, bindings, {})
    assert not checked['ready_for_output_check']
    assert any(row['code'] == 'ra_field_evidence_unverified' for row in checked['review']['issues'])
    assert checked['draft']['성상'].startswith(partial + ' [')  # Warn, never rewrite the fact.


def test_unrelated_other_line_negation_does_not_negate_the_appearance(tmp_path):
    _, sources = _read(tmp_path, '제품명: SyntheticDrugA\n성상: 흰색 분말\n포장: 병 아님')
    proposals = propose_ra_bindings(_profile(), sources)
    assert proposals['source_bindings']['성상']['quote'] == '흰색 분말'
    assert prepare_ra_workflow(_profile(), sources, proposals['source_bindings'], {})['ready_for_output_check']


def test_condition_in_next_original_line_stays_available_for_ra_omission_review(tmp_path):
    text = ('제품명: SyntheticDrugA\n'
            '용법 용량: SyntheticDrugA recommended dose is 10 mg twice daily.\n'
            'However, the dose may be increased to 20 mg based on physician assessment.\n')
    _, sources = _read(tmp_path, text)
    source = sources[1]
    quote = 'SyntheticDrugA recommended dose is 10 mg twice daily.'
    checked = inspect_ra_draft({'용법 용량': quote + ' [' + source['source_id'] + ']'}, sources,
                              profile={'ra_product_name': 'SyntheticDrugA'})
    assert source['context_text'] == text
    assert checked['blocking']
    assert any(row['code'] == 'ra_condition_omitted' for row in checked['issues']), checked


def test_alias_labels_still_require_explicit_mapping_not_fuzzy_automatic_copy(tmp_path):
    _, sources = _read(tmp_path, '제품명: SyntheticDrugA\n외관: 흰색 분말')
    proposals = propose_ra_bindings(_profile(), sources)
    assert '성상' not in proposals['source_bindings']
    assert next(row for row in proposals['fields'] if row['label'] == '성상')['status'] == 'missing'


def _complete_profile():
    return {**_profile(), 'ra_product_variant': '정제 10 mg',
            'fields': [*_profile()['fields'],
                       {'id': 'strength', 'value_key': '제형·함량', 'label': '제형·함량'},
                       {'id': 'package', 'value_key': '포장단위', 'label': '포장단위'}]}


@pytest.mark.parametrize('kind', ['txt', 'docx'])
def test_real_file_next_line_variant_and_thirty_tablet_package_are_exact_source_values(tmp_path, kind):
    lines = ['제품명: SyntheticDrugA', '제형·함량: 정제 10 mg', '성상: 흰색 정제', '포장단위: 30정']
    path = tmp_path / ('synthetic-ra.' + kind)
    if kind == 'txt':
        path.write_text('\n'.join(lines), encoding='utf-8')
    else:
        from docx import Document
        doc = Document()
        doc.add_paragraph('\n'.join(lines))
        doc.save(path)
    sources = chunk_documents(load_documents([path], allow_ocr=False))
    profile = _complete_profile()
    proposal = propose_ra_bindings(profile, sources)
    assert set(proposal['source_bindings']) == {'제품명', '제형·함량', '성상', '포장단위'}, proposal
    prepared = prepare_ra_workflow(profile, sources, proposal['source_bindings'], {})
    assert prepared['ready_for_output_check'], prepared['review']
    assert prepared['evidence']['포장단위']['quote'] == '30정'
    assert prepared['evidence']['제형·함량']['quote'] == '정제 10 mg'
    for value in prepared['evidence'].values():
        assert value['source']['text'][value['start']:value['end']] == value['quote']
        assert value['source']['document_sha256'] == sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('suffix', ['\n\n제형·함량: 정제 10 mg',
    '\n제품명: OtherDrug\n제형·함량: 정제 10 mg',
    '\n제형·함량: 정제 20 mg',
    '\n제형·함량: 정제 10 mg 아님',
    '\n제형·함량: 정제 10 mg\n함량: 20 mg'])
def test_next_line_variant_never_crosses_product_boundary_or_approves_conflict(tmp_path, suffix):
    _, sources = _read(tmp_path, '제품명: SyntheticDrugA' + suffix)
    proposal = propose_ra_bindings(_complete_profile(), sources)
    assert '제품명' not in proposal['source_bindings'], proposal


def test_same_block_different_product_package_and_other_numeric_role_do_not_match(tmp_path):
    _, sources = _read(tmp_path, '제품명: SyntheticDrugA\n제형·함량: 정제 10 mg\n포장단위: 30정\n'
                      '용량: 30\n제품명: OtherDrug\n제형·함량: 정제 10 mg\n포장단위: 30정')
    profile = _complete_profile()
    proposal = propose_ra_bindings(profile, sources)
    assert proposal['source_bindings']['포장단위']['source_id'] == sources[2]['source_id']
    for source in (sources[3], sources[6]):
        checked = prepare_ra_workflow(profile, sources,
            {**proposal['source_bindings'], '포장단위': {'source_id': source['source_id'],
              'quote': '30' if source is sources[3] else '30정'}}, {})
        assert not checked['ready_for_output_check']
        assert any(issue['code'] == 'ra_number_mismatch' for issue in checked['review']['issues']), checked


def test_fixed_package_label_does_not_replace_exact_narrative_numbering_and_duration_context(tmp_path):
    """Synthetic regimen for review plumbing, not a real dose recommendation."""
    regimen = '1) 성인 : SyntheticDrugA 10 mg을 1일 2회, 5일간 경구투여 한다.'
    _, sources = _read(tmp_path, '제품명: SyntheticDrugA\n제형·함량: 정제 10 mg\n'
                      '성상: 흰색 정제\n포장단위: 30정\n용법 용량: ' + regimen)
    profile = _complete_profile()
    profile['fields'].append({'id': 'dosage', 'value_key': '용법 용량', 'label': '용법 용량'})
    proposal = propose_ra_bindings(profile, sources)
    assert set(proposal['source_bindings']) == {field['value_key'] for field in profile['fields']}, proposal
    prepared = prepare_ra_workflow(profile, sources, proposal['source_bindings'], {})
    assert prepared['ready_for_output_check'], prepared['review']
    assert prepared['evidence']['용법 용량']['quote'] == regimen
    assert prepared['evidence']['포장단위']['quote'] == '30정'
    wrong = deepcopy(proposal['source_bindings'])
    wrong['용법 용량']['quote'] = regimen.replace('5일', '6일')
    with pytest.raises(ValueError, match='인용'):
        prepare_ra_workflow(profile, sources, wrong, {})
