"""In-memory RA work packages bound to current preparation and output proofs.

A package tracks registered fields and user-reviewed notices, not legal readiness,
AI accuracy or a human editing KPI. It never writes personal values to a cache.
"""
from copy import deepcopy
import csv
from hashlib import sha256
from io import BytesIO, StringIO
import json
from pathlib import PurePosixPath
import re
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from agent.brief import model_profile
from agent.field_citations import field_citations, profile_field, split_field_citations
from agent.ra_workflows import prepare_ra_workflow


STATES = {'pending', 'checked', 'not_applicable'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')


def _filename(name):
    _require(isinstance(name, str) and bool(name) and len(name) <= 180
             and '/' not in name and '\\' not in name and ':' not in name
             and name not in {'.', '..'} and not name.endswith((' ', '.'))
             and not any(ord(character) < 32 for character in name)
             and PurePosixPath(name).name == name
             and re.match(r'^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name, re.I) is None,
             '작업패키지 파일명에 경로나 예약 이름을 허용하지 않음')
    return name


def _attachment_rows(entry, checks):
    _require(isinstance(checks, dict), '첨부 확인 상태는 등록된 ID별 객체이어야 함')
    items = entry.get('attachments', [])
    _require(isinstance(items, list) and all(isinstance(item, dict) for item in items), '등록된 첨부 안내 목록이 잘못됨')
    items = [{**item, 'id': item['id'] if 'id' in item else str(entry['id']) + ':attachment:' + str(index + 1)}
             for index, item in enumerate(items)]
    ids = [item.get('id') for item in items]
    _require(all(isinstance(identifier, str) and identifier for identifier in ids) and len(set(ids)) == len(ids),
             '등록된 첨부 안내 ID가 비어 있거나 중복됨')
    _require(not set(checks) - set(ids), '등록되지 않은 첨부의 확인 상태를 추가할 수 없음')
    grouped = {}
    for item in items:
        _require(isinstance(item.get('source_quote'), str) and bool(item['source_quote'].strip())
                 and re.fullmatch(r'[0-9a-f]{64}', str(item.get('source_sha256', '')))
                 and type(item.get('page')) is int and item['page'] > 0,
                 '첨부 안내의 등록 원문·문서 SHA·페이지가 필요함')
        check = checks.get(item['id'], {'status': 'pending', 'confirmed_by_user': False})
        _require(isinstance(check, dict) and not set(check) - {'status', 'confirmed_by_user'}, '첨부 확인에 상태와 사용자 확인만 허용함')
        status, confirmed = check.get('status'), check.get('confirmed_by_user', False)
        _require(status in STATES and type(confirmed) is bool, '첨부 확인 상태가 잘못됨')
        _require(status == 'pending' or confirmed, '확인·해당 없음 상태는 실제 사용자 확인이 필요함')
        identity = (item['source_sha256'], item['page'], item['source_quote'])
        if identity in grouped:
            row = grouped[identity]
            _require((row['status'], row['confirmed_by_user']) == (status, confirmed), '같은 원문 첨부 안내의 사용자 확인이 상충함')
            row['registered_ids'].append(item['id'])
            continue
        grouped[identity] = {**deepcopy(item), 'registered_ids': [item['id']], 'status': status,
                             'confirmed_by_user': confirmed, 'regulatory_applicability_determined': False,
                             'version': {key: deepcopy(entry[key]) for key in
                                         ('law_effective_date', 'form_printed_revision_date', 'version_note', 'checked_at', 'checked_date') if key in entry}}
    return list(grouped.values())

def attachment_checklist(entry, attachment_checks=None):
    """Expose registered, deduplicated notices; missing IDs use workflow:attachment:N."""
    return _attachment_rows(entry, {} if attachment_checks is None else attachment_checks)


def _csv(rows):
    stream = StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['입력칸 ID', '등록 항목', '기입 상태', '입력 방식', '등록 필수', '출처 ID', '원문 위치'])
    # Prevent spreadsheet formula execution from labels, IDs or source filenames.
    for row in rows:
        cells = [row['field_id'], row['label'], row['status'], row['input_kind'], row['registered_required'],
                 ' | '.join(row['source_ids']), ' | '.join(row['source_locations'])]
        writer.writerow([("'" + str(cell)) if str(cell).lstrip().startswith(('=', '+', '-', '@'))
                         or str(cell).startswith(('\t', '\r', '\n')) else cell for cell in cells])
    return ('\ufeff' + stream.getvalue()).encode('utf-8')


def build_ra_workpack(entry, profile, prepared, export, *, selected_product=None,
                      attachment_checks=None, record_kind='production'):
    """Return {zip_bytes, filename, summary, attachments, sha256} without disk I/O.

    ``export`` is {document: bytes, filename: str, evidence: JSON bytes} from the
    verified UI exporter. checked/not_applicable notices require an explicit
    {status, confirmed_by_user: True}; omitted notices remain pending.
    ``record_kind='synthetic_test'`` labels fixture activity; neither kind is an
    automatic human KPI measurement or a regulatory applicability decision.
    """
    _require(record_kind in {'production', 'synthetic_test'}, '작업 기록 종류가 잘못됨')
    _require(isinstance(entry, dict) and isinstance(profile, dict) and isinstance(prepared, dict) and isinstance(export, dict),
             '현재 등록 양식·준비 결과·검수 출력을 전달해야 함')
    _require(prepared.get('mode') == 'verified_field_copy' and prepared.get('ready_for_output_check') is True
             and prepared.get('review', {}).get('blocking') is False and prepared.get('actual_model_requests') == 0,
             '준비 검수 오류를 해결하기 전 작업패키지를 만들 수 없음')
    cleaned = model_profile(profile)
    _require(cleaned == prepared.get('template_profile') and cleaned.get('source_sha256') == entry.get('source_sha256'),
             '현재 양식과 준비 검수의 원본·매핑이 다름')
    original_sources = [source for source in prepared['sources'] if source.get('filename') != '사용자 입력']
    bindings = {key: {part: item[part] for part in ('source_id', 'quote', 'start')} for key, item in prepared.get('evidence', {}).items()}
    direct = {key: item['value'] for key, item in prepared.get('locked_fields', {}).items()}
    current = prepare_ra_workflow(profile, original_sources, source_bindings=bindings, direct_values=direct)
    for key in ('fingerprint', 'draft', 'sources', 'locked_fields', 'evidence', 'template_profile', 'review',
                'missing_fields', 'questions', 'status', 'ready_for_output_check'):
        _require(current.get(key) == prepared.get(key), '현재 입력·근거와 준비 검수 결과가 다름')
    _require(current['ready_for_output_check'], '현재 입력값의 재검수가 실패함')
    document, evidence = export.get('document'), export.get('evidence')
    _require(isinstance(document, bytes) and 0 < len(document) <= 64 * 1024 * 1024
             and isinstance(evidence, bytes) and 0 < len(evidence) <= 64 * 1024 * 1024, '검수된 문서·근거 JSON 바이트가 필요함')
    name = _filename(export.get('filename'))
    _require(PurePosixPath(name).suffix.lstrip('.').lower() == cleaned.get('format'), '출력 문서 형식이 등록된 양식과 다름')
    try:
        sidecar = json.loads(evidence.decode('utf-8'))
    except (ValueError, UnicodeError):
        raise ValueError('검수된 근거 JSON을 읽을 수 없음') from None
    _require(isinstance(sidecar, dict), '근거 JSON이 객체이어야 함')
    _require(sidecar.get('workflow_id') == entry.get('id'), '출력 근거 기록과 선택 업무가 다름')
    for key in ('fingerprint', 'draft', 'sources', 'locked_fields', 'evidence', 'template_profile',
                'mode', 'actual_model_requests', 'submission_ready'):
        _require(sidecar.get(key) == prepared.get(key), '출력 근거 기록과 현재 준비 결과가 다름')
    proof = sidecar.get('output_verification', {})
    _require(proof.get('status') == 'passed' and proof.get('format') == cleaned.get('format')
             and proof.get('sha', {}).get('template') == entry['source_sha256']
             and proof.get('sha', {}).get('output') == sha256(document).hexdigest(), '현재 문서 바이트와 독립 출력 검수 SHA가 다름')
    product = {'product_name': cleaned.get('ra_product_name', ''), 'product_variant': cleaned.get('ra_product_variant', '')}
    if selected_product is not None:
        if isinstance(selected_product, str):
            selected_product = {'product_name': selected_product, 'product_variant': product['product_variant']}
        elif isinstance(selected_product, dict) and set(selected_product) == {'name', 'variant'}:
            selected_product = {'product_name': selected_product['name'], 'product_variant': selected_product['variant']}
        _require(selected_product == product, '선택 제품과 검수한 제품 범위가 다름')
    attachments = attachment_checklist(entry, attachment_checks)
    fields = cleaned['fields']; source_map = {source['source_id']: source for source in prepared['sources']}
    _require(len(fields) == len({field['id'] for field in fields}) and len(fields) == len({field['value_key'] for field in fields}),
             '등록 입력칸 ID·값 이름이 중복됨')
    if 'field_count' in entry:
        _require(entry['field_count'] == len(fields), '등록 입력칸 분모가 현재 양식과 다름')
    rows = []
    for field in fields:
        key = field['value_key']; value = prepared['draft'].get(key, '')
        plain, _ = split_field_citations(value, field, literal=direct.get(key))
        ids = list(dict.fromkeys(field_citations(value, field, literal=direct.get(key))))
        _require(all(identifier in source_map for identifier in ids), '기입 항목에 등록되지 않은 출처가 있음')
        locations = []
        for identifier in ids:
            source = source_map[identifier]
            location = source.get('page') or source.get('sheet') or source.get('location')
            locations.append(f"{source['filename']} / {location}")
        rows.append({'field_id': field['id'], 'value_key': key, 'label': field['label'],
                     'status': 'filled' if plain.strip() else 'unfilled',
                     'input_kind': 'direct' if field.get('input_required') else 'source',
                     'registered_required': bool(field.get('required', True)),
                     'source_ids': ids, 'source_locations': locations})
    count = lambda **criteria: sum(all(row.get(key) == value for key, value in criteria.items()) for row in rows)
    summary = {'schema_version': 1, 'workflow_id': entry['id'], 'title': entry.get('title'), 'selected_product': product,
               'record_kind': record_kind, 'mode': prepared['mode'], 'scope': '현재 등록 입력칸만 집계함; 양식 전체·법정 제출 필수 항목의 분모가 아님',
               'registered_fields': len(rows), 'filled_fields': count(status='filled'), 'unfilled_fields': count(status='unfilled'),
               'direct_fields': count(input_kind='direct'), 'filled_direct_fields': count(input_kind='direct', status='filled'),
               'source_fields': count(input_kind='source'), 'filled_source_fields': count(input_kind='source', status='filled'),
               'registered_required_unfilled': [row['field_id'] for row in rows if row['registered_required'] and row['status'] == 'unfilled'],
               'missing_fields': deepcopy(prepared.get('missing_fields', [])), 'unresolved_issues': deepcopy(prepared['review']['issues']),
               'unregistered_field_status': 'not_evaluated', 'rows': rows,
               'attachments_registered': len(entry.get('attachments', [])), 'attachments_unique': len(attachments),
               'attachments_pending': sum(row['status'] == 'pending' for row in attachments),
               'source_sha256': entry['source_sha256'], 'profile_sha256': entry.get('profile_sha256'),
               'document_sha256': sha256(document).hexdigest(), 'evidence_sha256': sha256(evidence).hexdigest(),
               'preparation_fingerprint': prepared['fingerprint'], 'submission_ready': False,
               'legal_compliance_certified': False, 'actual_model_requests': prepared.get('actual_model_requests', 0),
               'human_kpi_measured': False, 'user_edit_ratio': None}
    members = {'document/' + name: document, 'evidence.json': evidence,
               'work-status.json': _json(summary), 'work-status.csv': _csv(rows), 'attachments.json': _json(attachments)}
    members['manifest.json'] = _json({'members': {key: {'sha256': sha256(value).hexdigest(), 'bytes': len(value)}
                                                     for key, value in members.items()}, 'scope': summary['scope']})
    stream = BytesIO()
    with ZipFile(stream, 'w', compression=ZIP_DEFLATED) as archive:
        for key, value in members.items():
            info = ZipInfo(key, date_time=(1980, 1, 1, 0, 0, 0));info.compress_type = ZIP_DEFLATED
            archive.writestr(info, value)
    payload = stream.getvalue()
    package_name = _filename(entry['id'] + '_RA_workpack.zip')
    return {'zip_bytes': payload, 'filename': package_name, 'summary': summary,
            'attachments': attachments, 'sha256': sha256(payload).hexdigest()}
