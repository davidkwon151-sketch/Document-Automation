from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import agent.template_learning as learning
from app.form_config import configure_profile, configure_value_rules, mapping_rows, validation_rows
from app.ra_mvp_service import resolve_mvp_form
from templates.value_rules import mapped_rule_profile


def profile():
    _, p, _ = resolve_mvp_form('ra_law_form_32_pdf')
    p['configured'] = True
    return p


def field(p, key):
    return next(f for f in p['fields'] if f['value_key'] == key)


@pytest.mark.parametrize('mutation', ['unit', 'role', 'required', 'required_missing', 'direct', 'unknown_id', 'duplicate', 'source', 'format', 'empty'])
def test_obsolete_contract_rejected(mutation):
    authority = profile()
    candidate = deepcopy(authority)
    if mutation == 'unit':
        field(candidate, '누적 투약 임상시험 대상자 수')['validation'].pop('evidence_unit')
    elif mutation == 'role':
        candidate['fields'][0].pop('evidence_role')
    elif mutation in ('required', 'required_missing'):
        authority['fields'][0]['required'] = True
        if mutation == 'required_missing':
            candidate['fields'].pop(0)
    elif mutation == 'direct':
        f = next(f for f in candidate['fields'] if f['input_required'])
        f.update(input_required=False, input_mode='source_grounded')
    elif mutation == 'unknown_id':
        candidate['fields'][0]['id'] += '_obsolete'
    elif mutation == 'duplicate':
        candidate['fields'].append(deepcopy(candidate['fields'][0]))
    elif mutation == 'source':
        candidate['source_sha256'] = '0'*64
    elif mutation == 'format':
        candidate['format'] = 'docx'
    else:
        candidate['fields'] = []
    before = deepcopy((authority, candidate))
    assert not learning.reusable_profile(candidate, authority)
    assert before == (authority, candidate)


@pytest.mark.parametrize('key', ['domain', 'ra_workflow', 'ra_product_name', 'ra_product_variant'])
def test_registered_specialized_review_scope_cannot_disappear(key):
    authority = profile()
    authority[key] = {'domain':'pharmaceutical_ra','ra_workflow':'safety_management',
                       'ra_product_name':'합성약A','ra_product_variant':'정제'}[key]
    candidate = deepcopy(authority)
    candidate.pop(key)
    assert not learning.reusable_profile(candidate, authority)


def test_normal_renaming_and_rule_editor_roundtrip_preserves_current_contract(tmp_path):
    authority = profile()
    rows = mapping_rows(authority)
    for row in rows:
        row['채울 값'] += ' 변경'
    candidate, mapping = configure_profile(authority, rows)
    candidate = configure_value_rules(candidate, validation_rows(candidate))
    assert learning.reusable_profile(candidate, authority)
    path, _, _ = resolve_mvp_form('ra_law_form_32_pdf')
    learning.save_learned_profile(path, candidate, mapping, cache_dir=tmp_path, user_confirmed=True)
    assert learning.reusable_profile(learning.load_learned_profile(path, tmp_path), authority)


def relation_profile():
    p = profile()
    base = field(p, '누적 투약 임상시험 대상자 수')
    p['fields'] = [dict(deepcopy(base), id=k, value_key=k, label=k) for k in ('전체', '성인', '소아')]
    p['constraints'] = {'relations': [{'kind': 'sum', 'total': '전체', 'parts': ['성인', '소아']}],
                        'groups': [{'kind': 'all_or_none', 'fields': ['성인', '소아']}]}
    return p


def test_relation_and_group_renaming_remain_usable():
    authority = relation_profile()
    candidate = mapped_rule_profile(authority, {f['id']: f['value_key']+' 새값' for f in authority['fields']})
    assert learning.reusable_profile(candidate, authority)
    for name in ('relations', 'groups'):
        bad = deepcopy(candidate)
        bad['constraints'].pop(name)
        assert not learning.reusable_profile(bad, authority)


@pytest.mark.parametrize('kind', ['pdf_form', 'xlsx_blank', 'docx_sdt'])
@pytest.mark.parametrize('mutation', ['options', 'control_type', 'allow_custom', 'choice_items'])
def test_native_closed_choices_do_not_change_on_reuse(kind, mutation):
    authority = profile()
    base = deepcopy(authority['fields'][0])
    base.pop('evidence_role')
    base.update(kind=kind, control_type='choice', options=['a','b'],
                choice_items=[{'value':'a','display':'찬성'}, {'value':'b','display':'반대'}],
                allow_custom=False, input_required=True, input_mode='user_provided')
    authority['fields'] = [base]
    candidate = deepcopy(authority)
    candidate['fields'][0][mutation] = {'options':['a','b','c'], 'control_type':'text',
                                        'allow_custom':True, 'choice_items':[{'value':'a','display':'동의'}]}[mutation]
    assert not learning.reusable_profile(candidate, authority)


def test_repeat_preparation_remains_usable_and_binds_seed_rules():
    authority = profile()
    authority['repeat_expansion'] = {'original_sha256':'a'*64, 'prepared_sha256':authority['source_sha256'],
                                     'plan': {'table':'row','count':2}}
    authority['repeat_source_profile'] = profile()
    candidate = deepcopy(authority)
    assert learning.reusable_profile(candidate, authority)
    candidate['repeat_source_profile']['fields'][0].pop('evidence_role')
    assert not learning.reusable_profile(candidate, authority)


@pytest.mark.parametrize('route', ['cached','proposed','restored'])
@pytest.mark.parametrize('stale', [True,False])
def test_candidate_actual_ui_checks_each_override_route(route, stale, monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    import agent.template_learning as actual
    authority = profile()
    candidate = deepcopy(authority)
    if stale:
        candidate['fields'][0].pop('evidence_role')
        field(candidate, '누적 투약 임상시험 대상자 수')['validation'].pop('evidence_unit')
    monkeypatch.setattr(actual, 'reusable_profile', learning.reusable_profile, raising=False)
    monkeypatch.setattr(actual, 'load_learned_profile', lambda *a, **k: candidate if route=='cached' else None)
    path, _, _ = resolve_mvp_form('ra_law_form_32_pdf')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    key = f'learned_profile_{digest}' if route=='proposed' else 'resume_template_profile'
    setup = f'st.session_state[{key!r}] = json.loads({json.dumps(candidate, ensure_ascii=False)!r})' if route!='cached' else ''
    candidate_ui = ROOT / 'app' / 'ui.py'
    code = f'''import importlib.util,json,streamlit as st
from pathlib import Path
spec=importlib.util.spec_from_file_location('candidate_ui',{str(candidate_ui)!r})
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
{setup}
p,*rest=mod.form_options(Path({str(path)!r}),Path({str(tmp_path)!r}))
st.session_state['checked_profile']=p
'''
    ui = AppTest.from_string(code, default_timeout=30).run()
    assert not ui.exception
    result = ui.session_state['checked_profile']
    assert result['fields'][0].get('evidence_role') == 'product_name'
    assert field(result,'누적 투약 임상시험 대상자 수')['validation']['evidence_unit'] == '명'
