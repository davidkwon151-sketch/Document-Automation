"""Protected buyer-email workflow with actual parser and mock model calls."""
from base64 import b64encode
from hashlib import sha256
import json
import time
from uuid import uuid4
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app import web_api


SECRET = 'sales-test-gateway-secret-' + 'x' * 32


def call(api, method, path, payload=None, *, user='seller-A'):
    body = b'' if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    timestamp, nonce = str(int(time.time())), uuid4().hex
    headers = {'X-RA-User': user, 'X-RA-Time': timestamp, 'X-RA-Nonce': nonce,
               'X-RA-Signature': web_api.signature(SECRET, method, path, timestamp, nonce, user, body)}
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    return api.request(method, path, headers=headers, content=body)


def upload():
    return {'email_text': 'Please send a quote for Widget A.',
            'sources': [{'name': 'seller.txt',
                         'base64': b64encode(b'Product: Widget A\nUnit price: USD 12.50').decode()}]}


def test_sales_job_isolated_drafted_and_key_not_persisted(tmp_path):
    class Model:
        def generate_json(self, name, payload):
            if name == 'buyer_email':
                source = next(item for item in payload['sources'] if 'USD 12.50' in item['text'])
                return {'requests': [{'buyer_quote': 'Please send a quote for Widget A.',
                    'answer': 'The unit price is USD 12.50.',
                    'evidence': [{'source_id': source['source_id'], 'quote': 'Unit price: USD 12.50'}]}]}
            assert name == 'buyer_email_review'
            return {'complete': True, 'items': [{'index': 1, 'supported': True}]}
    root = tmp_path / 'private'
    api = TestClient(web_api.create_app(root=root, secret=SECRET, client_factory=Model),
                     raise_server_exceptions=False)
    response = call(api, 'POST', '/api/sales/jobs', upload())
    assert response.status_code == 200, response.text
    view = response.json()
    identifier = view['job_id']
    assert view['email']['body'] == 'Please send a quote for Widget A.'
    assert view['requested_documents'] == ['proforma_invoice']
    assert call(api, 'GET', f'/api/sales/jobs/{identifier}', user='seller-B').status_code == 404
    assert call(api, 'POST', f'/api/sales/jobs/{identifier}/draft', {'language': 'en'}, user='seller-B').status_code == 404
    response = call(api, 'POST', f'/api/sales/jobs/{identifier}/draft', {'language': 'en'})
    assert response.status_code == 202, response.text
    for _ in range(100):
        view = call(api, 'GET', f'/api/sales/jobs/{identifier}').json()
        if view['status'] != 'processing':
            break
        time.sleep(.02)
    assert view['status'] == 'review_required', view
    assert 'USD 12.50' in view['result']['email']
    assert view['result']['requests'][0]['evidence'][0]['document_sha256'] == sha256(b'Product: Widget A\nUnit price: USD 12.50').hexdigest()
    assert view['result']['documents'] == ['proforma_invoice']
    saved = (root / sha256(b'seller-A').hexdigest() / 'sales' / identifier / 'state.json').read_text(encoding='utf-8')
    assert 'gemini_api_key' not in saved
    assert call(api, 'DELETE', f'/api/sales/jobs/{identifier}').status_code == 200
    assert call(api, 'GET', f'/api/sales/jobs/{identifier}').status_code == 404


def test_email_only_and_followup_text_or_file_redraft_in_same_job(tmp_path):
    class Model:
        def generate_json(self, name, payload):
            if name == 'buyer_email_review':
                return {'complete': False, 'missing_requests': [],
                        'items': [{'index': 1, 'supported': True}]}
            sources = payload['sources']
            bound = next((source for source in sources if 'USD 12.50' in source['text']), None)
            if bound:
                quote = 'Unit price: USD 12.50' if bound.get('origin') != 'user_input' else 'USD 12.50'
                return {'requests': [{'buyer_quote': payload['email']['body'],
                    'answer': 'The unit price is USD 12.50.',
                    'evidence': [{'source_id': bound['source_id'], 'quote': quote}]}]}
            return {'requests': [{'buyer_quote': payload['email']['body'],
                'answer': 'Could you share the target quantity?', 'evidence': []}]}
    api = TestClient(web_api.create_app(root=tmp_path / 'private', secret=SECRET,
                     client_factory=Model), raise_server_exceptions=False)
    response = call(api, 'POST', '/api/sales/jobs',
                    {'email_text': 'What is the price for Widget A?', 'sources': []})
    assert response.status_code == 200, response.text
    identifier = response.json()['job_id']
    route = f'/api/sales/jobs/{identifier}'

    def draft():
        assert call(api, 'POST', route + '/draft', {'language': 'en'}).status_code == 202
        for _ in range(100):
            view = call(api, 'GET', route).json()
            if view['status'] != 'processing':
                return view
            time.sleep(.02)
        raise AssertionError('draft did not finish')

    first = draft()
    assert first['result']['requests'][0]['status'] == 'general'
    assert 'target quantity' in first['result']['email']
    assert first['result']['review']['coverage_check_required']
    assert call(api, 'POST', route + '/trade/propose',
                {'workflow': 'proforma_invoice', 'transaction': 'TEST-001'}).status_code == 409

    assert call(api, 'POST', route + '/supplement', {'text': 'Unit price: USD 12.50'}).status_code == 200
    assert call(api, 'GET', route, user='seller-B').status_code == 404
    assert call(api, 'GET', route).json()['result'] is None
    second = draft()
    assert 'USD 12.50' in second['result']['email']
    assert second['result']['requests'][0]['evidence'][0]['origin'] == 'user_input'

    file_bytes = b'Product: Widget A\nUnit price: USD 12.50'
    response = call(api, 'POST', route + '/supplement', {'sources': [
        {'name': 'terms.txt', 'base64': b64encode(file_bytes).decode()}]})
    assert response.status_code == 200, response.text
    assert response.json()['intake']['files'][0]['filename'] == 'terms.txt'
    assert response.json()['result'] is None
    assert len(draft()['result']['email']) > 0


def test_sales_scan_cannot_draft_before_original_confirmation(tmp_path):
    from PIL import Image
    from io import BytesIO
    stream = BytesIO(); Image.new('RGB', (24, 24), 'white').save(stream, format='PNG')
    api = TestClient(web_api.create_app(root=tmp_path / 'private', secret=SECRET,
                     client_factory=lambda: None), raise_server_exceptions=False)
    data = {'email_text': 'Can you send the specification?',
            'sources': [{'name': 'scan.png', 'base64': b64encode(stream.getvalue()).decode()}]}
    response = call(api, 'POST', '/api/sales/jobs', data)
    assert response.status_code == 200, response.text
    identifier = response.json()['job_id']
    assert response.json()['intake']['files'][0]['status'] == 'deferred'
    response = call(api, 'POST', f'/api/sales/jobs/{identifier}/draft', {})
    assert response.status_code == 409
    digest = sha256(stream.getvalue()).hexdigest()
    response = call(api, 'POST', f'/api/sales/jobs/{identifier}/intake',
                    {'transcriptions': {digest: [{'page': 1, 'text': 'Specification: Widget A'}]}})
    assert response.status_code == 200, response.text
    source = response.json()['intake']['sources'][0]
    assert source['requires_verification']
    assert call(api, 'POST', f'/api/sales/jobs/{identifier}/draft', {}).status_code == 409
    response = call(api, 'POST', f'/api/sales/jobs/{identifier}/intake',
                    {'receipts': [{'source_id': source['source_id'],
                                   'fingerprint': source['verification_fingerprint']}], 'confirmed': True})
    assert response.status_code == 200, response.text
    assert response.json()['intake']['confirmations']


def test_external_sales_quote_proposal_confirm_fill_and_independent_export(tmp_path):
    values = {'Order No.': 'TEST-001', 'Document Date': '2026-10-07',
              'Seller': 'Synthetic Seller Co', 'Buyer': 'Synthetic Buyer Ltd', 'Destination': 'Japan',
              'Currency': 'USD', 'Incoterms': 'FOB', 'Named Place': 'Busan',
              'Payment Terms': 'Advance payment', 'Description[1]': 'Widget A',
              'Quantity[1]': '3', 'Quantity Unit[1]': 'PCS', 'Unit Price[1]': '12.50',
              'Amount[1]': '37.50', 'Total Amount': '37.50'}
    text = '\n'.join(f'{key}: {value}' for key, value in values.items()).encode()
    api = TestClient(web_api.create_app(root=tmp_path / 'private', secret=SECRET),
                     raise_server_exceptions=False)
    payload = {'email_text': 'Please send a quotation for order TEST-001.',
               'sources': [{'name': 'transaction.txt', 'base64': b64encode(text).decode()}]}
    response = call(api, 'POST', '/api/sales/jobs', payload)
    assert response.status_code == 200, response.text
    identifier = response.json()['job_id']
    prefix = f'/api/sales/jobs/{identifier}/trade'
    response = call(api, 'POST', prefix + '/propose',
                    {'workflow': 'proforma_invoice', 'transaction': 'TEST-001', 'rows': 1})
    assert response.status_code == 200, response.text
    trade = response.json()['trade']
    assert trade['proposal']['source_bindings']['Unit Price[1]']['quote'] == '12.50'
    response = call(api, 'POST', prefix + '/prepare', {'fingerprint': trade['fingerprint'],
        'source_bindings': trade['proposal']['source_bindings'], 'direct_values': {}, 'confirmed': True})
    assert response.status_code == 200, response.text
    prepared = response.json()['trade']['prepared']
    assert prepared['ready_for_output_check'], prepared['review']
    assert prepared['plain_values']['Total Amount'] == '37.50'
    response = call(api, 'POST', prefix + '/export',
                    {'fingerprint': prepared['fingerprint'], 'confirmed': True})
    assert response.status_code == 200, response.text[:300]
    with ZipFile(BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {'proforma_invoice.docx', 'trade_evidence.json'}
        evidence = json.loads(archive.read('trade_evidence.json'))
        assert evidence['output_verification']['status'] == 'passed'
        assert not evidence['submission_ready']
