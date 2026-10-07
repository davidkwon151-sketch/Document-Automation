"""Pinned official public business forms; internal company-wide compatibility is unknown."""

from copy import deepcopy
from hashlib import file_digest
import json
from pathlib import Path
import re

from templates.catalog import CatalogError, _download_verified_entry, _https_host, _safe_filename

CATALOG_PATH=Path(__file__).with_name('international_catalog.json')
DEFAULT_DIRECTORY=Path(__file__).resolve().parents[1]/'data/public_templates/international'


def load_international_catalog() -> dict:
    data=json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    if data.get('schema_version')!=1 or not isinstance(data.get('documents'),list):
        raise CatalogError('국제 업무 양식 카탈로그 구조가 올바르지 않음')
    identifiers,filenames=set(),set()
    for entry in data['documents']:
        if not entry.get('id') or entry['id'] in identifiers or not entry.get('company'):
            raise CatalogError('국제 업무 양식의 ID·게시 기업이 없거나 중복됨')
        identifiers.add(entry['id'])
        filename=_safe_filename(entry['filename'])
        if filename in filenames:raise CatalogError('국제 업무 양식 파일명이 중복됨')
        filenames.add(filename)
        if entry.get('format') not in {'DOCX','XLSX','PPTX','PDF'} or not filename.endswith('.'+entry['format'].lower()):
            raise CatalogError('국제 업무 양식의 형식과 파일명이 다름')
        _https_host(entry['source_page'])
        if _https_host(entry['source_url']) not in entry.get('allowed_hosts',[]):
            raise CatalogError('관찰한 공식 원본 호스트가 등록되지 않음')
        for host in entry['allowed_hosts']:_https_host('https://'+host)
        if (entry.get('download_status')!='verified' or not re.fullmatch('[0-9a-f]{64}',entry.get('sha256') or '')
                or type(entry.get('size_bytes')) is not int or entry['size_bytes']<=0):
            raise CatalogError('국제 업무 원본 확인 해시·크기가 없음')
        if (entry.get('source_kind')!='public_form' or entry.get('resource_kind')!='blank_form'
                or entry.get('use_classification')!='company_public_submission' or entry.get('fill_verified') is not False
                or entry.get('internal_forms_verified') is not False or entry.get('korean_entity_employment_verified') is not False
                or entry.get('license',{}).get('redistribution_permitted') is not False):
            raise CatalogError('공개 제출 서식의 용도·미검증 범위·권한을 추정할 수 없음')
    return data


def list_international_templates(company: str | None = None) -> list[dict]:
    return [deepcopy(entry) for entry in load_international_catalog()['documents']
            if company is None or company in {entry['company'],*entry.get('company_aliases',[])}]


def download_international_template(entry: dict,directory: str | Path = DEFAULT_DIRECTORY) -> Path:
    approved=next((item for item in list_international_templates() if item['id']==entry.get('id')),None)
    if approved is None:raise CatalogError('등록되지 않은 국제 업무 공개 양식임')
    for key in ('source_url','filename','format','allowed_hosts','sha256'):
        if entry.get(key)!=approved.get(key):raise CatalogError('요청 문서가 관찰한 공식 원본과 다름')
    target_dir=Path(directory).resolve()
    target=target_dir/_safe_filename(approved['filename'])
    if target.is_symlink() or target.resolve().parent!=target_dir:
        raise CatalogError('국제 양식 캐시 경로가 지정 폴더를 벗어남')
    if target.is_file():
        with target.open('rb') as stream:digest=file_digest(stream,'sha256').hexdigest()
        if digest!=approved['sha256']:raise CatalogError('기존 국제 양식 원본 해시가 달라 다시 확인해야 함')
        return target
    return _download_verified_entry(approved,directory)
