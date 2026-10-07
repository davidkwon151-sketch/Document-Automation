"""Fresh RA MVP exports: quote-copy demo and live generation are separate."""

from argparse import ArgumentParser
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from app.ra_mvp_service import ROOT, generate_mvp, load_mvp_forms, resolve_mvp_form
from agent.pipeline import build_downloads, review_result
from llm.client import ConfigurationError, LLMError


def evaluate(*, mode='demo', form_ids=None, paths=None, instruction='RA 공식 원자료의 선정 항목을 검토용으로 작성해줘',
             artifact_dir=None, client=None, root=ROOT):
    if mode not in {'demo', 'live'}:
        raise ValueError('평가 모드는 demo 또는 live이어야 함')
    root = Path(root).resolve()
    records = load_mvp_forms(root)
    selected = form_ids if form_ids is not None else [form['id'] for form in records]
    if (not isinstance(selected, (list, tuple)) or not selected
            or any(not isinstance(value, str) for value in selected) or len(selected) != len(set(selected))):
        raise ValueError('서로 다른 등록 MVP 양식을 선택해야 함')
    target = Path(artifact_dir or root / 'outputs/ra-mvp/artifacts')
    rows = []
    for form_id in selected:
        started = perf_counter()
        directory = target / uuid4().hex
        row = {'form_id': form_id, 'mode': mode, 'passed': False, 'submission_ready': False,
               'model_evaluated': False, 'human_kpi_observations': 0}
        try:
            template, _, record = resolve_mvp_form(form_id, root)
            original = sha256(template.read_bytes()).hexdigest()
            result = generate_mvp(instruction, form_id, paths=paths, client=client, demo=mode == 'demo', root=root)
            row.update(status=result['status'], run_id=result['run_id'], source_id=record['demo_source_id'] if mode == 'demo' else None,
                       selected_keys=record['demo_field_keys'] if mode == 'demo' else [],
                       selected_source_field_map={key: record.get('demo_field_map', {}).get(key, key)
                                                  for key in record['demo_field_keys']} if mode == 'demo' else {},
                       document_kind=result['document_kind'], ra_workflow=result['ra_workflow'],
                       missing_form_fields=result['missing_form_fields'], notice=result['notice'],
                       model_evaluated=mode == 'live' and client is None and 'draft' in result,
                       review=result.get('review'), template_sha256=original)
            if result['status'] == 'ready':
                checked = review_result(result)
                if checked['blocking']:
                    raise ValueError('최종 내용 검수 오류가 남아 있음')
                payload = build_downloads(result, confirmed=True, template_paths={'pdf': template},
                                          template_profiles={'pdf': result['template_profile']}, native_review='off')
                if sha256(template.read_bytes()).hexdigest() != original:
                    raise ValueError('작성 중 양식 원본이 변경됨')
                directory.mkdir(parents=True, exist_ok=True)
                output = directory / 'mvp.pdf'
                output.write_bytes(payload['pdf'])
                row.update(passed=True, output=str(output), output_sha256=sha256(payload['pdf']).hexdigest(),
                           output_verification=result['output_verification']['pdf'], original_unchanged=True)
                # Private preview bytes are not a JSON record. No old PDF is reused.
                sidecar = directory / 'mvp.provenance.json'
                sidecar.write_text(json.dumps({'form': record, 'draft': result['draft'], 'sources': result['sources'],
                                               'mode': result['mode'], 'review': checked,
                                               'output_verification': row['output_verification'], 'submission_ready': False},
                                              ensure_ascii=False, indent=2), encoding='utf-8')
                row.update(provenance=str(sidecar), provenance_sha256=sha256(sidecar.read_bytes()).hexdigest())
        except ConfigurationError:
            row.update(status='unavailable', error_type='ConfigurationError',
                       error='실제 AI 작성의 API 설정을 확인할 수 없음', actual_api_requests=0)
        except LLMError:
            row.update(status='unavailable', error_type='LLMError', error='실제 모델 응답을 확인하지 못함')
        except (OSError, ValueError) as exc:
            row.update(passed=False, status='blocked', error_type=type(exc).__name__, error='원자료·양식·내용·출력 검수를 완료하지 못함')
            for key in ('output', 'output_sha256', 'output_verification', 'provenance', 'provenance_sha256'):
                row.pop(key, None)
        row['seconds'] = round(perf_counter() - started, 3)
        rows.append(row)
    return {'checked_at': datetime.now(timezone.utc).isoformat(), 'mode': mode,
            'notice': '공식 원문 복사 데모이며 실제 AI 품질 평가가 아님' if mode == 'demo' else '실제 AI 작성 경로 평가. 설정 불가·질문·근거 부족은 성공으로 집계하지 않음',
            'case_count': len(rows), 'configuration_count': len(rows),
            'distinct_registered_original_count': len({record['source_sha256'] for record in records if record['id'] in selected}),
            'distinct_verified_original_count': len({row['template_sha256'] for row in rows if row['passed']}),
            'passed_count': sum(row['passed'] for row in rows),
            'model_response_cases': sum(row['model_evaluated'] for row in rows),
            'actual_api_requests': 0 if mode == 'demo' or all(row.get('actual_api_requests') == 0 for row in rows) else None,
            'injected_client': client is not None, 'human_kpi_observations': 0,
            'full_submission_ready': False, 'results': rows}


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('demo', 'live'), default='demo')
    parser.add_argument('--form', action='append', dest='form_ids')
    parser.add_argument('--path', action='append', dest='paths')
    parser.add_argument('--instruction', default='RA 공식 원자료의 선정 항목을 검토용으로 작성해줘')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/ra-mvp/evaluation.json')
    parser.add_argument('--artifact-dir', type=Path)
    args = parser.parse_args(argv)
    report = evaluate(mode=args.mode, form_ids=args.form_ids, paths=args.paths, instruction=args.instruction,
                      artifact_dir=args.artifact_dir or args.output.parent / 'artifacts')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('mode', 'case_count', 'passed_count', 'model_response_cases', 'actual_api_requests')}, ensure_ascii=False))
    return 0 if report['passed_count'] == report['case_count'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
