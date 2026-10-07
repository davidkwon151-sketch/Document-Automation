import base64
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app import hwp_worker_api as worker

TOKEN = "s" * 40
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(worker.hancom, "hwp_capability", lambda: {
        "available": True, "module_active": True, "ownership_verified": True,
        "private_path": "secret"})
    return TestClient(worker.create_app(TOKEN), headers={"Authorization": "Bearer " + TOKEN})


def payload(name="양식.hwp", content=b"synthetic original"):
    return {"name": name, "base64": base64.b64encode(content).decode()}


def test_fail_closed_and_authorization_before_native_calls(monkeypatch):
    monkeypatch.setattr(worker.hancom, "hwp_capability", lambda: pytest.fail("unauthenticated native call"))
    for token in ("", "short"):
        assert TestClient(worker.create_app(token)).get("/health").status_code == 503
    client = TestClient(worker.create_app(TOKEN))
    assert client.get("/health").status_code == 401
    assert client.post("/convert", json=payload(), headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.get("/docs", headers={"Authorization": "Bearer " + TOKEN}).status_code == 404


def test_health_never_exposes_native_paths(client):
    response = client.get("/health")
    assert response.json() == {"status": "ready", "available": True, "module_active": True, "ownership_verified": True}
    assert response.headers["cache-control"] == "no-store"


def test_converts_isolated_copy_hashes_output_and_removes_files(client, monkeypatch):
    calls = []
    def convert(source, output_dir):
        calls.append((source, output_dir))
        assert source.name == "upload.hwp" and source.read_bytes() == b"synthetic original"
        output_dir.mkdir()
        output = output_dir / "upload.hwpx"
        output.write_bytes((ROOT / "samples/sample_company_form.hwpx").read_bytes())
        return output
    monkeypatch.setattr(worker.hancom, "convert_hwp", convert)
    first = client.post("/convert", json=payload())
    second = client.post("/convert", json=payload())
    assert first.status_code == second.status_code == 200
    result = first.json()
    assert result["name"] == "양식.hwpx"
    assert result["source_sha256"] == hashlib.sha256(b"synthetic original").hexdigest()
    assert result["output_sha256"] == hashlib.sha256(base64.b64decode(result["base64"])).hexdigest()
    assert calls[0][0] != calls[1][0]
    assert all(not source.parent.exists() for source, _ in calls)


@pytest.mark.parametrize("name", ["../form.hwp", r"C:\form.hwp", r"a\form.hwp", "form.hwpx", "a.hwp ", "a\x00.hwp", "x" * 181 + ".hwp"])
def test_untrusted_paths_rejected(client, monkeypatch, name):
    monkeypatch.setattr(worker.hancom, "convert_hwp", lambda *args: pytest.fail("invalid native call"))
    assert client.post("/convert", json=payload(name)).status_code == 400


def test_body_and_decoded_size_limits(client, monkeypatch):
    monkeypatch.setattr(worker, "MAX_BODY_BYTES", 160)
    monkeypatch.setattr(worker, "MAX_UPLOAD_BYTES", 20)
    assert client.post("/convert", content=b"x" * 161).status_code == 413
    assert client.post("/convert", json=payload(content=b"x" * 21)).status_code == 413
    assert client.post("/convert", json=payload(content=b"")).status_code == 413
    assert client.post("/convert", json={"name": "a.hwp", "base64": "!"}).status_code == 400
    assert client.post("/convert", json={**payload(), "path": "hidden"}).status_code == 400


def test_busy_health_and_conversion_share_lock(client):
    with worker.NATIVE_LOCK:
        assert client.get("/health").json() == {"status": "busy", "available": False}
        assert client.post("/convert", json=payload()).status_code == 429


@pytest.mark.parametrize("failure", [RuntimeError("private path and token"), TimeoutError("secret")])
def test_errors_sanitized_and_gate_released(client, monkeypatch, failure):
    def convert(*args):
        raise failure
    monkeypatch.setattr(worker.hancom, "convert_hwp", convert)
    response = client.post("/convert", json=payload())
    assert response.status_code == (504 if isinstance(failure, TimeoutError) else 422)
    assert "secret" not in response.text and "private" not in response.text
    assert worker.NATIVE_LOCK.acquire(blocking=False)
    worker.NATIVE_LOCK.release()


def test_source_mutation_and_invalid_result_are_blocked(client, monkeypatch):
    def convert(source, output_dir):
        source.write_bytes(b"changed")
        output_dir.mkdir()
        output = output_dir / "upload.hwpx"
        output.write_bytes(b"not hwpx")
        return output
    monkeypatch.setattr(worker.hancom, "convert_hwp", convert)
    assert client.post("/convert", json=payload()).status_code == 422
