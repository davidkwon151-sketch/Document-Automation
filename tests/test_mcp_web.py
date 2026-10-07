"""Actual synthetic files and mocked MCP-caller inference; no real Claude calls."""

from base64 import b64encode
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from docx import Document
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app import web_api
from app.guest_access import GuestAccess
from test_ra_auto_service import PipelineMock
from test_web_api import SECRET, configured, copied, request, upload_data
from test_web_guest_flow import enter


@pytest.fixture
def backend(tmp_path):
    root = tmp_path / 'private'
    def forbidden():
        pytest.fail('MCP must not create a paid API client or use a provider fallback')
    api = TestClient(web_api.create_app(root=root, secret=SECRET, client_factory=forbidden),
                     raise_server_exceptions=False)
    return root, api


def credential(api, user='user-A'):
    issued = request(api, 'POST', '/api/mcp/credentials', {}, user=user)
    assert issued.status_code == 200, issued.text
    return issued.json()


def rpc(api, token, method, params=None):
    return request(api, 'POST', '/mcp', {'jsonrpc': '2.0', 'id': uuid4().hex,
                   'method': method, 'params': params or {}}, user='mcp:' + token)


def tool(api, token, name, arguments=None, *, error=False):
    response = rpc(api, token, 'tools/call', {'name': name, 'arguments': arguments or {}})
    assert response.status_code == 200, response.text
    result = response.json()['result']
    assert result['isError'] is error, result
    return result if error else result['structuredContent']


def resume(api, token, identifier, value, *, model=None):
    model = model or PipelineMock()
    steps = []
    for _ in range(30):
        if value.get('status') != 'awaiting_claude':
            return value, steps
        inference = value['inference']
        name = Path(inference['prompt_name']).stem
        assert inference['instructions'].strip()
        assert 'image' not in inference
        response = model.generate_json(name, inference['payload'])
        steps.append(name)
        value = tool(api, token, 'ra_respond', {'job_id': identifier,
            'request_id': inference['request_id'], 'fingerprint': inference['fingerprint'],
            'response': response})
    pytest.fail('connector did not finish within bounded synthetic inference steps')


def test_mcp_real_synthetic_docx_write_and_independent_export(backend):
    root, api = backend
    identifier, _ = configured(api)
    issued = credential(api)
    initialized = rpc(api, issued['token'], 'initialize', {'protocolVersion': '2025-11-25'}).json()['result']
    assert initialized['protocolVersion'] == '2025-11-25'
    names = {item['name'] for item in rpc(api, issued['token'], 'tools/list').json()['result']['tools']}
    assert {'ra_start', 'ra_respond', 'ra_get_job', 'ra_list_jobs'} <= names
    value = tool(api, issued['token'], 'ra_start', {'job_id': identifier, 'operation': 'generate'})
    assert value['status'] == 'awaiting_claude'
    recovered = tool(api, issued['token'], 'ra_get_job', {'job_id': identifier})
    assert recovered['inference']['request_id'] == value['inference']['request_id']
    assert recovered['inference']['fingerprint'] == value['inference']['fingerprint']
    assert request(api, 'POST', f'/api/jobs/{identifier}/export', {'confirmed': True}).status_code == 409
    final, steps = resume(api, issued['token'], identifier, value)
    assert {'brief', 'draft', 'grounding', 'completeness'} <= set(steps)
    result = final['result']
    assert result['ready_for_output_check'], result['review']
    assert result['actual_model_requests'] == 0
    assert result['external_response_count'] == len(steps)
    assert result['inference_origin'] == 'claude_mcp_client_supplied'
    assert result['model_identity_verified'] is False
    assert result['embedding_mode'] == 'deterministic_local_lexical'
    assert not result['human_kpi_measured']
    assert result['target_coverage']['filled_count'] == 5
    response = request(api, 'POST', f'/api/jobs/{identifier}/export', {'confirmed': True})
    assert response.status_code == 200, response.text
    with ZipFile(BytesIO(response.content)) as archive:
        saved = Document(BytesIO(archive.read('RA_작성본.docx')))
        assert [row.cells[1].text for row in saved.tables[0].rows] == [
            'SyntheticDrugA', '흰색 정제', '실온 보관', '30정', '담당자 직접 시험 입력']
        evidence = json.loads(archive.read('출처와_검수기록.json'))
        assert evidence['output_verification']['docx']['status'] == 'passed'
        assert not evidence['submission_ready']
        assert evidence['inference_origin'] == 'claude_mcp_client_supplied'
        assert str(root) not in json.dumps(evidence)


def test_foreign_credentials_cannot_access_or_answer_other_workspace(backend):
    _, api = backend
    identifier, _ = configured(api)
    a, b = credential(api), credential(api, 'user-B')
    listed = tool(api, b['token'], 'ra_list_jobs')
    assert listed['jobs'] == []
    tool(api, b['token'], 'ra_get_job', {'job_id': identifier}, error=True)
    tool(api, b['token'], 'ra_start', {'job_id': identifier, 'operation': 'generate'}, error=True)
    pending = tool(api, a['token'], 'ra_start', {'job_id': identifier, 'operation': 'generate'})
    inference = pending['inference']
    tool(api, b['token'], 'ra_respond', {'job_id': identifier,
        'request_id': inference['request_id'], 'fingerprint': inference['fingerprint'], 'response': {}}, error=True)
    assert tool(api, a['token'], 'ra_get_job', {'job_id': identifier})['status'] == 'awaiting_claude'
    assert request(api, 'DELETE', '/api/mcp/credentials/' + a['credential_id'], user='user-B').status_code == 404
    assert request(api, 'DELETE', '/api/mcp/credentials/' + a['credential_id']).status_code == 200
    assert rpc(api, a['token'], 'ping').status_code == 401
    assert request(api, 'GET', '/api/me', user='mcp:' + b['token']).status_code == 401


def test_guest_parent_revocation_invalidates_existing_connector(backend):
    root, api = backend
    guests = GuestAccess(root)
    invite, guest = enter(guests, api, '가입 없는 합성 RA 담당자')
    identifier, _ = configured(api, user=guest)
    issued = credential(api, user=guest)
    assert tool(api, issued['token'], 'ra_get_job', {'job_id': identifier})['job_id'] == identifier
    guests.revoke(invite['invite_id'])
    assert rpc(api, issued['token'], 'ping').status_code == 401
    assert request(api, 'GET', f'/api/jobs/{identifier}', user=guest).status_code == 401


@pytest.mark.parametrize('tamper', ['request_id', 'fingerprint'])
def test_unbound_response_does_not_consume_pending_request(backend, tamper):
    _, api = backend
    identifier, _ = configured(api)
    issued = credential(api)
    pending = tool(api, issued['token'], 'ra_start', {'job_id': identifier, 'operation': 'generate'})
    inference = pending['inference']
    arguments = {'job_id': identifier, 'request_id': inference['request_id'],
                 'fingerprint': inference['fingerprint'], 'response': {}}
    arguments[tamper] = '0' * (32 if tamper == 'request_id' else 64)
    tool(api, issued['token'], 'ra_respond', arguments, error=True)
    assert tool(api, issued['token'], 'ra_get_job', {'job_id': identifier})['status'] == 'awaiting_claude'
    final, _ = resume(api, issued['token'], identifier, pending)
    assert final['result']['ready_for_output_check']


@pytest.mark.parametrize('change', ['configure', 'source'])
def test_changed_inputs_invalidate_pending_answer(backend, change):
    root, api = backend
    identifier, config = configured(api)
    issued = credential(api)
    pending = tool(api, issued['token'], 'ra_start', {'job_id': identifier, 'operation': 'generate'})
    inference = pending['inference']
    if change == 'configure':
        response = request(api, 'POST', f'/api/jobs/{identifier}/configure',
                           {**config, 'instruction': config['instruction'] + ' 변경된 요청'})
        assert response.status_code == 200, response.text
    else:
        owner = root / sha256(b'user-A').hexdigest() / identifier
        source = next(owner.rglob('Synthetic_RA_Evidence.txt'))
        source.write_text('제품명: DifferentProduct', encoding='utf-8')
    tool(api, issued['token'], 'ra_respond', {'job_id': identifier,
        'request_id': inference['request_id'], 'fingerprint': inference['fingerprint'], 'response': {}}, error=True)
    assert request(api, 'POST', f'/api/jobs/{identifier}/export', {'confirmed': True}).status_code != 200


def test_restart_marks_pending_interrupted_and_blocks_old_response(backend):
    root, api = backend
    identifier, _ = configured(api)
    issued = credential(api)
    pending = tool(api, issued['token'], 'ra_start', {'job_id': identifier, 'operation': 'generate'})
    def forbidden():
        pytest.fail('must not instantiate paid inference')
    restarted = TestClient(web_api.create_app(root=root, secret=SECRET, client_factory=forbidden),
                           raise_server_exceptions=False)
    value = tool(restarted, issued['token'], 'ra_get_job', {'job_id': identifier})
    assert value['status'] != 'awaiting_claude'
    assert not value.get('result')
    inference = pending['inference']
    tool(restarted, issued['token'], 'ra_respond', {'job_id': identifier,
        'request_id': inference['request_id'], 'fingerprint': inference['fingerprint'], 'response': {}}, error=True)


def test_png_mcp_image_content_then_original_confirmation_required(backend):
    _, api = backend
    payload = upload_data()
    stream = BytesIO()
    Image.new('RGB', (20, 20), color='white').save(stream, format='PNG')
    payload['sources'] = [{'name': 'Synthetic_RA_Scan.png', 'base64': b64encode(stream.getvalue()).decode()}]
    uploaded = request(api, 'POST', '/api/jobs', payload)
    assert uploaded.status_code == 200, uploaded.text
    identifier = uploaded.json()['job_id']
    issued = credential(api)
    response = rpc(api, issued['token'], 'tools/call', {'name': 'ra_start',
        'arguments': {'job_id': identifier, 'operation': 'ocr'}})
    value = response.json()['result']
    assert not value['isError'], value
    assert [item['type'] for item in value['content']] == ['text', 'image']
    assert value['content'][1]['mimeType'] == 'image/png'
    assert 'data' not in json.dumps(value['structuredContent'])
    pending = value['structuredContent']['inference']
    final = tool(api, issued['token'], 'ra_respond', {'job_id': identifier,
        'request_id': pending['request_id'], 'fingerprint': pending['fingerprint'],
        'response': {'본문': '제품명: SyntheticDrugA\n제형·함량: 정제 10 mg\n성상: 흰색 정제',
                     '표 목록': [], '불확실한 항목': []}})
    assert final['status'] == 'uploaded'
    assert final['intake']['files'][0]['status'] == 'needs_confirmation'
    assert all(source['requires_verification'] for source in final['intake']['sources'])
    assert request(api, 'POST', f'/api/jobs/{identifier}/export', {'confirmed': True}).status_code == 409


def test_completed_mcp_result_cannot_download_after_server_restart(backend):
    root, api = backend
    identifier, _ = configured(api)
    issued = credential(api)
    pending = tool(api, issued['token'], 'ra_start', {'job_id': identifier, 'operation': 'generate'})
    final, _ = resume(api, issued['token'], identifier, pending)
    assert final['result']['ready_for_output_check']
    assert request(api, 'POST', f'/api/jobs/{identifier}/export', {'confirmed': True}).status_code == 200
    restarted = TestClient(web_api.create_app(root=root, secret=SECRET), raise_server_exceptions=False)
    assert request(restarted, 'POST', f'/api/jobs/{identifier}/export', {'confirmed': True}).status_code == 409


def test_mcp_review_preserves_first_ai_baseline_and_does_not_edit_locked_identity(backend):
    _, api = backend
    identifier, _ = configured(api)
    issued = credential(api)
    first, _ = resume(api, issued['token'], identifier,
        tool(api, issued['token'], 'ra_start', {'job_id': identifier, 'operation': 'generate'}))
    baseline = deepcopy(first['result']['metrics'])
    draft = deepcopy(first['result']['draft'])
    draft['제목'] = '담당자가 수정한 합성 검토 제목'
    pending = tool(api, issued['token'], 'ra_start', {'job_id': identifier, 'operation': 'review', 'draft': draft})
    assert request(api, 'POST', f'/api/jobs/{identifier}/export', {'confirmed': True}).status_code == 409
    final, steps = resume(api, issued['token'], identifier, pending)
    assert set(steps) == {'grounding', 'completeness'}
    result = final['result']
    assert result['ready_for_output_check'], result['review']
    assert result['draft']['제목'] == draft['제목']
    assert result['metrics']['baseline_draft'] == baseline['baseline_draft']
    assert result['metrics']['draft_started_at'] == baseline['draft_started_at']
    assert result['metrics']['rejection_count'] == baseline['rejection_count']


def test_two_page_scanned_pdf_ocr_is_ordered_and_never_partial_success(backend):
    _, api = backend
    payload = upload_data()
    stream = BytesIO()
    Image.new('RGB', (20, 20), 'white').save(stream, format='PDF', save_all=True,
                                           append_images=[Image.new('RGB', (20, 20), 'red')])
    payload['sources'] = [{'name': 'Synthetic_RA_Scan.pdf', 'base64': b64encode(stream.getvalue()).decode()}]
    uploaded = request(api, 'POST', '/api/jobs', payload)
    assert uploaded.status_code == 200, uploaded.text
    identifier = uploaded.json()['job_id']
    issued = credential(api)
    pending = tool(api, issued['token'], 'ra_start', {'job_id': identifier, 'operation': 'ocr'})
    ids = []
    for page in (1, 2):
        inference = pending['inference']
        assert inference['payload']['페이지'] == page
        ids.append(inference['request_id'])
        response = {'본문': f'합성 {page}쪽 원문', '표 목록': [], '불확실한 항목': []}
        pending = tool(api, issued['token'], 'ra_respond', {'job_id': identifier,
            'request_id': inference['request_id'], 'fingerprint': inference['fingerprint'], 'response': response})
    assert len(set(ids)) == 2
    assert pending['status'] == 'uploaded'
    assert {source['page'] for source in pending['intake']['sources']} == {1, 2}
    assert pending['intake']['files'][0]['status'] == 'needs_confirmation'
    # Rereading discards earlier receipts; one unread page is not a complete file.
    pending = tool(api, issued['token'], 'ra_start', {'job_id': identifier, 'operation': 'ocr'})
    inference = pending['inference']
    pending = tool(api, issued['token'], 'ra_respond', {'job_id': identifier,
        'request_id': inference['request_id'], 'fingerprint': inference['fingerprint'],
        'response': {'본문': '합성 1쪽 원문', '표 목록': [], '불확실한 항목': []}})
    inference = pending['inference']
    final = tool(api, issued['token'], 'ra_respond', {'job_id': identifier,
        'request_id': inference['request_id'], 'fingerprint': inference['fingerprint'], 'response': {}})
    assert final['intake']['files'][0]['status'] == 'deferred'
    assert final['intake']['sources'] == []
    assert request(api, 'POST', f'/api/jobs/{identifier}/export', {'confirmed': True}).status_code == 409


def test_wrong_claude_supplied_number_remains_blocked_by_source_checks(backend):
    _, api = backend
    identifier, _ = configured(api)
    issued = credential(api)
    class WrongNumber(PipelineMock):
        def generate_json(self, name, payload):
            response = super().generate_json(name, payload)
            if name == 'draft':
                response['포장단위'] = response['포장단위'].replace('30정', '99정')
            return response
    final, _ = resume(api, issued['token'], identifier,
        tool(api, issued['token'], 'ra_start', {'job_id': identifier, 'operation': 'generate'}),
        model=WrongNumber())
    assert not final['result']['ready_for_output_check']
    assert final['result']['draft']['포장단위'].startswith('99정')  # Never silently corrected.
    assert request(api, 'POST', f'/api/jobs/{identifier}/export', {'confirmed': True}).status_code == 422


def test_initializer_malformed_version_is_safe_jsonrpc_error(backend):
    _, api = backend
    token = credential(api)['token']
    for version in ([], {}, 1, None):
        response = rpc(api, token, 'initialize', {'protocolVersion': version})
        assert response.status_code < 500
        assert 'error' in response.json()


def test_actual_human_answers_saved_without_paid_model_and_included_in_next_request(backend):
    _, api = backend
    identifier, _ = configured(api)
    issued = credential(api)
    class MissingBrief(PipelineMock):
        def generate_json(self, name, payload):
            response = super().generate_json(name, payload)
            if name == 'brief':
                response['부족한 정보'] = ['검토 목적']
                response['질문'] = ['검토 목적을 알려주시겠습니까?']
            return response
    missing, _ = resume(api, issued['token'], identifier,
        tool(api, issued['token'], 'ra_start', {'job_id': identifier, 'operation': 'generate'}),
        model=MissingBrief())
    assert missing['result']['questions'] == ['검토 목적을 알려주시겠습니까?']
    endpoint = f'/api/jobs/{identifier}/answers'
    assert request(api, 'POST', endpoint, {'answers': {'unasked': 'invented answer'}}).status_code == 422
    answer_payload = {'answers': {'검토 목적을 알려주시겠습니까?': '신규 담당자 내부 검토를 위한 합성 시험'}}
    assert request(api, 'POST', endpoint, answer_payload, user='user-B').status_code == 404
    saved = request(api, 'POST', endpoint, answer_payload)
    assert saved.status_code == 200, saved.text
    assert not saved.json().get('result')
    assert request(api, 'POST', f'/api/jobs/{identifier}/export', {'confirmed': True}).status_code == 409
    pending = tool(api, issued['token'], 'ra_start', {'job_id': identifier, 'operation': 'generate'})
    assert pending['inference']['payload']['answers']['검토 목적을 알려주시겠습니까?'] == answer_payload['answers']['검토 목적을 알려주시겠습니까?']
    final, _ = resume(api, issued['token'], identifier, pending)
    assert final['result']['ready_for_output_check']
    assert final['user_answers']['검토 목적을 알려주시겠습니까?'] == answer_payload['answers']['검토 목적을 알려주시겠습니까?']
