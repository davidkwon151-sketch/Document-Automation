from copy import deepcopy
from pathlib import Path

import pytest

from agent.pipeline import build_downloads, run_pipeline
from evals.run import MockEvaluationClient

ROOT = Path(__file__).resolve().parents[1]


def case():
    return {"mock_brief": {"목적": "매출", "보고 대상": "팀장", "보고서 유형": "결과보고서", "마감": "2026-10-06", "분량": "1쪽", "부족한 정보": [], "질문": []}}


def docs():
    text = "매출 120만원을 달성함"
    return [{"파일명": "실적.xlsx", "본문": text, "표 목록": [], "페이지/시트 정보": [{"본문": text, "위치": "실적!A1", "페이지": None, "시트": "실적", "표 목록": []}]}]


def ready_result():
    return run_pipeline("매출 결과보고서를 작성해줘", documents=docs(), client=MockEvaluationClient(case()))


def test_full_pipeline_with_both_template_exports():
    result = ready_result()
    assert result["status"] == "ready"
    assert len(result["boss_review"]["questions"]) == 3
    assert result["sources"][0]["filename"] == "실적.xlsx"
    outputs = build_downloads(result, confirmed=True)
    assert set(outputs) == {"docx", "hwpx"}
    assert all(data.startswith(b"PK") for data in outputs.values())


def test_document_cache_reuses_original_bytes_but_invalidates_changed_file(tmp_path, monkeypatch):
    import agent.pipeline as pipeline
    path = tmp_path / '실적.txt'
    path.write_text('매출 120만원을 달성함', encoding='utf-8')
    calls = []
    original = pipeline.load_documents
    def load(paths, **kwargs):
        calls.append(path.read_bytes())
        return original(paths, **kwargs)
    monkeypatch.setattr(pipeline, 'load_documents', load)
    cache = {}
    client = MockEvaluationClient(case())
    first = run_pipeline('매출 결과보고서', [path], client=client, document_cache=cache)
    second = run_pipeline('다시 매출 결과보고서', [path], client=client, document_cache=cache)
    assert len(calls) == 1 and first['draft'] == second['draft']
    first['sources'][0]['text'] = '외부 변경'
    assert next(iter(cache.values()))[0]['본문'] == '매출 120만원을 달성함'
    path.write_text('매출 130만원을 달성함', encoding='utf-8')
    third = run_pipeline('매출 결과보고서', [path], client=client, document_cache=cache)
    assert len(calls) == 2 and '130만원' in third['draft']['본문']


def test_missing_information_stops_before_retrieval():
    incomplete = case()
    incomplete["mock_brief"].update({"보고 대상": "", "부족한 정보": ["보고 대상"], "질문": ["누구에게 보고하나요?"]})
    result = run_pipeline("보고서 작성", documents=[], client=MockEvaluationClient(incomplete))
    assert result["status"] == "needs_information" and len(result["questions"]) <= 2
    assert "draft" not in result


def test_no_evidence_cannot_become_ready_or_downloadable():
    result = run_pipeline("보고서 작성", documents=[], client=MockEvaluationClient(case()))
    assert result["status"] == "needs_evidence" and "draft" not in result
    forged = ready_result()
    forged["sources"] = []
    with pytest.raises(ValueError):
        build_downloads(forged, confirmed=True)


def test_confirmation_and_invalid_manual_edits_block_download():
    result = ready_result()
    with pytest.raises(ValueError, match="확인"):
        build_downloads(result)
    altered = deepcopy(result)
    altered["draft"]["본문"] = "□ 신규 매출 999만원임"
    with pytest.raises(ValueError, match="오류"):
        build_downloads(altered, confirmed=True)


def test_download_second_check_rejects_damaged_saved_file(tmp_path, monkeypatch):
    import templates
    from zipfile import ZipFile
    fill = templates.fill_compatible_template
    def damaged(template, values, output, **kwargs):
        path = fill(template, values, output, **kwargs)
        with ZipFile(path) as archive:
            parts = [(item, archive.read(item.filename)) for item in archive.infolist() if item.filename != 'word/styles.xml']
        with ZipFile(path, 'w') as archive:
            for item, data in parts:
                archive.writestr(item, data)
        return path
    monkeypatch.setattr(templates, 'fill_compatible_template', damaged)
    result = ready_result()
    with pytest.raises(ValueError, match='출력 검증 실패'):
        build_downloads(result, confirmed=True, template_paths={'docx': ROOT / 'templates/result_report.docx'})
    assert 'output_verification' not in result


def test_only_selected_template_format_is_exported():
    outputs = build_downloads(ready_result(), confirmed=True, template_paths={"docx": ROOT / "templates" / "weekly_report.docx"})
    assert set(outputs) == {"docx"}
    with pytest.raises(ValueError, match="형식"):
        build_downloads(ready_result(), confirmed=True, template_paths={"pdf": ROOT / "templates" / "result_report.docx"})


def test_pptx_pipeline_export_is_reopened_and_independently_checked(tmp_path):
    from parsers import parse_file
    result = ready_result()
    outputs = build_downloads(result, confirmed=True,
                              template_paths={'pptx': ROOT / 'samples/sample_company_form.pptx'})
    assert set(outputs) == {'pptx'}
    assert result['output_verification']['pptx']['status'] == 'passed'
    path = tmp_path / '보고서.pptx'
    path.write_bytes(outputs['pptx'])
    text = parse_file(path)['본문']
    assert result['draft']['제목'] in text and '120만원' in text


def test_source_conflict_and_unreviewed_safe_correction_block_export():
    documents = docs() + docs()
    documents[1]["파일명"] = "다른자료.xlsx"
    documents[1]["본문"] = "매출 130만원을 달성함"
    documents[1]["페이지/시트 정보"][0]["본문"] = documents[1]["본문"]
    result = run_pipeline("매출 결과보고서", documents=documents, client=MockEvaluationClient(case()))
    assert result["status"] == "needs_revision" and result["conflicts"]
    with pytest.raises(ValueError, match="상충"):
        build_downloads(result, confirmed=True)
    modified = ready_result()
    modified["draft"]["본문"] = modified["draft"]["본문"].replace("120만원", "999만원")
    with pytest.raises(ValueError, match="다시 검수"):
        build_downloads(modified, confirmed=True)


def test_pipeline_loads_real_prompts_with_mock_api_transport():
    import json
    import httpx
    from llm.client import LLMClient

    fake = MockEvaluationClient(case())
    operations = []

    def handler(request):
        body = json.loads(request.content)
        operations.append(request.url.path)
        if request.url.path.endswith("embeddings"):
            vectors = fake.embed(body["input"])
            return httpx.Response(200, json={"object": "list", "model": "fake-embedding", "data": [{"object": "embedding", "index": index, "embedding": vector} for index, vector in enumerate(vectors)], "usage": {"prompt_tokens": 5, "total_tokens": 5}})
        instruction, _, data = body["input"].partition("\n")
        assert instruction == "Return one JSON object."
        payload = json.loads(data)
        name = "brief" if "instruction" in payload else "boss_review" if "draft" in payload and "brief" in payload else "draft"
        answer = fake.generate_json(name, payload)
        return httpx.Response(200, json={"id": "resp_test", "object": "response", "created_at": 1, "model": "fake-model", "status": "completed", "output": [{"id": "msg_test", "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(answer, ensure_ascii=False), "annotations": []}]}], "usage": {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}})

    client = LLMClient(api_key="test-only", transport=httpx.MockTransport(handler))
    result = run_pipeline("매출 결과보고서", documents=docs(), client=client)
    assert result["status"] == "ready"
    assert len(operations) == 4
