from copy import deepcopy
from io import BytesIO

from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import pytest

from agent.output_check import verify_output
from agent.pipeline import validate_template_values
from templates import analyze_template, fill_compatible_template
from templates.pdf_annex import plan_annex


@pytest.fixture
def form(tmp_path):
    source = tmp_path / 'original.pdf'
    document = canvas.Canvas(str(source), pagesize=A4)
    for text in ('Original application', 'Original attachments and instructions'):
        document.drawString(40, 750, text)
        document.showPage()
    document.save()
    fields = [{'id': 'dose', 'label': '용법·용량', 'value_key': '용법·용량', 'kind': 'pdf_overlay',
               'page': 1, 'x': 100, 'y': 160, 'width': 200, 'height': 15, 'font_size': 10,
               'max_chars': 20, 'required': True, 'input_required': False}]
    profile = analyze_template(source, manual_fields=fields)
    profile['overflow_mode'] = 'annex'
    return source, profile, tmp_path / 'completed.pdf'


def test_lossless_multipage_annex_retains_all_original_pages_sources_and_values(form):
    source, profile, output = form
    before = source.read_bytes()
    text = '\n'.join(f'시험 문장 {i}: Initial dose 8 mg/kg, maintenance dose 6 mg/kg every 3 weeks.' for i in range(80))
    values = {'용법·용량': text}
    profile['annex_sources'] = {'용법·용량': ['[S1] official.pdf / 4쪽 / https://example.org/official.pdf']}
    fill_compatible_template(source, values, output, profile=profile)
    result = verify_output(source, output, values, profile=profile)
    assert result['status'] == 'passed'
    assert source.read_bytes() == before
    read = PdfReader(output)
    assert len(read.pages) > 3
    assert 'Original application' in read.pages[0].extract_text()
    assert 'Original attachments and instructions' in read.pages[1].extract_text()
    assert '별첨 1 참조' in read.pages[0].extract_text()
    assert next(check for check in result['checks'] if check['name'] == 'pdf_annex_complete_content')['fields'] == 1
    assert 'official.pdf' in '\n'.join(page.extract_text() for page in read.pages[2:])


def test_without_explicit_annex_long_content_is_rejected_and_existing_output_is_preserved(form):
    source, profile, output = form
    profile.pop('overflow_mode')
    output.write_bytes(b'previous result')
    with pytest.raises(ValueError, match='길이|넘침'):
        fill_compatible_template(source, {'용법·용량': 'very long complete source text ' * 20}, output, profile=profile)
    assert output.read_bytes() == b'previous result'


def test_typed_numeric_cell_cannot_be_replaced_by_a_textual_annex_reference(form):
    source, profile, output = form
    profile['fields'][0]['validation'] = {'type': 'integer'}
    values = {'용법·용량': '1' * 30}
    output.write_bytes(b'previous output')
    with pytest.raises(ValueError, match='분량|별첨'):
        fill_compatible_template(source, values, output, profile=profile)
    assert output.read_bytes() == b'previous output'


def test_direct_inputs_and_overlong_content_are_not_replaced_by_an_annex(form):
    source, profile, output = form
    profile['fields'][0]['input_required'] = True
    with pytest.raises(ValueError):
        fill_compatible_template(source, {'용법·용량': 'long user value ' * 10}, output, profile=profile)
    profile['fields'][0]['input_required'] = False
    with pytest.raises(ValueError, match='분량'):
        plan_annex(profile, {'용법·용량': 'a' * 20001})
    assert not output.exists()


def test_short_values_use_the_original_cell_without_additional_pages(form):
    source, profile, output = form
    values = {'용법·용량': '5 mg'}
    fill_compatible_template(source, values, output, profile=profile)
    assert len(PdfReader(output).pages) == 2
    assert verify_output(source, output, values, profile=profile)['status'] == 'passed'


def test_too_small_reference_cell_and_wrong_document_kind_are_rejected(form):
    _, profile, _ = form
    profile['fields'][0]['width'] = 1
    with pytest.raises(ValueError):
        plan_annex(profile, {'용법·용량': '전체 원문'})
    profile['format'] = 'docx'
    with pytest.raises(ValueError, match='PDF'):
        validate_template_values({'용법·용량': 'text'}, profile)


@pytest.mark.parametrize('damage', ['missing_page', 'extra_page', 'changed_content'])
def test_independent_check_rejects_missing_added_or_changed_annex_content(form, damage):
    source, profile, output = form
    values = {'용법·용량': 'The recommended dose is 6 mg/kg every three weeks. ' * 6}
    fill_compatible_template(source, values, output, profile=profile)
    reader = PdfReader(output)
    writer = PdfWriter()
    for page in reader.pages[:2]:
        writer.add_page(page)
    if damage == 'missing_page':
        pass
    elif damage == 'extra_page':
        for page in reader.pages[2:]:
            writer.add_page(page)
        writer.add_blank_page(width=A4[0], height=A4[1])
    else:
        changed = deepcopy(values)
        changed['용법·용량'] = changed['용법·용량'].replace('6 mg/kg', '60 mg/kg')
        other = output.with_name('incorrect.pdf')
        fill_compatible_template(source, changed, other, profile=profile)
        writer.add_page(PdfReader(other).pages[2])
    with output.open('wb') as stream:
        writer.write(stream)
    with pytest.raises(ValueError, match='별첨'):
        verify_output(source, output, values, profile=profile)


def test_independent_check_rejects_complete_annex_text_moved_off_page(form):
    source, profile, output = form
    values = {'용법·용량': 'Complete clinical paragraph with 6 mg/kg maintenance dose. ' * 6}
    fill_compatible_template(source, values, output, profile=profile)
    writer = PdfWriter(clone_from=output)
    writer.pages[2].add_transformation(Transformation().translate(tx=1000))
    with output.open('wb') as stream:
        writer.write(stream)
    assert 'Complete clinical paragraph' in PdfReader(output).pages[2].extract_text()
    with pytest.raises(ValueError, match='작성 영역 밖'):
        verify_output(source, output, values, profile=profile)


@pytest.mark.parametrize('page', [0, 1])
def test_original_graphics_outside_writing_regions_are_protected(form, page):
    source, profile, output = form
    values = {'용법·용량': '5 mg'}
    fill_compatible_template(source, values, output, profile=profile)
    buffer = BytesIO()
    drawing = canvas.Canvas(buffer, pagesize=A4)
    drawing.setFillColorRGB(1, 0, 0)
    drawing.rect(400, 680, 60, 40, fill=1)
    drawing.save()
    buffer.seek(0)
    writer = PdfWriter(clone_from=output)
    writer.pages[page].merge_page(PdfReader(buffer).pages[0])
    with output.open('wb') as stream:
        writer.write(stream)
    # The complete original and entered text still extract correctly.
    assert '5 mg' in PdfReader(output).pages[0].extract_text()
    with pytest.raises(ValueError, match='작성 영역 밖'):
        verify_output(source, output, values, profile=profile)
