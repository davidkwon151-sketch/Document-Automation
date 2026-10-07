"""Document section numbers are identifiers; measurements retain every token."""
from copy import deepcopy

import pytest

from agent.retrieve import _conflict_tokens, chunk_documents, find_conflicts

@pytest.mark.parametrize('text', ['See Section 9.1.2.', 'See section 9.1.2)',
    'See Sections 9.1.2.', '참조 절: 9.1.2', '절 번호: 9.1.2', '제9.1.2절 참조'])
def test_only_explicit_section_reference_numbers_are_not_quantities(text):
    source = {'source_id': 'SSection', 'filename': 'synthetic-protocol.pdf', 'text': text}
    before = deepcopy(source)
    tokens = list(_conflict_tokens(source, set()))
    assert tokens == []
    assert source == before

@pytest.mark.parametrize('unit', ['mg', 'mg/mL', 'ng', 'µg', '%', 'mL'])
def test_quantity_after_section_shaped_number_is_never_dropped(unit):
    source = {'source_id': 'SSection', 'filename': 'synthetic-protocol.pdf',
              'text': f'Section 9.1.2 {unit}'}
    tokens = list(_conflict_tokens(source, set()))
    assert tokens and any(token['key'][1] for token in tokens)

def test_explicit_section_reference_preserves_same_line_dose_conflict():
    text = 'Dose: 5 mg (Section 9.1.2).\nDose: 50 mg (Section 9.1.2).'
    chunks = chunk_documents([{'파일명': 'synthetic.pdf', '본문': text, '표 목록': [],
        '페이지/시트 정보': [{'페이지': 1, '본문': text, '표 목록': []}]}])
    conflicts = find_conflicts(chunks)
    assert any(set(conflict['values']) == {'5', '50'} for conflict in conflicts)
    assert not any(conflict['context'].casefold() == 'section' for conflict in conflicts)
    assert '\n'.join(chunk['text'] for chunk in chunks) == text

def test_decimal_without_reference_label_remains_numeric():
    tokens = list(_conflict_tokens({'source_id': 'SDecimal', 'filename': 'synthetic.pdf',
                                   'text': 'Value: 9.1 mg and 2 mg'}, set()))
    assert len(tokens) == 2

def test_reference_is_identified_in_exact_original_context_of_wrapped_chunk():
    context = 'Dose: 5 mg; please see Section 9.1.2 for the procedure.'
    text = 'Section 9.1.2'
    start = context.index(text)
    source = {'source_id': 'SWrapped', 'filename': 'synthetic.pdf', 'text': text,
              'context_text': context, 'context_start': start, 'context_end': start + len(text)}
    assert list(_conflict_tokens(source, set())) == []
