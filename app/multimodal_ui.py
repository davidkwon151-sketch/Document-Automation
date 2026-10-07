"""Reusable upload and original-text confirmation panel for document workflows."""
from hashlib import sha256
import json
from pathlib import Path
import tempfile

import streamlit as st

from agent.multimodal_intake import (SUPPORTED_SUFFIXES, collect_multimodal,
                                     confirm_intake, generation_sources)
from llm.client import ConfigurationError, LLMClient


def _fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def intake_uploads(files, *, client=None, allow_ocr=False, transcriptions=None):
    """Private temporary files disappear; provenance retains original SHA/name."""
    if len(files) > 20:
        raise ValueError('원자료를 20개 이하로 첨부해야 함')
    names = [file.name for file in files]
    if (len(set(names)) != len(names)
            or any(Path(name).name != name or '/' in name or '\\' in name for name in names)):
        raise ValueError('첨부 파일명 중복이나 경로를 허용하지 않음')
    with tempfile.TemporaryDirectory(prefix='document-intake-') as directory:
        paths = []
        for file in files:
            data = file.getvalue()
            if not isinstance(data, bytes) or not 0 < len(data) <= 30 * 1024 * 1024:
                raise ValueError('첨부 원본은 비어 있지 않은 30 MiB 이하 파일이어야 함')
            path = Path(directory) / file.name
            path.write_bytes(data)
            paths.append(path)
        return collect_multimodal(paths, client=client, allow_ocr=allow_ocr,
                                  transcriptions=transcriptions)


def render_multimodal_upload(files=None, *, key_prefix='mm', context=None):
    """Return sources/intake/signature/changed/ready; missing scans block readiness.

    Callers include the current workflow/product/template in context and clear
    generated/download results whenever changed is True. Only returned sources
    may enter writing; intake.sources also includes unconfirmed OCR proposals.
    """
    if not isinstance(key_prefix, str) or not key_prefix.strip():
        raise ValueError('화면별 고유 원자료 키가 필요함')
    prefix = key_prefix + '_mm_'
    if files is None:
        files = st.file_uploader('업무 원자료 · 문서/표/스캔/사진',
                                type=sorted(suffix[1:] for suffix in SUPPORTED_SUFFIXES),
                                accept_multiple_files=True, key=prefix + 'uploads') or []
    identity = _fingerprint({'files': [(file.name, sha256(file.getvalue()).hexdigest()) for file in files],
                             'context': context})
    if st.session_state.get(prefix + 'identity') != identity:
        # Other workflows' uploads/answers are not reused after a product/form change.
        for key in list(st.session_state):
            if key.startswith(prefix) and key != prefix + 'uploads':
                st.session_state.pop(key, None)
        st.session_state[prefix + 'identity'] = identity
    st.caption('PDF · DOCX · HWP/HWPX · XLSX · TXT/CSV · PPTX · PNG/JPEG. 사진·스캔 읽기는 선택하며 원문 대조 후 사용합니다.')
    allow_ocr = st.checkbox('이미지 지원 모델로 사진·스캔 원문 읽기', key=prefix + 'image_reading')
    st.caption('이미지를 읽지 못하는 로컬 모델이나 잔액 부족이면 자동 대체하지 않습니다. 수동 전사 후 원본 확인도 가능합니다.')
    transcriptions = st.session_state.get(prefix + 'transcriptions', {})
    input_signature = _fingerprint({'identity': identity, 'allow_ocr': allow_ocr,
                                    'transcriptions': transcriptions})
    if st.session_state.get(prefix + 'input_signature') != input_signature:
        client = None
        if allow_ocr and files:
            try:
                client = LLMClient()
            except ConfigurationError:
                st.warning('이미지 지원 모델 설정을 확인할 수 없습니다. 읽을 수 있는 문서는 유지하고 스캔·사진은 보류합니다.')
        with st.spinner('원본 SHA·페이지·원문 위치를 기록하고 있습니다.'):
            intake = intake_uploads(files, client=client, allow_ocr=allow_ocr,
                                    transcriptions=transcriptions)
        st.session_state[prefix + 'intake'] = intake
        st.session_state[prefix + 'input_signature'] = input_signature
    intake = st.session_state[prefix + 'intake']
    by_name = {file.name: file for file in files}
    transcription_editors = []
    editing_names = set()
    for record in intake['files']:
        if record['status'] == 'deferred':
            st.warning(record['filename'] + ': ' + record.get('reason', '원문 확인을 완료하지 못함'))
        if (not record.get('manual_transcription_available')
                and record.get('extraction_mode') != 'manual_transcription'):
            continue
        digest = record['document_sha256']
        upload = by_name[record['filename']]
        with st.expander(record['filename'] + ' · 원본을 보고 직접 전사'):
            pages = record.get('transcription_pages', [entry['page'] for entry in transcriptions.get(digest, [])]) or [1]
            if record['format'] != 'pdf':
                st.image(upload.getvalue(), caption='확인할 원본 이미지', width='stretch')
            st.caption('전체 원문을 옮기며 숫자·단위·조건을 추정하지 마세요. 저장 후 원문 대조 확인이 필요합니다.')
            if len(pages) > 50:
                st.info('50쪽을 넘는 스캔은 원문 확인을 위해 파일을 나누어 주세요.')
                continue
            entries = []
            for page in pages:
                text = st.text_area(f'{page}쪽 원문 전사', key=prefix + f'transcribe_{digest}_{page}')
                entries.append({'page': page, 'text': text})
            transcription_editors.append({'document_sha256': digest, 'entries': entries})
            if digest in transcriptions and transcriptions[digest] != entries:
                editing_names.add(record['filename'])
                st.info('전사문이 수정 중입니다. 새 전사문을 등록하고 다시 원본 대조해야 작성할 수 있습니다.')
            if st.button('전사문 등록/수정 · 아직 원문 확인 전', key=prefix + 'save_transcription_' + digest,
                         disabled=not all(entry['text'].strip() for entry in entries)):
                st.session_state[prefix + 'transcriptions'] = {**transcriptions, digest: entries}
                st.session_state.pop(prefix + 'input_signature', None)
                st.rerun()
    pending = [source for source in intake['sources'] if source['requires_verification']]
    receipts = []
    if pending:
        st.write('사진·스캔·전사문 원본 대조 확인')
        for source in pending:
            token = source['verification_fingerprint']
            with st.expander(f"{source['filename']} · {source.get('page') or source['location']} · {source['source_id']}"):
                upload = by_name[source['filename']]
                if Path(upload.name).suffix.lower() in {'.png', '.jpg', '.jpeg'}:
                    st.image(upload.getvalue(), caption='원문 대조 대상', width='stretch')
                else:
                    st.download_button('원본 내려받아 페이지·원문 대조', upload.getvalue(),
                                       file_name=upload.name, key=prefix + 'original_' + token)
                st.text(source['context_text'])
                st.caption('SHA: ' + source['document_sha256'] + ' · 원문 위치: ' + source['location'])
                for item in source['uncertain_items']:
                    st.caption('원문에서 확인할 항목: ' + item)
                if st.checkbox('이 원문 조각·숫자·단위·조건을 원본과 대조했음',
                               key=prefix + 'confirm_' + token,
                               disabled=source['filename'] in editing_names) and source['filename'] not in editing_names:
                    receipts.append({'source_id': source['source_id'], 'fingerprint': token})
    # Rebuild from current checkboxes: unchecking a receipt removes its authority.
    current = {**intake, 'confirmations': []}
    if receipts:
        current = confirm_intake(current, receipts, confirmed=True)
    sources = [source for source in generation_sources(current) if source['filename'] not in editing_names]
    accepted_ids = {source['source_id'] for source in sources}
    unconfirmed = [source for source in pending if source['source_id'] not in accepted_ids]
    deferred = [record for record in current['files'] if record['status'] == 'deferred']
    signature = _fingerprint({'intake': current['fingerprint'], 'confirmations': current['confirmations'],
                             'context': context, 'transcription_editors': transcription_editors})
    changed = st.session_state.get(prefix + 'result_signature') != signature
    st.session_state[prefix + 'result_signature'] = signature
    st.caption(f'작성에 사용 가능한 원문 {len(sources)}개 · 원문 미확인 {len(unconfirmed)}개 · 보류 파일 {len(deferred)}개')
    return {'sources': sources, 'intake': current, 'signature': signature, 'changed': changed,
            'deferred_files': deferred, 'pending_sources': unconfirmed,
            'ready': not bool(deferred or unconfirmed)}
