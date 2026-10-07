"""Downloaded official EMA snapshots, separately identified from synthetic tests.

No network, model call, Korean authorisation or current legal certification here.
Quotes/versions live in the provenance manifest; PDFs are reopened and hashed.
"""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from agent.ra import inspect_ra_draft

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / 'evals/ra_public_sources.json').read_text(encoding='utf-8'))
PUBLIC_FACTS = [(source, fact) for source in MANIFEST['sources'] for fact in source['facts']]


@pytest.fixture(scope='module')
def pages():
    if any(not (ROOT / source['path']).is_file() for source in MANIFEST['sources']):
        pytest.skip('공식 원본 스냅샷 미확보: 실제 원자료 검증은 미실행이며 합성 성공으로 대체하지 않음')
    parsed = {}
    for source in MANIFEST['sources']:
        path = ROOT / source['path']
        assert sha256(path.read_bytes()).hexdigest() == source['sha256']
        reader = PdfReader(path)
        parsed[source['id']] = {fact['page']: reader.pages[fact['page'] - 1].extract_text()
                                for fact in source['facts']}
    return parsed


def evidence(source, fact, pages):
    return {'source_id': 'S1', 'text': pages[source['id']][fact['page']],
            'filename': source['filename'], 'page': fact['page'],
            'source_url': source['source_url'], 'source_sha256': source['sha256'],
            'product_name': source['product_name'], 'product_variant': source['product_variant']}


def selected(source):
    return {'ra_workflow': 'product_approval', 'ra_product_name': source['product_name'],
            'ra_product_variant': source['product_variant']}


@pytest.mark.parametrize('source,fact', PUBLIC_FACTS, ids=[source['id'] + ':' + fact['role'] for source, fact in PUBLIC_FACTS])
def test_official_whole_page_accepts_exact_selected_product_fact_without_modifying_it(source, fact, pages):
    proof = evidence(source, fact, pages)
    assert ''.join(fact['exact_quote'].split()) in ''.join(proof['text'].split())
    draft = {fact['field_key']: '\n'.join(line + ' [S1]' if line.strip() else '' for line in fact['value'].splitlines())}
    original = deepcopy(draft)
    result = inspect_ra_draft(draft, [proof], profile=selected(source))
    assert not result['blocking'], result['issues']
    assert draft == original
    assert any(check['status'] == 'not_certified' for check in result['checks'])


def source_named(name):
    return next(source for source in MANIFEST['sources'] if source['product_name'] == name)


def test_real_multi_strength_page_cannot_supply_wrong_herzuma_vial(pages):
    source = source_named('Herzuma')
    fact = next(fact for fact in source['facts'] if fact['role'] == 'product_name')
    proof = evidence(source, fact, pages)
    # The same actual page contains both variants; mere number presence is insufficient.
    assert '420 mg' in proof['text'] and '150 mg' in proof['text']
    good = {'바이알 함량': '150 mg [S1]'}
    wrong = {'바이알 함량': '420 mg [S1]'}
    assert not inspect_ra_draft(good, [proof], profile=selected(source))['blocking']
    assert inspect_ra_draft(wrong, [proof], profile=selected(source))['blocking']


def test_real_initial_and_maintenance_doses_are_not_interchangeable(pages):
    source = source_named('Herzuma')
    fact = next(fact for fact in source['facts'] if 'loading_and_maintenance' in fact['role'])
    proof = evidence(source, fact, pages)
    good = {'Recommended initial loading dose': '8 mg/kg [S1]', 'Recommended maintenance dose': '6 mg/kg [S1]'}
    assert not inspect_ra_draft(good, [proof], profile=selected(source))['blocking']
    wrong = {**good, 'Recommended initial loading dose': '6 mg/kg [S1]'}
    assert inspect_ra_draft(wrong, [proof], profile=selected(source))['blocking']


def test_real_weekly_alternative_dose_cannot_be_bound_to_primary_dose(pages):
    source = source_named('Benepali')
    fact = next(fact for fact in source['facts'] if 'twice_weekly' in fact['role'])
    proof = evidence(source, fact, pages)
    good = {'Dose': '25 mg twice weekly [S1]'}
    wrong = {'Dose': '25 mg once weekly [S1]'}
    assert not inspect_ra_draft(good, [proof], profile=selected(source))['blocking']
    assert inspect_ra_draft(wrong, [proof], profile=selected(source))['blocking']


def test_real_daily_and_weekly_sources_cannot_be_combined_into_new_regimen(pages):
    benepali = source_named('Benepali')
    keppra = source_named('Keppra')
    bfact = next(fact for fact in benepali['facts'] if 'twice_weekly' in fact['role'])
    kfact = next(fact for fact in keppra['facts'] if 'initial_dose' in fact['role'])
    bproof, kproof = evidence(benepali, bfact, pages), evidence(keppra, kfact, pages)
    kproof['source_id'] = 'S2'
    good = {'Dose': '25 mg twice weekly [S1]'}
    wrong = {'Dose': '25 mg twice daily [S1] [S2]'}
    assert not inspect_ra_draft(good, [bproof, kproof], profile=selected(benepali))['blocking']
    assert inspect_ra_draft(wrong, [bproof, kproof], profile=selected(benepali))['blocking']


def test_real_indication_does_not_support_changed_patient_population(pages):
    source = source_named('Benepali')
    fact = next(fact for fact in source['facts'] if fact['field_key'] == '효능 효과')
    proof = evidence(source, fact, pages)
    good = {'치료 대상': fact['value'].replace('\n', ' ') + ' [S1]'}
    wrong = {'치료 대상': good['치료 대상'].replace('in adults', 'in infants')}
    assert not inspect_ra_draft(good, [proof], profile=selected(source))['blocking']
    assert inspect_ra_draft(wrong, [proof], profile=selected(source))['blocking']
