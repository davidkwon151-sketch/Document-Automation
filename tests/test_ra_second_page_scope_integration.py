"""Exercise current writer and cache/UI roundtrips with all private guards active."""
from copy import deepcopy
from pathlib import Path
import importlib.util

import pytest
from agent.brief import model_profile
from agent.completeness import completeness_fingerprint
import agent.grounding as grounding
from agent.template_learning import save_learned_profile, load_learned_profile
from app.form_config import configure_profile, mapping_rows, configure_value_rules, validation_rows
from templates.value_rules import mapped_rule_profile
from app import ra_mvp_service as service
from agent.template_learning import reusable_profile
test_spec = importlib.util.spec_from_file_location('ra_v2_support', Path(__file__).with_name('test_ra_second_page_v2.py'))
support = importlib.util.module_from_spec(test_spec)
test_spec.loader.exec_module(support)
V2, generate, export, evidence = support.V2, support.generate, support.export, support.evidence
from agent.pipeline import review_result

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('form_id', V2)
def test_actual_v2_profile_survives_model_mapping_rule_editor_and_hmac_cache(form_id, tmp_path):
    template, profile, _ = service.resolve_mvp_form(form_id)
    expected = {f['id']: (f['label'], f['evidence_scope'], f['evidence_document_labels'])
                for f in profile['fields'] if 'evidence_document_labels' in f}
    sanitized = model_profile(profile)
    configured, mapping = configure_profile(sanitized, mapping_rows(sanitized))
    configured = configure_value_rules(configured, validation_rows(configured))
    rebound = mapped_rule_profile(configured, mapping)
    path = save_learned_profile(template, rebound, cache_dir=tmp_path, user_confirmed=True)
    loaded = load_learned_profile(template, tmp_path)
    assert path.is_file() and reusable_profile(loaded, profile)
    actual = {f['id']: (f['label'], f['evidence_scope'], f['evidence_document_labels'])
              for f in loaded['fields'] if 'evidence_document_labels' in f}
    assert actual == expected

@pytest.mark.parametrize('form_id', V2)
def test_different_actual_scope_sections_cannot_merge_to_one_value(form_id):
    _, profile, _ = service.resolve_mvp_form(form_id)
    mapping = {f['id']: f['value_key'] for f in profile['fields']}
    mapping[profile['fields'][-2]['id']] = '하나로 합친 잘못된 항목'
    mapping[profile['fields'][-1]['id']] = '하나로 합친 잘못된 항목'
    with pytest.raises(ValueError, match='같은 값 이름'):
        mapped_rule_profile(profile, mapping)

@pytest.mark.parametrize('form_id', V2)
@pytest.mark.parametrize('attribute', ['evidence_scope', 'evidence_document_labels', 'label'])
def test_scope_removal_or_move_invalidates_authority_semantics_and_export(form_id, attribute, evidence):
    client, result = generate(form_id, evidence)
    before = deepcopy(result['template_profile'])
    changed = deepcopy(before)
    changed['fields'][-2].pop(attribute)
    assert not reusable_profile(changed, before)
    assert grounding.evidence_fingerprint(result['draft'], result['sources'], template_profile=changed) != grounding.evidence_fingerprint(result['draft'], result['sources'], template_profile=before)
    assert completeness_fingerprint(result['draft'], result['brief'], changed) != completeness_fingerprint(result['draft'], result['brief'], before)
    with pytest.raises(ValueError):
        from agent.pipeline import build_downloads
        build_downloads(result, confirmed=True, template_paths=result['template_paths'],
                        template_profiles={'pdf': changed}, native_review='off')
    if attribute != 'label':
        result['template_profile'] = changed
        if attribute == 'evidence_scope':
            with pytest.raises(ValueError, match='범위'):
                review_result(result)
        else:
            checked = review_result(result)
            assert checked['blocking']
        with pytest.raises(ValueError):
            export(result)

actual_api_is_forbidden = support.actual_api_is_forbidden
