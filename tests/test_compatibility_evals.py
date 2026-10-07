import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from pypdf import PdfReader
import pytest

from evals import compatibility


ROOT = Path(__file__).resolve().parents[1]


def job(path, *, usage="blank_form", provenance="synthetic_fixture"):
    return {"id": "test:" + path.name, "path": str(path), "format": path.suffix.lstrip("."),
            "group": "테스트 그룹", "organization": "테스트 회사", "source_url": None,
            "usage": usage, "provenance": provenance}


@pytest.mark.parametrize("suffix", ["docx", "hwpx", "xlsx", "pdf", "pptx"])
def test_synthetic_company_fixture_is_parsed_analyzed_filled_and_not_mutated(suffix):
    item = job(ROOT / f"samples/sample_company_form.{suffix}")
    result = compatibility.check_file(item)
    assert result["status"] == ("pending" if suffix == "xlsx" else "passed")
    assert result["checks"]["source_unmodified"]["status"] == "passed"
    assert result["checks"]["parse"]["status"] == ("pending" if suffix == "xlsx" else "passed")
    if suffix == "xlsx":
        assert result["checks"]["parse"]["reason"] == "uncalculated_formula"
    assert result["checks"]["fill"]["status"] == "passed"
    assert result["checks"]["fill"]["output_validation"]["confirmed_values"] >= 1
    assert result["native_layout_review"] == "pending"


def test_published_report_is_never_filled_and_pdf_sample_scope_is_explicit(monkeypatch):
    item = job(ROOT / "samples/source.pdf", usage="layout_reference", provenance="official_catalog")
    def forbidden_fill(*args, **kwargs):
        raise AssertionError("완성 공개 보고서를 수정하려고 함")
    monkeypatch.setattr(compatibility, "fill_compatible_template", forbidden_fill)
    result = compatibility.check_file(item, max_pdf_pages=1)
    assert result["status"] == "passed"
    assert result["checks"]["fill"]["status"] == "not_applicable"
    assert result["checks"]["parse"]["scope"] == {"kind": "first_pages", "checked_pages": 1, "total_pages": 2}
    summary = compatibility.summarize([result])
    assert summary["sampled_parse_count"] == 1 and summary["full_parse_count"] == 0
    assert summary["fill_passed_count"] == 0


def test_missing_download_and_drm_are_pending_without_bypass(tmp_path):
    missing = job(tmp_path / "missing.pdf")
    result = compatibility.check_file(missing)
    assert result["status"] == "pending"
    blocked = missing | {"catalog_download_status": "blocked_drm"}
    result = compatibility.check_file(blocked)
    assert result["checks"]["access"]["reason"] == "drm_protected"
    assert "parse" not in result["checks"]


def test_catalog_hash_mismatch_is_reported_before_parsing():
    item = job(ROOT / "samples/source.docx") | {"expected_sha256": "0" * 64}
    result = compatibility.check_file(item)
    assert result["status"] == "failed"
    assert result["checks"]["provenance"]["status"] == "failed"
    assert "parse" not in result["checks"]


def test_collect_jobs_keeps_official_reference_separate_from_synthetic_and_unregistered(tmp_path, monkeypatch):
    import templates.catalog as catalog
    (tmp_path / "known.pdf").write_bytes(b"%PDF-not actually read in collection")
    (tmp_path / "unregistered.docx").write_bytes(b"zip fixture")
    entry = {"id": "reference", "filename": "known.pdf", "group": "공식 그룹", "affiliate": "공식 계열사",
             "source_url": "https://example.org/known.pdf", "source_kind": "published_report", "download_status": "verified"}
    monkeypatch.setattr(catalog, "list_public_templates", lambda group=None: [entry])
    calls = []
    monkeypatch.setattr(catalog, "download_public_template", lambda *args: calls.append(args))
    jobs = compatibility.collect_jobs(tmp_path, include_government=False, include_tech=False)
    official = next(item for item in jobs if item["provenance"] == "official_catalog")
    assert official["usage"] == "layout_reference"
    assert official["organization"] == "공식 계열사"
    assert len([item for item in jobs if item["provenance"] == "synthetic_fixture"]) == 6
    assert len([item for item in jobs if item["provenance"] == "unregistered_local"]) == 1
    assert calls == []


def test_timeout_is_pending_and_original_hash_is_checked(monkeypatch):
    item = job(ROOT / "samples/source.docx")
    def expired(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(compatibility.subprocess, "run", expired)
    result = compatibility.check_isolated(item, timeout=1)
    assert result["status"] == "pending"
    assert result["checks"]["execution"]["reason"] == "timeout"
    assert result["checks"]["source_unmodified"]["status"] == "passed"


def test_worker_failure_is_reported_without_assuming_output_success(monkeypatch):
    monkeypatch.setattr(compatibility.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=2))
    result = compatibility.check_isolated(job(ROOT / "samples/source.docx"))
    assert result["status"] == "failed"
    assert result["checks"]["execution"]["returncode"] == 2


def test_one_real_isolated_worker_is_offline_and_uses_current_python():
    result = compatibility.check_isolated(job(ROOT / "samples/sample_company_form.docx"), timeout=15)
    assert result["status"] == "passed"
    assert result["checks"]["fill"]["status"] == "passed"


def test_report_stats_do_not_count_registered_or_synthetic_companies_as_real_checks(monkeypatch):
    a = job(Path("official.pdf"), usage="layout_reference", provenance="official_catalog")
    b = job(Path("fixture.docx"))
    c = job(Path("missing.pdf"), provenance="official_catalog")
    responses = [
        a | {"status": "passed", "checks": {"available": {"status": "passed"}, "parse": {"status": "passed", "scope": {"kind": "first_pages"}}}},
        b | {"status": "passed", "checks": {"available": {"status": "passed"}, "fill": {"status": "passed"}}},
        c | {"status": "pending", "checks": {"available": {"status": "pending"}}},
    ]
    monkeypatch.setattr(compatibility, "check_isolated", lambda item, **kwargs: next(row for row in responses if row["id"] == item["id"]))
    report = compatibility.run_corpus([a, b, c])
    summary = report["summary"]
    assert summary["registered_count"] == 3 and summary["checked_count"] == 2
    assert summary["official_checked_count"] == 1 and summary["synthetic_checked_count"] == 1
    assert summary["official_group_count"] == 1
    assert summary["pending_count"] == 1


@pytest.mark.parametrize("options", [{"workers": 5}, {"timeout": 0}, {"max_pdf_pages": -1}])
def test_invalid_worker_limits_are_rejected(options):
    with pytest.raises(ValueError):
        compatibility.run_corpus([], **options)


def test_static_pdf_fixture_manual_fill_check():
    item = job(ROOT / "samples/sample_static_form.pdf") | {"id": "synthetic:sample_static_form.pdf"}
    result = compatibility.check_file(item)
    assert result["status"] == "passed"
    assert result["checks"]["fill"]["status"] == "passed"


def test_output_validation_detects_missing_filled_values():
    with pytest.raises(ValueError, match="입력값"):
        compatibility._verify_output(ROOT / "samples/sample_company_form.docx", "docx", ["누락된 입력값임"])


def test_verified_registered_profile_resolves_real_blank_form_mapping():
    source = ROOT / "data/public_templates/lotte_proxy_2026.pdf"
    if not source.is_file():
        pytest.skip("공개 원본 corpus는 별도 다운로드함")
    item = job(source, provenance="official_catalog")
    result = compatibility.check_file(item)
    assert result["status"] == "passed"
    assert result["registered_profile"] == "lotte_proxy_2026"
    assert result["checks"]["fill"]["mapping_method"] == "registered_verified_positions"
    assert result["checks"]["fill"]["test_data_only"] is True


def test_government_collection_keeps_change_restricted_forms_read_only(monkeypatch, tmp_path):
    import templates.catalog as catalog
    import templates.government as government
    monkeypatch.setattr(catalog, "list_public_templates", lambda group=None: [])
    entry = {"id": "restricted", "filename": "form.hwpx", "agency": "정부기관", "source_kind": "public_form",
             "resource_kind": "blank_form", "source_url": "https://example.org/form", "notes": "변경금지 원본",
             "download_status": "verified"}
    monkeypatch.setattr(government, "list_government_templates", lambda: [entry])
    jobs = compatibility.collect_jobs(tmp_path, include_samples=False, include_tech=False, include_international=False, include_ra=False, include_business=False, include_office=False)
    assert len(jobs) == 1
    assert jobs[0]["usage"] == "read_only_form"
    assert jobs[0]["provenance"] == "official_government"


def test_public_static_pdf_candidate_does_not_count_as_verified_fill_without_approved_positions(monkeypatch):
    item = job(ROOT / "samples/sample_static_form.pdf", provenance="official_government")
    monkeypatch.setattr(compatibility, "load_form_profile", lambda source: None)
    result = compatibility.check_file(item)
    assert result["status"] == "pending"
    assert result["checks"]["parse"]["status"] == "passed"
    assert result["checks"]["fill"]["status"] == "pending"


def test_tech_collection_separates_startup_forms_and_reference_reports(monkeypatch, tmp_path):
    import templates.catalog as catalog
    import templates.tech as tech
    monkeypatch.setattr(catalog, 'list_public_templates', lambda group=None: [])
    monkeypatch.setattr(tech, 'list_tech_templates', lambda: [dict(id='startup',filename='startup.hwp',company='지원기관',owner='지원기관',resource_kind='blank_form',use_classification='startup_submission',download_status='verified')])
    jobs = compatibility.collect_jobs(tmp_path, include_government=False, include_samples=False, include_international=False, include_ra=False, include_business=False, include_office=False)
    assert len(jobs) == 1 and jobs[0]['provenance'] == 'official_tech'
    assert jobs[0]['company_internal'] is False
    assert jobs[0]['usage'] == 'blank_form' and jobs[0]['use_classification'] == 'startup_submission'


def test_discovery_downloads_are_deduplicated_and_never_count_as_filled(tmp_path):
    for name in ('discovery_a','discovery_b'):
        directory = tmp_path/'government'/name
        directory.mkdir(parents=True)
        (directory/'one.hwp').write_bytes(b'original')
        (directory/'discovery.json').write_text(json.dumps({'documents':[dict(url='https://example.org/form',agency='기관',download_status='verified',filename='one.hwp',sha256='same')],'pages':[]}),encoding='utf-8')
    result = compatibility.discovery_statistics(tmp_path)
    assert result['downloaded_unique_sha256'] == 1
    assert result['fill_verified'] == 0 and result['classification'] == 'unclassified'


def test_hwp_reading_pass_is_separate_from_conversion_for_filling(monkeypatch,tmp_path):
    path = tmp_path/'original.hwp'
    path.write_bytes(b'readonly fixture')
    monkeypatch.setattr(compatibility,'parse_file',lambda path: {'본문':'원자료 본문', '표 목록':[], '페이지/시트 정보':[]})
    result = compatibility.check_file(job(path))
    assert result['checks']['parse']['status'] == 'passed'
    assert result['checks']['fill']['status'] == 'pending'
    assert 'HWPX' in result['checks']['fill']['reason']
    assert compatibility.summarize([result])['hwp_parse_passed_count'] == 1


def test_international_collection_preserves_public_submission_scope(monkeypatch, tmp_path):
    import templates.catalog as catalog
    import templates.international as international
    monkeypatch.setattr(catalog, 'list_public_templates', lambda group=None: [])
    monkeypatch.setattr(international, 'list_international_templates', lambda: [dict(
        id='foreign-form', filename='foreign.docx', company='기업', source_url='https://example.org/form',
        source_kind='public_form', resource_kind='blank_form', use_classification='company_public_submission',
        sha256='a'*64, download_status='verified')])
    jobs = compatibility.collect_jobs(tmp_path, include_government=False, include_tech=False, include_samples=False, include_ra=False, include_business=False, include_office=False)
    assert len(jobs) == 1 and jobs[0]['provenance'] == 'official_international'
    assert jobs[0]['company_internal'] is False
    assert jobs[0]['usage'] == 'blank_form'
    result = jobs[0] | {'status': 'pending', 'checks': {'available': {'status': 'passed'}}}
    summary = compatibility.summarize([result])
    assert summary['international_checked_count'] == summary['international_company_count'] == 1
    assert summary['fill_passed_count'] == 0


def test_ra_collection_distinguishes_unique_forms_and_registered_filling(monkeypatch, tmp_path):
    import templates.catalog as catalog
    import templates.ra as ra
    monkeypatch.setattr(catalog, 'list_public_templates', lambda group=None: [])
    monkeypatch.setattr(ra, 'list_ra_templates', lambda: [dict(
        id='ra-one', filename='ra.pdf', publisher='식약처', source_url='https://www.law.go.kr/form',
        source_kind='public_form', resource_kind='blank_form', use_classification='regulatory_submission',
        sha256='a'*64, download_status='verified', workflows=['clinical_trial'])])
    jobs = compatibility.collect_jobs(tmp_path, include_government=False, include_tech=False,
                                      include_samples=False, include_international=False, include_business=False, include_office=False)
    assert len(jobs) == 1 and jobs[0]['provenance'] == 'official_ra'
    assert jobs[0]['workflows'] == ['clinical_trial']
    result = jobs[0] | {'status': 'pending', 'checks': {'available': {'status': 'passed'}}}
    summary = compatibility.summarize([result])
    assert summary['ra_checked_count'] == 1 and summary['ra_profile_fill_count'] == 0
