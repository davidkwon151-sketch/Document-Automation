"""Native evaluation plumbing using official snapshots and offline PDF renderers."""

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pypdf import PdfReader
import pytest

from evals import ra_public


PROFILE = 'ra_law_form_4_pdf'


@pytest.fixture(scope='module')
def actual_record():
    manifest = json.loads((ra_public.ROOT / 'evals/ra_korean_sources.json').read_text(encoding='utf-8'))
    record = next(record for record in manifest['sources'] if record['id'] == 'ra-kr-hanmiflu-75')
    source = ra_public.ROOT / record['path']
    profile = json.loads((ra_public.ROOT / 'templates/profiles' / f'{PROFILE}.json').read_text(encoding='utf-8'))
    template = ra_public.ROOT / profile.get('source_path', 'data/public_templates/ra/' + profile['source_filename'])
    if not source.is_file() or not template.is_file():
        pytest.skip('공식 원자료·법정 양식 snapshot 미확보: 실제 원본 검증 미실행')
    original = {path: sha256(path.read_bytes()).hexdigest() for path in (source, template)}
    assert original[source] == record['sha256']
    parsed = ra_public.validate_source_parser(record)
    assert parsed['passed'] and parsed['page_count'] == record['page_count']
    assert parsed['fact_count'] == len(record['facts'])
    yield record
    assert {path: sha256(path.read_bytes()).hexdigest() for path in original} == original


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    from llm import client
    import parsers.native

    monkeypatch.setattr(client, 'LLMClient', lambda *args, **kwargs: pytest.fail('actual API must not run'))
    monkeypatch.setattr(parsers.native, 'render_native_pdf', lambda *args, **kwargs: pytest.fail('actual COM must not run'))


def unavailable_renderer(monkeypatch):
    import parsers.native

    calls = []
    def render(path, target, **kwargs):
        calls.append(Path(path))
        return {'status': 'unavailable', 'engine': None}
    monkeypatch.setattr(parsers.native, 'render_native_pdf', render)
    return calls


def copy_renderer(monkeypatch):
    """A local PDF copy is fake native evidence, never an Office-quality claim."""
    import parsers.native

    calls = []
    def render(path, target, **kwargs):
        path, target = Path(path), Path(target)
        calls.append(path)
        raw = path.read_bytes()
        target.write_bytes(raw)
        return {'status': 'rendered', 'engine': 'fake_renderer', 'source_unchanged': True,
                'source_sha256': sha256(raw).hexdigest(), 'output_sha256': sha256(raw).hexdigest(),
                'page_count': len(PdfReader(target).pages)}
    monkeypatch.setattr(parsers.native, 'render_native_pdf', render)
    return calls


@pytest.mark.parametrize('mode', ['off', 'auto', 'required'])
def test_native_evaluation_off_auto_and_required_unavailable(actual_record, tmp_path, monkeypatch, mode):
    calls = unavailable_renderer(monkeypatch)
    report = ra_public.evaluate({'sources': [actual_record]}, profiles=(PROFILE,), artifact_dir=tmp_path,
                                check_parser=mode == 'off', native_review=mode)
    row = report['results'][0]
    assert report['native_review'] == row['native_review'] == mode
    assert report['model_response_count'] == 0 and not report['model_evaluated']
    assert row['passed'] == (mode != 'required')
    assert report['passed_count'] == (0 if mode == 'required' else 1)
    assert len(calls) == (0 if mode == 'off' else 1)
    assert row['scores']['full_value_preserved_count'] == row['scores']['selected_field_count'] == 5
    if mode == 'off':
        assert row['native_output_verification'] == row['native_preview_artifacts'] == {}
        assert report['parser_results'][0]['passed']
    else:
        assert row['native_output_verification']['pdf']['status'] == 'unavailable'
    saved = json.loads(Path(row['provenance']).read_text(encoding='utf-8'))
    assert saved['native_output_verification'] == row['native_output_verification']
    assert '_native_preview_bytes' not in saved
    if mode == 'required':
        assert row['native_pending']
        assert 'output' not in row and 'output_verification' not in row
        assert not list(tmp_path.rglob('*.pdf'))
    else:
        assert row['output_verification']['status'] == 'passed'
        assert Path(row['output']).is_file()


def test_native_failed_diagnostics_survive_without_private_exception_text(actual_record, tmp_path, monkeypatch):
    import parsers.native

    private = 'PRIVATE-API-KEY-and-document-body'
    def broken(*args, **kwargs):
        raise RuntimeError(f'{tmp_path} / {private}')
    monkeypatch.setattr(parsers.native, 'render_native_pdf', broken)
    row = ra_public.evaluate_case(actual_record, PROFILE, mode='rules', artifact_dir=tmp_path, native_review='auto')
    assert not row['passed'] and not row['native_pending']
    assert row['native_output_verification']['pdf']['status'] == 'failed'
    assert any(issue['code'] == 'native_review_failed' for issue in row['native_output_verification']['pdf']['issues'])
    assert 'output' not in row and not list(tmp_path.rglob('*.pdf'))
    sidecar = Path(row['provenance']).read_text(encoding='utf-8')
    assert private not in sidecar and private not in json.dumps(row)
    assert json.loads(sidecar)['native_output_verification'] == row['native_output_verification']


def test_unexpected_native_boundary_error_does_not_log_private_payload(actual_record, tmp_path, monkeypatch):
    private = 'UNEXPECTED-PRIVATE-DOCUMENT-TEXT'
    def broken(*args, **kwargs):
        raise RuntimeError(private)
    monkeypatch.setattr(ra_public, 'build_downloads', broken)
    row = ra_public.evaluate_case(actual_record, PROFILE, mode='rules', artifact_dir=tmp_path, native_review='auto')
    assert not row['passed'] and not row['native_pending']
    assert private not in json.dumps(row)
    assert private not in Path(row['provenance']).read_text(encoding='utf-8')
    assert 'output' not in row


@pytest.mark.parametrize('mode', ['auto', 'required'])
def test_preview_is_saved_separately_and_required_warning_is_pending(actual_record, tmp_path, monkeypatch, mode):
    calls = copy_renderer(monkeypatch)
    row = ra_public.evaluate_case(actual_record, PROFILE, mode='rules', artifact_dir=tmp_path, native_review=mode)
    assert len(calls) == 2
    native = row['native_output_verification']['pdf']
    assert native['status'] == 'warning' and not native['blocking']
    assert any(issue['code'] == 'native_annex_scope' for issue in native['issues'])
    sidecar = json.loads(Path(row['provenance']).read_text(encoding='utf-8'))
    assert 'pdf_bytes' not in native and '_native_preview_bytes' not in sidecar
    if mode == 'required':
        assert not row['passed'] and row['native_pending']
        assert row['native_preview_artifacts'] == {}
        assert 'output' not in row
    else:
        assert row['passed']
        artifact = row['native_preview_artifacts']['pdf']
        preview = Path(artifact['path'])
        assert preview != Path(row['output'])
        assert sha256(preview.read_bytes()).hexdigest() == artifact['sha256'] == native['pdf_sha256']
        assert len(PdfReader(preview).pages) == artifact['page_count'] == native['page_count']
        assert sidecar['native_preview_artifacts'] == row['native_preview_artifacts']


def test_cli_native_flag_and_legacy_off_baseline_compatibility(actual_record, tmp_path, monkeypatch):
    calls = unavailable_renderer(monkeypatch)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'sources': [actual_record]}), encoding='utf-8')
    output = tmp_path / 'report.json'
    arguments = ['--manifest', str(manifest), '--profile', PROFILE, '--output', str(output),
                 '--artifact-dir', str(tmp_path / 'files')]
    assert ra_public.main(arguments) == 0
    previous = json.loads(output.read_text(encoding='utf-8'))
    assert previous['native_review'] == 'off' and calls == []
    previous.pop('native_review')
    baseline = tmp_path / 'baseline.json'
    baseline.write_text(json.dumps(previous), encoding='utf-8')
    assert ra_public.main([*arguments, '--baseline', str(baseline)]) == 0
    assert json.loads(output.read_text(encoding='utf-8'))['regressions'] == []
    with pytest.raises(ValueError, match='모드'):
        ra_public.main([*arguments, '--native-review', 'auto', '--baseline', str(baseline)])
    assert ra_public.main([*arguments, '--native-review', 'required', '--check-parser']) == 1
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['native_review'] == 'required' and report['passed_count'] == 0
    assert report['parser_results'][0]['passed']
    assert report['results'][0]['native_pending']


def test_invalid_native_mode_fails_before_source_or_model_work():
    with pytest.raises(ValueError, match='출력 확인 모드'):
        ra_public.evaluate({'sources': []}, native_review='unknown')
    with pytest.raises(ValueError, match='출력 확인 모드'):
        ra_public.evaluate_case({}, PROFILE, mode='rules', artifact_dir=Path('.'), native_review='unknown')


def test_repeated_attempts_keep_success_pending_and_retry_artifacts_separate(actual_record, tmp_path, monkeypatch):
    copy_renderer(monkeypatch)
    first = ra_public.evaluate_case(actual_record, PROFILE, mode='rules', artifact_dir=tmp_path, native_review='auto')
    first_paths = [Path(first['output']), Path(first['provenance']),
                   Path(first['native_preview_artifacts']['pdf']['path'])]
    first_hashes = {path: sha256(path.read_bytes()).hexdigest() for path in first_paths}
    assert first['passed']

    unavailable_renderer(monkeypatch)
    pending = ra_public.evaluate_case(actual_record, PROFILE, mode='rules', artifact_dir=tmp_path, native_review='required')
    assert not pending['passed'] and pending['native_pending']
    assert 'output' not in pending and pending['native_preview_artifacts'] == {}
    pending_path = Path(pending['provenance'])
    pending_bytes = pending_path.read_bytes()
    assert not list(pending_path.parent.glob('*.pdf'))
    pending_saved = json.loads(pending_bytes)
    assert not pending_saved['passed'] and 'output' not in pending_saved

    copy_renderer(monkeypatch)
    retry = ra_public.evaluate_case(actual_record, PROFILE, mode='rules', artifact_dir=tmp_path, native_review='auto')
    assert retry['passed']
    assert len({row['execution_id'] for row in (first, pending, retry)}) == 3
    assert len({Path(row['provenance']).parent for row in (first, pending, retry)}) == 3
    assert {path: sha256(path.read_bytes()).hexdigest() for path in first_paths} == first_hashes
    assert pending_path.read_bytes() == pending_bytes
    for row in (first, retry):
        sidecar = json.loads(Path(row['provenance']).read_text(encoding='utf-8'))
        assert sidecar['execution_id'] == row['execution_id']
        assert sidecar['output'] == row['output']
        assert Path(row['output']).parent == Path(row['provenance']).parent
        assert Path(row['native_preview_artifacts']['pdf']['path']).parent == Path(row['provenance']).parent
    assert first['scores'] == pending['scores'] == retry['scores']


def test_long_artifact_base_uses_short_windows_safe_execution_paths(actual_record, monkeypatch):
    copy_renderer(monkeypatch)
    with TemporaryDirectory(prefix='ra-path-') as directory:
        root = Path(directory)
        # Reproduce a deep artifact destination without changing OS long-path
        # policy; the old duplicate case stem exceeds MAX_PATH here.
        base = root / ('artifacts-' + 'x' * (170 - len(str(root)) - 11))
        assert len(str(base)) == 170
        old_stem = f"{actual_record['id']}_{PROFILE}_review"
        old_preview = base / f'{old_stem}_{"0" * 32}' / f'{old_stem}_native_preview_pdf.pdf'
        assert len(str(old_preview)) >= 260
        row = ra_public.evaluate_case(actual_record, PROFILE, mode='rules', artifact_dir=base, native_review='auto')
        assert row['passed'] and row['profile_id'] == PROFILE
        paths = [Path(row['output']), Path(row['provenance']), Path(row['native_preview_artifacts']['pdf']['path'])]
        assert all(path.is_file() and len(str(path)) < 260 for path in paths)
        assert {path.parent.name for path in paths} == {row['execution_id']}
        assert Path(row['output']).name == 'review.pdf'
        saved = json.loads(Path(row['provenance']).read_text(encoding='utf-8'))
        assert saved['execution_id'] == row['execution_id'] and saved['profile_id'] == PROFILE
        assert saved['source']['id'] == actual_record['id']
        assert saved['output'] == row['output']
