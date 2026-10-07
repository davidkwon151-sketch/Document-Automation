"""Explicit, lossless overflow pages for verified PDF writing regions."""

from copy import deepcopy
from io import BytesIO
import re

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

HEADER = '작성 내용 별첨 - 검토용'
FOOTER = '제출기관의 별첨 허용 여부와 필수 첨부를 별도 확인해야 함'
CONTENT_LIMIT = 20000


def annex_enabled(profile):
    mode = (profile or {}).get('overflow_mode')
    if mode is None:
        return False
    if mode != 'annex' or profile.get('format') != 'pdf' or profile.get('render_mode') != 'overlay':
        raise ValueError('별첨은 작성 영역을 확인한 PDF에서만 명시적으로 사용할 수 있음')
    return True


def draft_limit(profile, field):
    if annex_enabled(profile) and field.get('kind') == 'pdf_overlay' and not (
        field.get('input_required') or field.get('input_mode') == 'user_provided' or field.get('control_type') or field.get('validation')
    ):
        return CONTENT_LIMIT
    return field.get('max_chars')


def _fits(field, value):
    from templates.compatibility import _pdf_font, _wrap_pdf_text
    maximum = field.get('max_chars')
    return (not maximum or len(value) <= maximum) and (
        len(_wrap_pdf_text(value, _pdf_font(field, value), float(field['font_size']), field['width']))
        * float(field['font_size']) * 1.3 <= field['height']
    )


def plan_annex(profile, values, mapping=None):
    """Returns original content separately from the short references printed in cells."""
    if not annex_enabled(profile):
        return values, []
    from templates.compatibility import _selected_fields
    relaxed = deepcopy(profile)
    for field in relaxed['fields']:
        field['max_chars'] = 0
    selected = _selected_fields(relaxed, values, mapping)
    originals = {field['id']: field for field in profile['fields']}
    grouped = {}
    for field, value in selected:
        key = mapping.get(field['id']) if mapping is not None else field['value_key']
        actual = originals[field['id']]
        limit = draft_limit(profile, actual)
        if limit and len(value) > limit:
            raise ValueError(f"별첨 또는 직접 입력 항목의 분량을 초과함: {actual['label']}")
        grouped.setdefault(key, []).append(actual)
    printed, entries = dict(values), []
    notes = profile.get('annex_sources', {})
    if not isinstance(notes, dict):
        raise ValueError('별첨 출처는 항목별 문자열 목록이어야 함')
    for key, fields in grouped.items():
        value = values[key]
        if all(_fits(field, value) for field in fields):
            continue
        if any(draft_limit(profile, field) != CONTENT_LIMIT for field in fields):
            raise ValueError('숫자·날짜·직접 입력·선택·서명 항목은 별첨으로 대신할 수 없음')
        number = len(entries) + 1
        reference = f'별첨 {number} 참조'
        if not all(_fits(field, reference) for field in fields):
            raise ValueError(f'별첨 참조 문구도 원본 입력칸에 들어가지 않음: {fields[0]["label"]}')
        sources = notes.get(key, [])
        if not isinstance(sources, list) or any(not isinstance(note, str) or len(note) > 2000 for note in sources):
            raise ValueError('별첨 출처는 길이가 제한된 문자열 목록이어야 함')
        entries.append({'number': number, 'label': fields[0]['label'], 'value_key': key,
                        'value': value, 'reference': reference, 'sources': sources,
                        'field_ids': [field['id'] for field in fields]})
        printed[key] = reference
    if sum(len(entry['value']) for entry in entries) > 100000:
        raise ValueError('별첨 전체 내용은 100000자 이하여야 함')
    return printed, entries


def annex_text(entry):
    lines = [f"별첨 {entry['number']}: {entry['label']}", entry['value']]
    lines.extend('출처: ' + note for note in entry['sources'])
    return '\n'.join(lines)


def append_annex(writer, entries):
    if not entries:
        return
    from templates.compatibility import _pdf_font, _wrap_pdf_text
    font = _pdf_font({}, HEADER + FOOTER + '\n'.join(annex_text(entry) for entry in entries))
    width, height = A4
    buffer = BytesIO()
    document = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    y = 0

    def page():
        document.setFont(font, 12)
        document.drawString(40, height - 36, HEADER)
        document.setFont(font, 8)
        document.drawString(40, 28, FOOTER)
        return height - 65

    y = page()
    for entry in entries:
        for line in _wrap_pdf_text(annex_text(entry), font, 10, width - 80):
            if y < 60:
                document.showPage()
                y = page()
            document.setFont(font, 10)
            document.drawString(40, y, line)
            y -= 14
    document.showPage()
    document.save()
    buffer.seek(0)
    for added in PdfReader(buffer).pages:
        writer.add_page(added)


def verify_annex(reader, original_pages, entries):
    """Checks all appended content, rather than trusting PDF metadata or the writer."""
    pages = reader.pages[original_pages:]
    if not 1 <= len(pages) <= 128:
        raise ValueError('출력 검증 실패: 별첨 페이지 수가 잘못됨')
    contents = []
    for page in pages:
        text = page.extract_text() or ''
        if HEADER not in text or FOOTER not in text:
            raise ValueError('출력 검증 실패: 별첨 안내문이 누락됨')
        body = text.replace(HEADER, '', 1).replace(FOOTER, '', 1)
        if not body.strip():
            raise ValueError('출력 검증 실패: 빈 별첨 페이지가 추가됨')
        contents.append(body)
    normalize = lambda text: re.sub(r'\s+', '', text)
    if normalize(''.join(contents)) != normalize('\n'.join(annex_text(entry) for entry in entries)):
        raise ValueError('출력 검증 실패: 별첨 원문·출처 누락, 변경 또는 추가 내용이 있음')
    return {'name': 'pdf_annex_complete_content', 'status': 'passed', 'pages': len(pages), 'fields': len(entries)}
