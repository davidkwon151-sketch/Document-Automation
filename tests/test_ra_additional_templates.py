"""공식 공개 회사 시험/규제지원 양식의 선택 입력만 검증함."""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject
import pytest

from agent.output_check import verify_output
from templates import TemplateError, fill_compatible_template, load_form_profile
from parsers import parse_file

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=json.loads((ROOT/'evals/ra_additional_templates.json').read_text(encoding='utf-8'))
ENTRIES=MANIFEST['documents']


def source_and_profile(entry):
    source=ROOT/entry['source_path']
    if not source.is_file():
        pytest.skip('공식 원본 corpus를 별도로 확보해야 함')
    profile=load_form_profile(source,entry['profile_id'])
    assert profile is not None
    return source,profile


def widgets(reader):
    result={}
    for page in reader.pages:
        for reference in page.get('/Annots',[]):
            widget=reference.get_object()
            if widget.get('/Subtype')!='/Widget':
                continue
            parent=widget.get('/Parent',widget).get_object()
            name=str(parent.get('/T',widget.get('/T','')))
            result.setdefault(name,[]).append((widget,parent))
    return result


def appearance_text(widget):
    appearance=widget['/AP']['/N'].get_object()
    writer=PdfWriter();box=appearance['/BBox']
    page=writer.add_blank_page(width=float(box[2]-box[0]),height=float(box[3]-box[1]))
    page[NameObject('/Resources')]=appearance['/Resources'].clone(writer)
    page[NameObject('/Contents')]=writer._add_object(appearance.clone(writer))
    stream=BytesIO();writer.write(stream);stream.seek(0)
    extracted=PdfReader(stream).pages[0].extract_text()
    import pypdfium2 as pdfium
    document=pdfium.PdfDocument(stream.getvalue());page=document[0];textpage=page.get_textpage()
    try:
        actual=textpage.get_text_range()
        width,height=page.get_size()
        for index in range(textpage.count_chars()):
            if textpage.get_text_range(index,1).strip():
                left,bottom,right,top=textpage.get_charbox(index)
                assert -.5<=left<=right<=width+.5 and -.5<=bottom<=top<=height+.5
    finally:
        textpage.close();page.close();document.close()
    # pypdf sometimes appends a layout-only page-end newline; PDFium gives
    # the exact single-line glyph text without that synthetic terminator.
    assert extracted.rstrip('\r\n')==actual
    return actual


@pytest.mark.parametrize('entry',ENTRIES,ids=lambda e:e['id'])
def test_real_public_sources_preserve_all_pages_and_sourcebound_scope(entry):
    source,profile=source_and_profile(entry)
    before=source.read_bytes();reader=PdfReader(source)
    assert sha256(before).hexdigest()==entry['sha256']==profile['source_sha256']
    assert not reader.is_encrypted and not reader.trailer['/Root'].get('/Perms')
    canonical=reader.get_fields() or {}
    assert len(canonical)==entry['canonical_field_count']
    parsed=parse_file(source)
    assert len(parsed['페이지/시트 정보'])==entry['page_count']==len(reader.pages)
    for block,page in zip(parsed['페이지/시트 정보'],reader.pages):
        assert Counter(re.sub(r'\s+','',block['본문']))==Counter(re.sub(r'\s+','',page.extract_text() or ''))
    assert profile['citation_mode']=='sidecar' and profile['company_internal'] is False
    assert profile['current_version_verified'] is profile['submission_ready'] is False
    assert len(profile['fields'])==entry['registered_field_count']
    for field in profile['fields']:
        assert field['original_field_name'] in canonical
        assert canonical[field['original_field_name']]['/FT']=='/Tx'
        assert field['required'] == bool(int(canonical[field['original_field_name']].get('/Ff',0)) & 2)
        assert field['narrative_style_required'] is False
        assert field['input_mode']==('user_provided' if field['input_required'] else 'source_grounded')
        assert field['author_role']=='sender'
        assert not any(word in field['label'].lower() for word in ['signature','authorized by','receipt check'])
    assert source.read_bytes()==before


@pytest.mark.parametrize('entry',ENTRIES,ids=lambda e:e['id'])
def test_synthetic_selected_values_match_canonical_and_widget_appearances(entry,tmp_path):
    source,profile=source_and_profile(entry)
    before=source.read_bytes(); original=PdfReader(source)
    output=fill_compatible_template(source,profile['demo_values'],tmp_path/'partial.pdf',profile=profile)
    filled=PdfReader(output); canonical=filled.get_fields() or {}; all_widgets=widgets(filled)
    selected={f['original_field_name']:profile['demo_values'][f['value_key']] for f in profile['fields']}
    assert set(canonical)==set(original.get_fields() or {})
    assert len(filled.pages)==len(original.pages)
    for name,value in selected.items():
        assert str(canonical[name].get('/V',''))==value
        assert len(all_widgets[name])==1
        for widget,parent in all_widgets[name]:
            assert str(parent.get('/V',widget.get('/V','')))==value
            appearance=widget['/AP']['/N'].get_object()
            assert appearance.get_data()
    for name,field in (original.get_fields() or {}).items():
        if name not in selected:
            assert str(canonical[name].get('/V',''))==str(field.get('/V',''))
    assert verify_output(source,output,profile['demo_values'],profile=profile)['status']=='passed'
    assert source.read_bytes()==before


@pytest.mark.parametrize('entry',ENTRIES,ids=lambda e:e['id'])
def test_profile_rejects_long_value_without_truncating_or_destroying_output(entry,tmp_path):
    source,profile=source_and_profile(entry)
    field=profile['fields'][0];output=tmp_path/'existing.pdf';output.write_bytes(b'PREVIOUS USER OUTPUT')
    before=source.read_bytes()
    with pytest.raises(TemplateError,match='길이|제한|넘'):
        fill_compatible_template(source,{field['value_key']:'W'*(field['max_chars']+1)},output,profile=profile)
    assert output.read_bytes()==b'PREVIOUS USER OUTPUT' and source.read_bytes()==before


@pytest.mark.parametrize('entry',ENTRIES,ids=lambda e:e['id'])
def test_sha_mismatch_refuses_another_pdf_and_preserves_original(entry,tmp_path):
    source,profile=source_and_profile(entry)
    profile=deepcopy(profile);profile['source_sha256']='0'*64
    with pytest.raises(TemplateError):
        fill_compatible_template(source,profile['demo_values'],tmp_path/'bad.pdf',profile=profile)
    assert not (tmp_path/'bad.pdf').exists()


@pytest.mark.parametrize('company',['eurofins','sgs'])
@pytest.mark.parametrize('source_id',['ra-public-herzuma','ra-public-benepali','ra-public-keppra'])
def test_complete_public_product_name_identity_only_preserves_unselected_controls(company,source_id,tmp_path):
    entry=next(e for e in ENTRIES if e['id']==f'corporate_ra_{company}_sample_submission')
    source,profile=source_and_profile(entry)
    record=next(r for r in json.loads((ROOT/'evals/ra_public_sources.json').read_text(encoding='utf-8'))['sources'] if r['id']==source_id)
    product_source=ROOT/record['path']
    if not product_source.is_file():
        pytest.skip('공식 EMA 원자료 corpus를 별도로 확보해야 함')
    assert sha256(product_source.read_bytes()).hexdigest()==record['sha256']
    fact=next(f for f in record['facts'] if f['field_key']=='제품명')
    assert re.sub(r'\s+','',fact['exact_quote']) in re.sub(r'\s+','',PdfReader(product_source).pages[fact['page']-1].extract_text())
    assert profile['real_public_fact_mapping']['eligible'] is True
    assert '제품명' in profile['real_public_fact_mapping']['keys']
    result=fill_compatible_template(source,{'제품명':fact['value']},tmp_path/'identity_only.pdf',profile=profile)
    target=next(f for f in profile['fields'] if f['value_key']=='제품명')['original_field_name']
    fields=PdfReader(result).get_fields() or {}
    assert str(fields[target]['/V'])==fact['value']
    for name,field in (PdfReader(source).get_fields() or {}).items():
        if name!=target:
            assert str(fields[name].get('/V',''))==str(field.get('/V',''))
    assert verify_output(source,result,{'제품명':fact['value']},profile=profile)['status']=='passed'


def test_thermofisher_serum_scope_excludes_ema_drug_fact_mapping():
    entry=next(e for e in ENTRIES if 'thermofisher' in e['id'])
    _,profile=source_and_profile(entry)
    assert not profile['real_public_fact_mapping']['eligible']
    assert '제품명' not in {f['value_key'] for f in profile['fields']}
    assert MANIFEST['summary']['private_internal_template_verified']==0
    assert MANIFEST['summary']['full_form_filled']==0


@pytest.mark.parametrize('company',['eurofins','sgs'])
def test_independent_check_rejects_native_widget_moved_from_source_location(company,tmp_path):
    entry=next(e for e in ENTRIES if e['id']==f'corporate_ra_{company}_sample_submission')
    source,profile=source_and_profile(entry)
    value={'제품명':'TEST SAMPLE'}
    good=fill_compatible_template(source,value,tmp_path/'good.pdf',profile=profile)
    writer=PdfWriter();writer.clone_document_from_reader(PdfReader(good))
    target=next(f for f in profile['fields'] if f['value_key']=='제품명')['original_field_name']
    widget,_=widgets(writer)[target][0]
    widget[NameObject('/Rect')]=ArrayObject([FloatObject(float(v)+20) for v in widget['/Rect']])
    wrong=tmp_path/'wrong_position.pdf'
    with wrong.open('wb') as stream:writer.write(stream)
    with pytest.raises(ValueError,match='위치|속성|연결'):
        verify_output(source,wrong,value,profile=profile)


@pytest.mark.parametrize('change',['readonly','type','max_length','annotation_order','orphan_widget','unselected_flags'])
def test_native_source_metadata_and_canonical_widget_binding_are_not_editable(change,tmp_path):
    entry=next(e for e in ENTRIES if 'eurofins' in e['id'])
    source,profile=source_and_profile(entry)
    values={'제품명':'TEST SAMPLE'}
    good=fill_compatible_template(source,values,tmp_path/'good.pdf',profile=profile)
    writer=PdfWriter();writer.clone_document_from_reader(PdfReader(good))
    target=next(f for f in profile['fields'] if f['value_key']=='제품명')['original_field_name']
    widget,_=widgets(writer)[target][0]
    if change=='readonly':widget[NameObject('/Ff')]=NumberObject(int(widget.get('/Ff',0))|1)
    elif change=='type':widget[NameObject('/FT')]=NameObject('/Ch')
    elif change=='max_length':widget[NameObject('/MaxLen')]=NumberObject(1)
    elif change=='annotation_order':
        references=writer.pages[1]['/Annots']
        references[0],references[1]=references[1],references[0]
    elif change=='orphan_widget':
        clone=DictionaryObject(dict(widget))
        page=writer.pages[1]
        for index,reference in enumerate(page['/Annots']):
            if reference.get_object() is widget:
                page['/Annots'][index]=writer._add_object(clone)
                break
    else:
        other=next(items[0][0] for name,items in widgets(writer).items() if name!=target)
        other[NameObject('/Ff')]=NumberObject(int(other.get('/Ff',0))|1)
    wrong=tmp_path/'changed_metadata.pdf'
    with wrong.open('wb') as stream:writer.write(stream)
    with pytest.raises(ValueError,match='위치|속성|연결|트리|Widget'):
        verify_output(source,wrong,values,profile=profile)


def test_unselected_native_choice_options_remain_source_defined(tmp_path):
    entry=next(e for e in ENTRIES if 'thermofisher' in e['id'])
    source,profile=source_and_profile(entry)
    good=fill_compatible_template(source,profile['demo_values'],tmp_path/'good.pdf',profile=profile)
    writer=PdfWriter();writer.clone_document_from_reader(PdfReader(good))
    control,_=widgets(writer)['Type of Serum'][0]
    control[NameObject('/Opt')]=ArrayObject([TextStringObject('UNAUTHORISED OPTION')])
    wrong=tmp_path/'bad_options.pdf'
    with wrong.open('wb') as stream:writer.write(stream)
    with pytest.raises(ValueError,match='옵션|속성'):
        verify_output(source,wrong,profile['demo_values'],profile=profile)


def test_selected_unicode_font_resources_do_not_invalidate_source_widgets(tmp_path):
    entry=next(e for e in ENTRIES if 'eurofins' in e['id'])
    source,profile=source_and_profile(entry)
    values={'제품명':'가짜 시험용 제품 - 제출용 아님'}
    output=fill_compatible_template(source,values,tmp_path/'unicode.pdf',profile=profile)
    assert verify_output(source,output,values,profile=profile)['status']=='passed'


@pytest.mark.parametrize('change',['widget_missing','viewer_regeneration'])
def test_canonical_values_without_stable_widgets_or_saved_appearances_are_rejected(change,tmp_path):
    entry=next(e for e in ENTRIES if 'sgs' in e['id'])
    source,profile=source_and_profile(entry)
    values={'제품명':'TEST SAMPLE'}
    good=fill_compatible_template(source,values,tmp_path/'good.pdf',profile=profile)
    writer=PdfWriter();writer.clone_document_from_reader(PdfReader(good))
    if change=='viewer_regeneration':
        writer.root_object['/AcroForm'][NameObject('/NeedAppearances')]=BooleanObject(True)
    else:
        target=next(f for f in profile['fields'] if f['value_key']=='제품명')['original_field_name']
        references=writer.pages[0]['/Annots']
        for index,reference in enumerate(references):
            if str(reference.get_object().get('/T',''))==target:
                del references[index]
                break
    wrong=tmp_path/'canonical_only.pdf'
    with wrong.open('wb') as stream:writer.write(stream)
    with pytest.raises(ValueError,match='누락|재생성|위치'):
        verify_output(source,wrong,values,profile=profile)


def test_normal_native_radio_child_states_and_choices_are_preserved(tmp_path):
    source=ROOT/'samples/sample_selection_form.pdf'
    values={'Consent':'true','Decision':'/reject','Department':'Operations'}
    output=fill_compatible_template(source,values,tmp_path/'selection.pdf')
    assert verify_output(source,output,values)['status']=='passed'


@pytest.mark.parametrize('manifest,source_id',[
    ('ra_korean_sources.json','ra-kr-hanmiflu-75'),
    ('ra_additional_sources.json','ra-kr-additional-actimin'),
    ('ra_additional_sources.json','ra-kr-additional-rosulod-5'),
])
def test_actual_korean_product_identity_handles_indirect_and_local_native_fonts(manifest,source_id,tmp_path,caplog):
    from agent.native_review import review_native_output
    entry=next(e for e in ENTRIES if 'sgs' in e['id'])
    source,profile=source_and_profile(entry)
    record=next(r for r in json.loads((ROOT/'evals'/manifest).read_text(encoding='utf-8'))['sources'] if r['id']==source_id)
    fact=next(f for f in record['facts'] if f['field_key']=='제품명')
    product_source=ROOT/record['path']
    if not product_source.is_file():
        pytest.skip('공식 공개 원자료 corpus를 별도로 확보해야 함')
    assert sha256(product_source.read_bytes()).hexdigest()==record['sha256']
    assert re.sub(r'\s+','',fact['exact_quote']) in re.sub(r'\s+','',PdfReader(product_source).pages[fact['page']-1].extract_text())
    values={'제품명':fact['value']};before=source.read_bytes()
    output=fill_compatible_template(source,values,tmp_path/'korean.pdf',profile=profile)
    target=next(f for f in profile['fields'] if f['value_key']=='제품명')['original_field_name']
    reader=PdfReader(output);widget,_=widgets(reader)[target][0]
    assert str(reader.get_fields()[target]['/V'])==fact['value']
    assert '/Font' in PdfReader(source).trailer['/Root']['/AcroForm']['/DR']
    assert widget['/DA'].endswith(' 0 Tf 0 g')  # Native automatic size remains automatic.
    resource=re.match(r'(/CompatCJK[0-9a-f]{12}) ',str(widget['/DA']))[1]
    global_fonts=reader.trailer['/Root']['/AcroForm']['/DR']['/Font']
    local_fonts=widget['/DR']['/Font']
    assert local_fonts.get(resource)==global_fonts.get(resource)
    font=global_fonts[resource]
    assert font['/Subtype']=='/Type0' and font['/Encoding']=='/Identity-H'
    assert font['/DescendantFonts'][0]['/CIDToGIDMap']=='/Identity'
    from fontTools.ttLib import TTFont
    embedded=font['/DescendantFonts'][0]['/FontDescriptor']['/FontFile2'].get_data()
    with TTFont(BytesIO(embedded)) as actual_font:
        glyphs={character:actual_font.getGlyphID(glyph) for character,glyph in actual_font.getBestCmap().items()}
    reverse={int(glyph,16):bytes.fromhex(character.decode('ascii')).decode('utf-16-be')
             for glyph,character in re.findall(rb'<([0-9A-F]{4})> <([0-9A-F]+)>',font['/ToUnicode'].get_data())
             if glyph not in {b'0000',b'FFFF'}}
    for character in fact['value']:
        if not character.isspace():
            assert glyphs[ord(character)]>0 and reverse[glyphs[ord(character)]]==character
    assert appearance_text(widget)==fact['value']
    assert 'defaulting to Helvetica' not in caplog.text
    assert 'contains characters not supported' not in caplog.text
    assert verify_output(source,output,values,profile=profile)['status']=='passed'
    native=review_native_output(source,output,values,profile=profile,work_dir=tmp_path/'native')
    assert native['status']=='warning'  # PDF form position/identity remains explicitly ambiguous.
    assert all(issue['code']=='native_value_ambiguous' for issue in native['issues'])
    writer=PdfWriter();writer.clone_document_from_reader(reader)
    writer.update_page_form_field_values(None,{target:fact['value']},auto_regenerate=False)
    regenerated=tmp_path/'regenerated.pdf'
    with regenerated.open('wb') as stream:writer.write(stream)
    reopened=PdfReader(regenerated);regenerated_widget,_=widgets(reopened)[target][0]
    assert str(reopened.get_fields()[target]['/V'])==fact['value']
    assert appearance_text(regenerated_widget)==fact['value']
    assert verify_output(source,regenerated,values,profile=profile)['status']=='passed'
    assert source.read_bytes()==before


@pytest.mark.parametrize('change',['local_encoding','old_font','cjk_binding','extra_font','unselected_resources','da'])
def test_unicode_native_local_resource_exception_keeps_source_properties_strict(change,tmp_path):
    entry=next(e for e in ENTRIES if 'sgs' in e['id'])
    source,profile=source_and_profile(entry);values={'제품명':'한미플루 75mg'}
    good=fill_compatible_template(source,values,tmp_path/'good.pdf',profile=profile)
    writer=PdfWriter();writer.clone_document_from_reader(PdfReader(good))
    target=next(f for f in profile['fields'] if f['value_key']=='제품명')['original_field_name']
    widget,_=widgets(writer)[target][0]
    resource=re.match(r'(/CompatCJK[0-9a-f]{12}) ',str(widget['/DA']))[1]
    fonts=widget['/DR']['/Font']
    old_name=next(name for name in fonts if not str(name).startswith('/CompatCJK'))
    if change=='local_encoding':del widget['/DR']['/Encoding']
    elif change=='old_font':fonts[old_name][NameObject('/BaseFont')]=NameObject('/Courier')
    elif change=='cjk_binding':fonts[NameObject(resource)]=fonts.get(old_name)
    elif change=='extra_font':fonts[NameObject('/OtherFont')]=fonts.get(old_name)
    elif change=='unselected_resources':
        other=next(items[0][0] for name,items in widgets(writer).items() if name!=target and '/DR' in items[0][0])
        other['/DR'][NameObject('/Extra')]=TextStringObject('MUTATED')
    else:widget[NameObject('/DA')]=TextStringObject('/Helvetica 0 Tf 0 g')
    wrong=tmp_path/'wrong.pdf'
    with wrong.open('wb') as stream:writer.write(stream)
    with pytest.raises(ValueError,match='자원|글꼴|속성|연결'):
        verify_output(source,wrong,values,profile=profile)


def test_global_unicode_font_addition_does_not_mutate_shared_unselected_local_resources(tmp_path):
    from reportlab.pdfgen import canvas
    from templates import analyze_template
    original=tmp_path/'source.pdf';drawing=canvas.Canvas(str(original))
    drawing.acroForm.textfield(name='Product',x=50,y=600,width=250,height=30)
    drawing.acroForm.textfield(name='Unselected',x=50,y=550,width=250,height=30)
    drawing.showPage();drawing.save()
    writer=PdfWriter();writer.clone_document_from_reader(PdfReader(original))
    shared=writer.root_object['/AcroForm'].get('/DR')
    for items in widgets(writer).values():
        items[0][0][NameObject('/DR')]=shared
    with original.open('wb') as stream:writer.write(stream)
    profile=analyze_template(original);values={'Product':'한글 시험용'}
    output=fill_compatible_template(original,values,tmp_path/'filled.pdf',profile=profile)
    assert verify_output(original,output,values,profile=profile)['status']=='passed'


def test_unicode_medical_signs_preserve_their_exact_codepoints_in_original_and_reedited_appearance(tmp_path):
    entry=next(e for e in ENTRIES if 'sgs' in e['id']);source,profile=source_and_profile(entry)
    value='한글 µ μ Ω Ω';values={'제품명':value}
    output=fill_compatible_template(source,values,tmp_path/'symbols.pdf',profile=profile)
    target=next(f for f in profile['fields'] if f['value_key']=='제품명')['original_field_name']
    assert appearance_text(widgets(PdfReader(output))[target][0][0])==value
    writer=PdfWriter();writer.clone_document_from_reader(PdfReader(output))
    writer.update_page_form_field_values(None,{target:value},auto_regenerate=False)
    result=tmp_path/'reedit.pdf'
    with result.open('wb') as stream:writer.write(stream)
    assert str(PdfReader(result).get_fields()[target]['/V'])==value
    assert appearance_text(widgets(PdfReader(result))[target][0][0])==value
    assert verify_output(source,result,values,profile=profile)['status']=='passed'


def test_same_glyph_unicode_alias_never_silently_changes_value_or_replaces_previous_output(tmp_path,monkeypatch):
    from templates import compatibility
    entry=next(e for e in ENTRIES if 'sgs' in e['id']);source,profile=source_and_profile(entry)
    font=compatibility._pdf_font({'font_path':str(ROOT/'templates/fonts/NotoSansKR-Regular.ttf')},'한글 µ μ')
    face=compatibility.pdfmetrics.getFont(font).face
    changed=dict(face.charToGlyph);changed[ord('μ')]=changed[ord('µ')]
    monkeypatch.setattr(face,'charToGlyph',changed)
    result=tmp_path/'previous.pdf';result.write_bytes(b'PRESERVED PREVIOUS OUTPUT')
    before=source.read_bytes()
    with pytest.raises(TemplateError,match='역매핑'):
        fill_compatible_template(source,{'제품명':'한글 µ μ'},result,profile=profile)
    assert result.read_bytes()==b'PRESERVED PREVIOUS OUTPUT' and source.read_bytes()==before


def test_identity_font_scheme_never_reuses_old_unicode_cid_font_resource(tmp_path):
    from templates import compatibility
    old=ROOT/'outputs/ra-cross-company/sgs-hangul-fix/ra-kr-hanmiflu-75.pdf'
    if not old.is_file():pytest.skip('이전 CID 스킴 QA 원본 별도 필요함')
    writer=PdfWriter();writer.clone_document_from_reader(PdfReader(old))
    fonts=writer.root_object['/AcroForm']['/DR']['/Font']
    prior={name:font.get_object() for name,font in fonts.items() if name.startswith('/CompatCJK')}
    assert prior and all(font['/DescendantFonts'][0].get('/CIDToGIDMap')!='/Identity' for font in prior.values())
    font=compatibility._pdf_font({},'한미플루 75mg')
    name=compatibility._pdf_cjk_form_font(writer,font,'한미플루 75mg')
    assert name not in prior and fonts is not writer.root_object['/AcroForm']['/DR']['/Font']
    current=writer.root_object['/AcroForm']['/DR']['/Font']
    assert current[name]['/DescendantFonts'][0]['/CIDToGIDMap']=='/Identity'
    assert all(current[old_name] is old_font for old_name,old_font in prior.items())
