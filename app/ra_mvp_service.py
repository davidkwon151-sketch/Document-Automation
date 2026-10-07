"""Bounded registered RA forms, using the existing evidence and export checks."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from time import perf_counter
from uuid import uuid4

from agent.brief import model_profile, profile_fields
from agent.documents import DOCUMENT_KINDS
from agent.metrics import record_revision, start_metrics
from agent.pipeline import review_result, run_pipeline
from agent.ra import RA_WORKFLOWS
from templates import load_form_profile
from templates.value_rules import inspect_form_values

ROOT = Path(__file__).resolve().parents[1]
DEMO_NOTICE = '규칙 기입 데모이며 실제 AI 작성 결과가 아님. 공개 원문의 선정 항목만 기입함.'


def _local_path(root, name):
    path = (Path(root).resolve() / name).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('MVP 원본과 기록은 프로젝트 내부 파일이어야 함')
    return path


def load_mvp_forms(root=ROOT):
    """Return the bounded catalog, never an inferred or privately obtained form."""
    data = json.loads((Path(root) / 'templates/ra_mvp_catalog.json').read_text(encoding='utf-8'))
    forms = data.get('forms') if isinstance(data, dict) else None
    if not isinstance(forms, list) or not 1 <= len(forms) <= 8:
        raise ValueError('MVP에는 등록 양식 1~8종이 필요함')
    seen = set()
    for record in forms:
        identifier = record.get('id') if isinstance(record, dict) else None
        if (not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', identifier)
                or identifier in seen or record.get('profile_id') != identifier):
            raise ValueError('MVP 양식 ID·등록 프로파일이 잘못됨')
        seen.add(identifier)
        if (not isinstance(record.get('title'), str) or not record['title'].strip()
                or not isinstance(record.get('source_path'), str)
                or not isinstance(record.get('source_sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', record['source_sha256'])
                or record.get('ra_workflow') not in RA_WORKFLOWS
                or not isinstance(record.get('document_kind'), str)
                or record['document_kind'] not in DOCUMENT_KINDS):
            raise ValueError('MVP 양식의 원본·업무·문서 종류가 잘못됨')
        _local_path(root, record['source_path'])
        for key in ('source_based_keys', 'user_input_keys', 'demo_field_keys'):
            values = record.get(key)
            if (not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values)
                    or len(values) != len(set(values))):
                raise ValueError('MVP의 기입 항목 목록이 잘못됨')
        if (set(record['source_based_keys']) & set(record['user_input_keys'])
                or not set(record['demo_field_keys']) <= set(record['source_based_keys'])):
            raise ValueError('원자료 기입과 사용자 직접 입력 항목을 혼합할 수 없음')
        aliases = record.get('demo_field_map', {})
        if (not isinstance(aliases, dict)
                or any(not isinstance(key, str) or key not in record['demo_field_keys']
                       or not isinstance(value, str) or not value.strip() or value != value.strip()
                       for key, value in aliases.items())):
            raise ValueError('데모 항목 매핑은 등록된 원자료 데모 항목과 원문 항목명이어야 함')
        source_keys = [aliases.get(key, key) for key in record['demo_field_keys']]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError('여러 데모 항목을 같은 원문 항목으로 중복 매핑할 수 없음')
        if not isinstance(record.get('demo_manifest'), str) or not isinstance(record.get('demo_source_id'), str):
            raise ValueError('데모의 공식 원자료 기록이 필요함')
        _local_path(root, record['demo_manifest'])
    return deepcopy(forms)


def resolve_mvp_form(form_id, root=ROOT):
    record = next((form for form in load_mvp_forms(root) if form['id'] == form_id), None)
    if record is None:
        raise ValueError('등록된 MVP 양식을 선택해야 함')
    path = _local_path(root, record['source_path'])
    if not path.is_file() or sha256(path.read_bytes()).hexdigest() != record['source_sha256']:
        raise ValueError('MVP 양식 원본 SHA가 등록 기록과 다름')
    profile_path = _local_path(root, record.get('profile_path', f'templates/profiles/{form_id}.json'))
    if (profile_path != _local_path(root, f'templates/profiles/{form_id}.json')
            or not profile_path.is_file()
            or sha256(profile_path.read_bytes()).hexdigest() != record.get('profile_sha256')):
        raise ValueError('MVP 등록 프로파일 SHA가 카탈로그와 다름')
    profile = load_form_profile(path, record['profile_id'])
    if profile is None or profile.get('entry_id') != form_id:
        raise ValueError('MVP 원본과 등록 입력 위치를 확인할 수 없음')
    fields = profile_fields(profile)
    direct = {field['value_key'] for field in fields if field.get('input_required')}
    grounded = {field['value_key'] for field in fields if not field.get('input_required')}
    if direct != set(record['user_input_keys']) or grounded != set(record['source_based_keys']):
        raise ValueError('MVP 기입 항목과 원본 등록 권한이 다름')
    profile = model_profile(profile)
    profile.update(domain='pharmaceutical_ra', ra_workflow=record['ra_workflow'],
                   document_kind=record['document_kind'], citation_mode='sidecar')
    return path, profile, record


def _baseline_hash(metrics):
    return sha256(json.dumps(metrics['baseline_draft'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def generate_mvp(instruction, form_id, paths=None, field_values=None, answers=None, client=None,
                 demo=False, previous_metrics=None, root=ROOT, *, allow_annex=False):
    """Generate a fresh result; export still requires review and confirmation."""
    started = perf_counter()
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError('작성 지시를 입력해야 함')
    if type(demo) is not bool or type(allow_annex) is not bool:
        raise ValueError('데모·별첨 허용 여부는 boolean이어야 함')
    path, profile, record = resolve_mvp_form(form_id, root)
    supplied = {} if field_values is None else field_values
    answers = {} if answers is None else answers
    if (not isinstance(supplied, dict) or any(key not in record['user_input_keys'] or not isinstance(value, str)
            for key, value in supplied.items())):
        raise ValueError('직접 입력은 등록된 사용자 입력 항목의 문자열이어야 함')
    if not isinstance(answers, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in answers.items()):
        raise ValueError('보완 답변은 질문과 답변 문자열이어야 함')
    grounded_questions = {question for field in profile['fields'] if not field.get('input_required')
                          for question in (field['value_key'], field['label'], f"{field['label']}을(를) 알려주시겠습니까?")}
    if grounded_questions.intersection(answers):
        raise ValueError('원자료 기반 항목은 사용자 답변을 새 사실 근거로 대체할 수 없음')
    if previous_metrics is not None:
        if not isinstance(previous_metrics, dict):
            raise ValueError('KPI 기록은 객체여야 함')
        if 'baseline_draft' not in previous_metrics:
            raise ValueError('최초 초안의 KPI 기준이 필요함')
        if previous_metrics.get('mvp_form_id') not in (None, form_id):
            raise ValueError('다른 양식의 KPI 기준을 재사용할 수 없음')
        if previous_metrics.get('mvp_baseline_sha256') not in (None, _baseline_hash(previous_metrics)):
            raise ValueError('최초 초안의 KPI 기준이 변경됨')
    if allow_annex and profile.get('render_mode') == 'overlay':
        profile['overflow_mode'] = 'annex'
    if not demo:
        result = run_pipeline(instruction, paths, answers=answers, field_values=supplied, client=client,
                              template_profile=profile, ra_workflow=record['ra_workflow'],
                              document_kind=record['document_kind'], previous_metrics=previous_metrics,
                              semantic_review=True)
        user_grounded = any(source.get('filename') == '사용자 입력'
                            and source.get('location') in record['source_based_keys']
                            for source in result.get('sources', []))
        if user_grounded or set(result.get('locked_fields', {})) - set(record['user_input_keys']):
            raise ValueError('원자료 기반 항목을 작성 지시에서 새 사실 근거로 추출할 수 없음. 근거 자료를 첨부해야 함')
    else:
        if paths:
            raise ValueError('데모는 연결된 공식 예시 자료만 사용함. 첨부 자료 작성은 실제 AI 모드를 선택해야 함')
        from evals.ra_public import cited, read_source

        manifest = json.loads(_local_path(root, record['demo_manifest']).read_text(encoding='utf-8'))
        records = [source for source in manifest['sources'] if source['id'] == record['demo_source_id']]
        if len(records) != 1:
            raise ValueError('데모의 공식 원자료 ID를 확인할 수 없음')
        source_record = records[0]
        facts, sources, document = read_source(source_record, Path(root).resolve())
        selected = []
        for key in record['demo_field_keys']:
            source_key = record.get('demo_field_map', {}).get(key, key)
            matches = [fact for fact in facts if fact['field_key'] == source_key]
            if len(matches) != 1:
                raise ValueError('데모 선정 항목의 전체 공식 원문을 유일하게 확인할 수 없음')
            selected.append((key, matches[0]))
        if not selected:
            raise ValueError('데모 선정 항목의 전체 공식 원문을 확인할 수 없음')
        profile.update(ra_product_name=source_record['product_name'], ra_product_variant=source_record['product_variant'])
        first = selected[0][1]
        summary = first['value']
        if record['document_kind'] == 'report':
            summary = '○ ' + summary + '임'
        draft = {'제목': 'RA 공개 원자료 검토', '요약': cited(summary, first['source_id']),
                 '본문': cited(summary, first['source_id'])}
        draft.update({field['value_key']: '' for field in profile['fields']})
        draft.update({key: cited(fact['value'], fact['source_id']) for key, fact in selected})
        combined = {**answers, **supplied}
        locked = {}
        for field in profile['fields']:
            if not field.get('input_required'):
                continue
            key, label = field['value_key'], field['label']
            value = next((combined[q] for q in (key, label, f'{label}을(를) 알려주시겠습니까?') if q in combined), '')
            if not value.strip():
                continue
            text = f'{label} {value.strip()}'
            identifier = 'SU' + sha256(text.encode()).hexdigest()[:12]
            sources.append({'source_id': identifier, 'filename': '사용자 입력', 'page': None, 'sheet': None,
                            'location': key, 'text': text, 'score': 1.0})
            draft[key] = cited(value.strip(), identifier)
            locked[key] = {'value': value.strip(), 'source_id': identifier}
        issues = inspect_form_values(draft, profile)
        missing = [field['label'] for field in profile['fields'] if field.get('input_required')
                   and field.get('required') and not draft[field['value_key']].strip()]
        result = {'status': 'needs_information' if missing else 'ready', 'instruction': instruction,
                  'answers': combined, 'brief': {'목적': instruction, '보고 대상': 'RA 담당자',
                    '보고서 유형': '결과보고서', '마감': '', '분량': '', '부족한 정보': missing,
                    '질문': [f'{label}을(를) 알려주시겠습니까?' for label in missing[:2]]},
                  'documents': [document], 'sources': sources, 'draft': draft, 'locked_fields': locked,
                  'template_profile': profile, 'domain': 'pharmaceutical_ra',
                  'ra_workflow': record['ra_workflow'], 'document_kind': record['document_kind'],
                  'demo_source': source_record, 'questions': [f'{label}을(를) 알려주시겠습니까?' for label in missing[:2]],
                  'actual_model_requests': 0, 'actual_model_responses': 0}
        result['review'] = review_result(result)
        if not missing and (issues or result['review']['blocking']):
            result['status'] = 'needs_revision'
        result['metrics'] = record_revision(previous_metrics, draft) if previous_metrics else start_metrics(draft)
    run_id = (previous_metrics or {}).get('run_id') or uuid4().hex
    if 'metrics' in result:
        result['metrics']['run_id'] = run_id
        result['metrics']['mvp_form_id'] = form_id
        result['metrics']['mvp_baseline_sha256'] = _baseline_hash(result['metrics'])
    elif previous_metrics:
        result['metrics'] = deepcopy(previous_metrics)
    result.update(run_id=run_id, form_id=form_id, form_record=record,
                  mode='rules_demo' if demo else 'live', notice=DEMO_NOTICE if demo else '실제 AI 작성 경로임. 최종 원자료 검수와 사용자 확인이 필요함.',
                  submission_ready=False, generation_seconds=perf_counter()-started,
                  template_paths={'pdf': str(path)}, missing_form_fields=[field['label'] for field in profile['fields']
                      if not result.get('draft', {}).get(field['value_key'], '').strip()])
    return result
