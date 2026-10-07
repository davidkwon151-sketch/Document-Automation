"""RA internal preset integration: synthetic evidence, injected mock, fresh exports."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from docx import Document
import pytest

from agent.output_check import verify_output
from agent.pipeline import build_downloads, review_result, run_pipeline
from agent.review import CITATION_PATTERN, number_tokens
from app import ra_presets as PRESETS
from app.storage import load_record, save_record
from evals.run import MockEvaluationClient
from parsers import parse_file

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_LINES = ('시험용 합성 자료의 검토 범위는 내부 확인임', '시험 함량 5 mg임',
                   '자료 버전 A임', '변경 영향은 추가 확인 필요함')


class PresetClient(MockEvaluationClient):
    """Transport mock, not a semantic quality or actual model measurement."""

    def __init__(self, spec):
        super().__init__({'mock_brief': {'목적': '시험 함량·검토 범위·자료 버전·변경 영향 확인', '보고 대상': 'RA 팀장',
            '보고서 유형': spec['report_type'], '마감': '', '분량': '1쪽', '부족한 정보': [], '질문': []}})
        self.calls = []

    def generate_json(self, name, payload):
        self.calls.append((name, deepcopy(payload)))
        if name == 'draft':
            lines = []
            for text in SYNTHETIC_LINES:
                source = next(item for item in payload['sources'] if text in item['text'])
                lines.append(f"□ {text} [{source['source_id']}]")
            return {'제목': 'RA 합성자료 내부 검토', '요약': lines[1], '본문': '\n'.join(lines)}
        if name == 'grounding':
            by_id = {source['source_id']: source['text'] for source in payload['sources']}
            return {'claims': [{'field': item['field'], 'line': item['line'], 'status': 'supported',
                'evidence': [{'source_id': identifier, 'quote': by_id[identifier]}
                    for identifier in CITATION_PATTERN.findall(item['text']) if identifier in by_id]}
                for item in payload['claims']]}
        if name == 'completeness':
            return {'issues': [], 'checked_fields': list(payload['draft'])}
        return super().generate_json(name, payload)


@pytest.mark.parametrize('preset_id', ['ra_internal_review', 'ra_change_impact'])
@pytest.mark.parametrize('suffix', ['docx', 'hwpx'])
def test_ra_preset_actual_parser_to_independent_export_and_storage(preset_id, suffix, tmp_path):
    template, profile, spec = PRESETS.prepare_ra_preset(preset_id, format=suffix, root=ROOT)
    assert profile['template_origin'] == 'project_example'
    assert not profile['is_official_submission_form']
    assert '실제 회사 내부 양식' in spec['notice']
    template_before = sha256(template.read_bytes()).hexdigest()
    profile_before = deepcopy(profile)
    evidence = tmp_path / 'synthetic-ra-evidence.docx'
    document = Document()
    for line in SYNTHETIC_LINES:
        document.add_paragraph(line)
    document.save(evidence)
    evidence_before = sha256(evidence.read_bytes()).hexdigest()
    client = PresetClient(spec)
    result = run_pipeline(spec['instruction'], paths=[evidence], client=client,
                          template_profile=profile, ra_workflow=spec['ra_workflow'],
                          document_kind=spec['document_kind'], semantic_review=True)
    assert result['status'] == 'ready', result.get('review')
    assert profile == profile_before
    assert result['documents'][0]['파일명'] == evidence.name
    assert all(line in result['documents'][0]['본문'] for line in SYNTHETIC_LINES)
    assert result['domain'] == 'pharmaceutical_ra'
    assert result['brief']['보고서 유형'] == spec['report_type']
    assert result['ra_checks']['workflow'] == spec['ra_workflow']
    assert not result['ra_checks']['blocking']
    assert not result['grounding']['blocking'] and not result['completeness']['blocking']
    assert result['grounding']['reviewed_count'] == result['grounding']['claim_count'] > 0
    assert {'brief', 'draft', 'boss_review', 'grounding', 'completeness'} <= {name for name, _ in client.calls}
    for name, payload in client.calls:
        if name in {'brief', 'draft', 'boss_review'}:
            assert payload['template_profile']['source_sha256'] == template_before
            assert payload['template_profile']['preset_id'] == preset_id
            assert payload['template_profile']['format'] == suffix
    source_map = {source['source_id']: source for source in result['sources']}
    for line in result['draft']['본문'].splitlines():
        ids = CITATION_PATTERN.findall(line)
        assert ids and set(ids) <= source_map.keys()
        plain = CITATION_PATTERN.sub('', line).removeprefix('□ ').strip()
        assert any(plain in source_map[identifier]['text'] for identifier in ids)
    source_numbers = {item['key'] for source in result['sources'] for item in number_tokens(source['text'])}
    printed_numbers = {item['key'] for item in number_tokens(result['draft']['본문'])}
    assert source_numbers == printed_numbers and source_numbers
    assert '추가 확인 필요함' in result['draft']['본문']
    assert '승인 완료' not in result['draft']['본문'] and '영향 없음' not in result['draft']['본문']
    baseline = deepcopy(result['metrics']['baseline_draft'])
    started = result['metrics']['draft_started_at']
    outputs = build_downloads(result, confirmed=True, template_paths={suffix: template},
                              template_profiles={suffix: result['template_profile']}, native_review='off')
    assert set(outputs) == {suffix} and outputs[suffix].startswith(b'PK')
    output = tmp_path / f'filled.{suffix}'
    output.write_bytes(outputs[suffix])
    independent = verify_output(template, output, result['draft'], profile=result['template_profile'])
    assert independent['status'] == result['output_verification'][suffix]['status'] == 'passed'
    assert independent['sha']['output'] == sha256(output.read_bytes()).hexdigest()
    parsed_output = parse_file(output)
    assert '5 mg' in parsed_output['본문'] and '{{본문}}' not in parsed_output['본문']
    result.update(mode='mock_preset_integration', notice='합성 원자료·프로젝트 내부 예제·주입 mock 시험임',
                  actual_api_requests=0, actual_model_responses=0, human_kpi_observations=0,
                  submission_ready=False)
    saved_id = save_record(result, tmp_path / 'records-store')
    saved = load_record(saved_id, tmp_path / 'records-store')
    assert saved['mode'] == 'mock_preset_integration' and not saved['submission_ready']
    assert saved['sources'] == result['sources'] and saved['draft'] == result['draft']
    assert saved['metrics']['baseline_draft'] == baseline
    assert saved['metrics']['draft_started_at'] == started
    assert saved['output_verification'][suffix]['sha']['output'] == independent['sha']['output']
    assert sha256(template.read_bytes()).hexdigest() == template_before
    assert sha256(evidence.read_bytes()).hexdigest() == evidence_before
    artifacts = ROOT / 'outputs/ra-next-stage/formal-pipeline-artifacts' / preset_id / suffix
    artifacts.mkdir(parents=True, exist_ok=True)
    staged_output = artifacts / f'filled.{suffix}'
    staged_output.write_bytes(outputs[suffix])
    (artifacts / 'check.json').write_text(json.dumps({
        'scope': '합성 원자료·프로젝트 내부 예제·주입 mock 시험임', 'preset_id': preset_id,
        'format': suffix, 'original_sha256': template_before, 'original_unchanged': True,
        'fixture_sha256': evidence_before, 'output_sha256': independent['sha']['output'],
        'output_verification': independent, 'source_ids': list(source_map),
        'draft': saved['draft'], 'baseline': baseline, 'actual_api_requests': 0,
        'actual_model_responses': 0, 'human_kpi_observations': 0, 'submission_ready': False,
        'native_review': 'off', 'native_visual_qa': 'not_performed',
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    # A reviewed format is bound to its own source; do not soften this guard.
    other_suffix = 'hwpx' if suffix == 'docx' else 'docx'
    other_path, other_profile, _ = PRESETS.prepare_ra_preset(preset_id, format=other_suffix, root=ROOT)
    with pytest.raises(ValueError, match='다시|다름|검수|원본'):
        build_downloads(result, confirmed=True, template_paths={other_suffix: other_path},
                        template_profiles={other_suffix: other_profile}, native_review='off')
    # Mutating a same-unit dose after the passed proof must still block export.
    edited = {**result['draft'], '본문': result['draft']['본문'].replace('5 mg', '50 mg')}
    checked = review_result(result, edited)
    assert checked['blocking'] and not checked['corrected'] and '50 mg' in checked['draft']['본문']
    assert result['metrics']['baseline_draft'] == baseline
    result['draft'] = edited
    with pytest.raises(ValueError):
        build_downloads(result, confirmed=True, template_paths={suffix: template},
                        template_profiles={suffix: result['template_profile']}, native_review='off')
