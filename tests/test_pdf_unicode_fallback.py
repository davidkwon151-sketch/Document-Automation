"""Keep RA source glyphs intact across overlay, annex, and native appearances."""

from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfgen import canvas
import pytest

from agent.output_check import verify_output
from templates import analyze_template, fill_compatible_template
from templates.compatibility import _pdf_font, _wrap_pdf_text


ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / 'templates/fonts/NotoSansKR-Regular.ttf'
VALUE = '▪ 액티민주사 실온(1~30℃) 보관'


def form(tmp_path):
    source = tmp_path / 'source.pdf'
    drawing = canvas.Canvas(str(source))
    drawing.drawString(40, 750, 'Original form and instructions')
    drawing.save()
    field = {'id': 'content', 'label': '본문', 'value_key': '본문', 'kind': 'pdf_overlay',
             'page': 1, 'x': 70, 'y': 120, 'width': 350, 'height': 80, 'font_size': 10,
             'max_chars': 200, 'required': True, 'input_required': False}
    return source, analyze_template(source, manual_fields=[field])


def test_native_ra_glyphs_are_preserved_in_overlay_and_original_background(tmp_path):
    source, profile = form(tmp_path)
    before = source.read_bytes()
    output = tmp_path / 'filled.pdf'
    fill_compatible_template(source, {'본문': VALUE}, output, profile=profile)
    assert source.read_bytes() == before
    assert VALUE in PdfReader(output).pages[0].extract_text()
    assert verify_output(source, output, {'본문': VALUE}, profile=profile)['status'] == 'passed'


def test_annex_keeps_complete_ra_bullet_and_temperature_source(tmp_path):
    source, profile = form(tmp_path)
    profile.update(overflow_mode='annex', annex_sources={'본문': ['[S1] official.pdf / 1쪽']})
    profile['fields'][0].update(height=15, max_chars=20)
    value = '\n'.join(f'{VALUE} / 원문 {index}' for index in range(15))
    output = tmp_path / 'annex.pdf'
    fill_compatible_template(source, {'본문': value}, output, profile=profile)
    text = ''.join(page.extract_text() or '' for page in PdfReader(output).pages[1:])
    assert text.count('▪') == text.count('℃') == 15
    assert verify_output(source, output, {'본문': value}, profile=profile)['status'] == 'passed'


def test_two_native_fields_with_different_font_coverage_have_distinct_embedded_fonts(tmp_path):
    source = tmp_path / 'form.pdf'
    drawing = canvas.Canvas(str(source))
    drawing.drawString(40, 750, 'Original native form')
    for index, name in enumerate(('ordinary', 'fallback')):
        drawing.acroForm.textfield(name=name, x=70, y=620 - index * 100, width=360, height=65,
                                  value='', fontName='Helvetica', fontSize=10)
    drawing.save()
    profile = analyze_template(source)
    values = {'ordinary': '한글 기본 값', 'fallback': VALUE}
    output = tmp_path / 'native-filled.pdf'
    fill_compatible_template(source, values, output, profile=profile)
    reader = PdfReader(output)
    fields = reader.get_fields()
    assert {name: str(fields[name]['/V']) for name in values} == values
    widgets = [reference.get_object() for reference in reader.pages[0]['/Annots']]
    fonts = reader.trailer['/Root']['/AcroForm']['/DR']['/Font']
    resources = [widget['/DA'].split()[0] for widget in widgets]
    assert all(resource in fonts for resource in resources)
    # Some hosts already have a complete default font. On this Windows host the
    # installed Malgun lacks ▪, so the second widget must bind its own font.
    if _pdf_font({}, values['ordinary']) != _pdf_font({}, values['fallback']):
        assert len(set(resources)) == 2
    assert verify_output(source, output, values, profile=profile)['status'] == 'passed'


def test_explicit_font_choice_is_honoured_and_unknown_glyph_is_not_replaced(tmp_path):
    source, profile = form(tmp_path)
    profile['fields'][0]['font_path'] = str(FONT)
    output = tmp_path / 'previous.pdf'
    previous = b'previous result'
    output.write_bytes(previous)
    with pytest.raises(ValueError, match='표시할 수 없는 문자'):
        fill_compatible_template(source, {'본문': '\U0010ffff'}, output, profile=profile)
    assert output.read_bytes() == previous


def test_bundle_can_render_source_symbols_without_an_installed_korean_font():
    font = _pdf_font({'font_path': str(FONT)}, '▪℃≥µ한글')
    assert ''.join(_wrap_pdf_text('▪℃≥µ한글', font, 10, 500)) == '▪℃≥µ한글'


def test_original_auto_size_and_center_alignment_survive_short_native_field(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, NumberObject, TextStringObject
    source = tmp_path / 'short-native.pdf'
    drawing = canvas.Canvas(str(source))
    drawing.drawString(40, 750, 'Original form')
    drawing.acroForm.textfield(name='name', x=70, y=600, width=220, height=13.32, fontSize=10)
    drawing.save()
    writer = PdfWriter(clone_from=source)
    widget = writer.pages[0]['/Annots'][0].get_object()
    widget[NameObject('/DA')] = TextStringObject('/Helv 0 Tf 0 g')
    widget[NameObject('/Q')] = NumberObject(1)
    with source.open('wb') as stream:
        writer.write(stream)
    profile = analyze_template(source)
    before = source.read_bytes()
    output = tmp_path / 'short-completed.pdf'
    fill_compatible_template(source, {'name': VALUE}, output, profile=profile)
    filled = PdfReader(output)
    actual = filled.pages[0]['/Annots'][0].get_object()
    assert actual['/Q'] == 1 and ' 0 Tf ' in actual['/DA']
    assert filled.get_fields()['name']['/V'] == VALUE
    assert source.read_bytes() == before
    assert verify_output(source, output, {'name': VALUE}, profile=profile)['status'] == 'passed'


def test_fixed_source_font_is_not_silently_shrunk_for_short_native_field(tmp_path):
    source = tmp_path / 'fixed.pdf'
    drawing = canvas.Canvas(str(source))
    drawing.drawString(40, 750, 'Original fixed-font form')
    drawing.acroForm.textfield(name='name', x=70, y=600, width=220, height=13.32, fontSize=10)
    drawing.save()
    output = tmp_path / 'unchanged.pdf'
    output.write_bytes(b'previous result')
    with pytest.raises(ValueError, match='넘침'):
        fill_compatible_template(source, {'name': VALUE}, output, profile=analyze_template(source))
    assert output.read_bytes() == b'previous result'
