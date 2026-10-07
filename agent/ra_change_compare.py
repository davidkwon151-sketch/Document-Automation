"""Literal, source-bound RA change comparison; no model or approval inference."""
from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import csv
import io
import json
import re

from agent.ra_workflows import _bound_quote, _source_records

DEFAULT_LABELS = ('제조원', '주소', '보관조건', '성상', '포장')
PRODUCT_LABELS = {'제품명', '품목명', 'Product name'}
VARIANT_LABELS = {'제형·함량', '제형/함량', '제형', '함량', 'Strength', 'Dosage form'}
CONDITION_LABELS = {'조건', '대상', '보관 상태', '적용 범위'}


def _norm(value):
    return re.sub(r'\s+', ' ', value).strip()


def _lines(text):
    offset = 0
    for line in text.splitlines(keepends=True):
        raw = line.rstrip('\r\n')
        match = re.fullmatch(r'\s*([^:：\n]{1,100}?)\s*[:：]\s*(.*?)\s*', raw)
        if match:
            yield match[1].strip(), match[2], offset, raw, match.start(2)
        offset += len(line)


def _ref(side, source, quote, start):
    return {'side': side, 'source_id': source['source_id'], 'quote': quote,
            'start': start, 'end': start + len(quote), 'source': deepcopy(source)}


def _scope(records, product_name, variant):
    """Identity comes from literal declarations in each actual file, never metadata."""
    documents = defaultdict(list)
    for source in records.values():
        documents[source['document_sha256']].append(source)
    scopes = {}
    for digest, sources in documents.items():
        declarations = defaultdict(set)
        anchors = []
        seen = set()
        for source in sources:
            # Only the original chunk is an anchor; a filename/user annotation is not.
            for label, value, start, line, _ in _lines(source['text']):
                if label not in PRODUCT_LABELS | VARIANT_LABELS or not value:
                    continue
                declarations[label].add(_norm(value))
                key = (source['source_id'], start)
                if key not in seen:
                    anchors.append(_ref('identity', source, line, start)); seen.add(key)
        products = set().union(*(declarations[k] for k in PRODUCT_LABELS))
        combined = declarations['제형·함량'] | declarations['제형/함량']
        forms = declarations['제형'] | declarations['Dosage form']
        strengths = declarations['함량'] | declarations['Strength']
        explicit = combined or ({form + ' ' + strength for form in forms for strength in strengths}
                                if len(forms) == len(strengths) == 1 else forms | strengths)
        matched = (products == {_norm(product_name)} and explicit == {_norm(variant)}
                   and len(forms) <= 1 and len(strengths) <= 1)
        # Conflicting separate declarations cannot be hidden by one combined label.
        if combined and forms and strengths and combined != {form + ' ' + strength for form in forms for strength in strengths}:
            matched = False
        if combined and any(not any((' ' + component + ' ') in (' ' + item + ' ') for item in combined)
                            for component in forms | strengths):
            matched = False
        scopes[digest] = {'confirmed': matched, 'products': sorted(products),
                         'variants': sorted(explicit), 'anchors': anchors,
                         'note': '같은 파일의 명시 제품·제형/함량 선언 대조이며 허가 상태 판단이 아님'}
    return scopes


def _conditions(source):
    text = source.get('context_text', source['text'])
    declared = [(label, _norm(value)) for label, value, *_ in _lines(text) if label in CONDITION_LABELS]
    qualifiers = re.findall(r'개봉\s*전|개봉\s*후|미개봉|재구성\s*전|재구성\s*후|성인|소아|초기|유지', text)
    return {'declared': sorted(set(declared)), 'qualifiers': sorted(set(_norm(q) for q in qualifiers))}


def compare_ra_changes(before_sources, after_sources, labels=None, *, product_name, variant, selections=None):
    """Compare exact label:value lines or explicitly selected exact quotations.

    selections = {label: {before|after: {source_id, quote, start?}}}. Ambiguous
    candidates are preserved, never ranked or silently chosen. Every reference
    retains the complete source plus exact chunk offsets and document identity.
    """
    if not isinstance(product_name, str) or not product_name.strip() or not isinstance(variant, str) or not variant.strip():
        raise ValueError('원자료에 명시된 제품명과 제형·함량을 입력해야 함')
    labels = list(DEFAULT_LABELS if labels is None else labels)
    if (not labels or len(labels) > 100 or any(not isinstance(k, str) or not k.strip() or '\n' in k or ':' in k or '：' in k for k in labels)
            or len(set(labels)) != len(labels)):
        raise ValueError('서로 다른 정확한 항목명 1~100개가 필요함')
    selections = {} if selections is None else selections
    if not isinstance(selections, dict) or set(selections) - set(labels):
        raise ValueError('선택 인용은 등록된 비교 항목 객체이어야 함')
    records = {'before': _source_records(before_sources), 'after': _source_records(after_sources)}
    for side in records.values():
        for source in side.values():
            _bound_quote({'quote': source.get('text')}, source)
    scopes = {side: _scope(items, product_name, variant) for side, items in records.items()}
    overlap = set(scopes['before']) & set(scopes['after'])
    rows = []
    for label in labels:
        selection = selections.get(label, {})
        if not isinstance(selection, dict) or set(selection) - {'before', 'after'}:
            raise ValueError('변경 전/후 인용 연결만 허용함')
        found = {}
        for side, sources in records.items():
            candidates = []
            if side in selection:
                binding = selection[side]
                if not isinstance(binding, dict) or binding.get('source_id') not in sources:
                    raise ValueError('선택 인용의 실제 출처 ID가 없음')
                source = sources[binding['source_id']]
                quote, start = _bound_quote(binding, source)
                candidates.append({'value': quote, 'ref': _ref(side, source, quote, start),
                                   'conditions': _conditions(source), 'explicit_selection': True})
            else:
                for source in sources.values():
                    for name, value, start, line, value_start in _lines(source['text']):
                        if name != label or not value:
                            continue
                        end = start + len(line)
                        context = source.get('context_text', source['text'])
                        context_end = source.get('context_start', 0) + end
                        tail = context[context_end:].lstrip('\r\n')
                        next_line = tail.split('\n')[0] if tail else ''
                        multiline = bool(next_line.strip() and not re.match(r'\s*[^:：\n]+[:：]', next_line))
                        candidates.append({'value': value, 'ref': _ref(side, source, value, start + value_start),
                                           'conditions': _conditions(source), 'multiline_pending': multiline,
                                           'original_label': name, 'original_line': line})
            found[side] = candidates
        reasons = []
        for side, candidates in found.items():
            if len(candidates) > 1:
                reasons.append(f'{side}: 같은 항목의 후보가 여러 개임; 정확한 인용 선택 필요함')
            for candidate in candidates:
                digest = candidate['ref']['source']['document_sha256']
                if not scopes[side][digest]['confirmed']:
                    reasons.append(f'{side}: 원문 제품명·제형/함량 미확인 또는 혼입됨')
                if digest in overlap:
                    reasons.append('변경 전/후가 같은 파일 SHA임; 별도 버전 원자료 필요함')
                if candidate.get('multiline_pending'):
                    reasons.append(f'{side}: 항목의 다음 줄 내용은 자동 연결하지 않음; 전체 인용 선택 필요함')
        b, a = found['before'], found['after']
        if len(b) == len(a) == 1 and b[0]['conditions'] != a[0]['conditions']:
            reasons.append('적용 조건·대상·상태가 다르거나 한쪽에만 명시됨; 직접 비교 보류함')
        kind = '모호' if reasons else '자료없음' if not b or not a else '동일' if b[0]['value'] == a[0]['value'] else '차이'
        rows.append({'label': label, 'before': b[0]['value'] if len(b) == 1 else '',
                     'after': a[0]['value'] if len(a) == 1 else '',
                     'before_refs': [x['ref'] for x in b], 'after_refs': [x['ref'] for x in a],
                     'before_candidates': b, 'after_candidates': a, 'change_kind': kind,
                     'confirmation_required': True,
                     'confirmation_notes': list(dict.fromkeys(reasons)) or ['담당자가 항목·범위·버전·변경 의도를 확인해야 함']})
    result = {'mode': 'exact_source_comparison', 'rows': rows, 'document_scopes': scopes,
              'issues': [{'label': r['label'], 'kind': r['change_kind'], 'notes': r['confirmation_notes']}
                         for r in rows if r['change_kind'] in {'모호', '자료없음'}],
              'actual_model_requests': 0, 'submission_ready': False,
              'product_name': product_name, 'variant': variant,
              'scope': '명시 원문 변경 비교 초안; 임상 판단·변경 승인·법정 제출 적합성 인증 아님'}
    result['fingerprint'] = sha256(json.dumps({'result': result, 'sources': records}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return result


def comparison_csv(result):
    """Excel-safe comparison summary; the separate JSON retains exact raw values."""
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['항목', '변경 전', '변경 후', '구분', '변경 전 출처', '변경 후 출처', '확인 필요'])
    def safe(text):
        return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text
    for row in result['rows']:
        refs = lambda side: '; '.join(f"{r['source']['filename']} / {r['source'].get('page') or r['source'].get('sheet') or r['source'].get('location')} / {r['source_id']} / {r['source']['document_sha256']}" for r in row[side + '_refs'])
        writer.writerow([safe(str(x)) for x in [row['label'], row['before'], row['after'], row['change_kind'], refs('before'), refs('after'), '; '.join(row['confirmation_notes'])]])
    return ('\ufeff' + stream.getvalue()).encode('utf-8')
