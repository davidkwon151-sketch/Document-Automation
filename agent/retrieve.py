"""Paragraph retrieval with keyword scoring and injectable embedding vectors."""

from __future__ import annotations

import hashlib
import math
import re
from datetime import date
from pathlib import Path
from typing import Callable


def load_documents(paths: list[str | Path], *, client=None, allow_ocr=True) -> list[dict]:
    from parsers import parse_file
    from parsers.extended import parse_extended

    documents = []
    for path in paths:
        source = Path(path)
        original_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        document = (parse_file(path) if source.suffix.lower() in {'.xlsx', '.docx', '.hwpx'}
                    else parse_extended(path, client=client, allow_ocr=allow_ocr))
        if hashlib.sha256(source.read_bytes()).hexdigest() != original_sha:
            raise ValueError('원자료가 파싱 중 변경됨. 원본을 다시 선택해야 함')
        declared = [document, *document.get('페이지/시트 정보', []), *document.get('표 목록', [])]
        if any(isinstance(item, dict) and 'document_sha256' in item
               and item['document_sha256'] != original_sha for item in declared):
            raise ValueError('파서의 원자료 SHA와 실제 첨부 파일 SHA가 일치하지 않음')
        documents.append({**document, 'document_sha256': original_sha})
    return documents


def chunk_documents(documents: list[dict], max_chars: int = 800) -> list[dict]:
    """Keep original locations; never invent rendered pages for DOCX/HWPX."""
    if max_chars < 50:
        raise ValueError("max_chars는 50 이상이어야 함")
    chunks: list[dict] = []
    seen: set[str] = set()
    for document_index, document in enumerate(documents):
        filename = document["파일명"]
        blocks = document["페이지/시트 정보"]
        if not blocks and document["본문"].strip():
            blocks = [{"본문": document["본문"], "위치": "본문", "표 목록": document["표 목록"]}]
        else:
            blocks = list(blocks)
            # Keep table labels beside values; separate cell paragraphs lose that link.
            for table in document["표 목록"]:
                if not isinstance(table, dict):
                    continue
                rows = table.get("행", table.get("rows", []))
                header = rows[0] if len(rows) > 1 and not any(re.search(r"\d", str(cell)) for cell in rows[0]) else None
                for row_index, row in enumerate(rows, 1):
                    values = [str(cell) if cell is not None else "" for cell in row]
                    if header is not None and row_index > 1 and len(header) == len(values):
                        text = " ; ".join(f"{label} {value}" for label, value in zip(header, values) if value)
                    else:
                        text = " | ".join(values)
                    if text.strip():
                        blocks.append({"본문": text, "표 목록": [], "위치": f"{table.get('위치', '표')}/행 {row_index}", "페이지": table.get("페이지"), "시트": table.get("시트"), "검증 필요": table.get('검증 필요', False)})
        for block_index, block in enumerate(blocks):
            location = block.get("위치") or (
                f"{block['페이지']}페이지" if block.get("페이지") is not None
                else f"{block['시트']} 시트" if block.get("시트") else f"문단 {block_index + 1}"
            )
            context = block.get('본문', '')
            paragraphs = [line.strip() for line in re.split(r"\n+", context) if line.strip()]
            for table in block.get("표 목록", []):
                rows = table.get("행", table.get("rows", [])) if isinstance(table, dict) else table
                for row in rows:
                    text = " | ".join(str(cell) for cell in row if cell is not None)
                    if text.strip() and text not in paragraphs:
                        paragraphs.append(text)
            search_offset = 0
            for paragraph_index, paragraph in enumerate(paragraphs):
                paragraph_start = context.find(paragraph, search_offset)
                if paragraph_start >= 0:
                    search_offset = paragraph_start + len(paragraph)
                    paragraph_context = context
                else:
                    # Flattened table rows have their own source location and context.
                    paragraph_context, paragraph_start = paragraph, 0
                context_sha = hashlib.sha256(paragraph_context.encode('utf-8')).hexdigest()
                for offset in range(0, len(paragraph), max_chars):
                    text = paragraph[offset:offset + max_chars]
                    origin = f"{document_index}|{filename}|{block.get('페이지')}|{block.get('시트')}|{location}|{block_index}|{paragraph_index}|{offset}|{text}|{context_sha}"
                    source_id = "S" + hashlib.sha256(origin.encode("utf-8")).hexdigest()[:12]
                    if source_id in seen:
                        continue
                    seen.add(source_id)
                    metadata = {}
                    for key in ('product_name', 'product_variant', 'company_name', 'company_role',
                                'document_sha256', 'source_url', 'jurisdiction', 'regulatory_role'):
                        value = block.get(key, document.get(key))
                        if value is not None:
                            if not isinstance(value, str) or len(value) > 2000:
                                raise ValueError(f'자료의 출처·제품 범위 정보는 제한된 문자열이어야 함: {key}')
                            metadata[key] = value
                    chunks.append({
                        **metadata,
                        "source_id": source_id,
                        "filename": filename,
                        "page": block.get("페이지"),
                        "sheet": block.get("시트"),
                        "location": f"{location}, 항목 {paragraph_index + 1}",
                        "text": text,
                        "context_text": paragraph_context,
                        "context_start": paragraph_start + offset,
                        "context_end": paragraph_start + offset + len(text),
                        "score": 0.0,
                        "document_index": document_index,
                        "requires_verification": block.get('검증 필요', False),
                        "uncertain_items": block.get('불확실한 항목', []),
                    })
    return chunks


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[가-힣A-Za-z0-9]+", text.lower())
    return set(words) | {word[i:i + 2] for word in words for i in range(len(word) - 1)}


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("임베딩 벡터의 차원이 일치하지 않음")
    if not all(math.isfinite(value) for value in left + right):
        raise ValueError("임베딩 벡터에 유효하지 않은 값이 있음")
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0


def search_chunks(
    query: str,
    chunks: list[dict],
    embed: Callable[[list[str]], list[list[float]]],
    top_k: int = 6,
) -> list[dict]:
    """Only accepts the current request's chunks; index storage can be replaced later."""
    if not query.strip():
        raise ValueError("검색어를 입력해야 함")
    if top_k < 1:
        raise ValueError("top_k는 1 이상이어야 함")
    if not chunks:
        return []
    texts = [query] + [chunk["text"] for chunk in chunks]
    vectors = []
    for offset in range(0, len(texts), 128):
        batch = texts[offset:offset + 128]
        response = embed(batch)
        if len(response) != len(batch):
            raise ValueError("임베딩 응답 개수가 일치하지 않음")
        vectors.extend(response)
    if len(vectors) != len(chunks) + 1:
        raise ValueError("임베딩 응답 개수가 일치하지 않음")
    query_tokens = _tokens(query)
    ranked: list[dict] = []
    for chunk, vector in zip(chunks, vectors[1:]):
        tokens = _tokens(chunk["text"])
        keyword = len(query_tokens & tokens) / max(len(query_tokens), 1)
        semantic = max(_cosine(vectors[0], vector), 0.0)
        if keyword == 0 and semantic < 0.35:
            continue
        ranked.append({**chunk, "score": round(0.55 * keyword + 0.45 * semantic, 6)})
    return sorted(ranked, key=lambda item: (-item["score"], item["source_id"]))[:top_k]


def retrieve(
    query: str,
    documents: list[dict] | None = None,
    *,
    paths: list[str | Path] | None = None,
    client=None,
    top_k: int = 6,
) -> list[dict]:
    if documents is not None and paths is not None:
        raise ValueError("documents와 paths 중 하나만 지정해야 함")
    parsed = load_documents(paths or [], client=client) if documents is None else documents
    chunks = chunk_documents(parsed)
    if not chunks:
        return []
    if client is None:
        from llm.client import LLMClient

        client = LLMClient()
    return search_chunks(query, chunks, client.embed, top_k)


_STRENGTH_HEADING = re.compile(
    r'^\s*(?:[□○-]\s*)?(?:(?P<product>[A-Z][A-Za-z0-9_-]{2,}|[가-힣][가-힣A-Za-z0-9_-]+)\s+)?'
    r'\d+(?:\.\d+)?\s*(?i:mg|mcg|mL)\s+'
    r'(?i:tablets?|capsules?|solution|powder|suspension|emulsion|cream|ointment|정제|캡슐|용액|분말|현탁액)\b')


def _strength_heading(line):
    heading = _STRENGTH_HEADING.match(line)
    # A product-strength reference inside an instruction is not a new variant
    # heading. The actual dose must never become its own separating scope.
    if heading and not re.search(r'\b(?:is|are|was|were|contains?|take[ns]?|administered|recommended|'
                                 r'must|should|dose|dosage|once|twice|daily|weekly)\b|'
                                 r'투여|권장|복용|함유|포함', line[heading.end():], re.I):
        return heading
    return None


_CALENDAR_DATES = (
    re.compile(r'(?<![A-Za-z0-9_/.-])(?P<year>\d{4})\s*-\s*(?P<month>\d{2})\s*-\s*(?P<day>\d{2})(?![A-Za-z0-9_]|[./-][A-Za-z0-9_])'),
    re.compile(r'(?<![A-Za-z0-9_/.-])(?P<year>\d{4})\s*년\s*(?P<month>\d{1,2})\s*월\s*(?P<day>\d{1,2})\s*일(?![A-Za-z0-9_])'),
)
_CALENDAR_LABEL = re.compile(
    r'보고\s*기간|(?:임상\s*)?시험\s*기간|수집\s*기간|유효\s*기간|'
    r'마감(?:일|기한)?|제출\s*(?:일|기한)|시작일|종료일|작성일|접수일|승인일|발행일|개정일|'
    r'\b(?:reporting\s+period|study\s+period|deadline|due\s+date|start\s+date|end\s+date)\b', re.I)


def _calendar_spans(text):
    """Only explicit, calendar-valid dates; ambiguous/invalid numbers stay numeric."""
    from agent.review import NUMBER_PATTERN

    spans = []
    for pattern in _CALENDAR_DATES:
        for match in pattern.finditer(text):
            # A date-shaped prefix followed by a quantity unit is not a date
            # observation (e.g. 2026-01-01mg); retain every original number.
            suffix = NUMBER_PATTERN.match('0' + text[match.end():])
            if suffix['unit']:
                continue
            try:
                value = date(*(int(match[name]) for name in ('year', 'month', 'day'))).isoformat()
            except ValueError:
                continue
            spans.append({'start': match.start(), 'end': match.end(), 'value': value,
                          'parts': {name: (match.start(name), match.end(name), int(match[name]))
                                    for name in ('year', 'month', 'day')}})
    spans.sort(key=lambda item: item['start'])
    for left, right in zip(spans, spans[1:]):
        if (left['value'] <= right['value'] and
                re.fullmatch(r'\s*(?:[~～–—-]|부터|\bto\b)\s*', text[left['end']:right['start']], re.I)):
            interval = (left['value'], right['value'])
            left.update(calendar_endpoint='start', calendar_interval=interval)
            right.update(calendar_endpoint='end', calendar_interval=interval)
    return spans


def _calendar_context(text, span, spans):
    from agent.ra import _sentence_at
    from agent.review import _context

    sentence = _sentence_at(text, span['start'])
    start = text.rfind(sentence, 0, span['start'] + len(sentence))
    prefix = text[start:span['start']]
    labels = list(_CALENDAR_LABEL.finditer(prefix))
    context = re.sub(r'\s+', '', labels[-1][0]).casefold() if labels else _context(prefix, len(prefix))
    # A date's own year is a value, not a separate reporting-year qualifier.
    # Preserve only explicit year/quarter labels outside other full date spans.
    masked = list(prefix)
    for earlier in spans:
        lo, hi = max(start, earlier['start']), min(span['start'], earlier['end'])
        if lo < hi:
            masked[lo - start:hi - start] = ' ' * (hi - lo)
    periods = re.findall(r'20\d{2}\s*년|\d+\s*분기|상반기|하반기|\b(?:H[12]|Q[1-4])\b', ''.join(masked), re.I)
    return context, {re.sub(r'\s+', '', period).casefold() for period in periods}


def _conflict_tokens(source, names):
    """Use validated source context for scope, never as extra numeric evidence."""
    from agent.ra import _context_source, _material_scope, _sentence_at, _variant
    from agent.review import CITATION_PATTERN, number_tokens

    context_keys = {'context_text', 'context_start', 'context_end'}
    if context_keys & source.keys() and not context_keys <= source.keys():
        raise ValueError('주변 원문의 문자 범위 정보 일부가 누락됨')
    context, offset = _context_source(source)
    full = CITATION_PATTERN.sub('', context['text'])
    offset = len(CITATION_PATTERN.sub('', context['text'][:offset]))
    calendars = _calendar_spans(full)
    source_end = offset + len(CITATION_PATTERN.sub('', source['text']))
    emitted_dates = set()
    full_tokens = ({token['start']: token for token in number_tokens(full.replace('º', '°'))}
                   if 'context_text' in source else {})
    # Equal-length typographic normalisation for numeric parsing only. The source,
    # quotations, offsets and printed glyphs remain unchanged.
    tokens = number_tokens(source['text'].replace('º', '°'))
    identifier_spans = []
    for match in re.finditer(r'\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+-\d+(?:\.\d+)?(?![\w.])|'
                            r'\bGMT[+-](?:0?\d|1[0-4]):[0-5]\d(?![\w.])', full):
        line_start = full.rfind('\n', 0, match.start()) + 1
        line_end = full.find('\n', match.end())
        line = full[line_start:line_end if line_end >= 0 else len(full)]
        # Hyphenated quantities are not document IDs. Require explicit document
        # control metadata and keep every measurement-bearing line numeric.
        control = (re.search(r'\bApproved\s*-\s*\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\b', line)
                   or re.match(r'^\s*(?:Document(?:\s+control)?\s*(?:ID|number)|문서\s*(?:관리\s*)?번호)\s*[:：]', line, re.I))
        if (control and not re.search(r'\bdose\b|용량|투여', line, re.I)
                and not any(token['key'][1] for token in number_tokens(line))):
            identifier_spans.append((match.start(), match.end()))
    for match in re.finditer(
            r'(?:\bSections?|절\s*번호|참조\s*절)\s*(?:[:：]\s*)?(?P<section>\d+(?:\.\d+)+)(?!\w|\.\w)|'
            r'(?:제\s*)?(?P<korean>\d+(?:\.\d+)+)\s*절\b', full, re.I):
        name = 'section' if match['section'] is not None else 'korean'
        lo, hi = match.span(name)
        # An explicit section reference is a document identifier. Adjacent
        # measured quantities, including a malformed range, stay numeric.
        if not any(token['start'] < hi - lo and token['key'][1]
                   for token in number_tokens(full[lo:hi + 32])):
            identifier_spans.append((lo, hi))
    for original in tokens:
        if any(lo <= offset + original['start'] < hi for lo, hi in identifier_spans):
            continue
        token = {**original}
        position = offset + token['start']
        if full_tokens:
            complete = full_tokens.get(position)
            if (complete is None or complete['key'][0] != token['key'][0]
                    or (token['key'][1] and token['key'][1] != complete['key'][1])
                    or position + len(re.match(r'-?\d[\d,]*(?:\.\d+)?', complete['raw'])[0])
                    > offset + len(CITATION_PATTERN.sub('', source['text']))):
                raise ValueError('인용 조각의 숫자가 원문 숫자의 정확한 위치·값·단위와 일치하지 않음')
            # Only this exact numeric observation is restored. Other context
            # numbers remain outside the cited evidence and are never emitted.
            token.update({name: complete[name] for name in ('key', 'context', 'scope', 'role', 'entity')})
        calendar = next((span for span in calendars if span['start'] <= position < span['end']), None)
        if calendar:
            token['context'], token['scope'] = _calendar_context(full, calendar, calendars)
            if offset <= calendar['start'] and calendar['end'] <= source_end:
                if calendar['start'] in emitted_dates:
                    continue
                emitted_dates.add(calendar['start'])
                token.update(key=(calendar['value'], 'calendar_date'),
                             raw=full[calendar['start']:calendar['end']],
                             start=calendar['start'] - offset, end=calendar['end'] - offset)
                position = calendar['start']
            else:
                # A wrapped fragment retains only its selected date component;
                # never emit the unseen month/day from its source context.
                component = next((name for name, (lo, hi, _) in calendar['parts'].items()
                                  if position < hi and lo < offset + token['end']), None)
                if component is None:
                    raise ValueError('날짜 조각의 숫자 위치를 원문 달력 항목과 연결할 수 없음')
                token['key'] = (calendar['parts'][component][2], 'calendar_' + component)
            for name in ('calendar_endpoint', 'calendar_interval'):
                if name in calendar:
                    token[name] = calendar[name]
        sentence = _sentence_at(full, position)
        start = full.rfind(sentence, 0, position + len(sentence))
        prefix, suffix = full[start:position], full[position + len(token['raw']):start + len(sentence)]
        clinical = (token['key'][1].split('/')[0] in {'mg', 'mcg', 'ng', 'g', 'mL', 'IU'}
                    or re.search(r'\bdose\b|용량|투여|\bpen\b|펜|보관|refrigerat', sentence, re.I))
        material = _material_scope(full, position, names, source) if clinical or source.get('product_name') else {}
        scope = {key: str(value) for key, value in material.items()
                 if key in {'product', 'population', 'state', 'phase', 'quantity_basis', 'dose_modifier', 'dose_bound'}}
        # A prose dose ("The dose is increased to 5 mg") is not a strength
        # heading. Only a known product heading or explicit strength/form line
        # may override the independently supplied product variant.
        variant = _variant(source.get('product_variant', ''))
        line_end = full.find('\n', position)
        for line in full[:line_end if line_end >= 0 else len(full)].splitlines():
            heading = _strength_heading(line)
            if heading and (not heading['product'] or heading['product'].casefold() in names):
                variant = _variant(line)
        if material.get('quantity_basis'):
            variant = {**variant, 'container': material.get('variant', {}).get('container', variant.get('container'))}
        for key, value in variant.items():
            if value is not None:
                scope['variant_' + key] = str(value)
        states = list(re.finditer(r'(?P<before>\bbefore\s+(?:first\s+)?use\b|사용\s*전|개봉\s*전)|'
                                  r'(?P<after>\bafter\s+first\s+(?:opening|use)\b|개봉\s*후)|'
                                  r'(?P<unrefrigerated>\bunrefrigerated\b|냉장\s*외)', full[:position], re.I))
        if states and (clinical or source.get('product_name')):
            scope['state'] = states[-1].lastgroup
        if token['key'][1].split('/')[0] in {'mg', 'mcg', 'ng', 'g', 'mL', 'IU'}:
            roles = list(re.finditer(r'(?P<initial>\bstarting\s+dose\b|초기\s*용량|초회\s*용량)|'
                                     r'(?P<maintenance>유지\s*용량)|'
                                     r'(?P<maximum>\bmaximum\s+dose\b|최대\s*용량)|'
                                     r'(?P<minimum>\bminimum\s+dose\b|최소\s*용량)|'
                                     r'(?P<increase>\bincreased?\s+to\b|증량\s*(?:하여|해|한)?)|'
                                     r'(?P<current>\bwith\s+(?:a\s+)?dose\s+of\b|현재\s*용량)|'
                                     r'(?P<increment>\bdose\s+increases\b|증량\s*폭)', prefix, re.I))
            if roles:
                token['role'] = roles[-1].lastgroup
            if re.search(r'\bis\s+not\s+a\s+maintenance\s+dose\b|유지\s*용량이\s*아', suffix, re.I):
                token['role'] = 'not_maintenance'
            # Each explicitly stated current dose identifies a different transition.
            # Never use this token's own value as its distinguishing scope.
            if token['role'] == 'increase':
                current = re.search(r'\bwith\s+(?:a\s+)?dose\s+of\s+(\d+(?:\.\d+)?\s*mg)\b', prefix, re.I)
                elapsed = re.search(r'\bafter\s+(\d+)\s+(days?|weeks?)\b', prefix, re.I)
                if current:
                    scope['dose_step'] = 'from:' + re.sub(r'\s+', '', current[1]).casefold()
                elif elapsed:
                    scope['dose_step'] = 'after:' + elapsed[1] + '/' + elapsed[2].casefold().rstrip('s')
            elif token['role'] == 'current':
                following = re.search(r'\bincreased?\s+to\s+(\d+(?:\.\d+)?\s*mg)\b', suffix, re.I)
                if following:
                    scope['dose_step'] = 'to:' + re.sub(r'\s+', '', following[1]).casefold()
        elif token['key'][1] in {'week', 'day', 'month', 'year'}:
            if re.search(r'\b(?:after|minimum\s+of|at\s+least)\s*$', prefix, re.I) and re.search(r'\bdose\b|증량|투여', sentence, re.I):
                token['role'] = 'dose_interval'
            elif re.search(r'\b(?:pens?|vials?|syringes?)\b|펜|바이알', prefix, re.I):
                token['role'] = 'container_duration'
        token['material_scope'] = scope
        yield token


def find_conflicts(sources: list[dict]) -> list[dict]:
    """Compare quantities with compatible explicit roles and source-bound scopes.

    Unknown scope remains conservative. Normal dose transitions and adult/child
    limits are not contradictory observations; same-scope contradictions still
    block even when they occur in one source. This is not semantic certification.
    """
    from agent.ra import PRODUCT_LABEL, _context_source, _product_names
    from agent.review import ENGLISH_FUNCTION_WORDS

    # Metadata and explicit product labels are trusted names. Dose prose must
    # not nominate its first word as a product and its own value as a strength.
    names = _product_names([], sources, {})
    for source in sources:
        context, _ = _context_source(source)
        names.update(match[1].casefold() for match in PRODUCT_LABEL.finditer(context['text']))
        names.update(heading['product'].casefold() for line in context['text'].splitlines()
                     if (heading := _strength_heading(line)) and heading['product'])
    names -= ENGLISH_FUNCTION_WORDS
    groups = {}
    for source in sources:
        for token in _conflict_tokens(source, names):
            if token['context']:
                key = (token['context'], token['key'][1], tuple(sorted(token['scope'])))
                signature = (token['key'][0], token.get('entity', ''), token.get('role', ''),
                             tuple(sorted(token['material_scope'].items())),
                             token.get('calendar_endpoint', ''), token.get('calendar_interval', ()))
                observation = groups.setdefault(key, {}).setdefault(signature, (token, set()))
                observation[1].add(source['source_id'])
    conflicts = {}
    for key, observations in groups.items():
        entries = list(observations.values())
        # ponytail: compare distinct scoped observations, not repeated paragraph
        # tokens. A very large ambiguous group still needs a structured index.
        for index, (left, left_ids) in enumerate(entries):
            for right, right_ids in entries[index + 1:]:
                if left['key'][0] == right['key'][0]:
                    continue
                if (left.get('calendar_endpoint') and right.get('calendar_endpoint')
                        and left['calendar_endpoint'] != right['calendar_endpoint']):
                    continue
                if left.get('calendar_interval') and right.get('calendar_interval'):
                    # Disjoint reporting windows are different explicit periods;
                    # overlapping/same-window date disagreements remain conflicts.
                    lo, hi = left['calendar_interval'], right['calendar_interval']
                    if lo[1] < hi[0] or hi[1] < lo[0]:
                        continue
                if any(left.get(name) and right.get(name) and left[name] != right[name] for name in ('entity', 'role')):
                    continue
                left_scope, right_scope = left['material_scope'], right['material_scope']
                if any(left_scope[name] != right_scope[name] for name in left_scope.keys() & right_scope.keys()):
                    continue
                scope = {**left_scope, **right_scope}
                entity = left.get('entity') or right.get('entity', '')
                role = left.get('role') or right.get('role', '')
                identifier = (key, entity, role, tuple(sorted(scope.items())))
                conflict = conflicts.setdefault(identifier, {'context': key[0], 'unit': key[1], 'period': list(key[2]),
                                                             'entity': entity, 'role': role, 'material_scope': scope,
                                                             'values': [], 'source_ids': []})
                conflict['values'] = list(dict.fromkeys([*conflict['values'], str(left['key'][0]), str(right['key'][0])]))
                conflict['source_ids'] = sorted(set(conflict['source_ids']) | left_ids | right_ids)
    return list(conflicts.values())
