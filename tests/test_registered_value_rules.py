"""공식 공개 양식의 관찰된 숫자/날짜/관계 규칙. 제출 적합성 인증이 아님."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from pypdf import PdfReader
import pytest

from agent.output_check import verify_output
from templates import TemplateError, fill_compatible_template
from templates.value_rules import inspect_form_values, validate_rule_profile


ROOT = Path(__file__).resolve().parents[1]
PROFILE_IDS = (
    "ra_law_form_81_pdf", "ra_law_form_82_pdf", "ra_law_form_32_2_pdf",
    "ra_law_form_32_pdf", "ra_law_form_57_2_pdf", "ra_law_form_6_pdf",
    "ra_law_form_7_2_pdf", "ra_law_rmp_outline_pdf",
    "history-competition-2026", "moel-research-2026",
)


def profile(entry_id):
    return json.loads((ROOT / "templates/profiles" / f"{entry_id}.json").read_text(encoding="utf-8"))


def source(registered):
    candidates = ([ROOT / registered["source_path"]] if registered.get("source_path") else [])
    candidates += [ROOT / "data/public_templates" / directory / registered["source_filename"]
                   for directory in ("ra", "government", "business", "")]
    path = next((path for path in candidates if path.is_file()), None)
    if path is None:
        pytest.skip("공식 원본 corpus를 별도로 확보해야 함; 네트워크 다운로드하지 않음")
    assert sha256(path.read_bytes()).hexdigest() == registered["source_sha256"]
    return path


@pytest.mark.parametrize("entry_id", PROFILE_IDS)
def test_registered_rule_schema_and_fake_qa_values_are_valid(entry_id):
    registered = profile(entry_id)
    validate_rule_profile(registered)
    assert inspect_form_values(registered["demo_values"], registered) == []
    evidence = registered["validation_evidence"]
    assert evidence["source_sha256"] == registered["source_sha256"]
    assert evidence["source_filename"] == registered["source_filename"]
    rules = {field["value_key"] for field in registered["fields"] if field.get("validation")}
    assert set(evidence["fields"]) == rules
    assert all(item["printed_labels"] and item["location"] for item in evidence["fields"].values())
    assert len(evidence["relations"]) == len(registered.get("constraints", {}).get("relations", []))
    assert registered["submission_ready"] is False


def test_registered_scope_is_exact_and_does_not_infer_numeric_identifiers_or_month_only_dates():
    selected = [profile(entry_id) for entry_id in PROFILE_IDS]
    assert sum(sum("validation" in field for field in item["fields"]) for item in selected) == 92
    assert sum(len(item.get("constraints", {}).get("relations", [])) for item in selected) == 22
    for registered in selected:
        for field in registered["fields"]:
            if re.search("등록번호|식별번호|전화번호|연락처|이메일|제품명", field["label"]):
                assert "validation" not in field
    kotra = profile("business_kotra_salesforce_2026")
    for field in kotra["fields"]:
        if field["value_key"] in {"한국법인 설립년도", "출시일", "사업자번호"}:
            assert "validation" not in field
    # 원문 월 단위 예시·등록번호를 온전한 날짜·일반 숫자로 바꾸지 않음.
    assert not any(relation["kind"] == "date_order" for item in selected
                   for relation in item.get("constraints", {}).get("relations", []))


@pytest.mark.parametrize("entry_id", PROFILE_IDS[:8])
def test_each_pdf_rule_has_real_page_label_and_exact_printed_unit_evidence(entry_id):
    registered = profile(entry_id)
    raw = source(registered)
    before = raw.read_bytes()
    reader = PdfReader(raw)
    for field in registered["fields"]:
        rule = field.get("validation")
        if not rule:
            continue
        evidence = registered["validation_evidence"]["fields"][field["value_key"]]
        assert evidence["page"] == field["page"]
        text = re.sub(r"\s+", "", reader.pages[field["page"] - 1].extract_text())
        assert all(re.sub(r"\s+", "", label) in text for label in evidence["printed_labels"])
        if rule.get("unit_location") == "label":
            assert rule["unit"] in text
            assert rule["unit"] not in registered["demo_values"][field["value_key"]]
    assert raw.read_bytes() == before


@pytest.mark.parametrize("entry_id,key,bad", [
    ("ra_law_form_81_pdf", "제조소 작업소 면적", "-1"),
    ("ra_law_form_81_pdf", "제조소 작업소 면적", "12.50㎡"),
    ("ra_law_form_81_pdf", "작업인원 제조부서", "1.5"),
    ("ra_law_form_81_pdf", "신청 월", "13"),
    ("ra_law_form_82_pdf", "신청 일", "32"),
    ("ra_law_form_32_2_pdf", "시험책임자 의사 전체 인원", "3명"),
    ("ra_law_form_32_2_pdf", "의료기관 병상수", "NaN"),
    ("ra_law_form_32_2_pdf", "IRB 당해년도 승인 의뢰자주도 1상 시험건수", "-1"),
    ("ra_law_form_32_pdf", "보고서 제출일", "2026-02-30"),
    ("ra_law_form_32_pdf", "정기보고 차수", "0"),
    ("ra_law_form_57_2_pdf", "신청인 생년월일", "TEST-00"),
    ("history-competition-2026", "학년", "시험"),
    ("moel-research-2026", "연구비 신청액", "0(시험)"),
])
def test_observed_fields_reject_invalid_values(entry_id, key, bad):
    registered = profile(entry_id)
    values = registered["demo_values"] | {key: bad}
    issues = inspect_form_values(values, registered)
    assert issues
    assert any(issue["field"] == key for issue in issues)
    assert all(issue["severity"] == "error" and issue["code"] and issue["message"]
               and isinstance(issue["line"], int) for issue in issues)
    assert values[key] == bad  # 검사기가 숫자를 정정하거나 날짜를 추정하지 않음.


@pytest.mark.parametrize("entry_id,total,bad", [
    ("ra_law_form_81_pdf", "제조소 합계 면적", "31.00"),
    ("ra_law_form_82_pdf", "제조소 합계 면적", "31.00"),
    ("ra_law_form_81_pdf", "작업인원 합계", "8"),
    ("ra_law_form_82_pdf", "작업인원 합계", "8"),
])
def test_real_gmp_total_is_checked_without_automatic_recalculation(entry_id, total, bad):
    registered = profile(entry_id)
    values = registered["demo_values"] | {total: bad}
    assert any(issue["field"] == total for issue in inspect_form_values(values, registered))
    assert values[total] == bad


@pytest.mark.parametrize("entry_id", ["ra_law_form_81_pdf", "ra_law_form_82_pdf"])
@pytest.mark.parametrize("year,month,day", [("2026", "2", "30"), ("2025", "2", "29")])
def test_split_calendar_rejects_invalid_dates_even_when_scalar_ranges_pass(entry_id, year, month, day):
    registered = profile(entry_id)
    values = registered["demo_values"] | {"신청 연도": year, "신청 월": month, "신청 일": day}
    scalar_only = deepcopy(registered)
    scalar_only["constraints"]["relations"] = [item for item in scalar_only["constraints"]["relations"]
                                                if item["kind"] != "calendar_date"]
    assert inspect_form_values(values, scalar_only) == []
    before = deepcopy(values)
    assert inspect_form_values(values, registered)
    assert values == before


@pytest.mark.parametrize("entry_id", ["ra_law_form_81_pdf", "ra_law_form_82_pdf"])
def test_partial_split_calendar_cannot_treat_missing_day_as_another_date(entry_id):
    registered = profile(entry_id)
    values = registered["demo_values"].copy()
    values.pop("신청 일")
    before = deepcopy(values)
    assert inspect_form_values(values, registered)
    assert values == before and "신청 일" not in values


@pytest.mark.parametrize("entry_id", ["ra_law_form_81_pdf", "ra_law_form_82_pdf"])
def test_actual_leap_day_fills_all_three_cells_and_passes_independent_check(entry_id, tmp_path):
    registered = profile(entry_id)
    values = registered["demo_values"] | {"신청 연도": "2024", "신청 월": "2", "신청 일": "29"}
    assert inspect_form_values(values, registered) == []
    original = source(registered)
    before = original.read_bytes()
    output = fill_compatible_template(original, values, tmp_path / original.name, profile=registered)
    assert verify_output(original, output, values, profile=registered)["status"] == "passed"
    assert original.read_bytes() == before


EDUCATION_PAIRS = tuple((item["left"], item["right"]) for item in
                        profile("ra_law_form_32_2_pdf")["constraints"]["relations"])


@pytest.mark.parametrize("education,total", EDUCATION_PAIRS)
def test_each_real_training_subset_cannot_exceed_its_own_group(education, total):
    registered = profile("ra_law_form_32_2_pdf")
    values = registered["demo_values"] | {education: "4", total: "3"}
    assert any(issue["field"] in {education, total} for issue in inspect_form_values(values, registered))
    assert inspect_form_values(registered["demo_values"] | {education: "3", total: "3"}, registered) == []


def test_same_unit_sum_never_converts_square_metres_between_spellings():
    registered = deepcopy(profile("ra_law_form_81_pdf"))
    field = next(field for field in registered["fields"] if field["value_key"] == "제조소 작업소 면적")
    assert field["validation"]["unit"] == "㎡"
    field["validation"]["unit"] = "m²"
    with pytest.raises(ValueError):
        validate_rule_profile(registered)


def test_unvalidated_report_text_is_not_parsed_as_a_single_numeric_cell():
    registered = profile("moel-research-2026")
    values = registered["demo_values"] | {"연구과제명": "2026년 자료 3건 검토함 [S1]"}
    assert inspect_form_values(values, registered) == []


@pytest.mark.parametrize("entry_id", ["ra_law_form_81_pdf", "ra_law_form_32_2_pdf", "ra_law_form_32_pdf"])
def test_valid_actual_values_fill_preserve_source_and_pass_independent_output_check(entry_id, tmp_path):
    registered = profile(entry_id)
    original = source(registered)
    before = original.read_bytes()
    output = fill_compatible_template(original, registered["demo_values"],
                                      tmp_path / original.name, profile=registered)
    assert verify_output(original, output, registered["demo_values"], profile=registered)["status"] == "passed"
    assert original.read_bytes() == before


def test_bad_actual_total_cannot_replace_a_previous_output(tmp_path):
    registered = profile("ra_law_form_81_pdf")
    original = source(registered)
    before = original.read_bytes()
    output = tmp_path / "previous.pdf"
    output.write_bytes(b"PREVIOUS USER OUTPUT")
    values = registered["demo_values"] | {"제조소 합계 면적": "31.00"}
    with pytest.raises(TemplateError):
        fill_compatible_template(original, values, output, profile=registered)
    assert output.read_bytes() == b"PREVIOUS USER OUTPUT"
    assert original.read_bytes() == before
