"""공식 공개 제출 서식 목록. 원본 확인과 현행 법적 적합성 인증은 별개임."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import re

from templates.catalog import CatalogError, _download_verified_entry, _https_host, _safe_filename

CATALOG_PATH = Path(__file__).with_name("government_catalog.json")
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "public_templates" / "government"
FORMATS = {"HWP", "HWPX", "DOCX", "XLSX", "PDF"}
VERSION_STATUSES = {"unknown", "historical", "current_verified", "stale"}


def official_host(url: str) -> str:
    """정부 원본은 정확한 공식 도메인 경계와 HTTPS를 확인함."""
    host = _https_host(url)
    if not (host.endswith(".go.kr") or host in {"go.kr", "korea.kr", "gov.kr"}
            or host.endswith((".korea.kr", ".gov.kr"))):
        raise CatalogError("공식 정부 도메인 출처가 아님")
    return host


def load_government_catalog() -> dict:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != 1 or not isinstance(catalog.get("documents"), list):
        raise CatalogError("정부 카탈로그 구조가 올바르지 않음")
    ids = set()
    for entry in catalog["documents"]:
        if not isinstance(entry.get("id"), str) or not entry["id"] or entry["id"] in ids:
            raise CatalogError("정부 문서 ID가 없거나 중복됨")
        ids.add(entry["id"])
        for key in ("title", "agency", "category", "form_type", "notes"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise CatalogError(f"정부 문서 {key} 근거가 없음")
        official_host(entry["source_page"])
        host = official_host(entry["source_url"])
        if entry.get("document_url") != entry["source_url"]:
            raise CatalogError("관찰한 문서 URL과 다운로드 URL이 다름")
        if host not in entry.get("allowed_hosts", []):
            raise CatalogError("공식 다운로드 호스트가 등록되지 않음")
        for allowed in entry["allowed_hosts"]:
            official_host("https://" + allowed)
        _safe_filename(entry["filename"])
        if entry.get("format") not in FORMATS or not entry["filename"].lower().endswith("." + entry["format"].lower()):
            raise CatalogError("정부 문서 형식이 올바르지 않음")
        kind = entry.get("resource_kind")
        if kind not in {"blank_form", "layout_reference"}:
            raise CatalogError("빈 양식과 참고 문서 구분이 없음")
        if kind == "blank_form" and entry.get("source_kind") != "public_form":
            raise CatalogError("참고 보고서를 빈 양식으로 등록할 수 없음")
        if entry.get("statutory_form") is not None and type(entry["statutory_form"]) is not bool:
            raise CatalogError("법정서식 여부는 true/false/미확인만 허용됨")
        if entry.get("legal_compliance_certified") is not False:
            raise CatalogError("법적 적합성 자동 인증을 표시할 수 없음")
        date.fromisoformat(entry["last_checked"])
        if entry.get("version_status") not in VERSION_STATUSES:
            raise CatalogError("서식 현행 확인 상태가 올바르지 않음")
        if entry["version_status"] == "current_verified":
            if entry.get("statutory_form") is not True or not entry.get("legal_source_url") or not entry.get("effective_date"):
                raise CatalogError("현행 법정서식의 법령·시행일 근거가 없음")
            official_host(entry["legal_source_url"])
            if date.fromisoformat(entry["effective_date"]) > date.fromisoformat(entry["last_checked"]):
                raise CatalogError("확인 시점에 시행되지 않은 법정서식임")
        if entry.get("download_status") not in {"verified", "failed", "login_required", "blocked_drm", "portal_only"}:
            raise CatalogError("다운로드 확인 상태가 올바르지 않음")
        if entry["download_status"] == "verified" and (
            not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256") or "")
            or not isinstance(entry.get("size_bytes"), int) or entry["size_bytes"] <= 0
        ):
            raise CatalogError("원본 검증 완료 문서의 해시·크기가 없음")
    registry_ids = set()
    for agency in catalog.get("agency_registry", []):
        if not agency.get("id") or agency["id"] in registry_ids or not agency.get("name"):
            raise CatalogError("공식 기관 목록의 ID 또는 이름이 올바르지 않음")
        registry_ids.add(agency["id"])
        official_host(agency["registry_source"])
        if agency.get("forms_status") not in {"unknown", "seed_download_verified"}:
            raise CatalogError("기관의 서식 확인 상태가 올바르지 않음")
        if agency.get("crawl_eligible") is True:
            _https_host(agency["official_url"])
            if agency.get("presence_status") != "observed_official_registry":
                raise CatalogError("기관 공식 홈페이지의 목록 관찰 근거가 없음")
        if any(type(agency.get(key)) is not int or agency[key] < 0 for key in
               ("discovered_forms_count", "download_verified_count", "fill_verified_count")):
            raise CatalogError("기관별 서식 확인 수가 올바르지 않음")
    return catalog


def list_government_templates(category: str | None = None) -> list[dict]:
    """참고 기안 예시도 반환하므로 resource_kind를 확인해서 사용해야 함."""
    return [deepcopy(e) for e in load_government_catalog()["documents"]
            if category is None or e["category"] == category]


def list_government_agencies(agency_type: str | None = None) -> list[dict]:
    """공식 목록에서 관찰한 기관. forms_status=unknown은 미조사를 뜻함."""
    return [deepcopy(a) for a in load_government_catalog().get("agency_registry", [])
            if agency_type is None or a["agency_type"] == agency_type]


def registry_site_hosts() -> set[str]:
    """공식 정부 기관 목록에서 실제 연결된 사이트만 exact host로 허용함."""
    return {_https_host(a["official_url"]) for a in list_government_agencies()
            if a.get("crawl_eligible") is True}


def download_government_template(entry: dict, directory: str | Path = DEFAULT_DIRECTORY) -> Path:
    approved = next((e for e in list_government_templates() if e["id"] == entry.get("id")), None)
    if approved is None:
        raise CatalogError("등록되지 않은 정부 문서임")
    for key in ("source_url", "filename", "format", "allowed_hosts", "sha256"):
        if entry.get(key) != approved.get(key):
            raise CatalogError("문서 메타데이터가 등록된 공식 출처와 다름")
    return _download_verified_entry(approved, directory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="공식 공개 정부 서식 seed 목록")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--category")
    download = commands.add_parser("download")
    download.add_argument("id")
    download.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    args = parser.parse_args(argv)
    if args.command == "list":
        print(json.dumps(list_government_templates(args.category), ensure_ascii=False, indent=2))
    else:
        entry = next((e for e in list_government_templates() if e["id"] == args.id), None)
        if entry is None:
            parser.error("등록된 정부 문서 ID가 필요함")
        print(download_government_template(entry, args.directory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
