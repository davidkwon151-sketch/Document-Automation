"""One provenance contract for office files, scanned PDFs and source photos.

Parsing and model calls stay in the existing parsers/LLM boundary. Image text
is a proposal until the user checks that exact text against the original.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import warnings

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

from agent.retrieve import chunk_documents, load_documents
from llm.client import LLMError
from parsers.extract import ParseError, _block, _path, _result


SUPPORTED_SUFFIXES = {'.pdf', '.docx', '.hwpx', '.hwp', '.xlsx', '.txt', '.csv', '.tsv',
                      '.pptx', '.png', '.jpg', '.jpeg'}
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg'}
NON_EVIDENCE_ORIGINS = {'model', 'llm', 'ai', 'generated', 'model_generated', 'llm_generated',
                        'ai_generated', 'mock', 'demo', 'user', 'user_input', '사용자 입력',
                        'ai 생성', '모델 생성'}


def _fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _source_fingerprint(source):
    return _fingerprint({key: value for key, value in source.items()
                         if key != 'verification_fingerprint'})


def _intake_fingerprint(intake):
    return _fingerprint({key: intake[key] for key in ('documents', 'sources', 'files')})


def _validate_image(path):
    try:
        with Image.open(path) as image:
            if image.format not in {'PNG', 'JPEG'} or image.n_frames != 1:
                raise ParseError('단일 PNG/JPEG 원본 이미지가 필요함', 'invalid_image')
            if image.width * image.height > 16_000_000:
                raise ParseError('이미지는 1,600만 픽셀 이하이어야 함', 'too_large')
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ParseError('이미지 파일 구조를 확인할 수 없음', 'invalid_image') from exc


def _pdf_transcription_pages(path):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        try:
            document = load_documents([path], allow_ocr=False)[0]
        except ParseError as exc:
            if exc.code != 'ocr_required':
                raise
            return None, list(range(1, len(PdfReader(path).pages) + 1))
    reader = PdfReader(path)
    pages = [block['페이지'] for block in document['페이지/시트 정보']
             if not block['본문'].strip() and block.get('페이지')
             and reader.pages[block['페이지'] - 1].images]
    return document, pages


def _manual_document(path, entries):
    """Whole-image/page transcription; never use it to bypass PDF permissions."""
    if not isinstance(entries, list) or not entries:
        raise ParseError('원문 페이지별 전사문이 필요함', 'invalid_transcription')
    if path.suffix.lower() in IMAGE_SUFFIXES:
        _validate_image(path)
        document, required_pages = None, [1]
    elif path.suffix.lower() == '.pdf':
        # The normal parser checks EXTRACT permission before any image read.
        document, required_pages = _pdf_transcription_pages(path)
        if not required_pages:
            raise ParseError('읽을 수 있는 PDF 본문을 임의 전사문으로 덮어쓰지 않음',
                             'invalid_transcription')
    else:
        raise ParseError('수동 전사는 PNG/JPEG 또는 PDF의 읽지 못한 스캔 페이지에만 적용함',
                         'invalid_transcription')
    if len(required_pages) > 50:
        raise ParseError('전사할 스캔 페이지는 파일당 50쪽 이하로 나누어야 함', 'too_large')
    pages = []
    blocks = []
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'page', 'text'}
                or type(entry['page']) is not int or not isinstance(entry['text'], str)
                or not entry['text'].strip() or len(entry['text']) > 100_000):
            raise ParseError('전사문은 페이지 번호와 비어 있지 않은 원문 문자열이어야 함',
                             'invalid_transcription')
        pages.append(entry['page'])
        block = _block(entry['text'], f"페이지 {entry['page']}/담당자 전사", page=entry['page'])
        block.update({'검증 필요': True, '추출 방식': '담당자 전사 / 원본 대조 대기',
                      '불확실한 항목': ['전사문 전체를 원본 이미지와 대조해야 함']})
        blocks.append(block)
    if sorted(pages) != required_pages:
        raise ParseError('읽지 못한 전체 스캔 페이지의 전사문을 중복 없이 등록해야 함',
                         'invalid_transcription')
    if document:
        blocks = [block for block in document['페이지/시트 정보']
                  if block.get('페이지') not in required_pages] + blocks
    return _result(path, sorted(blocks, key=lambda block: block['페이지']),
                   document['표 목록'] if document else [])


def collect_multimodal(paths, *, client=None, allow_ocr=False, transcriptions=None,
                       max_source_chars=None, max_block_chars=None):
    """Return parseable sources plus explicit deferred files, without auto fallback.

    transcriptions maps the original file SHA to [{page, text}]. It contains
    actual user transcription, not model metadata or learned form-cache values.
    A failed multi-page image read contributes no partial sources for that file.
    """
    paths = [Path(path) for path in paths]
    if any(value is not None and (type(value) is not int or value < 1)
           for value in (max_source_chars, max_block_chars)):
        raise ValueError('원문 분량 한도는 양의 정수이어야 함')
    if type(allow_ocr) is not bool or len(paths) > 20:
        raise ValueError('이미지 읽기 여부를 지정하고 원자료는 20개 이하로 첨부해야 함')
    if len({path.name for path in paths}) != len(paths):
        raise ValueError('중복 파일명은 서로 다른 이름으로 첨부해야 함')
    transcriptions = {} if transcriptions is None else transcriptions
    if not isinstance(transcriptions, dict):
        raise ValueError('전사문은 원본 SHA별 등록 객체여야 함')
    documents, files = [], []
    seen_sha = set()
    for path in paths:
        record = {'filename': path.name, 'format': path.suffix.lower().lstrip('.'),
                  'status': 'deferred', 'source_ids': [], 'document_sha256': None,
                  'manual_transcription_available': False}
        try:
            _path(path)
            digest = sha256(path.read_bytes()).hexdigest()
            seen_sha.add(digest)
            record['document_sha256'] = digest
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                raise ParseError('지원 형식의 문서 또는 PNG/JPEG 이미지를 첨부해야 함',
                                 'unsupported_format')
            if path.suffix.lower() in IMAGE_SUFFIXES:
                _validate_image(path)
            if digest in transcriptions:
                document = _manual_document(path, transcriptions[digest])
                document['document_sha256'] = digest
                record['extraction_mode'] = 'manual_transcription'
            else:
                if (allow_ocr and path.suffix.lower() in IMAGE_SUFFIXES
                        and path.stat().st_size > 10 * 1024 * 1024):
                    raise ParseError('이미지 모델 요청은 10 MiB 이하 원본이어야 함. 원본을 보고 '
                                     '직접 전사하거나 지원 크기의 사본을 따로 첨부해야 함',
                                     'image_too_large')
                document = load_documents([path], client=client, allow_ocr=allow_ocr)[0]
                record['extraction_mode'] = ('image_reading' if any(
                    block.get('추출 방식') == 'AI OCR'
                    for block in document['페이지/시트 정보']) else 'file_parser')
            if sha256(path.read_bytes()).hexdigest() != digest:
                raise ParseError('원본이 읽는 동안 변경됨. 다시 첨부해야 함', 'source_changed')
            # An unread scanned page must not silently disappear from a mixed PDF.
            if path.suffix.lower() == '.pdf':
                reader = PdfReader(path)
                unread = [block['페이지'] for block in document['페이지/시트 정보']
                          if not block['본문'].strip() and block.get('페이지')
                          and reader.pages[block['페이지'] - 1].images]
                if unread:
                    raise ParseError('PDF에 읽지 못한 스캔 페이지가 남아 있음. 이미지 읽기 또는 '
                                     '텍스트가 포함된 PDF가 필요함', 'ocr_required')
            documents.append(document)
            record['status'] = ('needs_confirmation' if any(
                block.get('검증 필요') for block in document['페이지/시트 정보']) else 'ready')
        except ParseError as exc:
            record.update(reason=str(exc), reason_code=exc.code,
                          manual_transcription_available=(exc.code in {'ocr_required', 'invalid_ocr', 'image_too_large'}
                                                           and path.suffix.lower() in IMAGE_SUFFIXES | {'.pdf'}))
        except LLMError as exc:
            # Safe class labels only. Provider messages/credentials stay out of records.
            record.update(reason='이미지 읽기 모델 호출을 완료하지 못함. 이미지 지원 모델을 명시적으로 '
                          '설정하거나 원본 확인 후 수동 전사문을 등록해야 함',
                          reason_code='image_model_unavailable', model_error_kind=exc.kind,
                          manual_transcription_available=True)
        if record.get('manual_transcription_available'):
            record['transcription_pages'] = (_pdf_transcription_pages(path)[1]
                                              if path.suffix.lower() == '.pdf' else [1])
        files.append(record)
    if set(transcriptions) - seen_sha:
        raise ValueError('현재 첨부 원본 SHA와 연결되지 않은 전사문은 사용할 수 없음')
    # Optional service bounds run before chunking: a huge single paragraph would
    # otherwise be repeated as full context for every 800-character chunk.
    if max_source_chars is not None and sum(len(document['본문']) for document in documents) > max_source_chars:
        raise ParseError('서버 원문 분량 한도를 초과함. 더 작은 원자료 파일 묶음으로 나누어 첨부해야 함',
                         'intake_too_large')
    if max_block_chars is not None:
        for document in documents:
            blocks = document['페이지/시트 정보']
            texts = [block['본문'] for block in blocks] if blocks else [document['본문']]
            texts += [' | '.join(str(cell) for cell in row)
                      for table in document['표 목록'] if isinstance(table, dict)
                      for row in table.get('행', table.get('rows', []))]
            if any(len(text) > max_block_chars for text in texts):
                raise ParseError('서버 원문 블록 분량 한도를 초과함. 문단·표 행을 분리한 원자료를 별도로 제공해야 함',
                                 'intake_too_large')
    sources = chunk_documents(documents)
    by_file = {record['filename']: record for record in files}
    for source in sources:
        source.update(id=source['source_id'], quote=source['text'],
                      source_location={name: source[name] for name in ('filename', 'page', 'sheet', 'location')},
                      source_context=source['context_text'])
        source['extraction_mode'] = (by_file[source['filename']]['extraction_mode']
                                     if source['requires_verification'] else 'file_parser')
        source['verification_fingerprint'] = _source_fingerprint(source)
    for record in files:
        record['source_ids'] = [source['source_id'] for source in sources
                                if source['filename'] == record['filename']
                                and source['document_sha256'] == record['document_sha256']]
        if record['source_ids']:
            record['status'] = ('needs_confirmation' if any(
                source['requires_verification'] for source in sources
                if source['source_id'] in record['source_ids']) else 'ready')
    result = {'documents': documents, 'sources': sources, 'files': files, 'confirmations': []}
    result['fingerprint'] = _intake_fingerprint(result)
    return result


def append_multimodal_intake(intake, paths):
    """Add new originals while retaining checks for unchanged source blocks."""
    _check_intake(intake)
    added = collect_multimodal(paths)
    if {record['filename'].casefold() for record in intake['files']} & {
            record['filename'].casefold() for record in added['files']}:
        raise ValueError('이미 등록된 파일명은 다시 첨부할 수 없음')
    merged = {key: deepcopy(intake[key]) + added[key]
              for key in ('documents', 'sources', 'files')}
    merged['confirmations'] = deepcopy(intake['confirmations'])
    merged['fingerprint'] = _intake_fingerprint(merged)
    _check_intake(merged)
    return merged


def validate_source_origin(source):
    """Reject declared model/demo/user data as original-file evidence.

    Reviewed OCR extracts its source image rather than generating new facts.
    extraction_mode and verification_receipt remain allowed here.
    """
    if not isinstance(source, dict):
        raise ValueError('원자료는 출처 객체이어야 함')
    for key in ('origin', 'source_origin', 'source_type', 'source_kind', 'filename'):
        value = source.get(key)
        if isinstance(value, str) and value.strip().casefold().replace('-', '_') in NON_EVIDENCE_ORIGINS:
            raise ValueError('모델·mock·직접 입력 기록을 실제 원자료로 승인할 수 없음')
    return True


def validate_generation_source(source):
    """Validate a single accepted source before copying it into a work package.

    Consumers keep this original record before adding their own citation alias.
    The receipt describes original-text review, not approval of clinical facts.
    """
    validate_source_origin(source)
    if source.get('requires_verification') is not False:
        raise ValueError('원문 확인이 완료된 원자료만 작성에 사용할 수 있음')
    original = deepcopy(source)
    receipt = original.pop('verification_receipt', None)
    if 'verification_receipt' in source:
        if (not isinstance(receipt, dict)
                or set(receipt) != {'source_id', 'fingerprint', 'document_sha256', 'confirmed',
                                    'original_uncertain_items', 'review_scope'}
                or receipt.get('confirmed') is not True or source.get('uncertain_items') != []
                or receipt.get('source_id') != source.get('source_id')
                or receipt.get('document_sha256') != source.get('document_sha256')
                or receipt.get('fingerprint') != source.get('verification_fingerprint')
                or not isinstance(receipt.get('original_uncertain_items'), list)
                or receipt.get('review_scope') != '원본과 전사문 대조 / 사실·법정 적합성 승인 아님'):
            raise ValueError('원문 확인 기록이 현재 원자료와 일치하지 않음')
        original['requires_verification'] = True
        original['uncertain_items'] = receipt['original_uncertain_items']
    if original.get('verification_fingerprint') != _source_fingerprint(original):
        raise ValueError('확인 후 원문·페이지·SHA·출처가 변경됨')
    return True


def _check_intake(intake):
    if (not isinstance(intake, dict)
            or not {'documents', 'sources', 'files', 'confirmations', 'fingerprint'} <= set(intake)
            or not isinstance(intake['sources'], list)
            or not isinstance(intake['confirmations'], list)
            or intake.get('fingerprint') != _intake_fingerprint(intake)
            or any(source.get('verification_fingerprint') != _source_fingerprint(source)
                   for source in intake['sources'])):
        raise ValueError('원본·페이지·원문 또는 확인 지문이 변경됨. 원자료를 다시 확인해야 함')
    ids = [source['source_id'] for source in intake['sources']]
    if len(set(ids)) != len(ids):
        raise ValueError('원자료 ID가 중복됨')


def _receipts(intake):
    result = {}
    sources = {source['source_id']: source for source in intake['sources']}
    for receipt in intake['confirmations']:
        if (not isinstance(receipt, dict)
                or set(receipt) != {'source_id', 'fingerprint', 'document_sha256', 'confirmed'}):
            raise ValueError('원문 확인 기록 형식이 잘못됨')
        source = sources.get(receipt['source_id'])
        if (source is None or receipt['source_id'] in result or receipt['confirmed'] is not True
                or receipt['fingerprint'] != source['verification_fingerprint']
                or receipt['document_sha256'] != source['document_sha256']):
            raise ValueError('원문 확인 기록이 현재 원자료와 일치하지 않음')
        result[receipt['source_id']] = receipt
    return result


def confirm_intake(intake, receipts, *, confirmed=False):
    """Record deliberate source review, bound to the exact current text/SHA/page."""
    _check_intake(intake)
    if confirmed is not True or not isinstance(receipts, list) or not receipts:
        raise ValueError('원문 대조 후 명시적 확인이 필요함')
    sources = {source['source_id']: source for source in intake['sources']}
    known = _receipts(intake)
    seen = set()
    for receipt in receipts:
        if not isinstance(receipt, dict) or set(receipt) != {'source_id', 'fingerprint'}:
            raise ValueError('확인 대상의 원자료 ID와 현재 지문이 필요함')
        source = sources.get(receipt['source_id'])
        if (source is None or receipt['source_id'] in seen
                or receipt['fingerprint'] != source['verification_fingerprint']
                or not source['requires_verification']):
            raise ValueError('미확인 원자료의 현재 지문과 일치하지 않음')
        seen.add(receipt['source_id'])
        known[source['source_id']] = {'source_id': source['source_id'],
                                     'fingerprint': source['verification_fingerprint'],
                                     'document_sha256': source['document_sha256'],
                                     'confirmed': True}
    result = deepcopy(intake)
    result['confirmations'] = list(known.values())
    return result


def generation_sources(intake):
    """Only ordinary parsed text or explicitly reviewed image/transcribed sources."""
    _check_intake(intake)
    known = _receipts(intake)
    result = []
    for source in intake['sources']:
        receipt = known.get(source['source_id'])
        if source['requires_verification'] and receipt is None:
            continue
        accepted = deepcopy(source)
        if receipt:
            accepted['verification_receipt'] = {**receipt,
                                                 'original_uncertain_items': source['uncertain_items'],
                                                 'review_scope': '원본과 전사문 대조 / 사실·법정 적합성 승인 아님'}
            accepted['requires_verification'] = False
            accepted['uncertain_items'] = []
        validate_generation_source(accepted)
        result.append(accepted)
    return result
