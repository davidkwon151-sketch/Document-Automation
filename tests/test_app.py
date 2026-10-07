import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.storage import LocalStore


@pytest.fixture
def api(tmp_path):
    return TestClient(create_app(LocalStore(tmp_path)))


def test_health(api):
    response = api.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_input_validation_and_missing_records(api):
    assert api.post("/sources", json={"filename": "자료.docx"}).status_code == 422
    assert api.post("/reports", json={"instruction": ""}).status_code == 422
    assert api.get("/sources/missing").status_code == 404
    assert api.get("/reports/missing").status_code == 404


def test_source_and_report_api(api):
    source = {"source_id": "s1", "filename": "매출.docx", "text": "매출 100만원", "location": "문단 1"}
    assert api.post("/sources", json=source).status_code == 201
    assert api.get("/sources/s1").json()["text"] == source["text"]
    report = {"report_id": "r1", "instruction": "결과보고", "template_id": "result", "source_ids": ["s1"]}
    assert api.post("/reports", json=report).status_code == 201
    assert api.get("/reports/r1").json()["source_ids"] == ["s1"]
    report["source_ids"] = ["missing"]
    assert api.post("/reports", json=report).status_code == 422
