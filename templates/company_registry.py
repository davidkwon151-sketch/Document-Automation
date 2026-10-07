"""매출 순위의 기준과 출처를 보존하는 CSV/JSON 기업 목록 가져오기.

기업 목록은 양식 호환 검증 결과가 아니다. 매출 기준, 회계연도, 금융 포함 여부,
연결/별도 기준이 다르면 서로 같은 순위로 병합하지 않는다. 가져오기에서 입력한
source_checked는 사용자의 확인 선언이며 원문 수치를 자동으로 재검증한 뜻이 아니다.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

DEFAULT_REGISTRY = Path(__file__).with_name("revenue_registry.json")
MAX_IMPORT_BYTES = 20 * 1024 * 1024
MAX_COMPANIES = 100_000
META_FIELDS = {
    "dataset_name", "source_url", "source_owner", "fiscal_year", "accounting_basis",
    "financial_sector", "universe", "ranking_definition", "revenue_unit",
    "verification_status", "checked_at", "coverage", "expected_count",
}


class RegistryError(ValueError):
    """순위 정의, 출처 또는 기업 레코드가 불완전하거나 충돌함."""


def _url(value: str) -> str:
    if not isinstance(value, str):
        raise RegistryError("출처 URL이 문자열이어야 함")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RegistryError("출처 URL은 자격증명 없는 HTTPS여야 함")
    return value


def _integer(value, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)):
        raise RegistryError(f"{label}는 정수여야 함")
    number = int(value)
    if not minimum <= number <= maximum:
        raise RegistryError(f"{label} 범위가 올바르지 않음")
    return number


def _name(value, label="기업명") -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{label}이 비어 있음")
    return unicodedata.normalize("NFC", value).strip()


def _identity(name: str) -> str:
    # 같은 기업의 (주)/㈜ 표기와 공백만 중복 판단용으로 통일한다.
    return re.sub(r"\s+|\(주\)|㈜", "", name).casefold()


def _metadata(metadata: dict) -> dict:
    if not isinstance(metadata, dict) or META_FIELDS - metadata.keys():
        missing = sorted(META_FIELDS - metadata.keys()) if isinstance(metadata, dict) else sorted(META_FIELDS)
        raise RegistryError("순위 정의 메타데이터가 부족함: " + ", ".join(missing))
    result = deepcopy(metadata)
    for key in ("dataset_name", "source_owner", "universe", "ranking_definition"):
        result[key] = _name(result[key], key)
    result["source_url"] = _url(result["source_url"])
    result["fiscal_year"] = _integer(result["fiscal_year"], "회계연도", 1900, datetime.now(timezone.utc).year)
    result["expected_count"] = _integer(result["expected_count"], "목표 기업 수", 1, MAX_COMPANIES)
    choices = {
        "accounting_basis": {"separate", "consolidated", "mixed", "unknown"},
        "financial_sector": {"included", "excluded", "unknown"},
        "revenue_unit": {"KRW", "KRW_million", "KRW_billion"},
        "verification_status": {"source_checked", "unverified"},
        "coverage": {"partial", "complete", "unknown"},
    }
    for key, allowed in choices.items():
        if result[key] not in allowed:
            raise RegistryError(f"{key} 값이 허용되지 않음")
    try:
        checked_at = datetime.fromisoformat(result["checked_at"].replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError) as exc:
        raise RegistryError("checked_at이 ISO 시간이어야 함") from exc
    if checked_at.tzinfo is None:
        raise RegistryError("checked_at에 시간대가 필요함")
    result["checked_at"] = checked_at.astimezone(timezone.utc).isoformat()
    if result["coverage"] == "complete" and (
        result["accounting_basis"] == "unknown" or result["financial_sector"] == "unknown"
        or result["verification_status"] != "source_checked"
    ):
        raise RegistryError("기준/출처 확인 없이 전체 순위 확보를 선언할 수 없음")
    return result


def _registry(companies: list[dict], metadata: dict) -> dict:
    meta = _metadata(metadata)
    if not isinstance(companies, list) or len(companies) > MAX_COMPANIES:
        raise RegistryError("기업 목록의 형식 또는 크기가 올바르지 않음")
    validated = []
    ranks, identities = set(), set()
    for record in companies:
        if not isinstance(record, dict):
            raise RegistryError("각 기업 레코드는 객체여야 함")
        try:
            name = _name(record["name"])
            rank = _integer(record["rank"], "순위", 1, meta["expected_count"])
            year = _integer(record["year"], "기업 회계연도", 1900, datetime.now(timezone.utc).year)
            revenue = Decimal(str(record["revenue"]))
            source_url = _url(record["source_url"])
        except KeyError as exc:
            raise RegistryError(f"기업 필수 정보가 없음: {exc.args[0]}") from exc
        except (InvalidOperation, TypeError) as exc:
            raise RegistryError("매출액은 유한한 숫자여야 함") from exc
        if not revenue.is_finite() or revenue < 0:
            raise RegistryError("매출액은 유한한 0 이상의 숫자여야 함")
        if year != meta["fiscal_year"]:
            raise RegistryError("다른 회계연도의 기업을 같은 순위에 섞을 수 없음")
        identity = _identity(name)
        if rank in ranks or identity in identities:
            raise RegistryError("기업명 또는 순위가 중복됨")
        ranks.add(rank)
        identities.add(identity)
        normalized = {**deepcopy(record), "name": name, "rank": rank, "year": year, "revenue": format(revenue, "f"), "source_url": source_url}
        affiliate = record.get("affiliate")
        normalized["affiliate"] = None if affiliate in (None, "") else _name(affiliate, "계열사 소속")
        normalized["revenue_unit"] = meta["revenue_unit"]
        validated.append(normalized)
    if meta["coverage"] == "complete" and ranks != set(range(1, meta["expected_count"] + 1)):
        raise RegistryError("전체 순위 선언에는 1위부터 목표 순위까지 모두 필요함")
    validated.sort(key=lambda row: row["rank"])
    # 순위가 올라갈수록 매출이 커지는 행은 오기 또는 기준 혼합으로 처리한다.
    if any(Decimal(a["revenue"]) < Decimal(b["revenue"]) for a, b in zip(validated, validated[1:])):
        raise RegistryError("매출 순위와 매출액의 내림차순이 일치하지 않음")
    return {"metadata": meta, "companies": validated}


def import_registry(path: str | Path, metadata: dict | None = None) -> dict:
    """네트워크 호출 없이 출처가 기재된 CSV/JSON을 검증해 가져온다.

    CSV에는 rank,name,revenue,year,source_url 열이 필요하다. JSON은 같은 행의
    목록 또는 {metadata,companies}를 지원한다. 유효하지 않은 행은 누락하지 않고
    전체 가져오기를 실패시켜 coverage가 조용히 부풀려지지 않게 한다.
    """
    input_path = Path(path)
    if input_path.stat().st_size > MAX_IMPORT_BYTES:
        raise RegistryError("기업 목록 파일이 최대 크기를 넘음")
    if input_path.suffix.lower() == ".csv":
        with input_path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if metadata is None:
            raise RegistryError("CSV에는 별도의 순위 기준 메타데이터가 필요함")
    elif input_path.suffix.lower() == ".json":
        data = json.loads(input_path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            rows = data.get("companies")
            if metadata is None:
                metadata = data.get("metadata")
        else:
            rows = data
    else:
        raise RegistryError("CSV 또는 JSON 파일만 지원함")
    registry = _registry(rows, metadata)
    registry["metadata"]["import_file_sha256"] = hashlib.sha256(input_path.read_bytes()).hexdigest()
    registry["metadata"]["verification_origin"] = "provided_by_importer"
    return registry


def load_registry(path: str | Path | None = None) -> dict:
    """기본값은 공식 인천상의 FY2024 부분 목록. 전국 1000개 확보를 뜻하지 않는다."""
    target = Path(path) if path is not None else DEFAULT_REGISTRY
    if not target.exists():
        if path is not None:
            raise FileNotFoundError(target)
        return {"metadata": {"dataset_name": "전국 매출 상위 1000개 기업", "coverage": "unknown", "expected_count": 1000, "verification_status": "unverified", "fiscal_year": None, "accounting_basis": "unknown", "financial_sector": "unknown"}, "companies": []}
    data = json.loads(target.read_text(encoding="utf-8"))
    return _registry(data["companies"], data["metadata"])


def save_registry(registry: dict, path: str | Path) -> Path:
    validated = _registry(registry["companies"], registry["metadata"])
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def list_companies(registry: dict, query: str = "", group: str | None = None) -> list[dict]:
    needle = unicodedata.normalize("NFC", query).casefold()
    return [deepcopy(row) for row in registry["companies"] if
            (group is None or row.get("affiliate") == group) and
            needle in (row["name"] + " " + (row.get("affiliate") or "")).casefold()]


def registry_summary(registry: dict) -> dict:
    meta = registry["metadata"]
    count = len(registry["companies"])
    expected = meta.get("expected_count", 1000)
    return {"dataset_name": meta["dataset_name"], "registered_count": count,
            "expected_count": expected, "coverage": meta["coverage"],
            "coverage_fraction": count / expected, "fiscal_year": meta.get("fiscal_year"),
            "accounting_basis": meta["accounting_basis"], "financial_sector": meta["financial_sector"],
            "verification_status": meta["verification_status"],
            "template_compatibility": "not_verified_by_company_registration"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="출처와 매출 순위 기준을 보존하는 기업 목록 가져오기")
    parser.add_argument("input", nargs="?", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    meta = json.loads(args.metadata.read_text(encoding="utf-8")) if args.metadata else None
    registry = import_registry(args.input, meta) if args.input else load_registry()
    if args.output:
        save_registry(registry, args.output)
    print(json.dumps(registry_summary(registry), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
