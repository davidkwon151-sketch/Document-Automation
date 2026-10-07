"""Local text/CSV, legacy conversion and explicitly traceable scanned-page reading."""

from pathlib import Path
from io import BytesIO
import csv
import warnings
from concurrent.futures import ThreadPoolExecutor

from .extract import ParseError, _path, _block, _table, _result, parse_file


def render_pdf_page(path, page=1, scale=1.5) -> bytes:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(_path(path)))
    try:
        if type(page) is not int or not 1 <= page <= len(document):
            raise ParseError("PDF 페이지 번호가 잘못됨")
        item = document[page - 1]
        try:
            width, height = item.get_size()
            if width * height * scale * scale > 16_000_000:
                scale = (16_000_000 / (width * height)) ** .5
            bitmap = item.render(scale=scale)
            try:
                image = bitmap.to_pil()
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                return buffer.getvalue()
            finally:
                bitmap.close()
        finally:
            item.close()
    finally:
        document.close()


def _ocr_block(path, image, page, client):
    if client is None or not hasattr(client, "read_image_json"):
        raise ParseError("스캔 자료는 이미지 읽기 가능한 LLM 설정 또는 텍스트가 포함된 자료가 필요함", "ocr_required")
    mime = "image/jpeg" if image.startswith(b"\xff\xd8\xff") else "image/png"
    response = client.read_image_json(image, mime=mime, payload={"파일명": path.name, "페이지": page})
    text, rows, uncertain = response.get('본문'), response.get('표 목록', []), response.get('불확실한 항목', [])
    if not isinstance(text, str) or not isinstance(rows, list) or not isinstance(uncertain, list) or any(not isinstance(item, str) for item in uncertain):
        raise ParseError("이미지 읽기 응답 형식이 잘못됨", "invalid_ocr")
    tables = []
    for index, matrix in enumerate(rows, 1):
        if not isinstance(matrix, list) or any(not isinstance(row, list) or any(not isinstance(cell, str) for cell in row) for row in matrix):
            raise ParseError("이미지 표 응답 형식이 잘못됨", "invalid_ocr")
        tables.append(_table(matrix, f"페이지 {page}/OCR 표 {index}", page=page))
        tables[-1]['검증 필요'] = True
    if not text.strip() and tables:
        text = '\n'.join(' | '.join(row) for table in tables for row in table['행'])
    block = _block(text, f"페이지 {page}/이미지 읽기", page=page, tables=tables)
    block.update({"추출 방식": "AI OCR", "검증 필요": True, "불확실한 항목": uncertain})
    return block


def _pdf_form_blocks(reader):
    blocks = []
    fields = reader.get_fields() or {}
    seen = set()
    for page_number, page in enumerate(reader.pages, 1):
        for reference in page.get('/Annots', []):
            widget = reference.get_object()
            names = []
            node = widget
            for _ in range(20):
                if node.get('/T'):
                    names.append(str(node['/T']))
                if not node.get('/Parent'):
                    break
                node = node['/Parent'].get_object()
            name = '.'.join(reversed(names))
            value = fields.get(name, {}).get('/V', widget.get('/V', node.get('/V')))
            if value is None or not str(value).strip() or (page_number, name) in seen:
                continue
            seen.add((page_number, name))
            text = ', '.join(map(str, value)) if isinstance(value, list) else str(value)
            blocks.append(_block(f'{name} {text}', f'페이지 {page_number}/PDF 입력칸 {name}', page=page_number))
    return blocks


def _text_blocks(text):
    """Keep contiguous original lines together; blank lines end their context."""
    blocks, lines, start = [], [], None
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        if line.strip():
            if start is None:
                start = number
            lines.append(line)
        elif lines:
            location = f"줄 {start}" if start == number - 1 else f"줄 {start}~{number - 1}"
            blocks.append(_block(''.join(lines), location))
            lines, start = [], None
    if lines:
        location = f"줄 {start}" if start == number else f"줄 {start}~{number}"
        blocks.append(_block(''.join(lines), location))
    return blocks


def parse_extended(path, *, client=None, allow_ocr=True, converter=None) -> dict:
    path = _path(path)
    suffix = path.suffix.lower()
    if suffix in {'.txt', '.csv', '.tsv'}:
        raw = path.read_bytes()
        for encoding in ('utf-8-sig', 'cp949'):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeError:
                continue
        else:
            raise ParseError("UTF-8 또는 CP949 텍스트가 필요함")
        if suffix == '.txt':
            result = _result(path, _text_blocks(text), [])
            result['본문'] = text
            return result
        rows = list(csv.reader(text.splitlines(), delimiter='\t' if suffix == '.tsv' else ','))
        blocks = [_block(' | '.join(row), f"행 {number}", sheet=path.stem) for number, row in enumerate(rows, 1) if any(row)]
        return _result(path, blocks, [_table(rows, '전체 표', sheet=path.stem)])
    if suffix == '.hwp' and converter is None:
        from .hwp import parse_hwp
        return parse_hwp(path)
    if suffix in {'.doc', '.xls', '.hwp', '.odt', '.rtf'}:
        import tempfile
        from .legacy import convert_legacy
        with tempfile.TemporaryDirectory(prefix='report-import-') as directory:
            converted = convert_legacy(path, output_dir=directory, engine=converter)
            result = parse_file(converted)
        result['파일명'] = path.name
        for block in result['페이지/시트 정보']:
            block['추출 방식'] = f"{suffix[1:].upper()} 변환"
        return result
    if suffix in {'.png', '.jpg', '.jpeg'}:
        if not allow_ocr:
            raise ParseError("이미지 원자료 읽기가 꺼져 있음", "ocr_required")
        block = _ocr_block(path, path.read_bytes(), 1, client)
        return _result(path, [block], block['표 목록'])
    if suffix != '.pdf' or not allow_ocr:
        return parse_file(path)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        try:
            result = parse_file(path)
        except ParseError as exc:
            if exc.code != 'ocr_required':
                raise
            result = None
    from pypdf import PdfReader
    reader = PdfReader(path)
    form_blocks = _pdf_form_blocks(reader)
    if result and all(block['본문'].strip() for block in result['페이지/시트 정보']):
        if form_blocks:
            result['페이지/시트 정보'].extend(form_blocks)
            result['본문'] += '\n' + '\n'.join(block['본문'] for block in form_blocks)
        return result
    if len(reader.pages) > 50:
        raise ParseError("이미지 읽기는 한 파일당 50쪽 이하로 나누어야 함", 'too_large')
    blocks = result['페이지/시트 정보'] if result else [_block('', f"페이지 {i}", page=i) for i in range(1, len(reader.pages) + 1)]
    pending = []
    for i, block in enumerate(blocks):
        if not block['본문'].strip():
            if any(item['페이지'] == i + 1 for item in form_blocks):
                continue
            # An empty decorative page without images is not an evidence source.
            if result and not reader.pages[i].images:
                continue
            pending.append(i)
    # PDFium rendering stays sequential; independent network reads run in bounded batches.
    if pending and getattr(client, 'sequential_inference', False):
        # A remote conversation resumes one pending request at a time.
        for index in pending:
            blocks[index] = _ocr_block(path, render_pdf_page(path, index + 1), index + 1, client)
    elif pending:
        with ThreadPoolExecutor(max_workers=4) as executor:
            for offset in range(0, len(pending), 4):
                batch = pending[offset:offset + 4]
                jobs = [(index, executor.submit(_ocr_block, path, render_pdf_page(path, index + 1), index + 1, client)) for index in batch]
                for index, future in jobs:
                    blocks[index] = future.result()
    blocks.extend(form_blocks)
    tables = [table for block in blocks for table in block['표 목록']]
    return _result(path, blocks, tables)
