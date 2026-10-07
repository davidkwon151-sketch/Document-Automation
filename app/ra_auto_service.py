"""One selected form + confirmed data -> bulk RA drafting and guarded filling."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from agent.brief import model_profile, profile_fields
from agent.metrics import start_metrics, record_revision
from agent.multimodal_intake import validate_generation_source
from agent.pipeline import run_pipeline, review_result, build_downloads
from agent.ra import RA_WORKFLOWS
from agent.ra_autofill import propose_ra_bindings, PROTECTED
from agent.ra_workflows import _bound_quote, _source_records, prepare_ra_workflow
from agent.template_learning import reusable_profile
from templates import load_form_profile, fill_compatible_template
from templates.value_rules import mapped_rule_profile
from agent.output_check import verify_output
from agent.retrieve import find_conflicts


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _sources(sources):
    records = list(_source_records(sources).values())
    for source in records:
        if 'verification_fingerprint' in source or 'verification_receipt' in source:
            validate_generation_source(source)
        _bound_quote({'quote': source.get('text')}, source)
    return records


def target_profile(template_path, profile, selected_keys, *, product_name, variant, ra_workflow=None):
    """Strengthen selected blanks without weakening original permissions/rules."""
    template = Path(template_path)
    clean = model_profile(profile)
    _require(template.is_file() and clean and clean.get('source_sha256') == sha256(template.read_bytes()).hexdigest(),
             '현재 양식 원본과 확인 프로파일 SHA가 다름')
    _require(clean.get('format') == template.suffix.lower().lstrip('.'), '양식 형식과 프로파일이 다름')
    registered = load_form_profile(template, clean.get('entry_id'))
    if registered is not None:
        # Domain/product are user selection, not changes to field authority.
        authority = deepcopy(registered)
        for key in ('domain', 'ra_workflow', 'ra_product_name', 'ra_product_variant'):
            if key in clean and key not in authority:
                authority[key] = clean[key]
        _require(reusable_profile(clean, authority), '등록 양식의 원위치·직접 입력·선택·수치 제약을 변경할 수 없음')
    else:
        _require(clean.get('configured') is True and clean.get('auto_mapping_confirmed') is True,
                 '처음 보는 양식의 실제 입력 위치·매핑을 먼저 확인해야 함')
    fields = profile_fields(clean)
    keyed = {field['value_key']: field for field in fields}
    _require(len(keyed) == len(fields), '입력칸마다 고유 값 이름이 필요함; 반복 위치를 합칠 수 없음')
    _require(isinstance(selected_keys, list) and selected_keys and all(isinstance(k, str) for k in selected_keys)
             and len(set(selected_keys)) == len(selected_keys) and not set(selected_keys) - keyed.keys(),
             '이번에 작성할 실제 항목을 하나 이상 중복 없이 선택해야 함')
    _require(isinstance(product_name, str) and product_name.strip() and isinstance(variant, str),
             '선택 제품명과 명시된 제형/함량 문자열이 필요함; 없으면 빈 문자열로 유지함')
    workflow = ra_workflow or clean.get('ra_workflow') or 'product_approval'
    _require(workflow in RA_WORKFLOWS and clean.get('domain') in (None, 'pharmaceutical_ra')
             and clean.get('ra_workflow') in (None, workflow), '원본 양식과 선택 RA 업무가 다름')
    required = {k for k, field in keyed.items() if field.get('required', True)}
    targets = set(selected_keys) | required
    dependencies = set()
    for relation in clean.get('constraints', {}).get('relations', []):
        for key in ('total', 'left', 'right', 'start', 'end', 'year', 'month', 'day'):
            if key in relation:
                dependencies.add(relation[key])
        dependencies.update(relation.get('parts', []))
    for group in clean.get('constraints', {}).get('groups', []):
        dependencies.update(group['fields'])
    kept = targets | dependencies
    runtime = mapped_rule_profile(clean, {field['id']: field['value_key'] for field in fields if field['value_key'] in kept})
    for field in runtime['fields']:
        if field['value_key'] in targets:
            field['required'] = True
        if PROTECTED.search(field['label']) or field['label'].strip().casefold() in {
                'name', 'full name', 'first name', 'last name', 'applicant', 'applicant name',
                'signature', 'consent', 'email', 'contact', 'representative'}:
            field.update(input_required=True, input_mode='user_provided', narrative_style_required=False)
    runtime.update(domain='pharmaceutical_ra', ra_workflow=workflow, ra_product_name=product_name,
                   ra_product_variant=variant, citation_mode='sidecar', auto_target_keys=sorted(targets),
                   auto_requested_keys=deepcopy(selected_keys))
    runtime.setdefault('document_kind', 'application')
    return runtime


def propose_ra_auto(template_path, profile, sources, selected_keys, *, product_name, variant, ra_workflow=None):
    runtime = target_profile(template_path, profile, selected_keys, product_name=product_name,
                             variant=variant, ra_workflow=ra_workflow)
    records = _sources(sources)
    proposal = propose_ra_bindings(runtime, records)
    proposal['source_bindings'] = {k: v for k, v in proposal['source_bindings'].items() if k in runtime['auto_target_keys']}
    proposal['fingerprint'] = _digest({'profile': runtime, 'sources': records})
    proposal['target_keys'] = runtime['auto_target_keys']
    return proposal


def _fingerprint(result):
    return _digest({k: result.get(k) for k in ('auto', 'draft', 'sources', 'template_profile', 'locked_fields',
          'instruction', 'answers', 'brief', 'grounding', 'completeness', 'semantic_required', 'completeness_required')})


def _target_check(result):
    targets = result['template_profile']['auto_target_keys']
    draft = result.get('draft', {})
    missing = [key for key in targets if not draft.get(key, '').strip()]
    outside = [f['value_key'] for f in result['template_profile']['fields']
               if f['value_key'] not in targets and draft.get(f['value_key'], '').strip()]
    result['target_coverage'] = {'target_count': len(targets), 'filled_count': len(targets) - len(missing),
                                 'missing_keys': missing, 'unselected_filled_keys': outside,
                                 'requested_keys': result['template_profile']['auto_requested_keys'],
                                 'scope': '이번 선택 항목과 원본 필수 항목; 기관의 법정 필수 전체 분모 아님'}
    extra = [{'code': 'auto_target_missing', 'field': key, 'severity': 'error', 'message': '이번 작성 대상 항목이 비어 있음'} for key in missing]
    extra += [{'code': 'auto_unselected_filled', 'field': key, 'severity': 'error', 'message': '작성 대상으로 선택하지 않은 관계 항목이 채워짐; 작성 대상을 확인해야 함'} for key in outside]
    result['all_source_conflicts'] = find_conflicts(result['auto']['source_records'])
    if result['all_source_conflicts']:
        extra.append({'code': 'auto_all_source_conflict', 'field': '자료/양식', 'severity': 'error',
                      'message': '검색에 선택되지 않은 첨부를 포함해 같은 항목·단위·기간의 수치가 상충함; 원자료 확인 필요함'})
    result['retrieval_scope'] = {'uploaded_source_count': len(result['auto']['source_records']),
          'retrieved_source_count': len([s for s in result.get('sources', []) if s.get('filename') != '사용자 입력']),
          'live_search_limit': 40 if result['auto']['mode'] == 'live' else None,
          'scope': 'AI 검색은 현재 상위 최대 40개 조각; 누락 작성 대상은 출력 차단. 원문 후보 검색은 첨부 전체를 대조함'}
    if 'draft' in result:
        result['review']['blocking'] |= bool(extra)
        result['review']['warnings' if result['auto']['mode'] == 'live' else 'issues'].extend(extra)
        result['status'] = 'needs_revision' if result['review']['blocking'] else 'ready'
    result['ready_for_output_check'] = 'draft' in result and not result.get('review', {}).get('blocking', True)


class _Calls:
    def __init__(self, client):
        self.client, self.attempts = client, 0

    def generate_json(self, *args, **kwargs):
        self.attempts += 1
        return self.client.generate_json(*args, **kwargs)

    def embed(self, *args, **kwargs):
        self.attempts += 1
        return self.client.embed(*args, **kwargs)


def generate_ra_auto(instruction, template_path, profile, sources, selected_keys, *, product_name, variant,
                     mode='live', client=None, field_values=None, confirmed_proposals=None, answers=None,
                     previous_metrics=None, ra_workflow=None):
    """Bulk source copy or actual AI pipeline; failures never switch modes."""
    _require(mode in {'live', 'source_copy'}, '실제 AI 작성 또는 원문 기입을 명시해야 함')
    _require(isinstance(instruction, str) and instruction.strip(), '작성 목적과 지시를 입력해야 함')
    records = _sources(sources)
    runtime = target_profile(template_path, profile, selected_keys, product_name=product_name,
                             variant=variant, ra_workflow=ra_workflow)
    direct = {} if field_values is None else deepcopy(field_values)
    allowed = {f['value_key'] for f in runtime['fields'] if f.get('input_required')}
    _require(isinstance(direct, dict) and not set(direct) - allowed and all(isinstance(v, str) for v in direct.values()),
             '개인정보·선택·서명 등 선언된 직접 입력만 담당자가 제공해야 함')
    supplied_answers = {} if answers is None else answers
    grounded = {q for f in runtime['fields'] if not f.get('input_required')
                for q in (f['value_key'], f['label'], f"{f['label']}을(를) 알려주시겠습니까?")}
    _require(isinstance(supplied_answers, dict) and not grounded & supplied_answers.keys(),
             '제품 사실 항목을 사용자 답변으로 원자료 대신 채울 수 없음')
    attempted = 0
    if mode == 'source_copy':
        proposal = propose_ra_auto(template_path, profile, records, selected_keys,
            product_name=product_name, variant=variant, ra_workflow=ra_workflow)
        _require(confirmed_proposals == proposal['fingerprint'], '현재 일괄 후보의 전체 원문·제품·위치를 먼저 확인해야 함')
        result = prepare_ra_workflow(runtime, records, proposal['source_bindings'], direct)
        result['review'].setdefault('issues', [])
        result['instruction'], result['answers'] = instruction, deepcopy(supplied_answers)
        # The existing report KPI needs title/summary/body and actual AI output.
        # A flat original-source copy is not a fabricated AI baseline.
        result['metrics'] = None
    else:
        _require(client is not None, '실제 AI 작성은 명시적으로 구성한 LLMClient가 필요함')
        counter = _Calls(client)
        result = run_pipeline(instruction, source_records=records, answers=supplied_answers,
            field_values=direct, client=counter, template_profile=runtime,
            ra_workflow=runtime['ra_workflow'], document_kind=runtime.get('document_kind', 'application'),
            previous_metrics=previous_metrics, semantic_review=True)
        attempted = counter.attempts
        # User instructions/brief may describe a value, but cannot become product evidence.
        forbidden = [s for s in result.get('sources', []) if s.get('filename') == '사용자 입력' and s.get('location') not in allowed]
        _require(not forbidden and not set(result.get('locked_fields', {})) - allowed,
                 '사용자 지시·모델 추정 제품 사실을 새 원자료로 승인할 수 없음')
    result['auto'] = {'template_path': str(Path(template_path).resolve()), 'original_profile': model_profile(profile),
                      'selected_keys': deepcopy(selected_keys), 'product_name': product_name, 'variant': variant,
                      'ra_workflow': runtime['ra_workflow'], 'source_records': records, 'field_values': direct,
                      'confirmed_proposals': confirmed_proposals, 'mode': mode}
    result.setdefault('template_profile', runtime)
    result.update(mode=mode, submission_ready=False, model_request_attempts=attempted,
                  actual_model_requests=attempted if getattr(client, 'provider', None) in {'gemini', 'openai', 'local'} else 0,
                  human_kpi_measured=False, notice='선택 원문의 일괄 기입; AI 생성 아님' if mode == 'source_copy' else '실제 AI 작성 경로; 원자료 검수와 최종 담당자 확인 필요함')
    _target_check(result)
    result['auto_fingerprint'] = _fingerprint(result)
    return result


def _validate(result):
    _require(result.get('auto_fingerprint') == _fingerprint(result), '초안·원자료·양식·검수 내용이 변경됨; 다시 작성 또는 검수해야 함')
    _require(isinstance(result.get('draft'), dict), '작성된 초안이 없음; 부족한 원자료·직접 입력을 보완해야 함')
    data = result['auto']
    _sources(data['source_records'])
    runtime = target_profile(data['template_path'], data['original_profile'], data['selected_keys'],
              product_name=data['product_name'], variant=data['variant'], ra_workflow=data['ra_workflow'])
    actual = {k: v for k, v in result['template_profile'].items() if k not in {'ra_context', 'document_context'}}
    expected = {k: v for k, v in runtime.items() if k not in {'ra_context', 'document_context'}}
    _require(actual == expected, '작성 시 선택한 양식·항목·권한·제품 범위가 다름')
    if data['mode'] == 'source_copy':
        proposal = propose_ra_auto(data['template_path'], data['original_profile'], data['source_records'], data['selected_keys'],
                   product_name=data['product_name'], variant=data['variant'], ra_workflow=data['ra_workflow'])
        rebuilt = prepare_ra_workflow(runtime, data['source_records'], proposal['source_bindings'], data['field_values'])
        _require(proposal['fingerprint'] == data['confirmed_proposals'] and rebuilt['draft'] == result['draft']
                 and rebuilt['sources'] == result['sources'], '일괄 확인한 원문 기입 결과가 현재 근거와 다름')
        result['review'] = rebuilt['review']
    else:
        result['review'] = review_result(result)
    _target_check(result)


def review_ra_auto(result, draft, *, client=None):
    """Explicit edit review; stale content never keeps previous download approval."""
    _validate(deepcopy(result))
    _require(result['auto']['mode'] == 'live', '원문 기입 모드는 후보·원자료를 다시 확인해야 함; 임의 문장을 자동 승인하지 않음')
    _require(client is not None, '수정한 AI 초안은 실제 의미·완결성 검수가 필요함')
    updated = deepcopy(result)
    updated['draft'] = deepcopy(draft)
    for key in ('output_verification', 'output_hashes', 'native_output_verification', '_native_preview_bytes'):
        updated.pop(key, None)
    counter = _Calls(client)
    updated['review'] = review_result(updated, client=counter)
    updated['metrics'] = record_revision(result['metrics'], updated['draft'])
    updated['model_request_attempts'] += counter.attempts
    updated['actual_model_requests'] += counter.attempts if getattr(client, 'provider', None) in {'gemini', 'openai', 'local'} else 0
    _target_check(updated)
    updated['auto_fingerprint'] = _fingerprint(updated)
    return updated


def export_ra_auto(result, *, confirmed=False, native_review='off'):
    """Current form filling and saved-file checks, with separate source evidence."""
    _require(confirmed is True, '최종 내용과 원문 근거 확인이 필요함')
    checked = deepcopy(result)
    _validate(checked)
    _require(checked['ready_for_output_check'], '선택 항목·필수·출처·수치·의미 검수 오류를 해결해야 함')
    template = Path(checked['auto']['template_path'])
    profile = checked['template_profile']
    if checked['auto']['mode'] == 'live':
        outputs = build_downloads(checked, confirmed=True, template_paths={template.suffix[1:]: template}, native_review=native_review)
        data = outputs[template.suffix[1:]]
        proof = checked['output_verification'][template.suffix[1:]]
    else:
        _require(native_review == 'off', '원문 기입의 추가 네이티브 출력 확인은 기존 공식 기입 화면에서 사용해야 함')
        from agent.field_citations import split_field_citations, profile_field
        literals = {k: v['value'] for k, v in checked['locked_fields'].items()}
        values = {k: split_field_citations(v, profile_field(profile, k), literal=literals.get(k))[0] for k, v in checked['draft'].items()}
        with tempfile.TemporaryDirectory(prefix='ra-auto-output-') as folder:
            output = Path(folder) / ('작성본' + template.suffix)
            fill_compatible_template(template, values, output, profile=profile)
            proof = verify_output(template, output, values, profile=profile)
            data = output.read_bytes()
        _require(proof.get('status') == 'passed' and proof.get('sha', {}).get('output') == sha256(data).hexdigest()
                 and proof.get('sha', {}).get('template') == profile['source_sha256'], '현재 저장 파일과 원위치 검수 증거가 다름')
        checked['output_verification'] = {template.suffix[1:]: proof}
    return {'document': data, 'filename': 'RA_작성본' + template.suffix,
            'evidence': json.dumps({k: v for k, v in checked.items() if k != '_native_preview_bytes'},
                                   ensure_ascii=False, indent=2).encode('utf-8'),
            'output_verification': proof, 'submission_ready': False}
