"""IT 기업 공개 자료와 스타트업 제출 서식; 회사 내부 기안양식 인증 목록이 아님."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re

from templates.catalog import CatalogError, _download_verified_entry, _https_host, _safe_filename

CATALOG_PATH = Path(__file__).with_name('tech_catalog.json')
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / 'data' / 'public_templates' / 'tech'
FORMATS = {'HWP', 'HWPX', 'DOCX', 'XLSX', 'PDF'}


def load_tech_catalog() -> dict:
    """Return observed sources with address, original-download, and internal-form limits."""
    catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    if catalog.get('schema_version') != 1 or not isinstance(catalog.get('companies'), list):
        raise CatalogError('기술기업 카탈로그 구조가 올바르지 않음')
    ids, documents = set(), set()
    for profile in catalog['companies'] + catalog.get('startup_programs', []):
        if not profile.get('id') or profile['id'] in ids or not profile.get('name'):
            raise CatalogError('기업/지원사업 프로필 식별자가 없거나 중복됨')
        ids.add(profile['id'])
        _https_host(profile['official_url'])
        if profile.get('internal_forms_status') != 'unknown':
            raise CatalogError('비공개 사내 기안양식 호환을 인증할 수 없음')
        location = profile.get('location', {})
        if location.get('status') == 'verified':
            if not location.get('address') or location.get('kind') not in {'hq', 'office'}:
                raise CatalogError('확인된 소재지의 주소와 HQ/office 구분이 없음')
            _https_host(location['source_url'])
        elif location.get('status') != 'unknown':
            raise CatalogError('주소 확인 상태가 올바르지 않음')
        for entry in profile.get('documents', []):
            if not entry.get('id') or entry['id'] in documents:
                raise CatalogError('기술기업 문서 ID가 없거나 중복됨')
            documents.add(entry['id'])
            _https_host(entry['source_page'])
            host = _https_host(entry['source_url'])
            if host not in entry.get('allowed_hosts', []):
                raise CatalogError('관찰한 공식 다운로드 호스트가 등록되지 않음')
            for allowed in entry['allowed_hosts']:
                _https_host('https://' + allowed)
            filename = _safe_filename(entry['filename'])
            if entry.get('format') not in FORMATS or not filename.lower().endswith('.' + entry['format'].lower()):
                raise CatalogError('기술기업 문서의 형식과 파일명이 일치하지 않음')
            if entry.get('resource_kind') not in {'blank_form', 'layout_reference'}:
                raise CatalogError('참고 보고서와 빈 서식 구분이 없음')
            if entry.get('source_kind') not in {'public_form', 'published_report', 'public_policy', 'financial_data'}:
                raise CatalogError('기술기업 공개 문서 종류가 올바르지 않음')
            if entry['resource_kind'] == 'blank_form' and entry['source_kind'] != 'public_form':
                raise CatalogError('공개 보고서를 작성용 빈 서식으로 인증할 수 없음')
            if entry.get('use_classification') not in {'company_public_submission', 'startup_submission', 'reference_report'}:
                raise CatalogError('회사 외부 제출용/스타트업 지원사업/참고 자료 구분이 없음')
            if entry.get('company_internal') is not False or entry.get('license', {}).get('redistribution_permitted') is not False:
                raise CatalogError('사내 양식 인증이나 원본 재배포 권한을 추정할 수 없음')
            if entry.get('download_status') not in {'verified', 'failed', 'blocked_drm', 'login_required'}:
                raise CatalogError('원본 다운로드 상태가 올바르지 않음')
            if entry['download_status'] == 'verified' and (
                not re.fullmatch(r'[0-9a-f]{64}', entry.get('sha256') or '')
                or type(entry.get('size_bytes')) is not int or entry['size_bytes'] <= 0
            ):
                raise CatalogError('검증된 원본의 해시·크기가 없음')
        if profile.get('status') == 'verified_public_form' and not any(
            e['download_status'] == 'verified' and e['resource_kind'] == 'blank_form'
            for e in profile.get('documents', [])
        ):
            raise CatalogError('공개 빈 서식의 실제 원본 확인 근거가 없음')
        if profile.get('status') == 'layout_reference' and not any(
            e['download_status'] == 'verified' for e in profile.get('documents', [])
        ):
            raise CatalogError('참고 자료 원본 확인 근거가 없음')
    return catalog


def list_tech_templates(company: str | None = None) -> list[dict]:
    """List observed originals; a startup_submission belongs to the named public program."""
    catalog = load_tech_catalog()
    entries = []
    for profile in catalog['companies'] + catalog.get('startup_programs', []):
        if company is not None and company not in {profile['id'], profile['name'], *profile.get('aliases', [])}:
            continue
        for entry in profile.get('documents', []):
            entries.append({**deepcopy(entry), 'company': profile['name'], 'profile_id': profile['id']})
    return entries


def download_tech_template(entry: dict, directory: str | Path = DEFAULT_DIRECTORY) -> Path:
    """Pin the exact observed original; existing source URLs cannot be substituted."""
    approved = next((item for item in list_tech_templates() if item['id'] == entry.get('id')), None)
    if approved is None:
        raise CatalogError('등록되지 않은 기술기업 문서임')
    for key in ('source_url', 'filename', 'format', 'allowed_hosts', 'sha256'):
        if entry.get(key) != approved.get(key):
            raise CatalogError('요청 문서가 등록된 공식 원본과 다름')
    return _download_verified_entry(approved, directory)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='IT 기업 공개 참고 자료·스타트업 제출 서식 목록')
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list')
    listing.add_argument('--company')
    download = commands.add_parser('download')
    download.add_argument('id')
    download.add_argument('--directory', type=Path, default=DEFAULT_DIRECTORY)
    args = parser.parse_args(argv)
    if args.command == 'list':
        print(json.dumps(list_tech_templates(args.company), ensure_ascii=False, indent=2))
    else:
        entry = next((item for item in list_tech_templates() if item['id'] == args.id), None)
        if entry is None:
            parser.error('등록된 기술기업 문서 ID가 필요함')
        print(download_tech_template(entry, args.directory))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
