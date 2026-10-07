"""Native proof policy adds to, and never replaces, the raw output guard."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from agent.pipeline import build_downloads
from tests.test_pipeline import ready_result

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'templates/result_report.docx'


def install_review(monkeypatch, *, status='passed', blocking=False, preview=b'%PDF-mock', alter=None):
    import agent.native_review
    calls = []

    def review(template, output, values, **kwargs):
        calls.append((Path(template), Path(output), deepcopy(values), deepcopy(kwargs)))
        report = {'status': status, 'blocking': blocking, 'issues': [], 'engine': 'mock',
                  'source_sha256': sha256(Path(template).read_bytes()).hexdigest(),
                  'output_sha256': sha256(Path(output).read_bytes()).hexdigest(),
                  'checked_field_count': 3, 'page_count': 1}
        if preview is not None:
            report.update(pdf_bytes=preview, pdf_sha256=sha256(preview).hexdigest())
        if alter:
            alter(report, template, output, values, kwargs)
        return report

    monkeypatch.setattr(agent.native_review, 'review_native_output', review)
    return calls


def export(result, **kwargs):
    return build_downloads(result, confirmed=True, template_paths={'docx': TEMPLATE}, **kwargs)


def test_native_default_off_and_explicit_disabled_do_not_render(monkeypatch):
    calls = install_review(monkeypatch)
    for kwargs in ({}, {'native_review': 'off'}):
        result = ready_result()
        assert set(export(result, **kwargs)) == {'docx'}
        assert 'native_output_verification' not in result
    assert not calls


def test_native_pass_keeps_both_independent_proofs_and_transient_pdf(monkeypatch):
    calls = install_review(monkeypatch)
    result = ready_result()
    metrics = deepcopy(result['metrics'])
    outputs = export(result, native_review='required')
    report = result['native_output_verification']['docx']
    assert result['output_verification']['docx']
    assert report['output_sha256'] == sha256(outputs['docx']).hexdigest()
    assert report['elapsed_seconds'] >= 0
    assert 'pdf_bytes' not in report
    assert result['_native_preview_bytes'] == {'docx': b'%PDF-mock'}
    assert calls[0][2] == result['draft'] and result['metrics'] == metrics


@pytest.mark.parametrize('status', ['warning', 'unavailable'])
def test_auto_exposes_pending_and_allows_original_document_export(monkeypatch, status):
    install_review(monkeypatch, status=status, preview=None)
    result = ready_result()
    assert export(result, native_review='auto')['docx'].startswith(b'PK')
    assert result['native_output_verification']['docx']['status'] == status
    assert result['_native_preview_bytes'] == {}


@pytest.mark.parametrize('status,blocking,mode', [
    ('warning', False, 'required'), ('unavailable', False, 'required'),
    ('failed', True, 'auto'), ('passed', True, 'auto'), ('unknown', False, 'auto')])
def test_native_failure_or_strict_pending_removes_all_export_proof(monkeypatch, status, blocking, mode):
    install_review(monkeypatch, status=status, blocking=blocking)
    result = ready_result()
    result.update(output_verification={'old': True}, output_hashes={'old': 'hash'},
                  _native_preview_bytes={'old': b'old'})
    with pytest.raises(ValueError):
        export(result, native_review=mode)
    assert 'output_verification' not in result and 'output_hashes' not in result
    assert '_native_preview_bytes' not in result
    assert result['native_output_verification']['docx']['status'] == status


@pytest.mark.parametrize('mode', [True, None, 'invalid', []])
def test_invalid_mode_clears_previous_proofs(monkeypatch, mode):
    calls = install_review(monkeypatch)
    result = ready_result()
    result.update(output_verification={}, native_output_verification={'docx': {}},
                  _native_preview_bytes={'docx': b'old'})
    with pytest.raises(ValueError, match='모드'):
        export(result, native_review=mode)
    assert not calls
    assert not any(key in result for key in ('output_verification', 'native_output_verification', '_native_preview_bytes'))


@pytest.mark.parametrize('bad_key', ['source_sha256', 'output_sha256', 'pdf_sha256'])
def test_bad_native_hash_is_blocked(monkeypatch, bad_key):
    install_review(monkeypatch, alter=lambda report, *args: report.update({bad_key: 'old-hash'}))
    result = ready_result()
    with pytest.raises(ValueError, match='출력 확인 실패'):
        export(result, native_review='auto')
    assert result['native_output_verification']['docx']['blocking']
    assert 'output_verification' not in result and '_native_preview_bytes' not in result


def test_renderer_mutating_output_is_rejected_even_with_old_proof(monkeypatch):
    def alter(report, template, output, *_):
        Path(output).write_bytes(Path(output).read_bytes() + b'changed')
    install_review(monkeypatch, alter=alter)
    result = ready_result()
    with pytest.raises(ValueError, match='출력 확인 실패'):
        export(result, native_review='auto')
    assert result['native_output_verification']['docx']['status'] == 'failed'


def test_required_does_not_return_partial_formats(monkeypatch):
    def alter(report, template, *_):
        if Path(template).suffix == '.hwpx':
            report.update(status='unavailable')
            report.pop('pdf_bytes', None)
            report.pop('pdf_sha256', None)
    calls = install_review(monkeypatch, alter=alter)
    result = ready_result()
    with pytest.raises(ValueError, match='모든 형식'):
        build_downloads(result, confirmed=True, native_review='required')
    assert len(calls) == 2 and 'output_verification' not in result
    assert '_native_preview_bytes' not in result
    assert set(result['native_output_verification']) == {'docx', 'hwpx'}


def test_raw_output_error_still_blocks_before_native_renderer(monkeypatch):
    import agent.output_check
    calls = install_review(monkeypatch)
    def fail(*args, **kwargs):
        raise ValueError('독립 위치 검사 실패')
    monkeypatch.setattr(agent.output_check, 'verify_output', fail)
    result = ready_result()
    with pytest.raises(ValueError, match='독립 위치'):
        export(result, native_review='auto')
    assert not calls and 'native_output_verification' not in result


def test_initial_content_error_invalidates_previous_native_results(monkeypatch):
    calls = install_review(monkeypatch)
    result = ready_result()
    result.update(output_verification={}, native_output_verification={'docx': {'status': 'passed'}},
                  _native_preview_bytes={'docx': b'old'})
    with pytest.raises(ValueError, match='확인'):
        build_downloads(result, native_review='auto')
    assert not calls and 'native_output_verification' not in result


@pytest.mark.parametrize('mode', ['off', 'auto'])
@pytest.mark.parametrize('changed', ['source', 'output'])
def test_change_after_raw_checker_cannot_break_proof_chain(tmp_path, monkeypatch, mode, changed):
    import agent.output_check
    from shutil import copyfile
    from zipfile import ZipFile

    template = tmp_path / 'local-template.docx'
    copyfile(TEMPLATE, template)
    original = agent.output_check.verify_output
    calls = install_review(monkeypatch)
    def verify(source, output, *args, **kwargs):
        proof = original(source, output, *args, **kwargs)
        with ZipFile(source if changed == 'source' else output, 'a') as package:
            package.comment = b'changed-after-independent-review'
        return proof
    monkeypatch.setattr(agent.output_check, 'verify_output', verify)
    result = ready_result()
    with pytest.raises(ValueError, match='검증 기록'):
        build_downloads(result, confirmed=True, template_paths={'docx': template}, native_review=mode)
    assert not calls and 'output_verification' not in result
    assert 'native_output_verification' not in result and '_native_preview_bytes' not in result


@pytest.mark.parametrize('mode', ['off', 'auto', 'required'])
@pytest.mark.parametrize('changed', ['source', 'output'])
def test_later_format_cannot_publish_an_earlier_changed_file(tmp_path, monkeypatch, mode, changed):
    import agent.output_check
    from shutil import copyfile
    from zipfile import ZipFile

    paths = {suffix: tmp_path / f'local.{suffix}' for suffix in ('docx', 'hwpx')}
    for suffix, path in paths.items():
        copyfile(ROOT / 'templates' / f'result_report.{suffix}', path)
    first_output = []
    original = agent.output_check.verify_output
    def verify(source, output, *args, **kwargs):
        proof = original(source, output, *args, **kwargs)
        if source.suffix == '.docx':
            first_output.append(output)
        else:
            with ZipFile(paths['docx'] if changed == 'source' else first_output[0], 'a') as package:
                package.comment = b'changed-during-later-format'
        return proof
    monkeypatch.setattr(agent.output_check, 'verify_output', verify)
    install_review(monkeypatch)
    result = ready_result()
    metrics = deepcopy(result['metrics'])
    with pytest.raises(ValueError, match='최종 출력 검증'):
        build_downloads(result, confirmed=True, template_paths=paths, native_review=mode)
    assert result['metrics'] == metrics
    assert not any(key in result for key in ('output_verification', 'output_hashes', '_native_preview_bytes'))
    if mode != 'off':
        report = result['native_output_verification']['docx']
        assert report['status'] == 'failed' and report['blocking']
        assert any(issue['code'] == 'native_publish_mismatch' for issue in report['issues'])


@pytest.mark.parametrize('mode', ['auto', 'required'])
@pytest.mark.parametrize('changed', ['source_sha256', 'output_sha256', 'pdf_sha256', 'status', 'blocking'])
def test_later_native_format_cannot_replace_an_earlier_proof(tmp_path, monkeypatch, mode, changed):
    from shutil import copyfile
    import agent.native_review

    paths = {suffix: tmp_path / f'local.{suffix}' for suffix in ('docx', 'hwpx')}
    for suffix, path in paths.items():
        copyfile(ROOT / 'templates' / f'result_report.{suffix}', path)
    install_review(monkeypatch)
    original = agent.native_review.review_native_output
    first = []
    def review(template, *args, **kwargs):
        report = original(template, *args, **kwargs)
        if template.suffix == '.docx':
            first.append(report)
        else:
            first[0][changed] = ('failed' if changed == 'status' else True if changed == 'blocking'
                                 else 'replaced-after-first-format-was-checked')
        return report
    monkeypatch.setattr(agent.native_review, 'review_native_output', review)
    result = ready_result()
    with pytest.raises(ValueError, match='최종 출력 검증'):
        build_downloads(result, confirmed=True, template_paths=paths, native_review=mode)
    assert result['native_output_verification']['docx']['blocking']
    assert 'output_verification' not in result and '_native_preview_bytes' not in result


def test_required_final_gate_rechecks_an_earlier_pending_status(tmp_path, monkeypatch):
    from shutil import copyfile
    import agent.native_review

    paths = {suffix: tmp_path / f'local.{suffix}' for suffix in ('docx', 'hwpx')}
    for suffix, path in paths.items():
        copyfile(ROOT / 'templates' / f'result_report.{suffix}', path)
    install_review(monkeypatch)
    original = agent.native_review.review_native_output
    first = []
    def review(template, *args, **kwargs):
        report = original(template, *args, **kwargs)
        if template.suffix == '.docx':
            first.append(report)
        else:
            first[0]['status'] = 'unavailable'
        return report
    monkeypatch.setattr(agent.native_review, 'review_native_output', review)
    result = ready_result()
    with pytest.raises(ValueError, match='최종 출력 검증'):
        build_downloads(result, confirmed=True, template_paths=paths, native_review='required')
    assert result['native_output_verification']['docx']['blocking']
    assert 'output_verification' not in result and '_native_preview_bytes' not in result
