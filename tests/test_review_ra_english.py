"""Offline numerical guards for complete, line-wrapped EMA quotations.

The collected PDFs are optional local evidence and never downloaded by tests.
Neither these checks nor passing quotations certify clinical or submission accuracy.
"""

from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re

import pytest
from pypdf import PdfReader

from agent.review import number_tokens, review_draft
from agent.retrieve import find_conflicts

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / 'evals/ra_public_sources.json').read_text(encoding='utf-8'))
FACTS = [(record, fact) for record in MANIFEST['sources'] for fact in record['facts']]


def review(value, evidence, field='용법 용량'):
    cited = '\n'.join(f'{line} [S1]' if line.strip() else line for line in value.splitlines())
    draft = {'제목': '원문 검토', '요약': '○ 추가 확인 필요함', '본문': '○ 추가 확인 필요함', field: cited}
    return review_draft(draft, [{'source_id': 'S1', 'text': evidence}], auto_correct=False)


def fact(product, field):
    return next(item['exact_quote'] for record, item in FACTS
                if record['product_name'] == product and item['field_key'] == field)


@pytest.mark.parametrize(('record', 'item'), FACTS,
                         ids=[f"{record['product_name']}:{item['field_key']}" for record, item in FACTS])
def test_every_manifest_fact_passes_without_numeric_skip(record, item):
    result = review(item['value'], item['exact_quote'], item['field_key'])
    assert not result['blocking'], result['warnings']
    assert not result['warnings']


@pytest.mark.parametrize(('value', 'evidence'), [
    ('3 years.', '3 years.'), ('3 year', '3 years.'),
    ('Unopened vial\n\n6 years.', 'Unopened vial\n\n6 years.'),
])
def test_english_duration_is_not_assigned_an_unstated_korean_field_context(value, evidence):
    assert not review(value, evidence, '저장방법 및 유효기간')['blocking']


@pytest.mark.parametrize(('value', 'evidence'), [
    ('4 years.', '3 years.'), ('3 months.', '3 years.'),
    ('Unopened vial\n\n6 months.', 'Unopened vial\n\n6 years.'),
])
def test_wrong_duration_value_or_unit_still_blocks(value, evidence):
    result = review(value, evidence, '저장방법 및 유효기간')
    assert result['blocking']
    assert any(issue['code'] == 'number_mismatch' for issue in result['warnings'])


def test_loading_and_maintenance_are_separate_even_in_one_source():
    quote = fact('Herzuma', '용법 용량')
    tokens = number_tokens(quote)
    assert [(token['key'], token['role']) for token in tokens] == [
        ((Decimal(8), 'mg/kg'), 'loading'), ((Decimal(6), 'mg/kg'), 'maintenance')]
    assert tokens[1]['scope'] == {'interval:3/week'}
    assert not find_conflicts([{'source_id': 'S1', 'text': quote}])
    assert not review(re.sub(r'\s+', ' ', quote), quote)['blocking']


def test_two_different_loading_doses_are_still_a_source_conflict():
    quote = fact('Herzuma', '용법 용량')
    conflicts = find_conflicts([{'source_id': 'S1', 'text': quote},
                               {'source_id': 'S2', 'text': quote.replace('8 mg/kg', '9 mg/kg')}])
    assert len(conflicts) == 1
    assert conflicts[0]['role'] == 'loading'
    assert set(conflicts[0]['values']) == {'8', '9'}


@pytest.mark.parametrize('changed', [
    lambda text: text.replace('8 mg/kg', '99 mg/kg').replace('6 mg/kg', '8 mg/kg').replace('99 mg/kg', '6 mg/kg'),
    lambda text: text.replace('8 mg/kg', '8 mg/mL'),
    lambda text: text.replace('maintenance dose', 'loading dose'),
    lambda text: text.replace('three-weekly intervals', 'two-weekly intervals'),
])
def test_wrong_loading_maintenance_unit_role_or_interval_blocks(changed):
    quote = fact('Herzuma', '용법 용량')
    assert review(changed(quote), quote)['blocking']


def test_once_and_twice_weekly_doses_survive_line_wrapping():
    quote = fact('Benepali', '용법 용량')
    doses = [token for token in number_tokens(quote) if token['key'][1] == 'mg']
    assert [(token['key'][0], token['scope']) for token in doses] == [
        (Decimal(25), {'frequency:2/weekly'}), (Decimal(50), {'frequency:1/weekly'})]
    assert not find_conflicts([{'source_id': 'S1', 'text': quote}])
    assert not review(quote.replace('50 mg\nadministered', '50 mg administered'), quote)['blocking']


@pytest.mark.parametrize('changed', [
    lambda text: text.replace('25 mg', '99 mg').replace('50 mg', '25 mg').replace('99 mg', '50 mg'),
    lambda text: text.replace('twice weekly', 'twice daily'),
    lambda text: text.replace('50 mg', '50 mcg'),
    lambda text: text.replace('section 5.1', 'section 5.2'),
])
def test_alternative_dose_frequency_unit_and_reference_numbers_cannot_be_interchanged(changed):
    quote = fact('Benepali', '용법 용량')
    assert review(changed(quote), quote)['blocking']


def test_initial_lower_initial_and_increased_keppra_doses_do_not_conflict():
    quote = fact('Keppra', '용법 용량')
    doses = [token for token in number_tokens(quote) if token['key'][1] == 'mg']
    assert [token['role'] for token in doses] == ['initial', 'lower_initial', 'increase']
    assert all(token['scope'] == {'frequency:2/daily'} for token in doses)
    assert not find_conflicts([{'source_id': 'S1', 'text': quote}])
    wrong = quote.replace('initial therapeutic dose is 500 mg', 'initial therapeutic dose is 250 mg')
    assert review(wrong, quote)['blocking']
    assert review(quote.replace('twice daily', 'twice weekly'), quote)['blocking']


def test_correction_does_not_duplicate_a_unit_wrapped_onto_another_line():
    draft = {'제목': '원문 검토', '요약': '○ 추가 확인 필요함', '본문': '○ 추가 확인 필요함',
             '사용기간': '4 [S1]\nyears [S1]'}
    result = review_draft(draft, [{'source_id': 'S1', 'text': '3 years'}])
    assert result['blocking']
    assert result['draft'] == draft


@pytest.mark.parametrize(('raw', 'unit'), [
    ('8 mg/kg', 'mg/kg'), ('25 mg/4 mL', 'mg/4mL'), ('10 µg', 'mcg'),
    ('10 μg', 'mcg'), ('2 mL', 'mL'), ('6 YEARS', 'year'), ('12 months', 'month'),
])
def test_explicit_english_unit_is_retained_not_a_bare_number(raw, unit):
    tokens = number_tokens(raw)
    assert len(tokens) == 1
    assert tokens[0]['key'][1] == unit


@pytest.mark.parametrize('record', MANIFEST['sources'], ids=lambda record: record['product_name'])
def test_actual_pdf_sha_and_complete_page_quotes_are_independently_rechecked(record):
    path = ROOT / record['path']
    if not path.exists():
        pytest.skip('실제 수집 원본은 Git 재배포 대상이 아니며 테스트에서 다운로드하지 않음')
    assert sha256(path.read_bytes()).hexdigest() == record['sha256']
    reader = PdfReader(path)
    normalize = lambda text: re.sub(r'\s+', '', text)
    for item in record['facts']:
        page = reader.pages[item['page'] - 1].extract_text() or ''
        assert normalize(item['exact_quote']) in normalize(page)
        assert normalize(item['value']) == normalize(item['exact_quote'])
        assert not review(item['value'], item['exact_quote'], item['field_key'])['blocking']
