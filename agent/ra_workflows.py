"""Prepare exact, source-bound RA form values without an API or guessed facts.

This is deterministic field preparation, not AI writing, submission approval,
or a replacement for the original-position export and independent output checks.
Only explicit, confirmed field mappings are accepted.
"""

from copy import deepcopy
from hashlib import sha256
import json
import re

from agent.brief import model_profile, profile_fields
from agent.grounding import evidence_fingerprint
from agent.field_citations import is_selection_field, split_field_citations
from agent.ra import RA_WORKFLOWS, inspect_ra_draft
from templates.value_rules import inspect_form_values


def _source_records(sources):
    if not isinstance(sources, list):
        raise ValueError('원자료는 파서·검색 출처 목록이어야 함')
    records = {}
    for original in sources:
        if not isinstance(original, dict):
            raise ValueError('출처는 객체이어야 함')
        source = deepcopy(original)
        identifier = source.get('source_id')
        if not isinstance(identifier, str) or not re.fullmatch(r'S[A-Za-z0-9_-]+', identifier) or identifier in records:
            raise ValueError('원자료 출처 ID가 잘못되거나 중복됨')
        records[identifier] = source
    return records


def _bound_quote(binding, source):
    from agent.multimodal_intake import validate_source_origin

    validate_source_origin(source)
    if not isinstance(binding, dict) or set(binding) - {'source_id', 'quote', 'start'}:
        raise ValueError('기입 연결에는 출처 ID·전체 인용·선택적 시작 위치만 허용함')
    quote, text = binding.get('quote'), source.get('text')
    if not isinstance(quote, str) or not quote.strip() or not isinstance(text, str):
        raise ValueError('비어 있지 않은 전체 인용과 원문이 필요함')
    if source.get('requires_verification') or source.get('uncertain_items'):
        raise ValueError('사용자 입력·미확인 OCR을 원자료 항목으로 승격할 수 없음')
    if (not isinstance(source.get('document_sha256'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', source['document_sha256'])
            or not isinstance(source.get('filename'), str) or not source['filename'].strip()):
        raise ValueError('파서가 확인한 원문 파일명·SHA가 필요함')
    page, sheet, location = source.get('page'), source.get('sheet'), source.get('location')
    if not (type(page) is int and page > 0 or isinstance(sheet, str) and sheet.strip()
            or isinstance(location, str) and location.strip()):
        raise ValueError('원문의 페이지·시트 또는 실제 문단 위치가 필요함')
    start = binding.get('start')
    if start is None:
        if text.count(quote) != 1:
            raise ValueError('인용이 원문과 다르거나 여러 곳에 존재함; 정확한 시작 위치 필요함')
        start = text.index(quote)
    if type(start) is not int or start < 0 or text[start:start + len(quote)] != quote:
        raise ValueError('인용 문자 범위가 실제 원문과 다름')
    if any(key in source for key in ('context_text', 'context_start', 'context_end')):
        context, begin, end = (source.get(key) for key in ('context_text', 'context_start', 'context_end'))
        if (not isinstance(context, str) or type(begin) is not int or type(end) is not int
                or not 0 <= begin < end <= len(context) or context[begin:end] != text):
            raise ValueError('검색 조각과 전체 원문 블록의 문자 범위가 다름')
    return quote, start


def prepare_ra_workflow(profile, sources, source_bindings=None, direct_values=None):
    """Return a flat cited draft, missing questions and pre-export checks.

    ``profile`` must come from the registered/confirmed form resolver;
    ``sources`` must come from the file parser/search (not model-generated data).
    ``source_bindings`` maps value_key to {source_id, quote, optional start}.
    An unchanged source copy and field-local character span are kept in evidence.
    Direct values are recorded as user-input sources only for declared input fields.
    ``ready_for_output_check`` still requires user confirmation, original SHA and
    the normal original-position export/independent output checks before download.
    """
    cleaned = model_profile(profile)
    if not cleaned or cleaned.get('ra_workflow') not in RA_WORKFLOWS:
        raise ValueError('등록·확인된 RA 업무 양식이 필요함')
    fields = profile_fields(cleaned)
    if not fields or len({f['value_key'] for f in fields}) != len(fields):
        raise ValueError('입력칸별 고유 값 이름이 필요함; 반복 칸을 합칠 수 없음')
    bindings = {} if source_bindings is None else source_bindings
    direct = {} if direct_values is None else direct_values
    if not isinstance(bindings, dict) or not isinstance(direct, dict):
        raise ValueError('항목 연결과 직접 입력은 객체이어야 함')
    keyed = {f['value_key']: f for f in fields}
    if set(bindings) - keyed.keys() or set(direct) - keyed.keys() or set(bindings) & set(direct):
        raise ValueError('등록되지 않은 항목·중복 기입을 허용하지 않음')
    records = _source_records(sources)
    draft = {key: '' for key in keyed}
    evidence, locked = {}, {}
    for key, binding in bindings.items():
        if keyed[key].get('input_required'):
            raise ValueError('사용자 직접 입력·서명·선택 항목을 원자료로 자동 기입할 수 없음')
        if not isinstance(binding, dict) or binding.get('source_id') not in records:
            raise ValueError('기입할 출처 ID를 실제 자료에서 확인할 수 없음')
        source = records[binding['source_id']]
        quote, start = _bound_quote(binding, source)
        draft[key] = '\n'.join(line + ' [' + source['source_id'] + ']' if line.strip() else line
                               for line in quote.split('\n'))
        evidence[key] = {'source_id': source['source_id'], 'quote': quote, 'start': start,
                         'end': start + len(quote), 'source': deepcopy(source),
                         'field_id': keyed[key].get('id'), 'label': keyed[key]['label']}
    for key, value in direct.items():
        if not keyed[key].get('input_required') or not isinstance(value, str):
            raise ValueError('직접 입력은 선언된 사용자 입력 항목의 문자열만 허용함')
        if not value.strip():
            continue  # A blank choice is not consent, clearing, or the first option.
        text = keyed[key]['label'] + ' ' + value
        identifier = 'SU' + sha256((key + '\0' + text).encode()).hexdigest()[:20]
        if identifier in records:
            raise ValueError('원자료와 사용자 입력 출처 ID가 충돌함')
        records[identifier] = {'source_id': identifier, 'filename': '사용자 입력',
                               'location': key, 'page': None, 'sheet': None, 'text': text}
        draft[key] = '\n'.join(line + ' [' + identifier + ']' if line.strip() else line
                               for line in value.split('\n'))
        locked[key] = {'value': value, 'source_id': identifier}
    literals = {key: item['value'] for key, item in locked.items()}
    constraints = inspect_form_values(draft, cleaned, literal_values=literals)
    for key, field in keyed.items():
        plain, _ = split_field_citations(draft[key], field, literal=literals.get(key))
        if field.get('max_chars') is not None and len(plain) > field['max_chars']:
            constraints.append({'code': 'ra_field_length', 'field': key, 'severity': 'error',
                                'message': '원래 입력칸의 등록 분량을 넘음; 내용 삭제·글꼴 축소 없이 보완 필요함'})
    # Native choice codes (including [S999]) are data, not RA claims.
    # Their original option contract is validated above; never feed the code to
    # the narrative citation/medical-claim parser as a fabricated source ID.
    selections = {key for key, field in keyed.items() if is_selection_field(field)}
    narrative_profile = deepcopy(cleaned)
    for field in narrative_profile['fields']:
        if field['value_key'] in selections:
            field['required'] = False  # Original required/relations already checked above.
    ra_review = inspect_ra_draft({key: value for key, value in draft.items() if key not in selections},
                                list(records.values()), profile=narrative_profile)
    missing = [{'value_key': key, 'label': field['label'],
                'input_kind': 'direct' if field.get('input_required') else 'source'}
               for key, field in keyed.items() if field.get('required', True) and not draft[key].strip()]
    questions = [f"{item['label']}의 " + ('직접 입력값을 알려주시겠습니까?' if item['input_kind'] == 'direct'
                 else '원자료와 해당 인용 위치를 제공해 주시겠습니까?') for item in missing[:2]]
    blocking = bool(missing or constraints or ra_review['blocking'])
    return {'mode': 'verified_field_copy', 'draft': draft, 'sources': list(records.values()),
            'locked_fields': locked, 'evidence': evidence, 'template_profile': cleaned,
            'ra_workflow': cleaned['ra_workflow'], 'missing_fields': missing, 'questions': questions,
            'review': {'blocking': blocking, 'issues': constraints + ra_review['issues']},
            'status': 'needs_information' if missing else 'needs_revision' if blocking else 'ready_for_output_check',
            'ready_for_output_check': not blocking, 'submission_ready': False,
            'actual_model_requests': 0,
            'fingerprint': sha256(json.dumps({'evidence': evidence_fingerprint(draft, list(records.values()),
                template_profile=cleaned, literal_values=literals), 'profile': cleaned,
                'bindings': evidence}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
