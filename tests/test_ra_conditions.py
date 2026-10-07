"""Operating RA rules: real EMA excerpts and separately labelled synthetic pairs.

No network/model calls or evaluator truth metadata in the inspected evidence.
"""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

import pytest
from pypdf import PdfReader

from agent.ra import inspect_ra_draft

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / 'evals/ra_public_sources.json').read_text(encoding='utf-8'))
CASES = [('Herzuma', '성상'), ('Benepali', '효능 효과'), ('Keppra', '용법 용량')]


def cite(text, identifier='S1'):
    return '\n'.join(line + f' [{identifier}]' if line.strip() else '' for line in text.splitlines())


@pytest.fixture(scope='module')
def actual():
    result = {}
    for record in MANIFEST['sources']:
        path = ROOT / record['path']
        if not path.is_file():
            continue
        before = sha256(path.read_bytes()).hexdigest()
        assert before == record['sha256']
        reader = PdfReader(path)
        # Only the relevant actual page is read here; full production M1 QA is separate.
        text = reader.pages[1].extract_text()
        assert sha256(path.read_bytes()).hexdigest() == before
        result[record['product_name']] = (record, text)
    return result


def real_pair(actual, name, field, *, compact=False):
    if name not in actual:
        pytest.skip('해당 EMA 원본 미확보: 실자료 조건 검수를 합성 성공으로 대체하지 않음')
    record, page = actual[name]
    fact = next(item for item in record['facts'] if item['field_key'] == field)
    assert ''.join(fact['value'].split()) in ''.join(page.split())
    source = {'source_id': 'S1', 'text': page, 'filename': record['filename'], 'page': 2,
              'product_name': name, 'product_variant': record['product_variant']}
    profile = {'ra_product_name': name, 'ra_product_variant': record['product_variant']}
    value = re.sub(r'\s+', ' ', fact['value']) if compact else fact['value']
    return value, source, profile


@pytest.mark.parametrize('name,field', CASES)
@pytest.mark.parametrize('compact', [False, True])
def test_real_complete_selected_quote_passes_long_page_and_visual_line_wraps(actual, name, field, compact):
    value, source, profile = real_pair(actual, name, field, compact=compact)
    assert len(source['text']) > 1500
    assert not {'regulatory_role', 'clinical_scope', 'complete_selected_paragraph'} & source.keys()
    draft = {field: cite(value)}
    before = deepcopy((draft, source))
    checked = inspect_ra_draft(draft, [source], profile=profile)
    assert not checked['blocking'], checked['issues']
    assert (draft, source) == before


@pytest.mark.parametrize('name,field', CASES)
def test_real_compare_only_errors_now_block_in_operating_ra_check(actual, name, field):
    value, source, profile = real_pair(actual, name, field)
    if name == 'Herzuma':
        source['text'] = source['product_variant']  # A known product-ID quote, not appearance evidence.
        bad = value
        code = 'ra_field_evidence_unverified'
    elif name == 'Benepali':
        bad = value.split(' when the response')[0]
        code = 'ra_condition_omitted'
    else:
        bad = value.split('However,')[0].strip()
        code = 'ra_condition_omitted'
    checked = inspect_ra_draft({field: cite(bad)}, [source], profile=profile)
    assert checked['blocking'] and any(issue['code'] == code for issue in checked['issues'])


@pytest.mark.parametrize('removed', [' (unless contraindicated)', 'based on physician\nassessment of seizure reduction versus potential side effects', 'after two weeks'])
def test_real_exception_physician_assessment_and_escalation_timing_are_material(actual, removed):
    name, field = ('Benepali', '효능 효과') if 'unless' in removed else ('Keppra', '용법 용량')
    value, source, profile = real_pair(actual, name, field)
    assert removed in value
    checked = inspect_ra_draft({field: cite(value.replace(removed, ''))}, [source], profile=profile)
    assert checked['blocking'] and any(issue['code'] == 'ra_condition_omitted' for issue in checked['issues'])


@pytest.mark.parametrize('name,field', [('Benepali', '효능 효과'), ('Keppra', '용법 용량')])
def test_verified_context_detects_conditions_outside_the_cited_pdf_line(actual, name, field):
    value, source, profile = real_pair(actual, name, field)
    chunk = value.splitlines()[0] if name == 'Benepali' else 'The initial therapeutic dose is 500 mg twice daily.'
    start = source['text'].find(chunk)
    assert start >= 0
    proof = {**source, 'context_text': source['text'], 'context_start': start,
             'context_end': start + len(chunk), 'text': chunk}
    checked = inspect_ra_draft({field: cite(chunk)}, [proof], profile=profile)
    assert checked['blocking'] and any(issue['code'] == 'ra_condition_omitted' for issue in checked['issues'])


@pytest.mark.parametrize('damage', ['wrong_text', 'negative_start', 'missing_end', 'non_integer'])
def test_invalid_context_span_fails_closed_without_rewriting(damage):
    source = {'source_id': 'S1', 'text': '5 mg', 'context_text': 'Dose 5 mg.', 'context_start': 5, 'context_end': 9}
    if damage == 'wrong_text':
        source['context_text'] = 'Dose 8 mg.'
    elif damage == 'negative_start':
        source['context_start'] = -1
    elif damage == 'missing_end':
        source.pop('context_end')
    else:
        source['context_start'] = '5'
    draft = {'Dose': '5 mg [S1]'}
    before = deepcopy((draft, source))
    checked = inspect_ra_draft(draft, [source])
    assert checked['blocking'] and any(issue['code'] == 'ra_context_invalid' for issue in checked['issues'])
    assert (draft, source) == before


def test_context_does_not_turn_product_name_citation_into_appearance_evidence():
    context = 'Nova 80 mg tablets\nWhite lyophilised powder.'
    chunk = 'Nova 80 mg tablets'
    source = {'source_id': 'S1', 'text': chunk, 'context_text': context,
              'context_start': 0, 'context_end': len(chunk)}
    checked = inspect_ra_draft({'성상': 'White lyophilised powder. [S1]'}, [source])
    assert checked['blocking'] and any(issue['code'] == 'ra_field_evidence_unverified' for issue in checked['issues'])


def test_appearance_summary_and_selected_product_scope_are_grounded():
    source = {'source_id': 'S1', 'text': 'White to pale yellow lyophilised powder.', 'product_name': 'Nova'}
    draft = {'Appearance': 'lyophilised powder. [S1]'}
    assert not inspect_ra_draft(draft, [source], profile={'ra_product_name': 'Nova'})['blocking']
    assert inspect_ra_draft(draft, [source], profile={'ra_product_name': 'Other'})['blocking']
    assert inspect_ra_draft({'Appearance': 'lyophilised powder.'}, [source])['blocking']


@pytest.mark.parametrize('condition', ['when previous therapy has been inadequate', 'unless previous therapy is contraindicated'])
def test_synthetic_new_drug_conditions_are_not_company_or_value_specific(condition):
    text = f'Nova is indicated for the treatment of arthritis in adults {condition}.'
    source = {'source_id': 'S1', 'text': text, 'product_name': 'Nova'}
    good = {'Therapeutic indication': cite(text)}
    bad = {'Therapeutic indication': cite(text.split(' ' + condition)[0])}
    assert not inspect_ra_draft(good, [source])['blocking']
    checked = inspect_ra_draft(bad, [source])
    assert checked['blocking'] and any(issue['code'] == 'ra_condition_omitted' for issue in checked['issues'])
    # The qualified target can be extracted without copying the whole source sentence.
    summary = {'Therapeutic indication': cite('arthritis in adults ' + condition + '.')}
    assert not inspect_ra_draft(summary, [source])['blocking']


SYNTHETIC_REGIMEN = (
    'The initial therapeutic dose is 80 mg twice daily. '
    'This dose can be started on the first day of treatment. '
    'However, a lower initial dose of 40 mg twice daily may be given based on physician '
    'assessment of clinical response versus potential side effects. '
    'This can be increased to 80 mg twice daily after three weeks.'
)


def test_synthetic_new_regimen_checks_linked_conditions_but_accepts_scalar_and_benign_summary():
    source = {'source_id': 'S1', 'text': '4.2 Posology\nAdults\n\n' + SYNTHETIC_REGIMEN,
              'product_name': 'Nova', 'product_variant': 'Nova 80 mg tablets'}
    assert not inspect_ra_draft({'Dosage': cite(SYNTHETIC_REGIMEN)}, [source])['blocking']
    summary = SYNTHETIC_REGIMEN.replace('This dose can be started on the first day of treatment. ', '')
    assert not inspect_ra_draft({'Dosage': cite(summary)}, [source])['blocking']
    scalar = {'Initial dose': '80 mg twice daily [S1]'}
    assert not inspect_ra_draft(scalar, [source])['blocking']
    checked = inspect_ra_draft({'Dosage': cite(SYNTHETIC_REGIMEN.split('However,')[0])}, [source])
    assert checked['blocking'] and any(issue['code'] == 'ra_condition_omitted' for issue in checked['issues'])


def test_unrelated_indication_paragraphs_and_new_sections_are_not_required_wholesale():
    adult = 'Nova is indicated for arthritis in adults when previous therapy has been inadequate.'
    other = 'Juvenile arthritis\nNova is indicated in children when conventional therapy has been inadequate.'
    source = {'source_id': 'S1', 'text': adult + '\n\n' + other, 'product_name': 'Nova'}
    assert not inspect_ra_draft({'Therapeutic indication': cite(adult)}, [source])['blocking']
    initial = 'The initial therapeutic dose is 80 mg twice daily.'
    source['text'] = initial + '\n4.3 Other population\nHowever, a lower dose of 40 mg may be given based on physician assessment.'
    assert not inspect_ra_draft({'Dosage': cite(initial)}, [source])['blocking']


def test_same_qualifier_from_another_product_cannot_cover_omitted_condition():
    condition = 'when previous therapy has been inadequate'
    sources = [{'source_id': 'S1', 'text': f'Nova is indicated for arthritis in adults {condition}.', 'product_name': 'Nova'},
               {'source_id': 'S2', 'text': f'Other is indicated for arthritis in adults {condition}.', 'product_name': 'Other'}]
    good = {'Therapeutic indication': cite(sources[0]['text']) + '\n' + cite(sources[1]['text'], 'S2')}
    assert not inspect_ra_draft(good, sources)['blocking']
    bad = {'Therapeutic indication': cite(sources[0]['text'].split(' ' + condition)[0]) + '\n' + cite(sources[1]['text'], 'S2')}
    checked = inspect_ra_draft(bad, sources)
    assert checked['blocking'] and any(issue['code'] == 'ra_condition_omitted' for issue in checked['issues'])


def test_verified_context_does_not_require_an_explicitly_other_product_alternative():
    chunk = 'The initial therapeutic dose is 80 mg twice daily.'
    context = ('Product: Nova\n' + chunk + ' However, Product: Other, a lower initial dose of 40 mg '
               'may be given based on physician assessment.')
    start = context.index(chunk)
    source = {'source_id': 'S1', 'text': chunk, 'context_text': context,
              'context_start': start, 'context_end': start + len(chunk), 'product_name': 'Nova'}
    checked = inspect_ra_draft({'Dosage': cite(chunk)}, [source], profile={'ra_product_name': 'Nova'})
    assert not checked['blocking'], checked['issues']


@pytest.mark.parametrize('field', ['본문', 'custom_field_17'])
@pytest.mark.parametrize('clinical', ['indication', 'dosage'])
def test_generic_or_arbitrary_field_name_does_not_hide_clinical_conditions(field, clinical):
    if clinical == 'indication':
        source_text = 'Nova is indicated for arthritis in adults when previous therapy has been inadequate.'
        shortened = source_text.split(' when')[0]
    else:
        source_text = SYNTHETIC_REGIMEN
        shortened = source_text.split('However,')[0]
    source = {'source_id': 'S1', 'text': source_text, 'product_name': 'Nova'}
    assert not inspect_ra_draft({field: cite(source_text)}, [source])['blocking']
    checked = inspect_ra_draft({field: cite(shortened)}, [source])
    assert checked['blocking'] and any(issue['code'] == 'ra_condition_omitted' for issue in checked['issues'])


def test_broad_regimen_field_cannot_use_the_narrow_scalar_cell_exemption():
    source = {'source_id': 'S1', 'text': SYNTHETIC_REGIMEN, 'product_name': 'Nova'}
    scalar = '80 mg twice daily [S1]'
    assert not inspect_ra_draft({'Initial dose': scalar}, [source])['blocking']
    assert inspect_ra_draft({'Dosage': scalar}, [source])['blocking']
    profile = {'fields': [{'value_key': 'cell_17', 'label': '용법 용량'}]}
    checked = inspect_ra_draft({'cell_17': scalar}, [source], profile=profile)
    assert checked['blocking'] and any(issue['code'] == 'ra_condition_omitted' for issue in checked['issues'])


def test_user_confirmed_appearance_label_applies_to_arbitrary_mapping_key():
    profile = {'fields': [{'value_key': 'cell_2', 'label': '성상'}]}
    source = {'source_id': 'S1', 'text': 'Nova 80 mg tablets'}
    checked = inspect_ra_draft({'cell_2': 'White lyophilised powder. [S1]'}, [source], profile=profile)
    assert checked['blocking'] and any(issue['code'] == 'ra_field_evidence_unverified' for issue in checked['issues'])


@pytest.fixture(scope='module')
def m1_documents():
    from parsers import parse_file
    result = {}
    for record in MANIFEST['sources']:
        if record['product_name'] not in {'Benepali', 'Keppra'}:
            continue
        path = ROOT / record['path']
        if not path.is_file():
            continue
        before = sha256(path.read_bytes()).hexdigest()
        assert before == record['sha256']
        parsed = parse_file(path)
        assert len(parsed['페이지/시트 정보']) == record['page_count']
        assert sha256(path.read_bytes()).hexdigest() == before
        # These are selected product metadata, not evaluator role/completeness data.
        parsed.update(product_name=record['product_name'], product_variant=record['product_variant'], document_sha256=before)
        result[record['product_name']] = (record, parsed)
    return result


@pytest.mark.parametrize('name,field,anchor', [('Benepali', '효능 효과', 'is indicated'),
                                            ('Keppra', '용법 용량', 'The initial therapeutic dose')])
def test_actual_production_m1_and_retrieval_chunk_block_a_cut_off_clinical_condition(m1_documents, name, field, anchor):
    from agent.retrieve import chunk_documents
    if name not in m1_documents:
        pytest.skip('해당 실제 EMA PDF 미확보: M1 운영 경로는 미검증 상태임')
    record, document = m1_documents[name]
    chunks = chunk_documents([document])
    proof = next(chunk for chunk in chunks if chunk['page'] == 2 and anchor in chunk['text']
                 and (name in chunk['text'] if name == 'Benepali' else True))
    assert proof['context_text'][proof['context_start']:proof['context_end']] == proof['text']
    assert proof['context_text'] == next(block['본문'] for block in document['페이지/시트 정보'] if block['페이지'] == proof['page'])
    assert len(proof['text']) < len(proof['context_text'])
    assert proof['filename'] == record['filename']
    assert proof['document_sha256'] == record['sha256']
    draft = {field: cite(proof['text'], proof['source_id'])}
    before = deepcopy(draft)
    checked = inspect_ra_draft(draft, [proof], profile={'ra_product_name': name, 'ra_product_variant': record['product_variant']})
    assert checked['blocking'] and any(issue['code'] == 'ra_condition_omitted' for issue in checked['issues']), checked
    assert draft == before
    assert sha256((ROOT / record['path']).read_bytes()).hexdigest() == record['sha256']
