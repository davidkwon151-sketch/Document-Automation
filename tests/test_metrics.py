from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import time
import unicodedata

import pytest

from agent.metrics import finalize_metrics, record_rejection, record_revision, record_rework, start_metrics, summarize_metrics

START = datetime(2026, 10, 3, 1, tzinfo=timezone.utc)


def draft(body="가나다라"):
    return {"제목": "제목", "요약": "요약", "본문": body}


def test_unchanged_draft_zero_ratio_and_utc_elapsed():
    local_start = START.astimezone(timezone(timedelta(hours=9)))
    metrics = start_metrics(draft(), now=local_start)
    result = finalize_metrics(metrics, draft(), now=START + timedelta(seconds=125.25))
    assert result["draft_started_at"] == START.isoformat()
    assert result["elapsed_seconds"] == 125.25
    assert result["user_edit_ratio"] == 0
    assert result["revision_count"] == 0
    assert metrics["finalized_at"] is None


@pytest.mark.parametrize("before,after,edits,length", [("가나다", "가나다라", 1, 4), ("가나다라", "가다라", 1, 4), ("가나다라", "가나마라", 1, 4), ("가나다", "라마바", 3, 3)])
def test_character_insert_delete_replace_ratios(before, after, edits, length):
    metrics = start_metrics(draft(before), now=START)
    result = record_revision(metrics, draft(after), now=START + timedelta(seconds=1))
    assert result["edit_counts"]["본문"] == edits
    assert result["field_edit_ratios"]["본문"] == edits / length
    assert result["user_edit_ratio"] == edits / (length + 4)
    assert result["baseline_draft"] == draft(before)
    assert result["revision_count"] == 1


def test_unicode_line_endings_and_trailing_whitespace_are_normalized():
    baseline = draft("가나다\r\n보고함 \t")
    equivalent = draft(unicodedata.normalize("NFD", "가나다\n보고함"))
    metrics = start_metrics(baseline, now=START)
    result = record_revision(metrics, equivalent, now=START + timedelta(seconds=1))
    assert result["user_edit_ratio"] == 0
    assert result["revision_count"] == 0
    assert result["baseline_draft"]["본문"] == "가나다\n보고함"
    assert baseline["본문"].endswith(" \t")


def test_overall_ratio_is_weighted_and_dynamic_placeholder_is_included():
    baseline = dict(draft("가" * 1000), 담당자="홍길동")
    changed = dict(baseline, 제목="개정", 담당자="홍길순")
    summary = summarize_metrics(start_metrics(baseline, now=START), changed)
    assert summary["field_edit_ratios"]["제목"] == 1.0
    assert summary["field_edit_ratios"]["담당자"] == pytest.approx(1 / 3)
    assert summary["user_edit_ratio"] == pytest.approx(3 / 1007)


def test_baseline_events_and_inputs_are_immutable():
    baseline = draft()
    metrics = start_metrics(baseline, now=START)
    snapshot = deepcopy(metrics)
    changed = draft("수정본문")
    result = record_revision(metrics, changed, now=START + timedelta(seconds=1))
    changed["본문"] = "외부변경"
    result["events"][0]["draft"]["본문"] = "외부변경2"
    assert metrics == snapshot
    assert result["baseline_draft"] == baseline
    assert result["latest_draft"]["본문"] == "수정본문"


def test_rejections_and_reworks_are_explicit_separate_counts():
    metrics = start_metrics(draft(), now=START)
    rejected = record_rejection(metrics, "수치 확인", now=START + timedelta(seconds=1))
    worked = record_rework(rejected, now=START + timedelta(seconds=2))
    assert worked["rejection_count"] == worked["rework_count"] == 1
    assert rejected["rework_count"] == 0
    assert metrics["rejection_count"] == 0
    assert worked["user_edit_ratio"] == 0
    assert worked["revision_count"] == 0
    assert worked["events"][1]["reason"] == "수치 확인"


def test_finalize_compares_unsaved_preview_and_repeated_finalize_is_idempotent():
    metrics = start_metrics(draft(), now=START)
    changed = draft("가나마라")
    final = finalize_metrics(metrics, changed, now=START + timedelta(seconds=10))
    repeated = finalize_metrics(final, changed, now=START + timedelta(seconds=20))
    assert repeated == final
    assert final["revision_count"] == 1
    assert final["elapsed_seconds"] == 10
    assert final["user_edit_ratio"] == 1 / 8
    assert [event["kind"] for event in final["events"]].count("finalized") == 1


def test_edit_after_finalization_reopens_and_second_finalize_keeps_original_start():
    metrics = start_metrics(draft(), now=START)
    final = finalize_metrics(metrics, draft(), now=START + timedelta(seconds=10))
    revised = record_revision(final, draft("가나마라"), now=START + timedelta(seconds=20))
    assert revised["finalized_at"] is None and revised["elapsed_seconds"] is None
    assert revised["events"][-1]["reopened"] is True
    second = finalize_metrics(revised, draft("가나마라"), now=START + timedelta(seconds=30))
    assert second["elapsed_seconds"] == 30
    assert second["baseline_draft"] == draft()
    assert final["elapsed_seconds"] == 10


@pytest.mark.parametrize("action", [record_rework, record_rejection])
def test_rejection_or_rework_invalidates_finalization(action):
    final = finalize_metrics(start_metrics(draft(), now=START), draft(), now=START + timedelta(seconds=10))
    changed = action(final, now=START + timedelta(seconds=20))
    assert changed["finalized_at"] is None
    assert changed["elapsed_seconds"] is None


def test_preview_does_not_mutate_or_record_revision():
    metrics = start_metrics(draft(), now=START)
    snapshot = deepcopy(metrics)
    summary = summarize_metrics(metrics, draft("가나마라"))
    assert summary["user_edit_ratio"] == 1 / 8
    assert summary["revision_count"] == 0
    assert metrics == snapshot


def test_changed_unsaved_preview_does_not_show_stale_finalization():
    final = finalize_metrics(start_metrics(draft(), now=START), draft(), now=START + timedelta(seconds=10))
    summary = summarize_metrics(final, draft("가나마라"))
    assert summary["finalized_at"] is None
    assert summary["elapsed_seconds"] is None
    assert final["elapsed_seconds"] == 10


def test_tampered_finalization_time_is_rejected():
    final = finalize_metrics(start_metrics(draft(), now=START), draft(), now=START + timedelta(seconds=10))
    final["finalized_at"] = (START + timedelta(seconds=5)).isoformat()
    with pytest.raises(ValueError, match="마지막 확정"):
        summarize_metrics(final)


def test_backwards_naive_or_invalid_times_and_empty_drafts_rejected():
    metrics = start_metrics(draft(), now=START)
    updated = record_rework(metrics, now=START + timedelta(seconds=5))
    with pytest.raises(ValueError, match="빠를"):
        finalize_metrics(updated, draft(), now=START + timedelta(seconds=4))
    with pytest.raises(ValueError, match="시간대"):
        start_metrics(draft(), now=START.replace(tzinfo=None))
    with pytest.raises(ValueError, match="ISO"):
        start_metrics(draft(), now="bad-date")
    with pytest.raises(ValueError, match="비어"):
        start_metrics(draft("  "), now=START)
    with pytest.raises(ValueError, match="문자열"):
        record_rejection(metrics, reason=123, now=START)


def test_long_body_local_change_exact_and_whole_rewrite_bounded():
    baseline = draft("보고함 " * 20_000)
    metrics = start_metrics(baseline, now=START)
    changed = draft(baseline["본문"][:40_000] + "추가" + baseline["본문"][40_000:])
    started = time.perf_counter()
    exact = summarize_metrics(metrics, changed)
    approximate = summarize_metrics(metrics, draft("수정임 " * 20_000))
    assert time.perf_counter() - started < 3
    assert exact["edit_counts"]["본문"] == 2
    assert not exact["edit_ratio_is_approximate"]
    assert approximate["edit_ratio_is_approximate"]
    assert 0 <= approximate["user_edit_ratio"] <= 1


def test_json_roundtrip_preserves_baseline_and_metrics(tmp_path):
    from app.storage import load_record, save_record

    metrics = finalize_metrics(start_metrics(draft(), now=START), draft("가나마라"), now=START + timedelta(seconds=10))
    record_id = save_record({"metrics": metrics}, tmp_path)
    loaded = load_record(record_id, tmp_path)["metrics"]
    assert loaded == metrics
    assert summarize_metrics(loaded)["user_edit_ratio"] == 1 / 8
    assert json.loads(json.dumps(metrics, ensure_ascii=False)) == metrics
