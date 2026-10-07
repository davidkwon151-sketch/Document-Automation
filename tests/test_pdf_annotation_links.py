"""Reciprocal PDF Popup links preserve every non-input annotation property."""
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject
from reportlab.pdfgen import canvas
import pytest

from agent.output_check import _pdf_compare_form_structure


def fixture(tmp_path, listed=True):
    path = tmp_path/'source.pdf'
    drawing = canvas.Canvas(str(path))
    drawing.acroForm.textfield(name='Work', x=50, y=600, width=200, height=24)
    drawing.showPage(); drawing.drawString(50, 600, 'Second'); drawing.showPage(); drawing.save()
    writer = PdfWriter(); writer.clone_document_from_reader(PdfReader(path))
    page = writer.pages[0]
    markup = DictionaryObject({NameObject('/Subtype'): NameObject('/FreeText'),
        NameObject('/Contents'): TextStringObject('Original guidance'),
        NameObject('/Rect'): ArrayObject([NumberObject(v) for v in (30, 30, 120, 90)]),
        NameObject('/P'): page.indirect_reference})
    popup = DictionaryObject({NameObject('/Subtype'): NameObject('/Popup'),
        NameObject('/Contents'): TextStringObject('Original popup'),
        NameObject('/Rect'): ArrayObject([NumberObject(v) for v in (130, 30, 220, 90)]),
        NameObject('/P'): page.indirect_reference})
    markup_ref, popup_ref = writer._add_object(markup), writer._add_object(popup)
    markup[NameObject('/Popup')] = popup_ref; popup[NameObject('/Parent')] = markup_ref
    page['/Annots'].append(markup_ref)
    if listed: page['/Annots'].append(popup_ref)
    with path.open('wb') as stream: writer.write(stream)
    return path


def copy_form(source, tmp_path, change=None):
    writer = PdfWriter(); writer.clone_document_from_reader(PdfReader(source))
    writer.pages[0]['/Annots'][0].get_object()[NameObject('/V')] = TextStringObject('TEST')
    if change: change(writer)
    path = tmp_path/'output.pdf'
    with path.open('wb') as stream: writer.write(stream)
    return path


@pytest.mark.parametrize('listed', [True, False])
def test_valid_popup_cycles_are_compared_without_dropping_properties(tmp_path, listed):
    source = fixture(tmp_path, listed); original = source.read_bytes()
    output = copy_form(source, tmp_path)
    assert _pdf_compare_form_structure(PdfReader(source), PdfReader(output), {'Work': 'TEST'}) == []
    assert source.read_bytes() == original


@pytest.mark.parametrize('change', ['contents', 'rectangle', 'popup_contents', 'popup_rectangle',
                                  'popup_page', 'owner_page', 'wrong_parent', 'missing_parent',
                                  'missing_popup', 'cross_page_popup', 'duplicate', 'unknown_cycle'])
def test_popup_corruption_and_unrelated_cycles_remain_blocked(tmp_path, change):
    source = fixture(tmp_path)
    def mutate(writer):
        page = writer.pages[0]; markup = page['/Annots'][1].get_object()
        popup = markup['/Popup']
        if change == 'contents': markup[NameObject('/Contents')] = TextStringObject('Changed')
        elif change == 'rectangle': markup['/Rect'][0] = NumberObject(99)
        elif change == 'popup_contents': popup[NameObject('/Contents')] = TextStringObject('Changed')
        elif change == 'popup_rectangle': popup['/Rect'][0] = NumberObject(99)
        elif change == 'popup_page': popup[NameObject('/P')] = writer.pages[1].indirect_reference
        elif change == 'owner_page': markup[NameObject('/P')] = writer.pages[1].indirect_reference
        elif change == 'wrong_parent': popup[NameObject('/Parent')] = page['/Annots'][0]
        elif change == 'missing_parent': del popup['/Parent']
        elif change == 'missing_popup': del markup['/Popup']
        elif change == 'cross_page_popup':
            reference = page['/Annots'].pop(2)
            writer.pages[1][NameObject('/Annots')] = ArrayObject([reference])
        elif change == 'duplicate': page['/Annots'].append(page['/Annots'][1])
        else: markup[NameObject('/Other')] = page['/Annots'][1]
    output = copy_form(source, tmp_path, mutate)
    with pytest.raises(ValueError, match='PDF'):
        _pdf_compare_form_structure(PdfReader(source), PdfReader(output), {'Work': 'TEST'})
