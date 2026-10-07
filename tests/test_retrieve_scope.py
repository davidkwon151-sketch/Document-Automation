import pytest

from agent.retrieve import chunk_documents


def test_public_product_and_original_location_metadata_survive_chunking():
    document = {'파일명': 'original.pdf', '본문': 'Each vial contains 150 mg.', '표 목록': [],
                'product_name': 'Herzuma', 'source_url': 'https://www.ema.europa.eu/official.pdf',
                'document_sha256': 'a' * 64, 'jurisdiction': 'EU',
                '페이지/시트 정보': [{'본문': 'Each vial contains 150 mg.', '페이지': 2,
                    'product_variant': 'Herzuma 150 mg vial', '위치': '제형 설명'}]}
    source = chunk_documents([document])[0]
    assert source['product_name'] == 'Herzuma'
    assert source['product_variant'] == 'Herzuma 150 mg vial'
    assert source['document_sha256'] == 'a' * 64
    assert source['source_url'] == document['source_url']
    assert source['page'] == 2 and source['location'].startswith('제형 설명')
    assert source['text'] == document['본문']


def test_product_or_jurisdiction_change_invalidates_previous_semantic_proof():
    from agent.grounding import evidence_fingerprint
    draft = {'본문': '150 mg [S1]'}
    source = {'source_id': 'S1', 'text': '150 mg', 'product_name': 'Herzuma', 'jurisdiction': 'EU'}
    original = evidence_fingerprint(draft, [source])
    for changed in ({**source, 'product_name': 'Another product'}, {**source, 'jurisdiction': 'KR'}):
        assert original != evidence_fingerprint(draft, [changed])


def test_scope_annotations_do_not_accept_objects_or_unbounded_values():
    document = {'파일명': 'source.txt', '본문': '자료', '표 목록': [], '페이지/시트 정보': [], 'product_name': {'execute': 'x'}}
    with pytest.raises(ValueError, match='문자열'):
        chunk_documents([document])
