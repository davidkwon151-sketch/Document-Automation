"""Additional private native-PDF evidence, after independent position checks.

PDF text counts cannot certify complete layout. Short/common/format-dependent
values are explicitly unevaluated; XML/PDF field-position checks remain separate.
No document text or private local paths are copied into the public metadata.
"""
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import re
import tempfile
import unicodedata
from zipfile import ZipFile

from lxml import etree

from pypdf import PdfReader
from pypdf.constants import UserAccessPermissions

from templates.compatibility import analyze_template, _selected_fields
from parsers.extract import _check_zip, _xml


MAX_BYTES = 64 * 1024 * 1024
MAX_PAGES = 1000
MAX_CHARACTERS = 2000000
NUMERIC = re.compile(r'[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?')
COMMON = {'approved', 'rejected', 'pending', 'yes', 'no', 'true', 'false', '확인', '대기', '동의', '미동의'}
ENGINES = {'word_com', 'excel_com', 'powerpoint_com', 'pdf_copy', 'libreoffice', 'callable', 'fake_renderer'}


def _issue(code, message, severity='warning', field=None):
    issue = {'code': code, 'message': message, 'severity': severity}
    if field is not None:
        issue['field_id'] = field
    return issue


def _normalize(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFC', text))


def _bytes(path):
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError('파일이 없거나 64MiB 제한을 넘음')
    value = path.read_bytes()
    if not 0 < len(value) <= MAX_BYTES:
        raise ValueError('파일 크기가 검사 중 변경됨')
    return value


def _pdf_snapshot(path):
    """Read all pages sequentially, without OCR/protection removal or passwords."""
    import pypdfium2 as pdfium
    raw = _bytes(path)
    reader = PdfReader(path)
    if reader.is_encrypted:
        permissions = reader.user_access_permissions
        if permissions is None or not permissions & UserAccessPermissions.EXTRACT:
            raise ValueError('PDF 일반 추출 권한 제한으로 검수 보류')
        try:
            len(reader.pages)
        except Exception as exc:
            raise ValueError('암호가 필요한 PDF는 검수할 수 없음') from exc
    pages, clipped, character_count = [], Counter(), 0
    document = pdfium.PdfDocument(raw)
    try:
        if not 1 <= len(document) <= MAX_PAGES:
            raise ValueError('검수 가능한 PDF 페이지 수를 넘음')
        for number in range(len(document)):
            page = document[number]
            textpage = page.get_textpage()
            try:
                width, height = page.get_size()
                count = textpage.count_chars()
                character_count += count
                if character_count > MAX_CHARACTERS:
                    raise ValueError('검수 가능한 PDF 문자 수를 넘음')
                text = textpage.get_text_range()
                boxes = []
                for index in range(count):
                    character = textpage.get_text_range(index, 1)
                    if not character.strip():
                        continue
                    left, bottom, right, top = textpage.get_charbox(index)
                    # A small glyph-metric tolerance does not permit hidden lines.
                    outside = left < -.5 or bottom < -.5 or right > width + .5 or top > height + .5
                    if outside:
                        clipped[(number + 1, character, tuple(round(value, 1) for value in (left, bottom, right, top)))] += 1
                    boxes.append((index, character, outside))
                pages.append({'text': text, 'size': (width, height), 'boxes': boxes})
            finally:
                textpage.close()
                page.close()
    finally:
        document.close()
    return {'pages': pages, 'clipped': clipped, 'characters': character_count, 'sha256': sha256(raw).hexdigest(), 'bytes': raw}


def _occurrences(pages, expected, *, clipped_only=False):
    """Whitespace normalization with original word boundaries, never prefix hits."""
    needle = _normalize(expected)
    result = 0
    for page in pages:
        raw = unicodedata.normalize('NFC', page['text'])
        if clipped_only and (raw != page['text'] or any(
                raw[index:index + len(character)] != character
                for index, character, _ in page['boxes'])):
            # Do not infer glyph positions after Unicode/index transformations.
            return None
        clipped_indices = {index for index, _, outside in page['boxes'] if outside} if clipped_only else set()
        locations = [index for index, character in enumerate(raw) if not character.isspace()]
        flat = ''.join(raw[index] for index in locations)
        offset = 0
        while needle and (found := flat.find(needle, offset)) >= 0:
            first, last = locations[found], locations[found + len(needle) - 1]
            before, after = raw[first - 1:first], raw[last + 1:last + 2]
            # Whitespace/punctuation surrounding actual values remains a boundary.
            left_ok = not (needle[0].isalnum() and before and before.isalnum())
            right_ok = not (needle[-1].isalnum() and after and after.isalnum())
            if left_ok and right_ok and (not clipped_only or any(first <= index <= last for index in clipped_indices)):
                result += 1
            offset = found + len(needle)
    return result


def _ambiguous(field, value):
    plain = _normalize(value)
    return (len(plain) < 5 or bool(NUMERIC.fullmatch(value.strip()))
            or field.get('validation', {}).get('type') in {'integer', 'number'}
            or value.strip().casefold() in COMMON
            or field.get('control_type') in {'checkbox', 'radio', 'choice', 'combobox'}
            or field.get('kind') == 'pdf_form')


def _display_value(field, value):
    if field.get('control_type') in {'choice', 'radio', 'combobox'}:
        for item in field.get('choice_items', []):
            if item.get('value') == value:
                return item.get('label', value)
    return value


def _requests(selected):
    result = []
    for field, _ in selected:
        pieces = field['id'].split(':', 3)
        if len(pieces) >= 3 and pieces[0] == 'xlsx' and re.fullmatch(r'xl/worksheets/sheet[0-9]+\.xml', pieces[1]) and re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]*', pieces[2]):
            result.append({'id': field['id'], 'part': pieces[1], 'coordinate': pieces[2]})
    return result


def _physical_counts(source, selected):
    """Count source placeholders independently; a logical key may fill many slots."""
    keys = {field['id'][12:] for field, _ in selected if field.get('kind') == 'placeholder' and field['id'].startswith('placeholder:')}
    if not keys:
        return {}
    counts = Counter()
    kind = source.suffix.lower()
    allowed = {'.docx': r'word/.+\.xml', '.hwpx': r'Contents/(?:section|masterpage)[0-9]+\.xml', '.pptx': r'ppt/slides/slide[0-9]+\.xml'}
    if kind not in allowed:
        raise ValueError('전역 자리표시자의 물리적 위치를 확인할 수 없음')
    def visit(node, paragraph):
        name = etree.QName(node).localname
        if node is not paragraph and name == 'p':
            return ''
        if kind == '.docx' and node.tag == '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t':
            return node.text or ''
        if kind != '.docx' and name == 't':
            return (node.text or '') + ''.join(visit(child, paragraph) + (child.tail or '') for child in node)
        if name in {'br', 'cr', 'lineBreak', 'tab'}:
            return '\n' if name != 'tab' else '\t'
        return ''.join(visit(child, paragraph) for child in node if isinstance(child.tag, str))
    with ZipFile(source) as archive:
        _check_zip(archive)
        for name in archive.namelist():
            if not re.fullmatch(allowed[kind], name):
                continue
            root = _xml(archive.read(name))
            if kind == '.docx':
                w = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
                for control in list(root.iter(f'{{{w}}}sdt')):
                    properties = control.find(f'{{{w}}}sdtPr')
                    if properties is not None and any(etree.QName(child).localname in {'lock', 'dropDownList', 'comboBox', 'checkbox', 'date', 'picture', 'group', 'repeatingSection', 'repeatingSectionItem'} for child in properties if isinstance(child.tag, str)):
                        control.getparent().remove(control)
            for paragraph in root.xpath('.//*[local-name()="p"]'):
                for match in re.finditer(r'\{\{\s*([^{}]+?)\s*\}\}', visit(paragraph, paragraph)):
                    if match[1].strip() in keys:
                        counts[match[1].strip()] += 1
    if any(not counts[key] for key in keys):
        raise ValueError('선택한 전역 자리표시자가 원본에 없음')
    return {'placeholder:' + key: counts[key] for key in keys}


def _native_number(text):
    text = text.strip()
    if NUMERIC.fullmatch(text):
        return Decimal(text.replace(',', ''))
    return None


def _cell_expected(field, value, evidence, issues):
    """Use proven current cell Text; don't invent custom/localized format rules."""
    identity = field['id']
    if not isinstance(evidence, dict) or not isinstance(evidence.get('text'), str):
        issues.append(_issue('native_cell_evidence_unavailable', '셀별 네이티브 표시 검수가 불가하여 인쇄 위치 확인이 필요함', field=identity))
        return value, False
    if any(evidence.get(flag) is True for flag in ('row_hidden', 'column_hidden', 'sheet_hidden')) or evidence.get('within_print_area') is False:
        issues.append(_issue('native_cell_not_printed', '선택 입력칸이 숨김 또는 인쇄 영역 밖에 있음', 'error', identity))
        return value, False
    if any(type(evidence.get(flag)) is not bool for flag in ('row_hidden', 'column_hidden', 'sheet_hidden', 'within_print_area')):
        issues.append(_issue('native_cell_visibility_unknown', '셀 숨김·인쇄 영역의 정확한 확인이 필요함', field=identity))
        return value, False
    text = evidence['text']
    if not text.strip() or re.fullmatch(r'#+', text.strip()):
        issues.append(_issue('native_cell_display_missing', '선택 입력칸의 표시가 비어 있거나 숫자 너비 오류임', 'error', identity))
        return value, False
    if field.get('kind') != 'xlsx_cell':
        # Mixed placeholders retain surrounding original labels in Cell.Text.
        return value, False
    rule = field.get('validation', {})
    numeric = rule.get('type') in {'integer', 'number'} and (not rule.get('unit') or rule.get('unit_location') == 'label')
    if numeric:
        logical = _native_number(value)
        displayed = _native_number(text)
        if logical is None:
            return value, False
        if displayed is None or displayed != logical:
            issues.append(_issue('native_number_format_ambiguous', '원본 숫자 표시 형식의 반올림·통화·배율 등을 화면에서 확인해야 함', field=identity))
            return text, False
        return text, True
    if _normalize(text) != _normalize(_display_value(field, value)):
        issues.append(_issue('native_cell_value_mismatch', '셀의 네이티브 표시가 선택 입력값과 다름', 'error', identity))
        return text, False
    return text, True


def _render(renderer, source, target, requests):
    kwargs = {'timeout': 60}
    if requests:
        kwargs['cell_requests'] = requests
    metadata = renderer(source, target, **kwargs)
    if not isinstance(metadata, dict) or metadata.get('status') not in {'rendered', 'unavailable'}:
        raise ValueError('네이티브 renderer 응답 형식 오류')
    if metadata['status'] == 'unavailable':
        return metadata
    pdf = _bytes(target)
    if metadata.get('source_sha256') != sha256(_bytes(source)).hexdigest() or metadata.get('source_unchanged') is not True:
        raise ValueError('네이티브 renderer 원본 SHA·불변 증거 불일치')
    if metadata.get('output_sha256') != sha256(pdf).hexdigest():
        raise ValueError('네이티브 renderer PDF SHA 불일치')
    if metadata.get('engine') not in ENGINES or type(metadata.get('page_count')) is not int or metadata['page_count'] < 1:
        raise ValueError('네이티브 renderer 엔진 증거 없음')
    return metadata


def review_native_output(template_path, output_path, values, *, profile=None, mapping=None, renderer=None, work_dir=None):
    """Additional native-print evidence; bytes are private preview payload only.

    ``renderer`` has render_native_pdf's signature, including optional XLSX
    cell_requests. The caller must also retain verify_output's independent
    source-bound field checks. No automatic layout repair is attempted.
    """
    report = {'status': 'failed', 'blocking': True, 'issues': [], 'engine': None,
              'source_sha256': None, 'output_sha256': None, 'actualoutput_sha256': None,
              'pdf_sha256': None, 'source_pdf_sha256': None, 'page_count': 0,
              'checked_field_count': 0, 'checks': [],
              'coverage': {'selected_fields': 0, 'checked_fields': 0, 'ambiguous_fields': 0, 'failed_fields': 0},
              'scope': '추가 네이티브 PDF 인쇄 문구 증분·페이지 글자 경계 검사임. 정확한 입력 위치는 별도 원본 기반 검수이며 전체 배치·법적 적합성·모델 정확도 인증이 아님',
              'legal_compliance_certified': False, 'full_layout_certified': False}
    issues = report['issues']
    source = output = None
    source_raw = output_raw = None
    try:
        if not isinstance(template_path, (str, Path)) or not isinstance(output_path, (str, Path)):
            raise ValueError('원본·출력 경로 형식 오류')
        source, output = Path(template_path).resolve(), Path(output_path).resolve()
        if source == output or source.exists() and output.exists() and source.samefile(output):
            raise ValueError('원본과 작성본은 다른 파일이어야 함')
        source_raw, output_raw = _bytes(source), _bytes(output)
        report.update(source_sha256=sha256(source_raw).hexdigest(), output_sha256=sha256(output_raw).hexdigest(), actualoutput_sha256=sha256(output_raw).hexdigest())
        if not isinstance(values, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()):
            raise ValueError('검수 입력은 평면 문자열 사전이어야 함')
        profile = deepcopy(profile) if profile is not None else analyze_template(source)
        if profile.get('source_sha256') != report['source_sha256'] or profile.get('format') != source.suffix.lower()[1:]:
            raise ValueError('프로파일과 원본 SHA·형식 불일치')
        if output.suffix.lower() != source.suffix.lower():
            raise ValueError('원본·출력 형식 불일치')
        # User values passed to this API already obey the sidecar citation policy.
        from templates.pdf_annex import plan_annex
        printed, annex = plan_annex(profile, values, mapping)
        selected = _selected_fields(profile, printed, mapping)
        physical_counts = _physical_counts(source, selected)
        report['coverage']['selected_fields'] = sum(physical_counts.get(field['id'], 1) for field, _ in selected)
        requests = _requests(selected)
        if renderer is None:
            try:
                from parsers.native import render_native_pdf
                renderer = render_native_pdf
            except ImportError:
                issues.append(_issue('native_engine_unavailable', '설치된 네이티브 PDF 변환 엔진을 사용할 수 없음'))
                report.update(status='unavailable', blocking=False)
                return report
        # A fresh child directory prevents renderer artifacts from aliasing inputs.
        if work_dir is not None:
            Path(work_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='native-review-', dir=work_dir) as directory:
            original_pdf, filled_pdf = Path(directory) / 'source.pdf', Path(directory) / 'filled.pdf'
            before_meta = _render(renderer, source, original_pdf, requests)
            if before_meta['status'] == 'unavailable':
                issues.append(_issue('native_engine_unavailable', '원본 네이티브 변환을 사용할 수 없어 추가 검수가 보류됨'))
                report.update(status='unavailable', blocking=False, engine=before_meta.get('engine') if before_meta.get('engine') in ENGINES else None)
                return report
            after_meta = _render(renderer, output, filled_pdf, requests)
            if after_meta['status'] == 'unavailable':
                issues.append(_issue('native_engine_unavailable', '작성본 네이티브 변환을 사용할 수 없어 추가 검수가 보류됨'))
                report.update(status='unavailable', blocking=False, engine=after_meta.get('engine') if after_meta.get('engine') in ENGINES else None)
                return report
            report['engine'] = after_meta['engine']
            if source.suffix.lower() == '.xlsx':
                report['formula_recalculated'] = after_meta.get('formula_recalculated', False)
                report['calculation_state'] = after_meta.get('calculation_state')
                if before_meta.get('calculation_state') in {1, 2} or after_meta.get('calculation_state') in {1, 2}:
                    issues.append(_issue('native_calculation_pending', 'Excel이 계산 대기 상태를 표시함. 전체 수식 계산 완료는 미확인임'))
            if after_meta['engine'] != before_meta['engine']:
                issues.append(_issue('native_engine_pair_changed', '원본과 작성본의 변환 엔진이 달라 직접 화면 비교가 필요함'))
            before, after = _pdf_snapshot(original_pdf), _pdf_snapshot(filled_pdf)
            report.update(source_pdf_sha256=before['sha256'], pdf_sha256=after['sha256'], page_count=len(after['pages']), source_page_count=len(before['pages']))
            if after_meta.get('page_count') != len(after['pages']) or before_meta.get('page_count') != len(before['pages']):
                raise ValueError('네이티브 PDF 실제 페이지 수와 변환 증거 불일치')
            report['checks'].append({'name': 'all_pdf_pages_character_bounds', 'status': 'passed', 'pages': len(after['pages'])})
            new_clipped = after['clipped'] - before['clipped']
            if new_clipped:
                before_glyphs = Counter()
                after_glyphs = Counter()
                for (_, character, _), count in before['clipped'].items():
                    before_glyphs[character] += count
                for (_, character, _), count in after['clipped'].items():
                    after_glyphs[character] += count
                if after_glyphs - before_glyphs:
                    issues.append(_issue('native_new_page_clipping', '작성본에서 페이지 밖 글자가 증가함', 'error'))
                    report['checks'][-1]['status'] = 'failed'
                else:
                    issues.append(_issue('native_clipping_reflow_unknown', '원본부터 잘린 글자의 페이지·위치가 이동하여 화면 비교가 필요함'))
                    report['checks'][-1]['status'] = 'pending'
            if before['clipped']:
                issues.append(_issue('native_source_clipping', '원본 PDF에도 페이지 경계 밖의 글자가 있어 원본 화면 확인이 필요함'))
            if not after['characters']:
                issues.append(_issue('native_text_unavailable', 'PDF 인쇄 문구를 판독할 수 없어 추가 값 검수가 보류됨'))
            expected, field_ids, ambiguity, cell_failed = Counter(), {}, {}, {}
            cell_evidence = after_meta.get('cell_evidence', {})
            for field, value in selected:
                display = _display_value(field, value)
                issue_start = len(issues)
                if source.suffix.lower() == '.xlsx':
                    display, _ = _cell_expected(field, value, cell_evidence.get(field['id']), issues)
                key = _normalize(display)
                expected[key] += physical_counts.get(field['id'], 1)
                field_ids.setdefault(key, []).append(field['id'])
                new_cell_issues = issues[issue_start:]
                cell_failed[key] = cell_failed.get(key, False) or any(issue['severity'] == 'error' for issue in new_cell_issues)
                ambiguity[key] = ambiguity.get(key, False) or _ambiguous(field, display) or bool(new_cell_issues)
            for key, required in expected.items():
                if cell_failed[key]:
                    report['coverage']['failed_fields'] += required
                    continue
                if after['clipped']:
                    before_clipped_count = _occurrences(before['pages'], key, clipped_only=True)
                    after_clipped_count = _occurrences(after['pages'], key, clipped_only=True)
                    if before_clipped_count is None or after_clipped_count is None:
                        ambiguity[key] = True
                        issues.append(_issue('native_value_bounds_ambiguous', '입력값 문자와 PDF 글자 경계의 정확한 연결은 화면 확인이 필요함', field=field_ids[key][0]))
                    elif after_clipped_count > before_clipped_count:
                        issues.append(_issue('native_new_value_clipping', '선택 입력값에서 페이지 밖으로 나가는 인쇄가 증가함', 'error', field_ids[key][0]))
                        report['coverage']['failed_fields'] += required
                        report['checks'][0]['status'] = 'failed'
                        continue
                before_count = _occurrences(before['pages'], key)
                after_count = _occurrences(after['pages'], key)
                if ambiguity[key] or not after['characters']:
                    report['coverage']['ambiguous_fields'] += required
                    issues.append(_issue('native_value_ambiguous', '짧은 수치·선택 표시·반복 공통 문구의 위치별 인쇄 확인은 별도 화면 검수가 필요함', field=field_ids[key][0]))
                    continue
                if after_count - before_count < required:
                    if len(key) > 500:
                        report['coverage']['ambiguous_fields'] += required
                        issues.append(_issue('native_multiline_value_unverified', '페이지를 넘는 장문 값의 추가 인쇄 검수를 화면에서 확인해야 함', field=field_ids[key][0]))
                    else:
                        issues.append(_issue('native_value_missing', '원본에 이미 있는 문구를 제외하면 선택 입력값의 추가 인쇄가 부족함', 'error', field_ids[key][0]))
                        report['coverage']['failed_fields'] += required
                    continue
                report['checked_field_count'] += required
            if annex:
                issues.append(_issue('native_annex_scope', '별첨 전체 원문·출처와 원칸 참조는 별도 PDF 원위치·별첨 검수 결과로 확인함'))
            report['coverage']['checked_fields'] = report['checked_field_count']
            report['checks'].append({'name': 'selected_printed_text_increment', 'status': 'failed' if report['coverage']['failed_fields'] else 'pending' if report['coverage']['ambiguous_fields'] else 'passed', 'checked_fields': report['checked_field_count']})
            if source.read_bytes() != source_raw or output.read_bytes() != output_raw:
                raise ValueError('네이티브 검수 중 원본·작성본이 변경됨')
            report['checks'].append({'name': 'inputs_unchanged', 'status': 'passed'})
            report['blocking'] = any(issue['severity'] == 'error' for issue in issues)
            report['status'] = 'failed' if report['blocking'] else 'warning' if issues else 'passed'
            if not report['blocking']:
                report['pdf_bytes'] = after['bytes']
            return report
    except (TimeoutError, ImportError):
        issues.append(_issue('native_engine_unavailable', '네이티브 변환 시간 초과 또는 실행 환경 부재로 검수가 보류됨'))
        report.update(status='unavailable', blocking=False)
        return report
    except Exception as exc:
        try:
            from parsers.native import NativeRenderingUnavailable
        except ImportError:
            NativeRenderingUnavailable = None
        if NativeRenderingUnavailable is not None and isinstance(exc, NativeRenderingUnavailable):
            issues.append(_issue('native_rendering_guarded', '원본의 보호·서명·실행 요소를 유지하기 위해 네이티브 변환을 보류함'))
            report.update(status='unavailable', blocking=False)
            return report
        # Exception messages can contain private paths/document strings.
        issues.append(_issue('native_review_failed', '원본·출력·프로파일 또는 네이티브 변환 증거 검증에 실패함', 'error'))
        return report
    finally:
        if source_raw is not None and output_raw is not None:
            try:
                unchanged = source.read_bytes() == source_raw and output.read_bytes() == output_raw
            except Exception:
                unchanged = False
            if not unchanged:
                report.update(status='failed', blocking=True)
                report.pop('pdf_bytes', None)
                issues.append(_issue('native_inputs_changed', '검수 중 원본 또는 작성본이 변경됨', 'error'))
