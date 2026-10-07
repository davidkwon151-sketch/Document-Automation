"""Protected external DMF to CTD 2.3.S review workflow."""

from base64 import b64encode
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from fastapi.testclient import TestClient

from app import web_api
from test_web_api import SECRET, request


SAMPLES = Path(__file__).resolve().parents[1] / 'samples'
SOURCE = SAMPLES / 'qos_dmf_synthetic.txt'
TEMPLATE = SAMPLES / 'qos_dmf_demo_template.docx'
PRODUCT = '시험정 5 mg'


def _upload(path):
    return {'name': path.name, 'base64': b64encode(path.read_bytes()).decode()}


def _inputs():
    return {'product_name': PRODUCT, 'product_variant': '',
            'selected_sections': [f'2.3.S.{number}' for number in range(1, 8)],
            'dmf_links': {sha256(SOURCE.read_bytes()).hexdigest(): {
                'substance_name': '시험원료 A',
                'manufacturer_name': 'Example API Manufacturing Ltd.',
                'product_name': PRODUCT, 'confirmed': True}}}


def test_web_qos_dmf_options_prepare_and_checked_export(tmp_path):
    api = TestClient(web_api.create_app(root=tmp_path / 'private', secret=SECRET),
                     raise_server_exceptions=False)
    assert len(request(api, 'GET', '/api/qos/sections').json()['sections']) == 7
    created = request(api, 'POST', '/api/jobs', {
        'template': _upload(TEMPLATE), 'sources': [_upload(SOURCE)]})
    assert created.status_code == 200, created.text
    identifier = created.json()['job_id']
    path = f'/api/jobs/{identifier}/qos'
    assert request(api, 'GET', path + '/options', user='user-B').status_code == 404
    options = request(api, 'GET', path + '/options')
    assert options.status_code == 200
    candidate = options.json()['options'][0]
    assert candidate['manufacturer_names'][0]['value'] == 'Example API Manufacturing Ltd.'
    assert candidate['manufacturer_names'][0]['evidence'][0]['document_sha256'] == next(iter(_inputs()['dmf_links']))
    assert request(api, 'POST', path + '/prepare', _inputs(), user='user-B').status_code == 404
    prepared = request(api, 'POST', path + '/prepare', _inputs())
    assert prepared.status_code == 200, prepared.text
    package = prepared.json()['qos']['package']
    assert package['coverage']['needs_manual_check'] == 7
    assert package['summary_method'] == 'exact_source_excerpts'
    assert request(api, 'POST', path + '/export', {
        'confirmed': True, 'fingerprint': package['fingerprint']}, user='user-B').status_code == 404
    assert request(api, 'POST', path + '/export', {
        'confirmed': True, 'fingerprint': '0' * 64}).status_code == 409
    exported = request(api, 'POST', path + '/export', {
        'confirmed': True, 'fingerprint': package['fingerprint']})
    assert exported.status_code == 200, exported.text
    with ZipFile(BytesIO(exported.content)) as archive:
        document = archive.read('CTD_2.3.S_DMF_검토초안.docx')
        rendered = Document(BytesIO(document))
        content = '\n'.join(cell.text for table in rendered.tables
                            for row in table.rows for cell in row.cells)
        assert '99.4%' in content and '{{2.3.S.4}}' not in content
        sidecar = json.loads(archive.read('CTD_2.3.S_출처와_누락.json'))
        assert sidecar['document_kind'] == 'ctd_module_2_3_s_dmf_working_draft'
        assert sidecar['output_check']['status'] == 'passed'
        assert sidecar['evidence']['2.3.S.4'][0]['document_sha256'] == next(iter(_inputs()['dmf_links']))
        assert sidecar['submission_ready'] is False
    # Original bytes are checked anew when a download is requested.
    original = tmp_path / 'private' / sha256(b'user-A').hexdigest() / identifier / 'sources' / SOURCE.name
    original.write_text('tampered', encoding='utf-8')
    assert request(api, 'POST', path + '/export', {
        'confirmed': True, 'fingerprint': package['fingerprint']}).status_code == 409


def test_web_qos_refuses_unconfirmed_product_relation(tmp_path):
    api = TestClient(web_api.create_app(root=tmp_path / 'private', secret=SECRET),
                     raise_server_exceptions=False)
    identifier = request(api, 'POST', '/api/jobs', {
        'template': _upload(TEMPLATE), 'sources': [_upload(SOURCE)]}).json()['job_id']
    payload = _inputs()
    next(iter(payload['dmf_links'].values()))['confirmed'] = False
    response = request(api, 'POST', f'/api/jobs/{identifier}/qos/prepare', payload)
    assert response.status_code == 422
    assert 'qos' not in request(api, 'GET', f'/api/jobs/{identifier}').json()
