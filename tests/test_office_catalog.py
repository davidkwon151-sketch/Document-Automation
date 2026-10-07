from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json

import pytest

from templates import catalog, office


def test_sources_are_distinct_dated_and_honestly_classified():
    entries = office.list_office_templates()
    assert len(entries) == len({e["id"] for e in entries}) == len({e["sha256"] for e in entries}) == 11
    assert sum(e["resource_kind"] == "blank_form" for e in entries) == 10
    assert set().union(*(set(e["workflows"]) for e in entries)) == set(office.OFFICE_WORKFLOWS)
    for entry in entries:
        assert entry["download_status"] == "verified" and entry["checked_at"] and entry["version_label"]
        assert entry["company_internal"] is entry["fill_verified"] is entry["full_submission_ready"] is entry["legal_compliance_certified"] is entry["mandatory_requirements_verified"] is False
        assert entry["license"]["redistribution_permitted"] is False
    report = next(e for e in entries if e["id"] == "office_posco_report_2025")
    assert (report["resource_kind"], report["use_classification"]) == ("layout_reference", "reference_report")
    bosch = next(e for e in entries if e["id"] == "office_bosch_change_proposal")
    assert bosch["reused_from_catalog"] == "international" and bosch["origin_id"] == "international_bosch_change_proposal"
    assert sum(bool(e.get("reused_from_catalog")) for e in entries) == 1
    assert {e["id"] for e in entries if e.get("profile_id")} == {"office_treasury_voucher", "office_medical_balance", "office_bosch_change_proposal"}


def test_workflow_filters_return_independent_copies():
    result = office.list_office_templates("financial_report")
    assert result and all("financial_report" in e["workflows"] for e in result)
    result[0]["title"] = "mutated"
    assert office.list_office_templates("financial_report")[0]["title"] != "mutated"
    with pytest.raises(catalog.CatalogError):
        office.list_office_templates("unknown")


@pytest.mark.parametrize("change", [
    {"id": "unknown"}, {"source_url": "https://evil.example/form.pdf"}, {"filename": "../escape.pdf"},
    {"sha256": "0" * 64}, {"format": "PDF"}, {"allowed_hosts": ["evil.example"]},
    {"session_page": "https://evil.example/anonymous"},
])
def test_caller_cannot_change_pinned_source(change, tmp_path):
    with pytest.raises(catalog.CatalogError):
        office.download_office_template({**office.list_office_templates()[0], **change}, tmp_path)


def mock_entry(payload):
    return dict(id="mock", source_url="https://www.hanbat.ac.kr/cmm/fms/FileDown.do?atchFileId=MOCK&fileSn=0",
                filename="mock.pdf", format="PDF", allowed_hosts=["www.hanbat.ac.kr"],
                sha256=sha256(payload).hexdigest(), download_status="verified")


def test_mock_download_persists_provenance(monkeypatch, tmp_path):
    payload = b"%PDF-1.7\nmock official form"
    entry = mock_entry(payload)
    monkeypatch.setattr(office, "list_office_templates", lambda workflow=None: [deepcopy(entry)])
    class Response(BytesIO):
        headers = {}
        def geturl(self): return entry["source_url"]
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == entry["source_url"] and timeout == 30
            return Response(payload)
    monkeypatch.setattr(catalog, "build_opener", lambda *args: Opener())
    output = office.download_office_template(entry, tmp_path)
    assert output.read_bytes() == payload
    assert json.loads(output.with_suffix(".pdf.source.json").read_text(encoding="utf-8"))["sha256"] == entry["sha256"]


def test_cache_preserves_changed_original_and_never_calls_network(monkeypatch, tmp_path):
    payload = b"%PDF-1.7\ncache"; entry = mock_entry(payload)
    monkeypatch.setattr(office, "list_office_templates", lambda workflow=None: [entry])
    monkeypatch.setattr(office, "_download_verified_entry", lambda *a: pytest.fail("Cache must not download"))
    path = tmp_path / entry["filename"]; path.write_bytes(payload)
    assert office.download_office_template(entry, tmp_path) == path
    path.write_bytes(b"changed")
    with pytest.raises(catalog.CatalogError, match="해시"):
        office.download_office_template(entry, tmp_path)
    assert path.read_bytes() == b"changed"


@pytest.mark.parametrize("change", [
    {"fill_verified": True}, {"company_internal": True}, {"application_status": "open"},
    {"legal_compliance_certified": True}, {"mandatory_requirements_verified": True},
    {"full_submission_ready": True}, {"source_url": "http://www.hanbat.ac.kr/file"},
    {"allowed_hosts": ["www.hanbat.ac.kr.evil.example"]}, {"source_kind": "published_report"},
    {"workflows": ["fake"]}, {"sha256": None}, {"size_bytes": 0},
    {"session_page": "https://www.poscointl.com/login"},
])
def test_unverified_claims_and_bad_provenance_are_rejected(change, monkeypatch, tmp_path):
    data = office.load_office_catalog(); data["documents"][0].update(change)
    path = tmp_path / "catalog.json"; path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(office, "CATALOG_PATH", path)
    with pytest.raises(catalog.CatalogError):
        office.load_office_catalog()
