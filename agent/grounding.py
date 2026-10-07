"""Meaning-level evidence review with exact source quotes and complete coverage."""

import hashlib
import json

from agent.review import is_factual_line, number_tokens
from agent.field_citations import field_citations, is_selection_field, profile_field, split_field_citations


def evidence_fingerprint(draft, sources, *, template_profile=None, literal_values=None):
    evidence = [{key: source.get(key) for key in ('source_id', 'text', 'filename', 'page', 'sheet', 'location',
                'product_name', 'product_variant', 'company_name', 'company_role',
                'document_sha256', 'source_url', 'jurisdiction', 'regulatory_role',
                'context_text', 'context_start', 'context_end')} for source in sources]
    payload = {'draft': draft, 'sources': evidence}
    selections = [{key: field.get(key) for key in ('value_key', 'control_type', 'options', 'multiselect', 'allow_custom')}
                  for field in (template_profile or {}).get('fields', []) if is_selection_field(field)]
    if selections or literal_values:
        payload['field_citations'] = {'selections': selections, 'literal_values': literal_values or {}}
    scopes = [{key: field.get(key) for key in
               ('id', 'value_key', 'label', 'evidence_document_labels', 'evidence_scope')}
              for field in (template_profile or {}).get('fields', [])
              if 'evidence_document_labels' in field or 'evidence_scope' in field]
    if scopes:
        payload['field_evidence_scopes'] = scopes
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def factual_lines(draft, *, template_profile=None, literal_values=None):
    result = []
    for field, text in draft.items():
        metadata = profile_field(template_profile, field)
        literal = (literal_values or {}).get(field)
        literals = literal.splitlines() if isinstance(literal, str) else []
        for number, line in enumerate(text.splitlines(), 1):
            plain, _ = split_field_citations(line, metadata, literal=literals[number-1] if number <= len(literals) else None)
            selected = is_selection_field(metadata) or isinstance(literal, str)
            if (bool(plain) if selected else is_factual_line(line) and (field != '제목' or number_tokens(line))):
                result.append({'field': field, 'line': number, 'text': line})
    return result


def inspect_grounding(draft, sources, client, *, template_profile=None, literal_values=None):
    options = {'template_profile': template_profile, 'literal_values': literal_values}
    lines = factual_lines(draft, **options)
    expected = {(item['field'], item['line']): item for item in lines}
    source_map = {source['source_id']: source['text'] for source in sources}
    found, issues, claims = set(), [], []
    for offset in range(0, len(lines), 160):
        batch = lines[offset:offset + 160]
        batch_keys = {(item['field'], item['line']) for item in batch}
        response = client.generate_json('grounding', {'claims': batch, 'sources': sources})
        if not isinstance(response, dict):
            raise ValueError('의미 검수 응답은 JSON 객체여야 함')
        batch_claims = response.get('claims')
        if not isinstance(batch_claims, list):
            raise ValueError('의미 검수 응답에 claims 목록이 필요함')
        for claim in batch_claims:
            if not isinstance(claim, dict) or not isinstance(claim.get('field'), str) or type(claim.get('line')) is not int:
                raise ValueError('의미 검수 항목의 문장 위치가 잘못됨')
            key = (claim.get('field'), claim['line'])
            if key not in batch_keys or key in found:
                raise ValueError('의미 검수 문장이 중복되거나 현재 배치에 없음')
            found.add(key)
            status = claim.get('status')
            evidence = claim.get('evidence', [])
            if status not in {'supported', 'unsupported', 'unclear'} or not isinstance(evidence, list):
                raise ValueError('의미 검수 상태 또는 인용 근거가 잘못됨')
            literal = (literal_values or {}).get(key[0])
            literal_lines = literal.splitlines() if isinstance(literal, str) else []
            cited = set(field_citations(expected[key]['text'], profile_field(template_profile, key[0]),
                                       literal=literal_lines[key[1]-1] if key[1] <= len(literal_lines) else None))
            valid_quotes = bool(evidence)
            for item in evidence:
                if not isinstance(item, dict):
                    valid_quotes = False
                    continue
                source_id, quote = item.get('source_id'), item.get('quote')
                valid_quotes &= isinstance(source_id, str) and source_id in cited and source_id in source_map and isinstance(quote, str) and bool(quote.strip()) and quote in source_map.get(source_id, '')
            if status != 'supported' or not valid_quotes:
                issues.append({'code': 'semantic_grounding', 'field': key[0], 'line': key[1], 'severity': 'error',
                               'message': f"{key[0]} {key[1]}줄: 원자료가 문장의 의미를 뒷받침하는지 추가 확인 필요함"})
        claims.extend(batch_claims)
    for field, number in sorted(expected.keys() - found):
        issues.append({'code': 'semantic_missing', 'field': field, 'line': number, 'severity': 'error', 'message': f'{field} {number}줄: 의미 검수 응답에서 누락됨'})
    return {'fingerprint': evidence_fingerprint(draft, sources, **options), 'claims': claims, 'warnings': issues,
            'claim_count': len(lines), 'reviewed_count': len(found),
            'batch_count': (len(lines) + 159) // 160, 'blocking': bool(issues)}
