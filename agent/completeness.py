"""Independent content-completeness inspection; suggestions never modify a draft.

Passing client=None runs limited deterministic checks for offline tests. Passing a
client requires full model coverage; transport/schema failures block submission.
This checks editorial content. Source grounding and rendered-file layout remain
separate gates. Metadata fields never receive report sentence-ending rules.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import unicodedata

from agent.brief import model_profile, profile_fields
from agent.review import CITATION_PATTERN, TYPOS, _is_heading

CORE_FIELDS = {'제목', '요약', '본문'}
KINDS = {'empty_content', 'duplicate', 'contradiction', 'truncated', 'typo',
         'missing_requirement', 'unresolved_information', 'instruction_mismatch'}
PROMPT_PATH = Path(__file__).resolve().parents[1] / 'prompts' / 'completeness.md'
CONTENTLESS = re.compile(r'^(?:제목|요약|본문|보고서|결과보고서|주간업무보고|품의서|없음|작성\s*중|'
                         r'추가\s*확인\s*필요(?:함|임)?|미작성|미확정(?:함|임)?|TBD|TODO|N/?A|[?.…]+)$', re.I)


def completeness_fingerprint(draft: dict, brief: dict, profile: dict | None = None, *, instruction='', answers=None) -> str:
    """Bind approval to exact draft, instructions, registered fields, and prompt."""
    payload = {'draft': draft, 'brief': brief, 'profile': profile, 'instruction': instruction, 'answers': answers or {},
               'profile_projection': 'no-demo-v1',
               'prompt_sha256': sha256(PROMPT_PATH.read_bytes()).hexdigest()}
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _issue(field, line, kind, message, suggestion='', severity='error'):
    return {'field': field, 'line': line, 'kind': kind, 'severity': severity,
            'message': message, 'suggestion': suggestion}


def _clean(text):
    return re.sub(r'^[□○-]\s*', '', CITATION_PATTERN.sub('', unicodedata.normalize('NFC', text))).strip().rstrip('.。').strip()


def _model_issues(response, draft, allowed):
    if not isinstance(response, dict) or set(response) != {'issues', 'checked_fields'}:
        raise ValueError('완결성 검수 응답에는 issues와 checked_fields만 필요함')
    fields = response['checked_fields']
    if (not isinstance(fields, list) or any(not isinstance(field, str) or field not in allowed for field in fields)
            or len(set(fields)) != len(fields) or not set(draft).issubset(fields)):
        raise ValueError('완결성 검수 항목이 중복되거나 전체 초안 검수 범위가 누락됨')
    issues = response['issues']
    if not isinstance(issues, list) or len(issues) > 200:
        raise ValueError('완결성 검수 issues는 200개 이하 목록이어야 함')
    for issue in issues:
        if not isinstance(issue, dict) or set(issue) != {'field', 'line', 'kind', 'severity', 'message', 'suggestion'}:
            raise ValueError('완결성 문제 항목의 구조가 잘못됨')
        field, line = issue['field'], issue['line']
        if not isinstance(field, str) or field not in allowed or type(line) is not int or line < 0:
            raise ValueError('완결성 문제의 등록 항목·문장 위치가 잘못됨')
        if line > len(draft.get(field, '').splitlines()):
            raise ValueError('완결성 문제가 초안에 없는 줄을 가리킴')
        if issue['kind'] not in KINDS or issue['severity'] not in {'error', 'warning'}:
            raise ValueError('완결성 문제의 종류·심각도가 잘못됨')
        if (not isinstance(issue['message'], str) or not issue['message'].strip() or len(issue['message']) > 2000
                or not isinstance(issue['suggestion'], str) or len(issue['suggestion']) > 2000):
            raise ValueError('완결성 문제의 설명·제안 문자열이 잘못됨')
    return issues, fields


def check_completeness(draft: dict, brief: dict, profile: dict | None = None, client=None, *, instruction='', answers=None) -> dict:
    """Return issues/blocking/fingerprint, with no generated replacement values.

    line=0 means a whole-field or instruction issue; positive lines are one-based.
    Input metadata is untrusted data. Semantic suggestions are display-only and
    cannot invent consent, signatures, people, amounts, or unprovided facts.
    Offline deterministic success does not certify semantic completeness.
    """
    if not isinstance(draft, dict) or not isinstance(brief, dict):
        raise ValueError('완결성 검수에는 초안과 지시 분석 객체가 필요함')
    if not isinstance(instruction, str) or not isinstance(answers or {}, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in (answers or {}).items()):
        raise ValueError('원래 지시와 보완 답변은 문자열이어야 함')
    fields = profile_fields(profile)
    allowed = CORE_FIELDS | {field['value_key'] for field in fields}
    if any(not isinstance(key, str) or key not in allowed or not isinstance(value, str) for key, value in draft.items()):
        raise ValueError('초안에는 등록된 양식 항목의 문자열만 허용함')
    if sum(len(value) for value in draft.values()) > 200_000:
        raise ValueError('완결성 검수 초안은 20만 문자 이하로 나누어 작성해야 함')
    fingerprint = completeness_fingerprint(draft, brief, profile, instruction=instruction, answers=answers)
    required = CORE_FIELDS | {field['value_key'] for field in fields if field.get('required', True)}
    issues = []
    for field in sorted(required):
        text = draft.get(field, '')
        if not text.strip():
            issues.append(_issue(field, 0, 'empty_content', f'{field}: 필수 항목에 내용이 없음', '자료와 사용자 입력을 확인하여 작성해야 함'))
        elif field in CORE_FIELDS and not (field == '제목' and _clean(text) in {'보고서', '결과보고서', '주간업무보고', '품의서'}) and all(CONTENTLESS.fullmatch(_clean(line)) for line in text.splitlines() if line.strip()):
            issues.append(_issue(field, 0, 'empty_content', f'{field}: 실제 내용 없이 항목 이름이나 미완성 표시만 있음'))
    for field, text in draft.items():
        seen = set()
        for number, line in enumerate(text.splitlines(), 1):
            clean = _clean(line)
            if not clean:
                continue
            for typo in TYPOS:
                if typo in clean:
                    issues.append(_issue(field, number, 'typo', f'{field} {number}줄: 오탈자 후보 {typo}', TYPOS[typo]))
            if field in {'요약', '본문'}:
                if _is_heading(clean):
                    continue
                if clean in seen and len(clean) >= 8:
                    issues.append(_issue(field, number, 'duplicate', f'{field} {number}줄: 동일 문장이 중복됨'))
                seen.add(clean)
                if (clean.endswith((',', '，', ';', '；', ':', '：', '그리고', '하지만', '때문에', '하여'))
                        or any(clean.count(left) != clean.count(right) for left, right in [('(', ')'), ('[', ']'), ('“', '”'), ('「', '」')])):
                    issues.append(_issue(field, number, 'truncated', f'{field} {number}줄: 문장이 끊겼거나 괄호·인용부호가 닫히지 않음'))
    missing = brief.get('부족한 정보', [])
    if not isinstance(missing, list) or any(not isinstance(item, str) for item in missing):
        raise ValueError('지시 분석의 부족한 정보는 문자열 목록이어야 함')
    for item in dict.fromkeys(item.strip() for item in missing if item.strip()):
        issues.append(_issue('본문', 0, 'unresolved_information', f'지시 분석에서 아직 해결되지 않은 정보: {item}', '사용자 확인이나 원자료 보완이 필요함'))
    checked_fields = sorted(draft)
    semantic_checked, inspection_failed = False, False
    if client is not None:
        try:
            response = client.generate_json('completeness', {'draft': draft, 'brief': brief,
                                                            'instruction': instruction, 'answers': answers or {},
                                                            'profile': model_profile(profile), 'registered_fields': sorted(allowed)})
            generated, checked_fields = _model_issues(response, draft, allowed)
            issues.extend(generated)
            semantic_checked = True
        except Exception:
            # Fail closed; no transport payload, API key, or private report text in errors.
            issues.append(_issue('본문', 0, 'inspection_failure', '완결성 검수를 완료하지 못함. 다시 검수한 뒤 출력해야 함'))
            checked_fields, inspection_failed = [], True
    unique = []
    signatures = set()
    for issue in issues:
        signature = json.dumps(issue, ensure_ascii=False, sort_keys=True)
        if signature not in signatures:
            signatures.add(signature)
            unique.append(dict(issue))
    return {'fingerprint': fingerprint, 'issues': unique, 'warnings': unique,
            'blocking': any(issue['severity'] == 'error' for issue in unique),
            'semantic_checked': semantic_checked, 'inspection_failed': inspection_failed,
            'checked_fields': checked_fields, 'mode': 'semantic' if client is not None else 'deterministic'}
