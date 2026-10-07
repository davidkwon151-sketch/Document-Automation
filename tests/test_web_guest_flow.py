"""No-signup access still protects actual documents and source confirmation."""
from base64 import b64encode
from io import BytesIO
import time
from unittest.mock import Mock
from PIL import Image

from fastapi.testclient import TestClient
import pytest

from app.guest_access import GuestAccess
from app import web_api
from test_web_api import SECRET, request, configured, copied, upload_data


@pytest.fixture
def access(tmp_path):
    root = tmp_path / 'private'
    return root, GuestAccess(root), TestClient(web_api.create_app(root=root, secret=SECRET),
                                             raise_server_exceptions=False)


def enter(guests, api, label):
    invite = guests.issue(label)
    response = request(api, 'POST', '/api/access/redeem', {'token': invite['token']}, user='invite:redeem')
    assert response.status_code == 200, response.text
    return invite, 'guest:' + response.json()['session']


def test_guest_actual_file_completion_and_cross_invite_isolation(access):
    _, guests, api = access
    invite_a, user_a = enter(guests, api, '회사 A 담당자')
    _, user_b = enter(guests, api, '회사 B 담당자')
    me = request(api, 'GET', '/api/me', user=user_a).json()
    assert me['label'] == '회사 A 담당자' and me['access_mode'] == 'guest'
    assert 'session' not in me and 'user_id' not in me
    identifier, _ = configured(api, user=user_a)
    result, _ = copied(api, identifier, user=user_a)
    assert result['result']['target_coverage']['filled_count'] == 5
    assert request(api, 'POST', f'/api/jobs/{identifier}/export', {'confirmed': True}, user=user_a).status_code == 200
    assert [item['job_id'] for item in request(api, 'GET', '/api/jobs', user=user_a).json()['jobs']] == [identifier]
    assert request(api, 'GET', '/api/jobs', user=user_b).json()['jobs'] == []
    for method, suffix, body in [('GET', '', None), ('POST', '/configure', {}),
                                ('POST', '/export', {'confirmed': True}), ('DELETE', '', None)]:
        assert request(api, method, '/api/jobs/' + identifier + suffix, body, user=user_b).status_code == 404
    guests.revoke(invite_a['invite_id'])
    assert request(api, 'POST', f'/api/jobs/{identifier}/export', {'confirmed': True}, user=user_a).status_code == 401


def test_reserved_identity_cannot_skip_redemption_or_claim_guest_id(access):
    _, guests, api = access
    invite, user = enter(guests, api, 'QA')
    for forged in ['invite:redeem', 'invite:other', 'guest:' + invite['invite_id'], 'guest:' + 'x' * 43]:
        assert request(api, 'GET', '/api/health', user=forged).status_code == 401
    assert request(api, 'POST', '/api/access/redeem', {'token': invite['token']}, user='user-A').status_code == 403
    assert request(api, 'POST', '/api/access/redeem', {'token': invite['token']}, user='invite:redeem').status_code == 401
    assert request(api, 'POST', '/api/access/logout', {}, user=user).status_code == 200
    assert request(api, 'GET', '/api/me', user=user).status_code == 401


def test_guest_session_survives_server_restart_without_exposing_other_jobs(access):
    root, guests, api = access
    _, user = enter(guests, api, 'QA')
    identifier, _ = configured(api, user=user)
    restarted = TestClient(web_api.create_app(root=root, secret=SECRET), raise_server_exceptions=False)
    assert request(restarted, 'GET', '/api/jobs/' + identifier, user=user).status_code == 200
    assert request(restarted, 'GET', '/api/jobs/' + identifier, user='user-A').status_code == 404


def await_idle(api, identifier):
    for _ in range(100):
        response = request(api, 'GET', '/api/jobs/' + identifier)
        state = response.json()
        if state['status'] != 'processing':
            return state
        time.sleep(.01)
    pytest.fail('bounded OCR job did not finish')


def test_async_ocr_invalidates_previous_output_and_requires_new_confirmation(tmp_path):
    root = tmp_path / 'private'
    client = Mock()
    client.read_image_json.return_value = {'본문': '제품명: SyntheticDrugA\n제형·함량: 정제 10 mg\n'
        '성상: 흰색 정제\n저장방법: 실온 보관\n포장단위: 30정', '표 목록': [], '불확실한 항목': []}
    api = TestClient(web_api.create_app(root=root, secret=SECRET, client_factory=lambda: client),
                     raise_server_exceptions=False)
    image = BytesIO()
    Image.new('RGB', (100, 100), 'white').save(image, format='PNG')
    payload = upload_data()
    payload['sources'] = [{'name': 'Synthetic_scan.png', 'base64': b64encode(image.getvalue()).decode()}]
    view = request(api, 'POST', '/api/jobs', payload).json()
    identifier = view['job_id']
    assert view['intake']['files'][0]['status'] == 'deferred'
    assert request(api, 'POST', '/api/jobs/' + identifier + '/intake', {'auto_ocr': True}).status_code == 202
    state = await_idle(api, identifier)
    assert state['intake']['files'][0]['status'] == 'needs_confirmation'
    receipts = [{'source_id': source['source_id'], 'fingerprint': source['verification_fingerprint']}
                for source in state['intake']['sources']]
    assert request(api, 'POST', '/api/jobs/' + identifier + '/intake', {'receipts': receipts, 'confirmed': True}).status_code == 200
    rows = state['mapping_rows']
    for row, field in zip(rows, state['profile']['fields']):
        row['채울 값'] = field['value_key']
        if row['항목'] == '신청인':
            row['직접 입력'] = True
    config = {'confirmed': True, 'rows': rows, 'selected_keys': [row['채울 값'] for row in rows],
        'instruction': '합성 제품 신청서 작성', 'product_name': 'SyntheticDrugA', 'variant': '정제 10 mg',
        'field_values': {'신청인': '담당자 직접 시험 입력'}}
    assert request(api, 'POST', '/api/jobs/' + identifier + '/configure', config).status_code == 200
    copied(api, identifier)
    response = request(api, 'POST', '/api/jobs/' + identifier + '/intake', {'auto_ocr': True})
    assert response.status_code == 202
    state = await_idle(api, identifier)
    assert client.read_image_json.call_count == 2
    assert state['operation'] == 'intake_ocr' and 'result' not in state
    assert request(api, 'POST', '/api/jobs/' + identifier + '/proposals').status_code == 409
    assert request(api, 'POST', '/api/jobs/' + identifier + '/export', {'confirmed': True}).status_code == 409


def test_ocr_failure_is_safe_and_never_restores_old_ready_output(tmp_path, monkeypatch):
    from llm.client import LLMError
    api = TestClient(web_api.create_app(root=tmp_path, secret=SECRET, client_factory=lambda: object()),
                     raise_server_exceptions=False)
    identifier, _ = configured(api)
    copied(api, identifier)
    def failure(*args, **kwargs):
        raise LLMError('supplier secret must not escape', kind='quota')
    monkeypatch.setattr(web_api, 'collect_multimodal', failure)
    assert request(api, 'POST', '/api/jobs/' + identifier + '/intake', {'auto_ocr': True}).status_code == 202
    state = await_idle(api, identifier)
    assert state['error']['code'] == 'llm_quota' and 'result' not in state
    assert 'supplier secret' not in str(state)


@pytest.mark.parametrize('payload', [{'auto_ocr': False}, {'auto_ocr': 1},
                                   {'auto_ocr': True, 'receipts': []}])
def test_ocr_is_explicit_not_mixed_with_confirmation(access, payload):
    _, _, api = access
    identifier, _ = configured(api)
    assert request(api, 'POST', '/api/jobs/' + identifier + '/intake', payload).status_code == 422
