"""Confirmed literal change rows -> original MFDS form, without inferred reasons.

The registered form remains unchanged. A reason explicitly written by its user
is a user-input source for that one reason cell, never clinical/approval evidence.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from agent.brief import model_profile
from agent.ra import inspect_ra_draft
from agent.ra_change_compare import PRODUCT_LABELS, _lines, _scope, compare_ra_changes
from agent.ra_workflows import _bound_quote, _source_records, prepare_ra_workflow


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _scoped_sources(sources, side):
    records = _source_records(sources)
    remapped, ids = [], {}
    for identifier, original in records.items():
        if 'verification_fingerprint' in original or 'verification_receipt' in original:
            from agent.multimodal_intake import validate_generation_source
            validate_generation_source(original)
        _bound_quote({'quote': original.get('text')}, original)
        new_id = 'SChange' + side + _digest((identifier, original['document_sha256']))[:20]
        ids[identifier] = new_id
        remapped.append({**deepcopy(original), 'source_id': new_id,
                         'original_source_id': identifier, 'comparison_side': side,
                         'original_source_record': deepcopy(original)})
    return remapped, ids


def _label_binding(row, records, ids):
    """An item name is cited only when it is the actual source label at this row."""
    for side in ('after', 'before'):
        ref = row[side + '_refs'][0]
        source = records[side][ref['source_id']]
        for label, _, line_start, line, value_start in _lines(source['text']):
            if (label == row['label'] and line_start + value_start <= ref['start']
                    < line_start + len(line)):
                return {'source_id': ids[side][ref['source_id']], 'quote': label,
                        'start': line_start + line.index(label)}
    return None


def _product_binding(comparison, sources, ids, digest):
    for anchor in comparison['document_scopes']['after'][digest]['anchors']:
        source = sources[anchor['source_id']]
        for label, value, start, line, value_start in _lines(source['text']):
            if label in PRODUCT_LABELS and ' '.join(value.split()) == ' '.join(comparison['product_name'].split()):
                return {'source_id': ids[anchor['source_id']], 'quote': value, 'start': start + value_start}
    raise ValueError('변경 후 파일의 실제 제품명 인용을 연결할 수 없음')


def prepare_ra_change_document(profile, before_sources, after_sources, *, product_name, variant,
                               selected_labels, confirmed_labels, comparison_labels=None,
                               selections=None, reason_sources=None, source_reasons=None,
                               direct_reasons=None, direct_values=None):
    """Prepare up to three explicitly confirmed *different* rows in fixed order.

    source_reasons = {comparison_label: {source_id, quote, start?}} references
    reason_sources. direct_reasons = {comparison_label: exact user-written str}.
    Omitted reasons block output; rows are never cropped or filled with guesses.
    Direct reasons declare only those cells as user input in a runtime copy.
    The returned ``prepared`` uses the standard source/number/group checker.
    """
    clean = model_profile(profile)
    _require(clean and clean.get('format') == 'pdf' and clean.get('ra_workflow') == 'variation'
             and clean.get('domain') == 'pharmaceutical_ra', '등록된 RA 변경허가 PDF 양식이 필요함')
    fields = {field['value_key']: field for field in clean['fields']}
    _require(len(fields) == len(clean['fields']), '등록 입력칸의 값 이름이 중복됨')
    row_keys = [[name + ' ' + str(index) for name in ('변경 항목', '변경 전', '변경 후', '변경 사유')]
                for index in range(1, 4)]
    _require(all(key in fields and fields[key].get('input_required') is False for group in row_keys for key in group),
             '공식 고정 3행의 네 입력 위치와 원자료 입력 권한이 필요함')
    registered_groups = {tuple(group['fields']) for group in clean.get('constraints', {}).get('groups', [])
                         if group.get('kind') == 'all_or_none'}
    _require(all(tuple(group) in registered_groups for group in row_keys), '사용한 변경 행의 네 칸 필수 조건이 필요함')
    _require(isinstance(selected_labels, list) and 1 <= len(selected_labels) <= 3
             and all(isinstance(label, str) and label.strip() for label in selected_labels)
             and len(set(selected_labels)) == len(selected_labels), '변경 항목은 서로 다른 1~3개만 선택할 수 있음; 행을 잘라내지 않음')
    _require(isinstance(confirmed_labels, list) and len(confirmed_labels) == len(selected_labels)
             and all(isinstance(label, str) for label in confirmed_labels)
             and set(confirmed_labels) == set(selected_labels), '선택한 모든 변경 항목의 담당자 확인이 필요함')
    comparison = compare_ra_changes(before_sources, after_sources,
        selected_labels if comparison_labels is None else comparison_labels,
        product_name=product_name, variant=variant, selections=selections)
    compared = {row['label']: row for row in comparison['rows']}
    _require(not set(selected_labels) - set(compared), '비교하지 않은 항목을 변경 양식에 넣을 수 없음')
    _require(all(compared[label]['change_kind'] == '차이' for label in selected_labels),
             '모호·자료없음·동일 항목은 변경 행으로 자동 기입할 수 없음')
    source_reasons = {} if source_reasons is None else source_reasons
    direct_reasons = {} if direct_reasons is None else direct_reasons
    direct_values = {} if direct_values is None else direct_values
    _require(isinstance(source_reasons, dict) and isinstance(direct_reasons, dict)
             and not (set(source_reasons) | set(direct_reasons)) - set(selected_labels)
             and not set(source_reasons) & set(direct_reasons), '사유는 선택 항목별 원자료 인용 또는 직접 입력 한 가지만 허용함')
    _require(isinstance(direct_values, dict) and not set(direct_values) & {key for group in row_keys for key in group},
             '변경 행을 직접 입력값으로 덮어쓸 수 없음')
    _require(all(isinstance(value, str) for value in direct_reasons.values()), '담당자가 작성한 사유는 문자열이어야 함')
    original = {'before': _source_records(before_sources), 'after': _source_records(after_sources),
                'reason': _source_records([] if reason_sources is None else reason_sources)}
    sources, ids = [], {}
    for side in original:
        items, ids[side] = _scoped_sources(list(original[side].values()), side)
        sources.extend(items)
    reason_scope = _scope(original['reason'], product_name, variant)
    runtime = deepcopy(clean)
    runtime.update(ra_product_name=product_name, ra_product_variant=variant)
    by_key = {field['value_key']: field for field in runtime['fields']}
    bindings, direct = {}, deepcopy(direct_values)
    problems = []
    if '제품명' in fields:
        _require(fields['제품명'].get('input_required') is False, '원자료 제품명 입력 권한이 다름')
        first_digest = compared[selected_labels[0]]['after_refs'][0]['source']['document_sha256']
        bindings['제품명'] = _product_binding(comparison, original['after'], ids['after'], first_digest)
    for index, label in enumerate(selected_labels, 1):
        row = compared[label]
        item = _label_binding(row, original, ids)
        if item:
            bindings[f'변경 항목 {index}'] = item
        else:
            problems.append({'code': 'ra_change_original_label_missing', 'field': f'변경 항목 {index}',
                             'severity': 'error', 'message': '선택한 항목명과 인용 위치의 실제 원문 항목명을 연결해야 함'})
        for side, title in [('before', '변경 전'), ('after', '변경 후')]:
            ref = row[side + '_refs'][0]
            bindings[f'{title} {index}'] = {'source_id': ids[side][ref['source_id']],
                                            'quote': ref['quote'], 'start': ref['start']}
        reason_key = f'변경 사유 {index}'
        if label in source_reasons:
            binding = source_reasons[label]
            _require(isinstance(binding, dict) and binding.get('source_id') in original['reason'],
                     '변경 사유 인용의 원자료 출처 ID가 없음')
            source = original['reason'][binding['source_id']]
            quote, start = _bound_quote(binding, source)
            if not reason_scope[source['document_sha256']]['confirmed']:
                problems.append({'code': 'ra_change_reason_scope', 'field': reason_key, 'severity': 'error',
                                 'message': '사유 원자료의 명시 제품명·제형/함량이 선택 품목과 다름 또는 미확인임'})
            bindings[reason_key] = {'source_id': ids['reason'][binding['source_id']], 'quote': quote, 'start': start}
        elif label in direct_reasons:
            by_key[reason_key].update(input_required=True, input_mode='user_provided')
            direct[reason_key] = direct_reasons[label]
    prepared = prepare_ra_workflow(runtime, sources, source_bindings=bindings, direct_values=direct)
    # A generic fixed cell such as "변경 전 1" must retain the item semantics:
    # manufacturer roles, product strength and dose conditions still get checked.
    # This is an extra review profile, never a modification of original labels.
    review_profile = deepcopy(prepared['template_profile'])
    reviewed = {field['value_key']: field for field in review_profile['fields']}
    for index, label in enumerate(selected_labels, 1):
        for title in ('변경 전', '변경 후'):
            reviewed[f'{title} {index}']['label'] = label + ' · ' + title + ' ' + str(index)
    item_review = inspect_ra_draft(prepared['draft'], prepared['sources'], profile=review_profile)
    problems.extend(item for item in item_review['issues'] if item['severity'] == 'error'
                    and item not in prepared['review']['issues'])
    if problems:
        prepared['review']['issues'].extend(problems)
        prepared['review']['blocking'] = True
        prepared['ready_for_output_check'] = False
        prepared['status'] = 'needs_revision'
    inputs = {'product_name': product_name, 'variant': variant, 'selected_labels': deepcopy(selected_labels),
              'confirmed_labels': deepcopy(confirmed_labels), 'comparison_labels': deepcopy(comparison_labels),
              'selections': deepcopy(selections), 'reason_sources': deepcopy(reason_sources),
              'source_reasons': deepcopy(source_reasons), 'direct_reasons': deepcopy(direct_reasons),
              'direct_values': deepcopy(direct_values)}
    result = {'mode': 'confirmed_change_rows', 'comparison': comparison, 'prepared': prepared,
              'source_bindings': bindings, 'direct_values': direct, 'sources': sources, 'profile': runtime,
              'original_profile': deepcopy(profile), 'before_sources': deepcopy(before_sources),
              'after_sources': deepcopy(after_sources), 'inputs': inputs, 'selected_labels': deepcopy(selected_labels),
              'row_count': len(selected_labels), 'row_capacity': 3,
              'unselected_labels': [row['label'] for row in comparison['rows'] if row['label'] not in selected_labels],
              'direct_reason_keys': [f'변경 사유 {index}' for index, label in enumerate(selected_labels, 1) if label in direct_reasons],
              'actual_model_requests': 0, 'submission_ready': False, 'human_kpi_measured': False,
              'item_review': item_review,
              'scope': '담당자가 확인한 원문 차이와 제공 사유의 공식 고정 3행 기입 준비; 변경 승인·임상 판단·법정 제출 완료 아님'}
    result['fingerprint'] = _digest(result)
    return result


def export_ra_change_document(entry, result, *, attachment_checks=None, record_kind='production'):
    """Rebuild the entire confirmed mapping, fill and independently re-open it.

    Returns document/evidence bytes and the normal SHA-bound RA work package.
    The original catalog SHA and position profile must match exactly; only reason
    cells explicitly written by this user receive the runtime input declaration.
    """
    from app.ra_workflow_ui import resolve_workflow
    from agent.field_citations import profile_field, split_field_citations
    from agent.output_check import verify_output
    from agent.ra_workpack import build_ra_workpack
    from templates.compatibility import fill_compatible_template

    _require(isinstance(result, dict) and result.get('mode') == 'confirmed_change_rows', '현재 확인한 변경 작업 결과가 필요함')
    template, original = resolve_workflow(entry)
    _require(entry.get('ra_workflow') == 'variation' and original == result.get('original_profile'),
             '선택한 공식 변경 양식·등록 매핑과 준비 결과가 다름')
    rebuilt = prepare_ra_change_document(original, result['before_sources'], result['after_sources'], **result['inputs'])
    _require(rebuilt == result, '제품·원자료·인용·선택 항목·사유·확인·검수가 변경됨; 다시 준비해야 함')
    prepared = rebuilt['prepared']
    _require(prepared['ready_for_output_check'] and not prepared['review']['blocking'],
             '모든 사용 행의 사유·제품·근거·분량 오류를 해결해야 문서를 출력할 수 있음')
    used = prepared['template_profile']
    literals = {key: value['value'] for key, value in prepared['locked_fields'].items()}
    values = {key: split_field_citations(value, profile_field(used, key), literal=literals.get(key))[0]
              for key, value in prepared['draft'].items()}
    with tempfile.TemporaryDirectory(prefix='ra-change-output-') as directory:
        output = Path(directory) / ('변경신청서' + template.suffix)
        fill_compatible_template(template, values, output, profile=used)
        proof = verify_output(template, output, values, profile=used)
        document = output.read_bytes()
        _require(proof.get('status') == 'passed'
                 and proof.get('sha', {}).get('template') == entry['source_sha256']
                 and proof.get('sha', {}).get('output') == sha256(document).hexdigest(), '저장 후 원위치·원본 보존 검수 또는 출력 SHA가 다름')
    sidecar = {**prepared, 'output_verification': proof, 'workflow_id': entry['id'],
               'source_url': entry.get('source_url'), 'submission_ready': False,
               'change_document': {'fingerprint': rebuilt['fingerprint'], 'comparison': rebuilt['comparison'],
                                   'selected_labels': rebuilt['selected_labels'], 'row_count': rebuilt['row_count'],
                                   'row_capacity': 3, 'direct_reason_keys': rebuilt['direct_reason_keys'],
                                   'item_review': rebuilt['item_review'],
                                   'scope': rebuilt['scope']}}
    export = {'document': document, 'filename': entry['id'] + '_change.pdf',
              'evidence': json.dumps(sidecar, ensure_ascii=False, indent=2).encode()}
    package = build_ra_workpack(entry, rebuilt['profile'], prepared, export,
                               selected_product={'name': rebuilt['inputs']['product_name'], 'variant': rebuilt['inputs']['variant']},
                               attachment_checks=attachment_checks, record_kind=record_kind)
    return {**export, 'workpack': package, 'output_verification': proof,
            'fingerprint': rebuilt['fingerprint'], 'submission_ready': False}
