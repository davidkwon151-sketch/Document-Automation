"""Actual downloaded RA evidence -> verified fields -> original forms and annexes.

rules copies independently annotated quotations; it never measures LLM quality.
live executes the production generation/review pipeline and fails closed.
"""

from argparse import ArgumentParser
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from time import perf_counter
from urllib.parse import urlsplit
from uuid import uuid4

from pypdf import PdfReader
from pypdf.constants import UserAccessPermissions

from agent.documents import style_exempt_fields
from agent.pipeline import build_downloads, run_pipeline
from agent.ra import inspect_ra_draft
from agent.review import CITATION_PATTERN, number_tokens, review_draft
from templates import load_form_profile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / 'evals/ra_public_sources.json'
DEFAULT_PROFILES = ('ra_law_form_4_pdf', 'ra_law_form_20_pdf')


def normalized(text):
    return re.sub(r'\s+', '', text)


def read_source(record, root=ROOT):
    path = (root / record['path']).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix.lower() != '.pdf':
        raise ValueError('검증 원자료는 프로젝트 내부 PDF이어야 함')
    if record['filename'] != path.name or type(record['page_count']) is not int or record['page_count'] < 1:
        raise ValueError('원자료 파일명·페이지 수 기록이 잘못됨')
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != record['sha256']:
        raise ValueError('인터넷 원자료 SHA-256이 수집 기록과 다름')
    if 'size_bytes' in record and (type(record['size_bytes']) is not int or len(raw) != record['size_bytes']):
        raise ValueError('원자료 크기가 수집 기록과 다름')
    reader = PdfReader(path)
    if reader.is_encrypted:
        permissions = reader.user_access_permissions
        if permissions is None or not permissions & UserAccessPermissions.EXTRACT:
            raise ValueError('원자료 PDF의 텍스트 추출 허용을 확인할 수 없음; 보호를 유지하고 자동 평가를 보류함')
    if len(reader.pages) != record['page_count']:
        raise ValueError('원자료 페이지 수가 수집 기록과 다름')
    def page_text(page):
        if type(page) is not int or not 1 <= page <= len(reader.pages):
            raise ValueError('인용 페이지가 원자료 범위를 벗어남')
        return reader.pages[page - 1].extract_text() or ''
    company = record['company']
    company_text = page_text(company['page'])
    if (not company['exact_quote'].strip() or normalized(company['name']) != normalized(company['exact_quote'])
            or normalized(company['exact_quote']) not in normalized(company_text)):
        raise ValueError('원자료 회사명 인용·페이지가 일치하지 않음')
    heading_page = company.get('role_heading_page', company['page'])
    heading = page_text(heading_page)
    if company['role'] == 'marketing_authorisation_holder':
        start = re.search(r'\bMARKETING\s+AUTHORISATION\s+HOLDER\b', heading, re.I)
        if start is None or not heading_page <= company['page'] <= heading_page + 1:
            raise ValueError('회사명과 판매허가권자 역할의 원문 범위를 확인할 수 없음')
        section = heading[start.end():] + ('\n' + company_text if company['page'] != heading_page else '')
        boundary = re.search(r'(?m)^[ \t]*(?:\d{1,2}|[A-Z])\.[ \t]+[A-Z][A-Z ()/]+[ \t]*(?:\n|$)', section)
        if boundary is not None:
            section = section[:boundary.start()]
        if normalized(company['exact_quote']) not in normalized(section):
            raise ValueError('회사명이 판매허가권자 원문 섹션에 연결되어 있지 않음')
    elif company['role'] in {'manufacturer', 'importer', 'distributor'}:
        labels = {'manufacturer': ('제조원', '제조자', '제조업자', '제조판매업자', 'MANUFACTURER'),
                  'importer': ('수입원', '수입자', '수입업자', 'IMPORTER'),
                  'distributor': ('판매원', '판매자', '판매업자', 'DISTRIBUTOR')}
        quote = company.get('role_exact_quote', '')
        label = company.get('role_heading_quote', '')
        if (not label or not any(token in normalized(label).upper() for token in labels[company['role']])
                or normalized(label) not in normalized(quote)
                or normalized(company['exact_quote']) not in normalized(quote)
                or normalized(quote) not in normalized(heading)):
            raise ValueError('회사명과 제조·수입·판매 역할의 결합 원문을 확인할 수 없음')
    elif company['role'] == 'document_publisher':
        domain = company.get('official_domain', '')
        hosts = [urlsplit(record[key]).hostname or '' for key in ('source_url', 'source_page')]
        if (not domain or domain != domain.lower() or '/' in domain
                or any(host != domain and not host.endswith('.' + domain) for host in hosts)
                or any(urlsplit(record[key]).scheme != 'https' for key in ('source_url', 'source_page'))):
            raise ValueError('문서 발행사의 공식 게시·다운로드 도메인을 확인할 수 없음')
    else:
        raise ValueError('회사 역할을 원자료에서 확인할 수 없음')
    facts, sources, blocks = [], [], []
    names = [fact['value'] for fact in record['facts'] if fact['role'] == 'product_name']
    if len(names) != 1 or normalized(names[0]) != normalized(record['product_variant']):
        raise ValueError('선택 제형은 제품명 원문 전체와 같아야 함')
    product_name = record['product_name'].strip()
    product_pattern = r'\s+'.join(re.escape(part) for part in product_name.split())
    if not product_name or not re.match(product_pattern + r'(?=$|[\s(®™])', record['product_variant'], re.I):
        raise ValueError('제품명은 선택 제형 원문에서 완전한 이름으로 확인되어야 함')
    for index, fact in enumerate(record['facts']):
        page = fact['page']
        text = page_text(page)
        quote = fact['exact_quote']
        if not quote.strip() or normalized(quote) not in normalized(text) or normalized(fact['value']) != normalized(quote):
            raise ValueError('선택 사실의 전체 원문·페이지·값이 일치하지 않음')
        if fact.get('product_variant', record['product_variant']) != record['product_variant']:
            raise ValueError('한 평가에서 서로 다른 제품 제형을 합칠 수 없음')
        scope = {key: record[key] for key in ('product_name', 'product_variant', 'source_url', 'jurisdiction')}
        scope.update(company_name=company['name'], company_role=company['role'])
        scope['product_variant'] = fact.get('product_variant', scope['product_variant'])
        scope['document_sha256'] = record['sha256']
        scope['regulatory_role'] = fact['role']
        origin = f"{record['sha256']}|{page}|{quote}|{fact['role']}"
        identifier = 'S' + sha256(origin.encode()).hexdigest()[:16]
        sources.append({**scope, 'source_id': identifier, 'text': quote, 'filename': record['filename'],
                        'page': page, 'sheet': None, 'location': f'확인된 원문 사실 {index + 1}'})
        facts.append({**fact, **scope, 'filename': record['filename'], 'source_id': identifier})
        blocks.append({**scope, '본문': quote, '페이지': page, '시트': None,
                       '위치': f"{fact['field_key']} / {fact['role']}", '표 목록': []})
    document = {'파일명': record['filename'], '본문': '\n'.join(block['본문'] for block in blocks),
                '표 목록': [], '페이지/시트 정보': blocks,
                'source_url': record['source_url'], 'document_sha256': record['sha256'],
                'product_name': record['product_name'], 'product_variant': record['product_variant'],
                'jurisdiction': record['jurisdiction'],
                'company_name': company['name'], 'company_role': company['role']}
    return facts, sources, document


def cited(value, identifier):
    return '\n'.join(f'{line} [{identifier}]' if line.strip() else line for line in value.splitlines())


def compare_values(draft, facts, source_ids):
    """Require the cited evidence for this fact, not merely any known ID.

    Production retrieval splits quotes into chunks and assigns new IDs. Those IDs
    are accepted only with the same document/page/product/role and exact text.
    """
    evidence = ({source['source_id']: source for source in source_ids}
                if isinstance(source_ids, (list, tuple)) and all(isinstance(source, dict) for source in source_ids)
                else None)
    if evidence is not None and len(evidence) != len(source_ids):
        raise ValueError('동일 출처 ID의 중복 근거는 평가할 수 없음')
    known = set(evidence) if evidence is not None else set(source_ids)

    def supported(line, fact):
        ids = CITATION_PATTERN.findall(line)
        if not ids or not set(ids) <= known:
            return False
        if 'source_id' not in fact:
            return True  # Legacy callers without annotated fact provenance.
        if evidence is None:
            return set(ids) == {fact['source_id']}
        references = [evidence[identifier] for identifier in ids]
        scope_keys = ('document_sha256', 'page', 'product_name', 'product_variant', 'company_name',
                      'company_role', 'regulatory_role', 'filename', 'jurisdiction', 'source_url')
        expected = dict(fact)
        if 'role' in fact:
            expected['regulatory_role'] = fact['role']
        if any(key not in expected for key in scope_keys):
            return False
        quote = normalized(fact['value'])
        for reference in references:
            if any(key in expected and reference.get(key) != expected[key] for key in scope_keys):
                return False
            text = normalized(reference.get('text', ''))
            if not text or text not in quote:
                return False
        plain = normalized(CITATION_PATTERN.sub('', line))
        return bool(plain) and (any(plain in normalized(reference['text']) for reference in references)
                                or plain in normalized(''.join(reference['text'] for reference in references)))

    preserved = cited_count = numeric = numeric_total = 0
    for fact in facts:
        value = draft.get(fact['field_key'], '')
        plain = CITATION_PATTERN.sub('', value)
        preserved += normalized(plain) == normalized(fact['value'])
        cited_count += bool(value.strip()) and all(supported(line, fact) for line in value.splitlines() if line.strip())
        expected = [token['key'] for token in number_tokens(fact['value'])]
        if expected:
            numeric_total += 1
            numeric += expected == [token['key'] for token in number_tokens(plain)]
    return {'selected_field_count': len(facts), 'full_value_preserved_count': preserved,
            'cited_field_count': cited_count, 'numeric_field_count': numeric_total, 'numeric_exact_count': numeric}


def validate_source_parser(record, root=ROOT):
    """Compare full production M1 parsing with independently annotated PDF facts."""
    from parsers import parse_file

    facts, _, _ = read_source(record, root)
    path = root / record['path']
    started = perf_counter()
    document = parse_file(path)
    blocks = document['페이지/시트 정보']
    pages = {block.get('페이지'): block['본문'] for block in blocks}
    if set(pages) != set(range(1, record['page_count'] + 1)) or len(blocks) != record['page_count']:
        raise ValueError('운영 파서가 원자료 전체 페이지를 보존하지 못함')
    if any(not text.strip() for text in pages.values()):
        raise ValueError('텍스트가 없는 페이지는 OCR 또는 빈 페이지 확인이 필요함; 전체 판독 성공으로 집계하지 않음')
    for fact in facts:
        if normalized(fact['exact_quote']) not in normalized(pages[fact['page']]):
            raise ValueError(f"운영 파서와 독립 판독의 원문·페이지가 다름: {fact['field_key']}")
    if sha256(path.read_bytes()).hexdigest() != record['sha256']:
        raise ValueError('운영 파싱 중 원자료가 변경됨')
    return {'source_id': record['id'], 'passed': True, 'page_count': len(pages),
            'table_count': len(document['표 목록']), 'fact_count': len(facts),
            'seconds': perf_counter() - started, 'original_unchanged': True}


def _native_artifacts(result, row, artifact_dir, stem):
    """Persist private preview payload separately from JSON verification records."""
    reports = result.get('native_output_verification', {})
    row['native_output_verification'] = reports
    artifacts = {}
    for suffix, data in result.get('_native_preview_bytes', {}).items():
        if suffix not in {'pdf', 'docx', 'hwpx', 'xlsx', 'pptx'} or not isinstance(data, bytes):
            raise ValueError('출력 미리보기 형식이 잘못됨')
        digest = sha256(data).hexdigest()
        report = reports.get(suffix, {})
        pages = len(PdfReader(BytesIO(data)).pages)
        if digest != report.get('pdf_sha256') or pages != report.get('page_count'):
            raise ValueError('출력 미리보기와 검수 SHA·쪽수가 일치하지 않음')
        path = artifact_dir / f'{stem}_native_preview_{suffix}.pdf'
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('저장한 출력 미리보기 SHA가 일치하지 않음')
        artifacts[suffix] = {'path': str(path), 'sha256': digest, 'page_count': pages}
    row['native_preview_artifacts'] = artifacts


def _save_provenance(artifact_dir, stem, row, record, draft, sources):
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f'{stem}.provenance.json'
    row['provenance'] = str(path)
    provenance = {**row, 'source': record, 'draft': draft, 'sources': sources}
    path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')


def evaluate_case(record, profile_id, *, mode, artifact_dir, client=None, native_review='off'):
    if native_review not in {'off', 'auto', 'required'}:
        raise ValueError('출력 확인 모드는 off, auto 또는 required이어야 함')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', profile_id):
        raise ValueError('등록 양식 ID가 올바르지 않음')
    if not isinstance(record.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]+', record['id']):
        raise ValueError('원자료 평가 ID가 올바르지 않음')
    artifact_dir = Path(artifact_dir)
    # Identity is recorded in the row/sidecar, not duplicated in long paths.
    stem = 'review'
    facts, sources, document = read_source(record)
    profile_path = ROOT / 'templates/profiles' / (profile_id + '.json')
    binding = json.loads(profile_path.read_text(encoding='utf-8'))
    template = (ROOT / binding.get('source_path', 'data/public_templates/ra/' + binding['source_filename'])).resolve()
    if not template.is_relative_to(ROOT.resolve()):
        raise ValueError('평가 양식은 프로젝트 내부 원본이어야 함')
    profile = load_form_profile(template, profile_id)
    if profile is None:
        raise ValueError('실제 RA 양식과 등록 프로파일 SHA가 일치하지 않음')
    profile = {**profile, 'citation_mode': 'sidecar', 'document_kind': 'application',
               'ra_product_name': record['product_name'], 'ra_product_variant': record['product_variant']}
    if profile.get('format') == 'pdf' and profile.get('render_mode') == 'overlay':
        profile['overflow_mode'] = 'annex'
    keys = {field['value_key'] for field in profile['fields'] if not field.get('input_required')}
    selected = [fact for fact in facts if fact['field_key'] in keys]
    if not selected or len({fact['field_key'] for fact in selected}) != len(selected):
        raise ValueError('이 평가에는 중복 없는 양식 항목별 원문 정답이 필요함')
    # Preserve each attempt separately: a pending rerun must not pair its
    # provenance with a previous successful PDF under the same filename.
    execution_id = uuid4().hex
    artifact_dir = artifact_dir / execution_id
    started = perf_counter()
    model_received = False
    if mode == 'live':
        instruction = (f"RA 담당팀장에게 {record['product_variant']}의 공개 {record['jurisdiction']} 자료를 검토용으로 정리해줘. "
                       '첨부에서 확인한 양식 항목은 원문 전체를 그대로 기입하고 다른 제형·대상·초기/유지 용량을 혼합하지 마. '
                       '국내 허가, 갱신 신청 요건이나 제출 완료로 판단하지 말고 신청인·개인정보·서명은 빈칸으로 남겨줘.')
        result = run_pipeline(instruction, documents=[document], client=client, template_profile=profile,
                              ra_workflow=profile.get('ra_workflow'), document_kind='application', semantic_review=True)
        model_received = bool(result.get('draft'))
        if result.get('status') not in {'ready', 'needs_revision'}:
            return {'passed': False, 'status': result['status'], 'native_review': native_review, 'model_response_received': model_received,
                    'questions': result.get('questions', []), 'error': '실제 모델이 검증 가능한 초안을 완성하지 못함'}
        draft, sources = result['draft'], result['sources']
    else:
        draft = {'제목': 'RA 공개 원자료 검토', '요약': cited(selected[0]['value'], selected[0]['source_id']),
                 '본문': cited(selected[0]['value'], selected[0]['source_id'])}
        draft.update({fact['field_key']: cited(fact['value'], fact['source_id']) for fact in selected})
        for field in profile['fields']:
            draft.setdefault(field['value_key'], '')
        result = {'status': 'ready', 'draft': draft, 'sources': sources, 'template_profile': profile,
                  'domain': 'pharmaceutical_ra', 'ra_workflow': profile.get('ra_workflow'), 'document_kind': 'application'}
    generic = review_draft(draft, sources, auto_correct=False, style_exempt_fields=style_exempt_fields(profile))
    professional = inspect_ra_draft(draft, sources, profile=profile)
    scores = compare_values(draft, selected, sources)
    row = {'mode': mode, 'native_review': native_review, 'execution_id': execution_id, 'profile_id': profile_id, 'model_response_received': model_received, 'source_page_scope': sorted({fact['page'] for fact in selected}),
           'source_page_count': record['page_count'], 'selected_fact_count': len(selected), 'scores': scores,
           'review_issues': generic['warnings'], 'ra_issues': professional['issues'],
           'unfilled_fields': [field['value_key'] for field in profile['fields'] if not draft.get(field['value_key'], '').strip()],
           'submission_ready': False, 'korean_authorisation_verified': False}
    row['passed'] = (not generic['blocking'] and not professional['blocking']
                     and scores['full_value_preserved_count'] == len(selected)
                     and scores['cited_field_count'] == len(selected)
                     and scores['numeric_exact_count'] == scores['numeric_field_count'])
    if row['passed']:
        try:
            payload = build_downloads(result, confirmed=True, template_paths={'pdf': template},
                                      template_profiles={'pdf': profile}, native_review=native_review)
            _native_artifacts(result, row, artifact_dir, stem)
        except Exception as exc:
            diagnostics = result.get('native_output_verification', {})
            row.update(passed=False, native_output_verification=diagnostics,
                       native_preview_artifacts=row.get('native_preview_artifacts', {}),
                       native_pending=bool(native_review == 'required' and diagnostics
                           and any(report.get('status') in {'warning', 'unavailable'} for report in diagnostics.values())
                           and all(not report.get('blocking') and report.get('status') in {'passed', 'warning', 'unavailable'}
                                   for report in diagnostics.values())))
            # Native diagnostic issues are sanitized by the production inspector;
            # renderer exceptions may contain private paths/document strings.
            row['error'] = '문서 프로그램 출력 확인을 완료하지 못함' if native_review != 'off' else str(exc)
            row['seconds'] = perf_counter() - started
            if native_review != 'off':
                _save_provenance(artifact_dir, stem, row, record, draft, sources)
            return row
        artifact_dir.mkdir(parents=True, exist_ok=True)
        output = artifact_dir / f'{stem}.pdf'
        output.write_bytes(payload['pdf'])
        row['output'] = str(output)
        row['output_verification'] = result['output_verification']['pdf']
        row['original_pages'] = len(PdfReader(template).pages)
        row['output_pages'] = len(PdfReader(output).pages)
        _save_provenance(artifact_dir, stem, row, record, draft, sources)
    row['seconds'] = perf_counter() - started
    return row


def evaluate(manifest, *, mode='rules', profiles=DEFAULT_PROFILES, artifact_dir=None, client=None, check_parser=False, native_review='off'):
    if mode not in {'rules', 'live'}:
        raise ValueError('평가 모드는 rules 또는 live이어야 함')
    if native_review not in {'off', 'auto', 'required'}:
        raise ValueError('출력 확인 모드는 off, auto 또는 required이어야 함')
    records = manifest.get('sources') if isinstance(manifest, dict) else None
    if not isinstance(records, list) or not records:
        raise ValueError('평가할 원자료가 하나 이상 필요함')
    identifiers = [record.get('id') for record in records if isinstance(record, dict)]
    if (len(identifiers) != len(records) or any(not isinstance(identifier, str)
            or not re.fullmatch(r'[A-Za-z0-9_-]+', identifier) for identifier in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise ValueError('원자료 평가 ID는 유효하고 중복 없이 구분되어야 함')
    if (not isinstance(profiles, (list, tuple)) or not profiles
            or any(not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', profile) for profile in profiles)
            or len(set(profiles)) != len(profiles)):
        raise ValueError('평가 양식은 하나 이상이며 유효하고 중복 없어야 함')
    artifact_dir = Path(artifact_dir or ROOT / 'outputs/ra-public-validation')
    configuration_error = None
    if mode == 'live' and client is None:
        try:
            from llm.client import LLMClient
            client = LLMClient()
        except Exception as exc:
            configuration_error = str(exc)
    rows, parser_results = [], []
    for record in manifest['sources']:
        parser_error = None
        if check_parser:
            try:
                parser_results.append(validate_source_parser(record))
            except Exception as exc:
                parser_error = str(exc)
                parser_results.append({'source_id': record['id'], 'passed': False, 'error': parser_error})
        for profile_id in profiles:
            row = {'id': record['id'] + ':' + profile_id, 'native_review': native_review, 'company': record['company']['name'],
                   'company_role': record['company']['role'], 'jurisdiction': record['jurisdiction'],
                   'product': record['product_variant'], 'profile_id': profile_id,
                   'source_url': record['source_url'], 'source_sha256': record['sha256'],
                   'source_version_label': record.get('version_label'),
                   'current_source_version_verified': record.get('current_version_verified', False)}
            try:
                if parser_error:
                    raise ValueError(parser_error)
                if configuration_error:
                    raise ValueError(configuration_error)
                row.update(evaluate_case(record, profile_id, mode=mode, artifact_dir=artifact_dir,
                                         client=client, native_review=native_review))
            except Exception as exc:
                row.update(passed=False, error=str(exc))
            rows.append(row)
    return {'mode': mode, 'native_review': native_review, 'checked_at': datetime.now(timezone.utc).isoformat(),
            'company_count': len({row['company'] for row in rows}), 'case_count': len(rows),
            'passed_count': sum(row['passed'] for row in rows),
            'model_evaluated': any(row.get('model_response_received') for row in rows),
            'model_response_count': sum(bool(row.get('model_response_received')) for row in rows),
            'parser_checked': check_parser, 'parser_results': parser_results,
            'model': getattr(client, 'model', None) if client else None, 'results': rows,
            'scope': '공식 공개 제품정보의 선택 사실과 등록 RA 양식 입력칸 대조임',
            'limitations': ['rules는 사람이 확인한 원문을 그대로 옮기는 검사이며 AI 생성 품질을 측정하지 않음',
                            '공개 원본만 사용하며 비공개 기업 양식이나 공급·거래 관계를 추정하지 않음',
                            'EU 제품정보는 대한민국 허가·갱신·임상시험 승인이나 법정 필수자료·제출 적합성의 증거가 아님',
                            '누락된 신청인·서명·허가번호·제조·첨부자료를 채우거나 전체 제출 완료로 처리하지 않음']}


def main(argv=None):
    parser = ArgumentParser(description='실제 인터넷 RA 원자료와 공개 양식 검증')
    parser.add_argument('--mode', choices=['rules', 'live'], default='rules')
    parser.add_argument('--native-review', choices=['off', 'auto', 'required'], default='off',
                        help='추가 문서 프로그램 출력 확인. required는 미평가·경고도 실패로 집계함')
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--profile', action='append')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/ra-public-validation.json')
    parser.add_argument('--artifact-dir', type=Path, default=ROOT / 'outputs/ra-public-validation')
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--check-parser', action='store_true', help='실제 M1 전체 페이지 파싱도 독립 원문과 대조함')
    args = parser.parse_args(argv)
    raw = args.manifest.read_bytes()
    report = evaluate(json.loads(raw.decode('utf-8')), mode=args.mode,
                      profiles=tuple(args.profile or DEFAULT_PROFILES), artifact_dir=args.artifact_dir,
                      check_parser=args.check_parser, native_review=args.native_review)
    report['parser_checked'] = args.check_parser
    report['native_review'] = args.native_review
    report['manifest_sha256'] = sha256(raw).hexdigest()
    report['prompt_sha256'] = {path.name: sha256(path.read_bytes()).hexdigest() for path in sorted((ROOT / 'prompts').glob('*.md'))}
    report['profile_sha256'] = {name: sha256((ROOT / 'templates/profiles' / (name + '.json')).read_bytes()).hexdigest()
                                for name in args.profile or DEFAULT_PROFILES}
    if args.baseline:
        previous = json.loads(args.baseline.read_text(encoding='utf-8'))
        if (previous['mode'] != report['mode'] or previous.get('native_review', 'off') != report['native_review']
                or previous['manifest_sha256'] != report['manifest_sha256']
                or previous.get('parser_checked', False) != report['parser_checked']
                or previous.get('profile_sha256') != report['profile_sha256']):
            raise ValueError('같은 모드·동일 원자료·양식 프로파일의 기준 결과가 필요함')
        old = {row['id']: row for row in previous['results']}
        report['regressions'] = [row['id'] for row in report['results'] if old.get(row['id'], {}).get('passed') and not row['passed']]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('mode', 'native_review', 'company_count', 'case_count', 'passed_count', 'model_evaluated')}, ensure_ascii=False))
    return int(report['passed_count'] != report['case_count'])


if __name__ == '__main__':
    raise SystemExit(main())
