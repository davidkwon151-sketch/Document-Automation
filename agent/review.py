"""결정론적 수치·출처·문체 검사와 최대 한 번의 수정.

의미상 사실 일치와 두괄식 여부는 정규식만으로 확정할 수 없으므로 사람 검토가 필요함.
"""

import re
from bisect import bisect_right
from decimal import Decimal

from agent.preferences import SAFE_TERMS
from agent.field_citations import (CITATION_PATTERN, is_selection_field,
                                   profile_field, split_field_citations)
from templates.value_rules import field_evidence_scalar, inspect_form_values

NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z\d])(?P<value>-?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>℃|°\s*[CF]|백만\s*원|억\s*원|만\s*원|천\s*원|원|%|퍼센트|명|건|개|회|시간|개월|년|월|일|분|초|억|만|"
    r"(?:mg|mcg|[µμ]g|ng|kg|g|mL|ml|L|IU)(?:\s*/\s*(?:\d+(?:\.\d+)?\s*)?"
    r"(?:kg|mL|ml|L|vial|day))?(?![A-Za-z/])|"
    r"(?i:years?|months?|weeks?|days?|hours?|minutes?|seconds?)(?![A-Za-z]))?"
)
ENGLISH_UNITS = {'mg', 'mcg', 'ng', 'kg', 'g', 'mL', 'L', 'IU',
                 'year', 'month', 'week', 'day', 'hour', 'minute', 'second'}
ENGLISH_FUNCTION_WORDS = {'is', 'are', 'was', 'were', 'of', 'to', 'from', 'at', 'in', 'for',
                          'and', 'or', 'the', 'a', 'an', 'contains', 'alternatively', 'after',
                          'before', 'than', 'by', 'with', 'as', 'section', 'see'}
DOSE_ROLE = re.compile(r'(?P<loading>\b(?:initial\s+)?loading\s+dose\b)|'
                       r'(?P<maintenance>\bmaintenance\s+dose\b)|'
                       r'(?P<lower_initial>\blower\s+initial\s+(?:therapeutic\s+)?dose\b)|'
                       r'(?P<initial>\binitial\s+(?:therapeutic\s+)?dose\b)|'
                       r'(?P<increase>\b(?:increased|increase)\s+to\b)', re.I)
ENGLISH_FREQUENCY = re.compile(
    r'\b(?P<count>once|twice|three\s+times)\s+(?P<period>daily|weekly)\b|'
    r'\b(?:at\s+)?(?P<interval>\d+|one|two|three|four|six|eight|twelve)[-\s]weekly\s+intervals?\b|'
    r'\bevery\s+(?P<every>\d+|one|two|three|four|six|eight|twelve)\s+(?P<every_unit>days?|weeks?)\b', re.I)
SENTENCE_BREAK = re.compile(r'(?<=[.!?])\s+|\n\s*\n|[;；|]')
MONEY_FACTORS = {"원": 1, "천원": 1000, "만원": 10000, "백만원": 1000000, "억원": 100000000}
CONTEXT_GROUPS = {
    "매출": ("매출액", "매출"),
    "비용": ("비용", "지출액", "지출"),
    "예산": ("예산액", "예산"),
    "이익": ("영업이익", "순이익", "이익"),
    "달성률": ("달성률", "달성율"),
    "인원": ("인원", "직원", "참석자", "참여자"),
    "기간": ("기간", "일정"),
    "목표": ("목표",),
}
TYPOS = {"됬": "됐", "됀": "된", "달성율": "달성률", "확인헸": "확인했", "프로잭트": "프로젝트"}
ROLE_PATTERN = re.compile(
    r"(?P<target>목표|계획|예정)|(?P<forecast>예측|예상|전망|추정)|"
    r"(?P<actual>실적|실제|실측|달성(?!률|율)|집행|발생|완료|(?<!미)확정)"
)
ENTITY_PATTERN = re.compile(
    r"(?P<label>회사|기업|법인|부서|사업부|팀)\s*[:：=]\s*"
    r"(?P<label_value>(?:㈜|\(주\))?[가-힣A-Za-z0-9][가-힣A-Za-z0-9_().·&-]{0,39})|"
    r"(?<![가-힣A-Za-z0-9])(?P<company>[A-Z][A-Za-z0-9_-]{0,19}사)"
    r"(?=$|\s|[은는이가의와,:;·/])|"
    r"(?<![가-힣A-Za-z0-9])(?P<department>[가-힣A-Za-z][가-힣A-Za-z0-9_-]{0,19}(?:사업부|부서|팀))"
    r"(?=$|\s|[은는이가의와,:;·/])"
)


def validate_draft(draft: dict, allowed_fields=None) -> dict:
    if not isinstance(draft, dict):
        raise ValueError("초안은 JSON 객체여야 함")
    result = {}
    for field in ("제목", "요약", "본문"):
        if not isinstance(draft.get(field), str) or not draft[field].strip():
            raise ValueError(f"초안에 비어 있지 않은 문자열이 필요함: {field}")
        result[field] = draft[field].strip()
    if len([line for line in result["요약"].splitlines() if line.strip()]) > 3:
        raise ValueError("핵심 요약은 3줄 이하여야 함")
    for field in draft.keys() - result.keys():
        if not isinstance(field, str) or not field.strip() or not isinstance(draft[field], str):
            raise ValueError("추가 양식 항목은 이름과 값이 문자열이어야 함")
        if allowed_fields is not None and field not in allowed_fields:
            raise ValueError(f"양식에 등록되지 않은 항목임: {field}")
        result[field] = draft[field].strip()
    return result


def _context(text: str, position: int) -> str:
    prefix = re.split(r"[\n,;·|/]", text[:position])[-1][-40:]
    for before, after in SAFE_TERMS.items():
        prefix = prefix.replace(before, after)
    found = [(prefix.rfind(word), name) for name, words in CONTEXT_GROUPS.items() if name != '목표' for word in words if word in prefix]
    if found:
        return max(found, key=lambda item: item[0])[1]
    words = re.findall(r"[가-힣A-Za-z]{2,}", prefix)
    return re.sub(r"(?:은|는|이|가|을|를|의|에서)$", "", words[-1]) if words else ""


def _number_role(prefix: str, suffix: str) -> str:
    """Use an explicit role before the value, or its local suffix when none precedes it.

    An unknown role remains unknown; this is a guard for clear target/forecast labels,
    not a claim that a regular expression establishes the factual meaning of a sentence.
    """
    matches = list(ROLE_PATTERN.finditer(prefix[-80:]))
    if not matches:
        matches = list(ROLE_PATTERN.finditer(suffix[:80]))
    return matches[-1].lastgroup if matches else ''


def _number_entity(prefix: str) -> str:
    """Recognize A사/B사 and explicit company/department labels without guessing names.

    Multiple names before a single value are ambiguous. Company and department labels
    may form a compound entity; an unlabeled company name is deliberately unknown.
    """
    names = {}
    positions = {}
    for match in ENTITY_PATTERN.finditer(prefix):
        label = match['label']
        kind = 'company' if match['company'] or label in {'회사', '기업', '법인'} else 'department'
        value = match['label_value'] or match['company'] or match['department']
        if kind in names and names[kind] != value:
            between = prefix[positions[kind]:match.start()]
            if not NUMBER_PATTERN.search(between):
                return ''
        names[kind], positions[kind] = value, match.end()
    return '/'.join(f'{kind}:{names[kind]}' for kind in ('company', 'department') if kind in names)


def _unit(unit: str) -> str:
    unit = re.sub(r'\s+', '', unit).replace('µg', 'mcg').replace('μg', 'mcg').replace('ml', 'mL')
    unit = unit.replace('℃', '°C')
    lowered = unit.casefold()
    for duration in ('year', 'month', 'week', 'day', 'hour', 'minute', 'second'):
        if lowered in {duration, duration + 's'}:
            return duration
    return unit


def _english_number_context(clean, match, previous_end, next_start, unit):
    """Explicit dose roles and local intervals; prose wrapping is not a boundary.

    Restrict suffix evidence to this quantity and sentence. This prevents a following
    maintenance sentence or alternative dose from re-labeling the preceding value.
    Unknown English wording remains unknown rather than becoming a field label.
    """
    prefix = SENTENCE_BREAK.split(clean[previous_end:match.start()])[-1][-160:]
    suffix = SENTENCE_BREAK.split(clean[match.end():next_start])[0][:200]
    if unit == 'kg' and re.search(r'\b(?:weighing|body\s*weight|weight)\b', prefix, re.I):
        # A patient weight is not a dose just because a wrapped dosing paragraph
        # follows it on the same display line.
        return 'body_weight', '', set()
    roles = list(DOSE_ROLE.finditer(prefix))
    role = roles[-1].lastgroup if roles else ''
    frequencies = list(ENGLISH_FREQUENCY.finditer(suffix))
    if not frequencies:
        frequencies = list(ENGLISH_FREQUENCY.finditer(prefix))
    scope = set()
    if frequencies:
        frequency = frequencies[0] if ENGLISH_FREQUENCY.search(suffix) else frequencies[-1]
        if frequency['count']:
            counts = {'once': '1', 'twice': '2', 'three times': '3'}
            count = counts[re.sub(r'\s+', ' ', frequency['count'].casefold())]
            scope.add(f"frequency:{count}/{frequency['period'].casefold()}")
        else:
            count = frequency['interval'] or frequency['every']
            counts = {'one': '1', 'two': '2', 'three': '3', 'four': '4', 'six': '6', 'eight': '8', 'twelve': '12'}
            count = counts.get(count.casefold(), count)
            period = 'week' if frequency['interval'] else frequency['every_unit'].casefold().rstrip('s')
            scope.add(f'interval:{count}/{period}')
    local = prefix + ' ' + suffix
    if role or scope or re.search(r'\b(?:dose|dosage|administered)\b', local, re.I):
        context = 'dose'
    elif re.search(r'\b(?:each\b.*\bcontains|per\s+(?:vial|syringe|tablet))\b', prefix, re.I):
        context = 'content'
    else:
        # PDF line wrapping must not change English labels such as "Unopened
        # vial". Korean table/role boundaries keep their existing behavior.
        preceding = re.sub(r'\s+', ' ', clean[:match.start()])
        context = _context(preceding, len(preceding))
        if context.casefold() in ENGLISH_FUNCTION_WORDS:
            context = ''
    return context, role, scope


def number_tokens(text: str, *, field_meta=None) -> list[dict]:
    """Preserve units, item, period, and explicitly stated role/entity; normalize KRW."""
    clean = CITATION_PATTERN.sub("", text)
    tokens = []
    matches = list(NUMBER_PATTERN.finditer(clean))
    for index, match in enumerate(matches):
        value = Decimal(match["value"].replace(",", ""))
        unit = _unit(match["unit"] or "")
        # Explicit ranges contain two endpoints, not contradictory observations.
        # Keep their positions and shared unit so altered endpoints still fail.
        range_scope = set()
        if index + 1 < len(matches) and re.fullmatch(r'\s*[~～–—]\s*', clean[match.end():matches[index + 1].start()]):
            unit = unit or _unit(matches[index + 1]['unit'] or '')
            range_scope.add('range:start')
        if index and re.fullmatch(r'\s*[~～–—]\s*', clean[matches[index - 1].end():match.start()]):
            unit = unit or _unit(matches[index - 1]['unit'] or '')
            range_scope.add('range:end')
        if unit in MONEY_FACTORS:
            key = (value * MONEY_FACTORS[unit], "원")
        else:
            key = (value, "%" if unit == "퍼센트" else unit)
        prefix = re.split(r"[\n,;·|/]", clean[: match.start()])[-1]
        suffix = re.split(r"[\n,;·|/]", clean[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(clean)])[0]
        scopes = re.findall(r"(?:20\d{2}\s*년|\d+\s*분기|상반기|하반기)", prefix)
        context, role = _context(clean, match.start()), _number_role(prefix, suffix)
        scope = {scope.replace(" ", "") for scope in scopes} | range_scope
        if unit.split('/')[0] in ENGLISH_UNITS:
            context, english_role, english_scope = _english_number_context(
                clean, match, matches[index - 1].end() if index else 0,
                matches[index + 1].start() if index + 1 < len(matches) else len(clean), unit)
            role = english_role or role
            scope.update(english_scope)
        raw = match.group().strip()
        tokens.append({"raw": raw, "start": match.start(), "end": match.start() + len(raw),
                       "key": key, "context": context, "scope": scope,
                       "role": role, "entity": _number_entity(prefix)})
    # A form can explicitly bind a bare fixed scalar to its evidence unit.
    # Keep the token's original position/context/scope and the input text intact.
    try:
        scalar = field_evidence_scalar(text, field_meta)
    except ValueError:
        scalar = None  # Scalar syntax errors are reported by the field validator.
    if scalar and len(tokens) == 1 and tokens[0]['key'] == (scalar[0], ''):
        tokens[0] = {**tokens[0], 'key': (scalar[0], _unit(scalar[1]))}
        if not tokens[0]['context']:
            label = field_meta.get('label', field_meta.get('value_key', ''))
            tokens[0]['context'] = _context(label, len(label))
            tokens[0]['context_from_field'] = True
    return tokens


def _warning(code: str, field: str, line: int, message: str, severity="warning") -> dict:
    return {"code": code, "field": field, "line": line, "message": message, "severity": severity}


def _is_heading(line: str) -> bool:
    return bool(re.fullmatch(r"[□○-]?\s*(?:결론|핵심 요약|주요 결과|업무 현황|추진 내용|향후 계획|요청 사항|추가 확인 필요|근거|검토 의견)\s*:?", line))


def is_factual_line(line: str) -> bool:
    clean = CITATION_PATTERN.sub("", line).strip()
    if not clean or _is_heading(clean):
        return False
    pending_only = re.sub(r'^[□○-]\s*', '', clean).rstrip('.。').strip()
    pending_pattern = r'(?:추가 확인 필요(?:함|임)?|(?:자료|근거)(?:가|는)? 부족(?:함|임|하여 추가 확인 필요함)?|미확정(?:임|함)?|확인되지 (?:않음|않았음))'
    if not number_tokens(clean) and re.fullmatch(pending_pattern, pending_only):
        return False
    if not number_tokens(clean) and re.fullmatch(
        r'(?:(?:자료|내용|첨부자료)\s*)?(?:확인|검토|회신|제출)(?:을|를)?\s*요청(?:드립니다|함|합니다)', pending_only
    ):
        return False
    # ponytail: 사실 여부는 보수적인 문장 규칙임. 의미 판정이 필요하면 별도 사람 평가로 보완함.
    return True


def _matching_candidates(token: dict, candidates: list[dict]) -> list[dict]:
    same_unit = [candidate for candidate in candidates
                 if candidate["key"][1] == token["key"][1]
                 and token["scope"].issubset(candidate["scope"])
                 and (not token.get('role') or not candidate.get('role') or token['role'] == candidate['role'])
                 and (not token.get('entity') or not candidate.get('entity') or token['entity'] == candidate['entity'])]
    if token["context"]:
        return [candidate for candidate in same_unit if candidate["context"] == token["context"]]
    return same_unit


def _field_number_tokens(field: str, line: str, *, tokens=None) -> list[dict]:
    tokens = number_tokens(line) if tokens is None else tokens
    if field not in {"제목", "요약", "본문"}:
        for token in tokens:
            if not token["context"] and token['key'][1].split('/')[0] not in ENGLISH_UNITS:
                token["context"] = _context(field, len(field))
                token['context_from_field'] = True
    return tokens


def _field_lines(field, value, *, field_meta=None, literal=None):
    """Keep line citations independent while binding numbers to their full paragraph."""
    lines = value.splitlines()
    literals = literal.splitlines() if isinstance(literal, str) else []
    clean_lines = [split_field_citations(line, field_meta, literal=literals[index] if index < len(literals) else None)[0]
                   for index, line in enumerate(lines)]
    starts, offset = [], 0
    for line in clean_lines:
        starts.append(offset)
        offset += len(line) + 1
    by_line = [[] for _ in lines]
    for token in ([] if is_selection_field(field_meta) else number_tokens('\n'.join(clean_lines), field_meta=field_meta)):
        index = bisect_right(starts, token['start']) - 1
        by_line[index].append({**token, 'start': token['start'] - starts[index],
                              'end': token['end'] - starts[index]})
    for index, line in enumerate(lines):
        # A confirmed JSON key may be renamed without changing its original
        # printed item. Numeric context belongs to that original field label.
        context_field = (field_meta.get('label', field) if field not in {'제목', '요약', '본문'}
                         and isinstance(field_meta, dict) else field)
        yield index + 1, line, _field_number_tokens(context_field, line, tokens=by_line[index])


def inspect_draft(draft: dict, sources: list[dict], *, style_exempt_fields=(), template_profile=None, literal_values=None) -> list[dict]:
    draft = validate_draft(draft)
    source_map = {source["source_id"]: source for source in sources}
    evidence_fields = [field for field in (template_profile or {}).get('fields', [])
                       if 'evidence_unit' in field.get('validation', {})]
    issues = inspect_form_values(draft, {'fields': evidence_fields}) if evidence_fields else []
    for field in draft:
        metadata = profile_field(template_profile, field)
        literal = (literal_values or {}).get(field)
        plain, identifiers = split_field_citations(draft[field], metadata, literal=literal)
        field_ids = set(identifiers)
        exact_quote = (len(field_ids) == 1 and field_ids <= source_map.keys()
                       and re.sub(r'\s+', '', plain)
                       == re.sub(r'\s+', '', source_map[next(iter(field_ids))]['text']))
        literal_lines = literal.splitlines() if isinstance(literal, str) else []
        for line_no, line, numeric_tokens in _field_lines(field, draft[field], field_meta=metadata, literal=literal):
            if not line.strip():
                continue
            clean, ids = split_field_citations(line, metadata, literal=literal_lines[line_no-1] if line_no <= len(literal_lines) else None)
            missing_ids = sorted(set(ids) - source_map.keys())
            if missing_ids:
                issues.append(_warning("unknown_source", field, line_no, f"알 수 없는 출처 ID: {', '.join(missing_ids)}", "error"))
            factual = (bool(clean) if is_selection_field(metadata) or isinstance(literal, str)
                       else bool(numeric_tokens) if field == "제목" else is_factual_line(line))
            if not ids and factual:
                issues.append(_warning("missing_source", field, line_no, "사실 문장에 출처 ID가 없음", "error"))
            candidates = [token for source_id in set(ids) if source_id in source_map for token in number_tokens(source_map[source_id]["text"])]
            for token in numeric_tokens:
                # A verbatim quote supplies its own context. A fallback inferred
                # solely from a form's label must not contradict that same quote.
                # Explicit roles, entities, periods, values and units still match.
                checked_token = {**token, 'context': ''} if exact_quote and token.get('context_from_field') else token
                matches = _matching_candidates(checked_token, candidates)
                if not any(candidate["key"] == token["key"] for candidate in matches):
                    issues.append(_warning("number_mismatch", field, line_no, f"인용 자료의 항목·단위와 수치가 일치하지 않음: {token['raw']}", "error"))
            clean = clean.rstrip(".!。 ")
            if field in {"요약", "본문"} and field not in style_exempt_fields and not _is_heading(clean):
                if not re.match(r"^[□○-]\s*", clean):
                    issues.append(_warning("bullet_style", field, line_no, "개조식 기호(□, ○, -)가 필요함"))
                if not clean.endswith(("함", "임")):
                    issues.append(_warning("ending_style", field, line_no, "문장 종결은 ~함 또는 ~임이어야 함"))
            for typo in TYPOS:
                if typo in clean:
                    issues.append(_warning("typo", field, line_no, f"표기 수정 필요: {typo} → {TYPOS[typo]}"))
    combined = "\n".join(draft.values())
    if "리포트" in combined and "보고서" in combined:
        issues.append(_warning("term_inconsistency", "본문", 0, "리포트와 보고서 용어가 혼용됨"))
    return issues


def _safe_correction(draft: dict, sources: list[dict], *, style_exempt_fields=(), template_profile=None, literal_values=None) -> dict:
    source_map = {source["source_id"]: source for source in sources}
    result = dict(draft)
    for field in draft:
        metadata = profile_field(template_profile, field)
        if (is_selection_field(metadata) or field in (literal_values or {})
                or metadata and 'evidence_unit' in metadata.get('validation', {})):
            continue
        corrected_lines = []
        for _, line, numeric_tokens in _field_lines(field, draft[field]):
            if not line.strip():
                corrected_lines.append(line)
                continue
            ids = CITATION_PATTERN.findall(line)
            citations = " ".join(f"[{source_id}]" for source_id in dict.fromkeys(ids))
            clean = CITATION_PATTERN.sub("", line).strip().rstrip(".!。 ")
            candidates = [token for source_id in set(ids) if source_id in source_map for token in number_tokens(source_map[source_id]["text"])]
            replacements = []
            for token, local in zip(numeric_tokens, number_tokens(clean)):
                # A unit wrapped onto another line cannot be replaced atomically here.
                if token['key'][1] != local['key'][1]:
                    continue
                token = {**token, 'start': local['start'], 'end': local['end']}
                matches = _matching_candidates(token, candidates)
                if any(candidate["key"] == token["key"] for candidate in matches):
                    continue
                values = {candidate["key"] for candidate in matches}
                if len(values) == 1:
                    replacements.append((token["start"], token["end"], matches[0]["raw"]))
            for start, end, replacement in sorted(replacements, reverse=True):
                clean = clean[:start] + replacement + clean[end:]
            for before, after in TYPOS.items():
                clean = clean.replace(before, after)
            if "리포트" in "\n".join(draft.values()) and "보고서" in "\n".join(draft.values()):
                clean = clean.replace("리포트", "보고서")
            if field in {"요약", "본문"} and field not in style_exempt_fields and not _is_heading(clean):
                if not re.match(r"^[□○-]\s*", clean):
                    clean = "○ " + clean
                clean = re.sub(r"(?:입니다|이다)$", "임", clean)
                clean = re.sub(r"(?:하였습니다|했습니다|합니다|하였다|했다|한다)$", "함", clean)
                clean = re.sub(r"(?:되었음|됐음|되었다|됐다)$", "됨", clean)
                if not clean.endswith(("함", "임")):
                    if re.search(r"(?:완료|달성|진행|수행|실시|검토|확인)$", clean):
                        clean += "함"
                    elif number_tokens(clean) or clean.endswith(("필요", "예정")):
                        clean += "임" if number_tokens(clean) else "함"
            corrected_lines.append(f"{clean} {citations}".rstrip())
        result[field] = "\n".join(corrected_lines)
    return result


def _preserves_supported_numbers(original: dict, candidate: dict, sources: list[dict], *, template_profile=None, literal_values=None) -> bool:
    source_map = {source["source_id"]: source for source in sources}
    for field, value in original.items():
        if is_selection_field(profile_field(template_profile, field)):
            continue
        required = set()
        metadata = profile_field(template_profile, field)
        for _, line, numeric_tokens in _field_lines(field, value, field_meta=metadata):
            evidence = [token for source_id in CITATION_PATTERN.findall(line) if source_id in source_map for token in number_tokens(source_map[source_id]["text"])]
            for token in numeric_tokens:
                if any(item["key"] == token["key"] for item in _matching_candidates(token, evidence)):
                    required.add(token["key"])
        if not required <= {token["key"] for token in number_tokens(candidate.get(field, ""), field_meta=metadata)}:
            return False
    return True


def review_draft(draft: dict, sources: list[dict], client=None, *, auto_correct=True, style_exempt_fields=(), template_profile=None, literal_values=None) -> dict:
    draft = validate_draft(draft)
    options = {'style_exempt_fields': style_exempt_fields, 'template_profile': template_profile, 'literal_values': literal_values}
    initial = inspect_draft(draft, sources, **options)
    corrected = False
    reviewed = draft
    if initial and auto_correct:
        candidate = _safe_correction(draft, sources, **options)
        if candidate != draft:
            reviewed = candidate
            corrected = True
        elif client is not None:
            response = client.generate_json("review", {"draft": draft, "sources": sources, "issues": initial, "style_exempt_fields": list(style_exempt_fields)})
            candidate = validate_draft(response.get("draft", response) if isinstance(response, dict) else response, allowed_fields=set(draft))
            for field in draft.keys() - candidate.keys():
                candidate[field] = draft[field]
            for field in draft:
                metadata = profile_field(template_profile, field)
                if (is_selection_field(metadata) or field in (literal_values or {})
                        or metadata and 'evidence_unit' in metadata.get('validation', {})):
                    candidate[field] = draft[field]
            candidate_issues = inspect_draft(candidate, sources, **options)
            original_errors = sum(issue["severity"] == "error" for issue in initial)
            new_errors = sum(issue["severity"] == "error" for issue in candidate_issues)
            if new_errors <= original_errors and not any(issue["code"] == "unknown_source" for issue in candidate_issues) and _preserves_supported_numbers(draft, candidate, sources, template_profile=template_profile, literal_values=literal_values):
                reviewed = candidate
                corrected = candidate != draft
    warnings = inspect_draft(reviewed, sources, **options)
    return {"draft": reviewed, "warnings": warnings, "corrected": corrected, "blocking": any(item["severity"] == "error" for item in warnings)}
