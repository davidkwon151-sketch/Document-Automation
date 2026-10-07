"""Specialized review is required during generation, edits, and export."""

from copy import deepcopy
from pathlib import Path

import pytest

from agent.pipeline import build_downloads, review_result, run_pipeline
from agent.review import review_draft
from evals.run import MockEvaluationClient

ROOT = Path(__file__).resolve().parents[1]


class DomainClient(MockEvaluationClient):
    """Records real orchestration payloads; no network or live-model claims."""

    def __init__(self, *, candidate=None):
        super().__init__({'mock_brief': {
            '목적': '업무', '보고 대상': '팀장', '보고서 유형': '결과보고서',
            '마감': '', '분량': '1쪽', '부족한 정보': [], '질문': []}})
        self.calls = []
        self.candidate = candidate

    def generate_json(self, name, payload):
        self.calls.append((name, deepcopy(payload)))
        if name == 'draft' and self.candidate:
            source = payload['sources'][0]
            line = f"□ {self.candidate} [{source['source_id']}]"
            return {'제목': '업무 문서', '요약': line, '본문': line}
        if name == 'grounding':
            by_id = {source['source_id']: source['text'] for source in payload['sources']}
            from agent.review import CITATION_PATTERN
            return {'claims': [{
                'field': item['field'], 'line': item['line'], 'status': 'supported',
                'evidence': [{'source_id': identifier, 'quote': by_id[identifier]}
                             for identifier in CITATION_PATTERN.findall(item['text']) if identifier in by_id],
            } for item in payload['claims']]}
        if name == 'completeness':
            return {'issues': [], 'checked_fields': list(payload['draft'])}
        return super().generate_json(name, payload)


DOMAIN_CASES = [
    ('business_support', 'business', 'trade_sales', '계약금액 USD 1000임'),
    ('business_support', 'business', 'overseas_business', '투자 계획 USD 1000임'),
    ('business_support', 'business', 'government_grant', '정부지원금 100만원임'),
    ('business_support', 'business', 'rd_project', '과제번호: P001 신청 계획임'),
    ('office_finance', 'office', 'office_planning', '예산 계획 100만원임'),
    ('office_finance', 'office', 'financial_report', '연결 매출 실적 100억원임'),
    ('office_finance', 'office', 'daily_approval', '구매 승인 요청함'),
    ('office_finance', 'office', 'industrial_quality', '검사 계획 100건임'),
]


def domain_result(prefix='business', workflow='trade_sales', text='계약금액 USD 1000임', **kwargs):
    client = kwargs.pop('client', DomainClient())
    client.case['mock_brief']['목적'] = text.split()[0]
    return run_pipeline('팀장에게 업무 결과를 작성', documents=documents(text),
                        client=client,
                        **{prefix + '_workflow': workflow}, **kwargs)


def fixture_client():
    return MockEvaluationClient({'mock_brief': {
        '목적': '용량', '보고 대상': '팀장', '보고서 유형': '결과보고서',
        '마감': '', '분량': '1쪽', '부족한 정보': [], '질문': []}})


def documents(text='용량 5 mg임'):
    return [{'파일명': '검증자료.txt', '본문': text, '표 목록': [],
             '페이지/시트 정보': [{'본문': text, '페이지': 1, '시트': None, '표 목록': []}]}]


def test_ra_context_is_sent_to_brief_and_draft_without_manual_template():
    client = fixture_client()
    original = client.generate_json
    calls = []
    def generate(name, payload):
        calls.append((name, deepcopy(payload)))
        return original(name, payload)
    client.generate_json = generate
    result = run_pipeline('용량 결과보고서', documents=documents(), client=client, ra_workflow='clinical_trial')
    assert result['domain'] == 'pharmaceutical_ra'
    assert result['ra_checks']['workflow'] == 'clinical_trial'
    assert not result['ra_checks']['blocking'], result['ra_checks']
    for name, payload in calls:
        if name in {'brief', 'draft'}:
            assert payload['template_profile']['ra_workflow'] == 'clinical_trial'
            assert '시험번호' in ' '.join(payload['template_profile']['ra_context']['checks'])


def test_ra_wrong_dose_is_preserved_and_blocks_export_even_with_old_passed_checks():
    result = run_pipeline('용량 결과보고서', documents=documents(), client=fixture_client(), ra_workflow='product_approval')
    result['draft']['본문'] = result['draft']['본문'].replace('5 mg', '50 mg')
    checked = review_result(result)
    assert '50 mg' in checked['draft']['본문']
    assert checked['blocking'] and not checked['corrected']
    assert result['ra_checks']['blocking']
    # Export must recompute the domain check rather than trusting this old flag.
    result['ra_checks'] = {'blocking': False, 'issues': []}
    with pytest.raises(ValueError):
        build_downloads(result, confirmed=True)
    assert result['ra_checks']['blocking']


def test_ra_changed_unit_and_uncited_approval_claims_block_reinspection():
    result = run_pipeline('용량 결과보고서', documents=documents(), client=fixture_client(), ra_workflow='product_approval')
    source_id = result['sources'][0]['source_id']
    result['draft']['본문'] = f'□ 용량 5 mg/mL임 [{source_id}]\n○ 품목 승인 완료함 [{source_id}]'
    checked = review_result(result)
    assert checked['blocking']
    assert {'ra_quantity_mismatch', 'ra_claim_unverified'} <= {issue['code'] for issue in result['ra_checks']['issues']}


def test_ra_question_response_keeps_domain_and_original_instruction():
    client = fixture_client()
    client.case['mock_brief'].update({'부족한 정보': ['보고 대상'], '질문': ['누구에게 보고하나요?']})
    result = run_pipeline('임상 시험 계획서 작성', client=client, ra_workflow='clinical_trial')
    assert result['status'] == 'needs_information'
    assert result['instruction'] == '임상 시험 계획서 작성'
    assert result['domain'] == 'pharmaceutical_ra' and result['ra_workflow'] == 'clinical_trial'


def test_unknown_ra_workflow_rejected_before_model_request():
    with pytest.raises(ValueError, match='RA'):
        run_pipeline('작성', client=fixture_client(), ra_workflow='unregistered')


def test_registered_fixed_field_style_is_exempt_without_exempting_source_check():
    draft = {'제목': 'CTD overview', '요약': '□ 자료를 검토함 [S1]', '본문': 'Dose: 5 mg [S1]'}
    sources = [{'source_id': 'S1', 'text': '자료를 검토함; Dose: 5 mg'}]
    result = review_draft(draft, sources, auto_correct=False, style_exempt_fields={'본문'})
    assert result['draft'] == draft
    assert not any(issue['code'] in {'ending_style', 'bullet_style'} and issue['field'] == '본문' for issue in result['warnings'])
    wrong = draft | {'본문': 'Dose: 50 mg [S1]'}
    assert review_draft(wrong, sources, auto_correct=False, style_exempt_fields={'본문'})['blocking']


@pytest.mark.parametrize('domain,prefix,workflow,text', DOMAIN_CASES)
def test_domain_guidance_and_form_constraints_reach_brief_draft_and_boss(domain, prefix, workflow, text):
    from llm.client import load_prompt
    client = DomainClient()
    profile = {'fields': [{'value_key': '본문', 'label': '검토 내용', 'kind': 'placeholder',
                           'id': 'placeholder:본문', 'required': True, 'max_chars': 1000}],
               'constraints': {'summary_max_lines': 2}}
    result = domain_result(prefix, workflow, text, client=client, template_profile=profile)
    assert result['status'] == 'ready', result['review']
    assert result['domain'] == result['brief']['domain'] == domain
    assert result[prefix + '_checks']['workflow'] == workflow
    assert profile == {'fields': [{'value_key': '본문', 'label': '검토 내용', 'kind': 'placeholder',
                                  'id': 'placeholder:본문', 'required': True, 'max_chars': 1000}],
                      'constraints': {'summary_max_lines': 2}}
    for name, payload in client.calls:
        if name in {'brief', 'draft', 'boss_review'}:
            supplied = payload['template_profile']
            assert supplied['fields'] == profile['fields']
            assert supplied['constraints'] == profile['constraints']
            assert supplied[prefix + '_context']['guidance'] == load_prompt(prefix + '.md')
            assert supplied[prefix + '_workflow'] == workflow


@pytest.mark.parametrize('prefix,workflow,text,bad,code', [
    ('business', 'trade_sales', '계약금액 USD 1000임', '계약금액 EUR 1000임', 'business_money_mismatch'),
    ('office', 'financial_report', '연결 매출 실적 100억원임', '별도 매출 실적 100억원임', 'office_number_mismatch'),
])
def test_domain_edit_rechecks_same_number_in_different_currency_or_accounting_scope(prefix, workflow, text, bad, code):
    result = domain_result(prefix, workflow, text)
    source_id = result['sources'][0]['source_id']
    edited = {**result['draft'], '본문': f'□ {bad} [{source_id}]'}
    checked = review_result(result, edited)
    assert checked['draft'] == edited and not checked['corrected']
    assert checked['blocking']
    # A passed old check must never authorize a changed document.
    assert result[prefix + '_checks']['blocking']
    assert any(issue['code'] == code for issue in result[prefix + '_checks']['issues'])
    result['draft'] = edited
    result[prefix + '_checks'] = {'blocking': False, 'issues': []}
    with pytest.raises(ValueError, match='오류'):
        build_downloads(result, confirmed=True)


@pytest.mark.parametrize('prefix,workflow', [('business', 'government_grant'), ('office', 'office_planning')])
def test_domain_source_change_invalidates_model_proof_and_recomputes_numeric_check(prefix, workflow):
    result = domain_result(prefix, workflow, '예산 100만원임', semantic_review=True)
    assert result['status'] == 'ready'
    result['sources'][0]['text'] = '예산 200만원임'
    checked = review_result(result)
    assert checked['blocking'] and not checked['corrected']
    assert '100만원' in checked['draft']['본문']
    assert any(issue['code'] == 'semantic_stale' for issue in checked['warnings'])
    assert any(issue['code'] == 'source_context_invalid' for issue in checked['warnings'])
    assert result[prefix + '_checks']['blocking']
    with pytest.raises(ValueError):
        build_downloads(result, confirmed=True)


def test_specialized_bad_generation_is_not_silently_rewritten_but_general_review_can_correct():
    source = '계약금액 100만원임'
    specialized = domain_result(text=source, client=DomainClient(candidate='계약금액 999만원임'))
    assert specialized['status'] == 'needs_revision'
    assert '999만원' in specialized['draft']['본문']
    assert not specialized['initial_review']['corrected'] and not specialized['review']['corrected']
    client = DomainClient(candidate='계약금액 999만원임')
    client.case['mock_brief']['목적'] = '계약금액'
    general = run_pipeline('업무 결과를 작성', documents=documents(source), client=client)
    assert general['status'] == 'ready', general['review']
    assert '100만원' in general['draft']['본문'] and '999만원' not in general['draft']['본문']
    assert general['initial_review']['corrected']


@pytest.mark.parametrize('kind', ['report', 'application', 'plan', 'official_letter', 'minutes', 'other'])
def test_explicit_document_kind_stays_separate_from_internal_report_classification(kind):
    client = DomainClient()
    if kind != 'report':
        client.case['mock_brief'].update({'보고서 유형': '', '부족한 정보': ['보고서 유형'],
                                         '질문': ['보고서 유형을 알려주세요']})
    result = domain_result(document_kind=kind, client=client)
    assert result['status'] == 'ready', result
    assert result['document_kind'] == result['brief']['document_kind'] == kind
    assert result['template_profile']['document_kind'] == kind
    assert result['brief']['보고서 유형'] == '결과보고서'
    assert not result['brief']['부족한 정보']


def test_domain_mixing_is_rejected_before_any_model_or_embedding_request():
    client = DomainClient()
    with pytest.raises(ValueError, match='하나의 업무'):
        run_pipeline('업무 작성', client=client, business_workflow='trade_sales', office_workflow='financial_report')
    with pytest.raises(ValueError):
        run_pipeline('업무 작성', client=client, business_workflow='trade_sales',
                     template_profile={'domain': 'pharmaceutical_ra', 'ra_workflow': 'clinical_trial'})
    assert not client.calls


def test_domain_locked_user_fields_export_and_cannot_change_value_or_source(tmp_path):
    from docx import Document
    profile = {'fields': [{'value_key': '신청인', 'label': '신청인', 'required': True,
                           'input_required': True, 'narrative_style_required': False}]}
    client = DomainClient()
    result = domain_result('business', 'government_grant', '정부지원금 100만원임',
                           template_profile=profile, field_values={'신청인': '홍길동'}, client=client)
    assert result['status'] == 'ready', result['review']
    original = result['draft']['신청인']
    baseline = deepcopy(result['metrics']['baseline_draft'])
    template = tmp_path / '신청서.docx'
    doc = Document()
    for key in ('제목', '요약', '본문', '신청인'):
        doc.add_paragraph('{{' + key + '}}')
    doc.save(template)
    outputs = build_downloads(result, confirmed=True, template_paths={'docx': template})
    assert outputs['docx'].startswith(b'PK')
    assert result['output_verification']['docx']['status'] == 'passed'
    for changed in ('김영희 ' + original[original.index('['):],
                    '홍길동 [' + result['sources'][0]['source_id'] + ']'):
        checked = review_result(result, {**result['draft'], '신청인': changed})
        assert checked['blocking']
        assert any(item['code'] == 'user_input_changed' for item in checked['warnings'])
    assert result['draft']['신청인'] == original
    regenerated = domain_result('business', 'government_grant', '정부지원금 100만원임',
                               template_profile=profile, field_values={'신청인': '홍길동'},
                               previous_metrics=result['metrics'])
    assert regenerated['metrics']['baseline_draft'] == baseline
    assert regenerated['metrics']['draft_started_at'] == result['metrics']['draft_started_at']


def test_domain_export_still_reopens_file_and_blocks_structural_damage(monkeypatch):
    from zipfile import ZipFile
    import templates
    original = templates.fill_compatible_template
    def damage(*args, **kwargs):
        path = original(*args, **kwargs)
        with ZipFile(path) as archive:
            parts = [(item, archive.read(item.filename)) for item in archive.infolist()
                     if item.filename != 'word/styles.xml']
        with ZipFile(path, 'w') as archive:
            for item, data in parts:
                archive.writestr(item, data)
        return path
    monkeypatch.setattr(templates, 'fill_compatible_template', damage)
    result = domain_result('office', 'daily_approval', '구매 승인 요청함')
    with pytest.raises(ValueError, match='출력 검증 실패'):
        build_downloads(result, confirmed=True, template_paths={'docx': ROOT / 'templates/result_report.docx'})
    assert 'output_verification' not in result
