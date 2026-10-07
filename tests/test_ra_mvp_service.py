"""Offline MVP orchestration; real sources and fresh output, never live APIs."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil

from docx import Document
from pypdf import PdfReader
import pytest

from app import ra_mvp_service as service
from agent.pipeline import build_downloads, review_result
from agent.review import CITATION_PATTERN
from evals import ra_mvp
from evals.run import MockEvaluationClient
from llm.client import ConfigurationError

ROOT = service.ROOT
FORM_IDS = ('ra_law_form_4_pdf', 'ra_law_form_20_pdf', 'corporate_ra_eurofins_sample_submission',
            'ra_law_form_8_pdf', 'ra_law_form_23_pdf', 'ra_law_form_32_pdf',
            'ra_law_form_23_pdf_v2_second_page', 'ra_law_form_32_pdf_v2_second_page')


class OfflineClient(MockEvaluationClient):
    def __init__(self, *, question=False, uncited=False):
        super().__init__({'mock_brief': {'목적': '제품명', '보고 대상': 'RA 담당자',
            '보고서 유형': '결과보고서', '마감': '', '분량': '1쪽',
            '부족한 정보': ['보고 대상'] if question else [],
            '질문': ['누구에게 제출할까요?'] if question else []}})
        self.calls = []
        self.uncited = uncited

    def generate_json(self, name, payload):
        self.calls.append((name, deepcopy(payload)))
        if name == 'draft':
            identifier = next(source['source_id'] for source in payload['sources']
                              if source['filename'] != '사용자 입력')
            text = '제품명: 검증약' + ('' if self.uncited else f' [{identifier}]')
            return {'제목': 'RA 검토', '요약': text, '본문': text,
                    '제품명': '검증약' + ('' if self.uncited else f' [{identifier}]')}
        if name == 'grounding':
            by_id = {source['source_id']: source['text'] for source in payload['sources']}
            return {'claims': [{'field': item['field'], 'line': item['line'], 'status': 'supported',
                'evidence': [{'source_id': identifier, 'quote': by_id[identifier]}
                             for identifier in CITATION_PATTERN.findall(item['text']) if identifier in by_id]}
                for item in payload['claims']]}
        if name == 'completeness':
            return {'issues': [], 'checked_fields': list(payload['draft'])}
        return super().generate_json(name, payload)


@pytest.fixture
def attachment(tmp_path):
    path = tmp_path / 'synthetic-evidence.docx'
    doc = Document()
    doc.add_paragraph('제품명: 검증약')
    doc.save(path)
    return path


@pytest.fixture(scope='module')
def demo_results():
    return {identifier: service.generate_mvp('공식 선정 원문 검토', identifier, demo=True)
            for identifier in FORM_IDS}


def export(result):
    return build_downloads(result, confirmed=True,
                           template_paths={'pdf': result['template_paths']['pdf']},
                           template_profiles={'pdf': result['template_profile']}, native_review='off')


def test_catalog_bounds_and_no_private_or_qa_values():
    records = service.load_mvp_forms()
    assert tuple(record['id'] for record in records) == FORM_IDS
    assert sum(record['field_count'] for record in records) == 117
    assert sum(len(record['demo_field_keys']) for record in records) == 10
    assert len({record['source_sha256'] for record in records}) == 6
    for record in records:
        path, profile, verified = service.resolve_mvp_form(record['id'])
        assert verified == record and sha256(path.read_bytes()).hexdigest() == record['source_sha256']
        assert not any(key in profile for key in ('demo_values', 'qa_values', 'field_values', 'draft'))
        assert profile['citation_mode'] == 'sidecar'
    records[0]['title'] = 'changed'
    assert service.load_mvp_forms()[0]['title'] != 'changed'


@pytest.mark.parametrize('identifier', FORM_IDS)
def test_demo_exact_quote_and_fresh_pdf_with_independent_check(identifier, demo_results, tmp_path):
    result = deepcopy(demo_results[identifier])
    record = result['form_record']
    template = Path(result['template_paths']['pdf'])
    before = sha256(template.read_bytes()).hexdigest()
    assert result['status'] == 'ready' and not review_result(result)['blocking']
    assert result['mode'] == 'rules_demo' and result['actual_model_requests'] == 0
    assert not result['submission_ready'] and '실제 AI 작성 결과가 아님' in result['notice']
    assert all(result['draft'][key] == '' for key in record['user_input_keys'])
    manifest = json.loads((ROOT / record['demo_manifest']).read_text(encoding='utf-8'))
    source = next(item for item in manifest['sources'] if item['id'] == record['demo_source_id'])
    for key in record['demo_field_keys']:
        source_key = record.get('demo_field_map', {}).get(key, key)
        fact = next(fact for fact in source['facts'] if fact['field_key'] == source_key)
        plain = CITATION_PATTERN.sub('', result['draft'][key])
        assert ' '.join(plain.split()) == ' '.join(fact['value'].split())
    payload = export(result)['pdf']
    output = tmp_path / 'fresh.pdf'
    output.write_bytes(payload)
    assert len(PdfReader(output).pages) == record['source_page_count']
    assert result['output_verification']['pdf']['status'] == 'passed'
    assert result['output_verification']['pdf']['sha']['output'] == sha256(payload).hexdigest()
    assert sha256(template.read_bytes()).hexdigest() == before


def test_m1_parses_actual_demo_source_pages_without_modifying_source():
    from evals.ra_public import validate_source_parser
    manifest = json.loads((ROOT / 'evals/ra_korean_sources.json').read_text(encoding='utf-8'))
    record = next(item for item in manifest['sources'] if item['id'] == 'ra-kr-hanmiflu-75')
    path = ROOT / record['path']
    before = sha256(path.read_bytes()).hexdigest()
    checked = validate_source_parser(record)
    assert checked['passed'], checked
    assert checked['page_count'] == 2
    assert sha256(path.read_bytes()).hexdigest() == before


def test_live_injected_mock_uses_real_m1_and_semantic_pipeline(attachment):
    client = OfflineClient()
    result = service.generate_mvp('제품명 검토', FORM_IDS[0], paths=[attachment], client=client)
    assert result['status'] == 'ready', result.get('review')
    assert result['mode'] == 'live' and result['semantic_required']
    assert result['documents'][0]['파일명'] == attachment.name
    assert {'brief', 'draft', 'boss_review', 'grounding', 'completeness'} <= {name for name, _ in client.calls}
    assert result['grounding']['reviewed_count'] == result['grounding']['claim_count']
    assert export(result)['pdf'].startswith(b'%PDF')


def test_questions_and_missing_evidence_are_normal_non_success_states():
    result = service.generate_mvp('품목허가 초안', FORM_IDS[0], client=OfflineClient(question=True))
    assert result['status'] == 'needs_information' and len(result['questions']) <= 2
    result = service.generate_mvp('제품명 검토', FORM_IDS[0], client=OfflineClient())
    assert result['status'] == 'needs_evidence' and 'draft' not in result


def test_uncited_live_claim_blocks_even_if_mock_semantic_reply_claims_support(attachment):
    result = service.generate_mvp('제품명 검토', FORM_IDS[0], paths=[attachment], client=OfflineClient(uncited=True))
    assert result['status'] == 'needs_revision'
    assert result['review']['blocking'] and '검증약' in result['draft']['제품명']
    with pytest.raises(ValueError):
        export(result)


def test_explicit_user_input_is_locked_and_later_edit_requires_reinspection(demo_results):
    result = service.generate_mvp('공식 원문 검토', FORM_IDS[0], demo=True,
                                  field_values={'신청인 성명': '테스트 의뢰인'})
    assert result['locked_fields']['신청인 성명']['value'] == '테스트 의뢰인'
    assert not review_result(result)['blocking']
    edited = {**result['draft'], '신청인 성명': result['draft']['신청인 성명'].replace('테스트 의뢰인', '다른 의뢰인')}
    checked = review_result(result, edited)
    assert checked['blocking']
    assert any(issue['code'] == 'user_input_changed' for issue in checked['warnings'])


def test_wrong_drug_number_and_changed_evidence_block_old_demo_proof(demo_results):
    result = deepcopy(demo_results[FORM_IDS[0]])
    result['draft']['제품명'] = result['draft']['제품명'].replace('75mg', '750mg')
    assert review_result(result)['blocking']
    with pytest.raises(ValueError):
        export(result)
    result = deepcopy(demo_results[FORM_IDS[0]])
    source = next(item for item in result['sources'] if item['text'] == '한미플루 75mg')
    source['text'] = '다른 약 75mg'
    assert review_result(result)['blocking']
    with pytest.raises(ValueError):
        export(result)


def test_regeneration_keeps_first_draft_time_run_id_and_rejects_mutated_baseline(demo_results):
    first = deepcopy(demo_results[FORM_IDS[0]])
    baseline = deepcopy(first['metrics']['baseline_draft'])
    second = service.generate_mvp('선정 항목 다시 검토', FORM_IDS[0], demo=True,
                                  previous_metrics=first['metrics'], field_values={'신청인 성명': '테스트 의뢰인'})
    assert second['run_id'] == first['run_id']
    assert second['metrics']['baseline_draft'] == baseline
    assert second['metrics']['draft_started_at'] == first['metrics']['draft_started_at']
    assert second['metrics']['revision_count'] == 1
    assert first['metrics']['latest_draft'] == first['draft']
    bad = deepcopy(second['metrics'])
    bad['baseline_draft']['제품명'] = second['draft']['제품명'] + ' tampered'
    with pytest.raises(ValueError, match='KPI 기준'):
        service.generate_mvp('검토', FORM_IDS[0], demo=True, previous_metrics=bad)


@pytest.mark.parametrize('values', [{'제품명': '위조된 약'}, {'임의 키': '값'}, [], ''])
def test_direct_values_cannot_masquerade_as_source_grounded_facts(values):
    with pytest.raises(ValueError, match='직접 입력'):
        service.generate_mvp('검토', FORM_IDS[0], demo=True, field_values=values)


@pytest.mark.parametrize('answers', [{'제품명': '위조된 약'}, {'제품명을(를) 알려주시겠습니까?': '위조된 약'}, [], ''])
def test_answers_cannot_replace_grounded_fields(answers):
    with pytest.raises(ValueError):
        service.generate_mvp('검토', FORM_IDS[0], demo=True, answers=answers)


def test_instruction_extraction_cannot_create_a_drug_fact_from_user_text(attachment):
    client = OfflineClient()
    client.case['mock_brief']['양식 항목'] = {'제품명': '검증약'}
    with pytest.raises(ValueError, match='작성 지시'):
        service.generate_mvp('제품명 검증약을 작성', FORM_IDS[0], paths=[attachment], client=client)


def test_other_form_cannot_reuse_previous_first_draft_kpi(demo_results):
    with pytest.raises(ValueError, match='다른 양식'):
        service.generate_mvp('검토', FORM_IDS[1], demo=True,
                             previous_metrics=demo_results[FORM_IDS[0]]['metrics'])


def test_unknown_id_and_demo_attachments_do_not_fall_back_to_success(attachment):
    with pytest.raises(ValueError, match='등록된'):
        service.generate_mvp('검토', 'unknown-form', demo=True)
    with pytest.raises(ValueError, match='데모'):
        service.generate_mvp('검토', FORM_IDS[0], paths=[attachment], demo=True)


def test_source_and_profile_sha_tampering_are_rejected_without_changing_originals(tmp_path):
    catalog = json.loads((ROOT / 'templates/ra_mvp_catalog.json').read_text(encoding='utf-8'))
    record = catalog['forms'][0]
    for relative in ('templates/ra_mvp_catalog.json', record['source_path'], record['profile_path']):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    original = (tmp_path / record['source_path']).read_bytes()
    (tmp_path / record['source_path']).write_bytes(original + b'altered')
    with pytest.raises(ValueError, match='원본 SHA'):
        service.resolve_mvp_form(FORM_IDS[0], root=tmp_path)
    (tmp_path / record['source_path']).write_bytes(original)
    profile = tmp_path / record['profile_path']
    profile.write_bytes(profile.read_bytes() + b' ')
    with pytest.raises(ValueError, match='프로파일 SHA'):
        service.resolve_mvp_form(FORM_IDS[0], root=tmp_path)


def test_permission_restricted_source_stays_blocked(monkeypatch):
    original = service.resolve_mvp_form
    def protected(*args, **kwargs):
        path, profile, record = original(*args, **kwargs)
        record['demo_source_id'] = 'ra-kr-aerius-5'
        return path, profile, record
    monkeypatch.setattr(service, 'resolve_mvp_form', protected)
    with pytest.raises(ValueError, match='추출'):
        service.generate_mvp('검토', FORM_IDS[0], demo=True)


def test_cli_evaluation_demo_and_unavailable_live_are_separate(tmp_path, monkeypatch):
    report = ra_mvp.evaluate(artifact_dir=tmp_path / 'artifacts')
    assert report['passed_count'] == report['case_count'] == 8
    assert report['configuration_count'] == 8
    assert report['distinct_registered_original_count'] == report['distinct_verified_original_count'] == 6
    assert report['actual_api_requests'] == report['model_response_cases'] == 0
    assert report['human_kpi_observations'] == 0 and not report['full_submission_ready']
    assert len({Path(row['output']).parent for row in report['results']}) == 8
    for row in report['results']:
        assert sha256(Path(row['output']).read_bytes()).hexdigest() == row['output_sha256']
        saved = json.loads(Path(row['provenance']).read_text(encoding='utf-8'))
        assert saved['mode'] == 'rules_demo' and '_native_preview_bytes' not in saved
    def unavailable(*args, **kwargs):
        raise ConfigurationError('private_key_must_never_be_exposed')
    monkeypatch.setattr(ra_mvp, 'generate_mvp', unavailable)
    path = tmp_path / 'live.json'
    assert ra_mvp.main(['--mode', 'live', '--output', str(path)]) == 1
    live = json.loads(path.read_text(encoding='utf-8'))
    assert live['passed_count'] == live['model_response_cases'] == live['actual_api_requests'] == 0
    assert all(row['status'] == 'unavailable' and 'output' not in row for row in live['results'])
    assert 'private_key' not in json.dumps(live)


def test_failed_sidecar_write_does_not_publish_success_artifact(tmp_path, monkeypatch):
    original = Path.write_text
    def fail(path, *args, **kwargs):
        if path.name.endswith('provenance.json'):
            raise OSError('private_path')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', fail)
    report = ra_mvp.evaluate(form_ids=[FORM_IDS[0]], artifact_dir=tmp_path)
    assert report['passed_count'] == 0
    assert report['results'][0]['status'] == 'blocked'
    assert 'output' not in report['results'][0] and 'private_path' not in json.dumps(report)
