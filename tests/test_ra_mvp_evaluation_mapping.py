"""Evaluation distinguishes original evidence fields from submitted slot names."""
from hashlib import sha256
import json
from pathlib import Path

import pytest

from evals.ra_mvp import evaluate
from evals.ra_public import read_source
from llm.client import LLMClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('form_id,slot,kind,workflow', [
    ('ra_law_form_23_pdf', '시험약 제품명', 'application', 'clinical_trial'),
    ('ra_law_form_32_pdf', '제품명 성분명', 'report', 'safety_management'),
])
def test_demo_evaluation_records_original_fact_mapping_and_kind(form_id, slot, kind, workflow, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Quote-copy evaluation must not call a model')

    monkeypatch.setattr(LLMClient, 'generate_json', forbidden)
    monkeypatch.setattr(LLMClient, 'read_image_json', forbidden)
    report = evaluate(mode='demo', form_ids=[form_id], artifact_dir=tmp_path)
    assert report['passed_count'] == report['case_count'] == 1
    assert report['actual_api_requests'] == report['model_response_cases'] == 0
    assert report['human_kpi_observations'] == 0
    row = report['results'][0]
    assert row['document_kind'] == kind and row['ra_workflow'] == workflow
    assert row['selected_keys'] == [slot]
    assert row['selected_source_field_map'] == {slot: '제품명'}
    evidence = json.loads(Path(row['provenance']).read_text(encoding='utf-8'))
    assert evidence['form']['demo_field_map'] == {slot: '제품명'}
    manifest = json.loads((ROOT / evidence['form']['demo_manifest']).read_text(encoding='utf-8'))
    source_record = next(item for item in manifest['sources'] if item['id'] == row['source_id'])
    original_facts, _, _ = read_source(source_record, ROOT)
    fact = next(item for item in original_facts if item['field_key'] == '제품명')
    assert evidence['draft'][slot] == fact['exact_quote'] + ' [' + fact['source_id'] + ']'
    linked = next(source for source in evidence['sources'] if source['source_id'] == fact['source_id'])
    assert linked['page'] == fact['page']
    assert fact['exact_quote'] in linked['text']
    assert linked['filename'] == Path(source_record['path']).name
    unfilled = set(evidence['form']['source_based_keys'] + evidence['form']['user_input_keys']) - {slot}
    assert all(evidence['draft'][key] == '' for key in unfilled)
    assert row['output_verification']['status'] == 'passed'
    assert row['output_sha256'] == sha256(Path(row['output']).read_bytes()).hexdigest()
    assert row['provenance_sha256'] == sha256(Path(row['provenance']).read_bytes()).hexdigest()
    assert not evidence['submission_ready']
