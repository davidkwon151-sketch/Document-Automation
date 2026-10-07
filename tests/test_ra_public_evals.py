"""Internet snapshot integrity and honest reporting, with no network or API calls."""

from copy import deepcopy
from hashlib import sha256
import json

import pytest
from reportlab.pdfgen import canvas

from evals import ra_public


@pytest.fixture
def snapshot(tmp_path):
    path = tmp_path / 'product.pdf'
    pdf = canvas.Canvas(str(path))
    for y, text in [(750, 'MARKETING AUTHORISATION HOLDER'), (730, 'Example MAH Ltd.'),
                    (710, 'Alpha 25 mg tablets'), (690, 'Shelf life: 3 years.')]:
        pdf.drawString(30, y, text)
    pdf.save()
    record = {'id': 'snapshot', 'path': path.name, 'filename': path.name,
              'sha256': sha256(path.read_bytes()).hexdigest(), 'page_count': 1,
              'company': {'name': 'Example MAH Ltd.', 'exact_quote': 'Example MAH Ltd.',
                          'page': 1, 'role': 'marketing_authorisation_holder'},
              'product_name': 'Alpha', 'product_variant': 'Alpha 25 mg tablets',
              'source_url': 'https://example.test/product.pdf', 'jurisdiction': 'EU',
              'facts': [{'field_key': '제품명', 'value': 'Alpha 25 mg tablets',
                         'exact_quote': 'Alpha 25 mg tablets', 'page': 1, 'role': 'product_name'},
                        {'field_key': '저장방법 및 유효기간', 'value': 'Shelf life: 3 years.',
                         'exact_quote': 'Shelf life: 3 years.', 'page': 1, 'role': 'shelf_life'}]}
    return tmp_path, record


def test_snapshot_preserves_exact_quotes_page_sha_and_scope(snapshot):
    root, record = snapshot
    facts, sources, document = ra_public.read_source(record, root)
    assert [source['text'] for source in sources] == [fact['value'] for fact in record['facts']]
    assert all(source['page'] == 1 and source['document_sha256'] == record['sha256'] for source in sources)
    assert all(source['product_variant'] == 'Alpha 25 mg tablets' for source in sources)
    assert document['페이지/시트 정보'][1]['regulatory_role'] == 'shelf_life'
    assert facts[0]['source_id'] != facts[1]['source_id']


@pytest.mark.parametrize('mutation', ['sha', 'pages', 'quote', 'value', 'fact_page', 'company',
                                      'role', 'company_page', 'variant', 'fact_variant', 'path'])
def test_corrupted_snapshot_or_scope_fails_closed(snapshot, mutation):
    root, original = snapshot
    record = deepcopy(original)
    if mutation == 'sha':
        record['sha256'] = '0' * 64
    elif mutation == 'pages':
        record['page_count'] = 2
    elif mutation == 'quote':
        record['facts'][1]['exact_quote'] = 'Shelf life: 9 years.'
    elif mutation == 'value':
        record['facts'][1]['value'] = '3 years.'  # The selected entire quote cannot be shortened.
    elif mutation == 'fact_page':
        record['facts'][1]['page'] = True
    elif mutation == 'company':
        record['company']['exact_quote'] = 'Other company'
    elif mutation == 'role':
        record['company']['role'] = 'manufacturer'
    elif mutation == 'company_page':
        record['company']['page'] = 0
    elif mutation == 'variant':
        record['product_variant'] = 'Alpha 50 mg tablets'
    elif mutation == 'fact_variant':
        record['facts'][1]['product_variant'] = 'Alpha 50 mg tablets'
    else:
        record['path'] = '../outside.pdf'
    with pytest.raises(ValueError):
        ra_public.read_source(record, root)


def test_scores_reject_truncation_wrong_number_and_unknown_citation():
    facts = [{'field_key': '용법 용량', 'value': 'Initial dose 8 mg/kg. Maintenance dose 6 mg/kg.'}]
    good = ra_public.compare_values({'용법 용량': ra_public.cited(facts[0]['value'], 'S1')}, facts, {'S1'})
    assert good['full_value_preserved_count'] == good['cited_field_count'] == good['numeric_exact_count'] == 1
    changed = ra_public.compare_values({'용법 용량': 'Initial dose 6 mg/kg. [UNKNOWN]'}, facts, {'S1'})
    assert changed['full_value_preserved_count'] == changed['cited_field_count'] == changed['numeric_exact_count'] == 0


def test_live_missing_configuration_is_failure_not_mock_success(monkeypatch):
    from llm import client
    def unavailable():
        raise client.ConfigurationError('테스트 API 설정 없음')
    monkeypatch.setattr(client, 'LLMClient', unavailable)
    monkeypatch.setattr(ra_public, 'evaluate_case', lambda *args, **kwargs: pytest.fail('Fallback must not run'))
    manifest = json.loads(ra_public.DEFAULT_MANIFEST.read_text(encoding='utf-8'))
    report = ra_public.evaluate(manifest, mode='live')
    assert report['case_count'] == 6 and report['passed_count'] == 0
    assert not report['model_evaluated'] and report['model_response_count'] == 0
    assert all('테스트 API 설정 없음' in row['error'] for row in report['results'])


@pytest.mark.parametrize('record', json.loads(ra_public.DEFAULT_MANIFEST.read_text(encoding='utf-8'))['sources'],
                         ids=lambda record: record['id'])
def test_actual_downloads_reopen_sha_and_every_selected_quote(record):
    if not (ra_public.ROOT / record['path']).is_file():
        pytest.skip('공식 원본 스냅샷 미확보: 실제 원자료 검증 미실행')
    facts, sources, document = ra_public.read_source(record)
    assert len(facts) == len(sources) == len(record['facts'])
    assert {source['page'] for source in sources} == {fact['page'] for fact in record['facts']}
    assert document['jurisdiction'] == 'EU'


def test_cli_baseline_rejects_different_mode_source_or_profile(tmp_path, monkeypatch):
    def report(*args, **kwargs):
        return {'mode': kwargs['mode'], 'company_count': 3, 'case_count': 6,
                'passed_count': 6, 'model_evaluated': False, 'results': []}
    monkeypatch.setattr(ra_public, 'evaluate', report)
    output = tmp_path / 'evaluation.json'
    assert ra_public.main(['--output', str(output)]) == 0
    baseline = json.loads(output.read_text(encoding='utf-8'))
    baseline['profile_sha256'][ra_public.DEFAULT_PROFILES[0]] = 'changed'
    previous = tmp_path / 'baseline.json'
    previous.write_text(json.dumps(baseline), encoding='utf-8')
    with pytest.raises(ValueError, match='프로파일'):
        ra_public.main(['--output', str(output), '--baseline', str(previous)])
    assert output.read_text(encoding='utf-8') != previous.read_text(encoding='utf-8')
