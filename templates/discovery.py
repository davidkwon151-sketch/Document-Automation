"""공식 페이지에서 실제 관찰한 첨부 링크만 제한적으로 수집하는 CLI.

발견된 파일은 분류 미확인 후보임. 수집 성공이 양식 채우기 검증이나 현행
법정 적합성을 뜻하지 않는다. 로그인·보안/DRM·동적 링크는 우회하지 않음.
python -m templates.discovery --download --max-depth 1 --output data/public_templates/government/discovery
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit, urldefrag
from urllib.request import Request, build_opener

from templates.catalog import CatalogError, _OfficialRedirects, _check_signature, _https_host
from templates.government import DEFAULT_DIRECTORY, official_host, load_government_catalog, registry_site_hosts

MAX_PAGE_BYTES = 4 * 1024 * 1024
FORMAT_RE = re.compile(r"\.(hwpx|hwp|docx|xlsx|pdf)(?:\b|$)|file_ext=(hwpx|hwp|docx|xlsx|pdf)\b", re.I)
MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_TOTAL_BYTES = 300 * 1024 * 1024


def _site_host(url: str, approved_hosts: set[str]) -> str:
    try:
        return official_host(url)
    except CatalogError:
        host = _https_host(url)
        if host not in approved_hosts:
            raise CatalogError("공식 기관 목록에 연결되지 않은 사이트임")
        return host


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.active = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.active = {"href": attrs.get("href", ""), "title": attrs.get("title", ""), "text": "", "onclick": attrs.get("onclick", "")}
        elif self.active is not None and tag == "img":
            self.active["text"] += " " + attrs.get("alt", "")

    def handle_data(self, data):
        if self.active is not None:
            self.active["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.active is not None:
            self.active["text"] = " ".join(self.active["text"].split())
            self.links.append(self.active)
            self.active = None


def observed_links(page_url: str, page_text: str, *, approved_hosts: set[str] | None = None) -> list[dict]:
    """URL 확장자, 표시 파일명, title, file_ext의 관찰 근거를 보존함."""
    approved_hosts = approved_hosts or set()
    _site_host(page_url, approved_hosts)
    parser = _Links()
    parser.feed(page_text)
    result = []
    for link in parser.links:
        label = link["text"] or link["title"]
        match = FORMAT_RE.search(" ".join((link["href"], link["title"], link["text"])))
        fmt = (match[1] or match[2]).upper() if match else None
        href = link["href"].strip()
        if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
            if fmt:
                result.append({"url": None, "title": label, "format": fmt, "status": "dynamic_link", "source_page": page_url})
            continue
        url = urldefrag(urljoin(page_url, href))[0]
        try:
            _site_host(url, approved_hosts)
        except (CatalogError, ValueError):
            continue
        result.append({"url": url, "title": label, "format": fmt, "status": "attachment" if fmt else "page", "source_page": page_url})
    return result


def _fetch(url: str, timeout: float, limit: int, allowed_hosts: list[str]) -> tuple[bytes, str, str]:
    opener = build_opener(_OfficialRedirects(set(allowed_hosts)))
    request = Request(url, headers={"User-Agent": "ReportAgent-PublicForms/1.0", "Accept": "*/*"})
    with opener.open(request, timeout=timeout) as response:
        final_url = response.geturl()
        if _https_host(final_url) not in allowed_hosts:
            raise CatalogError("등록되지 않은 최종 다운로드 호스트임")
        raw = response.read(limit + 1)
        if len(raw) > limit:
            error = CatalogError("응답 크기 제한을 초과함")
            error.bytes_read = len(raw)
            raise error
        return raw, final_url, response.headers.get_content_charset() or "utf-8"


def _failure(exc: Exception) -> str:
    if isinstance(exc, HTTPError) and exc.code in {401, 403}:
        return "access_denied"
    return "failed"


def _atomic_json(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise CatalogError("메타데이터 대상이 심볼릭 링크임")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".manifest-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def default_seeds(include_registry: bool = False) -> list[dict]:
    catalog = load_government_catalog()
    seeds = [{"agency": e["agency"], "url": e["source_page"]} for e in catalog["documents"]]
    if include_registry:
        # 기관 존재 근거와 공식 URL이 모두 있는 항목만 탐색함. 미확인 URL은 만들지 않음.
        seeds += [{"agency": e["name"], "url": e["official_url"]} for e in catalog.get("agency_registry", [])
                  if (e.get("official_url") or "").startswith("https://") and e.get("crawl_eligible") is True]
    return list({s["url"]: s for s in seeds}.values())


def discover_forms(seeds: list[dict] | None = None, output_dir: str | Path | None = None,
                   *, download: bool = False, max_depth: int = 1, max_pages_per_host: int = 5,
                   max_files_per_host: int = 100, workers: int = 4, timeout: float = 15,
                   max_total_files: int = 250, max_file_bytes: int = MAX_FILE_BYTES,
                   max_total_bytes: int = MAX_TOTAL_BYTES) -> dict:
    """bounded HTML 탐색과 원본 수집. 카탈로그 승격은 별도 검토 후 수행함."""
    if max_depth not in {0, 1, 2} or not 1 <= workers <= 4 or not 1 <= max_pages_per_host <= 50:
        raise ValueError("탐색 깊이 0~2, worker 1~4, 호스트별 페이지 1~50만 허용됨")
    if not 1 <= max_files_per_host <= 200 or not 1 <= timeout <= 30:
        raise ValueError("호스트별 첨부 1~200, 타임아웃 1~30초만 허용됨")
    if not 1 <= max_total_files <= 300 or not 1 <= max_file_bytes <= MAX_FILE_BYTES or not 1 <= max_total_bytes <= MAX_TOTAL_BYTES:
        raise ValueError("실행당 최대 300개 파일, 개별 30MiB, 전체 300MiB만 허용됨")
    seeds = default_seeds() if seeds is None else seeds
    approved_hosts = registry_site_hosts()
    pending = []
    for seed in seeds:
        url = seed["url"]
        _site_host(url, approved_hosts)
        pending.append({"url": url, "agency": seed["agency"], "depth": 0})
    pages, documents, seen_pages, seen_files, page_counts = [], [], set(), set(), {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while pending:
            batch, rest = [], []
            for item in pending:
                host = _site_host(item["url"], approved_hosts)
                if item["url"] in seen_pages or page_counts.get(host, 0) >= max_pages_per_host:
                    continue
                if len(batch) >= workers:
                    rest.append(item)
                    continue
                seen_pages.add(item["url"])
                page_counts[host] = page_counts.get(host, 0) + 1
                batch.append(item)
            pending = rest
            if not batch:
                break
            def read_page(item):
                try:
                    raw, final, encoding = _fetch(item["url"], timeout, MAX_PAGE_BYTES, [_site_host(item["url"], approved_hosts)])
                    text = raw.decode(encoding, errors="replace")
                    status = "login_required" if re.search(r'<input[^>]*type=["\']password', text, re.I) else "read"
                    return {**item, "status": status, "final_url": final}, observed_links(final, text, approved_hosts=approved_hosts) if status == "read" else []
                except Exception as exc:
                    return {**item, "status": _failure(exc), "error": str(exc)}, []
            for page, links in pool.map(read_page, batch):
                pages.append(page)
                # ponytail: 정적 첨부/서식 메뉴 우선 탐색임. JS 전용 게시판은 portal_only로 남김.
                links.sort(key=lambda link: bool(re.search(r"서식|양식|민원|자료|신청|제출|form|download", link["title"] + " " + (link["url"] or ""), re.I)), reverse=True)
                for link in links:
                    if link["status"] == "dynamic_link":
                        documents.append({**link, "agency": page["agency"], "download_status": "portal_only", "version_status": "unknown", "resource_kind": "unclassified", "legal_compliance_certified": False})
                    elif link["format"]:
                        if link["url"] not in seen_files:
                            seen_files.add(link["url"])
                            documents.append({**link, "agency": page["agency"], "download_status": "discovered", "version_status": "unknown", "resource_kind": "unclassified", "legal_compliance_certified": False})
                    elif page["depth"] < max_depth and _site_host(link["url"], approved_hosts) == _site_host(page["url"], approved_hosts):
                        pending.append({"url": link["url"], "agency": page["agency"], "depth": page["depth"] + 1})
    directory = Path(output_dir or DEFAULT_DIRECTORY / "discovery").resolve()
    file_counts, selected = {}, []
    for document in documents:
        if not document["url"]:
            continue
        host = _site_host(document["url"], approved_hosts)
        if file_counts.get(host, 0) >= max_files_per_host:
            document["download_status"] = "limit_reached"
            continue
        if len(selected) >= max_total_files:
            document["download_status"] = "total_limit_reached"
            continue
        file_counts[host] = file_counts.get(host, 0) + 1
        selected.append(document)
    if download:
        directory.mkdir(parents=True, exist_ok=True)
        def read_document(item):
            document, byte_limit = item
            consumed = 0
            try:
                raw, final, _ = _fetch(document["url"], timeout, byte_limit, [_site_host(document["url"], approved_hosts)])
                consumed = len(raw)
                if raw.startswith((b"\xef\xbb\xbf", b"<")):
                    text = raw.decode("utf-8", errors="replace")
                    document["download_status"] = "login_required" if re.search(r'type=["\']password', text, re.I) else "portal_only"
                    return document, None, consumed
                if raw.startswith(b"NASCA"):
                    document["download_status"] = "blocked_drm"
                    return document, None, consumed
                _check_signature(raw[:8], document["format"])
                digest = hashlib.sha256(raw).hexdigest()
                document.update(download_status="verified", sha256=digest, size_bytes=len(raw), final_url=final,
                                filename=digest + "." + document["format"].lower())
                return document, raw, consumed
            except Exception as exc:
                document.update(download_status=_failure(exc), error=str(exc))
                return document, None, consumed or getattr(exc, "bytes_read", 0)
        hashes, bytes_read = set(), 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for offset in range(0, len(selected), workers):
                batch = selected[offset:offset + workers]
                remaining = max_total_bytes - bytes_read
                if remaining < 2 * len(batch):
                    for document in selected[offset:]:
                        document["download_status"] = "total_limit_reached"
                    break
                byte_limit = min(max_file_bytes, (remaining - len(batch)) // len(batch))
                for document, raw, consumed in pool.map(read_document, [(document, byte_limit) for document in batch]):
                    bytes_read += consumed
                    if raw is None:
                        continue
                    if document["sha256"] in hashes:
                        document["duplicate_content"] = True
                        continue
                    hashes.add(document["sha256"])
                    path = directory / document["filename"]
                    if path.is_symlink():
                        raise CatalogError("다운로드 대상이 심볼릭 링크임")
                    with tempfile.NamedTemporaryFile(dir=directory, prefix=".collect-", delete=False) as stream:
                        stream.write(raw)
                        temporary = Path(stream.name)
                    temporary.replace(path)
                    _atomic_json(path.with_suffix(path.suffix + ".source.json"), document)
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "scope": "관찰한 공식 첨부 링크 후보임. 전수 수집·빈 양식·채우기 호환·현행 법적 적합성 인증이 아님.",
              "pages": pages, "documents": documents,
              "limits": {"max_depth": max_depth, "max_pages_per_host": max_pages_per_host, "max_files_per_host": max_files_per_host, "max_total_files": max_total_files, "max_file_bytes": max_file_bytes, "max_total_bytes": max_total_bytes, "workers": workers, "timeout": timeout},
              "summary": {"seeds": len(seeds), "pages_read": sum(p["status"] == "read" for p in pages),
                          "discovered": sum(d["url"] is not None for d in documents),
                          "downloaded": sum(d["download_status"] == "verified" for d in documents),
                          "unique_downloads": len({d["sha256"] for d in documents if d.get("sha256")}), "fill_verified": 0}}
    if output_dir is not None or download:
        directory.mkdir(parents=True, exist_ok=True)
        _atomic_json(directory / "discovery.json", result)
    return result


def agency_observations(agencies: list[dict], result: dict) -> list[dict]:
    observations = []
    for agency in agencies:
        pages = [p for p in result["pages"] if p["agency"] == agency["name"]]
        documents = [d for d in result["documents"] if d["agency"] == agency["name"]]
        status = "read" if any(p["status"] == "read" for p in pages) else "failed" if pages else "unknown_website" if not agency.get("official_url") else "not_visited"
        observations.append({"id": agency["id"], "name": agency["name"], "official_url": agency.get("official_url"),
                             "registry_source": agency["registry_source"], "crawl_status": status,
                             "observed_urls": [p["url"] for p in pages], "errors": [p["error"] for p in pages if p.get("error")],
                             "discovered_count": len(documents), "downloaded_count": sum(d["download_status"] == "verified" for d in documents),
                             "fill_verified_count": 0, "version_status": "unknown"})
    return observations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="공식 페이지의 관찰된 공개 첨부 링크 제한 수집")
    parser.add_argument("--output", type=Path, default=DEFAULT_DIRECTORY / "discovery")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--include-registry", action="store_true")
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--max-pages-per-host", type=int, default=5)
    parser.add_argument("--max-files-per-host", type=int, default=100)
    parser.add_argument("--max-total-files", type=int, default=250)
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args(argv)
    result = discover_forms(default_seeds(args.include_registry), args.output, download=args.download,
                            max_depth=args.max_depth, max_pages_per_host=args.max_pages_per_host,
                            max_files_per_host=args.max_files_per_host, max_total_files=args.max_total_files, timeout=args.timeout)
    if args.include_registry:
        result["agency_observations"] = agency_observations(load_government_catalog()["agency_registry"], result)
        _atomic_json(args.output.resolve() / "discovery.json", result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
