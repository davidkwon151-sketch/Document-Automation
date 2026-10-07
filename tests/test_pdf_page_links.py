"""Only unresolved Widget page references can be repaired in a new copy."""
from hashlib import sha256
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject, TextStringObject
from reportlab.pdfgen import canvas
import pytest

from templates import TemplateError, analyze_template, fill_compatible_template


def form(tmp_path, mode):
    source = tmp_path/'source.pdf'
    drawing = canvas.Canvas(str(source))
    drawing.acroForm.textfield(name='Work', x=50, y=600, width=200, height=24)
    drawing.showPage(); drawing.drawString(50, 600, 'Second page'); drawing.showPage(); drawing.save()
    writer = PdfWriter(); writer.clone_document_from_reader(PdfReader(source))
    reference = writer.pages[0]['/Annots'][0]
    widget = reference.get_object()
    if mode == 'dangling':
        widget[NameObject('/P')] = IndirectObject(9999, 0, writer)
    elif mode == 'wrong_page':
        widget[NameObject('/P')] = writer.pages[1].indirect_reference
    elif mode == 'duplicate_page':
        writer.pages[1][NameObject('/Annots')] = ArrayObject([reference])
    elif mode == 'orphan':
        writer.root_object['/AcroForm'][NameObject('/Fields')] = ArrayObject([])
    elif mode == 'duplicate_canonical':
        writer.root_object['/AcroForm']['/Fields'].append(reference)
    elif mode == 'null':
        from pypdf.generic import NullObject
        widget[NameObject('/P')] = NullObject()
    with source.open('wb') as stream:
        writer.write(stream)
    return source


def test_dangling_widget_page_link_is_restored_only_in_output(tmp_path):
    source = form(tmp_path, 'dangling'); original = source.read_bytes()
    reader = PdfReader(source)
    assert reader.pages[0]['/Annots'][0].get_object()['/P'] is None
    output = fill_compatible_template(source, {'Work': 'TEST WORK'}, tmp_path/'output.pdf')
    after = PdfReader(output); widget = after.pages[0]['/Annots'][0].get_object()
    assert widget.get('/P') == after.pages[0].indirect_reference
    assert str(after.get_fields()['Work']['/V']) == 'TEST WORK'
    assert source.read_bytes() == original


@pytest.mark.parametrize('mode', ['wrong_page', 'duplicate_page', 'orphan', 'duplicate_canonical', 'null'])
def test_ambiguous_or_real_wrong_page_never_repaired(mode, tmp_path):
    source = form(tmp_path, mode); before = source.read_bytes()
    profile = analyze_template(source)
    # Even a supplied field mapping cannot bypass canonical/tree validation.
    if not profile['fields']:
        profile['fields'] = [{'id': 'pdf:Work', 'label': 'Work', 'kind': 'pdf_form',
                              'value_key': 'Work', 'required': False}]
        profile.update(supported=True, render_mode='acroform')
    output = tmp_path/'existing.pdf'; output.write_bytes(b'KEEP EXISTING OUTPUT')
    with pytest.raises(TemplateError, match='페이지|연결|canonical|중복'):
        fill_compatible_template(source, {'Work': 'TEST'}, output, profile=profile)
    assert source.read_bytes() == before and output.read_bytes() == b'KEEP EXISTING OUTPUT'


def test_actual_pace_dangling_widgets_preserve_ordinal_canonical_and_source(tmp_path):
    source = Path(__file__).resolve().parents[1]/'data/public_templates/ra_extended/pace_single_lot.pdf'
    if not source.is_file():
        pytest.skip('Official public fixture is not present')
    before = source.read_bytes(); old = PdfReader(source)
    assert sha256(before).hexdigest() == '55a70a0c086616bae9f8ba5d2b6731a72ee9d8e58054fa478191c8dfb1d0c94f'
    bad = [i for i, reference in enumerate(old.pages[0]['/Annots'])
           if isinstance(reference.get_object().get('/P'), IndirectObject)
           and reference.get_object().get('/P').get_object() is None]
    assert len(bad) == 30
    output = fill_compatible_template(source, {'Project Title': 'QA ONLY'}, tmp_path/'pace.pdf')
    new = PdfReader(output)
    assert list(old.get_fields()) == list(new.get_fields())
    assert len(old.pages[0]['/Annots']) == len(new.pages[0]['/Annots'])
    for ordinal in bad:
        a = old.pages[0]['/Annots'][ordinal].get_object()
        b = new.pages[0]['/Annots'][ordinal].get_object()
        assert a.get('/T') == b.get('/T') and a['/Rect'] == b['/Rect']
        assert b.get('/P') == new.pages[0].indirect_reference
    assert source.read_bytes() == before


@pytest.mark.parametrize('protection', ['certified', 'signed', 'encrypted'])
def test_protected_sources_do_not_enter_link_repair(protection, tmp_path):
    source = form(tmp_path, 'dangling')
    writer = PdfWriter(); writer.clone_document_from_reader(PdfReader(source))
    if protection == 'certified':
        writer.root_object[NameObject('/Perms')] = DictionaryObject()
        writer.root_object['/Perms'][NameObject('/DocMDP')] = TextStringObject('DO NOT CHANGE')
    elif protection == 'signed':
        field = writer.root_object['/AcroForm']['/Fields'][0].get_object()
        field[NameObject('/FT')] = NameObject('/Sig')
        field[NameObject('/V')] = DictionaryObject({NameObject('/Type'): NameObject('/Sig')})
    else:
        writer._ID = None  # A fresh encryption fixture needs binary file IDs.
        writer.encrypt('secret')
    with source.open('wb') as stream: writer.write(stream)
    before = source.read_bytes(); output = tmp_path/'output.pdf'
    with pytest.raises(TemplateError):
        fill_compatible_template(source, {'Work': 'TEST'}, output)
    assert source.read_bytes() == before and not output.exists()


def test_actual_cambrex_ascii_fixed_font_overflow_is_rejected_atomically(tmp_path):
    source = Path(__file__).resolve().parents[1]/'data/public_templates/ra_extended/cambrex_durham.pdf'
    if not source.is_file(): pytest.skip('Official public fixture is not present')
    profile = analyze_template(source)
    field = next(f for f in profile['fields'] if f['id'] == 'pdf:QtyRow1')
    profile['fields'] = [field]
    before = source.read_bytes(); output = tmp_path/'previous.pdf'; output.write_bytes(b'PREVIOUS OUTPUT')
    with pytest.raises(TemplateError, match='넘침'):
        fill_compatible_template(source, {field['value_key']: '123456789012345678901234567890'}, output, profile=profile)
    assert output.read_bytes() == b'PREVIOUS OUTPUT' and source.read_bytes() == before
    fill_compatible_template(source, {field['value_key']: '2'}, tmp_path/'short.pdf', profile=profile)


def test_actual_rtp_unresolved_calibri_is_not_silently_replaced(tmp_path, caplog):
    source = Path(__file__).resolve().parents[1]/'data/public_templates/ra_extended/pace_rtp_guidance.pdf'
    if not source.is_file(): pytest.skip('Official public fixture is not present')
    before = source.read_bytes(); output = tmp_path/'output.pdf'
    with pytest.raises(TemplateError, match='글꼴 자원'):
        fill_compatible_template(source, {'Project Title': 'QA ONLY'}, output)
    assert source.read_bytes() == before and not output.exists()
    assert 'defaulting to Helvetica' not in caplog.text


def test_actual_automatic_multiline_baseline_keeps_font_da_wrapping_and_complete_value(tmp_path):
    import re
    from templates import load_form_profile
    source = Path(__file__).resolve().parents[1]/'data/public_templates/ra_cross_company/eurofins_public_form.pdf'
    if not source.is_file(): pytest.skip('Official public fixture is not present')
    before = source.read_bytes(); original = PdfReader(source)
    profile = load_form_profile(source, 'corporate_ra_eurofins_sample_submission')
    value = 'Benepali 25 mg solution for injection in pre-filled syringe'
    output = fill_compatible_template(source, {'제품명': value}, tmp_path/'multiline.pdf', profile=profile)
    reader = PdfReader(output)
    old = original.get_fields()['Sample description1'].indirect_reference.get_object()
    new = reader.get_fields()['Sample description1'].indirect_reference.get_object()
    assert old['/DA'] == new['/DA'] and old['/Ff'] == new['/Ff'] and old['/Rect'] == new['/Rect']
    assert str(new['/V']) == value
    data = bytes(new['/AP']['/N'].get_object().get_data())
    assert re.search(rb'/Helv 11(?:\.0)? Tf\s+0 g', data) and data.count(b'Tj') == 4
    # The fourth line's descender is now inside the original rectangle; the
    # source's automatic DA and its original line segmentation stay intact.
    assert float(re.search(rb'2 ([\d.]+) Td', data)[1]) > 39.679
    assert source.read_bytes() == before
