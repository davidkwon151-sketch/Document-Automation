from copy import deepcopy
import json

import pytest

from agent.brief import analyze_brief, model_profile
from agent.boss_review import boss_review
from agent.completeness import check_completeness, completeness_fingerprint
from agent.draft import create_draft

BRIEF = {'목적': '자료 대조', '보고 대상': '팀장', '보고서 유형': '결과보고서',
         '마감': '', '분량': '', '부족한 정보': [], '질문': [], '양식 항목': {}}
DRAFT = {'제목': '검토 결과', '요약': '□ 매출 10원임 [S1]', '본문': '□ 매출 10원임 [S1]'}
SOURCES = [{'source_id': 'S1', 'text': '매출 10원', 'filename': '원자료.txt', 'page': 1}]


def profile():
    return {'format': 'pdf', 'source_sha256': 'a' * 64, 'citation_mode': 'sidecar',
            'document_kind': 'application', 'domain': 'pharmaceutical_ra',
            'ra_workflow': 'variation', 'ra_context': {'title': '변경', 'checks': ['용량 대조']},
            'constraints': {'summary_max_lines': 3}, 'warnings': ['서명은 직접 입력함'],
            'unregistered_fields': ['서명'], 'demo_values': {'제목': 'FAKE QA VALUE'},
            'verification': {'test_value': 'FAKE QA VALUE'}, 'values': {'제목': 'FAKE QA VALUE'},
            'profile_verification': {'sample': 'FAKE QA VALUE'},
            'demo_notice': 'FAKE QA VALUE', 'qa': 'FAKE QA VALUE',
            'answers': {'입력': 'FAKE QA VALUE'}, 'user_values': {'성명': 'FAKE QA VALUE'},
            'fields': [{'id': 'real-input', 'value_key': '검토 내용', 'label': '검토 내용',
                        'kind': 'pdf_overlay', 'page': 1, 'x': 80, 'y': 100, 'width': 200,
                        'height': 30, 'required': False, 'input_required': False,
                        'input_mode': 'source_grounded', 'max_chars': 40,
                        'value': 'FAKE QA VALUE', 'default': 'FAKE QA VALUE',
                        'default_value': 'FAKE QA VALUE', 'answer': 'FAKE QA VALUE',
                        'profile_verification': {'sample': 'FAKE QA VALUE'},
                        'user_value': 'FAKE QA VALUE', 'actual_value': 'FAKE QA VALUE'}]}


def test_profile_filters_only_examples_and_preserves_source_constraints_without_mutation():
    original = profile()
    before = deepcopy(original)
    sanitized = model_profile(original)
    assert 'FAKE QA VALUE' not in json.dumps(sanitized)
    assert all(sanitized[key] == original[key] for key in [
        'format', 'source_sha256', 'citation_mode', 'document_kind', 'domain',
        'ra_workflow', 'ra_context', 'constraints', 'warnings', 'unregistered_fields'])
    assert sanitized['fields'][0]['id'] == 'real-input'
    assert sanitized['fields'][0]['input_mode'] == 'source_grounded'
    assert sanitized['fields'][0]['max_chars'] == 40
    assert sanitized['fields'][0]['x'] == 80
    sanitized['fields'][0]['max_chars'] = 5
    assert original == before
    assert model_profile(None) is None


@pytest.mark.parametrize('name', ['corporate_ra_eurofins_sample_submission',
                                'corporate_ra_sgs_sample_submission',
                                'corporate_ra_thermofisher_custom_serum'])
def test_registered_company_qa_is_excluded_from_model_input(name):
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'templates/profiles' / (name + '.json')
    original = json.loads(path.read_text(encoding='utf-8'))
    before = deepcopy(original)
    clean = model_profile(original)
    assert not {'demo_values', 'demo_notice', 'profile_verification', 'qa'} & clean.keys()
    assert clean['fields'] == original['fields']
    assert original == before


@pytest.mark.parametrize('stage', ['brief', 'draft', 'boss_review', 'completeness'])
def test_every_generation_and_content_check_uses_clean_profile_with_real_user_evidence(stage):
    original = profile()
    before = deepcopy(original)
    calls = []

    class Client:
        def generate_json(self, prompt, payload):
            calls.append((prompt, deepcopy(payload)))
            assert 'FAKE QA VALUE' not in json.dumps(payload)
            if prompt == 'brief':
                assert payload['answers'] == {'작성자': '실제 사용자'}
                return deepcopy(BRIEF)
            if prompt == 'draft':
                assert payload['sources'] == SOURCES
                return deepcopy(DRAFT)
            if prompt == 'boss_review':
                return {'questions': ['질문 1?', '질문 2?', '질문 3?'], 'answers': []}
            assert payload['answers'] == {'작성자': '실제 사용자'}
            return {'issues': [], 'checked_fields': list(DRAFT)}

    client = Client()
    if stage == 'brief':
        analyze_brief('작성함', client, {'작성자': '실제 사용자'}, original)
    elif stage == 'draft':
        create_draft(BRIEF, SOURCES, client, original)
    elif stage == 'boss_review':
        boss_review(DRAFT, BRIEF, SOURCES, client, template_profile=original)
    else:
        report = check_completeness(DRAFT, BRIEF, original, client, instruction='작성함',
                                    answers={'작성자': '실제 사용자'})
        assert report['semantic_checked'] and not report['blocking']
        assert report['fingerprint'] == completeness_fingerprint(
            DRAFT, BRIEF, original, instruction='작성함', answers={'작성자': '실제 사용자'})
    assert calls and original == before
