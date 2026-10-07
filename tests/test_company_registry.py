from copy import deepcopy
import csv
import json
from time import perf_counter

import pytest

from templates.company_registry import RegistryError, import_registry, list_companies, load_registry, registry_summary, save_registry


@pytest.fixture
def metadata():
    return {"dataset_name": "테스트 기업 순위", "source_url": "https://official.example/ranking", "source_owner": "테스트 원문 소유자",
            "fiscal_year": 2025, "accounting_basis": "separate", "financial_sector": "included",
            "universe": "테스트에서 합성한 1000개 기업", "ranking_definition": "별도 FY2025 매출 내림차순 테스트 자료",
            "revenue_unit": "KRW_million", "verification_status": "source_checked", "checked_at": "2026-10-02T10:00:00+00:00",
            "coverage": "partial", "expected_count": 1000}


def company(rank=1, name="테스트기업", revenue="1000"):
    return {"rank": rank, "name": name, "revenue": revenue, "year": 2025,
            "source_url": "https://official.example/ranking", "affiliate": "테스트그룹", "source_location": f"표1 행{rank}"}


def write_json(tmp_path, rows, metadata=None):
    path = tmp_path / "companies.json"
    path.write_text(json.dumps({"metadata": metadata, "companies": rows} if metadata else rows, ensure_ascii=False), encoding="utf-8")
    return path


def test_real_public_partial_registry_does_not_claim_1000_or_fy2025():
    data = load_registry()
    summary = registry_summary(data)
    assert summary["registered_count"] == 38 and summary["coverage"] == "partial"
    assert summary["coverage_fraction"] == .038 and summary["fiscal_year"] == 2024
    assert summary["accounting_basis"] == "unknown" and summary["financial_sector"] == "unknown"
    assert data["metadata"]["latest_fiscal_year_coverage"]["2025"] == "unknown"
    assert data["companies"][0]["rank"] == 33
    assert data["companies"][0]["revenue"] == "18617622"
    assert data["companies"][-1]["rank"] == 986
    assert all(row["affiliate"] is None and row["source_location"] and row["source_document_sha256"] for row in data["companies"])
    assert summary["template_compatibility"] == "not_verified_by_company_registration"


def test_json_preserves_exact_decimal_source_and_normalizes_korean(metadata, tmp_path):
    row = company(revenue="123.456789123456789")
    row["name"] = "\u1100\u1161\u11bc"
    data = import_registry(write_json(tmp_path, [row], metadata))
    assert data["companies"][0]["name"] == "강"
    assert data["companies"][0]["revenue"] == row["revenue"]
    assert data["companies"][0]["source_url"] == row["source_url"]
    assert data["metadata"]["verification_origin"] == "provided_by_importer"
    assert len(data["metadata"]["import_file_sha256"]) == 64
    saved = save_registry(data, tmp_path / "saved.json")
    assert load_registry(saved) == data


def test_csv_utf8_bom_and_header_contract(metadata, tmp_path):
    path = tmp_path / "companies.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(company()))
        writer.writeheader()
        writer.writerow(company())
    data = import_registry(path, metadata)
    assert data["companies"][0]["rank"] == 1
    with pytest.raises(RegistryError, match="메타데이터"):
        import_registry(path)


@pytest.mark.parametrize("mutation", [{"rank": 0}, {"rank": 1001}, {"rank": True}, {"name": " "}, {"year": 2024}, {"revenue": "NaN"}, {"revenue": "Infinity"}, {"revenue": "-1"}, {"source_url": "http://official.example/ranking"}, {"source_url": "https://user:password@official.example/ranking"}])
def test_bad_row_rejects_entire_import_without_silent_loss(metadata, tmp_path, mutation):
    rows = [company(), {**company(2, "두번째기업", "900"), **mutation}]
    with pytest.raises(RegistryError):
        import_registry(write_json(tmp_path, rows), metadata)


@pytest.mark.parametrize("rows", [[company(), company(1, "다른기업", "900")], [company(name="㈜같은기업"), company(2, "같은기업(주)", "900")]])
def test_rank_or_corporate_name_variants_duplicate_rejected(metadata, tmp_path, rows):
    with pytest.raises(RegistryError, match="중복"):
        import_registry(write_json(tmp_path, rows), metadata)


def test_revenue_order_must_agree_with_ranks(metadata, tmp_path):
    with pytest.raises(RegistryError, match="내림차순"):
        import_registry(write_json(tmp_path, [company(), company(2, "다른기업", "2000")]), metadata)


@pytest.mark.parametrize("key", ["accounting_basis", "financial_sector", "ranking_definition", "fiscal_year", "source_owner", "source_url"])
def test_ranking_definition_fields_required(metadata, tmp_path, key):
    del metadata[key]
    with pytest.raises(RegistryError, match="메타데이터"):
        import_registry(write_json(tmp_path, [company()]), metadata)


def test_complete_claim_requires_full_rows_and_confirmed_basis(metadata, tmp_path):
    metadata["coverage"] = "complete"
    with pytest.raises(RegistryError, match="전체 순위"):
        import_registry(write_json(tmp_path, [company()]), metadata)
    metadata["accounting_basis"] = "unknown"
    with pytest.raises(RegistryError, match="기준/출처"):
        import_registry(write_json(tmp_path, [company()]), metadata)


def test_1000_companies_import_and_search_with_bounded_runtime(metadata, tmp_path):
    metadata["coverage"] = "complete"
    rows = [company(rank, f"합성기업{rank:04}", str(1001-rank)) for rank in range(1, 1001)]
    before = perf_counter()
    data = import_registry(write_json(tmp_path, rows, metadata))
    summary = registry_summary(data)
    assert summary["registered_count"] == 1000 and summary["coverage_fraction"] == 1
    found = list_companies(data, "합성기업0500", group="테스트그룹")
    assert len(found) == 1 and found[0]["rank"] == 500
    found[0]["name"] = "mutated"
    assert data["companies"][499]["name"] == "합성기업0500"
    assert perf_counter() - before < 3


def test_missing_default_data_is_unknown_not_fabricated(monkeypatch, tmp_path):
    import templates.company_registry as module
    monkeypatch.setattr(module, "DEFAULT_REGISTRY", tmp_path / "missing.json")
    data = load_registry()
    assert data["companies"] == [] and registry_summary(data)["coverage"] == "unknown"
    with pytest.raises(FileNotFoundError):
        load_registry(tmp_path / "explicit_missing.json")
