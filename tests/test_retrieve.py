import pytest

from agent.retrieve import chunk_documents, find_conflicts, retrieve, search_chunks


def document(filename="실적.xlsx", text="매출 120만원을 달성함", page=None, sheet="실적"):
    return {"파일명": filename, "본문": text, "표 목록": [], "페이지/시트 정보": [
        {"본문": text, "표 목록": [], "페이지": page, "시트": sheet, "위치": "A1:A2"}
    ]}


class FakeEmbedder:
    def embed(self, texts):
        return [[float("매출" in text), float("비용" in text), float("휴가" in text)] for text in texts]


def test_hybrid_retrieval_preserves_sheet_and_filename():
    sources = retrieve("매출", [document(), document("인사.pdf", "휴가 승인함", page=2, sheet=None)], client=FakeEmbedder())
    assert len(sources) == 1
    assert sources[0]["filename"] == "실적.xlsx"
    assert sources[0]["sheet"] == "실적"
    assert sources[0]["source_id"].startswith("S")
    assert "120" in sources[0]["text"]


def test_pdf_page_and_docx_unknown_page_are_honest():
    assert chunk_documents([document("실적.pdf", page=3, sheet=None)])[0]["page"] == 3
    source = chunk_documents([document("실적.docx", sheet=None)])[0]
    assert source["page"] is None and source["sheet"] is None
    assert source["location"]


def test_request_isolation_and_stable_ids():
    first = chunk_documents([document()])
    assert first == chunk_documents([document()])
    assert first[0]["source_id"] != chunk_documents([document(text="매출 130만원을 달성함")])[0]["source_id"]
    assert retrieve("매출", [], client=FakeEmbedder()) == []


def test_same_filename_and_text_in_different_sheets_stay_separate():
    chunks = chunk_documents([document(sheet="팀A"), document(sheet="팀B")])
    assert len(chunks) == 2
    assert chunks[0]["source_id"] != chunks[1]["source_id"]
    assert {item["sheet"] for item in chunks} == {"팀A", "팀B"}


def test_table_header_and_numeric_cell_stay_connected():
    doc = document(text="구분\n매출\n비용\n이번 주\n120만원\n50만원", sheet="실적")
    doc["표 목록"] = [{"행": [["구분", "매출", "비용"], ["이번 주", "120만원", "50만원"]], "위치": "실적 표", "시트": "실적", "페이지": None}]
    chunks = chunk_documents([doc])
    assert any("매출 120만원" in chunk["text"] and "비용 50만원" in chunk["text"] and "행 2" in chunk["location"] for chunk in chunks)


def test_embedding_batches_keep_result_order():
    chunks = [{"source_id": f"S{index}", "text": "매출", "filename": "자료.pdf", "location": f"항목{index}"} for index in range(260)]
    batch_sizes = []

    def embedding(texts):
        batch_sizes.append(len(texts))
        return [[1.0, 0.0] for _ in texts]

    assert len(search_chunks("매출", chunks, embedding, top_k=260)) == 260
    assert batch_sizes == [128, 128, 5]


def test_conflicting_values_keep_both_sources_but_separate_periods():
    sources = [{"source_id": "S1", "text": "매출 120만원임"}, {"source_id": "S2", "text": "매출 130만원임"}]
    assert find_conflicts(sources)[0]["source_ids"] == ["S1", "S2"]
    periods = [{"source_id": "S1", "text": "상반기 매출 120만원임"}, {"source_id": "S2", "text": "하반기 매출 130만원임"}]
    assert find_conflicts(periods) == []


def test_long_paragraphs_and_tables_are_located():
    doc = document(text="가" * 130)
    doc["페이지/시트 정보"][0]["표 목록"] = [[["매출", "120만원"]]]
    chunks = chunk_documents([doc], max_chars=50)
    assert len(chunks) == 4
    assert all(len(item["text"]) <= 50 for item in chunks)
    assert all(item["location"] for item in chunks)


def test_invalid_embeddings_and_search_inputs():
    chunks = chunk_documents([document()])
    with pytest.raises(ValueError, match="개수"):
        search_chunks("매출", chunks, lambda texts: [])
    with pytest.raises(ValueError, match="차원"):
        search_chunks("매출", chunks, lambda texts: [[1.0], [1.0, 2.0]])
    with pytest.raises(ValueError, match="유효"):
        search_chunks("매출", chunks, lambda texts: [[float("nan")], [1.0]])
    with pytest.raises(ValueError, match="검색어"):
        search_chunks("", chunks, FakeEmbedder().embed)


def test_paths_use_m1_parser(monkeypatch, tmp_path):
    import parsers
    from hashlib import sha256
    from openpyxl import Workbook

    attachment = tmp_path / '첨부.xlsx'
    Workbook().save(attachment)
    parsed_paths = []

    def parse(path):
        parsed_paths.append(path)
        return document(path.name)

    monkeypatch.setattr(parsers, "parse_file", parse)
    source = retrieve("매출", paths=[attachment], client=FakeEmbedder())[0]
    assert parsed_paths == [attachment]
    assert source["filename"] == "첨부.xlsx"
    assert source['document_sha256'] == sha256(attachment.read_bytes()).hexdigest()


@pytest.mark.parametrize(('first', 'second'), [
    ('A사 매출 120만원임', 'B사 매출 130만원임'),
    ('회사: A사 매출 120만원임', '회사: B사 매출 130만원임'),
    ('영업팀 매출 120만원임', '개발팀 매출 130만원임'),
    ('부서: 영업 매출 120만원임', '부서: 개발 매출 130만원임'),
    ('회사: A사 부서: 영업팀 매출 120만원임', '회사: B사 부서: 영업팀 매출 130만원임'),
    ('A사 목표 매출 120만원임', 'A사 실적 매출 130만원임'),
])
def test_explicit_entities_and_different_numeric_roles_are_not_conflicts(first, second):
    assert find_conflicts([{'source_id': 'S1', 'text': first}, {'source_id': 'S2', 'text': second}]) == []


@pytest.mark.parametrize(('first', 'second'), [
    ('A사 매출 120만원임', 'A사 매출 130만원임'),
    ('회사: A사 매출 120만원임', 'A사 매출 130만원임'),
    ('영업팀 매출 120만원임', '부서: 영업팀 매출 130만원임'),
    ('A사 실적 매출 120만원임', 'A사 실제 매출 130만원임'),
])
def test_same_explicit_entity_and_compatible_role_still_raise_conflict(first, second):
    conflicts = find_conflicts([{'source_id': 'S1', 'text': first}, {'source_id': 'S2', 'text': second}])
    assert conflicts and conflicts[0]['source_ids'] == ['S1', 'S2']


def test_unknown_entity_is_conservative_and_filename_does_not_manufacture_company():
    sources = [{'source_id': 'S1', 'filename': 'A사.pdf', 'text': 'A사 매출 120만원임'},
               {'source_id': 'S2', 'filename': 'B사.pdf', 'text': '매출 130만원임'}]
    assert find_conflicts(sources)[0]['source_ids'] == ['S1', 'S2']


def test_different_companys_comparison_inside_one_source_does_not_conflict():
    assert find_conflicts([{'source_id': 'S1', 'text': 'A사 매출 120만원, B사 매출 130만원임'}]) == []


def test_same_entity_different_period_does_not_conflict():
    sources = [{'source_id': 'S1', 'text': 'A사 상반기 실적 매출 120만원임'},
               {'source_id': 'S2', 'text': 'A사 하반기 실제 매출 130만원임'}]
    assert find_conflicts(sources) == []
