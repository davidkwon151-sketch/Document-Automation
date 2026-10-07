"""Official planning, finance, approval and quality forms; references stay separate."""

from copy import deepcopy
from hashlib import file_digest
import json
from pathlib import Path
import re

from .catalog import CatalogError, _check_signature, _download_verified_entry, _https_host, _safe_filename

CATALOG_PATH = Path(__file__).with_name("office_catalog.json")
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / "data/public_templates/office"
OFFICE_WORKFLOWS = {
    "office_planning": "기획·결과 보고", "financial_report": "재무·금융 보고",
    "daily_approval": "일상 품의·구매·지출", "industrial_quality": "산업 품질·변경관리",
}
OFFICIAL_HOSTS = {"www.hanbat.ac.kr", "www.kgrowth.or.kr", "www.poscointl.com",
                  "www.law.go.kr", "www.bosch.com", "assets.bosch.com"}
FORMATS = {"HWP", "HWPX", "DOCX", "XLSX", "PDF", "PPTX"}


def load_office_catalog() -> dict:
    """Return dated originals. Publication alone certifies neither eligibility nor filling."""
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("workflows") != OFFICE_WORKFLOWS or not isinstance(data.get("documents"), list):
        raise CatalogError("사무·재무 양식 카탈로그 구조·워크플로가 올바르지 않음")
    identifiers, filenames = set(), set()
    for entry in data["documents"]:
        if not entry.get("id") or entry["id"] in identifiers or any(not entry.get(k) for k in ("title", "version_label", "publisher", "checked_at")):
            raise CatalogError("사무 문서 ID·제목·발행기관·버전·확인일이 없거나 중복됨")
        identifiers.add(entry["id"])
        filename = _safe_filename(entry["filename"])
        if filename in filenames or entry.get("format") not in FORMATS or not filename.endswith("." + entry["format"].lower()):
            raise CatalogError("사무 원본 파일명·형식이 올바르지 않거나 중복됨")
        filenames.add(filename)
        workflows = entry.get("workflows")
        if not isinstance(workflows, list) or not workflows or len(set(workflows)) != len(workflows) or any(w not in OFFICE_WORKFLOWS for w in workflows):
            raise CatalogError("미등록 또는 중복 사무 워크플로임")
        hosts = entry.get("allowed_hosts")
        if not isinstance(hosts, list) or not hosts or any(host not in OFFICIAL_HOSTS for host in hosts):
            raise CatalogError("관찰한 공식 다운로드 호스트만 허용함")
        if _https_host(entry["source_page"]) not in OFFICIAL_HOSTS or _https_host(entry["source_url"]) not in hosts:
            raise CatalogError("사무 원본·출처 페이지가 공식 HTTPS 출처가 아님")
        if entry.get("session_page") and _https_host(entry["session_page"]) not in hosts:
            raise CatalogError("사무 원본의 공식 세션 페이지가 등록 호스트 밖임")
        classification = (entry.get("resource_kind"), entry.get("source_kind"), entry.get("use_classification"))
        if classification not in {
            ("blank_form", "public_form", "public_submission"),
            ("blank_form", "public_form", "statutory_submission"),
            ("blank_form", "public_form", "company_public_submission"),
            ("layout_reference", "published_report", "reference_report"),
            ("layout_reference", "public_guide", "reference_guidance"),
        }:
            raise CatalogError("사무 작성용 서식·법정 제출·회사 공개 제출·완성 보고서를 구분해야 함")
        if any(entry.get(flag) is not False for flag in (
            "company_internal", "legal_compliance_certified", "mandatory_requirements_verified", "full_submission_ready", "fill_verified",
        )) or entry.get("license", {}).get("redistribution_permitted") is not False:
            raise CatalogError("사내 양식·법적 적합성·전체 기입·재배포 권한을 추정할 수 없음")
        if entry.get("application_status") not in {"closed", "not_assessed", "not_applicable"}:
            raise CatalogError("원본 수집만으로 현재 제출·지원 가능 여부를 인증할 수 없음")
        if entry.get("download_status") not in {"verified", "failed", "login_required", "blocked_drm"}:
            raise CatalogError("사무 원본 다운로드 상태가 올바르지 않음")
        if entry["download_status"] == "verified" and (
            not re.fullmatch("[0-9a-f]{64}", entry.get("sha256") or "") or
            type(entry.get("size_bytes")) is not int or not 0 < entry["size_bytes"] <= 64 * 1024 * 1024
        ):
            raise CatalogError("확인된 사무 원본의 SHA·크기가 없음")
    return data


def list_office_templates(workflow: str | None = None) -> list[dict]:
    if workflow is not None and workflow not in OFFICE_WORKFLOWS:
        raise CatalogError("등록되지 않은 사무 워크플로임")
    return [deepcopy(entry) for entry in load_office_catalog()["documents"]
            if workflow is None or workflow in entry["workflows"]]


def download_office_template(entry: dict, directory: str | Path = DEFAULT_DIRECTORY) -> Path:
    """Use pinned official sources; preserve modified local originals for inspection."""
    approved = next((item for item in list_office_templates() if item["id"] == entry.get("id")), None)
    if approved is None or approved["download_status"] != "verified":
        raise CatalogError("등록·확인된 사무 원본만 다운로드할 수 있음")
    for key in ("source_url", "filename", "format", "allowed_hosts", "sha256", "session_page"):
        if entry.get(key) != approved.get(key):
            raise CatalogError("사무 요청이 관찰·등록한 공식 원본과 다름")
    parent = Path(directory).resolve()
    target = parent / _safe_filename(approved["filename"])
    if target.is_symlink() or target.resolve().parent != parent:
        raise CatalogError("사무 원본 캐시 경로가 지정 폴더를 벗어남")
    if target.is_file():
        with target.open("rb") as stream:
            digest = file_digest(stream, "sha256").hexdigest()
        if digest != approved["sha256"]:
            raise CatalogError("기존 사무 원본 해시가 달라 다시 확인해야 함")
        with target.open("rb") as stream:
            _check_signature(stream.read(8), approved["format"], target)
        return target
    return _download_verified_entry(approved, directory)
