from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
from urllib.request import Request
from zipfile import ZipFile

import pytest

from templates import catalog


def test_catalog_has_30_official_asset_ranks_and_honest_statuses():
    data = catalog.load_catalog()
    assert data["basis"]["year"] == 2026
    assert "자산" in data["basis"]["metric"]
    assert data["ranking_source"]["owner"] == "공정거래위원회"
    assert [g["rank"] for g in data["groups"]] == list(range(1, 31))
    assert data["groups"][16]["name"] == "에이치엠엠"
    assert data["groups"][18]["name"] == "셀트리온"
    assert data["groups"][-1]["name"] == "부영"
    assert all(not g["all_affiliates_verified"] and not g["internal_forms_verified"] for g in data["groups"])
    for entry in catalog.list_public_templates():
        assert entry["source_url"].startswith("https://")
        assert entry["owner"] and entry["affiliate"] and entry["checked_at"]
        assert entry["license"]["redistribution_permitted"] is False
        if entry["download_status"] == "verified":
            assert len(entry["sha256"]) == 64 and entry["size_bytes"] > 0
    protected = [e for e in catalog.list_public_templates() if e["download_status"] == "blocked_drm"]
    assert len(protected) == 2
    assert all(e["format"] == "DOCX" and e["compatibility_status"] == "blocked" for e in protected)


def test_list_supports_official_name_alias_rank_and_does_not_mutate():
    entries = catalog.list_public_templates("카카오")
    assert entries == catalog.list_public_templates("16")
    assert {e["format"] for e in entries} == {"PDF", "XLSX"}
    assert catalog.list_public_templates("LG") == catalog.list_public_templates("엘지")
    entries[0]["title"] = "changed"
    assert catalog.list_public_templates("카카오")[0]["title"] != "changed"
    assert catalog.list_public_templates("없는기업") == []


class Response(BytesIO):
    def __init__(self, payload, url="https://official.example/report.pdf", length=None):
        super().__init__(payload)
        self.url = url
        self.headers = {} if length is None else {"Content-Length": str(length)}

    def geturl(self):
        return self.url


@pytest.fixture
def approved(monkeypatch):
    payload = b"%PDF-1.7\nexample report"
    entry = {"id": "official-report", "source_url": "https://official.example/report.pdf",
             "filename": "official_report.pdf", "format": "PDF", "allowed_hosts": ["official.example"],
             "sha256": hashlib.sha256(payload).hexdigest(), "download_status": "verified", "license": {"redistribution_permitted": False}}
    monkeypatch.setattr(catalog, "list_public_templates", lambda group=None: [deepcopy(entry)])
    return entry, payload


def mock_response(monkeypatch, response):
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append((request.full_url, timeout))
            return response

    monkeypatch.setattr(catalog, "build_opener", lambda handler: Opener())
    return requests


def test_download_verifies_exact_source_hash_and_writes_provenance(monkeypatch, approved, tmp_path):
    entry, payload = approved
    requests = mock_response(monkeypatch, Response(payload, length=len(payload)))
    output = catalog.download_public_template(entry, tmp_path)
    assert output.read_bytes() == payload
    provenance = json.loads(output.with_suffix(".pdf.source.json").read_text(encoding="utf-8"))
    assert provenance["sha256"] == entry["sha256"]
    assert provenance["source_url"] == entry["source_url"]
    assert provenance["downloaded_at"]
    assert requests == [(entry["source_url"], 30)]


@pytest.mark.parametrize("mutation", [{"id": "unknown"}, {"source_url": "https://evil.example/file.pdf"}, {"filename": "../escape.pdf"}, {"allowed_hosts": ["evil.example"]}, {"sha256": "0" * 64}])
def test_arbitrary_source_or_metadata_cannot_be_downloaded(approved, tmp_path, mutation):
    entry, _ = approved
    with pytest.raises(catalog.CatalogError):
        catalog.download_public_template({**entry, **mutation}, tmp_path)


@pytest.mark.parametrize("payload", [b"%PDF-1.7\nchanged original", b"<html>access denied</html>"])
def test_download_rejects_changed_original_or_html_and_preserves_existing(monkeypatch, approved, tmp_path, payload):
    entry, _ = approved
    original = tmp_path / entry["filename"]
    original.write_bytes(b"existing original")
    mock_response(monkeypatch, Response(payload))
    with pytest.raises(catalog.CatalogError):
        catalog.download_public_template(entry, tmp_path)
    assert original.read_bytes() == b"existing original"
    assert list(tmp_path.glob(".download-*")) == []


def test_download_size_is_bounded_with_and_without_length(monkeypatch, approved, tmp_path):
    entry, payload = approved
    monkeypatch.setattr(catalog, "MAX_DOWNLOAD_BYTES", 8)
    for length in (None, len(payload)):
        mock_response(monkeypatch, Response(payload, length=length))
        with pytest.raises(catalog.CatalogError, match="크기"):
            catalog.download_public_template(entry, tmp_path)
        assert not (tmp_path / entry["filename"]).exists()
        assert list(tmp_path.glob(".download-*")) == []


def test_redirect_policy_rejects_unregistered_hosts_and_downgrades():
    handler = catalog._OfficialRedirects({"official.example"})
    request = Request("https://official.example/report.pdf")
    for url in ("https://evil.example/report.pdf", "http://official.example/report.pdf", "https://127.0.0.1/report.pdf", "https://user:pass@official.example/report.pdf"):
        with pytest.raises(catalog.CatalogError):
            handler.redirect_request(request, None, 302, "Found", {}, url)
    redirected = handler.redirect_request(request, None, 302, "Found", {}, "https://official.example/updated.pdf")
    assert redirected.full_url.endswith("updated.pdf")


def test_final_response_host_is_checked_even_if_opener_does_not_enforce(monkeypatch, approved, tmp_path):
    entry, payload = approved
    mock_response(monkeypatch, Response(payload, url="https://evil.example/file.pdf"))
    with pytest.raises(catalog.CatalogError, match="호스트"):
        catalog.download_public_template(entry, tmp_path)


@pytest.mark.parametrize("name", ["../file.pdf", "x\\file.pdf", "/file.pdf", "CON.pdf", "a..pdf", "a:stream.pdf"])
def test_windows_unsafe_names_rejected(name):
    with pytest.raises(catalog.CatalogError):
        catalog._safe_filename(name)


def test_blocked_drm_never_claims_normal_docx_compatibility(tmp_path):
    entry = next(e for e in catalog.list_public_templates() if e["download_status"] == "blocked_drm")
    with pytest.raises(catalog.CatalogError, match="검증"):
        catalog.download_public_template(entry, tmp_path)


def test_list_cli_filters_without_network(capsys):
    assert catalog.main(["list", "--group", "16", "--search", "xlsx"]) == 0
    entries = json.loads(capsys.readouterr().out)
    assert len(entries) == 1 and entries[0]["format"] == "XLSX"


@pytest.mark.parametrize("kind", ["html", "wrong_zip", "valid_pptx"])
def test_shared_download_checks_actual_pptx_parts(monkeypatch, tmp_path, kind):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        package.writestr("ppt/presentation.xml" if kind == "valid_pptx" else "word/document.xml", "<document/>")
    payload = b"<html>Access denied</html>" if kind == "html" else buffer.getvalue()
    entry = dict(id="pptx", source_url="https://official.example/form.pptx", filename="form.pptx",
                 format="PPTX", allowed_hosts=["official.example"], sha256=hashlib.sha256(payload).hexdigest(),
                 download_status="verified")
    mock_response(monkeypatch, Response(payload, entry["source_url"]))
    if kind == "valid_pptx":
        assert catalog._download_verified_entry(entry, tmp_path).read_bytes() == payload
    else:
        with pytest.raises(catalog.CatalogError):
            catalog._download_verified_entry(entry, tmp_path)
        assert not (tmp_path / entry["filename"]).exists()
        assert not list(tmp_path.glob(".download-*"))


def test_public_download_can_initialize_only_registered_anonymous_session(monkeypatch, tmp_path):
    payload = b"%PDF-1.7\nsession form"
    entry = dict(id="session", source_url="https://official.example/form.pdf",
                 session_page="https://official.example/notice", filename="session.pdf",
                 format="PDF", allowed_hosts=["official.example"], sha256=hashlib.sha256(payload).hexdigest(),
                 download_status="verified")
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, request.get_header("Referer"), timeout))
            return Response(b"<html>Public notice</html>" if request.full_url == entry["session_page"] else payload, request.full_url)
    monkeypatch.setattr(catalog, "build_opener", lambda *args: Opener())
    assert catalog._download_verified_entry(entry, tmp_path).read_bytes() == payload
    assert calls == [(entry["session_page"], None, 30), (entry["source_url"], entry["session_page"], 30)]
    with pytest.raises(catalog.CatalogError, match="세션"):
        catalog._download_verified_entry({**entry, "session_page": "https://evil.example/notice"}, tmp_path)
    assert len(calls) == 2
