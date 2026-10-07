"""Measure saved human finalization records, never infer human edits from mock runs."""

import argparse
import json
from pathlib import Path
from statistics import median

from agent.metrics import summarize_metrics
from app.storage import latest_runs


def summarize_runs(runs):
    complete, ignored = [], []
    for run in runs:
        try:
            metrics = summarize_metrics(run['metrics'], run['draft'])
            if metrics['finalized_at'] is None or run.get('state') != 'confirmed' or run.get('review', {}).get('blocking', True):
                ignored.append({'run_id': run.get('run_id'), 'reason': '미확정·재작성·검수 차단 상태'})
                continue
            complete.append({'run_id': run['run_id'], **metrics})
        except (KeyError, ValueError):
            ignored.append({'run_id': run.get('run_id'), 'reason': 'KPI 기록이 없거나 잘못됨'})
    return {'finalized_count': len(complete), 'excluded': ignored,
            'median_user_edit_ratio': median([m['user_edit_ratio'] for m in complete]) if complete else None,
            'median_draft_to_final_seconds': median([m['elapsed_seconds'] for m in complete]) if complete else None,
            'total_rejections': sum(m['rejection_count'] for m in complete),
            'total_reworks': sum(m['rework_count'] for m in complete),
            'approximate_edit_ratio_count': sum(m['edit_ratio_is_approximate'] for m in complete), 'runs': complete,
            'notice': '사람이 실제로 확인·확정한 저장 기록의 KPI임. 원자료의 의미 정확도나 반려되지 않은 제출 여부를 자동 보증하지 않음.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='확정 보고서의 실제 수정 비율·소요 시간·반려·재작업 집계')
    parser.add_argument('--data', type=Path, default=Path(__file__).resolve().parents[1] / 'data')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    report = summarize_runs(latest_runs(args.data))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"확정 보고서: {report['finalized_count']}건")
    ratio = report['median_user_edit_ratio']
    print(f"사용자 수정 비율 중앙값: {ratio:.1%}" if ratio is not None else '사용자 수정 비율: 측정할 확정 기록이 없음')
    print(f"상사 반려 {report['total_rejections']}회 / 재작업 {report['total_reworks']}회")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
