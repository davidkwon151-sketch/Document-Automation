from urllib.error import HTTPError

import pytest

from templates import discovery
from templates.catalog import CatalogError


def test_only_observed_official_attachment_links():
    links = discovery.observed_links("https://www.spo.go.kr/forms", '''
      <a href="/file?id=1" title="보고서.hwpx">다운로드</a>
      <a href="https://www.spo.go.kr.evil.test/a.docx">거짓</a>
      <a href="http://www.spo.go.kr/a.pdf">비보안</a>
      <a href="javascript:download(2)">지출결의서.xlsx</a>
      <a href="/other">서식 목록</a>''')
    assert [x["status"] for x in links] == ["attachment", "dynamic_link", "page"]
    assert links[0]["url"] == "https://www.spo.go.kr/file?id=1"
    assert links[1]["url"] is None


def test_crawl_limits_deduplicates_and_keeps_provenance(monkeypatch, tmp_path):
    calls = []
    def fetch(url, timeout, limit, allowed):
        calls.append(url)
        if url.endswith("a.pdf") or url.endswith("b.pdf"):
            return b"%PDF-1.4\n test", url, "utf-8"
        return b'<a href="/a.pdf">Form.pdf</a><a href="/b.pdf">Copy.pdf</a><a href="/next">Next</a><a href="/extra">Extra</a>', url, "utf-8"
    monkeypatch.setattr(discovery, "_fetch", fetch)
    result = discovery.discover_forms([{"agency": "검찰청", "url": "https://www.spo.go.kr/start"}], tmp_path, download=True, max_depth=2, max_pages_per_host=2)
    assert result["summary"] == {"seeds": 1, "pages_read": 2, "discovered": 2, "downloaded": 2, "unique_downloads": 1, "fill_verified": 0}
    assert len(list(tmp_path.glob("*.pdf"))) == 1
    assert all(d["source_page"] == "https://www.spo.go.kr/start" and d["resource_kind"] == "unclassified" and d["version_status"] == "unknown" for d in result["documents"])
    assert len([u for u in calls if not u.endswith(".pdf")]) == 2


def test_login_drm_and_access_errors_are_explicit(monkeypatch, tmp_path):
    def fetch(url, *args):
        if url.endswith("start"):
            return b'<a href="/drm.hwp">drm</a><a href="/login.pdf">login</a><a href="/denied.docx">denied</a>', url, "utf-8"
        if "drm" in url:
            return b"NASCA DRM content", url, "utf-8"
        if "login" in url:
            return b'<html><input type="password"></html>', url, "utf-8"
        raise HTTPError(url, 403, "Forbidden", None, None)
    monkeypatch.setattr(discovery, "_fetch", fetch)
    result = discovery.discover_forms([{"agency": "기관", "url": "https://www.spo.go.kr/start"}], tmp_path, download=True, max_depth=0)
    assert {d["download_status"] for d in result["documents"]} == {"blocked_drm", "login_required", "access_denied"}
    assert result["summary"]["downloaded"] == 0


def test_unknown_dynamic_url_is_never_guessed(monkeypatch):
    monkeypatch.setattr(discovery, "_fetch", lambda url, *a: (b'<a href="javascript:fileDownload(123)">Blank.hwpx</a>', url, "utf-8"))
    result = discovery.discover_forms([{"agency": "기관", "url": "https://www.spo.go.kr/start"}], max_depth=0)
    assert result["documents"][0]["download_status"] == "portal_only"
    assert result["documents"][0]["url"] is None


def test_small_page_budget_prioritizes_observed_form_menu(monkeypatch):
    calls = []
    def fetch(url, *args):
        calls.append(url)
        if url.endswith("start"):
            return '<a href="/news">뉴스</a><a href="/forms">민원 서식</a>'.encode(), url, "utf-8"
        return b"", url, "utf-8"
    monkeypatch.setattr(discovery, "_fetch", fetch)
    discovery.discover_forms([{"agency": "기관", "url": "https://www.spo.go.kr/start"}], max_pages_per_host=2)
    assert calls == ["https://www.spo.go.kr/start", "https://www.spo.go.kr/forms"]


@pytest.mark.parametrize("kwargs", [{"max_depth": 3}, {"workers": 5}, {"timeout": 100}, {"max_pages_per_host": 0}, {"max_files_per_host": 1000}])
def test_invalid_crawl_limits(kwargs):
    with pytest.raises(ValueError):
        discovery.discover_forms([], **kwargs)


def test_unofficial_seed_rejected():
    with pytest.raises(CatalogError):
        discovery.discover_forms([{"agency": "가짜", "url": "https://evil.test/forms"}])


def test_only_registry_verified_external_host_can_be_explicitly_approved(monkeypatch):
    monkeypatch.setattr(discovery, "registry_site_hosts", lambda: {"www.koagi.or.kr"})
    monkeypatch.setattr(discovery, "_fetch", lambda url, *args: (b"", url, "utf-8"))
    assert discovery.discover_forms([{"agency": "관리원", "url": "https://www.koagi.or.kr/forms"}])["summary"]["pages_read"] == 1
    with pytest.raises(CatalogError):
        discovery.discover_forms([{"agency": "가짜", "url": "https://www.koagi.or.kr.evil.test/forms"}])


def test_total_file_and_byte_caps(monkeypatch, tmp_path):
    document_limits = []
    def fetch(url, timeout, limit, allowed):
        if url.endswith("start"):
            return b'<a href="/a.pdf">a</a><a href="/b.pdf">b</a><a href="/c.pdf">c</a>', url, "utf-8"
        document_limits.append(limit)
        return b"%PDF-" + b"x" * 5, url, "utf-8"
    monkeypatch.setattr(discovery, "_fetch", fetch)
    result = discovery.discover_forms([{"agency": "기관", "url": "https://www.spo.go.kr/start"}], tmp_path, download=True, max_depth=0, max_total_files=2, max_total_bytes=30)
    assert result["summary"]["downloaded"] == 2
    assert all(limit <= 15 for limit in document_limits)
    assert result["documents"][2]["download_status"] == "total_limit_reached"


def test_registry_unknown_website_is_separate_from_collection_failure():
    result = {"pages": [{"agency": "조사기관", "url": "https://www.spo.go.kr/a", "status": "failed", "error": "TLS"}], "documents": []}
    agencies = [{"id": "1", "name": "조사기관", "official_url": "https://www.spo.go.kr/a", "registry_source": "https://www.mois.go.kr/"},
                {"id": "2", "name": "URL미확인기관", "official_url": None, "registry_source": "https://www.alioplus.go.kr/"}]
    observations = discovery.agency_observations(agencies, result)
    assert observations[0]["crawl_status"] == "failed" and observations[0]["errors"] == ["TLS"]
    assert observations[1]["crawl_status"] == "unknown_website"
