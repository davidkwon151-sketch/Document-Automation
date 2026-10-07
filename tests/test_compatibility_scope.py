import json

import pytest

from evals import compatibility


def test_ra_scope_avoids_other_catalogs_and_downloads(tmp_path, monkeypatch):
    from templates import catalog, ra

    def unrelated(*args, **kwargs):
        raise AssertionError('다른 출처를 읽거나 다운로드하면 안 됨')

    monkeypatch.setattr(catalog, 'list_public_templates', unrelated)
    monkeypatch.setattr(catalog, 'download_public_template', unrelated)
    entry = {'id': 'ra-example', 'filename': 'form.pdf', 'publisher': 'MFDS',
             'source_url': 'https://www.mfds.go.kr/example', 'source_kind': 'public_form',
             'resource_kind': 'blank_form', 'use_classification': 'regulatory_submission',
             'workflows': ['gmp'], 'sha256': 'a' * 64, 'download_status': 'verified'}
    monkeypatch.setattr(ra, 'list_ra_templates', lambda: [entry])
    downloaded = []
    monkeypatch.setattr(ra, 'download_ra_template',
                        lambda e, p: downloaded.append(e['id']) or p / e['filename'])
    jobs = compatibility.collect_jobs(tmp_path, provenance='official_ra', download=True)
    assert downloaded == ['ra-example']
    assert len(jobs) == 1 and jobs[0]['provenance'] == 'official_ra'
    assert compatibility.collect_jobs(tmp_path, provenance='official_ra', include_ra=False) == []


def test_synthetic_scope_only_contains_explicit_fixtures(tmp_path):
    jobs = compatibility.collect_jobs(tmp_path, provenance='synthetic_fixture')
    assert len(jobs) == 6
    assert all(job['provenance'] == 'synthetic_fixture' for job in jobs)


@pytest.mark.parametrize('kwargs', [dict(provenance='made_up'),
                                   dict(group='삼성', provenance='official_ra')])
def test_invalid_scope_fails_before_fetching(tmp_path, kwargs):
    with pytest.raises(ValueError):
        compatibility.collect_jobs(tmp_path, **kwargs)


def test_cli_records_selected_scope_without_government_discovery(tmp_path, monkeypatch):
    requested = []
    monkeypatch.setattr(compatibility, 'collect_jobs',
                        lambda *args, **kwargs: requested.append(kwargs) or [])
    monkeypatch.setattr(compatibility, 'run_corpus', lambda *args, **kwargs: {
        'summary': {'failed_count': 0}, 'results': []})
    def unrelated(*args):
        raise AssertionError('정부 탐색 분모를 RA 검사에 넣으면 안 됨')
    monkeypatch.setattr(compatibility, 'discovery_statistics', unrelated)
    output = tmp_path / 'report.json'
    assert compatibility.main(['--provenance', 'official_ra', '--output', str(output)]) == 0
    assert requested[0]['provenance'] == 'official_ra'
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['scope'] == {'provenance': 'official_ra', 'group': None, 'downloads_requested': False}
    assert report['government_discovery'] is None
