"""Domain rule regressions and optional actual-model generation on valid cases."""

from argparse import ArgumentParser
from collections import Counter
from hashlib import sha256
from importlib import import_module
import json
from pathlib import Path

from agent.pipeline import DOMAIN_MODULES, _workflow_context

ROOT = Path(__file__).resolve().parents[1]
CASES = Path(__file__).with_name('domain_cases.json')


def evaluate(cases, *, mode='rules', client=None):
    if mode not in {'rules', 'live'}:
        raise ValueError('평가 모드는 rules 또는 live이어야 함')
    identifiers = [case['id'] for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('평가 ID가 중복됨')
    rows = []
    model_response_count = 0
    models = set()
    for case in cases:
        if mode == 'live' and case['expected_blocking']:
            continue  # Injected wrong drafts test the checker, not a model instruction.
        module_name, _, prefix = DOMAIN_MODULES[case['domain']]
        inspector = getattr(import_module(module_name), 'inspect_' + prefix + '_draft')
        sources = [{'source_id': 'S1', 'text': case['source_text'], 'filename': '합성평가자료.txt', 'page': 1, 'sheet': None}]
        profile = {'domain': case['domain'], prefix + '_workflow': case['workflow']}
        draft = {'본문': case['candidate']}
        response_received = False
        model_name = None
        try:
            if mode == 'live':
                from agent.draft import create_draft
                from agent.grounding import inspect_grounding
                from agent.review import review_draft
                if client is None:
                    from llm.client import LLMClient
                    client = LLMClient()
                profile, context = _workflow_context(profile, **{prefix: case['workflow']})
                brief = {'목적': '첨부 자료 검토', '보고 대상': '팀장', '보고서 유형': '결과보고서',
                         '마감': '', '분량': '1쪽', '부족한 정보': [], '질문': [], **context}
                draft = create_draft(brief, sources, client, template_profile=profile)
                response_received = True
                model_response_count += 1
                configured_model = getattr(client, 'model', None)
                model_name = configured_model if isinstance(configured_model, str) and configured_model.strip() else None
                if model_name is not None:
                    models.add(model_name)
                generic = review_draft(draft, sources, auto_correct=False)
                semantic = inspect_grounding(draft, sources, client)
            checked = inspector(draft, sources, profile=profile)
            blocking = checked['blocking']
            if mode == 'live':
                blocking |= generic['blocking'] or semantic['blocking']
            rows.append({'id': case['id'], 'domain': case['domain'], 'workflow': case['workflow'],
                         'expected_blocking': case['expected_blocking'], 'blocking': blocking,
                         'passed': blocking == case['expected_blocking'], 'issues': checked['issues'],
                         'draft': draft if mode == 'live' else None,
                         'model_response_received': response_received, 'model': model_name})
        except Exception as exc:
            rows.append({'id': case['id'], 'domain': case['domain'], 'passed': False, 'error': str(exc),
                         'model_response_received': response_received, 'model': model_name})
    counts = Counter(row['domain'] for row in rows)
    return {'mode': mode, 'synthetic_sources': True, 'model_evaluated': model_response_count > 0,
            'model_response_count': model_response_count, 'models': sorted(models),
            'model_name_source': 'client_configuration' if model_response_count else None,
            'case_count': len(rows), 'passed_count': sum(row['passed'] for row in rows),
            'by_domain': dict(counts), 'results': rows,
            'limitations': ['rules는 고정 초안의 검수 회귀이며 프롬프트 품질·실사용 수정 비율을 측정하지 않음',
                            'live는 합성 원자료로 실제 모델을 평가하며 실제 제출 적합성과 현장 KPI를 인증하지 않음',
                            'model_response_count는 create_draft에서 반환된 초안 수이며 후속 검수 API 호출 수와 구분함']}


def main(argv=None):
    parser = ArgumentParser()
    parser.add_argument('--mode', choices=['rules', 'live'], default='rules')
    parser.add_argument('--cases', type=Path, default=CASES)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/domain-evaluation.json')
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args(argv)
    raw = args.cases.read_bytes()
    report = evaluate(json.loads(raw.decode('utf-8')), mode=args.mode)
    report['cases_sha256'] = sha256(raw).hexdigest()
    report['prompt_sha256'] = {path.name: sha256(path.read_bytes()).hexdigest() for path in sorted((ROOT / 'prompts').glob('*.md'))}
    if args.baseline:
        previous = json.loads(args.baseline.read_text(encoding='utf-8'))
        if previous['mode'] != report['mode'] or previous['cases_sha256'] != report['cases_sha256']:
            raise ValueError('같은 모드와 케이스 버전의 기준 결과가 필요함')
        old = {row['id']: row for row in previous['results']}
        report['regressions'] = [row['id'] for row in report['results'] if old.get(row['id'], {}).get('passed') and not row['passed']]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('mode', 'case_count', 'passed_count', 'by_domain')}, ensure_ascii=False))
    return int(report['passed_count'] != report['case_count'])


if __name__ == '__main__':
    raise SystemExit(main())
