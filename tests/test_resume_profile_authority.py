from copy import deepcopy
from pathlib import Path
import sys

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.pipeline import review_result
from agent.metrics import start_metrics
from app.ra_mvp_service import generate_mvp
from templates.value_rules import mapped_rule_profile

HERE = ROOT / 'app'
from app import ui as module


@pytest.fixture(scope='module')
def ready():
    r=generate_mvp('공개 원문을 대조함','ra_law_form_32_pdf',demo=True)
    r['boss_review']={'questions':[],'additional_checks':[]}
    return r


def run_resume(result, monkeypatch, tmp_path, *, suffix=''):
    monkeypatch.setenv('REPORT_AGENT_DATA_DIR',str(tmp_path/'owned-data'))
    source=tmp_path/'owned-data'/'runs'/result['run_id']/'inputs'/'source.txt'
    source.parent.mkdir(parents=True,exist_ok=True)
    source.write_text('누적 업무 원자료',encoding='utf8')
    text=(HERE/'ui.py').read_text(encoding='utf8')
    text=text.replace('ROOT = Path(__file__).resolve().parents[1]',f'ROOT = Path({str(ROOT)!r})',1)
    if suffix:
        marker='if __name__ == "__main__":'
        text=text.replace(marker,suffix+'\n'+marker,1)
    ui=AppTest.from_string(text,default_timeout=30)
    active={'run_id':result['run_id'],'metrics':deepcopy(result['metrics'])}
    for key,value in {'result':deepcopy(result),'resume_template_profile':deepcopy(result['template_profile']),
                      'public_template_path':result['template_paths']['pdf'],'request_fingerprint':None,
                      'instruction':result['instruction'],'work_domain':'pharmaceutical_ra',
                      'ra_workflow':'safety_management','document_kind':'report','answers':{'기존 질문':'누적 답변'},
                      'active_run':active,'resume_input_paths':[str(source)],
                      'exports':{'pdf':b'previous bytes'},'confirmed':True}.items():
        ui.session_state[key]=value
    ui.run()
    assert not ui.exception
    return ui,deepcopy(active),str(source)


def absent(ui,key):
    try:
        ui.session_state[key]
    except KeyError:
        return True
    return False


def test_actual_wrong_product_resume_counterexample_is_blocked(ready,monkeypatch,tmp_path):
    r=deepcopy(ready)
    for f in r['template_profile']['fields']:
        f.pop('evidence_role',None)
        f.get('validation',{}).pop('evidence_unit',None)
    sid=r['draft']['제품명 성분명'].rsplit('[',1)[1].split(']')[0]
    r['draft']['제품명 성분명']=f'합성약A [{sid}]'
    assert not review_result(r)['blocking']  # The original confirmed legacy counterexample.
    current=deepcopy(r)
    current['template_profile']=ready['template_profile']
    assert review_result(current)['blocking']
    ui,baseline,source=run_resume(r,monkeypatch,tmp_path)
    assert absent(ui,'result') and absent(ui,'exports')
    assert not ui.session_state['confirmed']
    assert not any(b.key=='prepare' for b in ui.button)
    assert ui.session_state['active_run']==baseline
    assert ui.session_state['answers']=={'기존 질문':'누적 답변'}
    assert ui.session_state['resume_input_paths']==[source]
    assert any('초안을 다시 작성' in w.value for w in ui.warning)


@pytest.mark.parametrize('change',['role','unit','required','direct','combined','unknown_id'])
def test_current_registered_policy_invalidates_restored_result(ready,monkeypatch,tmp_path,change):
    import templates.profiles as profiles
    original=profiles.load_form_profile
    r=deepcopy(ready)
    if change in ('role','combined'):
        r['template_profile']['fields'][0].pop('evidence_role')
    if change in ('unit','combined'):
        count=next(f for f in r['template_profile']['fields'] if 'evidence_unit' in f.get('validation',{}))
        count['validation'].pop('evidence_unit')
    if change in ('direct','combined'):
        personal=next(f for f in r['template_profile']['fields'] if f['input_required'])
        personal.update(input_required=False,input_mode='source_grounded')
    if change=='unknown_id':
        r['template_profile']['fields'][0]['id']+=' obsolete'
    def current(path,*a,**k):
        p=original(path,*a,**k)
        if p and change in ('required','combined'):
            p['fields'][0]['required']=True
        return p
    monkeypatch.setattr(profiles,'load_form_profile',current)
    ui,baseline,source=run_resume(r,monkeypatch,tmp_path)
    assert absent(ui,'result') and absent(ui,'exports')
    assert not ui.session_state['confirmed']
    assert ui.session_state['active_run']==baseline
    assert ui.session_state['answers']=={'기존 질문':'누적 답변'}
    assert ui.session_state['resume_input_paths']==[source]


@pytest.mark.parametrize('renamed',[False,True])
def test_normal_resume_keeps_initial_none_fingerprint_and_kpi(ready,monkeypatch,tmp_path,renamed):
    r=deepcopy(ready)
    if renamed:
        mapping={f['id']:f['value_key']+' 확인' for f in r['template_profile']['fields']}
        old_to_new={f['value_key']:mapping[f['id']] for f in r['template_profile']['fields']}
        r['template_profile']=mapped_rule_profile(r['template_profile'],mapping)
        r['draft']={old_to_new.get(k,k):v for k,v in r['draft'].items()}
        r['metrics']=start_metrics(r['draft'])
        r['metrics']['run_id']=r['run_id']
    ui,baseline,source=run_resume(r,monkeypatch,tmp_path)
    assert not absent(ui,'result')
    assert ui.session_state['result']['draft']==r['draft']
    assert ui.session_state['active_run']==baseline
    assert ui.session_state['result']['metrics']==r['metrics']
    assert ui.session_state['answers']=={'기존 질문':'누적 답변'}
    assert ui.session_state['resume_input_paths']==[source]
    assert not any('초안을 다시 작성' in w.value for w in ui.warning)


def test_export_rechecks_selected_profile_after_initial_page_gate(ready,monkeypatch,tmp_path):
    import agent.pipeline as pipeline
    calls=[]
    monkeypatch.setattr(pipeline,'build_downloads',lambda *a,**k:calls.append(k) or {'pdf':b'bad'})
    injected='''
_base_profile_check=result_profile_is_current
_calls=0
def result_profile_is_current(*args,**kwargs):
    global _calls
    _calls+=1
    if st.session_state.get('arm_export_policy_change') and _calls==2:
        return False
    return _base_profile_check(*args,**kwargs)
'''
    ui,baseline,source=run_resume(ready,monkeypatch,tmp_path,suffix=injected)
    ui.checkbox(key='confirmed').check().run()
    assert not ui.button(key='prepare').disabled
    ui.session_state['arm_export_policy_change']=True
    ui.button(key='prepare').click().run()
    assert not ui.exception
    assert calls==[] and absent(ui,'result') and absent(ui,'exports')
    assert not ui.session_state['confirmed']
    assert ui.session_state['active_run']==baseline
    assert ui.session_state['answers']=={'기존 질문':'누적 답변'}
    assert ui.session_state['resume_input_paths']==[source]


def test_export_check_reads_latest_registered_contract(ready,monkeypatch):
    import templates.profiles as profiles
    selected=deepcopy(ready['template_profile'])
    assert module.result_profile_is_current(ready,selected,ready['template_paths']['pdf'])
    original=profiles.load_form_profile
    def changed(path,*a,**k):
        p=original(path,*a,**k)
        p['fields'][0]['required']=True
        return p
    monkeypatch.setattr(profiles,'load_form_profile',changed)
    assert not module.result_profile_is_current(ready,selected,ready['template_paths']['pdf'])


@pytest.mark.parametrize('change', ['role', 'unit', 'direct', 'missing_required', 'position', 'native_options'])
def test_export_reads_fresh_registration_changes(ready, monkeypatch, change):
    import templates.profiles as profiles
    original = profiles.load_form_profile
    def changed(path, *args, **kwargs):
        profile = original(path, *args, **kwargs)
        if change == 'role':
            profile['fields'][0]['evidence_role'] = 'manufacturer'
        elif change == 'unit':
            field = next(f for f in profile['fields'] if 'evidence_unit' in f.get('validation', {}))
            field['validation']['evidence_unit'] = '건'
        elif change == 'direct':
            profile['fields'][0].update(input_required=True, input_mode='user_provided')
        elif change == 'missing_required':
            profile['fields'].append(dict(profile['fields'][0], id='new_required', value_key='추가 필수', required=True))
        elif change == 'position':
            profile['fields'][0]['x'] += 1
        else:
            profile['fields'][0].update(control_type='choice', options=['국내', '해외'], input_required=True)
        return profile
    monkeypatch.setattr(profiles, 'load_form_profile', changed)
    assert not module.result_profile_is_current(ready, ready['template_profile'], ready['template_paths']['pdf'])


@pytest.mark.parametrize('setting', ['citation_mode', 'overflow_mode'])
def test_saved_output_policy_must_match_selected_profile(ready, setting):
    selected = deepcopy(ready['template_profile'])
    selected[setting] = 'changed'
    assert not module.result_profile_is_current(ready, selected)


def test_native_profile_without_mapping_annotations_is_valid():
    profile = {'source_sha256': 'example', 'format': 'docx', 'fields': [
        {'id': 'title', 'value_key': '제목', 'label': '제목', 'kind': 'placeholder', 'required': True}]}
    assert module.result_profile_is_current({'template_profile': profile}, deepcopy(profile))
    profile['fields'][0]['max_chars'] = 15000
    assert module.result_profile_is_current({'template_profile': profile}, deepcopy(profile))


def test_regeneration_keeps_original_baseline_after_invalidation(ready,monkeypatch,tmp_path):
    import agent.pipeline as pipeline
    r=deepcopy(ready)
    r['template_profile']['fields'][0].pop('evidence_role')
    ui,baseline,source=run_resume(r,monkeypatch,tmp_path)
    calls=[]
    def generate(*a,**kw):
        calls.append((a,kw))
        new=deepcopy(ready)
        new['metrics']=deepcopy(kw['previous_metrics'])
        return new
    monkeypatch.setattr(pipeline,'run_pipeline',generate)
    ui.button(key='generate').click().run()
    assert not ui.exception
    assert calls and calls[0][1]['previous_metrics']==baseline['metrics']
    assert calls[0][1]['answers']=={'기존 질문':'누적 답변'}
    assert str(calls[0][0][1][0])==source
    assert not absent(ui,'result'), [(kind,[w.value for w in getattr(ui,kind)]) for kind in ('warning','error','caption')]
    assert ui.session_state['result']['metrics']['baseline_draft']==baseline['metrics']['baseline_draft']
    after=ui.session_state['active_run']['metrics']
    for key in ('baseline_draft','draft_started_at','started_at','first_draft_at','rejection_count','rework_count'):
        if key in baseline['metrics']:
            assert after[key]==baseline['metrics'][key]
