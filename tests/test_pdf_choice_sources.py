"""Offline regression against separately pinned official PDF snapshots."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / 'evals/pdf_choice_sources.json').read_text(encoding='utf-8'))


def local_source(entry):
    path = ROOT / entry['path']
    if not path.exists():
        pytest.skip('Public original is Git-excluded; no test network download')
    return path


def test_corpus_denominators_do_not_mix_observation_with_fill():
    docs = MANIFEST['documents']
    assert len(docs) == 3
    assert len({d['issuer'] for d in docs}) == 3
    assert MANIFEST['scope']['original_page_count'] == sum(d['page_count'] for d in docs) == 21
    assert MANIFEST['scope']['canonical_entry_count'] == sum(d['canonical_entry_count'] for d in docs) == 281
    assert MANIFEST['scope']['typed_field_count'] == sum(d['typed_field_count'] for d in docs) == 279
    assert MANIFEST['scope']['choice_count'] == sum(d['choice_count'] for d in docs) == 20
    assert MANIFEST['scope']['editable_choice_count'] == 0
    assert MANIFEST['scope']['multiselect_choice_count'] == 0
    assert MANIFEST['scope']['export_label_pair_count'] == 1
    assert MANIFEST['model_api_calls'] == 0
    assert MANIFEST['model_evaluated'] is False
    assert MANIFEST['human_kpi_observations'] == 0
    assert all(d['full_submission_ready'] is False and d['legal_compliance_certified'] is False for d in docs)


@pytest.mark.parametrize('entry', MANIFEST['documents'], ids=lambda entry: entry['id'])
def test_raw_canonical_observation_matches_official_original(entry):
    path = local_source(entry)
    before = path.read_bytes()
    assert hashlib.sha256(before).hexdigest() == entry['sha256']
    assert len(before) == entry['size_bytes']
    reader = PdfReader(path)
    assert not reader.is_encrypted
    assert len(reader.pages) == entry['page_count']
    fields = reader.get_fields() or {}
    assert len(fields) == entry['canonical_entry_count']
    recorded = {f['name']: f for f in entry['canonical_fields']}
    assert set(fields) == set(recorded)
    choices = {k: f for k, f in fields.items() if f.get('/FT') == '/Ch'}
    assert len(choices) == entry['choice_count']
    for name, field in choices.items():
        evidence = recorded[name]
        assert list(field['/Opt']) == evidence['Opt']
        assert field.get('/V') == evidence['V']
        assert field.get('/DV') == evidence['DV']
        assert field.get('/Ff', 0) == evidence['flags']
        assert evidence['widgets']
        for widget in evidence['widgets']:
            page = reader.pages[widget['page'] - 1]
            actual = page['/Annots'][widget['annotation_order'] - 1].get_object()
            assert list(actual['/Rect']) == widget['rect']
            assert actual.get('/Subtype') == '/Widget'
            raw_indices = actual['/I'] if '/I' in actual else None
            observed_indices = [item.get_object() if hasattr(item, 'get_object') else item
                                for item in raw_indices] if raw_indices is not None else None
            assert observed_indices == widget['effective_I']
            assert (actual['/V'] if '/V' in actual else None) == widget['effective_V']
        assert evidence['I'] == [0]
    assert path.read_bytes() == before


def test_real_export_label_pair_is_not_an_assumed_blank_selection():
    entry = next(d for d in MANIFEST['documents'] if d['id'] == 'nmbi_grade_iv_application')
    field = next(f for f in entry['canonical_fields'] if f['field_type'] == '/Ch')
    assert field['Opt'] == ['Yes', ['Select option', 'No']]
    assert field['V'] == 'Yes'
    assert entry['synthetic_user_choices'] == {'Dropdown1': 'Select option'}


def test_certification_guard_is_recorded_and_not_bypassed():
    entry = next(d for d in MANIFEST['documents'] if d['id'] == 'gsa_sf182_2020')
    assert entry['permissions']['engine_certification_guard'] is True
    assert entry['permissions']['Perms_keys']
    assert entry['synthetic_user_choices'] == {}
    assert entry['fill_status'] == 'protected_deferred'
    from templates.compatibility import analyze_template
    profile = analyze_template(local_source(entry))
    assert profile['fields'] == []


def test_no_choice_or_access_failure_candidates_are_excluded():
    excluded = {entry['id']: entry for entry in MANIFEST['excluded_candidates']}
    assert excluded['grants_sf424_individual_v1']['choice_count'] == 0
    assert excluded['grants_sf424_individual_v1']['status'] == 'excluded_no_choice_controls'
    assert excluded['fda_3500']['status'] == 'download_access_deferred'
    assert excluded['fda_3500']['http_status'] == 302
    assert excluded['uscis_i9']['http_status'] == 403


@pytest.mark.parametrize('document_id', ['thermofisher_custom_serum', 'nmbi_grade_iv_application'])
def test_real_choice_partial_fill_independent_check_and_source_immutability(document_id, tmp_path):
    from agent.output_check import verify_output
    from templates.compatibility import analyze_template, fill_compatible_template

    entry = next(d for d in MANIFEST['documents'] if d['id'] == document_id)
    source = local_source(entry)
    before = source.read_bytes()
    profile = analyze_template(source)
    values = entry['synthetic_user_choices']
    selected = {field['value_key']: field for field in profile['fields']}
    for name, value in values.items():
        assert value in selected[name]['options']
        assert selected[name]['input_required'] is True
    output = tmp_path / f'{document_id}.pdf'
    fill_compatible_template(source, values, output, profile=profile)
    result = verify_output(source, output, values, profile=profile)
    assert result['status'] == 'passed', result
    fields = PdfReader(output).get_fields()
    for name, value in values.items():
        assert fields[name]['/V'] == value
    if document_id == 'nmbi_grade_iv_application':
        assert selected['Dropdown1']['choice_items'] == [
            {'value': 'Yes', 'label': 'Yes'}, {'value': 'Select option', 'label': 'No'}]
    assert source.read_bytes() == before
