"""원자료와 RA 초안을 대조함. 법적 적합성/의학적 판단을 인증하지 않음."""

import re
from agent.ra_scope import qualify_natural_scope
from datetime import date
from decimal import Decimal

from agent.review import CITATION_PATTERN, _context, _field_number_tokens, _matching_candidates, number_tokens
from agent.field_citations import profile_field
from templates.value_rules import inspect_form_values, validate_rule_profile

PORTAL = "https://nedrug.mfds.go.kr/"
CTD = "https://admin.ich.org/page/ctd"
IND = "https://www.mfds.go.kr/brd/m_1060/view.do?seq=15953"
DMF = "https://www.mfds.go.kr/law/board/boardDetail.do?brdId=data0011&menuKey=38&seq=15802"
RMP = "https://www.mfds.go.kr/brd/m_211/view.do?seq=14879"
MFDS_RULES = "https://www.mfds.go.kr/brd/m_207/list.do"

# 모두 담당자의 추가 확인사항임. 자동으로 법정 필수 요건으로 판정하지 않음.
RA_WORKFLOWS = {
    "product_approval": {"title": "품목허가·CTD", "checks": [
        "추가 확인사항: 신청 구분, 품목·제형·함량·용법·효능의 원자료와 신청서 버전을 확인함",
        "추가 확인사항: CTD Module 1의 지역별 항목과 Module 2~5의 품질·비임상·임상 자료 위치를 확인함",
        "추가 확인사항: 적용 규정·제출자료·수수료·접수 항목을 현행 공식 안내와 대조함"],
        "source_urls": [PORTAL, CTD, MFDS_RULES]},
    "variation": {"title": "변경허가·변경신고", "checks": [
        "추가 확인사항: 변경 전후 대비표, 변경 사유, 변경 대상과 근거 자료의 버전을 확인함",
        "추가 확인사항: 변경 구분과 제출 경로·첨부자료를 현행 공식 안내에서 확인함"],
        "source_urls": [PORTAL, MFDS_RULES, DMF]},
    "clinical_trial": {"title": "임상시험계획 승인·변경(IND)", "checks": [
        "추가 확인사항: 시험번호·계획서 버전·대상·용량·투여경로·평가변수와 근거 자료를 대조함",
        "추가 확인사항: 임상시험 계획(변경) 승인 보완사례집과 현재 제출자료 목록을 확인함",
        "추가 확인사항: 시험 계획, 승인, 수행, 완료 상태를 서로 구분함"],
        "source_urls": [IND, PORTAL, CTD]},
    "dmf": {"title": "원료의약품 등록·변경(DMF)", "checks": [
        "추가 확인사항: 원료명·제조원·제조소·제조공정·규격·배치 자료와 등록 버전을 확인함",
        "추가 확인사항: 변경등록 및 연계심사 가이드라인의 적용 범위와 최신 개정을 확인함"],
        "source_urls": [DMF, PORTAL]},
    "gmp": {"title": "GMP 자료·실태조사 대응", "checks": [
        "추가 확인사항: 제조소·공정·배치 기록·검증·시험 결과의 동일성과 원자료 버전을 확인함",
        "추가 확인사항: 실태조사·보완·적합 판정의 실제 상태와 현행 GMP 안내를 확인함"],
        "source_urls": [MFDS_RULES, PORTAL]},
    "manufacturing_import": {"title": "제조·수입 업무 신청", "checks": [
        "추가 확인사항: 신청 주체·제조 또는 수입 구분·품목·제조소와 사용자 직접 입력사항을 확인함",
        "추가 확인사항: 민원 종류, 현재 신청서, 증명·첨부자료와 제출 경로를 공식 포털에서 확인함"],
        "source_urls": [PORTAL, MFDS_RULES]},
    "safety_management": {"title": "위해성 관리계획(RMP)·안전성 자료", "checks": [
        "추가 확인사항: 안전성 자료의 대상·기간·분모·이상사례 수·단위와 출처를 확인함",
        "추가 확인사항: 확인된 위해성, 잠재적 위해성, 부족한 정보를 자료에 따라 구분함",
        "추가 확인사항: RMP 적용 여부와 운영 규정의 현행 버전·제출 일정을 확인함"],
        "source_urls": [RMP, PORTAL]},
    "testing_support": {"title": "시험 의뢰·품질자료 지원", "checks": [
        "추가 확인사항: 시료 식별자·용기 수·시험방법·보관 조건을 원자료 및 의뢰 양식과 대조함",
        "추가 확인사항: 실제 의뢰인·제출 의사·서명은 사용자가 직접 확인함"],
        "source_urls": ["https://www.pacelabs.com/life-sciences/submit-a-sample/",
                        "https://www.cambrex.com/forms-and-certificates/"]},
}

QUANTITY = re.compile(
    r"(?<![A-Za-z\d])(?P<value>-?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>(?:mg|[µμ]g|mcg|ng|kg|g|mL|ml|IU|%)(?:\s*/\s*(?:\d+(?:\.\d+)?\s*)?(?:mL|ml|kg|vial)(?:\s*/\s*day)?)?)(?![A-Za-z/])"
)
IDENTIFIER = re.compile(
    r"(?P<label>시험(?:계획서)?\s*번호|프로토콜\s*번호|배치\s*번호|제조\s*번호|"
    r"(?:Protocol|Study|Trial|Batch|Lot)\s*(?:No\.?|ID))\s*[:：#=]?\s*"
    r"(?P<value>[A-Za-z0-9][A-Za-z0-9_./-]{0,79})", re.I
)
FULL_DATE = re.compile(r"(?<!\d)(?P<y>20\d{2})(?:\s*[-./]\s*|\s*년\s*)"
                       r"(?P<m>\d{1,2})(?:\s*[-./]\s*|\s*월\s*)(?P<d>\d{1,2})(?:\s*일)?(?!\d)")
AMBIGUOUS_DATE = re.compile(r"(?<!\d)\d{1,2}[/.-]\d{1,2}[/.-](?:\d{2}|20\d{2})(?!\d)")
FREQUENCY = re.compile(r"(?:1\s*일|하루)\s*\d+\s*회|\b(?:once|twice|three times)\s+daily\b", re.I)
INTERVAL = re.compile(r'\bevery\s+(?:\d+|one|two|three|four|six|eight|twelve)\s+(?:days?|weeks?)\b|'
                      r'\d+\s*(?:일|주)\s*(?:간격|마다)|\b(?:once|twice)\s+weekly\b|'
                      r'\b(?:\d+|one|two|three|four|six|eight|twelve)[-\s]weekly\s+intervals?\b', re.I)
PRODUCT_LABEL = re.compile(r'(?:제품명?|품목명?|약품명?|의약품명|Product(?:\s+name)?|Drug(?:\s+name)?)'
                           r'\s*[:：=]\s*([가-힣A-Za-z][가-힣A-Za-z0-9_-]+)', re.I)
PRODUCT_LEAD = re.compile(r'(?:^|[\n;；])\s*(?:[□○-]\s*)?([A-Z][A-Za-z0-9_-]{2,})'
                          r'(?=\s+(?:\d+(?:\.\d+)?\s*(?:mg|mcg|mL)|용량|권장용량|dose\b))')
POPULATION = re.compile(r'(?P<adult>성인|\badults?\b)|(?P<pediatric>소아|어린이|\b(?:pediatric|paediatric|children|child)\b)|'
                        r'(?P<adolescent>청소년|\badolescents?\b)|(?P<infant>영아|\binfants?\b)', re.I)
INDICATION_FIELD = re.compile(r'치료\s*대상|적응증|효능\s*[·/]?\s*효과|therapeutic\s+indication|patient\s+population', re.I)
APPEARANCE_FIELD = re.compile(r'성상|외관|appearance|pharmaceutical\s+form', re.I)
DOSAGE_FIELD = re.compile(r'용법|용량|posology|dosage|\bdose\b', re.I)
FULL_REGIMEN_FIELD = re.compile(r'용법|posology|dosage', re.I)
RESTRICTION = re.compile(r'\b(?:when|unless|if|provided\s+that|only\s+(?:when|if)|'
                         r'who\s+(?:have|has)|with\s+newly\s+diagnosed|after|'
                         r'based\s+on\s+(?:physician|clinical|medical))\b|'
                         r'(?:경우에?\s*(?:만|한)|반응이\s*불충분|금기|의사\s*판단)', re.I)
CLINICAL_ASSERTION = re.compile(r'\b(?:dose|dosage|recommended|initial|starting|increased|administered)\b|'
                               r'권장|초기|초회|증량|투여', re.I)
INDICATION_ASSERTION = re.compile(r'\bis\s+indicated\b|적응증|치료\s*대상|효능\s*[·/]?\s*효과', re.I)
DOSAGE_ASSERTION = re.compile(r'\b(?:dose|dosage|posology)\b|권장\s*용량|투여\s*용량|초회\s*용량', re.I)
VARIANT_FIELD = re.compile(r'함량|제형|바이알|시린지|프리필드|strength|per\s*vial|composition', re.I)
DRUG_VALUE_FIELD = re.compile(r'용량|함량|농도|\bdose\b|\bdosage\b|\bconcentration\b|\bstrength\b', re.I)
UNIT_TEXT = re.compile(r'(?<![A-Za-z\d])-?\d[\d,]*(?:\.\d+)?\s*(?P<unit>[A-Za-zµμ]+(?:\s*/\s*[\dA-Za-zµμ]+)*)')
ROUTE = re.compile(r"경구|정맥|피하|근육|\b(?:oral|intravenous|subcutaneous|intramuscular)\b", re.I)
CLAIM = re.compile(
    r"안전(?:성)?(?:이|은|을)?\s*(?:확인|입증|확보|보장)(?:됨|함|되었음|하였음)?|안전함|"
    r"(?:부작용|이상사례)(?:이|가|은)?\s*없(?:음|다)|"
    r"(?:유효성|효능)(?:이|은|을)?\s*(?:확인|입증|확보)(?:됨|함|되었음)?|"
    r"(?:허가|승인)(?:가|를|는)?\s*(?:완료|취득|받음|받았음|되었음|됨)|"
    r"임상(?:시험)?(?:이|은|을)?\s*(?:완료|종료)(?:됨|함|되었음)?|"
    r"GMP\s*(?:적합|인증)(?:을|이|은)?\s*(?:확인|취득|완료|받음|됨)|"
    r"\b(?:safety|efficacy)\b[^.;!?\n]{0,180}\b(?:established|confirmed|demonstrated)\b|"
    r"\b(?:safety|efficacy)\s+(?:was\s+|is\s+)?(?:established|confirmed|demonstrated)|"
    r"\b(?:approved|effective|safe)\b|\b(?:clinical\s+)?trial\s+(?:was\s+|is\s+)?completed\b", re.I
)
COMPANY_ROLES = {
    'marketing_authorisation_holder': re.compile(r'판매\s*허가권자|품목\s*허가권자|\bMAH\b|marketing\s+authori[sz]ation\s+holder', re.I),
    'manufacturer': re.compile(r'제조원|제조자|제조업자|제조판매업자|\bmanufacturer\b|manufactured\s+by', re.I),
    'importer': re.compile(r'수입원|수입자|수입업자|\bimporter\b|imported\s+by', re.I),
    'distributor': re.compile(r'판매원|판매자|판매업자|\bdistributor\b|distributed\s+by', re.I),
    'document_publisher': re.compile(r'문서\s*발행사|자료\s*발행사|\bdocument\s+publisher\b', re.I),
}


def _pending_company_role(text, match):
    prefix, suffix = text[:match.start()], text[match.end():]
    return bool(re.search(r"\b(?:not|unknown|whether|(?:is|was|are|were)n['’]t)\s+(?:(?:the|a|an)\s+)?$", prefix, re.I)
                or re.match(r'\s*[:：=]?\s*(?:(?:이|가|은|는|인지)\s*)?'
                            r'(?:아님|아니|않|미확인|확인\s*필요|여부|\?|\b(?:not|unknown)\b)', suffix, re.I))


def _company_role_unconfirmed(field, line, sources):
    """Check single-role clauses; a separate pending claim cannot hide them."""
    # Do not split periods in corporate names (e.g. "Pharma Ltd.").
    for clause in re.split(r'[;；\n]', line):
        roles = [role for role, pattern in COMPANY_ROLES.items() if pattern.search(field + ' ' + clause)]
        if len(roles) != 1:
            continue
        pattern = COMPANY_ROLES[roles[0]]
        matches = list(pattern.finditer(clause))
        if matches and all(_pending_company_role(clause, match) for match in matches):
            continue
        if not matches and re.search(r'아님|아니|않|미확인|확인\s*필요|여부|\?|\b(?:not|unknown|whether)\b', clause, re.I):
            continue
        known = [source for source in sources if source.get('company_role') in COMPANY_ROLES
                 and isinstance(source.get('company_name'), str) and source['company_name'].strip()
                 and _normal(source['company_name']) in _normal(clause)]
        supported = False
        for source in known:
            if source['company_role'] == roles[0]:
                supported = True
                break
            # Company-name-only copying does not prove a field's requested role.
            if matches and _normal(clause) in _normal(source['text']):
                supported = True
                break
            name = _normal(source['company_name'])
            for source_clause in re.split(r'[;；\n]', source['text']):
                for match in pattern.finditer(source_clause):
                    if _pending_company_role(source_clause, match):
                        continue
                    if (_normal(source_clause[match.end():]) == name
                            or _normal(source_clause[:match.start()]) == name):
                        supported = True
                        break
                if supported:
                    break
        # A mismatch means this citation does not establish the role, never
        # that the company cannot also have that role in another relationship.
        if known and not supported:
            return True
    return False


def _affirmative_claims(text):
    """A pending statement elsewhere must not hide an actual positive assertion."""
    found = []
    for match in CLAIM.finditer(text):
        prefix, suffix = text[:match.start()][-30:], text[match.end():match.end() + 24]
        if re.search(r'\bnot\s+(?:(?:yet|been|fully)\s+)*(?:established|confirmed|demonstrated)\b', match.group(), re.I):
            continue
        if re.search(r'(?:not(?:\s+yet)?(?:\s+been)?|never)\s*$', prefix, re.I):
            continue
        if re.match(r'\s*(?:되지\s*(?:않|못)|하지\s*않|아님|여부|인지|\?|(?:는|은)?\s*(?:미확인|확인\s*필요))', suffix):
            continue
        # English "effective date" is a label, not an efficacy assertion.
        if match.group().casefold() == 'effective' and re.match(r'\s+date\b', suffix, re.I):
            continue
        found.append(match)
    return found


def _ids(text):
    return {(('batch' if re.search(r"배치|제조|batch|lot", match['label'], re.I) else 'study'), match['value'])
            for match in IDENTIFIER.finditer(text)}


def _scope(text, position):
    before = text[:position]
    found = {}
    for match in IDENTIFIER.finditer(before):
        kind = 'batch' if re.search(r"배치|제조|batch|lot", match['label'], re.I) else 'study'
        found[kind] = match['value']
    # An identifier after a value applies only when the clause has a single ID of its kind.
    for kind, value in _ids(text):
        if kind not in found and sum(k == kind for k, _ in _ids(text)) == 1:
            found[kind] = value
    return set(found.items())


def _quantities(text):
    result = []
    for match in QUANTITY.finditer(text):
        unit = re.sub(r"\s+", "", match['unit']).replace('μg', 'mcg').replace('µg', 'mcg').replace('ml', 'mL')
        result.append({'key': (Decimal(match['value'].replace(',', '')), unit),
                       'scope': _scope(text, match.start()), 'context': _context(text, match.start()),
                       'raw': match.group(), 'start': match.start(), 'end': match.end()})
    return result


def _dates(text):
    result = []
    for match in FULL_DATE.finditer(text):
        try:
            value = date(int(match['y']), int(match['m']), int(match['d'])).isoformat()
        except ValueError:
            value = None
        result.append((value, match.group(), match.start(), match.end()))
    return result


def _clauses(text):
    # A single PDF line wrap is whitespace, not a new clinical statement.
    # Paragraph gaps, sentence punctuation, and semicolons retain boundaries.
    return [part.strip() for part in re.split(r"\n\s*\n|[;；]|(?<!\d)[.!?]\s+", text) if part.strip()]


def _normal(text):
    return re.sub(r"[\s□○\-.,:：]", "", CITATION_PATTERN.sub('', text)).casefold()


def _product_names(texts, sources, profile):
    names = set()
    for value in [*(source.get('product_name', '') for source in sources),
                  profile.get('ra_product_name', profile.get('product_name', ''))]:
        if isinstance(value, str) and value.strip():
            names.add(value.strip().split()[0].casefold())
    for text in texts:
        names.update(match[1].casefold() for match in PRODUCT_LABEL.finditer(text))
        names.update(match[1].casefold() for match in PRODUCT_LEAD.finditer(text)
                     if match[1].casefold() not in {'the', 'this', 'these', 'those', 'a', 'an', 'dose', 'injection', 'solution', 'concentration',
                                                   'adults', 'adult', 'children', 'pediatric'}
                     # A sentence about an active ingredient is not a product heading.
                     and not re.match(r'\s+\d+(?:\.\d+)?\s*(?:mg|mcg|mL)\s+'
                                      r'(?:is|are|was|were|has|contains)\b', text[match.end():], re.I))
    return names


def _variant(value):
    if not isinstance(value, str):
        return {}
    quantities = _quantities(value)
    strength = quantities[0]['key'] if quantities else None
    container = re.search(r'\b(syringe|pen|vial|tablet)\b|시린지|바이알|정제', value, re.I)
    return {'strength': strength, 'container': container.group().casefold() if container else None}


def _korean_strength_heading(line, names):
    """Only an exact known Korean product + one printed strength is a heading.

    A dose/composition sentence, unknown name, or other surrounding words must
    not turn a dose into the selected product's strength.
    """
    for name in names:
        if not re.search(r'[가-힣]', name):
            continue
        match = re.match(r'^\s*' + re.escape(name) + r'\s+', line, re.I)
        if match:
            remaining = line[match.end():].strip()
            quantities = _quantities(remaining)
            if len(quantities) == 1 and quantities[0]['start'] == 0 and quantities[0]['end'] == len(remaining):
                return True
    return False


def _declared_product_variant(text, position):
    """A labelled variant applies only inside this explicit product block.

    The product-name quote may precede its next-line strength. Blank paragraphs,
    another product declaration and conflicting/negative values end that link.
    Ordinary dose sentences never become product-strength declarations.
    """
    paragraph_start = max([0, *[match.end() for match in re.finditer(r'\n\s*\n', text)
                                if match.end() <= position]])
    paragraph_end = next((match.start() for match in re.finditer(r'\n\s*\n', text)
                          if match.start() >= position), len(text))
    declarations = [match for match in PRODUCT_LABEL.finditer(text, paragraph_start, paragraph_end)]
    preceding = [match for match in declarations if match.start() <= position]
    if not preceding:
        return None
    current = preceding[-1]
    end = next((match.start() for match in declarations if match.start() > current.start()), paragraph_end)
    strengths, containers, labelled, uncertain = set(), set(), False, False
    for line in text[current.start():end].splitlines():
        match = re.match(r'^\s*(?:제형\s*[·/]\s*함량|제형|함량|'
                         r'dosage\s+form(?:\s*/\s*strength)?|strength)\s*[:：=]\s*(.+?)\s*$', line, re.I)
        if not match:
            continue
        labelled = True
        value = match[1]
        uncertain |= bool(re.search(r'아님|아니|미확인|확인\s*필요|또는|\b(?:not|unknown|or)\b', value, re.I))
        strengths.update(item['key'] for item in _quantities(value))
        containers.update(item.group().casefold() for item in re.finditer(
            r'\b(?:syringe|pen|vial|tablet)\b|시린지|바이알|정제', value, re.I))
    if not labelled:
        return None
    return {'strength': next(iter(strengths)) if len(strengths) == 1 else None,
            'container': next(iter(containers)) if len(containers) == 1 else None,
            'ambiguous': uncertain or len(strengths) > 1 or len(containers) > 1}


def _material_scope(text, position, names, metadata=None):
    """Bind values to explicit product/population/strength headings, without inference."""
    metadata = metadata or {}
    found = {}
    product_matches = [(match.start(), name) for name in names
                       for match in re.finditer(r'(?<![A-Za-z0-9])' + re.escape(name) + r'(?![A-Za-z0-9])', text, re.I)]
    preceding = [item for item in product_matches if item[0] <= position]
    if preceding:
        found['product'] = max(preceding)[1]
    elif len({name for _, name in product_matches}) == 1:
        found['product'] = product_matches[0][1]
    elif isinstance(metadata.get('product_name'), str) and metadata['product_name'].strip():
        found['product'] = metadata['product_name'].split()[0].casefold()
    populations = list(POPULATION.finditer(text))
    prior = [match for match in populations if match.start() <= position]
    if prior:
        found['population'] = prior[-1].lastgroup
    elif len({match.lastgroup for match in populations}) == 1 and populations:
        found['population'] = populations[0].lastgroup
    # Printed product-strength headings may override a broad page's metadata.
    heading = None
    line_end = text.find('\n', position)
    for line in text[:line_end if line_end >= 0 else len(text)].splitlines():
        if (PRODUCT_LEAD.match(line) or _korean_strength_heading(line, names)
                or re.match(r'^\s*\d+\s*mg\s+(?:solution|powder)', line, re.I)):
            heading = _variant(line)
    declared_variant = _declared_product_variant(text, position)
    variant = declared_variant if declared_variant is not None else heading or _variant(metadata.get('product_variant', ''))
    # An explicit "each/per syringe/tablet" composition statement takes priority
    # over the selected product metadata. The metadata must not hide a changed
    # dosage container while the number and unit remain unchanged.
    sentence = _sentence_at(text, position)
    local_position = position - text.rfind(sentence, 0, position + len(sentence))
    containers = list(re.finditer(
        r'\b(?:each|per|one|1)\s+(?:pre[- ]filled\s+|film[- ]coated\s+)?(vial|syringe|pen|tablet)\b',
        sentence, re.I))
    prior_containers = [match for match in containers if match.start() <= local_position]
    composition_container = prior_containers[-1] if prior_containers else containers[0] if containers else None
    if composition_container:
        variant = {**variant, 'container': composition_container[1].casefold()}
    if variant:
        found['variant'] = variant
    bases = list(re.finditer(r'\b(?:each|per|one|1)\s+(?:pre[- ]filled\s+|film[- ]coated\s+)?'
                             r'(mL|millilitres?|milliliters?|dose|vial|syringe|pen|tablet)\b', sentence, re.I))
    prior_bases = [match for match in bases if match.start() <= local_position]
    basis = prior_bases[-1] if prior_bases else bases[0] if len(bases) == 1 else None
    if basis:
        unit = basis[1].casefold()
        found['quantity_basis'] = 'per_mL' if unit == 'ml' or unit.startswith('millilit') else 'per_' + unit
    states = list(re.finditer(r'(?P<unopened>unopened|미개봉)|(?P<reconstituted>reconstituted|reconstitution|재구성)|'
                              r'(?P<diluted>diluted|dilution|희석)', text[:position], re.I))
    if states:
        found['state'] = states[-1].lastgroup
    phase_prefix = re.split(r'[;；]|(?<!\d)\.(?!\d)', text[:position])[-1]
    phases = list(re.finditer(r'(?P<initial>initial|loading|starting|초회|초기)|(?P<maintenance>maintenance|유지)', phase_prefix, re.I))
    if phases:
        found['phase'] = phases[-1].lastgroup
    bounds = list(re.finditer(r'(?P<maximum>\bmax(?:imum)?\b|최대)|(?P<minimum>\bmin(?:imum)?\b|최소)', phase_prefix, re.I))
    if bounds:
        found['dose_bound'] = bounds[-1].lastgroup
    if re.search(r'\blower\b|감량', phase_prefix, re.I):
        found['dose_modifier'] = 'lower'
    return found


def _scope_matches(expected, actual, *, variant=False):
    if any(value != actual.get(key) for key, value in expected.items()
           if key in {'product', 'population', 'state', 'phase', 'dose_modifier', 'quantity_basis', 'dose_bound'}):
        return False
    if variant and expected.get('variant'):
        if expected['variant'].get('ambiguous') or actual.get('variant', {}).get('ambiguous'):
            return False
        for key, value in expected['variant'].items():
            if key in {'strength', 'container'} and value is not None and value != actual.get('variant', {}).get(key):
                return False
    return True


def _quantity_role(text, token):
    if re.search(r'polysorbate|excipient|첨가제', text, re.I):
        return 'excipient'
    if re.search(r'(?:vial|syringe|pen|tablet)\s+contains|per\s+(?:vial|syringe|pen|tablet)|'
                 r'바이알\s*(?:당|함량)|함량|strength|composition', text, re.I):
        return 'strength'
    if '/mL' in token['key'][1] or re.search(r'농도|concentration', text, re.I):
        return 'concentration'
    if re.search(r'권장\s*용량|투여\s*용량|recommended\s+(?:dose|dosage)|\bdose\b', text, re.I):
        return 'dose'
    return ''


def _evidence_parts(source):
    offset = 0
    for part in _clauses(source['text']):
        start = source['text'].find(part, offset)
        offset = start + len(part)
        yield {'text': part, 'offset': start, 'source': source}


def _sentence_at(text, position):
    boundaries = [0, *[match.end() for match in re.finditer(r'(?<!\d)[.!?](?=\s|$)|[;；]|\n\s*\n', text)], len(text)]
    start = max(boundary for boundary in boundaries if boundary <= position)
    end = min(boundary for boundary in boundaries if boundary > position) if position < len(text) else len(text)
    return text[start:end]


def _interval_key(text):
    words = {'one': '1', 'two': '2', 'three': '3', 'four': '4', 'six': '6', 'eight': '8', 'twelve': '12'}
    clean = re.sub(r'\s+', ' ', text.casefold())
    for word, value in words.items():
        clean = re.sub(r'\b' + word + r'\b', value, clean)
    if 'once weekly' in clean or 'twice weekly' in clean:
        return ('frequency', 1 if 'once' in clean else 2, 'week')
    value = int(re.search(r'\d+', clean)[0])
    return ('interval', value, 'week' if re.search(r'week|주', clean) else 'day')


def _regimen_quantity(text, position):
    """Bind a frequency to its local quantity, not every number in the sentence."""
    quantities = _quantities(text)
    preceding = [quantity for quantity in quantities if quantity['end'] <= position]
    quantity = preceding[-1] if preceding else quantities[0] if quantities else None
    return quantity['key'] if quantity else None


def _quote_matches(assertion, text):
    # Sentence splitting removes the terminal dot from an evidence part.
    words = assertion.rstrip('.。').split()
    pattern = r'\s+'.join(re.escape(word) for word in words)
    if words and re.search(r'[\w]$', words[-1]):
        pattern += r'(?!\w)'
    return re.finditer(pattern, text, re.I) if pattern else iter(())


def _appearance_negation_omitted(text, start, end):
    """Reject a literal adjacent negation removed from this exact quote span."""
    return bool(re.search(r'\b(?:not|no)(?:\s+(?:a|an))?\s*$', text[:start], re.I)
                or re.match(r'\s*(?:(?:이|가|은|는)\s*)?아(?:님|니(?:다|라|며|고|었))', text[end:])
                or re.match(r'\s*(?:(?:is|are|was|were)\s+)?(?:not|no)\b', text[end:], re.I))


def _context_source(source):
    """Context is only a verified original span, never extra numeric evidence."""
    if 'context_text' not in source:
        return source, 0
    context = source['context_text']
    start, end = source.get('context_start'), source.get('context_end')
    if (not isinstance(context, str) or type(start) is not int or type(end) is not int
            or not 0 <= start <= end <= len(context) or context[start:end] != source['text']):
        raise ValueError('주변 원문과 인용 조각의 정확한 문자 범위가 일치하지 않음')
    return {**source, 'text': context}, start


def _condition_requirements(source, position, *, dosage=False):
    """Only qualifiers of the cited sentence and its explicit continuation apply.

    No evaluator roles or drug-specific values are used. Ordinary later source
    sentences, another paragraph, and another section are not required wholesale.
    """
    text = source['text']
    sentence = _sentence_at(text, position)
    start = text.rfind(sentence, 0, position + len(sentence))
    for restriction in RESTRICTION.finditer(sentence):
        yield sentence[restriction.start():].strip(), start + restriction.start()
    if not dosage:
        return
    parts = list(_evidence_parts(source))
    index = next((i for i, part in enumerate(parts)
                  if part['offset'] <= position < part['offset'] + len(part['text'])), None)
    if index is None:
        return
    previous_end = parts[index]['offset'] + len(parts[index]['text'])
    dependent = bool(_quantities(sentence)) and bool(re.search(
        r'physician|clinical\s+(?:judg|assess)|based\s+on|의사|판단|평가', sentence, re.I))
    # ponytail: only three directly linked follow-ons; cross-page/remote links need semantic review.
    for part in parts[index + 1:index + 4]:
        gap = text[previous_end:part['offset']]
        if re.search(r'\n\s*\n', gap):
            break
        clean = re.sub(r'\s+', ' ', part['text']).strip()
        if not re.match(r'^(?:However\b|This\s+(?:dose|can|may|should)\b|다만|이\s*용량)', clean, re.I):
            break
        previous_end = part['offset'] + len(part['text'])
        conditional_dose = (bool(_quantities(clean)) and bool(re.search(
            r'physician|clinical\s+(?:judg|assess)|based\s+on|depending|의사|판단|평가|경우', clean, re.I)))
        if conditional_dose:
            # Keep the alternative and the decision condition together.
            body = re.sub(r'^(?:However\s*,?\s*|다만\s*[,，]?\s*)', '', part['text'], flags=re.I)
            yield body, part['offset'] + _quantities(part['text'])[0]['start']
            dependent = True
        elif dependent and re.search(r'\bincreas|\bafter\b|증량|이후', clean, re.I):
            quantities = _quantities(part['text'])
            yield part['text'], part['offset'] + (quantities[0]['start'] if quantities else 0)


def _dose_condition_anchors(clean, source, field_context, names, selected):
    """Locate an evidenced dose, never use context to invent numeric support.

    A small rewording must not disable the connected-condition check. The same
    amount/unit, printed dose role, material scope and stated local frequency
    must agree before the source's original position can supply conditions.
    This does not certify arbitrary paraphrases as clinically equivalent.
    """
    positions = set()
    for token in _quantities(clean):
        if _quantity_role(field_context + clean, token) != 'dose':
            continue
        expected = _material_scope(field_context + clean, len(field_context) + token['start'], names, selected)
        sentence = _sentence_at(clean, token['start'])
        for part in _evidence_parts(source):
            for item in _quantities(part['text']):
                position = part['offset'] + item['start']
                if (item['key'] != token['key'] or _quantity_role(part['text'], item) != 'dose'
                        or not token['scope'].issubset(item['scope'])
                        or not _scope_matches(expected, _material_scope(source['text'], position, names, source))):
                    continue
                original = _sentence_at(source['text'], position)
                if any(not any((_interval_key(match.group()) == _interval_key(candidate.group()))
                               if pattern is INTERVAL else _normal(match.group()) == _normal(candidate.group())
                               for candidate in pattern.finditer(original))
                       for pattern in (FREQUENCY, INTERVAL) for match in pattern.finditer(sentence)):
                    continue
                positions.add(position)
    return positions


def _field_polarity(field, text, source_records, names, selected):
    """Catch a literal not/no deletion; this is not general semantic inference."""
    found = []
    # The citation belongs to the preceding sentence even after its full stop.
    statements = re.sub(r'([.!?])([ \t]+)((?:\[S[A-Za-z0-9_-]+\][ \t]*)+)',
                        lambda match: match[2] + match[3].rstrip() + match[1] + match[3][len(match[3].rstrip()):], text)
    for part in _evidence_parts({'text': statements}):
        clean = CITATION_PATTERN.sub('', part['text']).strip()
        assertion = _normal(clean)
        if not assertion:
            continue
        context = field + '\n' + CITATION_PATTERN.sub('', text[:part['offset']]) + clean
        expected = _material_scope(context, len(context), names, selected)
        evidence = [original for identifier in dict.fromkeys(CITATION_PATTERN.findall(part['text']))
                    if identifier in source_records for original in _evidence_parts(source_records[identifier])]
        scoped = [original for original in evidence
                  if _scope_matches(expected, _material_scope(original['source']['text'],
                     original['offset'] + len(original['text']), names, original['source']))]
        if any(assertion == _normal(original['text']) for original in scoped):
            continue  # A supported negative quotation stays unchanged.
        for original in scoped:
            if (re.search(r'\b(?:not|no)\b', original['text'], re.I)
                    and assertion == _normal(re.sub(r'\b(?:not|no)\s+', '', original['text'], flags=re.I))):
                found.append(text[:part['offset']].count('\n') + 1)
                break
    return found


def _field_conditions(field, text, source_records, names, selected):
    """Missing material qualifications require review, without rewriting the draft."""
    indication_field, dosage_field = bool(INDICATION_FIELD.search(field)), bool(DOSAGE_FIELD.search(field))
    if not indication_field and not dosage_field and not (INDICATION_ASSERTION.search(text) or DOSAGE_ASSERTION.search(text)):
        return []
    lines = text.splitlines()
    scoped_lines = []
    for index, raw in enumerate(lines):
        clean = CITATION_PATTERN.sub('', raw)
        context = field + '\n' + '\n'.join(lines[:index]) + '\n' + clean
        scoped_lines.append((clean, _material_scope(context, len(context), names, selected)))
    found = {}
    for line_no, raw in enumerate(lines, 1):
        clean = re.sub(r'^[□○-]\s*', '', CITATION_PATTERN.sub('', raw)).strip()
        indication = indication_field or bool(INDICATION_ASSERTION.search(clean))
        dosage = dosage_field or bool(DOSAGE_ASSERTION.search(clean))
        if (not clean or not (indication or dosage)
                or not indication and dosage and not CLINICAL_ASSERTION.search(clean) and not FULL_REGIMEN_FIELD.search(field)):
            continue  # A scalar fixed cell does not purport to describe the full regimen.
        expected = scoped_lines[line_no - 1][1]
        group = {key: value for key, value in expected.items() if key in {'product', 'population'}}
        scoped_plain = '\n'.join(line for line, scope in scoped_lines if _scope_matches(group, scope))
        for identifier in dict.fromkeys(CITATION_PATTERN.findall(raw)):
            source = source_records.get(identifier)
            if source is None:
                continue
            try:
                contextual, offset = _context_source(source)
            except ValueError:
                continue  # The caller emits ra_context_invalid and blocks export.
            positions = {match.start() for match in _quote_matches(clean, source['text'])}
            if not positions and dosage:
                field_context = field + '\n' + '\n'.join(lines[:line_no - 1]) + '\n'
                positions = _dose_condition_anchors(clean, source, field_context, names, selected)
            for anchor in sorted(positions):
                for requirement, position in _condition_requirements(contextual, offset + anchor, dosage=dosage):
                    actual = _material_scope(contextual['text'], position, names, source)
                    if _scope_matches(expected, actual) and _normal(requirement) not in _normal(scoped_plain):
                        found.setdefault(_normal(requirement), (line_no, requirement))
    return list(found.values())


def _verified_draft_product(draft, source_records, profile, names):
    """A registered product cell's exact cited original name, never trial status."""
    products = set()
    for field in profile.get('fields', []):
        if field.get('evidence_role') != 'product_name' or field.get('input_required'):
            continue
        raw = draft.get(field['value_key'], '')
        clean = CITATION_PATTERN.sub('', raw).strip()
        if not clean or len(clean) > 200 or '\n' in clean:
            continue
        for identifier in CITATION_PATTERN.findall(raw):
            record = source_records.get(identifier)
            if not record or record.get('filename') == '사용자 입력' or record.get('source_origin') == 'user_input' or record.get('requires_verification'):
                continue
            try:
                _context_source(record)
            except ValueError:
                continue
            for match in _quote_matches(clean, record['text']):
                candidate = clean.split()[0].casefold()
                actual = _material_scope(record['text'], match.end(), names | {candidate}, record)
                if actual.get('product') == candidate:
                    products.add(clean)
    return next(iter(products)) if len(products) == 1 else ''


def inspect_ra_draft(draft, sources, *, profile=None):
    """대조만 수행함. 원문 숫자·단위·영어/고정셀 문체를 수정하지 않음.

    checks는 검사 범위, issues는 {code,field,line,message,severity} 목록임.
    profile.ra_workflow는 RA_WORKFLOWS의 key이며, 생략 시 공통 검수만 수행함.
    """
    if not isinstance(draft, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in draft.items()):
        raise ValueError('RA 초안은 항목명과 값이 문자열인 객체여야 함')
    if not isinstance(sources, list) or profile is not None and not isinstance(profile, dict):
        raise ValueError('원자료 목록 또는 RA 프로파일 형식이 잘못됨')
    source_map = {}
    source_records = {}
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get('source_id'), str) or not isinstance(source.get('text'), str):
            raise ValueError('원자료에는 문자열 source_id와 text가 필요함')
        if not re.fullmatch(r'S[A-Za-z0-9_-]+', source['source_id']) or source['source_id'] in source_map:
            raise ValueError('원자료 출처 ID가 유효하지 않거나 중복됨')
        source_map[source['source_id']] = source['text']
        source_records[source['source_id']] = source
    profile = profile or {}
    if any('evidence_role' in field for field in profile.get('fields', [])):
        validate_rule_profile(profile)
    context_texts = []
    for source in sources:
        try:
            contextual, _ = _context_source(source)
            context_texts.append(contextual['text'])
        except ValueError:
            pass  # Referenced invalid context is reported below, without using it.
    names = _product_names([*draft.values(), *source_map.values(), *context_texts], sources, profile)
    selected = {'product_name': profile.get('ra_product_name', profile.get('product_name', '')),
                'product_variant': profile.get('ra_product_variant', profile.get('product_variant', ''))}
    if any(not isinstance(value, str) for value in selected.values()):
        raise ValueError('선택 RA 제품명·제형은 문자열이어야 함')
    natural_product = selected['product_name'] or _verified_draft_product(draft, source_records, profile, names)
    workflow = (profile or {}).get('ra_workflow')
    if workflow is not None and (not isinstance(workflow, str) or workflow not in RA_WORKFLOWS):
        raise ValueError('알 수 없는 RA 업무 유형임')
    evidence_fields = [field for field in profile.get('fields', [])
                       if 'evidence_unit' in field.get('validation', {})]
    issues = inspect_form_values(draft, {'fields': evidence_fields}) if evidence_fields else []

    def issue(code, field, line_no, message, severity='error'):
        issues.append(dict(code=code, field=field, line=line_no, message=message, severity=severity))

    for field in profile.get('fields', []):
        if isinstance(field, dict) and field.get('required', True) and isinstance(field.get('value_key'), str):
            key = field['value_key']
            if not draft.get(key, '').strip():
                issue('ra_required_field_missing', key, 0, '선택 양식에 명시된 필수 항목 값이 누락됨; 법정 필수 요건의 자동 판정은 아님')
    labels = {item['value_key']: item['label'] for item in profile.get('fields', [])
              if isinstance(item, dict) and isinstance(item.get('value_key'), str) and isinstance(item.get('label'), str)}
    for field, text in draft.items():
        metadata = profile_field(profile, field)
        role_field = field + '\n' + labels[field] if field in labels else field
        plain_field = CITATION_PATTERN.sub('', text)
        field_tokens = {token['start']: token for token in number_tokens(plain_field, field_meta=metadata)}
        invalid_context = False
        for identifier in dict.fromkeys(CITATION_PATTERN.findall(text)):
            if identifier in source_records:
                try:
                    _context_source(source_records[identifier])
                except ValueError as exc:
                    issue('ra_context_invalid', field, 0, str(exc))
                    invalid_context = True
        if invalid_context:
            continue  # No scope/numeric check may consume a rejected original span.
        for line_no in _field_polarity(role_field, text, source_records, names, selected):
            issue('ra_statement_polarity_unverified', field, line_no,
                  '인용 원문의 명시적인 부정을 긍정으로 바꾼 문구임; 원문 적용 범위와 담당자 확인이 필요함')
        for line_no, requirement in _field_conditions(role_field, text, source_records, names, selected):
            issue('ra_condition_omitted', field, line_no,
                  '인용한 임상 문장에 연결된 적용 조건·의사 판단·증량 조건이 누락됨; 원문 확인 필요함: '
                  + re.sub(r'\s+', ' ', requirement))
        for line_no, raw_line in enumerate(text.splitlines(), 1):
            ids = CITATION_PATTERN.findall(raw_line)
            unknown = sorted(set(ids) - source_map.keys())
            if unknown:
                issue('ra_unknown_source', field, line_no, '알 수 없는 출처: ' + ', '.join(unknown))
            if _company_role_unconfirmed(role_field, CITATION_PATTERN.sub('', raw_line).strip(),
                                         [source_records[identifier] for identifier in ids if identifier in source_records]):
                issue('ra_company_role_unconfirmed', field, line_no,
                      '기입한 회사의 제조·수입·판매허가·발행 역할을 인용 자료에서 확인할 수 없음; 회사 역할의 결합 원문 확인이 필요함')
            parts = [part for source_id in dict.fromkeys(ids) if source_id in source_map
                     for part in _evidence_parts(source_records[source_id])]
            evidence = [part['text'] for part in parts]
            clean = CITATION_PATTERN.sub('', raw_line).strip()
            if clean and metadata and metadata.get('evidence_document_labels'):
                kinds = metadata['evidence_document_labels']
                if (not isinstance(kinds, list) or not kinds
                        or any(not isinstance(kind, str) or not kind.strip() for kind in kinds)):
                    raise ValueError('RA 원자료 종류 제한이 잘못됨')
                expected = _material_scope(role_field + '\n' + clean, len(role_field) + 1 + len(clean), names, selected)
                supported = False
                for part in parts:
                    if part['source'].get('filename') == '사용자 입력':
                        continue
                    for match in _quote_matches(clean, part['text']):
                        natural_scope = qualify_natural_scope(part['source'], metadata['label'],
                            expected_product=natural_product.split()[0] if natural_product else '',
                            known_products=names, allowed_document_labels=kinds,
                            quote_start=part['offset'] + match.start(), quote_end=part['offset'] + match.end())
                        if natural_scope is None:
                            continue
                        actual = _material_scope(part['source']['text'], part['offset'] + match.end(), names, part['source'])
                        if _scope_matches(expected, actual):
                            supported = True
                if not supported:
                    issue('ra_document_scope_unverified', field, line_no,
                          '동일 제품의 명시된 임상/안전성 원자료 종류와 기입 문구를 같은 인용 블록에서 확인할 수 없음; 추가 원자료 필요함')
            # Field labels and preceding lines are real context, especially for fixed cells.
            preceding_lines = '\n'.join(text.splitlines()[:line_no - 1])
            material_text = role_field + '\n' + preceding_lines + '\n' + clean
            material_offset = len(material_text) - len(clean)
            if field in {'제품명', '품목명', 'Product name', 'Medicinal product'} or metadata and metadata.get('evidence_role') == 'product_name':
                requested_name = selected['product_name'].split()[0].casefold() if selected['product_name'] else ''
                expected_product = _material_scope(material_text, len(material_text), names, selected)
                product_supported = any(_scope_matches(expected_product, _material_scope(
                    part['source']['text'], part['offset'] + match.end(), names, part['source']))
                    for part in parts for match in _quote_matches(clean, part['text']))
                if (requested_name and not re.search(r'(?<![A-Za-z0-9])' + re.escape(requested_name) + r'(?![A-Za-z0-9])', clean, re.I)) or not product_supported:
                    issue('ra_product_unverified', field, line_no, '제품명과 선택 품목의 동일 원문을 확인할 수 없음')
            if INDICATION_FIELD.search(role_field):
                assertion = re.sub(r'(?:함|임)$', '', re.sub(r'^[□○-]\s*', '', clean)).strip()
                expected = _material_scope(material_text, len(material_text), names, selected)
                supported = False
                if assertion:
                    for source_id in dict.fromkeys(ids):
                        if source_id not in source_records:
                            continue
                        record = source_records[source_id]
                        for match in _quote_matches(assertion, record['text']):
                            actual = _material_scope(record['text'], match.end(), names, record)
                            if _scope_matches(expected, actual):
                                supported = True
                if assertion and not supported:
                    issue('ra_indication_unverified', field, line_no, '치료 대상·적응증의 동일 원문과 제품 범위를 확인할 수 없음; 번역·요약은 추가 의미 검수 필요함')
            if APPEARANCE_FIELD.search(role_field) and clean:
                assertion = re.sub(r'(?:함|임)$', '', re.sub(r'^[□○-]\s*', '', clean)).strip()
                expected = _material_scope(material_text, len(material_text), names, selected)
                supported = any(_scope_matches(expected, _material_scope(record['text'], offset + match.end(), names, record))
                                for identifier in dict.fromkeys(ids) if identifier in source_records
                                for original in [source_records[identifier]]
                                for record, offset in [_context_source(original)]
                                for match in _quote_matches(assertion, original['text'])
                                if not _appearance_negation_omitted(record['text'], offset + match.start(), offset + match.end()))
                if not supported:
                    issue('ra_field_evidence_unverified', field, line_no,
                          '성상·외관의 기입 문구와 선택 제품 범위를 인용한 원자료에서 확인할 수 없음')
            for value, original, position, _ in _dates(clean):
                expected_scope = _scope(clean, position)
                expected_context = _context(clean, position)
                expected_material = _material_scope(material_text, material_offset + position, names, selected)
                supported = any(value and candidate[0] == value
                                and expected_scope.issubset(_scope(part['text'], candidate[2]))
                                and _scope_matches(expected_material, _material_scope(part['source']['text'], part['offset'] + candidate[2], names, part['source']))
                                and (not expected_context or not _context(part['text'], candidate[2])
                                     or expected_context == _context(part['text'], candidate[2]))
                                for part in parts for candidate in _dates(part['text']))
                if not supported:
                    issue('ra_date_mismatch', field, line_no, f'날짜를 인용 원자료에서 확인할 수 없음: {original}')
            if AMBIGUOUS_DATE.search(clean):
                issue('ra_ambiguous_date', field, line_no, '모호한 날짜 순서·연도는 원자료 확인이 필요함; 자동 해석하지 않음', 'warning')
            for kind, value in _ids(clean):
                if not any((kind, value) in _ids(part) for part in evidence):
                    issue('ra_identifier_mismatch', field, line_no, f'시험/배치 번호를 인용 원자료에서 확인할 수 없음: {value}')
            for clause in _clauses(clean):
                quantities = _quantities(clause)
                for match in UNIT_TEXT.finditer(clause):
                    unit = match['unit'].replace(' ', '').casefold()
                    if unit.startswith(('mg', 'mcg', 'ml', 'µg', 'μg', 'iu')) and not any(item['start'] == match.start() and item['end'] >= match.end() for item in quantities):
                        issue('ra_unit_unrecognized', field, line_no, f'의약품 단위 표기가 인식되지 않음: {match["unit"]}; 자동 교정하지 않음')
                for token in quantities:
                    clause_offset = material_offset + clean.find(clause)
                    expected = _material_scope(material_text, clause_offset + token['start'], names, selected)
                    role = _quantity_role(role_field + ' ' + clause, token)
                    use_variant = (role == 'strength' or bool(VARIANT_FIELD.search(role_field)) or field in {'제품명', '품목명'}
                                   or bool(metadata and metadata.get('evidence_role') == 'product_name'))
                    if use_variant and selected['product_variant']:
                        if not _scope_matches({'variant': _variant(selected['product_variant'])}, expected, variant=True):
                            issue('ra_variant_mismatch', field, line_no, '기입한 함량·제형이 선택 제품의 원문 범위와 다름')
                    matches = []
                    for part in parts:
                        for item in _quantities(part['text']):
                            contextual, context_offset = _context_source(part['source'])
                            actual = _material_scope(contextual['text'], context_offset + part['offset'] + item['start'], names, contextual)
                            candidate_role = _quantity_role(part['text'], item)
                            # A vial's amount and a dose/active excipient are different quantities.
                            same_role = not role or not candidate_role or role == candidate_role
                            same_context = bool(role and candidate_role and role == candidate_role) or not token['context'] or not item['context'] or token['context'] == item['context']
                            if (item['key'] == token['key'] and token['scope'].issubset(item['scope'])
                                    and same_role and same_context and _scope_matches(expected, actual, variant=use_variant)):
                                matches.append(item)
                    if not matches:
                        issue('ra_quantity_mismatch', field, line_no,
                              f'량·단위·시험/배치 문맥을 인용 원자료에서 확인할 수 없음: {token["raw"]}; 단위환산은 자동 수행하지 않음')
                skip = [(item['start'], item['end']) for item in quantities]
                skip += [(match.start(), match.end()) for match in IDENTIFIER.finditer(clause)]
                skip += [(item[2], item[3]) for item in _dates(clause)]
                skip += [(match.start(), match.end()) for match in AMBIGUOUS_DATE.finditer(clause)]
                skip += [(match.start(), match.end()) for match in INTERVAL.finditer(clause)]
                for token in number_tokens(clause):
                    if any(start <= token['start'] < end for start, end in skip):
                        continue
                    # PDF visual wraps must retain the same field's numeric context.
                    plain_lines = plain_field.splitlines()
                    prefix_length = sum(len(line) + 1 for line in plain_lines[:line_no - 1])
                    leading = len(plain_lines[line_no - 1]) - len(plain_lines[line_no - 1].lstrip())
                    absolute = prefix_length + leading + clean.find(clause) + token['start']
                    if absolute in field_tokens:
                        token = {**field_tokens[absolute], 'start': token['start'], 'end': token['end']}
                    # Only a fixed scalar/count value inherits a printed field
                    # label. Narrative numbering/duration keeps its own source
                    # context, including a complete verbatim dosage paragraph.
                    if re.fullmatch(r'-?\d[\d,]*(?:\.\d+)?\s*(?:정|캡슐|병|팩|포|상자|개)?', clause.strip()):
                        token = _field_number_tokens(labels.get(field, field), clause, tokens=[token])[0]
                    expected = _material_scope(material_text, material_offset + clean.find(clause) + token['start'], names, selected)
                    if DRUG_VALUE_FIELD.search(role_field + ' ' + clause) and not token['key'][1] and not quantities:
                        if any(item['key'][0] == token['key'][0] for part in evidence for item in _quantities(part)):
                            issue('ra_unit_missing', field, line_no, '용량·함량·농도 값의 단위가 누락됨; 원문 단위를 확인해야 함')
                    candidates = [item for part in parts if _scope(clause, token['start']).issubset(_ids(part['text']))
                                  for item in number_tokens(part['source']['text'])
                                  if part['offset'] <= item['start'] < part['offset'] + len(part['text'])
                                  for contextual, context_offset in [_context_source(part['source'])]
                                  if _scope_matches(expected, _material_scope(contextual['text'], context_offset + item['start'], names, contextual))]
                    if not any(item['key'] == token['key'] for item in _matching_candidates(token, candidates)):
                        issue('ra_number_mismatch', field, line_no, f'수치·단위·문맥을 인용 원자료에서 확인할 수 없음: {token["raw"]}')
                for pattern, code, label in [(FREQUENCY, 'ra_regimen_mismatch', '투여 빈도'),
                                             (INTERVAL, 'ra_regimen_mismatch', '투여 간격'), (ROUTE, 'ra_route_mismatch', '투여 경로')]:
                    for match in pattern.finditer(clause):
                        expected = _material_scope(material_text, material_offset + clean.find(clause) + match.start(), names, selected)
                        supported = False
                        for part in parts:
                            for candidate in pattern.finditer(part['text']):
                                actual = _material_scope(part['source']['text'], part['offset'] + candidate.start(), names, part['source'])
                                same_value = (_interval_key(match.group()) == _interval_key(candidate.group())) if pattern is INTERVAL else _normal(match.group()) == _normal(candidate.group())
                                candidate_sentence = _sentence_at(part['source']['text'], part['offset'] + candidate.start())
                                sentence_start = part['source']['text'].rfind(candidate_sentence, 0,
                                    part['offset'] + candidate.start() + len(candidate_sentence))
                                expected_dose = _regimen_quantity(clause, match.start())
                                actual_dose = _regimen_quantity(candidate_sentence, part['offset'] + candidate.start() - sentence_start)
                                same_dose = expected_dose is None or actual_dose is None or expected_dose == actual_dose
                                if same_value and same_dose and _scope(clause, match.start()).issubset(_ids(part['text'])) and _scope_matches(expected, actual):
                                    supported = True
                        if not supported:
                            issue(code, field, line_no, f'{label}를 인용 원자료에서 확인할 수 없음: {match.group()}')
                claims = _affirmative_claims(clause)
                if claims:
                    # ponytail: 의미 동등성을 자동 확정하지 않음. 원문과 다른 긍정 주장은 담당자가 근거 문구를 확인함.
                    claim_text = re.sub(r'(?:함|임|됨)$', '', clause.rstrip('.。').strip())
                    expected = _material_scope(material_text, material_offset + clean.find(clause) + claims[-1].end(), names, selected)
                    supported = any(_affirmative_claims(part['text']) and _normal(claim_text) in _normal(part['text'])
                                    and _scope_matches(expected, _material_scope(part['source']['text'], part['offset'] + len(part['text']), names, part['source']))
                                    for part in parts)
                    if not supported:
                        issue('ra_claim_unverified', field, line_no, '안전성·효능·승인·임상 완료 주장의 동일 근거 문구를 확인할 수 없음; 원문 확인 필요함')
    checks = [
        {'name': '출처·수치·의약품 단위·시험/배치·용법·날짜·주장 대조', 'status': 'failed' if any(i['severity'] == 'error' for i in issues) else 'passed'},
        {'name': '성상 근거·인용 문장의 연결된 적용 조건(동일 원문 범위)', 'status': 'failed' if any(i['code'] in {'ra_field_evidence_unverified', 'ra_condition_omitted', 'ra_context_invalid'} for i in issues) else 'passed'},
        {'name': '자동 수치 수정·단위 환산', 'status': 'disabled'},
        {'name': '신청서 고정 셀·CTD 원문 문체 강제', 'status': 'disabled'},
        {'name': '법적 적합성 및 임상적 타당성 인증', 'status': 'not_certified'},
    ]
    return {'domain': 'pharmaceutical_ra', 'blocking': any(i['severity'] == 'error' for i in issues),
            'issues': issues, 'checks': checks, 'workflow': workflow,
            'additional_checks': list(RA_WORKFLOWS[workflow]['checks']) if workflow else []}
