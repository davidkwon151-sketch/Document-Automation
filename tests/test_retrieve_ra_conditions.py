"""Explicit RA scope guards; no network, model call or clinical certification."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from agent.retrieve import _conflict_tokens, chunk_documents, find_conflicts

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / 'evals/ra_extended_public_sources.json').read_text(encoding='utf-8'))


def source(text, identifier='S1', **metadata):
    return {'source_id': identifier, 'text': text, **metadata}


@pytest.mark.parametrize(('left', 'right'), [
    ('The starting dose is 2.5 mg once weekly.', 'The dose is increased to 5 mg once weekly.'),
    ('Adults: the maximum dose is 15 mg once weekly.', 'Paediatric population: the maximum dose is 10 mg once weekly.'),
    ('The initial dose is 5 mg once weekly.', 'The maintenance dose is 10 mg once weekly.'),
    ('초기 용량은 5 mg임.', '유지 용량은 10 mg임.'),
    ('성인 최대 용량은 15 mg임.', '소아 최대 용량은 10 mg임.'),
    ('Each vial contains 5 mg.', 'Each pen contains 10 mg.'),
    ('Each mL contains 5 mg.', 'Each dose contains 10 mg.'),
    ('Unopened vial: shelf life is 3 years.', 'Reconstituted vial: shelf life is 1 year.'),
    ('Before first use\nShelf life: 3 years.', 'After first opening\nShelf life: 1 year.'),
    ('After 4 weeks, the dose is increased to 0.5 mg once weekly.',
     'After at least 4 weeks with a dose of 0.5 mg, the dose can be increased to 1 mg once weekly.'),
])
def test_explicit_different_scopes_are_not_conflicts(left, right):
    metadata = {'product_name': 'Example', 'product_variant': 'Example solution'}
    assert not find_conflicts([source(left, **metadata), source(right, 'S2', **metadata)])


@pytest.mark.parametrize('single_source', [False, True])
@pytest.mark.parametrize(('left', 'right'), [
    ('Adults: the maximum dose is 15 mg once weekly.', 'Adults: the maximum dose is 10 mg once weekly.'),
    ('The starting dose is 2.5 mg once weekly.', 'The starting dose is 5 mg once weekly.'),
    ('The dose is increased to 5 mg once weekly.', 'The dose is increased to 10 mg once weekly.'),
    ('After at least 4 weeks with a dose of 0.5 mg, the dose is increased to 1 mg once weekly.',
     'After at least 4 weeks with a dose of 0.5 mg, the dose is increased to 2 mg once weekly.'),
    ('성인 최대 용량은 15 mg임.', '성인 최대 용량은 10 mg임.'),
    ('Before first use\nShelf life: 3 years.', 'Before first use\nShelf life: 2 years.'),
    ('Refrigerator (2ºC – 8ºC).', 'Refrigerator (2ºC – 9ºC).'),
])
def test_same_scope_different_values_conflict_even_in_one_source(left, right, single_source):
    metadata = {'product_name': 'Example', 'product_variant': 'Example solution'}
    sources = ([source(left + '\n\n' + right, **metadata)] if single_source else
               [source(left, **metadata), source(right, 'S2', **metadata)])
    assert find_conflicts(sources)


@pytest.mark.parametrize(('left', 'right'), [
    ('Adults: the maximum dose is 15 mg once weekly.', 'The dose is 10 mg once weekly.'),
    ('The initial dose is 5 mg once weekly.', 'The dose is 10 mg once weekly.'),
    ('A사 실적 매출 120만원임', '매출 130만원임'),
    ('목표 매출 120만원임', '매출 130만원임'),
])
def test_missing_scope_or_business_role_remains_conservative(left, right):
    assert find_conflicts([source(left), source(right, 'S2')])


def test_other_product_and_strength_remain_separate_without_filename_inference():
    assert not find_conflicts([
        source('The recommended dose is 5 mg once weekly.', product_name='ExampleA', product_variant='ExampleA 5 mg tablet'),
        source('The recommended dose is 10 mg once weekly.', 'S2', product_name='ExampleB', product_variant='ExampleB 10 mg tablet')])
    assert not find_conflicts([
        source('The recommended dose is 5 mg once weekly.', product_name='Example', product_variant='Example 5 mg tablet'),
        source('The recommended dose is 10 mg once weekly.', 'S2', product_name='Example', product_variant='Example 10 mg tablet')])
    assert find_conflicts([source('The recommended dose is 5 mg once weekly.', filename='ExampleA.pdf'),
                           source('The recommended dose is 10 mg once weekly.', 'S2', filename='ExampleB.pdf')])


def test_wrapped_context_preserves_explicit_population_when_chunk_has_only_value():
    text = ('Adults\nThe maximum dose is 15 mg once weekly.\n\n'
            'Paediatric population\nThe maximum dose is 10 mg once weekly.')
    document = {'파일명': '공식.pdf', '본문': text, '표 목록': [], '페이지/시트 정보': [
        {'본문': text, '페이지': 4, '위치': '4페이지', '표 목록': []}]}
    chunks = chunk_documents([document])
    assert all(chunk['context_text'][chunk['context_start']:chunk['context_end']] == chunk['text'] for chunk in chunks)
    assert not find_conflicts(chunks)
    copied = deepcopy(chunks)
    assert find_conflicts([*chunks, {**chunks[1], 'source_id': 'Sother', 'text': chunks[1]['text'].replace('15', '10'),
                                   'context_text': text.replace('15', '10')}])
    assert chunks == copied


@pytest.mark.parametrize('mutation', [
    {'context_start': True}, {'context_start': 1}, {'context_end': 10000},
    {'context_text': 'Paediatric population\nThe maximum dose is 10 mg once weekly.'},
])
def test_invalid_context_cannot_manufacture_scope(mutation):
    text = 'The maximum dose is 15 mg once weekly.'
    context = 'Adults\n' + text
    item = source(text, context_text=context, context_start=7, context_end=len(context))
    with pytest.raises(ValueError, match='문자 범위'):
        find_conflicts([{**item, **mutation}])


@pytest.mark.parametrize('record', MANIFEST['sources'], ids=lambda record: record['id'])
def test_all_complete_extended_quotes_have_no_false_conflict_and_source_is_immutable(record):
    items = [source(fact['exact_quote'], f'S{index}', product_name=record['product_name'],
                    product_variant=record['product_variant']) for index, fact in enumerate(record['facts'], 1)]
    before = deepcopy(items)
    assert not find_conflicts(items)
    assert items == before


@pytest.mark.parametrize('record', MANIFEST['sources'], ids=lambda record: record['id'])
def test_actual_pdf_permission_sha_page_quotes_and_conflicts(record):
    from evals.ra_public import read_source

    if not (ROOT / record['path']).is_file():
        pytest.skip('공식 원본은 로컬 스냅샷이며 pytest에서 다운로드하지 않음')
    _, items, _ = read_source(record)
    assert not find_conflicts(items)


def test_duration_and_refrigeration_ranges_keep_units_endpoints_and_original_glyphs():
    text = ('After 4 weeks, the dose is increased to 5 mg once weekly.\n\n'
            'After first opening\nPre-filled pens: 6 weeks.\n\n'
            'Store in a refrigerator (2ºC – 8ºC).')
    item = source(text, product_name='Example')
    before = deepcopy(item)
    assert not find_conflicts([item])
    changed = {**item, 'source_id': 'S2', 'text': text.replace('8ºC', '9ºC')}
    conflicts = find_conflicts([item, changed])
    assert len(conflicts) == 1
    assert conflicts[0]['unit'] == '°C'
    assert conflicts[0]['period'] == ['range:end']
    assert set(conflicts[0]['values']) == {'8', '9'}
    assert item == before


@pytest.mark.parametrize('lead', ['Recommended dose is', 'Maximum dose is', 'Example dose is'])
@pytest.mark.parametrize('single_source', [False, True])
def test_ordinary_dose_sentence_cannot_turn_its_own_value_into_a_strength(lead, single_source):
    left, right = f'{lead} 5 mg once weekly.', f'{lead} 10 mg once weekly.'
    metadata = {'product_name': 'Example', 'product_variant': 'Example solution'}
    sources = ([source(left + '\n\n' + right, **metadata)] if single_source else
               [source(left, **metadata), source(right, 'S2', **metadata)])
    before = deepcopy(sources)
    assert find_conflicts(sources)
    assert sources == before


@pytest.mark.parametrize('form', ['tablets', 'Tablets', 'solution for injection', 'capsules'])
def test_explicit_product_strength_and_form_heading_preserves_distinct_variant(form):
    left = f'Example 5 mg {form}\nThe recommended dose is 5 mg once weekly.'
    right = f'Example 10 mg {form}\nThe recommended dose is 10 mg once weekly.'
    assert not find_conflicts([source(left), source(right, 'S2')])
    same_strength_wrong_dose = right.replace('Example 10 mg', 'Example 5 mg')
    assert find_conflicts([source(left), source(same_strength_wrong_dose, 'S3')])


@pytest.mark.parametrize('single_source', [False, True])
@pytest.mark.parametrize('wrapped', [False, True])
def test_full_source_and_numeric_only_chunks_detect_same_population_maximum_conflict(single_source, wrapped):
    separator = '\n' if wrapped else ' '
    left = 'Adults\nThe maximum dose is' + separator + '15 mg.'
    right = 'Adults\nThe maximum dose is' + separator + '10 mg.'
    texts = [left + '\n\n' + right] if single_source else [left, right]
    documents = [{'파일명': f'원문{index}.pdf', '본문': text, '표 목록': [],
                  '페이지/시트 정보': [{'본문': text, '페이지': 4, '위치': '4페이지', '표 목록': []}]}
                 for index, text in enumerate(texts)]
    chunks = chunk_documents(documents)
    before = deepcopy(chunks)
    assert find_conflicts(chunks)
    assert chunks == before


def test_population_difference_survives_numeric_only_wrapped_chunks():
    text = ('Adults\nThe maximum dose is\n15 mg.\n\n'
            'Paediatric population\nThe maximum dose is\n10 mg.')
    chunks = chunk_documents([{'파일명': '원문.pdf', '본문': text, '표 목록': [], '페이지/시트 정보': [
        {'본문': text, '페이지': 4, '표 목록': []}]}])
    assert not find_conflicts(chunks)
    numeric = [chunk for chunk in chunks if chunk['text'][0].isdigit()]
    assert not find_conflicts(numeric)
    # Complete deletion leaves a legacy unknown-scope observation. It must be
    # conservative, not invent adult/child scopes from filenames or source IDs.
    deleted = [{k: v for k, v in chunk.items() if k not in {'context_text', 'context_start', 'context_end'}}
               for chunk in numeric]
    assert not any(token.get('material_scope') for chunk in deleted
                   for token in _conflict_tokens(chunk, set()))


@pytest.mark.parametrize('missing', ['context_text', 'context_start', 'context_end'])
def test_partial_context_deletion_does_not_fall_back_to_legacy_scope(missing):
    text = '15 mg.'
    context = 'Adults\nThe maximum dose is\n' + text
    start = context.index(text)
    item = source(text, context_text=context, context_start=start, context_end=start + len(text))
    del item[missing]
    with pytest.raises(ValueError, match='누락|문자 범위'):
        find_conflicts([item])


def test_bound_context_does_not_import_numbers_outside_cited_fragment():
    context = 'The starting dose is 2.5 mg. Adults: the maximum dose is 15 mg.'
    fragment = '15 mg.'
    start = context.index(fragment)
    item = source(fragment, context_text=context, context_start=start, context_end=start + len(fragment))
    tokens = list(_conflict_tokens(item, set()))
    assert len(tokens) == 1
    assert str(tokens[0]['key'][0]) == '15'
    assert tokens[0]['context'] == 'dose'
    assert tokens[0]['role'] == 'maximum'


@pytest.mark.parametrize(('number', 'fragment'), [('15', '1'), ('2.50', '2.5'), ('2.00', '2'), ('0.500', '0.50')])
def test_partial_number_fragment_cannot_be_promoted_to_full_context_number(number, fragment):
    context = f'The maximum dose is {number} mg.'
    start = context.index(number)
    item = source(fragment, context_text=context, context_start=start, context_end=start + len(fragment))
    with pytest.raises(ValueError, match='숫자'):
        find_conflicts([item])


@pytest.mark.parametrize('template', [
    'Example {} mg tablets are administered once weekly.',
    'Example {} mg tablets should be taken once weekly.',
    'Example {} mg solution is the recommended dose once weekly.',
    'Recommended {} mg tablets taken once weekly.',
])
def test_product_strength_form_reference_in_an_instruction_is_not_a_variant_heading(template):
    metadata = {'product_name': 'Example', 'product_variant': 'Example solution'}
    assert find_conflicts([source(template.format(5), **metadata), source(template.format(10), 'S2', **metadata)])


@pytest.mark.parametrize('record', MANIFEST['sources'], ids=lambda record: record['id'])
def test_every_complete_fact_remains_normal_after_real_paragraph_chunking(record):
    documents = [{'파일명': record['filename'], '본문': item['exact_quote'], '표 목록': [],
                  'product_name': record['product_name'], 'product_variant': record['product_variant'],
                  '페이지/시트 정보': [{'본문': item['exact_quote'], '페이지': item['page'], '표 목록': []}]}
                 for item in record['facts']]
    assert not find_conflicts(chunk_documents(documents))
