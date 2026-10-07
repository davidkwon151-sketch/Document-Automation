"""Korean real-source quotes and storage ranges must survive both review passes."""

from decimal import Decimal
import json
from pathlib import Path

import pytest

from agent.pipeline import review_result
from agent.retrieve import find_conflicts
from agent.review import number_tokens, review_draft
from evals.ra_public import read_source, cited


@pytest.fixture
def hanmi():
    root = Path(__file__).resolve().parents[1]
    record = json.loads((root / 'evals/ra_korean_sources.json').read_text(encoding='utf-8'))['sources'][0]
    return read_source(record)


def test_original_korean_quotes_pass_generic_ra_and_export_preflight(hanmi):
    facts, sources, _ = hanmi
    draft = {'제목': '원자료 대조', '요약': '○ 추가 확인 필요함', '본문': '○ 추가 확인 필요함'}
    draft.update({fact['field_key']: cited(fact['value'], fact['source_id']) for fact in facts})
    result = {'draft': draft, 'sources': sources, 'domain': 'pharmaceutical_ra',
              'ra_workflow': 'product_approval',
              'template_profile': {'domain': 'pharmaceutical_ra', 'document_kind': 'application',
                                   'ra_product_variant': '한미플루 75mg'}}
    checked = review_result(result)
    assert checked['draft'] == draft
    assert not checked['blocking'], checked['warnings']
    assert not find_conflicts(sources)


@pytest.mark.parametrize(('original', 'changed'), [
    ('75mg', '85mg'), ('5일', '6일'), ('13', '12'), ('1~30℃', '1~35℃'),
    ('1~30℃', '1~30°F'), ('1~30℃', '30~1℃'),
])
def test_wrong_korean_dose_age_duration_temperature_and_unit_still_block(hanmi, original, changed):
    facts, sources, _ = hanmi
    fact = next(fact for fact in facts if original in fact['value']
                and fact['field_key'] != '제품명')
    draft = {'제목': '검토', '요약': '○ 추가 확인 필요함', '본문': '○ 추가 확인 필요함',
             fact['field_key']: cited(fact['value'].replace(original, changed), fact['source_id'])}
    checked = review_draft(draft, sources, auto_correct=False)
    assert checked['blocking']
    assert any(issue['code'] == 'number_mismatch' for issue in checked['warnings'])


def test_explicit_temperature_range_has_distinct_endpoints_and_shared_unit():
    tokens = number_tokens('실온(1~30℃)보관')
    assert [token['key'] for token in tokens] == [(Decimal(1), '°C'), (Decimal(30), '°C')]
    assert [token['scope'] for token in tokens] == [{'range:start'}, {'range:end'}]
    assert not find_conflicts([{'source_id': 'S1', 'text': '실온(1~30℃)보관'}])
    conflicts = find_conflicts([{'source_id': 'S1', 'text': '실온(1~30℃)보관'},
                               {'source_id': 'S2', 'text': '실온(1~35℃)보관'}])
    assert len(conflicts) == 1 and conflicts[0]['source_ids'] == ['S1', 'S2']


def test_range_does_not_hide_two_genuinely_conflicting_values():
    conflicts = find_conflicts([{'source_id': 'S1', 'text': '보관 온도 20℃임'},
                               {'source_id': 'S2', 'text': '보관 온도 30℃임'}])
    assert conflicts and conflicts[0]['unit'] == '°C'
