"""Synthetic mutations of independently annotated, actual downloaded EMA records.

This offline checker measures detection of deliberate errors, never model quality,
legal suitability, clinical validity, or the current status of a source snapshot.
"""

from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

from agent.ra import inspect_ra_draft
from agent.review import CITATION_PATTERN, number_tokens, review_draft
from evals.ra_public import DEFAULT_MANIFEST, ROOT, cited, compare_values, read_source


MODE = 'synthetic_mutations_of_real_records'


def baseline_draft(facts):
    first = facts[0]
    draft = {'제목': 'RA 공개 원문 검토', '요약': cited(first['value'], first['source_id']),
             '본문': cited(first['value'], first['source_id'])}
    draft.update({fact['field_key']: cited(fact['value'], fact['source_id']) for fact in facts})
    return draft


def selected_profile(record, facts):
    return {'domain': 'pharmaceutical_ra', 'ra_workflow': 'product_approval',
            'document_kind': 'application', 'ra_product_name': record['product_name'],
            'ra_product_variant': record['product_variant'],
            'fields': [{'value_key': fact['field_key'], 'required': True} for fact in facts]}


def check_draft(draft, facts, sources, profile):
    before = deepcopy(draft)
    generic = review_draft(draft, sources, auto_correct=False, style_exempt_fields=('요약', '본문'))
    professional = inspect_ra_draft(draft, sources, profile=profile)
    # Dictionaries permit a production retrieval ID only after provenance matching.
    scores = compare_values(draft, facts, sources)
    mismatch = (scores['full_value_preserved_count'] != len(facts)
                or scores['cited_field_count'] != len(facts)
                or scores['numeric_exact_count'] != scores['numeric_field_count'])
    if draft != before or generic['draft'] != before or generic['corrected']:
        raise AssertionError('전문 업무 검수에서 원문·숫자가 자동 수정됨')
    return {'generic_blocking': generic['blocking'], 'ra_blocking': professional['blocking'],
            'compare_blocking': mismatch,
            'blocked_by_any': generic['blocking'] or professional['blocking'] or mismatch,
            'generic_issues': generic['warnings'], 'ra_issues': professional['issues'], 'scores': scores,
            'original_draft_unchanged': True}


def mutations(record, facts, sources):
    """Each mutation changes one selected field; all other baseline facts survive."""
    cases = []
    original = baseline_draft(facts)

    def add(fact, family, value):
        draft = {**original, fact['field_key']: value}
        assert draft != original, family
        cases.append({'id': record['id'] + ':' + fact['role'] + ':' + family,
                      'family': family, 'field': fact['field_key'], 'fact_role': fact['role'],
                      'mutated_value': value, 'draft': draft})

    for fact in facts:
        correct = original[fact['field_key']]
        wrong_id = next(source['source_id'] for source in sources if source['source_id'] != fact['source_id'])
        add(fact, 'wrong_known_citation', correct.replace(fact['source_id'], wrong_id))
        add(fact, 'missing_citation', CITATION_PATTERN.sub('', correct).rstrip())
        add(fact, 'unknown_citation', correct.replace(fact['source_id'], 'Snot_a_snapshot_source'))
        add(fact, 'missing_selected_field', '')
        if number_tokens(fact['value']):
            changed = re.sub(r'(?<![A-Za-z\d])\d+(?:\.\d+)?', '999', fact['value'], count=1)
            add(fact, 'changed_numeric_value', cited(changed, fact['source_id']))

    def change(field, family, old, new):
        fact = next(fact for fact in facts if fact['field_key'] == field)
        assert old in fact['value'], (record['id'], family, old)
        add(fact, family, cited(fact['value'].replace(old, new, 1), fact['source_id']))

    product = record['product_name']
    if product == 'Herzuma':
        change('제품명', 'wrong_selected_strength', '150 mg', '420 mg')
        change('저장방법 및 유효기간', 'wrong_unopened_state', 'Unopened vial', 'Reconstituted vial')
        change('용법 용량', 'loading_maintenance_mixed', 'initial loading dose is 8 mg/kg', 'initial loading dose is 6 mg/kg')
        change('용법 용량', 'wrong_dose_interval', 'three-weekly', 'four-weekly')
        change('용법 용량', 'wrong_dose_unit', '8 mg/kg', '8 mg/mL')
        change('용법 용량', 'no_automatic_unit_conversion', '8 mg/kg', '8000 mcg/kg')
    elif product == 'Benepali':
        change('원료약품 및 분량', 'wrong_container', 'syringe', 'tablet')
        change('원료약품 및 분량', 'wrong_quantity_unit', '25 mg', '25 mcg')
        change('원료약품 및 분량', 'invented_concentration', '25 mg', '25 mg/mL')
        change('용법 용량', 'weekly_frequency_mixed', 'twice weekly', 'once weekly')
        change('용법 용량', 'daily_weekly_mixed', 'twice weekly', 'twice daily')
        change('효능 효과', 'wrong_population', 'in adults', 'in infants')
        fact = next(fact for fact in facts if fact['field_key'] == '효능 효과')
        add(fact, 'selected_condition_omitted', cited(fact['value'].split(' when the response')[0], fact['source_id']))
    elif product == 'Keppra':
        change('용법 용량', 'daily_weekly_mixed', 'twice daily', 'twice weekly')
        change('용법 용량', 'lower_initial_primary_mixed', '250 mg twice daily', '500 mg twice daily')
        change('효능 효과', 'wrong_population_age', '16 years', '6 years')
        change('원료약품 및 분량', 'wrong_container', 'tablet', 'syringe')
        change('원료약품 및 분량', 'wrong_quantity_unit', '250 mg', '250 mcg')
        fact = next(fact for fact in facts if fact['field_key'] == '용법 용량')
        add(fact, 'selected_lower_initial_condition_omitted', cited(fact['value'].split('However,')[0].strip(), fact['source_id']))

    # Benepali and Keppra both say 3 years: numerically true, wrong company/product.
    shelf = next((fact for fact in facts if fact['field_key'] == '저장방법 및 유효기간' and '3 years' in fact['value']), None)
    if shelf:
        foreign = next((source for source in sources if source.get('product_name') != product and '3 years' in source['text']), None)
        if foreign:
            add(shelf, 'other_company_same_number', original[shelf['field_key']].replace(shelf['source_id'], foreign['source_id']))
    product_fact = next(fact for fact in facts if fact['role'] == 'product_name')
    foreign_product = next((source for source in sources if source.get('product_name') != product and source.get('regulatory_role') == 'product_name'), None)
    if foreign_product:
        add(product_fact, 'other_company_product_quote', cited(foreign_product['text'], foreign_product['source_id']))
    return cases


def evaluate(manifest, *, root=ROOT):
    root = Path(root)
    loaded, snapshots = [], []
    for record in manifest['sources']:
        path = root / record['path']
        status = {'id': record['id'], 'path': str(path), 'source_url': record['source_url'],
                  'expected_sha256': record['sha256'], 'declared_fact_count': len(record['facts'])}
        if not path.is_file():
            status.update(status='missing_snapshot', checked_fact_count=0, original_unchanged=None)
        else:
            before = sha256(path.read_bytes()).hexdigest()
            status['observed_sha256'] = before
            try:
                facts, sources, _ = read_source(record, root=root)
                loaded.append((record, facts, sources))
                status.update(status='verified', checked_fact_count=len(facts))
            except Exception as error:
                status.update(status='source_validation_error', checked_fact_count=0,
                              error_type=type(error).__name__, error=str(error))
            status['original_unchanged'] = sha256(path.read_bytes()).hexdigest() == before
        snapshots.append(status)
    all_sources = [source for _, _, sources in loaded for source in sources]
    positives, rows = [], []
    for record, facts, _ in loaded:
        profile = selected_profile(record, facts)
        positive_cases = [('exact_quotation', baseline_draft(facts))]
        for fact in facts:
            normalized_draft = baseline_draft(facts)
            normalized_draft[fact['field_key']] = cited(re.sub(r'\s+', ' ', fact['value']), fact['source_id'])
            positive_cases.append(('whitespace_only:' + fact['role'], normalized_draft))
        for family, draft in positive_cases:
            positive = {'id': record['id'] + ':' + family, 'family': family,
                        'company': record['company']['name'], 'product': record['product_variant'],
                        'selected_fact_count': len(facts)}
            try:
                positive.update(check_draft(draft, facts, all_sources, profile))
                positive['passed'] = not positive['blocked_by_any']
            except Exception as error:
                positive.update(passed=False, error_type=type(error).__name__, error=str(error))
            positives.append(positive)
        for case in mutations(record, facts, all_sources):
            row = {key: value for key, value in case.items() if key != 'draft'}
            row.update(company=record['company']['name'], product=record['product_variant'],
                       source_sha256=record['sha256'], source_url=record['source_url'])
            try:
                row.update(check_draft(case['draft'], facts, all_sources, profile))
                row['status'] = 'detected' if row['blocked_by_any'] else 'escaped'
            except Exception as error:
                # An exception is an untested mutation, never a successful detection.
                row.update(status='checker_error', error_type=type(error).__name__, error=str(error))
            rows.append(row)
    checked = [row for row in rows if row['status'] != 'checker_error']
    detected = sum(row['status'] == 'detected' for row in checked)
    report = {'mode': MODE, 'checked_at': datetime.now(timezone.utc).isoformat(),
              'model_evaluated': False, 'model_training_performed': False, 'network_calls': 0,
              'expected_snapshot_count': len(manifest['sources']), 'verified_snapshot_count': len(loaded),
              'declared_fact_count': sum(len(record['facts']) for record in manifest['sources']),
              'checked_fact_count': sum(len(facts) for _, facts, _ in loaded),
              'expected_company_count': len({record['company']['name'] for record in manifest['sources']}),
              'verified_company_count': len({record['company']['name'] for record, _, _ in loaded}),
              'positive_case_count': len(positives), 'positive_passed_count': sum(row['passed'] for row in positives),
              'exact_positive_count': sum(row['family'] == 'exact_quotation' for row in positives),
              'whitespace_positive_count': sum(row['family'].startswith('whitespace_only:') for row in positives),
              'mutation_case_count': len(rows), 'mutation_checked_count': len(checked),
              'mutation_detected_count': detected, 'mutation_escaped_count': len(checked) - detected,
              'checker_error_count': len(rows) - len(checked),
              'detection_rate': detected / len(checked) if checked else None,
              'layer_detected_count': {name: sum(bool(row.get(name + '_blocking')) for row in checked) for name in ['generic', 'ra', 'compare']},
              'families': dict(Counter(row['family'] for row in rows)),
              'snapshots': snapshots, 'positives': positives, 'results': rows,
              'limitations': ['실제 PDF에서 확인한 선택 원문에 의도적 오류를 넣은 합성 시험임. 실제 모델 생성 품질·사람 수정 비율 평가가 아님.',
                              '일반 숫자 검수와 RA 규칙만으로 전체 문단의 완결성과 모든 사실의 의미를 인증하지 않음. 원문·정확 출처 평가를 함께 적용함.',
                              'EU 제품정보 스냅샷의 현재 상태·대한민국 허가·법정 요건·신청인·제조원 역할은 인증하지 않음.',
                              '누락 스냅샷과 검사 예외는 성공 검출로 세지 않음. 원본 PDF를 수정하지 않음.']}
    report['complete'] = len(loaded) == len(manifest['sources'])
    report['passed'] = (report['complete'] and len(positives) > 0 and report['positive_passed_count'] == len(positives)
                        and len(rows) > 0 and report['checker_error_count'] == 0 and report['mutation_escaped_count'] == 0
                        and all(snapshot['original_unchanged'] for snapshot in snapshots))
    return report


def main(argv=None):
    parser = ArgumentParser(description='실제 EMA 기록의 합성 오류 검출 시험: 모델 품질 평가는 수행하지 않음')
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/ra-public-adversarial.json')
    args = parser.parse_args(argv)
    raw = args.manifest.read_bytes()
    report = evaluate(json.loads(raw.decode('utf-8')))
    report['manifest_sha256'] = sha256(raw).hexdigest()
    report['checker_sha256'] = {name: sha256((ROOT / name).read_bytes()).hexdigest()
                                for name in ['agent/review.py', 'agent/ra.py', 'evals/ra_public.py', 'evals/ra_adversarial.py']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ['mode', 'verified_snapshot_count', 'checked_fact_count',
          'positive_passed_count', 'mutation_checked_count', 'mutation_detected_count', 'mutation_escaped_count',
          'checker_error_count', 'passed']}, ensure_ascii=False))
    return int(not report['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
