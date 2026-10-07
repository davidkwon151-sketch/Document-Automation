"""Offline checks for two annotated official Korean PDF snapshots.

Ignored source binaries are optional outside the collection workspace. Metadata
tests always run; actual source checks skip explicitly when a PDF is absent.
"""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from evals.ra_public import normalized, read_source, validate_source_parser


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evals/ra_additional_sources.json"
RECORDS = json.loads(MANIFEST.read_text(encoding="utf-8"))["sources"]


def available(record):
    path = ROOT / record["path"]
    if not path.is_file():
        pytest.skip("공식 PDF 로컬 스냅샷 없음; 실제 원문 검증을 성공으로 대체하지 않음")
    return path


def test_manifest_denominators_and_official_source_snapshots():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["source_count"] == len(RECORDS) == 2
    assert manifest["company_count"] == len({r["company"]["name"] for r in RECORDS}) == 2
    assert manifest["fact_count"] == sum(len(r["facts"]) for r in RECORDS) == 9
    assert len({r["sha256"] for r in RECORDS}) == 2
    assert manifest["downloaded_pdf_count"] == len(RECORDS) + len(manifest["excluded_sources"]) == 8
    assert manifest["excluded_pdf_count"] == len(manifest["excluded_sources"]) == 6
    assert manifest["model_training_performed"] is False
    assert manifest["live_llm_evaluated"] is False
    for record in RECORDS:
        assert record["jurisdiction"] == "KR"
        assert record["scope"]["korean_authorisation_verified"] is False
        assert record["scope"]["full_submission_ready"] is False
        assert record["current_version_verified"] is False
        assert record["currentness"]["latest_revision_verified"] is False
        assert record["pdf_access"]["protection_bypass_performed"] is False
        assert record["pdf_access"]["general_text_extraction_permitted"] is True
        assert record["final_url"] == record["source_url"]
        for key in ("source_page", "source_url", "final_url"):
            url = urlsplit(record[key])
            assert url.scheme == "https"
            domain = record["company"]["official_domain"]
            assert url.hostname == domain or url.hostname.endswith("." + domain)


def test_annotations_keep_whole_selected_values_and_single_variant():
    for record in RECORDS:
        assert 3 <= len(record["facts"]) <= 5
        assert len({fact["field_key"] for fact in record["facts"]}) == len(record["facts"])
        names = [f["value"] for f in record["facts"] if f["role"] == "product_name"]
        assert names == [record["product_variant"]]
        for fact in record["facts"]:
            assert fact["value"] == fact["exact_quote"]
            assert fact["product_variant"] == record["product_variant"]
            assert fact["complete_selected_paragraph"] is True
        storage = next(f for f in record["facts"] if f["field_key"] == "저장방법 및 유효기간")
        assert storage["missing_expiry_duration"] is True


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["id"])
def test_actual_whole_quotes_roles_pages_and_source_immutability(record):
    path = available(record)
    before = sha256(path.read_bytes()).hexdigest()
    facts, sources, document = read_source(record, ROOT)
    assert before == record["sha256"] == sha256(path.read_bytes()).hexdigest()
    assert len(facts) == len(sources) == len(record["facts"])
    assert {source["page"] for source in sources} == {1, 2}
    assert len({source["source_id"] for source in sources}) == len(sources)
    assert document["document_sha256"] == before
    assert all(fact["value"] == annotation["value"] for fact, annotation in zip(facts, record["facts"]))


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["id"])
def test_actual_m1_full_pages_match_independent_annotations(record, monkeypatch):
    available(record)
    from llm.client import LLMClient

    def forbidden(*args, **kwargs):
        pytest.fail("실제 PDF 파서 검증은 외부 LLM을 호출하면 안 됨")

    monkeypatch.setattr(LLMClient, "generate_json", forbidden)
    result = validate_source_parser(record, ROOT)
    assert result["passed"] is True
    assert result["page_count"] == record["page_count"] == 2
    assert result["fact_count"] == len(record["facts"])
    assert result["original_unchanged"] is True


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["id"])
def test_changed_amount_is_not_blessed_by_valid_source_id(record):
    available(record)
    changed = deepcopy(record)
    fact = next(f for f in changed["facts"] if f["field_key"] == "원료약품 및 분량")
    old, new = ("5.46mg", "54.6mg") if "5.46mg" in fact["value"] else ("5.20mg", "52.0mg")
    fact["value"] = fact["value"].replace(old, new)
    fact["exact_quote"] = fact["exact_quote"].replace(old, new)
    with pytest.raises(ValueError, match="전체 원문"):
        read_source(changed, ROOT)


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["id"])
def test_changed_source_sha_and_wrong_quote_page_are_blocked(record):
    available(record)
    changed = deepcopy(record)
    changed["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        read_source(changed, ROOT)
    changed = deepcopy(record)
    composition = next(f for f in changed["facts"] if f["field_key"] == "원료약품 및 분량")
    composition["page"] = 2
    with pytest.raises(ValueError, match="전체 원문"):
        read_source(changed, ROOT)


def test_publisher_is_not_inferred_as_manufacturer_or_another_strength():
    record = next(r for r in RECORDS if r["company"]["role"] == "document_publisher")
    available(record)
    changed = deepcopy(record)
    changed["company"]["role"] = "manufacturer"
    with pytest.raises(ValueError, match="제조·수입·판매 역할"):
        read_source(changed, ROOT)
    changed = deepcopy(record)
    changed["product_variant"] = "로수로드정 10mg"
    with pytest.raises(ValueError, match="선택 제형"):
        read_source(changed, ROOT)
    composition = next(f for f in record["facts"] if f["field_key"] == "원료약품 및 분량")
    assert "<로수로드정10mg>" not in normalized(composition["value"])
    assert "<로수로드정20mg>" not in normalized(composition["value"])


def test_actimin_dose_keeps_adjustment_and_per_ml_not_per_ampoule():
    record = next(r for r in RECORDS if r["company"]["role"] == "manufacturer")
    dose = next(f for f in record["facts"] if f["field_key"] == "용법 용량")
    assert "증상에따라적절히증감한다." in normalized(dose["value"])
    composition = next(f for f in record["facts"] if f["field_key"] == "원료약품 및 분량")
    assert composition["unit_scope"]["denominator"] == "1mL"
    assert composition["unit_scope"]["ampoule_total_mass_calculated"] is False
