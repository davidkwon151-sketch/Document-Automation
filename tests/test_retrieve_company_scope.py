"""Company identity must survive retrieval and invalidate stale meaning checks."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from agent.grounding import evidence_fingerprint, inspect_grounding
from agent.pipeline import review_result
from agent.retrieve import chunk_documents, retrieve


def document():
    text = 'Alpha 25 mg tablets'
    return {'파일명': 'product.pdf', '본문': text, '표 목록': [],
            'company_name': 'Example MAH Ltd.', 'company_role': 'marketing_authorisation_holder',
            'product_name': 'Alpha', 'product_variant': text, 'jurisdiction': 'EU',
            'document_sha256': 'a' * 64, 'source_url': 'https://example.test/product.pdf',
            '페이지/시트 정보': [{'본문': text, '페이지': 1, '시트': None,
                              '위치': 'product name', '표 목록': [], 'regulatory_role': 'product_name'}]}


def test_company_role_survives_production_hybrid_retrieval():
    client = Mock()
    client.embed.side_effect = lambda texts: [[1.0] for _ in texts]
    result = retrieve('Alpha', [document()], client=client)
    assert len(result) == 1
    source = result[0]
    assert source['company_name'] == 'Example MAH Ltd.'
    assert source['company_role'] == 'marketing_authorisation_holder'
    assert source['regulatory_role'] == 'product_name'
    assert source['product_variant'] == 'Alpha 25 mg tablets'
    assert source['document_sha256'] == 'a' * 64 and source['page'] == 1


def test_mixed_company_blocks_preserve_their_explicit_role_without_inference():
    doc = document()
    block = deepcopy(doc['페이지/시트 정보'][0])
    block.update(본문='Alpha manufacturing location', company_name='Example Manufacturer Ltd.',
                 company_role='manufacturer', regulatory_role='manufacturing_site', 페이지=2)
    doc['페이지/시트 정보'].append(block)
    sources = chunk_documents([doc])
    assert [(s['company_name'], s['company_role'], s['page']) for s in sources] == [
        ('Example MAH Ltd.', 'marketing_authorisation_holder', 1),
        ('Example Manufacturer Ltd.', 'manufacturer', 2)]
    unspecified = document()
    del unspecified['company_name'], unspecified['company_role']
    assert 'company_name' not in chunk_documents([unspecified])[0]


@pytest.mark.parametrize('mutation', [{'company_name': 'Other Company Ltd.'},
                                    {'company_role': 'manufacturer'}])
def test_company_change_blocks_reuse_of_old_semantic_review(mutation):
    sources = chunk_documents([document()])
    source_id = sources[0]['source_id']
    draft = {'제목': '검토', '요약': f'□ Alpha 자료를 검토함 [{source_id}]',
             '본문': f'○ Alpha 자료를 검토함 [{source_id}]'}
    client = Mock()
    client.generate_json.return_value = {'claims': [
        {'field': field, 'line': 1, 'status': 'supported',
         'evidence': [{'source_id': source_id, 'quote': sources[0]['text']}]} for field in ('요약', '본문')]}
    proof = inspect_grounding(draft, sources, client)
    assert not proof['blocking']
    changed = deepcopy(sources)
    changed[0].update(mutation)
    assert evidence_fingerprint(draft, changed) != proof['fingerprint']
    result = {'draft': draft, 'sources': changed, 'semantic_required': True, 'grounding': proof}
    check = review_result(result)
    assert check['blocking']
    assert any(issue['code'] == 'semantic_stale' for issue in check['warnings'])


@pytest.mark.parametrize(('key', 'value'), [('company_name', ['Company']),
                                          ('company_role', True), ('company_name', 'x' * 2001)])
def test_malformed_company_scope_is_rejected_before_search(key, value):
    doc = document()
    doc[key] = value
    with pytest.raises(ValueError, match='제한된 문자열'):
        chunk_documents([doc])
