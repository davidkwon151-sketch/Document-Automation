"""Native PDF choices preserve stored codes, display labels, and source data."""

from hashlib import sha256
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, ContentStream, DictionaryObject, NameObject, NumberObject, TextStringObject
import pytest
from reportlab.pdfgen import canvas

from templates import analyze_template, fill_compatible_template, TemplateError
from templates.pdf_choices import (COMBO, EDIT, MULTISELECT, choice_metadata,
                                  encode_choice_values, parse_choice_value, validate_choice_metadata)


def make_form(path, *, flags=COMBO, options=None, height=32, font_size=12):
    buffer = BytesIO()
    drawing = canvas.Canvas(buffer, pagesize=(400, 350), invariant=1)
    drawing.drawString(30, 310, 'Synthetic choice QA only - no submission')
    drawing.acroForm.choice(name='selection', x=30, y=200, width=300, height=height,
                           options=['Alpha', 'Beta', 'Gamma'], value='Alpha',
                           fieldFlags='combo' if flags & COMBO else 'multiSelect', fontSize=font_size)
    drawing.acroForm.textfield(name='untouched', x=30, y=70, width=300, height=25, value='SOURCE')
    drawing.showPage(); drawing.save(); buffer.seek(0)
    writer = PdfWriter(); writer.clone_document_from_reader(PdfReader(buffer))
    node = writer.pages[0]['/Annots'][0].get_object()
    node[NameObject('/Ff')] = NumberObject(flags)
    native_options = options if options is not None else [('A', 'Alpha'), ('B', 'Beta'), ('C', 'Gamma')]
    node[NameObject('/Opt')] = ArrayObject([
        ArrayObject([TextStringObject(code), TextStringObject(label)]) if isinstance(item, tuple)
        else TextStringObject(item) for item in native_options
        for code, label in ([item] if isinstance(item, tuple) else [(item, item)])])
    node[NameObject('/V')] = TextStringObject((native_options[0][0] if isinstance(native_options[0], tuple) else native_options[0]) if native_options else '')
    node[NameObject('/I')] = ArrayObject([NumberObject(0)])
    if not native_options:
        node.pop(NameObject('/I'))
    if not flags & COMBO:
        node[NameObject('/TI')] = NumberObject(0)
    with path.open('wb') as stream:
        writer.write(stream)
    return path


def widget(reader, name='selection'):
    return next(ref.get_object() for page in reader.pages for ref in page['/Annots']
                if ref.get_object().get('/T') == name)


def appearance_text(node):
    appearance = node['/AP']['/N']
    writer = PdfWriter()
    page = writer.add_blank_page(float(appearance['/BBox'][2]), float(appearance['/BBox'][3]))
    page[NameObject('/Resources')] = appearance['/Resources'].clone(writer)
    page[NameObject('/Contents')] = writer._add_object(appearance.clone(writer))
    data = BytesIO(); writer.write(data); data.seek(0)
    return PdfReader(data).pages[0].extract_text().strip()


def test_analyze_exposes_original_codes_labels_and_flags(tmp_path):
    source = make_form(tmp_path/'choice.pdf')
    field = next(f for f in analyze_template(source)['fields'] if f['id']=='pdf:selection')
    assert field['options'] == ['A', 'B', 'C']
    assert field['choice_items'] == [{'value':'A','label':'Alpha'}, {'value':'B','label':'Beta'}, {'value':'C','label':'Gamma'}]
    assert field['pdf_choice_flags'] == COMBO
    assert field['allow_custom'] is False and field['multiselect'] is False
    assert field['input_required'] is True and field['narrative_style_required'] is False
    validate_choice_metadata(field)


@pytest.mark.parametrize('flags,value,expected,label,indices', [
    (COMBO,'B','B','Beta',[1]),
    (0,'C','C','Alpha\nBeta\nGamma',[2]),
    (COMBO|EDIT,'New entry','New entry','New entry',None),
    (COMBO|EDIT,'B','B','Beta',[1]),
    (MULTISELECT,'["C","A"]',['A','C'],'Alpha\nBeta\nGamma',[0,2]),
])
def test_fill_native_choices_store_codes_and_render_display(tmp_path,flags,value,expected,label,indices):
    source = make_form(tmp_path/'choice.pdf',flags=flags,height=80 if not flags&COMBO else 32)
    original = source.read_bytes(); before = PdfReader(source)
    output = fill_compatible_template(source, {'selection':value}, tmp_path/'filled.pdf')
    result = PdfReader(output); actual = widget(result)
    assert result.get_fields()['selection']['/V'] == expected
    assert (list(actual['/I']) if '/I' in actual else None) == indices
    assert appearance_text(actual) == label
    assert actual['/Opt'] == widget(before)['/Opt'] and actual['/Ff'] == flags
    assert actual['/Rect'] == widget(before)['/Rect']
    assert appearance_text(widget(result,'untouched')) == appearance_text(widget(before,'untouched'))
    assert widget(result,'untouched')['/V'] == 'SOURCE'
    assert source.read_bytes() == original


def test_blank_native_placeholder_keeps_full_original_option_indices(tmp_path):
    source = make_form(tmp_path/'choice.pdf',options=[' ',('A','Alpha'),('B','Beta')])
    profile = analyze_template(source)
    field = next(f for f in profile['fields'] if f['id']=='pdf:selection')
    assert field['options'] == ['A','B']
    assert field['pdf_blank_options'] == [{'index':0,'value':' ','label':' '}]
    output = fill_compatible_template(source,{'selection':'B'},tmp_path/'filled.pdf',profile=profile)
    assert widget(PdfReader(output))['/I'] == [2]
    assert widget(PdfReader(output))['/Opt'] == widget(PdfReader(source))['/Opt']


@pytest.mark.parametrize('options,flags', [
    ([('A','Alpha'),('A','Beta')],COMBO),
    ([('','Meaningful option')],COMBO),
    (['Alpha','Beta'],EDIT),
    (['Alpha','Beta'],COMBO|MULTISELECT),
])
def test_malformed_source_choices_are_unresolved_and_never_written(tmp_path,options,flags):
    source = make_form(tmp_path/'choice.pdf',options=options,flags=flags)
    profile = analyze_template(source)
    assert next(f for f in profile['fields'] if f['id']=='pdf:selection')['control_type']=='choice_unresolved'
    output = tmp_path/'existing.pdf'; output.write_bytes(b'existing')
    with pytest.raises(ValueError):
        fill_compatible_template(source,{'selection':'Alpha'},output,profile=profile)
    assert output.read_bytes() == b'existing'


@pytest.mark.parametrize('value',['[]','["A","A"]','["UNKNOWN"]','A','[1]','{"A":true}'])
def test_multiselect_rejects_ambiguous_or_empty_value(value):
    with pytest.raises(TemplateError):
        parse_choice_value(choice_metadata([('A','Alpha'),('B','Beta')],MULTISELECT),value)


def test_multiselect_canonicalizes_source_order_without_mutating_input():
    field=choice_metadata([('A','Alpha'),('B','Beta'),('[S1]','Literal source-like code')],MULTISELECT)
    value='["[S1]", "A"] [S9]'
    assert parse_choice_value(field,value)==['A','[S1]']
    assert value=='["[S1]", "A"] [S9]'
    assert encode_choice_values(['A','[S1]'])=='["A","[S1]"]'
    assert parse_choice_value(choice_metadata(['[S1]','B'],COMBO),'[S1]')==['[S1]']


@pytest.mark.parametrize('change', ['allow_custom','multiselect','labels','flags'])
def test_profile_cannot_relax_or_replace_source_choice_conditions(tmp_path,change):
    source = make_form(tmp_path/'choice.pdf')
    profile = analyze_template(source)
    field = next(f for f in profile['fields'] if f['id']=='pdf:selection')
    if change=='allow_custom':field.update(allow_custom=True,control_type='combobox')
    elif change=='multiselect':field.update(multiselect=True,selection_encoding='json_array')
    elif change=='labels':field['choice_items'][1]['label']='Changed label'
    else:field['pdf_choice_flags']=COMBO|EDIT
    with pytest.raises(ValueError):
        fill_compatible_template(source,{'selection':'B'},tmp_path/'filled.pdf',profile=profile)


def test_fixed_native_font_overflow_is_blocked_without_partial_output(tmp_path):
    source = make_form(tmp_path/'choice.pdf',options=[('A','Alpha'),('B','Very long display label '*20)])
    output = tmp_path/'existing.pdf'; output.write_bytes(b'existing')
    with pytest.raises(ValueError,match='폭|넘'):
        fill_compatible_template(source,{'selection':'B'},output)
    assert output.read_bytes()==b'existing'


def test_multiselect_refuses_hidden_selected_label(tmp_path):
    source = make_form(tmp_path/'choice.pdf',flags=MULTISELECT,height=38)
    with pytest.raises(ValueError,match='같은 목록'):
        fill_compatible_template(source,{'selection':'["A","C"]'},tmp_path/'filled.pdf')


def test_explicit_optional_blank_keeps_existing_choice_and_other_value(tmp_path):
    source = make_form(tmp_path/'choice.pdf')
    output = fill_compatible_template(source,{'selection':'','untouched':'USER'},tmp_path/'filled.pdf')
    before, after = PdfReader(source), PdfReader(output)
    assert widget(after)['/V']==widget(before)['/V']
    assert widget(after)['/AP']['/N'].get_data()==widget(before)['/AP']['/N'].get_data()


def test_unicode_display_is_rendered_and_editable_even_when_code_is_ascii(tmp_path):
    source = make_form(tmp_path/'choice.pdf',options=[('A','Alpha'),('B','한글 표시 문구')])
    original_sha = sha256(source.read_bytes()).hexdigest()
    output = fill_compatible_template(source,{'selection':'B'},tmp_path/'filled.pdf')
    reader=PdfReader(output);actual=widget(reader)
    assert actual['/V']=='B' and appearance_text(actual)=='한글 표시 문구'
    assert actual['/DA'].startswith('/CompatCJK')
    reopened=fill_compatible_template(output,{'selection':'B'},tmp_path/'adapter_reedit.pdf')
    assert PdfReader(reopened).get_fields()['selection']['/V']=='B'
    assert appearance_text(widget(PdfReader(reopened)))=='한글 표시 문구'
    assert sha256(source.read_bytes()).hexdigest()==original_sha


@pytest.mark.parametrize('remove_options',[False,True])
def test_editable_combo_with_no_suggestions_preserves_original_empty_opt(tmp_path,remove_options):
    source=make_form(tmp_path/'empty_combo.pdf',flags=COMBO|EDIT,options=[])
    if remove_options:
        writer=PdfWriter();writer.clone_document_from_reader(PdfReader(source))
        writer.pages[0]['/Annots'][0].get_object().pop(NameObject('/Opt'))
        with source.open('wb') as stream:writer.write(stream)
    before=PdfReader(source);profile=analyze_template(source)
    field=next(f for f in profile['fields'] if f['id']=='pdf:selection')
    assert field['options']==[] and field['choice_items']==[] and field['allow_custom'] is True
    output=fill_compatible_template(source,{'selection':'User provided free text'},tmp_path/'filled.pdf',profile=profile)
    actual=widget(PdfReader(output))
    assert actual['/V']=='User provided free text' and '/I' not in actual
    assert actual.get('/Opt')==widget(before).get('/Opt')
    assert appearance_text(actual)=='User provided free text'


def test_list_selection_highlight_matches_full_source_indices_and_scroll_window(tmp_path):
    source=make_form(tmp_path/'list.pdf',flags=MULTISELECT,height=38)
    output=fill_compatible_template(source,{'selection':'["B","C"]'},tmp_path/'filled.pdf')
    reader=PdfReader(output);actual=widget(reader)
    assert actual['/I']==[1,2] and actual['/TI']==1
    assert appearance_text(actual)=='Beta\nGamma'
    rectangles=[list(map(float,operands)) for operands,operator in ContentStream(actual['/AP']['/N'],reader).operations if operator==b're']
    assert rectangles==[[2.,20.4,296.,15.6],[2.,4.8,296.,15.6]]


def test_source_native_readonly_flag_cannot_be_overridden_by_profile(tmp_path):
    source=make_form(tmp_path/'readonly.pdf')
    profile=analyze_template(source)
    writer=PdfWriter();writer.clone_document_from_reader(PdfReader(source))
    writer.pages[0]['/Annots'][0].get_object()[NameObject('/Ff')]=NumberObject(COMBO|1)
    with source.open('wb') as stream:writer.write(stream)
    profile['source_sha256']=sha256(source.read_bytes()).hexdigest()
    next(f for f in profile['fields'] if f['id']=='pdf:selection')['pdf_choice_flags']=COMBO|1
    with pytest.raises(ValueError,match='원본 PDF 선택 조건'):
        fill_compatible_template(source,{'selection':'B'},tmp_path/'filled.pdf',profile=profile)


def test_orphan_widget_with_same_name_does_not_receive_canonical_field_value(tmp_path):
    source=make_form(tmp_path/'orphan.pdf')
    writer=PdfWriter();writer.clone_document_from_reader(PdfReader(source))
    original=writer.pages[0]['/Annots'][0].get_object()
    ghost=DictionaryObject(dict(original))
    writer.pages[0]['/Annots'].append(writer._add_object(ghost))
    with source.open('wb') as stream:writer.write(stream)
    target=tmp_path/'existing.pdf';target.write_bytes(b'existing')
    with pytest.raises(ValueError,match='연결이 모호'):
        fill_compatible_template(source,{'selection':'B'},target)
    assert target.read_bytes()==b'existing'


@pytest.mark.parametrize('flags,value,height',[(COMBO,'B',32),(MULTISELECT,'["C","A"]',80)])
def test_unicode_choice_appearance_glyphs_stay_inside_native_rectangle(tmp_path,flags,value,height):
    import pypdfium2 as pdfium
    source=make_form(tmp_path/'unicode.pdf',flags=flags,height=height,
                     options=[('A','첫째 선택'),('B','둘째 선택'),('C','셋째 선택')])
    output=fill_compatible_template(source,{'selection':value},tmp_path/'filled.pdf')
    appearance=widget(PdfReader(output))['/AP']['/N']
    writer=PdfWriter();page=writer.add_blank_page(300,height)
    page[NameObject('/Resources')]=appearance['/Resources'].clone(writer)
    page[NameObject('/Contents')]=writer._add_object(appearance.clone(writer))
    stream=BytesIO();writer.write(stream)
    with pdfium.PdfDocument(stream.getvalue()) as document:
        rendered=document[0]
        text=rendered.get_textpage()
        try:
            for index in range(text.count_chars()):
                if not text.get_text_range(index,1).strip():continue
                left,bottom,right,top=text.get_charbox(index)
                assert 0<=left<=right<=300 and 0<=bottom<=top<=height
        finally:
            text.close();rendered.close()
