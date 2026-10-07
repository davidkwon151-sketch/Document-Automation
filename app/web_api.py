"""Authenticated Site gateway for the existing RA writer (no public local CRUD)."""
from base64 import b64decode, b64encode
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import hmac
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from uuid import uuid4
from zipfile import ZipFile, BadZipFile, ZIP_DEFLATED

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
import httpx

from agent.multimodal_intake import (collect_multimodal, append_multimodal_intake, confirm_intake,
                                     generation_sources, SUPPORTED_SUFFIXES)
from agent.buyer_email import parse_buyer_email, draft_buyer_reply, requested_documents
from agent.global_workflows import (blank_global_input, create_global_template,
                                    export_global_workflow, prepare_global_workflow, propose_global_bindings)
from agent.ctd import CTD_SECTIONS, prepare_ctd_package
from agent.ctd_qos import QOS_S_SECTIONS, prepare_qos_package, propose_dmf_options
from agent.ctd_template import fill_ctd_working_template
from app.form_config import configure_profile, mapping_rows
from app import ra_auto_service as writer
from llm.client import LLMClient, LLMError, MCPInferenceClient, PendingInference
from parsers.extract import ParseError
from templates.compatibility import analyze_template
from templates.profiles import load_form_profile
from app.guest_access import GuestAccess, GuestAccessError
from app.mcp_access import MCPAccess, MCPAccessError
from app.mcp_api import install_mcp

MAX_BODY = 16 * 1024 * 1024
MAX_FILE = 10 * 1024 * 1024
MAX_TENANT_BYTES = 256 * 1024 * 1024
MAX_STORAGE_BYTES = 4 * 1024 * 1024 * 1024
MAX_STATE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_CHARS = 2 * 1024 * 1024
MAX_BLOCK_CHARS = 20_000
ID = re.compile(r'^[0-9a-f]{32}$')
SAFE_NAME = re.compile(r'^[^/\\:\x00-\x1f]{1,120}$')


def signature(secret, method, path, timestamp, nonce, user, body):
    canonical = '\n'.join([method, path, timestamp, nonce, user,
                           sha256(body).hexdigest()])
    return hmac.new(secret.encode(), canonical.encode(), 'sha256').hexdigest()


class Gateway:
    """Verify the whole request before parsing, including timestamp and replay ID."""
    def __init__(self, app, secret, nonce_db, guests, mcp_access):
        self.app, self.secret, self.nonce_db, self.guests = app, secret, nonce_db, guests
        self.mcp_access = mcp_access
        self.rates, self.lock = {}, threading.Lock()
        with sqlite3.connect(nonce_db) as database:
            database.execute('CREATE TABLE IF NOT EXISTS nonces (nonce TEXT PRIMARY KEY, expiry REAL NOT NULL)')

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        async def reject(status, code):
            await JSONResponse({'error': code}, status_code=status)(scope, receive, send)
        body = bytearray()
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                return
            body.extend(event.get('body', b''))
            if len(body) > MAX_BODY:
                return await reject(413, 'upload_too_large')
            if not event.get('more_body'):
                break
        headers = {key.decode().lower(): value.decode() for key, value in scope['headers']}
        user, stamp, nonce, supplied = [headers.get('x-ra-' + key, '')
                                       for key in ('user', 'time', 'nonce', 'signature')]
        now = time.time()
        if (not re.fullmatch(r'[A-Za-z0-9_.:@-]{1,200}', user)
                or not re.fullmatch(r'[0-9]{10}', stamp) or abs(now - int(stamp)) > 120
                or not ID.fullmatch(nonce) or not ID.fullmatch(supplied[:32])
                or not re.fullmatch(r'[0-9a-f]{64}', supplied)):
            return await reject(401, 'authentication_required')
        path = scope.get('raw_path', scope['path'].encode()).decode()
        if scope.get('query_string'):
            path += '?' + scope['query_string'].decode()
        expected = signature(self.secret, scope['method'], path, stamp, nonce, user, bytes(body))
        if not hmac.compare_digest(supplied, expected):
            return await reject(401, 'authentication_required')
        guest, connector = None, None
        if user == 'invite:redeem':
            if scope['method'] != 'POST' or path != '/api/access/redeem':
                return await reject(401, 'authentication_required')
        elif user.startswith('guest:'):
            try:
                guest = self.guests.resolve(user[6:])
            except GuestAccessError:
                return await reject(401, 'invitation_expired')
        elif user.startswith('mcp:'):
            if path != '/mcp':
                return await reject(401, 'authentication_required')
            try:
                connector = self.mcp_access.resolve(user[4:])
            except MCPAccessError:
                return await reject(401, 'connector_expired')
        elif user.startswith('invite:'):
            return await reject(401, 'authentication_required')
        if path == '/mcp' and not connector:
            return await reject(401, 'connector_authentication_required')
        with self.lock:
            with sqlite3.connect(self.nonce_db) as database:
                database.execute('DELETE FROM nonces WHERE expiry < ?', (now,))
                if database.execute('SELECT count(*) FROM nonces').fetchone()[0] >= 20_000:
                    replay = True
                else:
                    try:
                        database.execute('INSERT INTO nonces VALUES (?, ?)', (nonce, now + 241))
                        replay = False
                    except sqlite3.IntegrityError:
                        replay = True
        if replay:
            return await reject(401, 'replayed_request')
        with self.lock:
            self.rates = {key: [stamp for stamp in stamps if stamp >= now - 60]
                          for key, stamps in self.rates.items() if stamps and stamps[-1] >= now - 60}
            requests = self.rates.setdefault(user, [])
            throttled = len(requests) >= 60
            if not throttled:
                requests.append(now)
        if throttled:
            return await reject(429, 'request_limit')
        scope.setdefault('state', {})['user'] = connector['user_id'] if connector else guest['user_id'] if guest else user
        scope['state']['mcp'] = connector
        scope['state']['guest'] = guest
        scope['state']['guest_session'] = user[6:] if guest else None
        delivered = False
        async def buffered():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()
        async def protected_send(message):
            if message['type'] == 'http.response.start':
                message['headers'] += [(b'cache-control', b'no-store'),
                                       (b'x-content-type-options', b'nosniff')]
            await send(message)
        await self.app(scope, buffered, protected_send)


def _clean(value, root):
    """Public views/evidence preserve provenance, but never reveal host paths."""
    if isinstance(value, dict):
        literal = {'draft', 'field_values', 'locked_fields', 'quote', 'text', 'full_text',
                   'context_text', 'source_context', 'value_key', 'label', 'instruction', 'answers', 'user_answers',
                   'sources', 'source_records', 'source_bindings'}
        private_paths = {'template_path', 'path', 'filepath', 'source_path', 'output_path',
                         'local_path', 'source_paths', 'original_path', 'directory'}
        result = {}
        for key, item in value.items():
            if key == '_native_preview_bytes':
                continue
            if key in literal:
                # These are original facts/field identifiers, never host metadata.
                result[key] = deepcopy(item)
            elif key in private_paths and (isinstance(item, list) or isinstance(item, str)
                    and (item.startswith(str(root)) or re.match(r'^[A-Za-z]:[\\/]|^/', item))):
                continue
            else:
                result[key] = _clean(item, root)
        return result
    if isinstance(value, list):
        return [_clean(item, root) for item in value]
    if isinstance(value, str):
        value = value.replace(str(root), '[서버 저장소]')
        return re.sub(r'(?i)[A-Z]:[\\/][^\n]*', '[서버 경로]', value)
    return value


def _storage_size(directory):
    total = 0
    for path in directory.rglob('*'):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            # Atomic JSON temporaries / SQLite journals can disappear between stat calls.
            continue
    return total


def _upload(item, allowed):
    if not isinstance(item, dict) or set(item) != {'name', 'base64'}:
        raise ValueError('upload')
    name, encoded = item['name'], item['base64']
    if (not isinstance(name, str) or not SAFE_NAME.fullmatch(name)
            or name in {'.', '..'} or name.endswith(('.', ' '))
            or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name, re.I)
            or Path(name).suffix.lower() not in allowed or not isinstance(encoded, str)
            or len(encoded) > MAX_FILE * 4 // 3 + 8):
        raise ValueError('upload')
    try:
        data = b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError('upload') from exc
    if not 0 < len(data) <= MAX_FILE:
        raise ValueError('upload')
    if Path(name).suffix.lower() in {'.docx', '.hwpx', '.xlsx', '.pptx'}:
        try:
            with ZipFile(BytesIO(data)) as archive:
                infos = archive.infolist()
                if (len(infos) > 2000 or sum(i.file_size for i in infos) > 64 * 1024 * 1024
                        or any(i.flag_bits & 1 or i.file_size > max(i.compress_size, 1) * 500
                               or i.filename.startswith(('/', '\\')) or '..' in Path(i.filename.replace('\\', '/')).parts
                               for i in infos)):
                    raise ValueError('archive')
                if archive.testzip() is not None:
                    raise ValueError('archive')
        except BadZipFile as exc:
            raise ValueError('archive') from exc
    return name, data


def _save(path, value, *, max_bytes=MAX_STATE_BYTES):
    temporary = None
    try:
        count = 0
        with tempfile.NamedTemporaryFile('wb', dir=path.parent, delete=False, suffix='.tmp') as stream:
            temporary = Path(stream.name)
            for part in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(value):
                data = part.encode('utf-8')
                count += len(data)
                if count > max_bytes:
                    raise ParseError('서버 작업 기록 분량 한도를 초과함. 원자료를 더 작은 묶음으로 나누어야 함',
                                     'intake_too_large')
                stream.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _convert_hwp(path, url, token):
    if not url or not token:
        raise HTTPException(409, {'error': 'hwp_worker_unavailable',
                                 'message': 'Windows 한글 변환 서버 연결이 필요합니다.'})
    # This is a fixed operator-configured service, never a user supplied URL.
    with httpx.Client(timeout=150, trust_env=False, follow_redirects=False) as client:
        response = client.post(url.rstrip('/') + '/convert',
                               headers={'Authorization': 'Bearer ' + token},
                               json={'name': path.name, 'base64': b64encode(path.read_bytes()).decode()})
    if response.status_code != 200 or len(response.content) > MAX_BODY:
        raise HTTPException(409, {'error': 'hwp_conversion_failed',
                                 'message': 'HWP 변환을 확인하지 못했습니다. HWPX 사본을 업로드해 주세요.'})
    converted = path.with_suffix('.hwpx')
    if converted.exists():
        raise ValueError('conversion_collision')
    try:
        result = response.json()
        _, data = _upload({'name': converted.name, 'base64': result['base64']}, {'.hwpx'})
        if (result['source_sha256'] != sha256(path.read_bytes()).hexdigest()
                or result['output_sha256'] != sha256(data).hexdigest()):
            raise ValueError('conversion_proof')
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError('conversion_proof') from exc
    converted.write_bytes(data)
    return converted


def create_app(*, root=None, secret=None, client_factory=None, hwp_url=None, hwp_token=None):
    secret = secret or os.environ.get('RA_GATEWAY_SECRET', '')
    if len(secret) < 32:
        raise RuntimeError('RA_GATEWAY_SECRET must contain at least 32 characters')
    root = Path(root or os.environ.get('RA_WEB_DATA_ROOT') or os.environ.get('RA_WEB_DATA') or 'data/external').resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Production requests use each visitor's Gemini key only in this process;
    # injected factories remain available for network-free tests.
    def gemini_key(payload):
        key = payload.get('gemini_api_key')
        if (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9._-]{20,512}', key)):
            raise HTTPException(422, {'error': 'gemini_key_required',
                'message': '본인의 Gemini API 키를 작업실에 입력해 주세요. 키는 서버에 저장하지 않습니다.'})
        return key

    def model_client(key):
        return client_factory() if client_factory is not None else LLMClient(provider='gemini', api_key=key)

    def sales_model_client(key, *, writing=False):
        # The interactive sales flow needs a bounded response time. Keep strict
        # source/review checks; use Flash for customer-facing writing.
        return client_factory() if client_factory is not None else LLMClient(
            provider='gemini', api_key=key,
            model='gemini-3.5-flash' if writing else 'gemini-3.5-flash-lite',
            timeout=30, max_retries=0)
    hwp_url = hwp_url or os.environ.get('RA_HWP_WORKER_URL')
    hwp_token = hwp_token or os.environ.get('RA_HWP_WORKER_TOKEN')
    app = FastAPI(title='문서 표준화 AI AGENT', docs_url=None, redoc_url=None, openapi_url=None)
    guests = GuestAccess(root)
    mcp_access = MCPAccess(root, guest_access=guests)
    app.add_middleware(Gateway, secret=secret, nonce_db=str(root / '.gateway-nonces.sqlite3'), guests=guests,
                       mcp_access=mcp_access)
    # ponytail: single process and per-job locks for the PC trial; durable queue at multi-host scale.
    locks, lock_guard, upload_lock = {}, threading.Lock(), threading.RLock()
    executor, work_slots = ThreadPoolExecutor(max_workers=2, thread_name_prefix='ra-web'), threading.BoundedSemaphore(2)
    instance = uuid4().hex

    def tenant(request):
        directory = root / sha256(request.state.user.encode()).hexdigest()
        directory.mkdir(exist_ok=True)
        return directory

    def job(request, identifier):
        if not ID.fullmatch(identifier):
            raise HTTPException(404, 'job_not_found')
        directory = tenant(request) / identifier
        if not (directory / 'state.json').is_file():
            raise HTTPException(404, 'job_not_found')
        with lock_guard:
            lock = locks.setdefault(str(directory), threading.RLock())
        return directory, lock

    def read(directory):
        state = json.loads((directory / 'state.json').read_text(encoding='utf-8'))
        if (state.get('processing') and state.get('operation_owner') != instance
                or state.get('mcp_run') and state['mcp_run'].get('owner') != instance):
            state.update(processing=False, status='failed',
                         error={'code': 'interrupted', 'message': '서버 재시작으로 작업이 중단되었습니다. 다시 작성·검수해 주세요.'})
            state.pop('result', None)
            state.pop('mcp_run', None)
            write(directory, state)
        if state.get('ctd') and state['ctd'].get('owner') != instance:
            state.pop('ctd', None)
            write(directory, state)
        if state.get('qos') and state['qos'].get('owner') != instance:
            state.pop('qos', None)
            write(directory, state)
        return state

    def write(directory, state):
        # All writes reserve their replacement size under the same upload lock.
        # No already stored file is deleted or replaced when a quota check fails.
        with upload_lock:
            state_path = directory / 'state.json'
            old_size = state_path.stat().st_size if state_path.exists() else 0
            owner_directory = directory.parent.parent if directory.parent.name == 'sales' else directory.parent
            tenant_free = MAX_TENANT_BYTES - _storage_size(owner_directory) + old_size
            server_free = MAX_STORAGE_BYTES - _storage_size(root) + old_size
            allowed = min(MAX_STATE_BYTES, tenant_free, server_free)
            if allowed <= 0:
                raise ParseError('서버 작업 기록 저장 한도를 초과함', 'intake_too_large')
            _save(state_path, state, max_bytes=allowed)

    def invalidate(state):
        if state.get('result', {}).get('metrics'):
            state['previous_metrics'] = state['result']['metrics']
        state.pop('result', None)
        state.pop('proposal', None)
        state.pop('error', None)
        state.pop('mcp_run', None)
        state.pop('ctd', None)
        state.pop('qos', None)
        state['revision'] += 1

    def ensure_idle(state):
        if state.get('processing'):
            raise HTTPException(409, {'error': 'job_busy', 'message': '작성·검수 작업이 진행 중입니다.'})

    def safe_error(exc):
        kinds = {'quota', 'billing_limit', 'rate_limit', 'configuration', 'connection', 'timeout'}
        if isinstance(exc, ParseError) and exc.code == 'intake_too_large':
            code, message = 'resource_limit', '원자료·문단·작업 기록 분량이 시험 서버 한도를 초과했습니다. 더 작은 자료 묶음으로 나누어 주세요.'
        elif isinstance(exc, LLMError):
            code = 'llm_' + (exc.kind if exc.kind in kinds else 'response')
            message = ('AI 응답 시간이 초과됐습니다. 같은 자료로 다시 시도해 주세요.' if exc.kind == 'timeout' else
                       '실제 AI 호출을 완료하지 못했습니다. 모델 연결·API 잔액을 확인해 주세요. 원문 기입으로 자동 대체하지 않습니다.')
        elif isinstance(exc, ValueError):
            code, message = 'validation_failed', '입력·매핑·원문 확인 또는 현재 검수 상태를 확인해 주세요.'
        else:
            code, message = 'processing_failed', '처리를 완료하지 못했습니다. 양식·원자료를 확인해 주세요.'
        return {'code': code, 'message': message}

    def schedule(directory, lock, state, operation, function, *, apply=None, present=None):
        if not work_slots.acquire(blocking=False):
            raise HTTPException(429, {'error': 'worker_busy', 'message': '서버에서 2개 작업을 처리 중입니다. 잠시 후 다시 시도해 주세요.'})
        state.update(processing=True, operation=operation, operation_owner=instance, status='processing')
        state.pop('error', None)
        write(directory, state)
        def execute():
            try:
                result = function()
                with lock:
                    current = read(directory)
                    if current.get('operation_owner') != instance:
                        return
                    if apply is not None:
                        apply(current, result)
                        current.update(processing=False, status='uploaded')
                    else:
                        current.update(result=result, processing=False, status=result.get('status', 'ready'))
                        if result.get('metrics'):
                            current['previous_metrics'] = result['metrics']
                    write(directory, current)
            except Exception as exc:
                with lock:
                    current = read(directory)
                    if current.get('operation_owner') != instance:
                        return
                    current.update(processing=False, status='failed', error=safe_error(exc))
                    current.pop('result', None)
                    write(directory, current)
            finally:
                work_slots.release()
        executor.submit(execute)
        return JSONResponse((present or view)(state), status_code=202)

    def view(state):
        public = {key: state[key] for key in ('job_id', 'revision', 'profile', 'configuration') if key in state}
        public['user_answers'] = deepcopy(state.get('user_answers', state.get('result', {}).get('answers', {})))
        public['status'] = ('processing' if state.get('processing') else 'failed' if state.get('error') else
                            state.get('result', {}).get('status', 'configured' if 'configuration' in state else 'uploaded'))
        public['operation'] = state.get('operation')
        if state.get('mcp_run'):
            public['status'] = 'awaiting_claude'
            public['operation'] = 'claude_' + state['mcp_run']['operation']
        if state.get('error'):
            public['error'] = state['error']
        public['mapping_rows'] = mapping_rows(state['profile'])
        public['intake'] = {key: state['intake'][key] for key in ('files', 'sources', 'fingerprint', 'confirmations')}
        if 'result' in state:
            public['result'] = {key: state['result'][key] for key in
                ('draft', 'review', 'questions', 'target_coverage', 'retrieval_scope', 'status', 'notice',
                 'mode', 'submission_ready', 'actual_model_requests', 'model_request_attempts',
                 'ready_for_output_check', 'metrics', 'human_kpi_measured', 'boss_review') if key in state['result']}
            for key in ('inference_origin', 'external_response_count', 'model_identity_verified', 'embedding_mode'):
                if key in state['result']:
                    public['result'][key] = state['result'][key]
        if state.get('ctd'):
            public['ctd'] = {'inputs': state['ctd']['inputs'], 'package': state['ctd']['package'],
                             'template_sha256': state['ctd']['template_sha256']}
        if state.get('qos'):
            public['qos'] = {'inputs': state['qos']['inputs'], 'package': state['qos']['package'],
                             'template_sha256': state['qos']['template_sha256']}
        return _clean(public, root)

    def configured(state):
        if 'configuration' not in state:
            raise HTTPException(409, {'error': 'mapping_confirmation_required'})
        sources = generation_sources(state['intake'])
        if not sources or any(f['status'] == 'deferred' for f in state['intake']['files']):
            raise HTTPException(409, {'error': 'source_confirmation_required',
                                     'message': '첨부 원자료 전체를 읽고 미확인 전사문을 확인해야 합니다.'})
        if len(sources) != len(state['intake']['sources']):
            raise HTTPException(409, {'error': 'source_confirmation_required'})
        return sources

    def ctd_sources(state):
        intake = state['intake']
        if (len(state['source_paths']) != len(intake['files'])
                or any(file['status'] == 'deferred' or
                       file.get('document_sha256') != sha256(Path(path).read_bytes()).hexdigest()
                       for file, path in zip(intake['files'], state['source_paths']))):
            raise HTTPException(409, {'error': 'source_confirmation_required'})
        sources = generation_sources(intake)
        if not sources or len(sources) != len(intake['sources']):
            raise HTTPException(409, {'error': 'source_confirmation_required'})
        return sources

    def ctd_inputs(payload):
        if (not isinstance(payload, dict)
                or set(payload) != {'product_name', 'product_variant', 'selected_sections'}):
            raise ValueError('ctd_inputs')
        return {key: deepcopy(payload[key]) for key in
                ('product_name', 'product_variant', 'selected_sections')}

    @app.exception_handler(LLMError)
    async def llm_error(request, exc):
        kinds = {'quota', 'billing_limit', 'rate_limit', 'configuration', 'connection', 'timeout'}
        kind = exc.kind if exc.kind in kinds else 'response'
        return JSONResponse({'error': 'llm_' + kind,
            'message': '실제 AI 호출을 완료하지 못했습니다. 모델 연결·API 잔액을 확인해 주세요. 원문 기입으로 자동 대체하지 않습니다.'}, 503)

    @app.exception_handler(ValueError)
    async def validation_error(request, exc):
        if isinstance(exc, ParseError) and exc.code == 'intake_too_large':
            error = safe_error(exc)
            return JSONResponse({'error': error['code'], 'message': error['message']}, 413)
        return JSONResponse({'error': 'validation_failed',
                             'message': '입력·매핑·원문 확인 또는 현재 검수 상태를 확인해 주세요.'}, 422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse({'error': 'processing_failed', 'message': '처리를 완료하지 못했습니다. 양식·원자료를 확인해 주세요.'}, 500)

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'deployment': 'pc_trial', 'default_ai': 'gemini_byok',
                'gemini_key_storage': 'request_only',
                'hwp_worker_configured': bool(hwp_url and hwp_token)}

    @app.post('/api/access/redeem')
    def redeem_access(request: Request, payload: dict):
        if request.state.user != 'invite:redeem':
            raise HTTPException(403, 'invitation_required')
        try:
            return guests.redeem(payload.get('token'))
        except GuestAccessError:
            raise HTTPException(401, {'error': 'invitation_invalid',
                'message': '초대 링크가 만료되었거나 이미 사용되었습니다. 새 링크를 요청해 주세요.'})

    @app.post('/api/access/logout')
    def logout_access(request: Request):
        if not request.state.guest:
            raise HTTPException(403, 'guest_required')
        guests.end(request.state.guest_session)
        return {'signed_out': True}

    @app.get('/api/me')
    def me(request: Request):
        guest = request.state.guest
        return {'signed_in': True, 'access_mode': 'guest' if guest else 'account',
                **({key: guest[key] for key in ('label', 'expires_at')} if guest else {})}

    def sales_job(request, identifier):
        if not ID.fullmatch(identifier):
            raise HTTPException(404, 'sales_job_not_found')
        directory = tenant(request) / 'sales' / identifier
        if not (directory / 'state.json').is_file():
            raise HTTPException(404, 'sales_job_not_found')
        with lock_guard:
            lock = locks.setdefault(str(directory), threading.RLock())
        return directory, lock

    def sales_view(state):
        trade = state.get('trade')
        public_trade = None if not trade else {key: trade[key] for key in
            ('workflow', 'rows', 'transaction', 'fingerprint', 'proposal', 'fields', 'inputs', 'prepared') if key in trade}
        return _clean({'job_id': state['job_id'], 'email': state['email'],
                       'requested_documents': requested_documents(state['email']),
                       'status': ('processing' if state.get('processing') else
                                  'failed' if state.get('error') else state.get('status', 'uploaded')),
                       'operation': state.get('operation'), 'error': state.get('error'),
                       'intake': {key: state['intake'][key] for key in ('files', 'sources', 'confirmations', 'fingerprint')},
                       'result': state.get('result'), 'trade': public_trade,
                       'user_notes': state.get('user_notes', [])}, root)

    def sales_sources(state, *, require_sources=True):
        intake = state['intake']
        if (len(state['source_paths']) != len(intake['files'])
                or any(file['status'] == 'deferred' or
                       file['document_sha256'] != sha256(Path(path).read_bytes()).hexdigest()
                       for file, path in zip(intake['files'], state['source_paths']))):
            raise HTTPException(409, {'error': 'source_confirmation_required'})
        accepted = generation_sources(intake)
        if (require_sources and not accepted) or len(accepted) != len(intake['sources']):
            raise HTTPException(409, {'error': 'source_confirmation_required'})
        return accepted

    @app.post('/api/sales/jobs')
    def sales_upload(request: Request, payload: dict):
        if not isinstance(payload, dict) or set(payload) - {'email_text', 'email_eml', 'sources'}:
            raise ValueError('sales_upload')
        raw_eml = payload.get('email_eml')
        eml = _upload(raw_eml, {'.eml'})[1] if raw_eml is not None else None
        email = parse_buyer_email(payload.get('email_text', ''), eml=eml)
        uploads = payload.get('sources')
        if not isinstance(uploads, list) or len(uploads) > 12:
            raise ValueError('sources')
        sources = [_upload(item, SUPPORTED_SUFFIXES) for item in uploads]
        processed = [str(Path(name).with_suffix('.hwpx') if Path(name).suffix.lower() == '.hwp' else name).casefold()
                     for name, _ in sources]
        if len(set(processed)) != len(sources) or len({name.casefold() for name, _ in sources}) != len(sources):
            raise ValueError('duplicate_filename')
        with upload_lock:
            owner = tenant(request)
            if (len(list((owner / 'sales').glob('*/state.json'))) >= 30 or
                    _storage_size(owner) + sum(len(data) for _, data in sources) + len(eml or b'') > MAX_TENANT_BYTES):
                raise HTTPException(429, {'error': 'storage_limit'})
            if _storage_size(root) + sum(len(data) for _, data in sources) + len(eml or b'') > MAX_STORAGE_BYTES:
                raise HTTPException(429, {'error': 'server_storage_limit'})
            base = owner / 'sales'; base.mkdir(exist_ok=True)
            directory = base / uuid4().hex; directory.mkdir()
            try:
                source_dir = directory / 'sources'; source_dir.mkdir()
                paths = []
                for name, data in sources:
                    path = source_dir / name
                    path.write_bytes(data)
                    if path.suffix.lower() == '.hwp':
                        path = _convert_hwp(path, hwp_url, hwp_token)
                    paths.append(str(path))
                state = {'job_id': directory.name, 'email': email, 'source_paths': paths,
                         'user_notes': [],
                         'created_at': time.time(), 'status': 'uploaded',
                         'intake': collect_multimodal(paths, allow_ocr=False,
                                                      max_source_chars=MAX_SOURCE_CHARS,
                                                      max_block_chars=MAX_BLOCK_CHARS)}
                write(directory, state)
                return sales_view(state)
            except BaseException:
                shutil.rmtree(directory)
                raise

    @app.get('/api/sales/jobs/{identifier}')
    def sales_get(identifier: str, request: Request):
        directory, lock = sales_job(request, identifier)
        with lock:
            return sales_view(read(directory))

    @app.delete('/api/sales/jobs/{identifier}')
    def sales_delete(identifier: str, request: Request):
        directory, lock = sales_job(request, identifier)
        with lock:
            state = read(directory)
            ensure_idle(state)
            shutil.rmtree(directory)
            return {'deleted': True}

    @app.post('/api/sales/jobs/{identifier}/supplement')
    def sales_supplement(identifier: str, request: Request, payload: dict):
        directory, lock = sales_job(request, identifier)
        with lock:
            state = read(directory)
            ensure_idle(state)
            if not isinstance(payload, dict) or set(payload) - {'text', 'sources'}:
                raise ValueError('sales_supplement')
            note, raw_files = payload.get('text', ''), payload.get('sources', [])
            if (not isinstance(note, str) or len(note) > 20_000 or
                    not isinstance(raw_files, list) or len(raw_files) > 12 or
                    not note.strip() and not raw_files or
                    len(state.get('user_notes', [])) + bool(note.strip()) > 10 or
                    len(state['source_paths']) + len(raw_files) > 12):
                raise ValueError('sales_supplement')
            uploads = [_upload(item, SUPPORTED_SUFFIXES) for item in raw_files]
            used = {file['filename'].casefold() for file in state['intake']['files']}
            names = [name.casefold() for name, _ in uploads]
            outputs = [Path(name).with_suffix('.hwpx').name.casefold()
                       if Path(name).suffix.lower() == '.hwp' else name.casefold()
                       for name, _ in uploads]
            if (len(set(names)) != len(names) or len(set(outputs)) != len(outputs)
                    or used.intersection(names) or used.intersection(outputs)):
                raise ValueError('duplicate_filename')
            if (_storage_size(tenant(request)) + sum(len(data) for _, data in uploads) > MAX_TENANT_BYTES or
                    _storage_size(root) + sum(len(data) for _, data in uploads) > MAX_STORAGE_BYTES):
                raise HTTPException(429, {'error': 'storage_limit'})
            paths = []
            try:
                for name, data in uploads:
                    path = directory / 'sources' / name
                    path.write_bytes(data)
                    if path.suffix.lower() == '.hwp':
                        path = _convert_hwp(path, hwp_url, hwp_token)
                    paths.append(str(path))
                intake = append_multimodal_intake(state['intake'], paths) if paths else state['intake']
                state['intake'] = intake
                state['source_paths'] += paths
                if note.strip():
                    state.setdefault('user_notes', []).append(note.strip())
                state.pop('result', None); state.pop('trade', None); state.pop('error', None)
                state['status'] = 'uploaded'
                write(directory, state)
                return sales_view(state)
            except BaseException:
                for path in paths:
                    Path(path).unlink(missing_ok=True)
                raise

    @app.post('/api/sales/jobs/{identifier}/intake')
    def sales_intake(identifier: str, request: Request, payload: dict):
        directory, lock = sales_job(request, identifier)
        with lock:
            state = read(directory)
            ensure_idle(state)
            if 'auto_ocr' in payload:
                if payload.get('auto_ocr') is not True or set(payload) - {'auto_ocr', 'gemini_api_key'}:
                    raise ValueError('ocr_request')
                key = gemini_key(payload) if client_factory is None else None
                paths = list(state['source_paths'])
                state.pop('result', None); state.pop('trade', None)
                write(directory, state)
                def apply(current, extracted):
                    current['intake'] = extracted
                    current.pop('result', None); current.pop('trade', None)
                return schedule(directory, lock, state, 'sales_ocr',
                    lambda: collect_multimodal(paths, client=sales_model_client(key), allow_ocr=True,
                        max_source_chars=MAX_SOURCE_CHARS, max_block_chars=MAX_BLOCK_CHARS),
                    apply=apply, present=sales_view)
            if 'transcriptions' in payload:
                state['intake'] = collect_multimodal(state['source_paths'], allow_ocr=False,
                    transcriptions=payload['transcriptions'], max_source_chars=MAX_SOURCE_CHARS,
                    max_block_chars=MAX_BLOCK_CHARS)
            if 'receipts' in payload:
                state['intake'] = confirm_intake(state['intake'], payload['receipts'],
                                                 confirmed=payload.get('confirmed'))
            if not ({'transcriptions', 'receipts'} & set(payload)) or set(payload) - {'transcriptions', 'receipts', 'confirmed'}:
                raise ValueError('intake_action')
            state.pop('result', None); state.pop('trade', None); state.pop('error', None)
            state['status'] = 'uploaded'
            write(directory, state)
            return sales_view(state)

    @app.post('/api/sales/jobs/{identifier}/draft')
    def sales_draft(identifier: str, request: Request, payload: dict):
        directory, lock = sales_job(request, identifier)
        with lock:
            state = read(directory)
            ensure_idle(state)
            if not isinstance(payload, dict) or set(payload) - {'language', 'gemini_api_key'}:
                raise ValueError('sales_draft')
            language = payload.get('language', 'en')
            if language not in {'en', 'ko'}:
                raise ValueError('language')
            sources = sales_sources(state, require_sources=False)
            key = gemini_key(payload) if client_factory is None else None
            state.pop('result', None)
            return schedule(directory, lock, state, 'sales_draft',
                lambda: draft_buyer_reply(state['email'], sources, sales_model_client(key, writing=True),
                                          language=language, user_notes=state.get('user_notes', [])),
                present=sales_view)

    @app.post('/api/sales/jobs/{identifier}/trade/propose')
    def sales_trade_propose(identifier: str, request: Request, payload: dict):
        directory, lock = sales_job(request, identifier)
        with lock:
            state = read(directory); ensure_idle(state)
            sources = sales_sources(state)
            workflow, transaction, rows = payload.get('workflow'), payload.get('transaction'), payload.get('rows', 1)
            if (workflow not in {'proforma_invoice', 'commercial_invoice', 'packing_list'}
                    or not isinstance(transaction, str) or not transaction.strip() or transaction != transaction.strip()
                    or type(rows) is not int or not 1 <= rows <= 20):
                raise ValueError('trade_selection')
            with tempfile.TemporaryDirectory(prefix='sales-trade-') as temp:
                template = Path(temp) / 'trade.docx'
                profile = create_global_template(workflow, template, rows=rows)
                template_b64 = b64encode(template.read_bytes()).decode()
            profile['global_transaction_id'] = transaction
            proposal = propose_global_bindings(profile, sources)
            fingerprint = sha256(json.dumps({'profile': profile, 'intake': state['intake']['fingerprint'],
                                              'transaction': transaction}, ensure_ascii=False,
                                             sort_keys=True).encode()).hexdigest()
            state['trade'] = {'workflow': workflow, 'transaction': transaction, 'rows': rows,
                              'fingerprint': fingerprint, 'proposal': proposal,
                              'fields': [{key: field.get(key) for key in ('value_key', 'label', 'required', 'input_required')}
                                         for field in profile['fields']],
                              'profile': profile, 'template_b64': template_b64,
                              'blank_input_b64': b64encode(blank_global_input(workflow, rows)).decode()}
            write(directory, state)
            return sales_view(state)

    @app.post('/api/sales/jobs/{identifier}/trade/prepare')
    def sales_trade_prepare(identifier: str, request: Request, payload: dict):
        directory, lock = sales_job(request, identifier)
        with lock:
            state = read(directory); ensure_idle(state)
            trade = state.get('trade')
            if (not trade or payload.get('fingerprint') != trade['fingerprint']
                    or set(payload) != {'fingerprint', 'source_bindings', 'direct_values', 'confirmed'}
                    or payload.get('confirmed') is not True):
                raise ValueError('trade_confirmation')
            proposed = {item['value_key']: item for item in trade['proposal']['fields']}
            bindings = payload['source_bindings']
            if (not isinstance(bindings, dict) or
                    any(key not in proposed or binding not in proposed[key]['candidates']
                        for key, binding in bindings.items())):
                raise ValueError('trade_binding')
            direct = payload['direct_values']
            prepared = prepare_global_workflow(trade['profile'], sales_sources(state), bindings, direct)
            trade['inputs'] = {'source_bindings': bindings, 'direct_values': direct}
            trade['prepared'] = {key: prepared[key] for key in
                                 ('plain_values', 'missing_fields', 'review', 'ready_for_output_check', 'fingerprint')}
            write(directory, state)
            return sales_view(state)

    @app.post('/api/sales/jobs/{identifier}/trade/export')
    def sales_trade_export(identifier: str, request: Request, payload: dict):
        directory, lock = sales_job(request, identifier)
        with lock:
            state = read(directory); ensure_idle(state)
            trade = state.get('trade')
            if (not trade or set(payload) != {'fingerprint', 'confirmed'} or payload.get('confirmed') is not True
                    or payload.get('fingerprint') != trade.get('prepared', {}).get('fingerprint')
                    or not trade['prepared']['ready_for_output_check']):
                raise ValueError('trade_export_confirmation')
            with tempfile.TemporaryDirectory(prefix='sales-trade-export-') as temp:
                template = Path(temp) / 'trade.docx'
                template.write_bytes(b64decode(trade['template_b64']))
                result = export_global_workflow(template, trade['profile'], sales_sources(state),
                    trade['inputs']['source_bindings'], trade['inputs']['direct_values'])
            stream = BytesIO()
            with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
                archive.writestr(result['filename'], result['document'])
                archive.writestr('trade_evidence.json', result['evidence'])
            return Response(stream.getvalue(), media_type='application/zip',
                            headers={'Content-Disposition': 'attachment; filename="trade_documents.zip"',
                                     'Cache-Control': 'no-store'})

    @app.post('/api/mcp/credentials')
    def issue_mcp(request: Request, payload: dict):
        if payload or request.state.mcp:
            raise ValueError('explicit workspace action required')
        try:
            return mcp_access.issue(request.state.user, ttl=7 * 86400)
        except MCPAccessError:
            raise HTTPException(409, 'connector_issuance_unavailable')

    @app.get('/api/mcp/credentials')
    def list_mcp(request: Request):
        return {'credentials': mcp_access.list_for(request.state.user)}

    @app.delete('/api/mcp/credentials/{identifier}')
    def revoke_mcp(identifier: str, request: Request):
        if not mcp_access.revoke(request.state.user, identifier):
            raise HTTPException(404, 'connector_not_found')
        return {'revoked': True}

    def do_upload(request: Request, payload: dict):
        template_name, template_bytes = _upload(payload.get('template'), {'.docx', '.hwpx', '.xlsx', '.pdf', '.hwp'})
        uploads = payload.get('sources')
        if not isinstance(uploads, list) or not 1 <= len(uploads) <= 12:
            raise ValueError('sources')
        sources = [_upload(item, SUPPORTED_SUFFIXES) for item in uploads]
        processed_names = [str(Path(name).with_suffix('.hwpx') if Path(name).suffix.lower() == '.hwp' else Path(name)).casefold()
                           for name, _ in sources]
        if (len({name.casefold() for name, _ in sources}) != len(sources)
                or len(set(processed_names)) != len(sources)):
            raise ValueError('duplicate_filename')
        owner = tenant(request)
        if (len(list(owner.glob('*/state.json'))) >= 30 or
                _storage_size(owner) + len(template_bytes)
                + sum(len(data) for _, data in sources) > MAX_TENANT_BYTES):
            raise HTTPException(429, {'error': 'storage_limit', 'message': '시험 배포의 사용자 저장 한도에 도달했습니다.'})
        if (_storage_size(root) + len(template_bytes)
                + sum(len(data) for _, data in sources) > MAX_STORAGE_BYTES):
            raise HTTPException(429, {'error': 'server_storage_limit', 'message': '시험 서버의 저장 한도에 도달했습니다.'})
        directory = owner / uuid4().hex
        directory.mkdir()
        try:
            form_dir, source_dir = directory / 'template', directory / 'sources'
            form_dir.mkdir(); source_dir.mkdir()
            template = form_dir / template_name
            template.write_bytes(template_bytes)
            conversions = []
            if template.suffix.lower() == '.hwp':
                before = sha256(template.read_bytes()).hexdigest()
                template = _convert_hwp(template, hwp_url, hwp_token)
                conversions.append({'kind': 'template', 'original_sha256': before,
                                    'converted_sha256': sha256(template.read_bytes()).hexdigest()})
            paths = []
            for name, data in sources:
                path = source_dir / name
                path.write_bytes(data)
                if path.suffix.lower() == '.hwp':
                    before = sha256(data).hexdigest()
                    path = _convert_hwp(path, hwp_url, hwp_token)
                    conversions.append({'kind': 'source', 'original_name': name, 'original_sha256': before,
                                        'converted_sha256': sha256(path.read_bytes()).hexdigest()})
                paths.append(str(path))
            profile = load_form_profile(template) or analyze_template(template)
            if len(profile.get('fields', [])) > 2000:
                raise ParseError('서버 입력칸 수 한도를 초과함', 'intake_too_large')
            profile.setdefault('document_kind', 'application')
            state = {'job_id': directory.name, 'revision': 1, 'template_path': str(template),
                     'source_paths': paths, 'raw_profile': profile, 'profile': profile,
                     'user_answers': {}, 'created_at': time.time(),
                     'intake': collect_multimodal(paths, allow_ocr=False, max_source_chars=MAX_SOURCE_CHARS,
                                                  max_block_chars=MAX_BLOCK_CHARS), 'conversions': conversions}
            write(directory, state)
            return view(state)
        except BaseException:
            shutil.rmtree(directory)
            raise

    @app.post('/api/jobs')
    def upload(request: Request, payload: dict):
        with upload_lock:
            return do_upload(request, payload)

    @app.get('/api/jobs')
    def list_jobs(request: Request):
        entries = []
        for state_path in tenant(request).glob('*/state.json'):
            directory, lock = job(request, state_path.parent.name)
            with lock:
                state = read(directory)
                entries.append({'job_id': state['job_id'],
                    'template_name': Path(state['template_path']).name,
                    'created_at': state.get('created_at'), 'status': view(state)['status']})
        return {'jobs': sorted(entries, key=lambda item: item['created_at'] or 0, reverse=True)}

    @app.get('/api/jobs/{identifier}')
    def get_job(identifier: str, request: Request):
        directory, lock = job(request, identifier)
        with lock:
            return view(read(directory))

    @app.get('/api/ctd/sections')
    def ctd_sections():
        return {'sections': [{'section_id': item['section_id'], 'title': item['title'],
                              'module': item['module']} for item in CTD_SECTIONS]}

    @app.get('/api/qos/sections')
    def qos_sections():
        return {'sections': list(QOS_S_SECTIONS)}

    @app.get('/api/jobs/{identifier}/qos/options')
    def qos_options(identifier: str, request: Request):
        directory, lock = job(request, identifier)
        with lock:
            return {'options': propose_dmf_options(ctd_sources(read(directory)))}

    @app.post('/api/jobs/{identifier}/qos/prepare')
    def qos_prepare(identifier: str, request: Request, payload: dict):
        directory, lock = job(request, identifier)
        with lock:
            state = read(directory)
            ensure_idle(state)
            state.pop('qos', None)
            write(directory, state)
            if (not isinstance(payload, dict) or
                    set(payload) != {'product_name', 'product_variant',
                                     'selected_sections', 'dmf_links'}):
                raise ValueError('qos_inputs')
            inputs = deepcopy(payload)
            package = prepare_qos_package(ctd_sources(state), **inputs)
            state['qos'] = {'owner': instance, 'inputs': inputs, 'package': package,
                            'template_sha256': sha256(Path(state['template_path']).read_bytes()).hexdigest()}
            write(directory, state)
            return view(state)

    @app.post('/api/jobs/{identifier}/qos/export')
    def qos_export(identifier: str, request: Request, payload: dict):
        directory, lock = job(request, identifier)
        with lock:
            state = read(directory)
            ensure_idle(state)
            prepared = state.get('qos')
            if (not prepared or prepared['owner'] != instance or
                    set(payload) != {'confirmed', 'fingerprint'} or
                    payload['confirmed'] is not True or
                    payload['fingerprint'] != prepared['package']['fingerprint']):
                raise HTTPException(409, {'error': 'current_qos_review_required'})
            template = Path(state['template_path'])
            if sha256(template.read_bytes()).hexdigest() != prepared['template_sha256']:
                raise HTTPException(409, {'error': 'template_changed'})
            sources = ctd_sources(state)
            with tempfile.TemporaryDirectory(prefix='ra-qos-export-') as temporary:
                target = Path(temporary) / ('CTD_2.3.S_DMF_검토초안' + template.suffix.lower())
                result = fill_ctd_working_template(
                    template, target, prepared['package'], sources=sources,
                    product_name=prepared['inputs']['product_name'],
                    product_variant=prepared['inputs']['product_variant'],
                    confirmed_dmf_links=prepared['inputs']['dmf_links'])
                evidence = _clean(json.loads(result['evidence_path'].read_text(encoding='utf-8')), root)
                stream = BytesIO()
                with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
                    archive.writestr(target.name, target.read_bytes())
                    archive.writestr('CTD_2.3.S_출처와_누락.json',
                                     json.dumps(evidence, ensure_ascii=False))
                return Response(stream.getvalue(), media_type='application/zip', headers={
                    'Content-Disposition': 'attachment; filename="CTD_QOS_DMF_working_draft.zip"'})

    @app.post('/api/jobs/{identifier}/ctd/prepare')
    def ctd_prepare(identifier: str, request: Request, payload: dict):
        directory, lock = job(request, identifier)
        with lock:
            state = read(directory)
            ensure_idle(state)
            state.pop('ctd', None)
            write(directory, state)
            inputs = ctd_inputs(payload)
            package = prepare_ctd_package(ctd_sources(state), **inputs)
            state['ctd'] = {'owner': instance, 'inputs': inputs, 'package': package,
                            'template_sha256': sha256(Path(state['template_path']).read_bytes()).hexdigest()}
            write(directory, state)
            return view(state)

    @app.post('/api/jobs/{identifier}/ctd/export')
    def ctd_export(identifier: str, request: Request, payload: dict):
        directory, lock = job(request, identifier)
        with lock:
            state = read(directory)
            ensure_idle(state)
            prepared = state.get('ctd')
            if (not prepared or prepared['owner'] != instance or
                    set(payload) != {'confirmed', 'fingerprint'} or
                    payload['confirmed'] is not True or
                    payload['fingerprint'] != prepared['package']['fingerprint']):
                raise HTTPException(409, {'error': 'current_ctd_review_required'})
            template = Path(state['template_path'])
            if sha256(template.read_bytes()).hexdigest() != prepared['template_sha256']:
                raise HTTPException(409, {'error': 'template_changed'})
            sources = ctd_sources(state)
            with tempfile.TemporaryDirectory(prefix='ra-ctd-export-') as temporary:
                target = Path(temporary) / ('CTD_작업초안' + template.suffix.lower())
                result = fill_ctd_working_template(
                    template, target, prepared['package'], sources=sources,
                    product_name=prepared['inputs']['product_name'],
                    product_variant=prepared['inputs']['product_variant'])
                evidence = _clean(json.loads(result['evidence_path'].read_text(encoding='utf-8')), root)
                stream = BytesIO()
                with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
                    archive.writestr(target.name, target.read_bytes())
                    archive.writestr('CTD_출처와_누락.json', json.dumps(evidence, ensure_ascii=False))
                return Response(stream.getvalue(), media_type='application/zip', headers={
                    'Content-Disposition': 'attachment; filename="CTD_working_draft.zip"'})

    @app.delete('/api/jobs/{identifier}')
    def delete_job(identifier: str, request: Request):
        directory, lock = job(request, identifier)
        with lock:
            ensure_idle(read(directory))
            shutil.rmtree(directory)
        return {'deleted': True}

    @app.post('/api/jobs/{identifier}/configure')
    def configure(identifier: str, request: Request, payload: dict):
        directory, lock = job(request, identifier)
        with lock:
            state = read(directory)
            ensure_idle(state)
            if payload.get('confirmed') is not True:
                raise ValueError('confirmation')
            profile, _ = configure_profile(state['raw_profile'], payload.get('rows'))
            profile.update(auto_mapping_confirmed=True, citation_mode='sidecar')
            config = {key: deepcopy(payload.get(key, {} if key == 'field_values' else ''))
                      for key in ('selected_keys', 'product_name', 'variant', 'instruction', 'field_values')}
            config['ra_workflow'] = payload.get('ra_workflow') or 'product_approval'
            if (not isinstance(config['instruction'], str) or not config['instruction'].strip()
                    or len(config['instruction']) > 10_000
                    or not isinstance(config['field_values'], dict)
                    or any(not isinstance(v, str) or len(v) > 100_000 for v in config['field_values'].values())):
                raise ValueError('configuration')
            runtime = writer.target_profile(state['template_path'], profile, config['selected_keys'],
                                            product_name=config['product_name'], variant=config['variant'],
                                            ra_workflow=config['ra_workflow'])
            protected = {field['id']: field for field in runtime['fields'] if field.get('input_required')}
            for field in profile['fields']:
                if field['id'] in protected:
                    field.update(input_required=True, input_mode='user_provided', narrative_style_required=False)
            # Keep the first actual AI baseline when the same document is regenerated.
            if state.get('result', {}).get('metrics'):
                state['previous_metrics'] = state['result']['metrics']
            state.update(profile=profile, configuration=config)
            invalidate(state)
            write(directory, state)
            return view(state)

    @app.post('/api/jobs/{identifier}/intake')
    def intake(identifier: str, request: Request, payload: dict):
        directory, lock = job(request, identifier)
        with lock:
            state = read(directory)
            ensure_idle(state)
            if 'auto_ocr' in payload:
                if payload.get('auto_ocr') is not True or set(payload) - {'auto_ocr', 'gemini_api_key'}:
                    raise ValueError('ocr_request')
                key = gemini_key(payload) if client_factory is None else None
                paths = list(state['source_paths'])
                invalidate(state)
                write(directory, state)
                def collect():
                    return collect_multimodal(paths, client=model_client(key), allow_ocr=True,
                        max_source_chars=MAX_SOURCE_CHARS, max_block_chars=MAX_BLOCK_CHARS)
                def apply(current, extracted):
                    current['intake'] = extracted
                    current.pop('result', None)
                return schedule(directory, lock, state, 'intake_ocr', collect, apply=apply)
            if 'transcriptions' in payload:
                state['intake'] = collect_multimodal(state['source_paths'], allow_ocr=False,
                    transcriptions=payload['transcriptions'], max_source_chars=MAX_SOURCE_CHARS,
                    max_block_chars=MAX_BLOCK_CHARS)
            if 'receipts' in payload:
                state['intake'] = confirm_intake(state['intake'], payload['receipts'],
                                                confirmed=payload.get('confirmed'))
            invalidate(state)
            write(directory, state)
            return view(state)

    @app.post('/api/jobs/{identifier}/proposals')
    def proposals(identifier: str, request: Request):
        directory, lock = job(request, identifier)
        with lock:
            state = read(directory)
            ensure_idle(state)
            sources, config = configured(state), state['configuration']
            state['proposal'] = writer.propose_ra_auto(state['template_path'], state['profile'], sources,
                config['selected_keys'], product_name=config['product_name'], variant=config['variant'],
                ra_workflow=config['ra_workflow'])
            write(directory, state)
            return _clean(state['proposal'], root)

    @app.post('/api/jobs/{identifier}/generate')
    def generate(identifier: str, request: Request, payload: dict):
        directory, lock = job(request, identifier)
        with lock:
            state = read(directory)
            ensure_idle(state)
            sources, config = configured(state), state['configuration']
            mode = payload.get('mode')
            if mode == 'source_copy':
                proposal = state.get('proposal', {})
                if (not proposal or payload.get('confirmed_proposals') != proposal.get('fingerprint')
                        or not isinstance(payload.get('confirmed_keys'), list)
                        or set(payload['confirmed_keys']) != set(proposal['source_bindings'])
                        or len(payload['confirmed_keys']) != len(set(payload['confirmed_keys']))):
                    raise ValueError('candidate_confirmation')
            if mode not in {'live', 'source_copy'}:
                raise ValueError('mode')
            key = gemini_key(payload) if mode == 'live' and client_factory is None else None
            confirmed_proposals = payload.get('confirmed_proposals')
            supplied = payload.get('answers', {})
            if (not isinstance(supplied, dict) or any(not isinstance(key, str) or not key.strip()
                    or len(key) > 2000 or '\x00' in key or not isinstance(value, str)
                    or len(value) > 10_000 or '\x00' in value for key, value in supplied.items())):
                raise ValueError('answers')
            answers = deepcopy(state.get('user_answers', state.get('result', {}).get('answers', {})))
            answers.update(supplied)
            if len(answers) > 64:
                raise ValueError('answers')
            # Exact question keys retain actual user replies; new questions never
            # borrow another question's value, and model/quota failures keep them.
            state['user_answers'] = answers
            previous = state.get('result', {}).get('metrics') or state.get('previous_metrics')
            if previous:
                state['previous_metrics'] = previous
            # Failed model/validation attempts must not leave a previous ready draft downloadable.
            invalidate(state)
            state.pop('error', None)
            write(directory, state)
            def generate_result():
                return writer.generate_ra_auto(config['instruction'], state['template_path'], state['profile'],
                    sources, config['selected_keys'], product_name=config['product_name'], variant=config['variant'],
                    ra_workflow=config['ra_workflow'], mode=mode, client=model_client(key) if mode == 'live' else None,
                    field_values=config['field_values'], confirmed_proposals=confirmed_proposals,
                    answers=answers, previous_metrics=previous)
            if mode == 'live':
                return schedule(directory, lock, state, 'generate', generate_result)
            result = generate_result()
            state['result'] = result
            if result.get('metrics'):
                state['previous_metrics'] = result['metrics']
            write(directory, state)
            return view(state)

    @app.post('/api/jobs/{identifier}/answers')
    def save_answers(identifier: str, request: Request, payload: dict):
        """Actual web replies can be saved without invoking any model."""
        directory, lock = job(request, identifier)
        with lock:
            state = read(directory)
            ensure_idle(state)
            questions = state.get('result', {}).get('questions', [])
            allowed = {q if isinstance(q, str) else q.get('question', '') for q in questions[:2]}
            answers = payload.get('answers')
            if (set(payload) != {'answers'} or not isinstance(answers, dict) or not answers
                    or set(answers) - allowed
                    or any(not isinstance(v, str) or not v.strip() or len(v) > 10_000 or '\x00' in v
                           for v in answers.values())):
                raise ValueError('current human answers required')
            cumulative = deepcopy(state.get('user_answers', state.get('result', {}).get('answers', {})))
            cumulative.update(answers)
            if len(cumulative) > 64:
                raise ValueError('answers')
            state['user_answers'] = cumulative
            invalidate(state)
            write(directory, state)
            return view(state)

    @app.post('/api/jobs/{identifier}/review')
    def review(identifier: str, request: Request, payload: dict):
        directory, lock = job(request, identifier)
        with lock:
            state = read(directory)
            ensure_idle(state)
            if state.get('result', {}).get('mode') != 'live':
                raise ValueError('live_draft_required')
            key = gemini_key(payload) if client_factory is None else None
            result = state.pop('result', None)
            if result and result.get('metrics'):
                state['previous_metrics'] = result['metrics']
            state['revision'] += 1
            write(directory, state)
            if not result:
                raise ValueError('draft_required')
            return schedule(directory, lock, state, 'review', lambda:
                writer.review_ra_auto(result, payload.get('draft'), client=model_client(key)))

    @app.post('/api/jobs/{identifier}/export')
    def export(identifier: str, request: Request, payload: dict):
        directory, lock = job(request, identifier)
        with lock:
            state = read(directory)
            ensure_idle(state)
            if not state.get('result'):
                raise HTTPException(409, {'error': 'current_review_required'})
            if (state['result'].get('inference_origin') == 'claude_mcp_client_supplied'
                    and state['result'].get('mcp_binding') != mcp_binding(state)):
                invalidate(state)
                write(directory, state)
                raise HTTPException(409, {'error': 'current_claude_review_required'})
            output = writer.export_ra_auto(state['result'], confirmed=payload.get('confirmed'), native_review='off')
            evidence = _clean(json.loads(output['evidence']), root)
            evidence['hwp_conversions'] = state['conversions']
            stream = BytesIO()
            with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
                archive.writestr(output['filename'], output['document'])
                archive.writestr('출처와_검수기록.json', json.dumps(evidence, ensure_ascii=False, indent=2))
            return Response(stream.getvalue(), media_type='application/zip',
                            headers={'Content-Disposition': 'attachment; filename="RA_document_package.zip"'})

    def mcp_binding(state):
        inputs = {key: state.get(key) for key in ('revision', 'profile', 'configuration', 'intake', 'user_answers')}
        inputs['server_instance'] = instance
        inputs['files'] = [sha256(Path(path).read_bytes()).hexdigest()
                           for path in [state['template_path'], *state['source_paths']]]
        base = Path(__file__).resolve().parents[1]
        inputs['prompts'] = {path.name: sha256(path.read_bytes()).hexdigest()
                             for path in sorted((base / 'prompts').glob('*.md'))}
        inputs['engines'] = {name: sha256((base / name).read_bytes()).hexdigest() for name in
            ('llm/client.py', 'llm/mcp_client.py', 'app/ra_auto_service.py', 'agent/pipeline.py',
             'agent/grounding.py', 'agent/completeness.py', 'agent/review.py')}
        return sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def mcp_step(directory, state, client):
        run = state['mcp_run']
        try:
            if run['owner'] != instance or run['binding'] != mcp_binding(state):
                raise ValueError('stale connector inference')
            operation = run['operation']
            if operation == 'ocr':
                result = collect_multimodal(state['source_paths'], client=client, allow_ocr=True,
                    max_source_chars=MAX_SOURCE_CHARS, max_block_chars=MAX_BLOCK_CHARS)
            elif operation == 'review':
                result = writer.review_ra_auto(run['base_result'], run['draft'], client=client)
            else:
                config = state['configuration']
                result = writer.generate_ra_auto(config['instruction'], state['template_path'], state['profile'],
                    configured(state), config['selected_keys'], product_name=config['product_name'],
                    variant=config['variant'], ra_workflow=config['ra_workflow'], mode='live', client=client,
                    field_values=config['field_values'], answers=state.get('user_answers', {}),
                    previous_metrics=state.get('previous_metrics'))
            client.assert_complete()
        except PendingInference as pending:
            run['records'] = client.records
            state.update(status='awaiting_claude', operation='claude_' + run['operation'])
            write(directory, state)
            inference = deepcopy(pending.request)
            image = inference.pop('image', None)
            value = {'job_id': state['job_id'], 'status': 'awaiting_claude',
                     'inference': _clean(inference, root),
                     'next_step': '현재 Claude 대화 모델로 instructions와 payload에 따라 JSON을 작성하고 ra_respond에 전달하세요. 외부 API 키를 요청하지 않습니다.'}
            if image:
                value['_mcp_image'] = {'data': image['data'], 'mime_type': image['mime']}
            return value
        except Exception as exc:
            state.pop('mcp_run', None)
            state.pop('result', None)
            state.update(status='failed', error=safe_error(exc))
            write(directory, state)
            return view(state)
        count = len(client.records)
        state.pop('mcp_run', None)
        if operation == 'ocr':
            state['intake'] = result
            state['status'] = 'uploaded'
        else:
            result.update(inference_origin='claude_mcp_client_supplied', external_response_count=count,
                          actual_model_requests=0, model_identity_verified=False,
                          embedding_mode='deterministic_local_lexical', mcp_binding=mcp_binding(state),
                          notice='Claude 커넥터가 전달한 작성·검수 응답임. 서버 유료 API 호출 없음; 모델 신원·실사용 품질 인증 아님. 담당자 최종 확인 필요함.')
            state['result'] = result
            state['status'] = result.get('status', 'ready')
            if result.get('metrics'):
                state['previous_metrics'] = result['metrics']
        write(directory, state)
        return view(state)

    def mcp_get(request, job_id):
        directory, lock = job(request, job_id)
        with lock:
            state = read(directory)
            run = state.get('mcp_run')
            if run:
                if run['owner'] != instance or run['binding'] != mcp_binding(state):
                    invalidate(state)
                    write(directory, state)
                    raise HTTPException(409, 'connector_request_stale')
                records = MCPInferenceClient(records=run['records']).records
                if records and 'response' not in records[-1]:
                    value = view(state)
                    inference = deepcopy(records[-1])
                    image = inference.pop('image', None)
                    value['inference'] = _clean(inference, root)
                    if image:
                        value['_mcp_image'] = {'data': image['data'], 'mime_type': image['mime']}
                    return value
            return view(state)

    def mcp_start(request, job_id, operation, draft=None):
        directory, lock = job(request, job_id)
        with lock:
            state = read(directory)
            ensure_idle(state)
            if state.get('mcp_run'):
                raise HTTPException(409, 'connector_request_pending')
            if operation not in {'generate', 'review', 'ocr'}:
                raise ValueError('operation')
            base_result = deepcopy(state.get('result'))
            if operation != 'ocr':
                configured(state)
            if operation == 'review' and (not base_result or base_result.get('mode') != 'live'
                    or not isinstance(draft, dict) or any(not isinstance(v, str) for v in draft.values())):
                raise ValueError('review requires existing draft')
            invalidate(state)
            if operation == 'ocr':
                # Rereading invalidates earlier OCR receipt, even before the first Claude response.
                state['intake'] = collect_multimodal(state['source_paths'], allow_ocr=False,
                    max_source_chars=MAX_SOURCE_CHARS, max_block_chars=MAX_BLOCK_CHARS)
            run = {'operation': operation, 'records': [], 'owner': instance,
                   'binding': mcp_binding(state), 'started_at': time.time()}
            if operation == 'review':
                run.update(base_result=base_result, draft=deepcopy(draft))
            state['mcp_run'] = run
            write(directory, state)
            return mcp_step(directory, state, MCPInferenceClient())

    def mcp_respond(request, job_id, request_id, fingerprint, response):
        directory, lock = job(request, job_id)
        with lock:
            state = read(directory)
            ensure_idle(state)
            run = state.get('mcp_run')
            if not run:
                raise HTTPException(409, 'connector_request_missing')
            if run['owner'] != instance or run['binding'] != mcp_binding(state):
                invalidate(state)
                write(directory, state)
                raise HTTPException(409, 'connector_request_stale')
            client = MCPInferenceClient(records=run['records'])
            client.submit_response(request_id, fingerprint, response)
            return mcp_step(directory, state, client)

    def mcp_ctd_preview(request, job_id, product_name, product_variant, selected_sections):
        directory, lock = job(request, job_id)
        with lock:
            state = read(directory)
            ensure_idle(state)
            return {'job_id': job_id, 'package': prepare_ctd_package(ctd_sources(state),
                product_name=product_name, product_variant=product_variant,
                selected_sections=selected_sections)}

    install_mcp(app, list_jobs=list_jobs, get_job=mcp_get, start=mcp_start,
                respond=mcp_respond, ctd_preview=mcp_ctd_preview)

    return app


def production_app():
    """uvicorn app.web_api:production_app --factory --host 127.0.0.1 --port 8600"""
    return create_app()
