from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import importlib.util
import json

import pytest
from agent.retrieve import load_documents, chunk_documents
from agent.ra_scope import qualify_natural_scope
from agent.ra import inspect_ra_draft, _verified_draft_product
from app import ra_mvp_service as service
from agent.review import CITATION_PATTERN
from evals.run import MockEvaluationClient

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'outputs/ranat' / uuid4().hex[:8]
PUBLIC = ROOT / 'tests/fixtures/ra_protocol_public/full-original-protocol.pdf'
PUBLIC_PAGE17 = ROOT / 'tests/fixtures/ra_protocol_public/public-protocol-page-17.pdf'
PUBLIC_SHA = '52c3066161ceee95cf4ecfea4656a81c36260bfcdb8e645ee669c8c9694d8007'
V2 = 'ra_law_form_23_pdf_v2_second_page'

@pytest.fixture(scope='module', autouse=True)
def isolated_run_artifacts():
    HERE.mkdir(parents=True, exist_ok=True)

@pytest.fixture(scope='module')
def actual_sources():
    documents = load_documents([PUBLIC], allow_ocr=False)
    assert len(documents[0]['페이지/시트 정보']) == 68
    assert documents[0]['document_sha256'] == PUBLIC_SHA
    return chunk_documents(documents)

def test_actual_pdf_paths_parser_chunks_reach_natural_scope(actual_sources):
    positives = []
    for page, label in [(17, '임상시험 목적'), (31, '투여 방법')]:
        matches = [(source, qualify_natural_scope(source, label, expected_product='REGN3918'))
                   for source in actual_sources if source['page'] == page]
        matches = [(source, anchor) for source, anchor in matches if anchor]
        assert matches, (page, label)
        for source, anchor in matches:
            assert source['document_sha256'] == anchor['document_sha256'] == PUBLIC_SHA
            assert source['context_text'][source['context_start']:source['context_end']] == source['text']
            positives.append({'source': source, 'qualification': anchor})
    assert not any(qualify_natural_scope(source, '임상시험 목적', expected_product='REGN3918')
                   for source in actual_sources if source['page'] == 8)
    (HERE / 'actual-parser-qualifications.json').write_text(json.dumps({'actual_api_calls': 0,
        'parsed_original_pages': 68, 'verified_sections_pages': [17, 31], 'source_sha256': PUBLIC_SHA,
        'positives': positives}, ensure_ascii=False, indent=2), encoding='utf-8')

@pytest.mark.parametrize('suffix,parser', [('.docx', 'local'), ('.pdf', 'extended')])
@pytest.mark.parametrize('failure', ['changed', 'declared', 'block-declared', 'table-declared'])
def test_parser_sha_conflict_or_mutation_is_blocked(tmp_path, monkeypatch, suffix, parser, failure):
    import parsers
    import parsers.extended
    path = tmp_path / ('input' + suffix)
    path.write_bytes(b'original immutable bytes')
    def parse(path, **kwargs):
        doc = {'파일명': Path(path).name, '본문': 'keep', '표 목록': [],
               '페이지/시트 정보': [{'본문': 'keep', '페이지': 1, '표 목록': []}]}
        if failure == 'changed':
            Path(path).write_bytes(b'changed bytes')
        elif failure == 'declared':
            doc['document_sha256'] = '0' * 64
        elif failure == 'block-declared':
            doc['페이지/시트 정보'][0]['document_sha256'] = '0' * 64
        else:
            doc['표 목록'] = [{'document_sha256': '0' * 64}]
        return doc
    monkeypatch.setattr(parsers if parser == 'local' else parsers.extended,
                        'parse_file' if parser == 'local' else 'parse_extended', parse)
    with pytest.raises(ValueError, match='파싱 중 변경|SHA'):
        load_documents([path], allow_ocr=False)

def test_parser_preserves_ocr_tables_and_metadata(tmp_path, monkeypatch):
    import parsers.extended
    path = tmp_path / 'scanned.pdf'
    path.write_bytes(b'original')
    digest = sha256(path.read_bytes()).hexdigest()
    doc = {'파일명': path.name, '본문': 'keep', '표 목록': [{'행': [['x']]}],
        '페이지/시트 정보': [{'본문': 'keep', '페이지': 1, '검증 필요': True,
             '추출 방식': 'AI OCR', '불확실한 항목': ['x'], 'document_sha256': digest}],
        'company_role': 'issuer', 'custom_original_metadata': {'preserve': True},
        'document_sha256': digest}
    def parse(path, **kwargs):
        assert kwargs == {'client': None, 'allow_ocr': False}
        return deepcopy(doc)
    monkeypatch.setattr(parsers.extended, 'parse_extended', parse)
    assert load_documents([path], allow_ocr=False) == [doc]

@pytest.mark.parametrize('identifier', ['VV-RIM-00072054-1.0', 'ABC-DOC-002-2.5'])
def test_document_control_identifier_and_timezone_are_not_business_quantities(identifier):
    from agent.retrieve import find_conflicts
    sources = chunk_documents([{'파일명': 'control.txt', '본문': '', '표 목록': [],
        '페이지/시트 정보': [{'본문': identifier + ' Approved - 19 Mar 2019 GMT-5:00\nDose: 5 mg\nDose: 50 mg',
                       '페이지': 1, '표 목록': []}]}])
    conflicts = find_conflicts(sources)
    assert conflicts and all(c['context'] not in ('RIM', 'GMT', 'DOC') for c in conflicts)
    assert any(set(c['values']) == {'5', '50'} for c in conflicts)
    assert identifier in sources[0]['text']

def test_english_article_is_not_product_name():
    from agent.ra import _product_names
    assert 'the' not in _product_names(['The primary objective is pending.'], [], {})

@pytest.mark.parametrize('line', ['Dose DOSE-20-50 mg', 'Document ID: DOSE-20-50 mg',
    'Document ID: LIMIT-20-50 ng', 'Document ID: LIMIT-20-50 µg', 'Document ID: LIMIT-20-50 %',
    'Document ID: DOSE-20-50 Dose 5 mg', 'VV-RIM-00072054-1.0 Approved - 19 Mar 2019 GMT-5:00 dose 5 mg'])
def test_document_shaped_numbers_with_measurements_remain_numeric(line):
    from agent.retrieve import _conflict_tokens
    tokens = list(_conflict_tokens({'filename': 'synthetic.pdf', 'source_id': 'STest', 'text': line}, set()))
    assert tokens
    assert any(token['key'][1] for token in tokens)

def synthetic_source(section='2. Study Objectives', product='SyntheticDrugA'):
    quote = product + ' study objectives remain under evaluation.'
    context = f'Clinical Study Protocol TEST-0001 Original\n{section}\n{quote}\n3. Study Design\nOther text'
    start = context.index(quote)
    return {'filename': 'synthetic.pdf', 'document_sha256': 'a' * 64, 'page': 1,
       'source_id': 'SSynthetic', 'text': quote, 'context_text': context,
       'context_start': start, 'context_end': start + len(quote)}

@pytest.mark.parametrize('change', ['valid', 'wrong-section', 'wrong-type', 'unknown-product',
    'user-product', 'fake-product-citation', 'ocr-product', 'product-conflict'])
def test_registered_cited_product_selected_for_natural_only_when_verified(change):
    _, profile, _ = service.resolve_mvp_form(V2)
    source = synthetic_source('3. Study Design' if change == 'wrong-section' else '2. Study Objectives')
    if change == 'wrong-type':
        source['context_text'] = source['context_text'].replace('Clinical Study Protocol TEST-0001 Original', 'FDA E2F Guideline')
        source['context_start'] = source['context_text'].index(source['text'])
        source['context_end'] = source['context_start'] + len(source['text'])
    draft = {'시험약 제품명': 'SyntheticDrugA [SSynthetic]',
             '임상시험 목적': source['text'] + ' [SSynthetic]'}
    sources = [source]
    if change == 'unknown-product': draft.pop('시험약 제품명')
    if change == 'user-product':
        draft['시험약 제품명'] = 'SyntheticDrugA [SUser]'
        sources.append({'source_id': 'SUser', 'filename': '사용자 입력', 'text': 'SyntheticDrugA'})
    if change == 'fake-product-citation': draft['시험약 제품명'] = 'SyntheticDrugA [SMissing]'
    if change == 'ocr-product': source['requires_verification'] = True
    if change == 'product-conflict':
        profile['fields'].append({**next(f for f in profile['fields'] if f.get('evidence_role') == 'product_name'),
            'id': 'other-product', 'value_key': '다른 제품명'})
        sources.append({**synthetic_source(product='SyntheticDrugB'), 'source_id': 'SOther'})
        draft['다른 제품명'] = 'SyntheticDrugB [SOther]'
    result = inspect_ra_draft(draft, sources, profile=profile)
    assert any(i['code'] == 'ra_document_scope_unverified' for i in result['issues']) == (change != 'valid'), result

class PublicProtocolClient(MockEvaluationClient):
    def __init__(self, omit_product=False):
        super().__init__({'mock_brief': {'목적': 'To evaluate the immunogenicity of REGN3918 immunogenicity 임상시험 목적',
            '보고 대상': 'RA 담당자', '보고서 유형': '결과보고서', '마감': '', '분량': '목적 원문',
            '부족한 정보': [], '질문': []}})
        self.omit_product = omit_product
    def generate_json(self, name, payload):
        if name == 'draft':
            matches = [s for s in payload['sources'] if
                qualify_natural_scope(s, '임상시험 목적', expected_product='REGN3918')]
            assert matches, 'real retrieved page17 objectives missing'
            source = next(s for s in matches if 'To evaluate the immunogenicity of REGN3918' in s['text'])
            cited = 'To evaluate the immunogenicity of REGN3918 [' + source['source_id'] + ']'
            result = {'제목': '공개 계획서 원문 검토', '요약': cited, '본문': cited, '임상시험 목적': cited}
            if not self.omit_product: result['시험약 제품명'] = 'REGN3918 [' + source['source_id'] + ']'
            return result
        if name == 'grounding':
            by_id = {s['source_id']: s['text'] for s in payload['sources']}
            return {'claims': [{'field': c['field'], 'line': c['line'], 'status': 'supported',
                'evidence': [{'source_id': sid, 'quote': by_id[sid]} for sid in CITATION_PATTERN.findall(c['text']) if sid in by_id]}
                for c in payload['claims']]}
        if name == 'completeness': return {'issues': [], 'checked_fields': list(payload['draft'])}
        return super().generate_json(name, payload)

@pytest.mark.parametrize('omit_product', [False, True])
@pytest.mark.parametrize('source_kind', ['page17-excerpt', 'whole68'])
def test_actual_paths_generate_mvp_with_mock_selects_only_original_cited_product(omit_product, source_kind):
    attachment = PUBLIC if source_kind == 'whole68' else PUBLIC_PAGE17
    case_dir = HERE / source_kind
    case_dir.mkdir(parents=True, exist_ok=True)
    result = service.generate_mvp('REGN3918 Study Objectives 임상시험 목적 원문을 검토함', V2,
        paths=[attachment], client=PublicProtocolClient(omit_product))
    scope_errors = [i for i in result.get('review', {}).get('warnings', [])
                    if i['code'] == 'ra_document_scope_unverified']
    (case_dir / ('actual-generate-unknown.json' if omit_product else 'actual-generate-verified.json')).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    assert bool(scope_errors) == omit_product, result.get('review')
    if not omit_product:
        assert result['status'] == 'ready', result.get('review')
        from agent.pipeline import build_downloads
        from agent.output_check import verify_output
        from agent.field_citations import profile_field, split_field_citations
        payload = build_downloads(result, confirmed=True, template_paths=result['template_paths'],
            template_profiles={'pdf': result['template_profile']}, native_review='off')['pdf']
        output = case_dir / 'actual-public-purpose.pdf'
        output.write_bytes(payload)
        values = {key: split_field_citations(value, profile_field(result['template_profile'], key))[0]
                  for key, value in result['draft'].items()}
        check = verify_output(result['template_paths']['pdf'], output, values, profile=result['template_profile'])
        assert check['status'] == 'passed', check
        (case_dir / 'actual-public-output-check.json').write_text(json.dumps({
            'evaluation': 'actual public original quotation + mock model; not clinical certification',
            'actual_api_calls': 0, 'whole_original_sha256': PUBLIC_SHA,
            'original_source_page': 17, 'attachment_kind': source_kind,
            'attachment_page': 17 if source_kind == 'whole68' else 1,
            'attachment_sha256': sha256(attachment.read_bytes()).hexdigest(),
            'output_sha256': sha256(payload).hexdigest(),
            'profile_sha256': result['form_record']['profile_sha256'], 'check': check},
            ensure_ascii=False, indent=2), encoding='utf-8')
