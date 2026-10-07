from datetime import datetime, timezone, timedelta
from copy import deepcopy
from types import SimpleNamespace
import os

import pytest

from agent.metrics import start_metrics, finalize_metrics, record_rejection, record_rework
from app.storage import save_record, latest_runs
from evals.work_kpis import summarize_runs, main


def sample():
    draft = {'제목': '실적', '요약': '□ 매출 120만원임 [S1]', '본문': '○ 매출 120만원임 [S1]'}
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    changed = dict(draft, 본문='□ 매출 120만원임 [S1]')
    metrics = finalize_metrics(start_metrics(draft, now=now), changed, now=now + timedelta(minutes=2))
    return {'run_id': 'sample-run', 'draft': changed, 'metrics': metrics, 'state': 'confirmed', 'review': {'blocking': False}}


def test_kpi_summary_excludes_reopened_report_and_uses_latest_snapshot(tmp_path):
    run = sample()
    save_record(run, tmp_path / 'runs' / run['run_id'])
    summary = summarize_runs(latest_runs(tmp_path))
    assert summary['finalized_count'] == 1 and summary['median_draft_to_final_seconds'] == 120
    assert 0 < summary['median_user_edit_ratio'] < .1
    reopened = deepcopy(run)
    reopened['metrics'] = record_rejection(run['metrics'], '성과 보완', now='2026-10-03T00:03:00Z')
    save_record(reopened, tmp_path / 'runs' / run['run_id'])
    summary = summarize_runs(latest_runs(tmp_path))
    assert summary['finalized_count'] == 0 and summary['median_user_edit_ratio'] is None
    assert len(summary['excluded']) == 1


def test_kpi_empty_data_is_na_instead_of_zero_perfect_score(tmp_path, capsys):
    assert summarize_runs([])['median_user_edit_ratio'] is None
    assert main(['--data', str(tmp_path), '--output', str(tmp_path / 'result.json')]) == 0
    assert '측정할 확정 기록이 없음' in capsys.readouterr().out


def test_different_unsaved_draft_cannot_reuse_previous_finalized_time():
    run = sample()
    run['draft']['본문'] = '○ 다른 내용임 [S1]'
    assert summarize_runs([run])['finalized_count'] == 0


@pytest.mark.parametrize('reopen', [record_rejection, record_rework])
def test_latest_snapshot_survives_equal_file_times_and_reverse_uuid_order(tmp_path, monkeypatch, reopen):
    ids = iter(['f' * 32, '0' * 32, '1' * 32])
    monkeypatch.setattr('app.storage.uuid4', lambda: SimpleNamespace(hex=next(ids)))
    run = sample()
    directory = tmp_path / 'runs' / run['run_id']
    original_id = save_record(run, directory)
    reopened = deepcopy(run)
    reopened['metrics'] = reopen(run['metrics'], now='2026-10-03T00:03:00Z')
    reopened_id = save_record(reopened, directory)
    for record_id in (original_id, reopened_id):
        os.utime(directory / 'records' / f'{record_id}.json', ns=(1_000_000_000, 1_000_000_000))
    summary = summarize_runs(latest_runs(tmp_path))
    assert summary['finalized_count'] == 0
    assert summary['median_user_edit_ratio'] is None
    assert latest_runs(tmp_path)[0]['metrics']['baseline_draft'] == run['metrics']['baseline_draft']
    # A later human confirmation is eligible again, without replacing the first draft.
    finalized = deepcopy(reopened)
    finalized['metrics'] = finalize_metrics(reopened['metrics'], finalized['draft'], now='2026-10-03T00:04:00Z')
    final_id = save_record(finalized, directory)
    os.utime(directory / 'records' / f'{final_id}.json', ns=(1_000_000_000, 1_000_000_000))
    summary = summarize_runs(latest_runs(tmp_path))
    assert summary['finalized_count'] == 1
    assert summary['median_draft_to_final_seconds'] == 240
    assert summary['total_rejections'] == (reopen is record_rejection)
    assert summary['total_reworks'] == (reopen is record_rework)


def test_existing_snapshots_without_pointer_still_use_latest_file_time(tmp_path):
    run = sample()
    directory = tmp_path / 'runs' / run['run_id']
    original_id = save_record(run, directory)
    reopened = deepcopy(run)
    reopened['metrics'] = record_rejection(run['metrics'], now='2026-10-03T00:03:00Z')
    reopened_id = save_record(reopened, directory)
    (directory / 'latest_record_id').unlink()
    for index, record_id in enumerate((original_id, reopened_id), 1):
        os.utime(directory / 'records' / f'{record_id}.json', ns=(index * 1_000_000_000, index * 1_000_000_000))
    latest = latest_runs(tmp_path)
    assert len(latest) == 1 and latest[0]['metrics']['rejection_count'] == 1
    assert summarize_runs(latest)['finalized_count'] == 0


@pytest.mark.parametrize('damage', ['invalid_id', 'missing_snapshot', 'invalid_json'])
def test_broken_latest_pointer_never_falls_back_to_old_confirmed_kpi(tmp_path, damage):
    run = sample()
    directory = tmp_path / 'runs' / run['run_id']
    save_record(run, directory)
    reopened = deepcopy(run)
    reopened['metrics'] = record_rejection(run['metrics'], now='2026-10-03T00:03:00Z')
    record_id = save_record(reopened, directory)
    snapshot = directory / 'records' / f'{record_id}.json'
    if damage == 'invalid_id':
        (directory / 'latest_record_id').write_text('../../escape', encoding='utf-8')
    elif damage == 'missing_snapshot':
        snapshot.unlink()
    else:
        snapshot.write_text('not JSON', encoding='utf-8')
    assert latest_runs(tmp_path) == []
    assert summarize_runs(latest_runs(tmp_path))['finalized_count'] == 0
