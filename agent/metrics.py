"""User work KPIs anchored to the first AI draft shown to the user.

Call start_metrics after internal AI review, immediately before displaying a draft.
Only explicit human actions call record_rejection / record_rework. AI questions and
automatic AI revisions are not human rejection or rework events. Functions return
deep copies and never replace the original baseline. Timestamps must be timezone
aware and monotonic; all persisted timestamps use UTC ISO 8601.

Edit ratio = character insert/delete/substitute count / max(old_len, new_len).
The overall ratio uses the sum of counts divided by the sum of field lengths,
not an unweighted average. NFC, CRLF/CR and trailing horizontal whitespace are
normalized; remaining spaces, bullets and citation changes count as edits.
Small differences use exact Levenshtein distance. Large differences use at most
256 text blocks per side with SequenceMatcher, and explicitly report an
approximation. This keeps long documents from causing unbounded quadratic work.
"""

from __future__ import annotations

import math
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher

REQUIRED_FIELDS = ("제목", "요약", "본문")
EXACT_CELL_LIMIT = 250_000
NORMALIZATION = "NFC; CRLF/CR→LF; 줄끝 공백·탭 제외; 그 외 문자·공백 포함"


def _draft(draft: dict) -> dict[str, str]:
    if not isinstance(draft, dict) or any(
        not isinstance(key, str) or not key.strip() or not isinstance(value, str)
        for key, value in draft.items()
    ):
        raise ValueError("초안은 문자열 자리표시자와 문자열 값의 객체여야 함")
    if any(not draft.get(field, "").strip() for field in REQUIRED_FIELDS):
        raise ValueError("제목·요약·본문이 비어 있지 않은 초안이 필요함")
    return {
        key: "\n".join(line.rstrip(" \t") for line in unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n").split("\n"))
        for key, value in draft.items()
    }


def _time(value=None) -> datetime:
    if value is None:
        value = datetime.now(timezone.utc)
    elif isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("유효한 ISO 8601 시각이 필요함") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("시각에는 시간대가 필요함")
    return value.astimezone(timezone.utc)


def _copy(metrics: dict) -> dict:
    if not isinstance(metrics, dict):
        raise ValueError("KPI 기록은 객체여야 함")
    result = deepcopy(metrics)
    try:
        result["baseline_draft"] = _draft(result["baseline_draft"])
        result["latest_draft"] = _draft(result["latest_draft"])
        started = _time(result["draft_started_at"])
        previous = started
        for event in result["events"]:
            stamp = _time(event["timestamp"])
            if stamp < previous:
                raise ValueError("KPI 이벤트 시각이 역순임")
            previous = stamp
        if result["finalized_at"] is not None:
            finalized = _time(result["finalized_at"])
            if finalized < started or finalized != previous or not result["events"] or result["events"][-1].get("kind") != "finalized":
                raise ValueError("확정 시각은 마지막 확정 이벤트와 일치해야 함")
        for field in ("rejection_count", "rework_count", "revision_count"):
            if type(result[field]) is not int or result[field] < 0:
                raise ValueError("KPI 횟수는 0 이상의 정수여야 함")
    except (KeyError, TypeError) as exc:
        raise ValueError("KPI 기록에 필요한 항목이 없음") from exc
    return result


def _stamp(metrics: dict, now) -> str:
    stamp = _time(now)
    last = metrics["events"][-1]["timestamp"] if metrics["events"] else metrics["draft_started_at"]
    if stamp < _time(last):
        raise ValueError("새 이벤트 시각은 이전 이벤트보다 빠를 수 없음")
    return stamp.isoformat()


def _edit_count(old: str, new: str) -> tuple[int, str]:
    if old == new:
        return 0, "exact"
    # Trim matching ends before the expensive comparison; long local edits stay exact.
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    old, new = old[prefix:], new[prefix:]
    suffix = 0
    while suffix < min(len(old), len(new)) and old[-suffix - 1] == new[-suffix - 1]:
        suffix += 1
    if suffix:
        old, new = old[:-suffix], new[:-suffix]
    if not old or not new:
        return max(len(old), len(new)), "exact"
    if len(old) * len(new) <= EXACT_CELL_LIMIT:
        if len(old) < len(new):
            old, new = new, old
        previous = list(range(len(new) + 1))
        for row, char in enumerate(old, 1):
            current = [row]
            for column, other in enumerate(new, 1):
                current.append(min(current[-1] + 1, previous[column] + 1, previous[column - 1] + (char != other)))
            previous = current
        return previous[-1], "exact"
    size = max(64, math.ceil(max(len(old), len(new)) / 256))
    before = [old[offset:offset + size] for offset in range(0, len(old), size)]
    after = [new[offset:offset + size] for offset in range(0, len(new), size)]
    count = sum(
        max(sum(map(len, before[i:j])), sum(map(len, after[k:l])))
        for tag, i, j, k, l in SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
        if tag != "equal"
    )
    return min(count, max(len(old), len(new))), "bounded_block_approximation"


def _comparison(baseline: dict, draft: dict) -> dict:
    ratios, counts, lengths, methods = {}, {}, {}, {}
    for field in sorted(baseline.keys() | draft.keys()):
        old, new = baseline.get(field, ""), draft.get(field, "")
        count, method = _edit_count(old, new)
        length = max(len(old), len(new))
        counts[field], lengths[field], methods[field] = count, length, method
        ratios[field] = count / length if length else 0.0
    return {
        "user_edit_ratio": sum(counts.values()) / sum(lengths.values()) if sum(lengths.values()) else 0.0,
        "field_edit_ratios": ratios,
        "edit_counts": counts,
        "comparison_lengths": lengths,
        "field_edit_methods": methods,
        "edit_ratio_is_approximate": "bounded_block_approximation" in methods.values(),
        "edit_normalization": NORMALIZATION,
    }


def _unfinalize(metrics: dict):
    metrics["finalized_at"] = None
    metrics["elapsed_seconds"] = None


def start_metrics(draft: dict, now=None) -> dict:
    baseline = _draft(draft)
    stamp = _time(now).isoformat()
    return {
        "baseline_draft": deepcopy(baseline),
        "latest_draft": deepcopy(baseline),
        "draft_started_at": stamp,
        "finalized_at": None,
        "elapsed_seconds": None,
        "rejection_count": 0,
        "rework_count": 0,
        "revision_count": 0,
        "events": [{"kind": "started", "timestamp": stamp, "draft": deepcopy(baseline)}],
        **_comparison(baseline, baseline),
    }


def record_revision(metrics: dict, draft: dict, now=None) -> dict:
    result, current = _copy(metrics), _draft(draft)
    stamp = _stamp(result, now)
    if current != result["latest_draft"]:
        reopened = result["finalized_at"] is not None
        _unfinalize(result)
        result["latest_draft"] = deepcopy(current)
        result["revision_count"] += 1
        result["events"].append({"kind": "revision", "timestamp": stamp, "draft": deepcopy(current), "reopened": reopened})
    result.update(_comparison(result["baseline_draft"], current))
    return result


def record_rejection(metrics: dict, reason: str = "", now=None) -> dict:
    if not isinstance(reason, str):
        raise ValueError("반려 사유는 문자열이어야 함")
    result = _copy(metrics)
    stamp = _stamp(result, now)
    _unfinalize(result)
    result["rejection_count"] += 1
    result["events"].append({"kind": "rejection", "timestamp": stamp, "reason": reason.strip()})
    return result


def record_rework(metrics: dict, now=None) -> dict:
    result = _copy(metrics)
    stamp = _stamp(result, now)
    _unfinalize(result)
    result["rework_count"] += 1
    result["events"].append({"kind": "rework", "timestamp": stamp})
    return result


def finalize_metrics(metrics: dict, draft: dict, now=None) -> dict:
    """Compare the final preview even if no prior save/revision event was recorded."""
    result = record_revision(metrics, draft, now=now)
    if result["finalized_at"] is not None:
        return result
    stamp = _stamp(result, now)
    result["finalized_at"] = stamp
    result["elapsed_seconds"] = (_time(stamp) - _time(result["draft_started_at"])).total_seconds()
    result["events"].append({"kind": "finalized", "timestamp": stamp, "draft": deepcopy(result["latest_draft"])})
    return result


def summarize_metrics(metrics: dict, draft: dict | None = None) -> dict:
    """Read-only KPI preview; unsaved edits are compared but never recorded."""
    result = _copy(metrics)
    current = _draft(draft) if draft is not None else result["latest_draft"]
    if current != result["latest_draft"]:
        _unfinalize(result)
    result.update(_comparison(result["baseline_draft"], current))
    fields = ("draft_started_at", "finalized_at", "elapsed_seconds", "rejection_count", "rework_count", "revision_count", "user_edit_ratio", "field_edit_ratios", "edit_counts", "comparison_lengths", "field_edit_methods", "edit_ratio_is_approximate", "edit_normalization")
    return {field: result[field] for field in fields}
