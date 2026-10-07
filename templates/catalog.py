"""공식 공개 문서 카탈로그. 공개 보고서는 사내 빈 양식으로 간주하지 않는다.

원본은 Git에 포함하지 않는 data/public_templates에 보관한다. 공개 열람이나
다운로드 가능 여부는 재배포 또는 수정 허가와 별개이며 각 소유자의 조건을 따른다.
CLI: python -m templates.catalog list --group 삼성 / download 삼성-보고서
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from http.cookiejar import CookieJar
import ipaddress
import json
from pathlib import Path
import re
import tempfile
from zipfile import BadZipFile, ZipFile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener


CATALOG_PATH = Path(__file__).with_name("enterprise_catalog.json")
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "public_templates"
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
STATUSES = {"verified_public_form", "layout_reference", "portal_only", "no_verified_public_form"}


class CatalogError(ValueError):
    """등록되지 않은 출처, 안전하지 않은 경로 또는 잘못된 다운로드."""


def _https_host(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise CatalogError("출처 URL은 자격증명 없는 HTTPS여야 함")
    if parsed.fragment or parsed.port not in (None, 443):
        raise CatalogError("출처 URL의 포트 또는 fragment가 허용되지 않음")
    host = parsed.hostname.lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise CatalogError("IP 주소를 출처로 사용할 수 없음")
    if host == "localhost" or "." not in host or host.endswith((".local", ".internal")):
        raise CatalogError("로컬 호스트를 출처로 사용할 수 없음")
    return host


def _safe_filename(filename: str) -> str:
    if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}", filename):
        raise CatalogError("다운로드 파일명은 단일 안전한 파일명이어야 함")
    if ".." in filename or filename.split(".")[0].upper() in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))
    }:
        raise CatalogError("다운로드 파일명이 허용되지 않음")
    return filename


def load_catalog() -> dict:
    """검증 근거와 확인 한계를 포함한 2026년 자산 상위 30개 그룹 메타데이터."""
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    groups = catalog["groups"]
    if len(groups) != 30 or sorted(g["rank"] for g in groups) != list(range(1, 31)):
        raise CatalogError("공정위 상위 30개 그룹 순위가 불완전함")
    if len({g["name"] for g in groups}) != 30:
        raise CatalogError("기업집단 이름이 중복됨")
    ids: set[str] = set()
    for group in groups:
        if group["status"] not in STATUSES:
            raise CatalogError("기업집단 확인 상태가 올바르지 않음")
        _https_host(group["official_url"])
        _https_host(group["source_page"])
        for entry in group["documents"]:
            if entry["id"] in ids:
                raise CatalogError("문서 ID가 중복됨")
            ids.add(entry["id"])
            _safe_filename(entry["filename"])
            host = _https_host(entry["source_url"])
            if host not in entry["allowed_hosts"]:
                raise CatalogError("공식 다운로드 호스트가 등록되지 않음")
            for allowed in entry["allowed_hosts"]:
                _https_host("https://" + allowed)
            if entry["source_kind"] not in {"public_form", "published_report", "public_policy", "financial_data"}:
                raise CatalogError("문서 종류가 올바르지 않음")
            if entry["download_status"] == "verified":
                if not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256") or "") or entry.get("size_bytes", 0) <= 0:
                    raise CatalogError("검증 완료 문서에 원본 해시와 크기가 없음")
        if group["status"] == "verified_public_form" and not any(
            e["source_kind"] == "public_form" and e["download_status"] == "verified"
            for e in group["documents"]
        ):
            raise CatalogError("공개 빈 양식 확인 근거가 없음")
    return catalog


def list_public_templates(group: str | None = None) -> list[dict]:
    """다운로드 링크가 관찰된 공개 문서 목록. layout_reference는 빈 양식이 아님."""
    entries = []
    for item in load_catalog()["groups"]:
        if group is not None and group not in {item["name"], str(item["rank"]), *item.get("aliases", [])}:
            continue
        for entry in item["documents"]:
            entries.append({**deepcopy(entry), "group": item["name"], "rank": item["rank"], "group_status": item["status"]})
    return entries


class _OfficialRedirects(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str]):
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _https_host(newurl) not in self.allowed_hosts:
            raise CatalogError("등록되지 않은 호스트로의 다운로드 리다이렉트가 차단됨")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_signature(prefix: bytes, format_name: str, file_path: Path | None = None) -> None:
    if format_name == "PDF" and not prefix.startswith(b"%PDF-"):
        raise CatalogError("PDF 대신 HTML 등 다른 내용이 반환됨")
    if format_name in {"DOCX", "HWPX", "XLSX", "PPTX", "ZIP"} and not prefix.startswith(b"PK\x03\x04"):
        raise CatalogError("Office/ZIP 원본 서명이 일치하지 않음")
    if format_name == "PPTX" and file_path is not None:
        try:
            with ZipFile(file_path) as package:
                if "ppt/presentation.xml" not in package.namelist() or "[Content_Types].xml" not in package.namelist():
                    raise CatalogError("PPTX 프레젠테이션 본문이 없는 ZIP임")
        except BadZipFile as exc:
            raise CatalogError("PPTX 패키지가 올바르지 않음") from exc
    if format_name == "HWP" and not prefix.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise CatalogError("HWP 원본 서명이 일치하지 않음")


def download_public_template(entry: dict, directory: str | Path = DEFAULT_DIRECTORY) -> Path:
    """등록된 정확한 공식 URL만 내려받고 크기·형식·원본 해시를 확인한다.

    metadata의 URL/파일명을 호출자가 바꿔서 임의 사이트를 다운로드할 수 없다.
    확인 이후 원본이 바뀌면 해시 불일치로 차단하므로 카탈로그 재확인이 필요하다.
    """
    approved = next((e for e in list_public_templates() if e["id"] == entry.get("id")), None)
    if approved is None:
        raise CatalogError("등록되지 않은 공개 문서임")
    for key in ("source_url", "filename", "format", "allowed_hosts", "sha256"):
        if entry.get(key) != approved.get(key):
            raise CatalogError("호출자의 문서 메타데이터가 승인된 출처와 다름")
    return _download_verified_entry(approved, directory)


def _download_verified_entry(approved: dict, directory: str | Path = DEFAULT_DIRECTORY) -> Path:
    """기업/정부 wrapper가 승인 목록을 검증한 뒤 호출하는 공통 원본 다운로드."""
    if approved["download_status"] != "verified":
        raise CatalogError("원본 다운로드 검증이 완료되지 않은 출처임")
    target_dir = Path(directory).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(approved["filename"])
    target = target_dir / filename
    if target.is_symlink():
        raise CatalogError("다운로드 대상이 심볼릭 링크임")
    if target.resolve().parent != target_dir:
        raise CatalogError("다운로드 대상이 지정 폴더를 벗어남")
    allowed = set(approved["allowed_hosts"])
    _https_host(approved["source_url"])
    session_page = approved.get("session_page")
    if session_page:
        if _https_host(session_page) not in allowed:
            raise CatalogError("등록되지 않은 공식 세션 페이지임")
        opener = build_opener(_OfficialRedirects(allowed), HTTPCookieProcessor(CookieJar()))
        # Ordinary anonymous page cookies; no authentication or access-control bypass.
        with opener.open(Request(session_page, headers={"User-Agent": "ReportAgent-PublicCorpus/1.0"}), timeout=30) as session_response:
            if _https_host(session_response.geturl()) not in allowed:
                raise CatalogError("공식 세션 응답의 호스트가 다름")
    else:
        opener = build_opener(_OfficialRedirects(allowed))
    headers = {"User-Agent": "ReportAgent-PublicCorpus/1.0", "Accept": "application/octet-stream,*/*"}
    if session_page:
        headers["Referer"] = session_page
    request = Request(approved["source_url"], headers=headers)
    temp_path = None
    try:
        with opener.open(request, timeout=30) as response:
            final_url = response.geturl()
            if _https_host(final_url) not in allowed:
                raise CatalogError("응답의 최종 호스트가 공식 출처와 다름")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
                raise CatalogError("공개 문서가 최대 다운로드 크기를 넘음")
            digest = hashlib.sha256()
            count = 0
            prefix = b""
            with tempfile.NamedTemporaryFile(dir=target_dir, prefix=".download-", delete=False) as stream:
                temp_path = Path(stream.name)
                while block := response.read(64 * 1024):
                    count += len(block)
                    if count > MAX_DOWNLOAD_BYTES:
                        raise CatalogError("공개 문서가 최대 다운로드 크기를 넘음")
                    if len(prefix) < 8:
                        prefix = (prefix + block)[:8]
                    digest.update(block)
                    stream.write(block)
            _check_signature(prefix, approved["format"], temp_path)
            if digest.hexdigest() != approved["sha256"]:
                raise CatalogError("확인된 원본의 SHA-256과 달라 카탈로그 재확인이 필요함")
            temp_path.replace(target)
            metadata = {**approved, "downloaded_at": datetime.now(timezone.utc).isoformat(), "final_url": final_url, "size_bytes": count}
            target.with_suffix(target.suffix + ".source.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            return target
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="공식 공개 문서 카탈로그 (사내 양식 호환 보장 목록이 아님)")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--group")
    listing.add_argument("--search", default="")
    download = commands.add_parser("download")
    download.add_argument("id")
    download.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    args = parser.parse_args(argv)
    if args.command == "list":
        entries = list_public_templates(args.group)
        query = args.search.casefold()
        entries = [e for e in entries if query in " ".join(str(e.get(k, "")) for k in ("id", "group", "affiliate", "title", "format")).casefold()]
        print(json.dumps(entries, ensure_ascii=False, indent=2))
    else:
        entry = next((e for e in list_public_templates() if e["id"] == args.id), None)
        if entry is None:
            parser.error("등록된 문서 ID를 지정해야 함")
        print(download_public_template(entry, args.directory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
