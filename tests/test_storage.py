from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas import NumericEvidence, ReportRecord, SourceRecord
from app.storage import LocalStore, load_record, save_record


def test_source_report_roundtrip_keeps_numeric_provenance(tmp_path):
    store = LocalStore(tmp_path)
    source = SourceRecord(source_id="s1", filename="매출.xlsx", text="2026년 매출 100.50만원", location="실적!A2", sheet="실적")
    store.save_source(source)
    number = NumericEvidence(value=Decimal("100.50"), unit="만원", as_of="2026", source_id="s1", source_location="실적!A2")
    report = ReportRecord(instruction="매출 보고", template_id="result", source_ids=["s1"], numbers=[number], placeholders={"본문": "□ 매출 100.50만원임 [s1]"})
    store.save_report(report)
    assert store.load_source("s1") == source
    assert store.load_report(report.report_id) == report
    assert "매출" in (tmp_path / "reports" / f"{report.report_id}.json").read_text(encoding="utf-8")


def test_rejects_missing_or_mismatched_source(tmp_path):
    store = LocalStore(tmp_path)
    report = ReportRecord(instruction="보고", template_id="result", source_ids=["s1"])
    with pytest.raises(ValueError, match="저장되지"):
        store.save_report(report)
    store.save_source(SourceRecord(source_id="s1", filename="a.docx", text="100원", location="문단 1"))
    report.numbers = [NumericEvidence(value=100, unit="원", as_of="2026", source_id="s1", source_location="문단 2")]
    with pytest.raises(ValueError, match="위치"):
        store.save_report(report)


def test_numeric_sources_required_and_path_traversal_blocked(tmp_path):
    number = NumericEvidence(value=100, unit="원", as_of="2026", source_id="s1", source_location="문단 1")
    with pytest.raises(ValidationError, match="출처"):
        ReportRecord(instruction="보고", template_id="result", numbers=[number])
    with pytest.raises(ValueError, match="ID"):
        LocalStore(tmp_path).load_source("../escape")
    with pytest.raises(ValidationError):
        SourceRecord(source_id="../escape", filename="a", text="", location="1")


def test_calculated_numbers_keep_formula_input_sources():
    number = NumericEvidence(value=200, unit="원", as_of="2026", source_id="s1", source_location="문단 1", formula="100 * 2")
    with pytest.raises(ValidationError, match="입력 원자료"):
        ReportRecord(instruction="보고", template_id="result", source_ids=["s1"], numbers=[number])
    number.input_source_ids = ["s1"]
    assert ReportRecord(instruction="보고", template_id="result", source_ids=["s1"], numbers=[number]).numbers[0].formula == "100 * 2"


def test_pipeline_record_preserves_originals_and_sources(tmp_path):
    record = {"instruction": "결과보고", "originals": ["data/run/inputs/매출.xlsx"],
              "sources": [{"source_id": "s1", "filename": "매출.xlsx", "location": "실적!A2", "text": "100만원"}],
              "draft": {"본문": "□ 매출 100만원임 [s1]"}}
    record_id = save_record(record, tmp_path)
    assert load_record(record_id, tmp_path) == record
    with pytest.raises(ValueError, match="ID"):
        load_record("../../bad", tmp_path)
    with pytest.raises(ValueError, match="dict"):
        save_record([], tmp_path)


def test_native_preview_bytes_are_transient_and_verification_is_persisted(tmp_path):
    from hashlib import sha256

    preview = b'%PDF-private-preview'
    metadata = {'docx': {'status': 'warning', 'pdf_sha256': sha256(preview).hexdigest()}}
    record = {'draft': {'본문': '검수됨'}, 'native_output_verification': metadata,
              '_native_preview_bytes': {'docx': preview}}
    loaded = load_record(save_record(record, tmp_path), tmp_path)
    assert loaded == {'draft': record['draft'], 'native_output_verification': metadata}
    assert record['_native_preview_bytes']['docx'] is preview
    assert 'private-preview' not in next((tmp_path / 'records').glob('*.json')).read_text(encoding='utf-8')
