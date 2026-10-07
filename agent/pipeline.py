"""M4 -> M5 -> M6 -> M7 -> M8 orchestration; M2 runs after confirmation."""

from __future__ import annotations

import tempfile
from time import perf_counter
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from importlib import import_module

from agent.boss_review import boss_review
from agent.brief import analyze_brief, profile_fields
from agent.draft import create_draft
from agent.metrics import start_metrics, record_revision
from agent.grounding import inspect_grounding, evidence_fingerprint
from agent.completeness import check_completeness, completeness_fingerprint
from agent.retrieve import find_conflicts, load_documents, retrieve
from agent.review import review_draft, CITATION_PATTERN
from agent.field_citations import field_citations, profile_field, split_field_citations
from agent.documents import document_context, style_exempt_fields
from templates.pdf_annex import draft_limit, annex_enabled
from templates.value_rules import inspect_form_values


DOMAIN_MODULES = {
    'pharmaceutical_ra': ('agent.ra', 'RA_WORKFLOWS', 'ra'),
    'business_support': ('agent.business', 'BUSINESS_WORKFLOWS', 'business'),
    'office_finance': ('agent.office', 'OFFICE_WORKFLOWS', 'office'),
}


def _workflow_context(profile, **selected):
    profile = profile or {}
    choices = {prefix: value or profile.get(prefix + '_workflow') for prefix, value in selected.items()}
    choices = {prefix: value for prefix, value in choices.items() if value is not None}
    if len(choices) > 1:
        raise ValueError('한 문서에는 하나의 업무 분야를 선택해야 함. 원본 양식의 업무와 선택을 확인해야 함')
    if not choices:
        if profile.get('domain') in DOMAIN_MODULES:
            raise ValueError('등록된 전문 양식의 업무 유형을 선택해야 함')
        return profile or None, {'domain': 'general'}
    prefix, workflow = next(iter(choices.items()))
    domain, (module_name, constant, _) = next((domain, config) for domain, config in DOMAIN_MODULES.items() if config[2] == prefix)
    if profile.get('domain') in DOMAIN_MODULES and profile['domain'] != domain:
        raise ValueError('선택한 업무 분야와 원본 양식의 업무 분야가 다름')
    workflows = getattr(import_module(module_name), constant)
    if not isinstance(workflow, str) or workflow not in workflows:
        raise ValueError(f'등록되지 않은 업무 유형임: {prefix.upper()}')
    from llm.client import load_prompt
    enriched = {**profile, 'domain': domain, prefix + '_workflow': workflow,
                prefix + '_context': {**workflows[workflow], 'guidance': load_prompt(prefix + '.md')}}
    return enriched, {'domain': domain, prefix + '_workflow': workflow}


def run_pipeline(instruction, paths=None, *, documents=None, source_records=None, answers=None, client=None,
                 template_profile=None, field_values=None, preferences=None, previous_metrics=None, semantic_review=False,
                 document_cache=None, ra_workflow=None, business_workflow=None, office_workflow=None, document_kind=None):
    started = perf_counter()
    # Confirmed multimodal chunks keep their exact original receipt and context;
    # rechunking a transcription would discard the user's original-file check.
    if source_records is not None:
        from agent.ra_workflows import _source_records, _bound_quote
        from agent.multimodal_intake import validate_generation_source
        if paths:
            raise ValueError('확인된 원자료와 새 파일 경로를 동시에 지정할 수 없음')
        source_records = list(_source_records(source_records).values())
        for source in source_records:
            if 'verification_fingerprint' in source or 'verification_receipt' in source:
                validate_generation_source(source)
            _bound_quote({'quote': source.get('text')}, source)
    template_profile, domain_context = _workflow_context(template_profile, ra=ra_workflow,
                                                       business=business_workflow, office=office_workflow)
    template_profile, kind = document_context(template_profile, document_kind)
    domain_context['document_kind'] = kind
    specialized = domain_context['domain'] != 'general'
    fields = profile_fields(template_profile)
    supplied = field_values or {}
    allowed = {field['value_key'] for field in fields}
    if not isinstance(supplied, dict) or any(key not in allowed or not isinstance(value, str) for key, value in supplied.items()):
        raise ValueError("사용자 양식 값은 등록된 항목의 문자열이어야 함")
    if answers is not None and (not isinstance(answers, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in answers.items())):
        raise ValueError('보완 답변은 질문과 답변 문자열의 객체여야 함')
    combined_answers = {**(answers or {}), **supplied}
    provided = {}
    for field in fields:
        key, label = field['value_key'], field['label']
        for question in (key, label, f'{label}을(를) 알려주시겠습니까?'):
            if question in combined_answers:
                provided[key] = combined_answers[question]
                break
        if field.get('control_type') == 'choice_unresolved' and provided.get(key, '').strip():
            raise ValueError(f'{label}: 원본 선택 목록을 확인할 수 없어 기입을 보류함')
    input_fields = [dict(field, required=False) for field in fields]
    direct = {field['value_key'] for field in fields if field.get('input_required')}
    groups = [group for group in (template_profile or {}).get('constraints', {}).get('groups', [])
              if set(group['fields']) <= direct]
    # Validate only actual supplied values before model/parsing costs. Missing
    # grounded fields and required blanks continue to the normal brief questions.
    issues = inspect_form_values(provided, {'fields': input_fields, 'constraints': {'groups': groups}}, literal_values=provided)
    if issues:
        raise ValueError('직접 입력값을 확인해야 함: ' + ' / '.join(f"{item['field']}: {item['message']}" for item in issues))
    if client is None:
        from llm.client import LLMClient

        client = LLMClient()
    context = {"template_profile": template_profile} if template_profile is not None else {}
    brief = analyze_brief(instruction, client=client, answers=combined_answers, **context)
    brief.update(domain_context)
    explicit_origins = {}
    for field in fields:
        key, label = field['value_key'], field['label']
        matching = next((answer for question, answer in combined_answers.items() if question in {key, label, f'{label}을(를) 알려주시겠습니까?'} and answer.strip()), None)
        extracted = brief.get('양식 항목', {}).get(key, '')
        if matching:
            brief.setdefault('양식 항목', {})[key] = matching.strip()
            explicit_origins[key] = f'{label} {matching.strip()}'
        elif extracted and extracted in instruction:
            # Preserve the real sentence so a wrongly inferred field context is not evidence.
            explicit_origins[key] = instruction
        elif extracted:
            brief['양식 항목'].pop(key, None)
        if field.get('input_required') and field.get('required', True) and key not in explicit_origins:
            if label not in brief['부족한 정보']:
                brief['부족한 정보'].append(label)
    if brief['부족한 정보'] and not brief['질문']:
        brief['질문'] = [f'{label}을(를) 알려주시겠습니까?' for label in brief['부족한 정보'][:2]]
    if brief["부족한 정보"]:
        return {"status": "needs_information", "brief": brief, "questions": brief["질문"],
                'instruction': instruction, 'answers': combined_answers,
                **context, **domain_context}
    if documents is not None:
        parsed = documents
    elif document_cache is not None:
        if not isinstance(document_cache, dict):
            raise ValueError('자료 캐시는 객체여야 함')
        base = Path(__file__).resolve().parents[1]
        parser_version = hashlib.sha256(b''.join((base / name).read_bytes() for name in ('parsers/extract.py', 'parsers/extended.py', 'parsers/legacy.py', 'parsers/hwp.py', 'parsers/pptx.py', 'llm/client.py', 'prompts/ocr.md'))).hexdigest()
        key_data = {'files': [(Path(path).name, hashlib.sha256(Path(path).read_bytes()).hexdigest()) for path in paths or []],
                    'parser': parser_version, 'ocr_model': getattr(client, 'model', 'injected')}
        cache_key = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
        if cache_key not in document_cache:
            document_cache[cache_key] = load_documents(paths or [], client=client)
            while len(document_cache) > 4:
                document_cache.pop(next(iter(document_cache)))
        parsed = deepcopy(document_cache[cache_key])
    else:
        parsed = load_documents(paths or [], client=client)
    query = f"{brief['목적']} {brief['보고서 유형']}"
    if specialized:
        prefix = DOMAIN_MODULES[domain_context['domain']][2]
        query += ' ' + template_profile[prefix + '_context']['title']
    if fields:
        query += " " + " ".join(field['label'] for field in fields if not field.get('input_required'))
    if source_records is None:
        sources = retrieve(query, parsed, client=client, top_k=max(6, min(40, len(fields) * 2)))
    else:
        from agent.retrieve import search_chunks
        sources = search_chunks(query, source_records, client.embed, top_k=max(6, min(40, len(fields) * 2)))
    # User-supplied form metadata is evidence with its own origin, never silently invented.
    user_sources = []
    field_source_ids = {}
    for key, value in brief.get("양식 항목", {}).items():
        if not value.strip():
            continue
        field = next(field for field in fields if field['value_key'] == key)
        if key not in explicit_origins:
            continue
        text = explicit_origins[key]
        source_id = "SU" + hashlib.sha256(text.encode()).hexdigest()[:12]
        field_source_ids[key] = source_id
        if any(source['source_id'] == source_id for source in user_sources):
            continue
        user_sources.append({"source_id": source_id, "filename": "사용자 입력", "page": None,
                             "sheet": None, "location": key, "text": text, "score": 1.0})
    sources.extend(user_sources)
    if field_source_ids:
        brief['양식 항목 출처'] = field_source_ids
    if not sources:
        return {"status": "needs_evidence", "brief": brief, "documents": parsed, "sources": [],
                'instruction': instruction, 'answers': combined_answers,
                **context, **domain_context,
                "message": "관련 자료를 첨부하거나 양식의 직접 입력 정보를 제공해 주세요."}
    options = dict(context)
    if preferences is not None:
        options['preferences'] = preferences
    draft = create_draft(brief, sources, client=client, **options)
    locked_fields = {}
    for field in fields:
        key = field['value_key']
        if field.get('input_required') and key in field_source_ids:
            value = brief['양식 항목'][key]
            source_id = field_source_ids[key]
            draft[key] = '\n'.join(f'{line} [{source_id}]' if line.strip() else line for line in value.split('\n'))
            locked_fields[key] = {'value': value, 'source_id': source_id}
    exempt = style_exempt_fields(template_profile)
    citation_options = {'template_profile': template_profile,
                        'literal_values': {key: constraint['value'] for key, constraint in locked_fields.items()}}
    checked = review_draft(draft, sources, client=client, auto_correct=not specialized, style_exempt_fields=exempt, **citation_options)
    boss = boss_review(checked["draft"], brief, sources, client=client, template_profile=template_profile)
    final = review_draft(boss["draft"], sources, auto_correct=not specialized, style_exempt_fields=exempt, **citation_options)
    conflicts = find_conflicts(sources)
    result = {
        "status": "needs_revision" if final["blocking"] else "ready",
        "instruction": instruction,
        'answers': combined_answers,
        "brief": brief,
        "documents": parsed,
        "sources": sources,
        "draft": final["draft"],
        "review": final,
        "initial_review": checked,
        "boss_review": boss,
        "conflicts": conflicts,
        'locked_fields': locked_fields,
    }
    if template_profile is not None:
        result['template_profile'] = template_profile
    result.update(domain_context)
    if semantic_review:
        result['semantic_required'] = True
        result['grounding'] = inspect_grounding(final['draft'], sources, client, **citation_options)
    result['completeness_required'] = True
    result['completeness'] = check_completeness(final['draft'], brief, template_profile, client if semantic_review else None, instruction=instruction, answers=combined_answers)
    result['review'] = review_result(result)
    result['draft'] = result['review']['draft']
    result['status'] = 'needs_revision' if result['review']['blocking'] else 'ready'
    result['metrics'] = record_revision(previous_metrics, result['draft']) if previous_metrics else start_metrics(result['draft'])
    result['generation_seconds'] = perf_counter() - started
    return result


def validate_template_values(draft, profile, *, check_rules=True, literal_values=None):
    """Recheck required fields and size limits after manual edits as well as generation."""
    for field in profile_fields(profile):
        value = draft.get(field['value_key'], '')
        if field.get('required', True) and not value.strip():
            raise ValueError(f"필수 양식 항목이 비어 있음: {field['label']}")
        plain, _ = split_field_citations(value, field, literal=(literal_values or {}).get(field['value_key']))
        printed_value = plain if (profile or {}).get('citation_mode') == 'sidecar' else value
        if field.get('control_type') in {'checkbox', 'radio', 'choice', 'combobox'}:
            printed_value = plain
        maximum = draft_limit(profile, field)
        if maximum and len(printed_value) > maximum:
            raise ValueError(f"양식 항목 분량을 초과함: {field['label']}")
    constraints = (profile or {}).get('constraints', {})
    if constraints.get('summary_max_lines') and len(draft['요약'].splitlines()) > constraints['summary_max_lines']:
        raise ValueError("양식 요약 줄 수를 초과함")
    if constraints.get('body_max_chars') and len(draft['본문']) > constraints['body_max_chars']:
        raise ValueError("양식 본문 분량을 초과함")
    if check_rules:
        values = {key: split_field_citations(value, profile_field(profile, key), literal=(literal_values or {}).get(key))[0]
                  for key, value in draft.items()}
        issues = inspect_form_values(values, profile or {}, literal_values=literal_values)
        if issues:
            raise ValueError('양식 입력값 검증 실패: ' + ' / '.join(f"{issue['field']}: {issue['message']}" for issue in issues))


def review_result(result, draft=None, *, client=None):
    domain = result.get('domain', (result.get('template_profile') or {}).get('domain', 'general'))
    profile_domain = (result.get('template_profile') or {}).get('domain')
    if profile_domain in DOMAIN_MODULES:
        domain = profile_domain
    exempt = style_exempt_fields(result.get('template_profile'))
    literal_values = {key: constraint['value'] for key, constraint in result.get('locked_fields', {}).items()}
    citation_options = {'template_profile': result.get('template_profile'), 'literal_values': literal_values}
    checked = review_draft(draft if draft is not None else result['draft'], result['sources'], auto_correct=domain not in DOMAIN_MODULES, style_exempt_fields=exempt, **citation_options)
    extra = []
    if domain in DOMAIN_MODULES:
        module_name, _, prefix = DOMAIN_MODULES[domain]
        inspector = getattr(import_module(module_name), 'inspect_' + prefix + '_draft')
        workflow_key = prefix + '_workflow'
        domain_profile = {**(result.get('template_profile') or {}), workflow_key: result.get(workflow_key) or (result.get('template_profile') or {}).get(workflow_key)}
        result[prefix + '_checks'] = inspector(checked['draft'], result['sources'], profile=domain_profile)
        checked['warnings'].extend(result[prefix + '_checks']['issues'])
        checked['blocking'] |= result[prefix + '_checks']['blocking']
    for key, constraint in result.get('locked_fields', {}).items():
        value = checked['draft'].get(key, '')
        literal_lines = constraint['value'].split('\n')
        parts = [split_field_citations(line, profile_field(result.get('template_profile'), key),
                 literal=literal_lines[index] if index < len(literal_lines) else None)
                 for index, line in enumerate(value.split('\n'))]
        actual = '\n'.join(plain.rstrip() for plain, _ in parts).strip()
        ids = {identifier for _, identifiers in parts for identifier in identifiers}
        if actual != constraint['value'].strip() or ids != {constraint['source_id']}:
            extra.append(('user_input_changed', f'{key}: 사용자 직접 입력값이 변경됨. 작성 요청의 입력란에서 정정한 후 다시 작성해야 함'))
    if result.get('semantic_required'):
        fingerprint = evidence_fingerprint(checked['draft'], result['sources'], **citation_options)
        if client is not None:
            result['grounding'] = inspect_grounding(checked['draft'], result['sources'], client, **citation_options)
        grounding = result.get('grounding', {})
        if grounding.get('fingerprint') != fingerprint:
            extra.append(('semantic_stale', '수정된 내용의 의미 검수를 다시 실행해야 함'))
        else:
            checked['warnings'].extend(grounding.get('warnings', []))
            checked['blocking'] |= grounding.get('blocking', True)
    if result.get('completeness_required'):
        brief, profile = result.get('brief', {}), result.get('template_profile')
        original_request = {'instruction': result.get('instruction', ''), 'answers': result.get('answers', {})}
        if client is not None or not result.get('semantic_required'):
            result['completeness'] = check_completeness(checked['draft'], brief, profile, client if result.get('semantic_required') else None, **original_request)
        proof = result.get('completeness', {})
        if proof.get('fingerprint') != completeness_fingerprint(checked['draft'], brief, profile, **original_request):
            extra.append(('completeness_stale', '수정된 초안·지시·양식의 완결성을 다시 검수해야 함'))
        elif result.get('semantic_required') and not proof.get('semantic_checked'):
            extra.append(('completeness_failed', '완결성 의미 검수를 완료하지 못함. 다시 검수해야 함'))
        else:
            checked['warnings'].extend({**issue, 'code': 'completeness_' + issue['kind']} for issue in proof.get('issues', []))
            checked['blocking'] |= proof.get('blocking', True)
    try:
        conflicts = find_conflicts(result['sources'])
    except ValueError:
        extra.append(('source_context_invalid', '원자료와 연결된 문맥의 문자 범위가 일치하지 않음. 자료를 다시 첨부하고 검수해야 함'))
    else:
        if conflicts:
            extra.append(('source_conflict', '같은 항목·단위·기간의 원자료 수치가 서로 다름. 올바른 자료를 확인해 다시 첨부해야 함'))
    unverified = [source for source in result['sources'] if source.get('requires_verification') and source['source_id'] not in result.get('verified_source_ids', [])]
    if unverified:
        extra.append(('ocr_unverified', '추출이 불확실한 원자료의 수치·문장을 원본과 대조하고 출처 확인에 표시해야 함'))
    try:
        validate_template_values(checked['draft'], result.get('template_profile'), check_rules=False, literal_values=literal_values)
    except ValueError as exc:
        extra.append(('template_required', str(exc)))
    plain_values = {key: split_field_citations(value, profile_field(result.get('template_profile'), key), literal=literal_values.get(key))[0]
                    for key, value in checked['draft'].items()}
    value_issues = inspect_form_values(plain_values, result.get('template_profile') or {}, literal_values=literal_values)
    checked['warnings'].extend(value_issues)
    checked['blocking'] |= bool(value_issues)
    for code, message in extra:
        checked['warnings'].append({'code': code, 'field': '자료/양식', 'line': 0, 'message': message, 'severity': 'error'})
    checked['blocking'] = checked['blocking'] or bool(extra)
    return checked


def build_downloads(result, *, confirmed=False, template_paths=None, template_profiles=None, mappings=None, native_review='off'):
    from templates import fill_compatible_template
    from agent.output_check import verify_output

    # A failed new attempt must never leave an older export marked as checked.
    for key in ('output_verification', 'output_hashes', 'native_output_verification', '_native_preview_bytes'):
        result.pop(key, None)
    if not isinstance(native_review, str) or native_review not in {'off', 'auto', 'required'}:
        raise ValueError('문서 프로그램 출력 확인 모드가 잘못됨')
    if not confirmed:
        raise ValueError("최종 내용 확인이 필요함")
    if result.get("status") not in {"ready", "needs_revision"}:
        raise ValueError("작성된 초안이 없음")
    checked = review_result(result)
    if not result["sources"]:
        raise ValueError("관련 근거 자료를 첨부해야 함")
    if find_conflicts(result["sources"]):
        raise ValueError("원자료의 상충하는 수치를 먼저 확인해야 함")
    if checked["blocking"]:
        raise ValueError("수치·출처·필수 항목 오류를 해결해야 다운로드할 수 있음")
    if checked["draft"] != result["draft"]:
        raise ValueError("수정본을 다시 검수하고 확인해야 함")
    literal_values = {key: constraint['value'] for key, constraint in result.get('locked_fields', {}).items()}
    validate_template_values(checked['draft'], result.get('template_profile'), literal_values=literal_values)
    base = Path(__file__).resolve().parents[1] / "templates"
    default_name = 'generic_document' if result.get('document_kind', 'report') != 'report' else 'result_report'
    paths = template_paths or {suffix: base / f"{default_name}.{suffix}" for suffix in ("docx", "hwpx")}
    paths = {suffix: Path(template).resolve() for suffix, template in paths.items()}
    for suffix, template in paths.items():
        if suffix not in {"docx", "hwpx", "xlsx", "pdf", "pptx"} or template.suffix.lower() != f".{suffix}":
            raise ValueError("출력 형식과 양식 파일이 일치하지 않음")
    source_hashes = {suffix: hashlib.sha256(template.read_bytes()).hexdigest() for suffix, template in paths.items()}
    outputs, verification, native_checks, previews = {}, {}, {}, {}
    with tempfile.TemporaryDirectory(prefix="report-export-") as directory:
        for suffix, template in paths.items():
            output = Path(directory) / f"보고서.{suffix}"
            source_hash = source_hashes[suffix]
            profile = (template_profiles or {}).get(suffix)
            reviewed_profile = result.get('template_profile')
            if (reviewed_profile and reviewed_profile.get('fields')
                    and ('source_sha256' in reviewed_profile or 'format' in reviewed_profile)):
                if profile is None:
                    profile = reviewed_profile
                # Generation adds advisory prompt context. Writing permissions and
                # every selected input location must still match the reviewed form.
                context_keys = {'ra_context', 'business_context', 'office_context', 'document_context'}
                writing = {key: value for key, value in profile.items() if key not in context_keys}
                reviewed = {key: value for key, value in reviewed_profile.items() if key not in context_keys}
                for key in ('domain', 'ra_workflow', 'business_workflow', 'office_workflow', 'document_kind'):
                    if key in reviewed and key not in writing:
                        writing[key] = reviewed[key]
                if writing != reviewed:
                    raise ValueError('검수한 양식과 출력 프로파일이 다름. 변경한 입력칸·권한·범위를 다시 작성하고 검수해야 함')
                mapping = (mappings or {}).get(suffix)
                expected = {field['id']: field['value_key'] for field in reviewed_profile['fields']}
                if mapping is not None and mapping != expected:
                    raise ValueError('검수한 양식과 출력 매핑이 다름. 변경한 매핑을 다시 작성하고 검수해야 함')
            if annex_enabled(profile):
                by_id = {source['source_id']: source for source in result['sources']}
                notes = {}
                for key, value in checked['draft'].items():
                    notes[key] = []
                    for identifier in dict.fromkeys(field_citations(value, profile_field(profile, key), literal=literal_values.get(key))):
                        source = by_id[identifier]
                        location = ' / '.join(str(source.get(part)) for part in ('page', 'sheet', 'location') if source.get(part) is not None)
                        notes[key].append(f"[{identifier}] {source['filename']} / {location}" + (f" / {source['source_url']}" if source.get('source_url') else ''))
                profile = {**profile, 'annex_sources': notes}
            validate_template_values(checked['draft'], profile, literal_values=literal_values)
            expansion = result.get('template_expansion')
            if expansion or (profile or {}).get('repeat_expansion'):
                from templates.repeat_rows import verify_prepared_template
                prepared_check = verify_prepared_template(template, profile, expansion)
            else:
                prepared_check = None
            values = checked['draft']
            if profile and profile.get('citation_mode') == 'sidecar':
                values = {key: split_field_citations(value, profile_field(profile, key), literal=literal_values.get(key))[0]
                          for key, value in values.items()}
            fill_compatible_template(template, values, output,
                                     mapping=(mappings or {}).get(suffix), profile=profile)
            output_bytes = output.read_bytes()
            output_hash = hashlib.sha256(output_bytes).hexdigest()
            verification[suffix] = verify_output(template, output, values, profile=profile, mapping=(mappings or {}).get(suffix))
            proof = verification[suffix].get('sha', {})
            if (proof.get('template') != source_hash or proof.get('output') != output_hash
                    or hashlib.sha256(Path(template).read_bytes()).hexdigest() != source_hash
                    or hashlib.sha256(output.read_bytes()).hexdigest() != output_hash):
                raise ValueError('출력 검증 기록과 현재 양식·문서가 다름')
            if prepared_check:
                verification[suffix]['repeat_expansion'] = prepared_check
            if native_review != 'off':
                from agent.native_review import review_native_output
                started = perf_counter()
                native = review_native_output(template, output, values, profile=profile,
                                              mapping=(mappings or {}).get(suffix),
                                              work_dir=Path(directory) / f'native-{suffix}')
                preview = native.pop('pdf_bytes', None)
                native['elapsed_seconds'] = perf_counter() - started
                native_checks[suffix] = native
                # Keep current diagnostics, but publish downloadable bytes only
                # after every requested format has passed the selected policy.
                result['native_output_verification'] = native_checks
                unchanged = (hashlib.sha256(Path(template).read_bytes()).hexdigest() == source_hash
                             and hashlib.sha256(output.read_bytes()).hexdigest() == output_hash)
                proof_matches = (native.get('source_sha256') == source_hash
                                 and native.get('output_sha256') == output_hash)
                if not unchanged or not proof_matches:
                    native.update(status='failed', blocking=True)
                    native.setdefault('issues', []).append({'code': 'native_proof_mismatch',
                        'message': '출력 확인 기록과 현재 양식·문서가 다름', 'severity': 'error'})
                if preview is not None:
                    if not isinstance(preview, bytes) or hashlib.sha256(preview).hexdigest() != native.get('pdf_sha256'):
                        native.update(status='failed', blocking=True)
                        native.setdefault('issues', []).append({'code': 'native_preview_mismatch',
                            'message': '미리보기와 출력 확인 기록이 다름', 'severity': 'error'})
                    else:
                        previews[suffix] = preview
                if native.get('status') not in {'passed', 'warning', 'unavailable'} or native.get('blocking') is not False:
                    raise ValueError('문서 프로그램 출력 확인 실패: ' + ' / '.join(
                        issue['message'] for issue in native.get('issues', [])))
                if native_review == 'required' and native['status'] != 'passed':
                    raise ValueError('모든 형식의 문서 프로그램 출력 확인을 완료해야 다운로드할 수 있음')
            outputs[suffix] = output_bytes
        # A later format may take time to render. Recheck every earlier proof
        # before publishing any bytes, not only the format just inspected.
        mismatched = []
        for suffix, template in paths.items():
            output_hash = hashlib.sha256(outputs[suffix]).hexdigest()
            try:
                unchanged = (hashlib.sha256(template.read_bytes()).hexdigest() == source_hashes[suffix]
                             and hashlib.sha256((Path(directory) / f"보고서.{suffix}").read_bytes()).hexdigest() == output_hash)
            except OSError:
                unchanged = False
            proof = verification[suffix].get('sha', {})
            matches = unchanged and proof.get('template') == source_hashes[suffix] and proof.get('output') == output_hash
            if native_review != 'off':
                native = native_checks[suffix]
                matches = (matches and native.get('source_sha256') == source_hashes[suffix]
                           and native.get('output_sha256') == output_hash
                           and native.get('blocking') is False
                           and native.get('status') in {'passed', 'warning', 'unavailable'}
                           and (native_review != 'required' or native.get('status') == 'passed'))
                if suffix in previews:
                    matches = matches and hashlib.sha256(previews[suffix]).hexdigest() == native.get('pdf_sha256')
                if not matches:
                    native.update(status='failed', blocking=True)
                    native.setdefault('issues', []).append({'code': 'native_publish_mismatch',
                        'message': '최종 출력 준비 중 양식·문서 또는 확인 증거가 변경됨', 'severity': 'error'})
            if not matches:
                mismatched.append(suffix)
        if mismatched:
            raise ValueError('최종 출력 검증 기록과 현재 양식·문서가 다름. 모든 다운로드 파일을 다시 준비해야 함')
    result['output_verification'] = verification
    if native_review != 'off':
        result['_native_preview_bytes'] = previews
    return outputs
