"""Declared demo aliases copy complete facts without asserting clinical status."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil

from pypdf import PdfReader
import pytest

from agent.pipeline import build_downloads, review_result
from agent.review import CITATION_PATTERN
from app import ra_mvp_service as service
from evals.ra_public import cited, read_source
from llm.client import LLMClient

ROOT = service.ROOT
NEW_FORMS = ('ra_law_form_23_pdf', 'ra_law_form_32_pdf')


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Source-copy tests must not call a model')

    for name in ('generate_json', 'read_image_json', 'embed'):
        monkeypatch.setattr(LLMClient, name, forbidden)


@pytest.fixture(params=NEW_FORMS)
def bound_catalog(request, tmp_path):
    entry = next(form for form in service.load_mvp_forms() if form['id'] == request.param)
    source_manifest = json.loads((ROOT / entry['demo_manifest']).read_text(encoding='utf-8'))
    source = next(record for record in source_manifest['sources'] if record['id'] == entry['demo_source_id'])
    for relative in (entry['source_path'], entry['profile_path'], entry['demo_manifest'], source['path']):
        original, target = ROOT / relative, tmp_path / relative
        if not original.is_file():
            pytest.skip('Locally acquired official public sources are absent')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    write_catalog(tmp_path, entry)
    return tmp_path, entry, source


def write_catalog(root, record):
    path = root / 'templates/ra_mvp_catalog.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'schema_version': 1, 'forms': [record]}, ensure_ascii=False), encoding='utf-8')


def write_manifest(root, entry, manifest):
    (root / entry['demo_manifest']).write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')


def test_alias_preserves_source_fact_and_complete_value_without_inventing_other_fields(bound_catalog, tmp_path):
    root, entry, record = bound_catalog
    files = [root / entry['source_path'], root / entry['profile_path'], root / entry['demo_manifest'], root / record['path']]
    before = {path: sha256(path.read_bytes()).hexdigest() for path in files}
    facts, sources, document = read_source(record, root)
    selected = next(fact for fact in facts if fact['field_key'] == '제품명')
    key = entry['demo_field_keys'][0]
    result = service.generate_mvp('제품명 원문만 검토용으로 기입', entry['id'], root=root, demo=True)
    assert result['status'] == 'ready', result['review']
    assert result['template_profile']['document_kind'] == result['document_kind'] == entry['document_kind']
    assert result['ra_workflow'] == entry['ra_workflow']
    assert result['draft'][key] == cited(selected['value'], selected['source_id'])
    assert selected['value'] == '한미플루 75mg'
    assert result['sources'] == sources and result['documents'] == [document]
    assert result['demo_source'] == record
    assert all(fact['field_key'] != key for fact in result['demo_source']['facts'])
    remaining = set(entry['source_based_keys'] + entry['user_input_keys']) - {key}
    assert all(result['draft'][field] == '' for field in remaining)
    assert not result['locked_fields'] and not result['submission_ready']
    assert result['actual_model_requests'] == result['actual_model_responses'] == 0
    checked = review_result(result)
    assert not checked['blocking'], checked
    template = root / entry['source_path']
    payload = build_downloads(result, confirmed=True, template_paths={'pdf': template},
                              template_profiles={'pdf': result['template_profile']}, native_review='off')
    output = tmp_path / 'selected-public-name.pdf'
    output.write_bytes(payload['pdf'])
    assert result['output_verification']['pdf']['status'] == 'passed'
    assert len(PdfReader(output).pages) == entry['source_page_count'] == 2
    assert before == {path: sha256(path.read_bytes()).hexdigest() for path in files}


def test_report_styles_only_general_narrative_and_keeps_native_name_raw():
    result = service.generate_mvp('선정 원문 검토', 'ra_law_form_32_pdf', demo=True)
    assert result['document_kind'] == 'report'
    key = result['form_record']['demo_field_keys'][0]
    assert CITATION_PATTERN.sub('', result['draft'][key]).strip() == '한미플루 75mg'
    field = next(field for field in result['template_profile']['fields'] if field['value_key'] == key)
    assert field['narrative_style_required'] is False
    for core in ('요약', '본문'):
        assert CITATION_PATTERN.sub('', result['draft'][core]).strip() == '○ 한미플루 75mg임'
    assert not review_result(result)['blocking']


@pytest.mark.parametrize('kind', [None, '', ' report', 'unsupported', True, [], {}])
def test_unregistered_or_nonstring_document_kind_is_rejected(bound_catalog, kind):
    root, entry, _ = bound_catalog
    entry['document_kind'] = kind
    write_catalog(root, entry)
    with pytest.raises(ValueError, match='문서 종류'):
        service.load_mvp_forms(root)


@pytest.mark.parametrize('bad_map', [None, [], '', {'임의 항목': '제품명'},
                                   {'신청인 성명': '제품명'},
                                   {'시험약 제품명': 75}, {'시험약 제품명': True},
                                   {'시험약 제품명': ''}, {'시험약 제품명': ' 제품명'},
                                   {'시험약 제품명': {'field': '제품명', 'replace': {'75': '750'}}}])
def test_alias_schema_does_not_promote_direct_or_arbitrary_slots(bound_catalog, bad_map):
    root, entry, _ = bound_catalog
    # Keep the invalid types/unknown targets; adapt valid target spelling so the
    # number-transform and source-key cases exercise both registered forms.
    if isinstance(bad_map, dict) and '시험약 제품명' in bad_map:
        bad_map = {entry['demo_field_keys'][0]: bad_map['시험약 제품명']}
    entry['demo_field_map'] = bad_map
    write_catalog(root, entry)
    with pytest.raises(ValueError, match='데모 항목 매핑'):
        service.load_mvp_forms(root)


@pytest.mark.parametrize('source_key', ['없는 원문 항목', 'S123', '제품명 750mg'])
def test_unknown_citation_id_or_transformed_source_key_is_not_an_alias(bound_catalog, source_key):
    root, entry, _ = bound_catalog
    entry['demo_field_map'] = {entry['demo_field_keys'][0]: source_key}
    write_catalog(root, entry)
    with pytest.raises(ValueError, match='공식 원문'):
        service.generate_mvp('검토', entry['id'], root=root, demo=True)


def test_duplicate_resolved_source_key_is_not_written_to_two_slots(bound_catalog):
    root, entry, _ = bound_catalog
    other = next(key for key in entry['source_based_keys'] if key != entry['demo_field_keys'][0])
    entry['demo_field_keys'].append(other)
    entry['demo_field_map'][other] = '제품명'
    write_catalog(root, entry)
    with pytest.raises(ValueError, match='중복 매핑'):
        service.load_mvp_forms(root)


@pytest.mark.parametrize('mutation', ['duplicate_source_id', 'unknown_source_id', 'duplicate_fact_key', 'changed_number'])
def test_source_id_fact_key_or_number_cannot_be_fabricated(bound_catalog, mutation):
    root, entry, _ = bound_catalog
    manifest = json.loads((root / entry['demo_manifest']).read_text(encoding='utf-8'))
    source = next(record for record in manifest['sources'] if record['id'] == entry['demo_source_id'])
    if mutation == 'duplicate_source_id':
        manifest['sources'].append(deepcopy(source))
    elif mutation == 'unknown_source_id':
        entry['demo_source_id'] = 'S123'
    elif mutation == 'duplicate_fact_key':
        fact = deepcopy(next(fact for fact in source['facts'] if fact['field_key'] == '제품명'))
        fact['role'] = 'duplicate_product_label'
        source['facts'].append(fact)
    else:
        fact = next(fact for fact in source['facts'] if fact['field_key'] == '제품명')
        fact['value'] = fact['value'].replace('75mg', '750mg')
    write_catalog(root, entry)
    write_manifest(root, entry, manifest)
    with pytest.raises(ValueError, match='공식 원자료 ID|유일하게|제형'):
        service.generate_mvp('임의 값을 근거로 사용', entry['id'], root=root, demo=True)


def test_undeclared_alias_does_not_guess_product_name_slot(bound_catalog):
    root, entry, _ = bound_catalog
    entry.pop('demo_field_map')
    write_catalog(root, entry)
    with pytest.raises(ValueError, match='공식 원문'):
        service.generate_mvp('이름이 비슷하면 추정해서 작성', entry['id'], root=root, demo=True)


def test_existing_identity_mapping_preserves_the_original_application_demo():
    result = service.generate_mvp('기존 공개 원문', 'ra_law_form_8_pdf', demo=True)
    assert 'demo_field_map' not in result['form_record']
    assert result['draft']['제품명'].startswith('한미플루 75mg [S')
    assert result['draft']['요약'] == result['draft']['본문'] == result['draft']['제품명']
    assert not review_result(result)['blocking']


@pytest.mark.parametrize('payload', [{'field_values': True}, {'answers': True}])
def test_alias_target_cannot_be_supplied_as_user_grounded_evidence(bound_catalog, payload):
    root, entry, _ = bound_catalog
    target = entry['demo_field_keys'][0]
    argument = next(iter(payload))
    with pytest.raises(ValueError, match='직접 입력|새 사실 근거'):
        service.generate_mvp('지시의 수치를 근거로 승격하지 않음', entry['id'], root=root, demo=True,
                             **{argument: {target: '다른 약 750mg'}})


def test_catalog_stays_bounded_to_eight_configurations(tmp_path):
    records = service.load_mvp_forms()
    assert len(records) == 8
    write_catalog(tmp_path, records[0])
    path = tmp_path / 'templates/ra_mvp_catalog.json'
    path.write_text(json.dumps({'forms': records + [deepcopy(records[0])]}, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ValueError, match='1~8종'):
        service.load_mvp_forms(tmp_path)
