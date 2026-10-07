"""거래·지원사업 자료 대조. 계산, 거래 결정, 법적 적합성 인증은 수행하지 않음."""

import re
from decimal import Decimal

from agent.ra import AMBIGUOUS_DATE, _clauses, _dates
from agent.review import CITATION_PATTERN, _matching_candidates, _number_entity, _number_role, number_tokens

KOTRA = 'https://www.kotra.or.kr/subList/20000006014?targetId=e'
KITA = 'https://membership.kita.net/cert/sec/requestHelp.do'
CUSTOMS = 'https://www2.customs.go.kr/kcs/cm/cntnts/cntntsView.do?cntntsId=818&mi=2820'
VOUCHER = 'https://www.exportvoucher.com/portal/bizinfo/voucher_01'
SETTLEMENT = 'https://www.exportvoucher.com/portal/board/boardList?bbs_id=9'
STARTUP = 'https://www.kised.or.kr/menu.es?mid=a10205020000'
IRIS = 'https://www.iris.go.kr/contents/retrieveBsnsAncmView.do?ancmId=024139&ancmPrg=ancmIng'

BUSINESS_WORKFLOWS = {
    'trade_sales': {'title': '종합상사·해외영업', 'checks': [
        '추가 확인사항: 견적·계약·발주·선적·대금회수의 실제 단계를 구분함',
        '추가 확인사항: 거래 상대·국가·품목·HS 코드·통화·단가·수량·Incoterms·지정장소·납기를 원문과 대조함',
        '추가 확인사항: 수출실적 주장과 계약·신고·선적·입금 증빙의 범위를 확인함',
        '추가 확인사항: 서명·계좌·수취인·계약 승인은 담당자가 직접 확인함'],
        'source_urls': [KOTRA, KITA, CUSTOMS]},
    'overseas_business': {'title': '해외사업·해외 진출', 'checks': [
        '추가 확인사항: 사업 국가·거래 또는 프로젝트 번호·추진 단계·일정·현지 자료의 기준일을 확인함',
        '추가 확인사항: 시장·파트너·투자·계약 자료의 추정치와 확정 실적을 구분함',
        '추가 확인사항: 환율 기준일·통화·비용 범위를 확인하고 거래 결정을 담당자가 승인함'],
        'source_urls': [KOTRA, CUSTOMS, KITA]},
    'government_grant': {'title': '정부 지원사업 신청·수행·정산', 'checks': [
        '추가 확인사항: 현재 개별 공고·사업연도·신청기간·자격·서식·첨부 목록을 확인함',
        '추가 확인사항: 신청·선정·협약·집행·검수·정산·지급 상태와 증빙을 구분함',
        '추가 확인사항: 정부지원금·기업부담금·현금/현물·VAT·사용기간은 해당 공고와 협약을 대조함',
        '추가 확인사항: 서명·계좌·수취인·확약은 사용자 직접 확인으로 처리함'],
        'source_urls': [VOUCHER, SETTLEMENT, STARTUP]},
    'rd_project': {'title': '정부 R&D 계획·협약·성과 보고', 'checks': [
        '추가 확인사항: 공고·과제 번호·기관·책임자·접수 및 기관 승인 일정을 현재 공고에서 확인함',
        '추가 확인사항: 계획 목표·연차·성과지표·실제 시험/수행 결과를 구분함',
        '추가 확인사항: 정부지원·기관부담 연구개발비·현금/현물·변경 협약·증빙의 버전을 대조함',
        '추가 확인사항: 의무·자격·지원 비율은 해당 과제의 공고와 협약에 한정하여 확인함'],
        'source_urls': [IRIS, 'https://www.iris.go.kr/main.do']},
}

ISO = 'USD|EUR|JPY|CNY|KRW|GBP|AUD|CAD|CHF|SGD|HKD|AED|INR|VND|THB|IDR'
CUR = rf'(?:{ISO}|US\$|미화|미국달러|미달러|유로|엔화|위안|₩|\$)'
VAL = r'-?\d[\d,]*(?:\.\d+)?'
SCALE = r'(?:billion|million|천|만|억)?'
MONEY = re.compile(rf'(?<![A-Za-z\d])(?:(?P<c1>{CUR})\s*(?P<v1>{VAL})\s*(?P<s1>{SCALE})|'
                   rf'(?P<v2>{VAL})\s*(?P<s2>{SCALE})\s*(?P<c2>{CUR}))(?![A-Za-z])', re.I)
SCALES = {'': Decimal(1), '천': Decimal(1000), '만': Decimal(10000), '억': Decimal(100000000),
          'million': Decimal(1000000), 'billion': Decimal(1000000000)}
CUR_ALIASES = {'US$': 'USD', '미화': 'USD', '미국달러': 'USD', '미달러': 'USD',
               '유로': 'EUR', '엔화': 'JPY', '위안': 'CNY', '₩': 'KRW'}
IDENTIFIER = re.compile(r'(?P<label>프로젝트\s*번호|과제\s*번호|사업\s*번호|공고\s*번호|계약\s*번호|거래\s*번호|'
                        r'(?:Project|Contract|Order|PO|Reference|Req\.?)\s*(?:No\.?|ID))\s*[:：#=]?\s*(?:제)?'
                        r'(?P<value>[A-Za-z0-9][A-Za-z0-9_./-]{0,79})(?:호)?', re.I)
HS = re.compile(r'\bHS(?:\s*(?:CODE|코드|부호))?\s*[:：]?\s*(?P<code>\d{4}(?:[. -]?\d{2}){0,3})(?!\d)', re.I)
INCOTERM = re.compile(r'\b(?:EXW|FCA|CPT|CIP|FAS|FOB|CFR|CIF|DAP|DPU|DDP)\b', re.I)
COUNTRY = re.compile(r'(?:거래국가|수출국|대상국|국가|Country|Destination)\s*[:：]\s*'
                     r'(?P<country>United\s+States|United\s+Kingdom|South\s+Korea|New\s+Zealand|'
                     r'[가-힣A-Za-z]{2,30})', re.I)
COUNTRY_ALIASES = {'미국': 'US', 'usa': 'US', 'us': 'US', 'unitedstates': 'US',
    '한국': 'KR', '대한민국': 'KR', 'southkorea': 'KR', 'kr': 'KR', '일본': 'JP', 'japan': 'JP', 'jp': 'JP',
    '중국': 'CN', 'china': 'CN', 'cn': 'CN', '영국': 'GB', 'uk': 'GB', 'unitedkingdom': 'GB', 'gb': 'GB',
    '독일': 'DE', 'germany': 'DE', 'de': 'DE', '베트남': 'VN', 'vietnam': 'VN', 'vn': 'VN'}
CATEGORIES = {
    'grant': r'정부지원(?:연구개발비|사업비|금)?|국고|보조금|정부지원금|government\s+(?:grant|fund)',
    'self': r'자(?:기|비)?부담(?:금|사업비)?|기업부담금|기관부담(?:연구개발비)?|self[- ]fund|matching\s+fund',
    'vat': r'부가(?:가치)?세|VAT', 'total': r'총(?:사업비|금액|액)|합계|total',
    'price': r'단가|unit\s+price', 'amount': r'계약금액|견적금액|금액|amount|contract\s+value',
    'export': r'수출(?:액|실적|목표|계획)?|export', 'delivery': r'납기|선적기한|인도일|배송일|delivery|shipment\s+date',
    'application': r'신청기간|접수기간|신청마감|접수마감|마감일|application|deadline',
}
CLAIMS = {
    'approval': re.compile(r'승인(?:을|이|은|가)?\s*(?:완료|확정|취득|받음|받았|됨|되었)|\bapproved\b', re.I),
    'selection': re.compile(r'선정(?:을|이|은|가)?\s*(?:완료|확정|받음|됨|되었)|선정결과\s*[:：]\s*선정|\bselected\b', re.I),
    'receipt': re.compile(r'(?:보조금|지원금|국고)(?:을|이|은|가)?\s*(?:수령|입금|지급\s*완료)|'
                          r'\b(?:grant|subsidy)\s+(?:was\s+|has\s+been\s+)?(?:received|paid)\b', re.I),
    'export': re.compile(r'수출(?:을|이|은)?\s*(?:완료|달성|실현)|\bexport\s+(?:was\s+)?completed\b', re.I),
    'achievement': re.compile(r'(?:성과|목표)(?:를|가|은)?[^;\n]{0,30}?달성(?:함|됨|하였|했)|\bachieved\b', re.I),
}


def _normal(text):
    return re.sub(r'\s+|[□○:：,;]', '', CITATION_PATTERN.sub('', text)).casefold()


def _identifiers(text):
    return {(_id_kind(m['label']), m['value']) for m in IDENTIFIER.finditer(text)}


def _id_kind(label):
    return 'project' if re.search(r'과제|사업|프로젝트|project', label, re.I) else ('notice' if '공고' in label else 'transaction')


def _scope(text, position):
    found = {}
    for m in IDENTIFIER.finditer(text[:position]):
        found[_id_kind(m['label'])] = m['value']
    for kind, value in _identifiers(text):
        if kind not in found and sum(k == kind for k, _ in _identifiers(text)) == 1:
            found[kind] = value
    return set(found.items())


def _category(prefix):
    matches = [(m.start(), name) for name, pattern in CATEGORIES.items() for m in re.finditer(pattern, prefix[-100:], re.I)]
    return max(matches, default=(0, ''), key=lambda item: item[0])[1]


def _vat_mode(text):
    modes = {m['mode'].casefold() for m in re.finditer(r'(?:VAT|부가(?:가치)?세)\s*(?:는|를|이)?\s*'
             r'(?P<mode>포함|별도|제외|inclusive|included|exclusive|excluded)', text, re.I)}
    normalized = {'included' if mode in {'포함', 'inclusive', 'included'} else 'excluded' for mode in modes}
    return next(iter(normalized)) if len(normalized) == 1 else ''


def _fact(text, start, end, key, raw):
    prefix, suffix = text[:start], text[end:]
    category = _category(prefix)
    kind = ''
    if category == 'self':
        found = list(re.finditer(r'현금|현물|cash|in[- ]kind', prefix[-45:], re.I))
        if found:
            kind = 'cash' if found[-1].group().casefold() in {'현금', 'cash'} else 'in_kind'
    role = _number_role(prefix, suffix)
    if category == 'export' and re.search(r'수출\s*실적', prefix[-35:]):
        role = 'actual'
    return dict(key=key, raw=raw, start=start, end=end, context=category, burden_kind=kind,
                scope={s.replace(' ', '') for s in re.findall(r'20\d{2}\s*년|\d+\s*분기', prefix)},
                ids=_scope(text, start), role=role, entity=_number_entity(prefix), vat=_vat_mode(text))


def _money(text):
    found = []
    for m in MONEY.finditer(text):
        currency = m['c1'] or m['c2']
        currency = CUR_ALIASES.get(currency, CUR_ALIASES.get(currency.upper(), currency.upper()))
        value = Decimal((m['v1'] or m['v2']).replace(',', '')) * SCALES[(m['s1'] or m['s2'] or '').casefold()]
        found.append(_fact(text, m.start(), m.end(), (value, currency), m.group()))
    for token in number_tokens(text):
        if token['key'][1] == '원' and not any(m['start'] <= token['start'] < m['end'] for m in found):
            found.append(_fact(text, token['start'], token['end'], (token['key'][0], 'KRW'), token['raw']))
    return found


def _compatible(token, candidate):
    return (token['ids'].issubset(candidate['ids'])
            and token['scope'].issubset(candidate['scope'])
            and (not token['context'] or not candidate['context'] or token['context'] == candidate['context'])
            and (not token['burden_kind'] or not candidate['burden_kind'] or token['burden_kind'] == candidate['burden_kind'])
            and (not token['role'] or not candidate['role'] or token['role'] == candidate['role'])
            and (not token['entity'] or not candidate['entity'] or token['entity'] == candidate['entity'])
            and (not token['vat'] or not candidate['vat'] or token['vat'] == candidate['vat']))


def _positive_statuses(text):
    result = []
    for kind, pattern in CLAIMS.items():
        for m in pattern.finditer(text):
            prefix, suffix = text[:m.start()][-35:], text[m.end():m.end() + 25]
            if re.search(r'(?:not(?:\s+yet)?(?:\s+been)?|never)\s*$', prefix, re.I):
                continue
            if re.match(r'\s*(?:되지\s*(?:않|못)|하지\s*(?:않|못)|아님|여부|인지|\?|예정|계획|목표)', suffix):
                continue
            result.append((kind, m.start(), m.end()))
    return result


def _country(value):
    normalized = _normal(re.sub(r'(?:임|함)$', '', value))
    return COUNTRY_ALIASES.get(normalized, normalized)


def _term_place(text, match):
    tail = text[match.end():]
    m = re.match(r'\s+([A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)?|[가-힣]{2,15})(?=$|\s|[,;])', tail)
    if not m or re.match(r'조건|예정|확정|진행|적용|임|함|금액|통화|Incoterms|VAT', m[1], re.I):
        return ''
    return _normal(m[1])


def inspect_business_draft(draft, sources, *, profile=None):
    """평면 항목을 대조만 수행함. 계산·작성·자동 수정이나 외부 요청은 없음."""
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
        source_map[source['source_id']] = source
    workflow = (profile or {}).get('business_workflow')
    if workflow is not None and (not isinstance(workflow, str) or workflow not in BUSINESS_WORKFLOWS):
        raise ValueError('알 수 없는 업무 유형임')
    issues = []

    def issue(code, field, line_no, message, severity='error'):
        issues.append(dict(code=code, field=field, line=line_no, message=message, severity=severity))

    for field, value in draft.items():
        for line_no, raw_line in enumerate(value.splitlines(), 1):
            ids = CITATION_PATTERN.findall(raw_line)
            if set(ids) - source_map.keys():
                issue('business_unknown_source', field, line_no, '알 수 없는 출처 ID가 있음')
            evidence = [part for source_id in dict.fromkeys(ids) if source_id in source_map for part in _clauses(source_map[source_id]['text'])]
            clean = CITATION_PATTERN.sub('', raw_line).strip()
            if not clean:
                continue
            # Fixed fields carry semantic labels even when the value itself is only a number or code.
            clean = clean if field in {'제목', '요약', '본문'} else field + ': ' + clean
            for kind, identifier in _identifiers(clean):
                if not any((kind, identifier) in _identifiers(part) for part in evidence):
                    issue('business_identifier_mismatch', field, line_no, f'과제/거래/공고 번호가 인용 원자료와 일치하지 않음: {identifier}')
            for m in HS.finditer(clean):
                code = re.sub(r'[. -]', '', m['code'])
                if not any(code == re.sub(r'[. -]', '', candidate['code']) and _scope(clean, m.start()).issubset(_scope(part, candidate.start()))
                           for part in evidence for candidate in HS.finditer(part)):
                    issue('business_hs_mismatch', field, line_no, f'HS 코드가 인용 원자료와 일치하지 않음: {m["code"]}; 품목분류는 자동 확정하지 않음')
            for m in COUNTRY.finditer(clean):
                if not any(_country(m['country']) == _country(candidate['country'])
                           and _scope(clean, m.start()).issubset(_scope(part, candidate.start()))
                           for part in evidence for candidate in COUNTRY.finditer(part)):
                    issue('business_country_mismatch', field, line_no, f'거래 국가가 인용 원자료와 일치하지 않음: {m["country"]}')
            for m in INCOTERM.finditer(clean):
                place = _term_place(clean, m)
                if not any(m.group().upper() == candidate.group().upper()
                           and (not place or place == _term_place(part, candidate))
                           and _scope(clean, m.start()).issubset(_scope(part, candidate.start()))
                           for part in evidence for candidate in INCOTERM.finditer(part)):
                    issue('business_incoterm_mismatch', field, line_no, 'Incoterms 조건·지정장소가 인용 원자료와 일치하지 않음: ' + m.group())
            for expected, original, position, _ in _dates(clean):
                if not any(expected and expected == candidate[0]
                           and _scope(clean, position).issubset(_scope(part, candidate[2]))
                           and (not _category(clean[:position]) or not _category(part[:candidate[2]]) or _category(clean[:position]) == _category(part[:candidate[2]]))
                           for part in evidence for candidate in _dates(part)):
                    issue('business_date_mismatch', field, line_no, f'납기/신청기간/날짜가 인용 원자료와 일치하지 않음: {original}')
            if AMBIGUOUS_DATE.search(clean):
                issue('business_ambiguous_date', field, line_no, '모호한 날짜의 순서·연도는 원자료 확인이 필요함', 'warning')
            for clause in _clauses(clean):
                money = _money(clause)
                candidates = [item for part in evidence for item in _money(part)]
                for token in money:
                    matches = [candidate for candidate in candidates if candidate['key'] == token['key'] and _compatible(token, candidate)]
                    if not matches:
                        issue('business_money_mismatch', field, line_no, f'통화·금액·지원/자부담·VAT·계획/실적 문맥이 원자료와 일치하지 않음: {token["raw"]}')
                    if token['key'][1] == '$':
                        issue('business_ambiguous_currency', field, line_no, '$ 표기만으로 국가 통화를 추정하지 않음; 통화 확인 필요함', 'warning')
                    if matches and not token['vat'] and any(item['vat'] for item in matches):
                        issue('business_vat_unspecified', field, line_no, '원자료의 VAT 포함/제외 범위를 금액과 함께 확인해야 함', 'warning')
                skip = [(t['start'], t['end']) for t in money]
                skip += [(m.start(), m.end()) for pattern in [IDENTIFIER, HS, AMBIGUOUS_DATE] for m in pattern.finditer(clause)]
                skip += [(item[2], item[3]) for item in _dates(clause)]
                for token in number_tokens(clause):
                    if any(start <= token['start'] < end for start, end in skip):
                        continue
                    token = dict(token, context=_category(clause[:token['start']]))
                    candidates_n = [dict(item, context=_category(part[:item['start']]))
                                    for part in evidence if _scope(clause, token['start']).issubset(_scope(part, len(part))) for item in number_tokens(part)]
                    if not any(item['key'] == token['key'] for item in _matching_candidates(token, candidates_n)):
                        issue('business_number_mismatch', field, line_no, f'수치·단위·항목·계획/실적이 인용 원자료와 일치하지 않음: {token["raw"]}')
                for status, start, _ in _positive_statuses(clause):
                    if not any(status == candidate[0] and _scope(clause, start).issubset(_scope(part, candidate[1]))
                               and (not _number_entity(clause[:start]) or not _number_entity(part[:candidate[1]]) or _number_entity(clause[:start]) == _number_entity(part[:candidate[1]]))
                               for part in evidence for candidate in _positive_statuses(part)):
                        issue('business_status_unverified', field, line_no, '승인·선정·지원금 수령·수출/성과 완료 상태를 인용 원자료에서 확인할 수 없음')
            if re.search(r'서명|signature|계좌|account|수취인|beneficiary|거래결정', field, re.I) and not re.search(r'추가 확인|직접 입력|미확정|빈칸', value):
                value_clean = re.sub(r'(?:함|임)$', '', CITATION_PATTERN.sub('', raw_line).strip())
                supported = any(_normal(value_clean) in _normal(part) for part in evidence)
                user_confirmed = any(source_map[i].get('filename') == '사용자 입력' or source_map[i].get('source_type') == 'user_input' for i in ids if i in source_map)
                if not supported:
                    issue('business_direct_input_unsupported', field, line_no, '서명·계좌·수취인·거래 결정은 임의 생성할 수 없음; 실제 입력 또는 원자료 확인 필요함')
                elif not user_confirmed:
                    issue('business_direct_input_confirmation', field, line_no, '원자료에서 확인한 민감 거래 항목임; 사용자 직접 확인 필요함', 'warning')
    blocking = any(item['severity'] == 'error' for item in issues)
    return dict(domain='business_support', workflow=workflow, blocking=blocking, issues=issues,
                checks=[{'name': '출처·거래조건·사업식별자·금액·기간·계획/실적 대조', 'status': 'failed' if blocking else 'passed'},
                        {'name': '환율·합계·의무·서명·거래 결정 자동 생성', 'status': 'disabled'},
                        {'name': '법적 적합성/지원 자격 인증', 'status': 'not_certified'}],
                additional_checks=list(BUSINESS_WORKFLOWS[workflow]['checks']) if workflow else [])
