from copy import deepcopy
import json

import pytest

from templates import government
from templates.catalog import CatalogError


def test_government_seed_has_verified_diverse_originals():
    catalog = government.load_government_catalog()
    entries = catalog["documents"]
    verified_forms = [e for e in entries if e["download_status"] == "verified" and e["resource_kind"] == "blank_form"]
    assert len(verified_forms) >= 20
    assert {e["format"] for e in verified_forms} == government.FORMATS
    assert len({e["agency"] for e in verified_forms}) >= 6
    assert len({e["category"] for e in verified_forms}) >= 7
    assert len({e["sha256"] for e in verified_forms}) == len(verified_forms)
    assert all(e["legal_compliance_certified"] is False for e in entries)
    assert any(e["resource_kind"] == "layout_reference" for e in entries)
    assert any(e["version_status"] == "historical" for e in entries)


def test_filter_returns_independent_entries():
    filtered = government.list_government_templates("정보공개")
    assert filtered and all(e["category"] == "정보공개" for e in filtered)
    filtered[0]["notes"] = "changed"
    assert government.list_government_templates("정보공개")[0]["notes"] != "changed"
    assert government.list_government_templates("없는 종류") == []


def test_agency_registry_distinguishes_presence_from_form_coverage():
    catalog = government.load_government_catalog()
    agencies = government.list_government_agencies()
    assert len(agencies) >= 600
    assert len(government.list_government_agencies("public_institution")) >= 300
    assert catalog["registry_coverage"]["all_forms_collected"] is False
    assert any(a["forms_status"] == "unknown" and a["fill_verified_count"] == 0 for a in agencies)
    assert all(a["download_verified_count"] <= a["discovered_forms_count"] for a in agencies)


@pytest.mark.parametrize("url", ["https://www.spo.go.kr.evil.test/a", "http://www.spo.go.kr/a", "https://127.0.0.1/a", "https://evil.test/a"])
def test_official_url_boundary(url):
    with pytest.raises(CatalogError):
        government.official_host(url)


def test_downloader_reuses_checked_common_helper(monkeypatch, tmp_path):
    entry = government.list_government_templates()[0]
    calls = []
    def download(approved, directory):
        calls.append((approved, directory))
        return tmp_path / approved["filename"]
    monkeypatch.setattr(government, "_download_verified_entry", download)
    assert government.download_government_template(entry, tmp_path).name == entry["filename"]
    assert calls[0][0]["sha256"] == entry["sha256"]


@pytest.mark.parametrize("mutation", [{"id": "unknown"}, {"source_url": "https://www.spo.go.kr/other"}, {"filename": "../secret"}, {"sha256": "0" * 64}, {"allowed_hosts": ["evil.test"]}])
def test_unregistered_or_mutated_metadata_cannot_download(mutation):
    entry = government.list_government_templates()[0]
    entry.update(mutation)
    with pytest.raises(CatalogError):
        government.download_government_template(entry)


@pytest.mark.parametrize("mutation", [{"legal_compliance_certified": True}, {"version_status": "current_verified"}, {"resource_kind": "blank_form", "source_kind": "published_report"}, {"download_status": "verified", "sha256": None}])
def test_catalog_rejects_unsupported_verification_claims(monkeypatch, tmp_path, mutation):
    catalog = deepcopy(government.load_government_catalog())
    catalog["documents"][0].update(mutation)
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(government, "CATALOG_PATH", path)
    with pytest.raises(CatalogError):
        government.load_government_catalog()
