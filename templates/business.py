"""Official public business submission forms with dated provenance and SHA pins."""

from copy import deepcopy
from hashlib import file_digest
import json
from pathlib import Path
import re

from .catalog import CatalogError, _download_verified_entry, _https_host, _safe_filename

CATALOG_PATH = Path(__file__).with_name('business_catalog.json')
DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / 'data/public_templates/business'
BUSINESS_WORKFLOWS = {
    'trade_sales': '해외영업·수출', 'overseas_business': '해외사업·진출',
    'government_grant': '정부지원사업', 'rd_project': '국가 연구개발사업',
}
OFFICIAL_HOSTS = {'www.kotra.or.kr', 'kotra.or.kr', 'www.mss.go.kr', 'mss.go.kr',
                  'www.iris.go.kr', 'esg.kosmes.or.kr', 'www.bizinfo.go.kr', 'sssf.seoultech.ac.kr'}
FORMATS = {'HWP', 'HWPX', 'DOCX', 'XLSX', 'PDF', 'PPTX'}


def load_business_catalog() -> dict:
    """Return verified originals; an old notice never implies current recruitment."""
    data = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or data.get('workflows') != BUSINESS_WORKFLOWS or not isinstance(data.get('documents'), list):
        raise CatalogError('사업 양식 카탈로그 구조·워크플로가 올바르지 않음')
    identifiers, filenames = set(), set()
    for entry in data['documents']:
        if not entry.get('id') or entry['id'] in identifiers or not entry.get('title') or not entry.get('version_label') or not entry.get('publisher'):
            raise CatalogError('사업 문서 ID·제목·발행기관·버전이 없거나 중복됨')
        identifiers.add(entry['id'])
        filename = _safe_filename(entry['filename'])
        if filename in filenames or entry.get('format') not in FORMATS or not filename.endswith('.' + entry['format'].lower()):
            raise CatalogError('사업 파일명·형식이 올바르지 않거나 중복됨')
        filenames.add(filename)
        workflows = entry.get('workflows')
        if not isinstance(workflows, list) or not workflows or len(set(workflows)) != len(workflows) or any(w not in BUSINESS_WORKFLOWS for w in workflows):
            raise CatalogError('미등록 또는 중복 사업 워크플로임')
        hosts = entry.get('allowed_hosts')
        if not isinstance(hosts, list) or not hosts or any(host not in OFFICIAL_HOSTS for host in hosts):
            raise CatalogError('관찰한 사업 공식 다운로드 호스트만 허용함')
        if _https_host(entry['source_page']) not in OFFICIAL_HOSTS or _https_host(entry['source_url']) not in hosts:
            raise CatalogError('사업 원본과 출처 페이지가 공식 HTTPS 출처가 아님')
        expected = {'blank_form': ('public_form', 'public_submission'),
                    'layout_reference': ('public_guide', 'reference_guidance')}.get(entry.get('resource_kind'))
        if expected is None or (entry.get('source_kind'), entry.get('use_classification')) != expected:
            raise CatalogError('사업 작성용 서식과 참고자료를 구분해야 함')
        if any(entry.get(flag) is not False for flag in ('company_internal', 'legal_compliance_certified', 'mandatory_requirements_verified', 'full_submission_ready', 'fill_verified')) or entry.get('license', {}).get('redistribution_permitted') is not False:
            raise CatalogError('사업 사내 양식·법적 적합성·전체 기입·재배포 권한을 추정할 수 없음')
        if entry.get('application_status') not in {'closed', 'not_assessed'}:
            raise CatalogError('공고 원본 수집만으로 현재 지원 가능 여부를 인증할 수 없음')
        if entry.get('download_status') not in {'verified', 'failed', 'login_required', 'blocked_drm'}:
            raise CatalogError('사업 원본 다운로드 상태가 올바르지 않음')
        if entry['download_status'] == 'verified' and (
            not re.fullmatch('[0-9a-f]{64}', entry.get('sha256') or '') or type(entry.get('size_bytes')) is not int or not 0 < entry['size_bytes'] <= 64 * 1024 * 1024
        ):
            raise CatalogError('사업 확인 원본의 SHA·크기가 없음')
    return data


def list_business_templates(workflow: str | None = None) -> list[dict]:
    if workflow is not None and workflow not in BUSINESS_WORKFLOWS:
        raise CatalogError('등록되지 않은 사업 워크플로임')
    return [deepcopy(entry) for entry in load_business_catalog()['documents']
            if workflow is None or workflow in entry['workflows']]


def download_business_template(entry: dict, directory: str | Path = DEFAULT_DIRECTORY) -> Path:
    """Accept pinned catalog originals only; preserve changed local files for review."""
    approved = next((item for item in list_business_templates() if item['id'] == entry.get('id')), None)
    if approved is None or approved['download_status'] != 'verified':
        raise CatalogError('등록·확인된 사업 원본만 다운로드할 수 있음')
    for key in ('source_url', 'filename', 'format', 'allowed_hosts', 'sha256'):
        if entry.get(key) != approved.get(key):
            raise CatalogError('사업 요청이 관찰·등록한 공식 원본과 다름')
    parent = Path(directory).resolve()
    target = parent / _safe_filename(approved['filename'])
    if target.is_symlink() or target.resolve().parent != parent:
        raise CatalogError('사업 원본 캐시 경로가 지정 폴더를 벗어남')
    if target.is_file():
        with target.open('rb') as stream:
            digest = file_digest(stream, 'sha256').hexdigest()
        if digest != approved['sha256']:
            raise CatalogError('기존 사업 원본 해시가 달라 다시 확인해야 함')
        return target
    return _download_verified_entry(approved, directory)
