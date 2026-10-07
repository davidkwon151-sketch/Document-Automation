"""Hand-built source/prepared packages, without a transformer or filler oracle."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

from lxml import etree
import pytest

from agent.repeat_xlsx_check import verify_xlsx_repeat, S, R, P, C, XDR, X14, XM


SHEET = 'xl/worksheets/sheet1.xml'
OTHER = 'xl/worksheets/sheet2.xml'
BOOK = 'xl/workbook.xml'
DRAWING = 'xl/drawings/drawing1.xml'
CHART = 'xl/charts/chart1.xml'
TABLE = 'xl/tables/table1.xml'
PLAN = {'table_id': 'xlsx:' + SHEET, 'row': 2, 'count': 3}
NS = {'s': S, 'r': R, 'c': C, 'xdr': XDR, 'x14': X14, 'xm': XM}


def _encode(value):
    return value.encode('utf-8') if isinstance(value, str) else value


def _write(path, parts):
    with ZipFile(path, 'w') as archive:
        for part, content in parts.items():
            archive.writestr(part, _encode(content))
    return path


def _package(count=1, protected_other=False):
    """Manually spell out both expected addresses and formulas for any count."""
    delta, last, total = count - 1, count + 4, count + 2
    rows = [f'<row r="1"><c r="A1" t="inlineStr"><is><t>품목</t></is></c></row>']
    for number in range(2, count + 2):
        cached = '<v>0</v>' if count == 1 else ''
        rows.append(f'<row r="{number}" ht="22" s="2" customFormat="1" customHeight="1" spans="1:9">'
                    f'<c r="A{number}" s="2" t="inlineStr"><is><r><rPr><b/><sz val="11"/></rPr><t>{{{{품목}}}}</t></r></is></c>'
                    f'<c r="B{number}" s="2"/><c r="C{number}" s="2"><f>B{number}*$G${last}+Other!A{number}</f>{cached}</c>'
                    f'<c r="H{number}" s="2"/><c r="I{number}" s="2"/></row>')
    cached = '<v>0</v>' if count == 1 else ''
    rows.extend([f'<row r="{total}"><c r="A{total}" t="inlineStr"><is><t>합계</t></is></c><c r="C{total}" s="2"><f>SUM(B2:B{count + 1})</f>{cached}</c></row>',
                 f'<row r="{last}"><c r="G{last}" s="2"><v>3</v></c></row>'])
    merges = [f'<mergeCell ref="H2:I2"/>', f'<mergeCell ref="H{last}:I{last}"/>']
    merges.extend(f'<mergeCell ref="H{number}:I{number}"/>' for number in range(3, count + 2))
    sheet = f'''<worksheet xmlns="{S}" xmlns:r="{R}">
      <dimension ref="A1:I{last}"/>
      <sheetViews><sheetView workbookViewId="0" topLeftCell="A{total + 1}"><pane state="frozen" ySplit="{total}" topLeftCell="A{total + 1}" activePane="bottomLeft"/><selection pane="bottomLeft" activeCell="A{total}" sqref="A{total} B2"/></sheetView></sheetViews>
      <cols><col min="1" max="9" width="16" customWidth="1"/></cols>
      <sheetData>{''.join(rows)}</sheetData>
      <autoFilter ref="A1:C{count + 1}"><sortState ref="A2:C{count + 1}"><sortCondition ref="A2:A{count + 1}"/></sortState></autoFilter>
      <mergeCells count="{len(merges)}">{''.join(merges)}</mergeCells>
      <conditionalFormatting sqref="B2:B{count + 1}"><cfRule type="expression" priority="1"><formula>B2&gt;0</formula></cfRule></conditionalFormatting>
      <dataValidations count="2"><dataValidation type="list" allowBlank="1" sqref="B2:B{count + 1} B{last}"><formula1>"예,아니요"</formula1></dataValidation><dataValidation type="custom" sqref="D2:D{count + 1}"><formula1>COUNTIF($P:$P,D2)&lt;2</formula1></dataValidation></dataValidations>
      <hyperlinks><hyperlink ref="A{last}" location="Other!A3" tooltip="그대로"/></hyperlinks>
      <ignoredErrors><ignoredError sqref="G{last}" numberStoredAsText="1"/></ignoredErrors>
      <pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>
      <rowBreaks count="1" manualBreakCount="1"><brk id="{total}" min="0" max="16383" man="1"/></rowBreaks>
      <drawing r:id="draw"/><tableParts count="1"><tablePart r:id="tbl"/></tableParts>
    </worksheet>'''
    other_formula = "SUM(A3:A4)" if protected_other else f"'폼'!$B${total}+SUM('폼'!$B$2:$B${count + 1})"
    other_cached = '<v>10</v>' if count == 1 or protected_other else ''
    other = f'<worksheet xmlns="{S}"><sheetData><row r="1"><c r="A1"><f>{other_formula}</f>{other_cached}</c></row></sheetData>' + ('<sheetProtection sheet="1" password="ABCD"/>' if protected_other else '') + '</worksheet>'
    book = f'''<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="폼" sheetId="1" r:id="s1"/><sheet name="Other" sheetId="2" r:id="s2"/></sheets>
      <definedNames><definedName name="Items">'폼'!$B$2:$B${count + 1}</definedName><definedName name="_xlnm.Print_Area" localSheetId="0">'폼'!$A$1:$I${last}</definedName><definedName name="Local" localSheetId="1">A3:A4</definedName></definedNames>
      <calcPr calcId="181029" calcMode="manual" fullPrecision="1" fullCalcOnLoad="{1 if count > 1 else 0}" forceFullCalc="{1 if count > 1 else 0}"/></workbook>'''
    types = {'xl/workbook.xml': 'sheet.main', SHEET: 'worksheet', OTHER: 'worksheet', TABLE: 'table', CHART: 'chart', DRAWING: 'drawing'}
    ct = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    ct += ''.join(f'<Override PartName="/{part}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.{kind}+xml"/>' for part, kind in types.items())
    if count == 1:
        ct += '<Override PartName="/xl/calcChain.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"/>'
    ct += '</Types>'
    rels = f'<Relationships xmlns="{P}"><Relationship Id="s1" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="s2" Type="{R}/worksheet" Target="worksheets/sheet2.xml"/>'
    if count == 1:
        rels += f'<Relationship Id="chain" Type="{R}/calcChain" Target="calcChain.xml"/>'
    rels += '</Relationships>'
    table = f'<table xmlns="{S}" id="1" name="ItemsTable" displayName="ItemsTable" ref="A1:C{count + 1}" headerRowCount="1" totalsRowCount="0"><autoFilter ref="A1:C{count + 1}"/><tableColumns count="1"><tableColumn id="1" name="금액"><calculatedColumnFormula>\'폼\'!$G${last}+[@금액]</calculatedColumnFormula></tableColumn></tableColumns></table>'
    chart_cache = '<c:numCache><c:formatCode>General</c:formatCode><c:ptCount val="1"/><c:pt idx="0"><c:v>0</c:v></c:pt></c:numCache>' if count == 1 else ''
    chart = f'<c:chartSpace xmlns:c="{C}"><c:chart><c:plotArea><c:barChart><c:ser><c:val><c:numRef><c:f>\'폼\'!$B$2:$B${count + 1}</c:f>{chart_cache}</c:numRef></c:val></c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>'
    drawing = f'<xdr:wsDr xmlns:xdr="{XDR}"><xdr:twoCellAnchor editAs="oneCell"><xdr:from><xdr:col>0</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{5 + delta}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:to><xdr:col>3</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{6 + delta}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to><xdr:clientData/></xdr:twoCellAnchor></xdr:wsDr>'
    parts = {'[Content_Types].xml': ct, BOOK: book, 'xl/_rels/workbook.xml.rels': rels,
             SHEET: sheet, OTHER: other, TABLE: table, CHART: chart, DRAWING: drawing,
             'xl/worksheets/_rels/sheet1.xml.rels': f'<Relationships xmlns="{P}"><Relationship Id="draw" Type="{R}/drawing" Target="../drawings/drawing1.xml"/><Relationship Id="tbl" Type="{R}/table" Target="../tables/table1.xml"/></Relationships>',
             'xl/styles.xml': f'<styleSheet xmlns="{S}"><fonts count="1"><font><name val="맑은 고딕"/><sz val="11"/></font></fonts></styleSheet>',
             'docProps/core.xml': '<properties>ORIGINAL</properties>'}
    if count == 1:
        parts['xl/calcChain.xml'] = f'<calcChain xmlns="{S}"><c r="C2" i="1"/></calcChain>'
    return {part: _encode(content) for part, content in parts.items()}


def _edit(parts, part, path, *, attr=None, value=None, remove=False):
    xml = etree.fromstring(parts[part])
    node = xml.xpath(path, namespaces=NS)[0]
    if remove:
        node.getparent().remove(node)
    elif attr:
        node.set(attr, value)
    else:
        node.text = value
    parts[part] = etree.tostring(xml)


def _run(tmp_path, source=None, prepared=None, plan=None):
    a = _write(tmp_path / 'original.xlsx', source or _package())
    b = _write(tmp_path / 'prepared.xlsx', prepared or _package(3))
    return verify_xlsx_repeat(a, b, plan or PLAN)


@pytest.mark.parametrize('count', [1, 2, 3, 200])
def test_source_bound_rows_formulas_ranges_and_metadata(tmp_path, count):
    source = _package()
    a = _write(tmp_path / 'original.xlsx', source)
    b = _write(tmp_path / 'prepared.xlsx', _package(count))
    before = a.read_bytes()
    report = verify_xlsx_repeat(a, b, dict(PLAN, count=count))
    assert report['status'] == 'passed'
    assert report['added_rows'] == count - 1
    assert report['original_sha256'] == sha256(before).hexdigest()
    assert a.read_bytes() == before
    assert report['native_formula_recalculation'] == report['native_visual_qa'] == 'pending'
    assert report['deleted_parts'] == ([] if count == 1 else ['xl/calcChain.xml'])
    assert not report['full_submission_ready']


@pytest.mark.parametrize('part,path,attribute,value', [
    (SHEET, './/s:row[@r="3"]/s:c[@r="A3"]', 's', '9'),
    (SHEET, './/s:row[@r="3"]', 'ht', '1'),
    (SHEET, './/s:row[@r="3"]/s:c[@r="A3"]', 'r', 'A9'),
    (SHEET, './/s:col', 'width', '99'),
    (SHEET, './/s:mergeCell', 'ref', 'H2:I4'),
    (SHEET, './/s:dataValidation', 'sqref', 'B2:B2 B7'),
    (SHEET, './/s:conditionalFormatting', 'sqref', 'B2:B2'),
    (SHEET, './/s:sortCondition', 'ref', 'A2:A2'),
    (SHEET, './/s:selection', 'activeCell', 'A3'),
    (SHEET, './/s:pane', 'ySplit', '3'),
    (SHEET, './/s:hyperlink', 'ref', 'A5'),
    (SHEET, './/s:brk', 'id', '3'),
    (SHEET, './/s:ignoredError', 'sqref', 'G5'),
    (TABLE, '.', 'ref', 'A1:C2'),
    (BOOK, './/s:calcPr', 'calcMode', 'auto'),
    (BOOK, './/s:calcPr', 'fullPrecision', '0'),
    (BOOK, './/s:calcPr', 'forceFullCalc', '0'),
])
def test_rejects_address_style_merge_range_and_settings_tampering(tmp_path, part, path, attribute, value):
    prepared = _package(3)
    _edit(prepared, part, path, attr=attribute, value=value)
    with pytest.raises(ValueError):
        _run(tmp_path, prepared=prepared)


@pytest.mark.parametrize('part,path,value', [
    (SHEET, './/s:c[@r="C2"]/s:f', 'B2*$G$5+Other!A2'),
    (SHEET, './/s:c[@r="C3"]/s:f', 'B3*$G$8+Other!A3'),
    (SHEET, './/s:c[@r="C3"]/s:f', 'B3*$G$7+Other!A2'),
    (SHEET, './/s:c[@r="C5"]/s:f', 'SUM(B2:B2)'),
    (SHEET, './/s:c[@r="A3"]//s:t', 'MALICIOUS'),
    (SHEET, './/s:dataValidation/s:formula1', '"YES,NO"'),
    (OTHER, './/s:f', "'폼'!$B$3+SUM('폼'!$B$2:$B$2)"),
    (OTHER, './/s:f', 'SUM(A1:A100)'),
    (BOOK, './/s:definedName[@name="Items"]', "'폼'!$B$2:$B$2"),
    (TABLE, './/s:calculatedColumnFormula', "'폼'!$G$5+[@금액]"),
    (CHART, './/c:f', "'폼'!$B$2:$B$2"),
    (DRAWING, './/xdr:from/xdr:row', '5'),
])
def test_rejects_formula_copy_absolute_other_sheet_and_literal_tampering(tmp_path, part, path, value):
    prepared = _package(3)
    _edit(prepared, part, path, value=value)
    with pytest.raises(ValueError):
        _run(tmp_path, prepared=prepared)


@pytest.mark.parametrize('operation', ['delete_styles', 'add_part', 'change_properties', 'cache', 'chart_cache', 'chain', 'restore_chain_rel', 'remove_row', 'more_rows', 'lose_source_row'])
def test_rejects_package_scope_row_and_cache_tampering(tmp_path, operation):
    source, prepared = _package(), _package(3)
    if operation == 'delete_styles':
        del prepared['xl/styles.xml']
    elif operation == 'add_part':
        prepared['xl/other.xml'] = b'<other/>'
    elif operation == 'change_properties':
        prepared['docProps/core.xml'] = b'<properties>NEW</properties>'
    elif operation in {'cache', 'chart_cache'}:
        part = SHEET if operation == 'cache' else CHART
        xml = etree.fromstring(prepared[part])
        parent = xml.xpath('.//s:c[@r="C3"]' if operation == 'cache' else './/c:numRef', namespaces=NS)[0]
        etree.SubElement(parent, f'{{{S}}}v' if operation == 'cache' else f'{{{C}}}numCache').text = '10'
        prepared[part] = etree.tostring(xml)
    elif operation == 'chain':
        prepared['xl/calcChain.xml'] = source['xl/calcChain.xml']
    elif operation == 'restore_chain_rel':
        prepared['xl/_rels/workbook.xml.rels'] = source['xl/_rels/workbook.xml.rels']
    elif operation == 'more_rows':
        prepared = _package(4)
    else:
        _edit(prepared, SHEET, './/s:row[@r="2"]' if operation == 'lose_source_row' else './/s:row[@r="3"]', remove=True)
    with pytest.raises(ValueError):
        _run(tmp_path, source, prepared)


def test_unaffected_protected_sheet_preserved_byte_for_byte(tmp_path):
    source, prepared = _package(protected_other=True), _package(3, protected_other=True)
    assert source[OTHER] == prepared[OTHER]
    assert _run(tmp_path, source, prepared)['status'] == 'passed'


@pytest.mark.parametrize('which', [SHEET, OTHER])
def test_target_or_affected_protected_sheet_is_blocked(tmp_path, which):
    source = _package()
    xml = etree.fromstring(source[which])
    etree.SubElement(xml, f'{{{S}}}sheetProtection', sheet='1', password='ABCD')
    source[which] = etree.tostring(xml)
    with pytest.raises(ValueError, match='보호'):
        _run(tmp_path, source)


def test_count_one_is_noop_and_still_preserves_formula_cache(tmp_path):
    source = _package()
    assert _run(tmp_path, source, source, dict(PLAN, count=1))['changed_parts'] == []
    prepared = deepcopy(source)
    _edit(prepared, SHEET, './/s:c[@r="C2"]/s:v', remove=True)
    with pytest.raises(ValueError):
        _run(tmp_path, source, prepared, dict(PLAN, count=1))


@pytest.mark.parametrize('formula', ['INDIRECT("B3")', 'OFFSET(B2,1,0)', "'Other:폼'!A3", '[Other.xlsx]폼!A3', 'SUM(B3:B2)', 'B1048576', '#REF!'])
def test_unsupported_or_overflowing_formula_is_explicitly_blocked(tmp_path, formula):
    source = _package()
    _edit(source, SHEET, './/s:c[@r="C2"]/s:f', value=formula)
    with pytest.raises(ValueError):
        _run(tmp_path, source)


@pytest.mark.parametrize('tag,attribute,value', [
    ('f', 't', 'shared'), ('f', 't', 'array'), ('f', 'ref', 'C2:C3'),
    ('c', 'cm', '1'),
])
def test_shared_array_and_dynamic_metadata_blocked(tmp_path, tag, attribute, value):
    source = _package()
    _edit(source, SHEET, './/s:c[@r="C2"]' + ('/s:f' if tag == 'f' else ''), attr=attribute, value=value)
    with pytest.raises(ValueError):
        _run(tmp_path, source)


@pytest.mark.parametrize('unsafe', ['vertical_merge', 'header', 'total', 'sequence', 'no_input', 'drawing_crosses', 'unknown_extension', 'legacy', 'workbook_protection'])
def test_unsafe_prototype_or_unsupported_layout_is_blocked(tmp_path, unsafe):
    source = _package()
    plan = deepcopy(PLAN)
    if unsafe == 'vertical_merge':
        _edit(source, SHEET, './/s:mergeCell', attr='ref', value='H2:I3')
    elif unsafe == 'header':
        plan['row'] = 1
    elif unsafe == 'total':
        plan['row'] = 3
    elif unsafe == 'drawing_crosses':
        _edit(source, DRAWING, './/xdr:from/xdr:row', value='1')
    elif unsafe == 'workbook_protection':
        xml = etree.fromstring(source[BOOK]); etree.SubElement(xml, f'{{{S}}}workbookProtection', lockStructure='1'); source[BOOK] = etree.tostring(xml)
    else:
        xml = etree.fromstring(source[SHEET])
        if unsafe == 'sequence':
            cell = xml.xpath('.//s:c[@r="A2"]', namespaces=NS)[0]
            cell.attrib.pop('t'); cell.remove(cell[0]); etree.SubElement(cell, f'{{{S}}}v').text = '1'
        elif unsafe == 'no_input':
            row = xml.xpath('.//s:row[@r="2"]', namespaces=NS)[0]
            for cell in list(row)[1:]: row.remove(cell)
            row[0].xpath('.//s:t', namespaces=NS)[0].text = 'FIXED EXISTING VALUE'
        elif unsafe == 'unknown_extension':
            etree.SubElement(xml, f'{{{S}}}extLst')
            ext = etree.SubElement(xml[-1], f'{{{S}}}ext', uri='unknown'); etree.SubElement(ext, '{unknown}rows', ref='A3')
        elif unsafe == 'legacy':
            etree.SubElement(xml, f'{{{S}}}legacyDrawing', {f'{{{R}}}id': 'VML'})
        source[SHEET] = etree.tostring(xml)
    with pytest.raises(ValueError):
        _run(tmp_path, source, plan=plan)


def test_full_sheet_dv_range_caps_last_row_while_refs_remain_exact(tmp_path):
    source, prepared = _package(), _package(3)
    _edit(source, SHEET, './/s:dataValidation', attr='sqref', value='B2:B1048576')
    _edit(prepared, SHEET, './/s:dataValidation', attr='sqref', value='B2:B1048576')
    _edit(source, SHEET, './/s:dataValidation[2]/s:formula1', value='COUNTIF($P:$P,D2)<2')
    _edit(prepared, SHEET, './/s:dataValidation[2]/s:formula1', value='COUNTIF($P:$P,D2)<2')
    assert _run(tmp_path, source, prepared)['status'] == 'passed'


def _add_x14(parts, count):
    xml = etree.fromstring(parts[SHEET])
    extension = etree.SubElement(etree.SubElement(xml, f'{{{S}}}extLst'), f'{{{S}}}ext', uri='{CCE6A557-97BC-4B89-ADB6-D9C93CAAB3DF}')
    container = etree.SubElement(extension, f'{{{X14}}}dataValidations', count='1')
    rule = etree.SubElement(container, f'{{{X14}}}dataValidation', type='list', allowBlank='1')
    etree.SubElement(etree.SubElement(rule, f'{{{X14}}}formula1'), f'{{{XM}}}f').text = '"가능,불가"'
    etree.SubElement(rule, f'{{{XM}}}sqref').text = f'D2:D{count + 1}'
    parts[SHEET] = etree.tostring(xml)


def test_x14_closed_list_options_and_addresses_are_preserved(tmp_path):
    source, prepared = _package(), _package(3)
    _add_x14(source, 1); _add_x14(prepared, 3)
    assert _run(tmp_path, source, prepared)['status'] == 'passed'
    _edit(prepared, SHEET, './/xm:f', value='"possibly,yes"')
    with pytest.raises(ValueError):
        _run(tmp_path, source, prepared)


def test_formula_string_literal_and_whitespace_preserved_separately_from_addresses(tmp_path):
    source, prepared = _package(), _package(3)
    _edit(source, SHEET, './/s:c[@r="C2"]/s:f', value='IF(B2="B5",  B2*$G$5,Other!A2)')
    for number in [2, 3, 4]:
        _edit(prepared, SHEET, f'.//s:c[@r="C{number}"]/s:f', value=f'IF(B{number}="B5",  B{number}*$G$7,Other!A{number})')
    assert _run(tmp_path, source, prepared)['status'] == 'passed'


@pytest.mark.parametrize('bad', [None, {}, dict(PLAN, extra=1), dict(PLAN, row=True), dict(PLAN, count=True), dict(PLAN, count=0), dict(PLAN, count=201), dict(PLAN, row=0), dict(PLAN, table_id='xlsx:../sheet.xml'), dict(PLAN, table_id='xlsx:xl/worksheets/sheet1.xml ')])
def test_strict_plan_schema(tmp_path, bad):
    a, b = _write(tmp_path / 'a.xlsx', _package()), _write(tmp_path / 'b.xlsx', _package(3))
    with pytest.raises(ValueError):
        verify_xlsx_repeat(a, b, bad)


def test_same_file_is_blocked(tmp_path):
    path = _write(tmp_path / 'original.xlsx', _package())
    with pytest.raises(ValueError, match='같은|다른'):
        verify_xlsx_repeat(path, path, PLAN)


@pytest.mark.parametrize('name', ['../evil.xml', 'xl\\evil.xml', '/evil.xml', 'xl/evil:part.xml'])
def test_unsafe_zip_paths_are_blocked(tmp_path, name):
    source = _package(); source[name] = b'<evil/>'
    with pytest.raises(ValueError):
        _run(tmp_path, source)


def test_size_limit_checked_without_loading_huge_package(tmp_path, monkeypatch):
    import agent.repeat_xlsx_check as checker
    a = _write(tmp_path / 'original.xlsx', _package())
    b = _write(tmp_path / 'prepared.xlsx', _package(3))
    monkeypatch.setattr(checker, 'MAX_BYTES', 100)
    with pytest.raises(ValueError, match='크기'):
        verify_xlsx_repeat(a, b, PLAN)


def test_checker_does_not_import_transformer_or_translator(tmp_path, monkeypatch):
    import openpyxl.formula.translate
    def forbidden(*args, **kwargs):
        raise AssertionError('a transformer cannot be the independent oracle')
    monkeypatch.setattr(openpyxl.formula.translate.Translator, 'translate_formula', forbidden)
    assert _run(tmp_path)['status'] == 'passed'


@pytest.mark.parametrize('before,after2,after3,after4', [
    ('B2+$G$5', 'B2+$G$7', 'B3+$G$7', 'B4+$G$7'),
    ('B$2+$G5', 'B$2+$G7', 'B$2+$G8', 'B$2+$G9'),
    ('SUM($B$2:B2)', 'SUM($B$2:B4)', 'SUM($B$2:B5)', 'SUM($B$2:B6)'),
    ('SUM(2:2)', 'SUM(2:4)', 'SUM(3:5)', 'SUM(4:6)'),
    ('SUM($2:$2)', 'SUM($2:$4)', 'SUM($2:$4)', 'SUM($2:$4)'),
    ('COUNTIF($P:$P,B2)', 'COUNTIF($P:$P,B2)', 'COUNTIF($P:$P,B3)', 'COUNTIF($P:$P,B4)'),
    ("Other!$A2+'폼'!$B$3", "Other!$A2+'폼'!$B$5", "Other!$A3+'폼'!$B$5", "Other!$A4+'폼'!$B$5"),
    ('IF(B2="G5",1E3,Items)', 'IF(B2="G5",1E3,Items)', 'IF(B3="G5",1E3,Items)', 'IF(B4="G5",1E3,Items)'),
])
def test_independent_relative_absolute_and_full_axis_arithmetic(tmp_path, before, after2, after3, after4):
    source, prepared = _package(), _package(3)
    _edit(source, SHEET, './/s:c[@r="C2"]/s:f', value=before)
    for number, text in zip([2, 3, 4], [after2, after3, after4]):
        _edit(prepared, SHEET, f'.//s:c[@r="C{number}"]/s:f', value=text)
    assert _run(tmp_path, source, prepared)['status'] == 'passed'


def test_inactive_workbook_protection_preserved_and_active_not_removed(tmp_path):
    source, prepared = _package(), _package(3)
    for parts in [source, prepared]:
        xml = etree.fromstring(parts[BOOK])
        etree.SubElement(xml, f'{{{S}}}workbookProtection', lockStructure='0')
        parts[BOOK] = etree.tostring(xml)
    assert _run(tmp_path, source, prepared)['status'] == 'passed'
    _edit(source, BOOK, './/s:workbookProtection', attr='lockStructure', value='1')
    with pytest.raises(ValueError, match='보호'):
        _run(tmp_path, source, prepared)


@pytest.mark.parametrize('bad_password', ['workbookPassword', 'workbookHashValue'])
def test_password_or_hash_protection_never_removed(tmp_path, bad_password):
    source = _package()
    xml = etree.fromstring(source[BOOK]); etree.SubElement(xml, f'{{{S}}}workbookProtection', {bad_password: 'ABCD'})
    source[BOOK] = etree.tostring(xml)
    with pytest.raises(ValueError, match='보호'):
        _run(tmp_path, source)


def test_hyperlink_prototype_clones_only_declared_refs(tmp_path):
    source, prepared = _package(), _package(3)
    _edit(source, SHEET, './/s:hyperlink', attr='ref', value='A2')
    xml = etree.fromstring(prepared[SHEET])
    links = xml.find(f'{{{S}}}hyperlinks')
    links[0].set('ref', 'A2')
    for number in [3, 4]:
        clone = deepcopy(links[0]); clone.set('ref', f'A{number}'); links.append(clone)
    prepared[SHEET] = etree.tostring(xml)
    assert _run(tmp_path, source, prepared)['status'] == 'passed'
    _edit(prepared, SHEET, './/s:hyperlink[2]', attr='location', value='Other!A4')
    with pytest.raises(ValueError):
        _run(tmp_path, source, prepared)


def _add_note(parts, attached=0, anchor_end=0, object_type='Note'):
    name = 'xl/drawings/commentsDrawing1.vml'
    xml = etree.fromstring(parts[SHEET])
    etree.SubElement(xml, f'{{{S}}}legacyDrawing', {f'{{{R}}}id': 'note'})
    parts[SHEET] = etree.tostring(xml)
    rel = 'xl/worksheets/_rels/sheet1.xml.rels'
    xml = etree.fromstring(parts[rel])
    etree.SubElement(xml, f'{{{P}}}Relationship', Id='note', Type=f'{R}/vmlDrawing', Target='../drawings/commentsDrawing1.vml')
    parts[rel] = etree.tostring(xml)
    parts[name] = _encode(f'<xml xmlns:v="urn:schemas-microsoft-com:vml" xmlns:x="urn:schemas-microsoft-com:office:excel"><v:shape><x:ClientData ObjectType="{object_type}"><x:Anchor>0,0,0,0,1,0,{anchor_end},0</x:Anchor><x:Row>{attached}</x:Row></x:ClientData></v:shape></xml>')


def test_unaffected_comment_vml_above_prototype_preserved(tmp_path):
    source, prepared = _package(), _package(3)
    _add_note(source); _add_note(prepared)
    assert _run(tmp_path, source, prepared)['status'] == 'passed'


@pytest.mark.parametrize('attached,anchor_end,kind', [(1, 0, 'Note'), (0, 2, 'Note'), (0, 0, 'Button')])
def test_vml_comment_movement_or_active_control_is_blocked(tmp_path, attached, anchor_end, kind):
    source, prepared = _package(), _package(3)
    _add_note(source, attached, anchor_end, kind); _add_note(prepared, attached, anchor_end, kind)
    with pytest.raises(ValueError, match='VML|댓글'):
        _run(tmp_path, source, prepared)


def test_x14_single_cell_range_extends_and_wrong_uid_cannot_change(tmp_path):
    source, prepared = _package(), _package(3)
    _add_x14(source, 1); _add_x14(prepared, 3)
    _edit(source, SHEET, './/xm:sqref', value='D2')
    for parts in [source, prepared]:
        _edit(parts, SHEET, './/x14:dataValidation', attr='{urn:uid}uid', value='ORIGINAL-ID')
    assert _run(tmp_path, source, prepared)['status'] == 'passed'
    _edit(prepared, SHEET, './/x14:dataValidation', attr='{urn:uid}uid', value='REPLACED-ID')
    with pytest.raises(ValueError):
        _run(tmp_path, source, prepared)


def test_physical_last_row_overflow_never_capped(tmp_path):
    source = _package()
    _edit(source, SHEET, './/s:row[@r="5"]', attr='r', value='1048576')
    _edit(source, SHEET, './/s:c[@r="G5"]', attr='r', value='G1048576')
    with pytest.raises(ValueError, match='최대 행'):
        _run(tmp_path, source)


def test_original_existing_rows_cannot_be_replaced_with_extra_clones(tmp_path):
    prepared = _package(3)
    _edit(prepared, SHEET, './/s:c[@r="G7"]/s:v', value='4')
    with pytest.raises(ValueError):
        _run(tmp_path, prepared=prepared)


def test_duplicate_zip_parts_rejected(tmp_path):
    source = _write(tmp_path / 'a.xlsx', _package())
    with pytest.warns(UserWarning):
        with ZipFile(source, 'a') as archive:
            archive.writestr(SHEET, _package()[SHEET])
    prepared = _write(tmp_path / 'b.xlsx', _package(3))
    with pytest.raises(ValueError):
        verify_xlsx_repeat(source, prepared, PLAN)


def _other_hyperlink(parts, location, protected=False):
    xml = etree.fromstring(parts[OTHER])
    etree.SubElement(etree.SubElement(xml, f'{{{S}}}hyperlinks'), f'{{{S}}}hyperlink', ref='A3', location=location)
    if protected:
        etree.SubElement(xml, f'{{{S}}}sheetProtection', sheet='1')
    parts[OTHER] = etree.tostring(xml)


def test_other_sheet_internal_hyperlink_points_to_shifted_source_position(tmp_path):
    source, prepared = _package(), _package(3)
    _other_hyperlink(source, "'폼'!B3"); _other_hyperlink(prepared, "'폼'!B5")
    assert _run(tmp_path, source, prepared)['status'] == 'passed'
    _edit(prepared, OTHER, './/s:hyperlink', attr='location', value="'폼'!B3")
    with pytest.raises(ValueError):
        _run(tmp_path, source, prepared)


def test_protected_other_sheet_internal_hyperlink_change_is_blocked(tmp_path):
    source, prepared = _package(protected_other=True), _package(3, protected_other=True)
    _other_hyperlink(source, "'폼'!B3"); _other_hyperlink(prepared, "'폼'!B5")
    with pytest.raises(ValueError, match='보호'):
        _run(tmp_path, source, prepared)


def test_case_insensitive_sheet_reference_keeps_original_spelling(tmp_path):
    source, prepared = _package(), _package(3)
    for parts in [source, prepared]:
        for name in list(parts):
            parts[name] = parts[name].replace('폼'.encode(), b'InputForm')
    _edit(source, SHEET, './/s:c[@r="C2"]/s:f', value="'inputform'!B3")
    for number, reference in [(2, 5), (3, 6), (4, 7)]:
        _edit(prepared, SHEET, f'.//s:c[@r="C{number}"]/s:f', value=f"'inputform'!B{reference}")
    assert _run(tmp_path, source, prepared)['status'] == 'passed'


@pytest.mark.parametrize('path', [None, 1, {}, False])
def test_bad_path_types_raise_value_error(tmp_path, path):
    prepared = _write(tmp_path / 'prepared.xlsx', _package(3))
    with pytest.raises(ValueError):
        verify_xlsx_repeat(path, prepared, PLAN)


def test_unknown_x14_formula_child_is_not_silently_preserved(tmp_path):
    source = _package(); _add_x14(source, 1)
    xml = etree.fromstring(source[SHEET])
    etree.SubElement(xml.xpath('.//x14:formula1', namespaces=NS)[0], '{unknown}address', ref='A3')
    source[SHEET] = etree.tostring(xml)
    with pytest.raises(ValueError):
        _run(tmp_path, source)


def test_calcchain_named_part_is_not_authority_to_delete_unrelated_xml(tmp_path):
    source = _package(); source['xl/calcChain.xml'] = b'<actually_unrelated/>'
    with pytest.raises(ValueError, match='calcChain'):
        _run(tmp_path, source)


def _above_picture(parts, kind='oneCellAnchor', height=127000, default='15', hidden=False):
    xml = etree.fromstring(parts[SHEET])
    if default is not None:
        etree.SubElement(xml, f'{{{S}}}sheetFormatPr', defaultRowHeight=default)
    if hidden:
        xml.xpath('.//s:row[@r="1"]', namespaces=NS)[0].set('hidden', '1')
    parts[SHEET] = etree.tostring(xml)
    if kind == 'oneCellAnchor':
        anchor = '<xdr:from><xdr:col>0</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>0</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
    else:
        anchor = '<xdr:pos x="0" y="0"/>'
    parts[DRAWING] = _encode(f'<xdr:wsDr xmlns:xdr="{XDR}"><xdr:{kind}>{anchor}<xdr:ext cx="100000" cy="{height}"/><xdr:clientData/></xdr:{kind}></xdr:wsDr>')


@pytest.mark.parametrize('kind', ['oneCellAnchor', 'absoluteAnchor'])
def test_declared_picture_fits_before_prototype_without_repositioning(tmp_path, kind):
    source, prepared = _package(), _package(3)
    _above_picture(source, kind); _above_picture(prepared, kind)
    assert _run(tmp_path, source, prepared)['status'] == 'passed'
    assert source[DRAWING] == prepared[DRAWING]


@pytest.mark.parametrize('kind,kwargs', [
    ('oneCellAnchor', {'height': 254000}), ('absoluteAnchor', {'height': 254000}),
    ('oneCellAnchor', {'default': None}), ('absoluteAnchor', {'default': None}),
    ('oneCellAnchor', {'hidden': True}), ('absoluteAnchor', {'hidden': True}),
])
def test_picture_span_unknown_or_crossing_prototype_is_blocked(tmp_path, kind, kwargs):
    source, prepared = _package(), _package(3)
    _above_picture(source, kind, **kwargs); _above_picture(prepared, kind, **kwargs)
    with pytest.raises(ValueError, match='그림|높이'):
        _run(tmp_path, source, prepared)


def test_anchored_picture_address_overflow_is_blocked(tmp_path):
    source = _package()
    _edit(source, DRAWING, './/xdr:from/xdr:row', value='1048574')
    _edit(source, DRAWING, './/xdr:to/xdr:row', value='1048575')
    with pytest.raises(ValueError, match='그림 행 좌표'):
        _run(tmp_path, source)
