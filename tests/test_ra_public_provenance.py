"""Fact-level evidence bindings and production parser coverage, offline."""

from copy import deepcopy
from hashlib import sha256

import pytest
from reportlab.pdfgen import canvas

from evals import ra_public


@pytest.fixture
def annotated(tmp_path):
    path = tmp_path / 'product.pdf'
    pdf = canvas.Canvas(str(path))
    for y, text in [(750, 'MARKETING AUTHORISATION HOLDER'), (730, 'Example MAH Ltd.'),
                    (710, 'Alpha 25 mg tablets'), (690, 'Shelf life: 3 years.')]:
        pdf.drawString(30, y, text)
    pdf.save()
    record = {'id': 'snapshot', 'path': path.name, 'filename': path.name,
              'sha256': sha256(path.read_bytes()).hexdigest(), 'size_bytes': path.stat().st_size,
              'page_count': 1, 'company': {'name': 'Example MAH Ltd.', 'exact_quote': 'Example MAH Ltd.',
                                         'page': 1, 'role': 'marketing_authorisation_holder'},
              'product_name': 'Alpha', 'product_variant': 'Alpha 25 mg tablets',
              'source_url': 'https://example.test/product.pdf', 'jurisdiction': 'EU',
              'facts': [{'field_key': '제품명', 'value': 'Alpha 25 mg tablets',
                         'exact_quote': 'Alpha 25 mg tablets', 'page': 1, 'role': 'product_name'},
                        {'field_key': '저장방법 및 유효기간', 'value': 'Shelf life: 3 years.',
                         'exact_quote': 'Shelf life: 3 years.', 'page': 1, 'role': 'shelf_life'}]}
    facts, sources, document = ra_public.read_source(record, tmp_path)
    return tmp_path, record, facts, sources, document


def test_known_id_for_a_different_fact_is_not_valid_evidence(annotated):
    _, _, facts, sources, _ = annotated
    draft = {fact['field_key']: ra_public.cited(fact['value'], fact['source_id']) for fact in facts}
    good = ra_public.compare_values(draft, facts, sources)
    assert good['cited_field_count'] == good['full_value_preserved_count'] == 2
    draft[facts[1]['field_key']] = ra_public.cited(facts[1]['value'], facts[0]['source_id'])
    wrong = ra_public.compare_values(draft, facts, sources)
    assert wrong['full_value_preserved_count'] == 2
    assert wrong['cited_field_count'] == 1
    assert ra_public.compare_values(draft, facts, {s['source_id'] for s in sources})['cited_field_count'] == 1


def test_production_chunk_ids_are_accepted_only_with_original_fact_scope(annotated):
    from agent.retrieve import chunk_documents

    _, _, facts, _, document = annotated
    chunks = chunk_documents([document])
    assert chunks[0]['source_id'] != facts[0]['source_id']
    draft = {fact['field_key']: ra_public.cited(fact['value'], chunk['source_id'])
             for fact, chunk in zip(facts, chunks)}
    assert ra_public.compare_values(draft, facts, chunks)['cited_field_count'] == 2


@pytest.mark.parametrize('change', [{'page': 2}, {'document_sha256': '0' * 64},
                                   {'product_variant': 'Beta 25 mg tablets'}, {'jurisdiction': 'KR'},
                                   {'regulatory_role': 'manufacturer'}, {'filename': 'other.pdf'},
                                   {'source_url': 'https://different.test/product.pdf'},
                                   {'text': 'Different fact: 3 years.'}])
def test_a_correct_id_cannot_hide_forged_provenance(annotated, change):
    _, _, facts, sources, _ = annotated
    altered = deepcopy(sources)
    altered[1].update(change)
    draft = {fact['field_key']: ra_public.cited(fact['value'], fact['source_id']) for fact in facts}
    assert ra_public.compare_values(draft, facts, altered)['cited_field_count'] == 1


def test_parser_checks_the_complete_document_and_every_quote(annotated):
    root, record, _, _, _ = annotated
    result = ra_public.validate_source_parser(record, root)
    assert result['passed'] and result['original_unchanged']
    assert result['page_count'] == 1 and result['fact_count'] == 2


@pytest.mark.parametrize('change', ['size', 'company_name', 'wrong_role'])
def test_integrity_includes_size_name_and_role_evidence(annotated, change):
    root, original, _, _, _ = annotated
    record = deepcopy(original)
    if change == 'size':
        record['size_bytes'] += 1
    elif change == 'company_name':
        record['company']['name'] = 'Wrong company'
    else:
        record['company']['role'] = 'manufacturer'
        record['company']['role_exact_quote'] = 'Example MAH Ltd.'
    with pytest.raises(ValueError):
        ra_public.read_source(record, root)


def test_publisher_is_not_a_manufacturer_and_requires_the_observed_official_domain(annotated):
    root, record, _, _, _ = annotated
    record['company'].update(role='document_publisher', official_domain='example.test')
    record['source_page'] = 'https://www.example.test/products'
    record['source_url'] = 'https://files.example.test/product.pdf'
    assert ra_public.read_source(record, root)[0]
    record['source_url'] = 'https://example.test.evil.test/product.pdf'
    with pytest.raises(ValueError, match='도메인'):
        ra_public.read_source(record, root)


@pytest.mark.parametrize('password,allow_extraction', [('', True), ('', False), ('required', True)])
def test_encrypted_sources_require_normal_read_access_and_extraction_permission(annotated, password, allow_extraction):
    from pypdf import PdfWriter
    from pypdf.constants import UserAccessPermissions
    from pypdf.errors import FileNotDecryptedError

    root, record, _, _, _ = annotated
    path = root / record['path']
    writer = PdfWriter(clone_from=path)
    permissions = UserAccessPermissions.PRINT
    if allow_extraction:
        permissions |= UserAccessPermissions.EXTRACT
    writer.encrypt(password, owner_password='synthetic-owner', permissions_flag=permissions)
    writer.write(path)
    record.update(sha256=sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size)
    original = path.read_bytes()
    if not password and allow_extraction:
        assert ra_public.read_source(record, root)[0]
    elif password:
        with pytest.raises(FileNotDecryptedError):
            ra_public.read_source(record, root)
    else:
        with pytest.raises(ValueError, match='텍스트 추출 허용'):
            ra_public.read_source(record, root)
    assert path.read_bytes() == original


def test_duplicate_source_ids_cannot_silently_replace_the_evidence(annotated):
    _, _, facts, sources, _ = annotated
    duplicate = {**sources[0], 'text': 'Another company fact'}
    with pytest.raises(ValueError, match='중복'):
        ra_public.compare_values({}, facts, [*sources, duplicate])


@pytest.mark.parametrize('corruption', ['missing_page', 'missing_quote', 'modified_original'])
def test_production_parser_failures_do_not_use_independent_pdf_text_as_fallback(annotated, monkeypatch, corruption):
    import parsers

    root, record, _, _, _ = annotated
    real_parse = parsers.parse_file

    def corrupt(path):
        result = real_parse(path)
        if corruption == 'missing_page':
            result['페이지/시트 정보'] = []
        elif corruption == 'missing_quote':
            result['페이지/시트 정보'][0]['본문'] = 'Alpha 25 mg tablets'
        else:
            path.write_bytes(path.read_bytes() + b'\nchanged')
        return result

    monkeypatch.setattr(parsers, 'parse_file', corrupt)
    with pytest.raises(ValueError):
        ra_public.validate_source_parser(record, root)
