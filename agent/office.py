"""기획·재무·일상 기안·산업 품질 문서의 근거 대조만 수행함."""

import re
from decimal import Decimal

from agent.business import IDENTIFIER, _compatible, _identifiers, _money, _positive_statuses, _scope
from agent.ra import AMBIGUOUS_DATE, IDENTIFIER as QUALITY_ID, _clauses, _dates, _ids, _scope as _quality_scope
from agent.review import CITATION_PATTERN, _number_entity, _number_role, number_tokens

DART = 'https://dart.fss.or.kr/guide/main.jsp?menu=410'
FINANCIAL = 'https://www.samsungcnt.com/eng/ir/financial-info/audited-report.do'
GUIDANCE = 'https://www.samsung.com/global/ir/reports-disclosures/public-disclosure-view.84695/'
MANUAL = 'https://www.mois.go.kr/frt/bbs/type001/commonSelectBoardArticle.do?bbsId=BBSMSTR_000000000012&nttId=122878'
KOLAS = 'https://www.knab.go.kr/en/Testing_Lab.do'
CORRECTION = 'https://www.knab.go.kr/en/Accreditation_Process.do'
OFFICE_WORKFLOWS = {
    'office_planning': {'title': '기획·전략·예산·경영계획', 'checks': [
        '추가 확인사항: 목적·의사결정 대상·계획/예산·실적·자료 기준일을 구분함',
        '추가 확인사항: 회사·사업부·연도/분기·매출/이익·계획/전망/실적을 원자료와 대조함',
        '추가 확인사항: 승인 요청과 실제 승인 상태, 집행 근거와 담당자를 확인함'],
        'source_urls': [DART, GUIDANCE, MANUAL]},
    'financial_report': {'title': '재무·금융·투자 검토', 'checks': [
        '추가 확인사항: 원공시와 정정·감사/검토·잠정 발표 여부, 연결/별도 및 기간을 확인함',
        '추가 확인사항: 통화·원/천원/백만원·항목·%/%p/bp·분모·수익/위험 표현을 대조함',
        '추가 확인사항: 투자 판단·보장 조건·가정·환율 기준은 담당자가 원문으로 확인함'],
        'source_urls': [DART, FINANCIAL, GUIDANCE]},
    'daily_approval': {'title': '일상보고·구매·출장·비용품의', 'checks': [
        '추가 확인사항: 목적·요청 사항·견적/예산·일정·근거 자료를 확인함',
        '추가 확인사항: 신청·검토·승인·집행·정산을 구분하고 직접 입력·서명 사항을 확인함',
        '추가 확인사항: 부서의 실제 결재선과 원본 양식 기준을 확인함'],
        'source_urls': [MANUAL]},
    'industrial_quality': {'title': '제조 품질·개선·CAPA·납품', 'checks': [
        '추가 확인사항: 제품/로트·시험번호·규격 버전·시험 계획과 실제 검사 결과를 대조함',
        '추가 확인사항: 부적합·원인·시정조치 계획·수행·효과 확인·승인 상태를 구분함',
        '추가 확인사항: 시험성적서의 기관·인정 범위·단위·자료 버전을 확인함'],
        'source_urls': [KOLAS, CORRECTION]},
}

CATEGORIES = {
    'revenue': r'매출(?:액)?|revenue|sales',
    'operating_margin': r'영업이익률|operating\s+margin',
    'operating_profit': r'영업이익|operating\s+(?:profit|income)',
    'net_profit': r'당기순이익|순이익|net\s+(?:profit|income)',
    'budget': r'예산|budget', 'spent': r'집행(?:액)?|지출(?:액)?|spent|expense',
    'assets': r'자산(?:총계)?|assets', 'liabilities': r'부채(?:총계)?|liabilities',
    'equity': r'자본(?:총계)?|equity', 'cash': r'현금흐름|cash\s+flow',
    'interest': r'금리|이자율|interest\s+rate', 'yield': r'수익률|yield|return\s+rate',
    'defect': r'불량(?:률|건수|수)?|부적합(?:건수|수)?|defect',
    'inspection': r'검사(?:계획|실적|결과|수량)?|시험(?:계획|실적|결과|수량)?|test|inspection',
    'total': r'합계|총액|total',
}
PERCENT = re.compile(r'(?<![A-Za-z\d])(?P<value>-?\d[\d,]*(?:\.\d+)?)\s*'
                     r'(?P<unit>%p|%|퍼센트포인트|퍼센트|basis\s+points?|bps?|p\.p\.)(?![A-Za-z])', re.I)
BASIS = re.compile(r'연결|별도|개별|consolidated|separate|standalone', re.I)
QUALITY_COMPLETE = re.compile(r'(?:검사|시험|시정조치|개선조치|CAPA)(?:가|을|는)?\s*(?:완료|종결|검증)(?:함|됨|되었|했)?', re.I)
GUARANTEE = re.compile(r'(?:수익|수익률|원금)(?:을|이|은)?\s*(?:보장|확정)(?:함|됨|되었|한다)?|'
                       r'\b(?:guaranteed\s+(?:return|profit|principal)|risk[- ]free\s+return)\b', re.I)


def _category(prefix):
    found = [(m.start(), len(m.group()), category) for category, pattern in CATEGORIES.items()
             for m in re.finditer(pattern, prefix[-100:], re.I)]
    return max(found, default=(0, 0, ''), key=lambda item: (item[0], item[1]))[2]


def _basis(prefix):
    found = list(BASIS.finditer(prefix))
    if not found:
        return ''
    return 'consolidated' if found[-1].group().casefold() in {'연결', 'consolidated'} else 'separate'


def _period(prefix):
    years = {f'year:{m[1]}' for m in re.finditer(r'(20\d{2})\s*년|FY\s*(20\d{2})', prefix, re.I) if m[1]}
    years |= {f'year:{m[2]}' for m in re.finditer(r'(20\d{2})\s*년|FY\s*(20\d{2})', prefix, re.I) if m[2]}
    quarters = {f'quarter:{m[1] or m[2]}' for m in re.finditer(r'([1-4])\s*분기|Q([1-4])', prefix, re.I)}
    return years | quarters | set(re.findall(r'상반기|하반기', prefix))


def _role(prefix, suffix):
    explicit = list(re.finditer(r'확정\s*실적|검사\s*실적|검사\s*결과|시험\s*실적|시험\s*결과|실적|실측|actual|'
                               r'계획|목표|planned|target|전망|추정|예측|잠정|forecast|estimate|guidance', prefix[-80:], re.I))
    if explicit:
        text = explicit[-1].group().casefold()
        if re.search(r'실적|실측|결과|actual', text):
            return 'actual'
        return 'forecast' if re.search(r'전망|추정|예측|잠정|forecast|estimate|guidance', text) else 'target'
    return _number_role(prefix, suffix)


def _token(text, start, end, key, raw):
    prefix, suffix = text[:start], text[end:]
    return dict(key=key, raw=raw, start=start, end=end, context=_category(prefix), burden_kind='',
                scope=_period(prefix), ids=_scope(text, start) | _quality_scope(text, start),
                role=_role(prefix, suffix), entity=_number_entity(prefix), vat='', basis=_basis(prefix))


def _tokens(text):
    tokens = [_token(text, item['start'], item['end'], item['key'], item['raw']) for item in _money(text)]
    for m in PERCENT.finditer(text):
        unit = m['unit'].casefold()
        unit = '%p' if unit in {'%p', '퍼센트포인트', 'p.p.'} else ('%' if unit in {'%', '퍼센트'} else 'bp')
        tokens.append(_token(text, m.start(), m.end(), (Decimal(m['value'].replace(',', '')), unit), m.group()))
    skip = [(t['start'], t['end']) for t in tokens]
    skip += [(m.start(), m.end()) for pattern in [IDENTIFIER, QUALITY_ID, AMBIGUOUS_DATE] for m in pattern.finditer(text)]
    skip += [(item[2], item[3]) for item in _dates(text)]
    skip += [(m.start(), m.end()) for m in re.finditer(r'20\d{2}\s*년|[1-4]\s*분기|FY\s*20\d{2}|Q[1-4]', text, re.I)]
    for item in number_tokens(text):
        if not any(start <= item['start'] < end for start, end in skip):
            tokens.append(_token(text, item['start'], item['end'], item['key'], item['raw']))
    return tokens


def _positive(pattern, text):
    found = []
    for m in pattern.finditer(text):
        prefix, suffix = text[:m.start()][-30:], text[m.end():m.end() + 20]
        if re.search(r'(?:not(?:\s+yet)?(?:\s+been)?|never)\s*$', prefix, re.I):
            continue
        if re.match(r'\s*(?:되지\s*(?:않|못)|하지\s*(?:않|못)|아님|여부|인지|\?|예정|계획)', suffix):
            continue
        found.append(m)
    return found


def _quality_kind(match):
    return 'corrective_action' if re.search(r'시정|개선|CAPA', match.group(), re.I) else 'inspection'


def inspect_office_draft(draft, sources, *, profile=None):
    """근거 확인 결과를 반환함. 원문·초안·단위·투자 판단을 변경하지 않음."""
    if not isinstance(draft, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in draft.items()):
        raise ValueError('업무 초안은 항목명과 값이 문자열인 객체여야 함')
    if not isinstance(sources, list) or profile is not None and not isinstance(profile, dict):
        raise ValueError('원자료 목록 또는 업무 프로파일 형식이 잘못됨')
    source_map = {}
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get('text'), str) or not isinstance(source.get('source_id'), str):
            raise ValueError('원자료에는 문자열 source_id와 text가 필요함')
        if not re.fullmatch(r'S[A-Za-z0-9_-]+', source['source_id']) or source['source_id'] in source_map:
            raise ValueError('원자료 출처 ID가 유효하지 않거나 중복됨')
        source_map[source['source_id']] = source['text']
    workflow = (profile or {}).get('office_workflow')
    if workflow is not None and (not isinstance(workflow, str) or workflow not in OFFICE_WORKFLOWS):
        raise ValueError('알 수 없는 사무·재무 업무 유형임')
    issues = []

    def issue(code, field, line_no, message, severity='error'):
        issues.append(dict(code=code, field=field, line=line_no, message=message, severity=severity))

    for field, text in draft.items():
        for line_no, raw_line in enumerate(text.splitlines(), 1):
            ids = CITATION_PATTERN.findall(raw_line)
            if set(ids) - source_map.keys():
                issue('office_unknown_source', field, line_no, '알 수 없는 출처 ID가 있음')
            evidence = [part for source_id in dict.fromkeys(ids) if source_id in source_map for part in _clauses(source_map[source_id])]
            clean = CITATION_PATTERN.sub('', raw_line).strip()
            if not clean:
                continue
            clean = clean if field in {'제목', '요약', '본문'} else field + ': ' + clean
            for kind, identifier in _identifiers(clean) | _ids(clean):
                if not any((kind, identifier) in (_identifiers(part) | _ids(part)) for part in evidence):
                    issue('office_identifier_mismatch', field, line_no, f'과제·거래·시험/로트 번호가 원자료와 일치하지 않음: {identifier}')
            for value, original, position, _ in _dates(clean):
                scope = _scope(clean, position) | _quality_scope(clean, position)
                if not any(value and value == item[0] and scope.issubset(_scope(part, item[2]) | _quality_scope(part, item[2]))
                           for part in evidence for item in _dates(part)):
                    issue('office_date_mismatch', field, line_no, f'날짜가 인용 원자료와 일치하지 않음: {original}')
            if AMBIGUOUS_DATE.search(clean):
                issue('office_ambiguous_date', field, line_no, '모호한 날짜는 원자료 확인이 필요함', 'warning')
            for clause in _clauses(clean):
                period = _period(clause)
                if period and not any(period.issubset(_period(part)) for part in evidence):
                    issue('office_period_mismatch', field, line_no, '연도·분기·반기가 인용 원자료와 일치하지 않음')
                candidates = [item for part in evidence for item in _tokens(part)]
                for token in _tokens(clause):
                    matches = [item for item in candidates if token['key'] == item['key'] and _compatible(token, item)
                               and (not token['basis'] or not item['basis'] or token['basis'] == item['basis'])]
                    if not matches:
                        issue('office_number_mismatch', field, line_no,
                              f'항목·통화/단위·연결/별도·기간·계획/실적·회사/시험 문맥이 원자료와 일치하지 않음: {token["raw"]}')
                    elif token['basis'] and all(not item['basis'] for item in matches):
                        issue('office_basis_unverified', field, line_no, '연결/별도 기준을 원자료에서 확인할 수 없음', 'warning')
                    if matches and token['role'] == 'actual' and all(not item['role'] for item in matches):
                        issue('office_actual_status_unverified', field, line_no, '확정/실측 실적 상태를 원자료에서 확인할 수 없음')
                    if token['key'][1] == '$':
                        issue('office_ambiguous_currency', field, line_no, '$ 국가 통화는 원자료 확인이 필요함', 'warning')
                for kind, start, _ in _positive_statuses(clause):
                    if kind not in {'approval', 'achievement'}:
                        continue
                    expected_scope = _scope(clause, start) | _quality_scope(clause, start)
                    if not any(kind == candidate[0] and expected_scope.issubset(_scope(part, candidate[1]) | _quality_scope(part, candidate[1]))
                               for part in evidence for candidate in _positive_statuses(part)):
                        issue('office_status_unverified', field, line_no, '승인·성과 달성 상태를 인용 원자료에서 확인할 수 없음')
                for m in _positive(QUALITY_COMPLETE, clause):
                    expected_scope = _scope(clause, m.start()) | _quality_scope(clause, m.start())
                    if not any(_quality_kind(m) == _quality_kind(candidate)
                               and expected_scope.issubset(_scope(part, candidate.start()) | _quality_scope(part, candidate.start()))
                               for part in evidence for candidate in _positive(QUALITY_COMPLETE, part)):
                        issue('office_quality_status_unverified', field, line_no, '검사·시정조치 완료 상태를 인용 원자료에서 확인할 수 없음')
                for m in _positive(GUARANTEE, clause):
                    expected_scope = _scope(clause, m.start()) | _quality_scope(clause, m.start())
                    entity = _number_entity(clause[:m.start()])
                    if not any(expected_scope.issubset(_scope(part, candidate.start()) | _quality_scope(part, candidate.start()))
                               and (not entity or not _number_entity(part[:candidate.start()]) or entity == _number_entity(part[:candidate.start()]))
                               for part in evidence for candidate in _positive(GUARANTEE, part)):
                        issue('office_guarantee_unverified', field, line_no, '원금·수익 보장 주장을 인용 원자료에서 확인할 수 없음')
                    else:
                        issue('office_guarantee_conditions', field, line_no, '원자료의 보장 주장임; 조건·상대·기간·위험을 담당자가 확인해야 함', 'warning')
    blocking = any(item['severity'] == 'error' for item in issues)
    return dict(domain='office_finance', workflow=workflow, blocking=blocking, issues=issues,
                checks=[{'name': '수치·단위·연결/별도·기간·항목·계획/실적·승인 대조', 'status': 'failed' if blocking else 'passed'},
                        {'name': '환율·합계·투자추천·법정 적합성 자동 판정', 'status': 'disabled'},
                        {'name': '법적/품질 적합성 및 수익 보장 인증', 'status': 'not_certified'}],
                additional_checks=list(OFFICE_WORKFLOWS[workflow]['checks']) if workflow else [])
