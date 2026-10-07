from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

from lxml import etree
import pytest

from parsers import ParseError, parse_file, parse_pptx
from parsers.pptx import A,P,NS
from templates import TemplateError, analyze_template, fill_compatible_template

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'samples/sample_company_form.pptx'
VALUES={'제목':'시험용 결과보고', '요약':'□ 핵심 결과를 확인함', '본문':'□ 결론을 제시함\n○ 근거를 확인함\n- 후속 검토함', '작성자':'시험홍길동', '업무 메모':'제출 금지 시험'}


def rewrite(path,changes):
    with ZipFile(SOURCE) as source, ZipFile(path,'w') as target:
        for item in source.infolist():
            target.writestr(item,changes.get(item.filename,source.read(item.filename)))
    return path


def test_parse_common_contract_slide_shape_and_table_locations():
    result=parse_file(SOURCE)
    assert set(result)=={'파일명','본문','표 목록','페이지/시트 정보'}
    assert result==parse_pptx(SOURCE)
    assert '{{제목}}' in result['본문']
    assert {block['페이지'] for block in result['페이지/시트 정보']}=={1,2}
    assert all(block['도형 ID'] and '슬라이드' in block['위치'] for block in result['페이지/시트 정보'])
    assert result['표 목록'][0]['행'][2]==['작성자','']


def test_profile_recognizes_placeholders_empty_shapes_and_table_cells():
    profile=analyze_template(SOURCE)
    assert profile['format']=='pptx' and profile['supported']
    assert {field['kind'] for field in profile['fields']}=={'placeholder','pptx_cell','pptx_text'}
    author=next(field for field in profile['fields'] if field['label']=='작성자')
    assert author['input_required'] is True


def test_fill_preserves_original_zip_styles_theme_and_other_slides(tmp_path):
    before=sha256(SOURCE.read_bytes()).hexdigest()
    output=fill_compatible_template(SOURCE,VALUES,tmp_path/'filled.pptx')
    parsed=parse_pptx(output)
    assert all(value in parsed['본문'] for value in VALUES.values())
    assert sha256(SOURCE.read_bytes()).hexdigest()==before
    with ZipFile(SOURCE) as source,ZipFile(output) as filled:
        assert set(source.namelist())==set(filled.namelist())
        for part in source.namelist():
            if part!='ppt/slides/slide1.xml':
                assert source.read(part)==filled.read(part)
        original=etree.fromstring(source.read('ppt/slides/slide1.xml'))
        changed=etree.fromstring(filled.read('ppt/slides/slide1.xml'))
        for xpath in ['.//a:tblPr','.//a:tcPr','.//a:pPr','.//a:xfrm','.//p:spPr','.//a:tblGrid']:
            assert [etree.tostring(n) for n in original.xpath(xpath,namespaces=NS)]==[etree.tostring(n) for n in changed.xpath(xpath,namespaces=NS)]
        assert len(changed.findall('.//'+f'{{{A}}}br'))==2
        assert original.xpath('.//a:rPr[@b="1"]',namespaces=NS)
        assert changed.xpath('.//a:rPr[@b="1"]',namespaces=NS)


def test_input_literals_and_xml_characters_are_not_recursively_substituted(tmp_path):
    values=VALUES|{'본문':'□ A&B <검토> {{제목}}임', '업무 메모':'{{본문}} 그대로임'}
    output=fill_compatible_template(SOURCE,values,tmp_path/'literal.pptx')
    text=parse_file(output)['본문']
    assert values['본문'] in text and values['업무 메모'] in text


def test_missing_required_and_source_overwrite_are_blocked(tmp_path):
    with pytest.raises(TemplateError,match='누락'):
        fill_compatible_template(SOURCE,{'제목':'제목'},tmp_path/'missing.pptx')
    with pytest.raises(TemplateError):
        fill_compatible_template(SOURCE,VALUES,SOURCE)


def test_source_hash_prevents_stale_profile(tmp_path):
    profile=analyze_template(SOURCE)
    profile['source_sha256']='0'*64
    with pytest.raises(TemplateError,match='변경'):
        fill_compatible_template(SOURCE,VALUES,tmp_path/'stale.pptx',profile=profile)


def test_nonempty_original_shape_cannot_be_changed_with_forged_profile(tmp_path):
    profile=analyze_template(SOURCE)
    with ZipFile(SOURCE) as archive:
        root=etree.fromstring(archive.read('ppt/slides/slide1.xml'))
    body=root.xpath('//p:sp[1]/p:txBody',namespaces=NS)[0]
    field={'id':'pptx:ppt/slides/slide1.xml:'+root.getroottree().getpath(body),'label':'기존 설명','kind':'pptx_text','value_key':'제목','required':False}
    profile['fields']=[field]
    with pytest.raises(TemplateError,match='기입할 수 없는'):
        fill_compatible_template(SOURCE,VALUES,tmp_path/'forged.pptx',profile=profile)


def test_locked_shape_is_excluded_and_protected_deck_is_rejected(tmp_path):
    with ZipFile(SOURCE) as archive:
        root=etree.fromstring(archive.read('ppt/slides/slide1.xml'))
        presentation=etree.fromstring(archive.read('ppt/presentation.xml'))
    locked=root.xpath('//p:sp[last()]/p:nvSpPr/p:cNvSpPr',namespaces=NS)[0]
    etree.SubElement(locked,f'{{{A}}}spLocks',noTextEdit='1')
    path=rewrite(tmp_path/'locked.pptx',{'ppt/slides/slide1.xml':etree.tostring(root)})
    profile=analyze_template(path)
    assert all(field['label']!='업무 메모' for field in profile['fields'])
    assert any('잠긴' in warning for warning in profile['warnings'])
    etree.SubElement(presentation,f'{{{P}}}modifyVerifier')
    path=rewrite(tmp_path/'protected.pptx',{'ppt/presentation.xml':etree.tostring(presentation)})
    assert analyze_template(path)['supported'] is False
    with pytest.raises(TemplateError):
        fill_compatible_template(path,VALUES,tmp_path/'protected-output.pptx')


def test_slide_order_follows_relationships_not_filename(tmp_path):
    with ZipFile(SOURCE) as archive:
        root=etree.fromstring(archive.read('ppt/presentation.xml'))
    slides=root.find(f'{{{P}}}sldIdLst')
    first=slides[0]
    slides.remove(first)
    slides.append(first)
    path=rewrite(tmp_path/'reordered.pptx',{'ppt/presentation.xml':etree.tostring(root)})
    result=parse_file(path)
    assert result['페이지/시트 정보'][0]['페이지']==1
    assert '두 번째 슬라이드' in result['페이지/시트 정보'][0]['본문']


@pytest.mark.parametrize('suffix', ['ppt','pptx'])
def test_invalid_or_legacy_file_is_clear_error(tmp_path,suffix):
    path=tmp_path/f'bad.{suffix}'
    path.write_bytes(b'invalid')
    with pytest.raises(ParseError) as error:
        parse_file(path)
    assert error.value.code==('unsupported_format' if suffix=='ppt' else 'invalid_file')
