from copy import deepcopy

import pytest

from agent.brief import analyze_brief
from agent.documents import DOCUMENT_KINDS, document_context, style_exempt_fields
from agent.pipeline import build_downloads, review_result, run_pipeline
from evals.run import MockEvaluationClient


def client():
    return MockEvaluationClient({'mock_brief': {'목적': '문서', '보고 대상': '담당자',
        '보고서 유형': '결과보고서', '마감': '', '분량': '1쪽', '부족한 정보': [], '질문': []}})


@pytest.mark.parametrize('kind', DOCUMENT_KINDS)
def test_document_purpose_is_forwarded_and_exports_with_sources(kind):
    fake = client()
    calls = []
    original = fake.generate_json
    def generate(name, payload):
        calls.append((name, deepcopy(payload)))
        return original(name, payload)
    fake.generate_json = generate
    text = '문서를 검토함'
    document = {'파일명': '자료.txt', '본문': text, '표 목록': [], '페이지/시트 정보': [{'본문': text, '페이지': 1, '시트': None}]}
    result = run_pipeline('첨부 자료로 문서를 작성해줘', documents=[document], client=fake, document_kind=kind)
    assert result['document_kind'] == kind
    if kind != 'report':
        assert all(payload['template_profile']['document_kind'] == kind for name, payload in calls if name in {'brief', 'draft'})
    assert result['status'] == 'ready'
    outputs = build_downloads(result, confirmed=True)
    assert set(outputs) == {'docx', 'hwpx'}
    if kind != 'report':
        from io import BytesIO
        from docx import Document
        text = '\n'.join(paragraph.text for paragraph in Document(BytesIO(outputs['docx'])).paragraphs)
        assert '사내외 문서 (일반 검토용)' in text
        assert '결과보고서' not in text.splitlines()[1]


def test_external_application_does_not_ask_internal_report_type():
    fake = client()
    fake.case['mock_brief'].update({'보고서 유형': '', '부족한 정보': ['보고서 유형'], '질문': ['보고서 유형을 알려주세요']})
    profile, kind = document_context(kind='application')
    brief = analyze_brief('신청서 작성', fake, template_profile=profile)
    assert brief['보고서 유형'] == '결과보고서' and not brief['부족한 정보'] and not brief['질문']
    original = analyze_brief('보고서 작성', fake)
    assert original['부족한 정보'] == ['보고서 유형']


def test_external_style_exemption_preserves_facts_and_original_wording():
    profile, _ = document_context(kind='official_letter')
    assert style_exempt_fields(profile) == {'요약', '본문'}
    result = {'template_profile': profile, 'sources': [{'source_id': 'S1', 'text': '예산 120만원'}],
              'draft': {'제목': '안내문', '요약': '자료 확인을 요청드립니다.', '본문': '예산 120만원 [S1]'}}
    checked = review_result(result)
    assert not checked['blocking']
    assert checked['draft']['요약'] == '자료 확인을 요청드립니다.'
    result['draft']['본문'] = '예산 999만원 [S1]'
    # Source checking still runs; unsupported numbers cannot pass by choosing a form kind.
    checked = review_result(result)
    assert checked['corrected'] or checked['blocking']


def test_unknown_document_purpose_is_rejected():
    with pytest.raises(ValueError, match='문서 종류'):
        document_context(kind='unknown')


def test_pure_request_is_not_an_uncited_fact_but_numeric_request_still_requires_evidence():
    from agent.review import is_factual_line
    assert not is_factual_line('자료 확인을 요청드립니다.')
    assert is_factual_line('예산 120만원 승인을 요청드립니다.')
    assert is_factual_line('자료 확인 결과 오류가 없음')
