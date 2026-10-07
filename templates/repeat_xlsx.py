"""XLSX 입력 행을 ZIP/XML에서 확장하고 명시적 A1 의존성을 함께 이동함."""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import posixpath
import re

from lxml import etree
from openpyxl.formula import Tokenizer
from openpyxl.formula.translate import Translator
from openpyxl.utils.cell import column_index_from_string

from parsers.extract import _xml
from .fill import PLACEHOLDER, TemplateError


S='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P='http://schemas.openxmlformats.org/package/2006/relationships'
NS={'s':S,'r':R}
X14='http://schemas.microsoft.com/office/spreadsheetml/2009/9/main'
XM='http://schemas.microsoft.com/office/excel/2006/main'
NS.update(x14=X14,xm=XM)
CELL=re.compile(r'(\$?)([A-Za-z]{1,3})(\$?)([1-9][0-9]{0,6})')
ROW=re.compile(r'(\$?)([1-9][0-9]{0,6})')
COL=re.compile(r'\$?[A-Za-z]{1,3}')
MAX_ROW=1048576
UNSAFE_FUNCTIONS={'INDIRECT','OFFSET','FILTER','UNIQUE','SORT','SORTBY','SEQUENCE','RANDARRAY','LAMBDA','LET'}


def _serial(root):
    return etree.tostring(root,encoding='UTF-8',xml_declaration=True,standalone=True)


def _target(part,target):
    resolved=posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(part),target))
    if resolved.startswith('../'):
        raise TemplateError('패키지 밖의 XLSX 부품 참조임')
    return resolved


def _relations(parts,part):
    name=posixpath.join(posixpath.dirname(part),'_rels',posixpath.basename(part)+'.rels')
    if name not in parts:
        return {}
    root=_xml(parts[name])
    if len({node.get('Id') for node in root})!=len(root): raise TemplateError('부품 관계 ID가 중복됨')
    return {node.get('Id'):_target(part,node.get('Target','')) for node in root
            if node.get('TargetMode')!='External'}


def _package(parts):
    if not isinstance(parts,dict) or 'xl/workbook.xml' not in parts:
        raise TemplateError('XLSX 통합문서 부품이 없음')
    workbook=_xml(parts['xl/workbook.xml'])
    if workbook.tag!=f'{{{S}}}workbook': raise TemplateError('지원하지 않는 통합문서 XML 종류임')
    relations=_relations(parts,'xl/workbook.xml')
    sheets=[]
    for index,node in enumerate(workbook.findall(f'{{{S}}}sheets/{{{S}}}sheet')):
        part=relations.get(node.get(f'{{{R}}}id'))
        if part not in parts or not part.startswith('xl/worksheets/'):
            raise TemplateError('XLSX 시트 관계가 없거나 지원하지 않는 시트 종류임')
        sheet_name=node.get('name'); sheet_root=_xml(parts[part])
        if not sheet_name or any(char in sheet_name for char in '[]:') or sheet_root.tag!=f'{{{S}}}worksheet':
            raise TemplateError('지원하지 않는 시트 이름 또는 XML 종류임')
        sheets.append({'part':part,'name':sheet_name,'index':index,'id':node.get('sheetId'),'root':sheet_root})
    if len({item['part'] for item in sheets})!=len(sheets) or len({item['name'].casefold() for item in sheets})!=len(sheets):
        raise TemplateError('시트 이름 또는 부품이 중복됨')
    shared=[]
    if 'xl/sharedStrings.xml' in parts:
        shared=[''.join(node.itertext()) for node in _xml(parts['xl/sharedStrings.xml'])]
    tables={name:_xml(data) for name,data in parts.items() if re.fullmatch(r'xl/tables/table\d+\.xml',name)}
    return workbook,sheets,shared,tables


def _text(cell,shared):
    if cell.find(f'{{{S}}}f') is not None:
        return '='+str(cell.find(f'{{{S}}}f').text or '')
    if cell.get('t')=='inlineStr':
        return ''.join(cell.xpath('./s:is//s:t/text()',namespaces=NS))
    value=cell.find(f'{{{S}}}v')
    if value is None: return ''
    if cell.get('t')=='s':
        try: return shared[int(value.text)]
        except (ValueError,TypeError,IndexError) as exc: raise TemplateError('유효하지 않은 공유 문자열 주소임') from exc
    return value.text or ''


def _cell(coordinate):
    match=CELL.fullmatch(coordinate or '')
    if not match or int(match[4])>MAX_ROW or column_index_from_string(match[2])>16384:
        raise TemplateError('지원 범위를 넘거나 잘못된 Excel 셀 주소임: '+str(coordinate))
    return match


def _row(coordinate):
    match=ROW.fullmatch(coordinate)
    if not match or int(match[2])>MAX_ROW:
        raise TemplateError('지원 범위를 넘거나 잘못된 Excel 행 주소임')
    return match


def _range(value,cut,delta,*,expand=True,single_expand=False):
    """같은 시트의 A1/행/열 범위. $는 이동에는 영향을 주지 않음."""
    items=value.split(':')
    if len(items)>2: raise TemplateError('3D 또는 모호한 범위 주소임: '+value)
    if all(COL.fullmatch(item or '') for item in items):
        if any(column_index_from_string(item.replace('$',''))>16384 for item in items): raise TemplateError('Excel 열 범위를 넘음')
        return value
    cells=all(CELL.fullmatch(item or '') for item in items)
    parser=_cell if cells else _row
    matches=[parser(item) for item in items]
    numbers=[int(match[4] if cells else match[2]) for match in matches]
    if len(numbers)==2 and numbers[0]>numbers[1]:
        raise TemplateError('역순 행 범위는 확장할 수 없음')
    moved=[number+delta if number>cut else number for number in numbers]
    if len(numbers)==2 and expand and numbers[0]<=cut==numbers[1]: moved[1]+=delta
    if len(numbers)==2 and numbers[1]==MAX_ROW: moved[1]=MAX_ROW
    def render(match,number):
        if not 1<=number<=MAX_ROW: raise TemplateError('행 확장 후 Excel 최대 행을 넘음')
        return match[1]+match[2]+match[3]+str(number) if cells else match[1]+str(number)
    output=[render(match,number) for match,number in zip(matches,moved)]
    if len(output)==1 and single_expand and numbers[0]==cut:
        output.append(render(matches[0],cut+delta))
    return ':'.join(output)


def _formula(value,context,selected,cut,delta,table_names):
    """문자열·이름을 보존하며 RANGE 토큰의 명시적 A1 참조만 이동함."""
    if not value: return value
    leading=value.startswith('=')
    rendered=[]
    for token,original in _tokens(value):
        text=original
        if token.type=='FUNC' and token.subtype=='OPEN':
            function=text[:-1].upper()
            if function.startswith(('_XLFN.','_XLWS.')) or function in UNSAFE_FUNCTIONS:
                raise TemplateError('동적·간접 참조 수식은 행 확장을 보류함: '+function)
        if token.type=='OPERAND' and token.subtype=='ERROR':
            raise TemplateError('오류 참조가 있는 원본 수식은 확장할 수 없음')
        if token.type=='OPERAND' and token.subtype=='RANGE':
            if '[' in text or ']' in text:
                if not (any(text.startswith(name+'[') for name in table_names) or (table_names and text.startswith('[') and text.endswith(']'))) or '!' in text:
                    raise TemplateError('외부 또는 알 수 없는 구조화 참조임: '+text)
            else:
                qualifier,address=text.rsplit('!',1) if '!' in text else ('',text)
                sheet=qualifier[1:-1].replace("''", "'") if qualifier.startswith("'") and qualifier.endswith("'") else qualifier
                if ':' in sheet or '[' in sheet: raise TemplateError('3D 또는 외부 시트 참조임')
                if (CELL.fullmatch(address) or ROW.fullmatch(address) or ':' in address):
                    if not qualifier and context is None:
                        raise TemplateError('전역 정의 이름의 비한정 참조 시트를 확인할 수 없음')
                    if (sheet if qualifier else context).casefold()==selected.casefold():
                        address=_range(address,cut,delta)
                    else:
                        _range(address,cut,0)
                    text=(qualifier+'!' if qualifier else '')+address
        rendered.append(text)
    return ('=' if leading else '')+''.join(rendered)


def _tokens(value):
    try: tokens=Tokenizer(value if value.startswith('=') else '='+value).items
    except Exception as exc: raise TemplateError('해석할 수 없는 Excel 수식임: '+value) from exc
    body=value[1:] if value.startswith('=') else value
    cursor=0
    for token in tokens:
        if token.type=='WHITE-SPACE':
            match=re.match(r'\s+',body[cursor:])
            if match is None: raise TemplateError('수식의 공백 위치를 보존할 수 없음')
            original=match[0]
        else:
            original=body[cursor:cursor+len(token.value)]
            if original!=token.value: raise TemplateError('수식 토큰의 원문 위치를 보존할 수 없음')
        cursor+=len(original)
        yield token,original
    if cursor!=len(body): raise TemplateError('수식 원문 일부를 해석하지 못함')


def _copy_formula(value,origin,destination):
    output=[]
    for token,original in _tokens(value):
        if token.type=='OPERAND' and token.subtype=='RANGE':
            try: original=Translator('='+token.value,origin=origin).translate_formula(destination)[1:]
            except Exception as exc: raise TemplateError('복제 수식의 상대 주소를 안전하게 이동할 수 없음') from exc
            address=original.rsplit('!',1)[-1]
            if CELL.fullmatch(address) or ROW.fullmatch(address) or ':' in address and '[' not in address:
                _range(address,1,0)
        output.append(original)
    return ('=' if value.startswith('=') else '')+''.join(output)


def _global_reason(parts,workbook,sheets,tables):
    if any(name.startswith('_xmlsignatures/') for name in parts): return '전자 서명된 XLSX는 확장하지 않음'
    if any('vbaProject' in name for name in parts): return '매크로의 주소 의존성은 확장 미지원'
    protections=workbook.findall(f'{{{S}}}workbookProtection')
    if len(protections)>1: return '통합문서 보호 정의가 중복되어 확장을 보류함'
    protection=protections[0] if protections else None
    if protection is not None and (any(protection.get(key) in {'1','true','on'} for key in ('lockStructure','lockWindows','lockRevision'))
                                  or any(value and ('password' in key.lower() or 'hash' in key.lower()) for key,value in protection.attrib.items())):
        return '통합문서 보호를 유지하며 확장을 보류함'
    if any(name.startswith(('xl/externalLinks/','xl/pivotTables/','xl/pivotCache/','xl/queryTables/')) or name=='xl/metadata.xml' for name in parts):
        return '외부·피벗·쿼리·동적 배열 의존성은 행 확장 미지원'
    if workbook.find(f'{{{S}}}externalReferences') is not None: return '외부 통합문서 참조를 유지하며 확장을 보류함'
    calc=workbook.find(f'{{{S}}}calcPr')
    if calc is not None and (calc.get('refMode')=='R1C1' or calc.get('iterate') in {'1','true'}): return 'R1C1 또는 반복 계산의 의미를 유지할 수 없음'
    table_names={root.get('name') for root in tables.values()}
    try:
        for sheet in sheets:
            for formula in sheet['root'].findall(f'.//{{{S}}}f'):
                if formula.get('t','normal')!='normal' or set(formula.attrib)-{'t','ca'}:
                    return '배열·공유·데이터 테이블 또는 확장 수식 구조는 미지원'
                _formula(formula.text,sheet['name'],sheet['name'],1,0,table_names)
            if sheet['root'].xpath('.//s:c[@cm or @vm]',namespaces=NS): return '셀 metadata에 연결된 동적 입력 구조는 미지원'
    except TemplateError as exc: return str(exc)
    return ''


def _row_reason(parts,sheet,row,shared,tables,blocked):
    if blocked: return blocked
    root=sheet['root']; number=int(row.get('r'))
    if any(node.tag!=f'{{{S}}}c' for node in row) or any(etree.QName(key).localname.lower().endswith('id') for node in row.iter() for key in node.attrib):
        return '참조 식별자 또는 알 수 없는 반복 행 구조는 미지원'
    if root.find(f'{{{S}}}sheetProtection') is not None: return '시트 보호를 유지하며 행 확장을 보류함'
    extension=root.find(f'{{{S}}}extLst')
    if extension is not None:
        for item in extension:
            if len(item)!=1 or item[0].tag!=f'{{{X14}}}dataValidations': return '알 수 없는 시트 확장 metadata의 주소 의존성은 미지원'
            for validation in item[0]:
                if validation.tag!=f'{{{X14}}}dataValidation' or any(node.tag not in {f'{{{X14}}}formula1',f'{{{X14}}}formula2',f'{{{XM}}}sqref'} for node in validation):
                    return '알 수 없는 x14 입력 제한 구조는 미지원'
    for drawing in root.findall(f'{{{S}}}legacyDrawing'):
        target=_relations(parts,sheet['part']).get(drawing.get(f'{{{R}}}id'))
        if target not in parts: return '레거시 그림 관계를 확인할 수 없음'
        notes=_xml(parts[target]).xpath('.//*[local-name()="ClientData"]')
        if not notes: return 'VML 개체의 댓글 위치를 확인할 수 없음'
        for note in notes:
            anchors=note.xpath('./*[local-name()="Anchor"]/text()')
            rows=note.xpath('./*[local-name()="Row"]/text()')
            if note.get('ObjectType')!='Note' or len(anchors)!=1 or len(rows)!=1: return '댓글 이외 VML 개체는 확장 미지원'
            try:
                coordinates=[int(item.strip()) for item in anchors[0].split(',')]
                attached=int(rows[0])
            except ValueError: return 'VML 댓글의 위치를 확인할 수 없음'
            if len(coordinates)!=8 or max(coordinates[2],coordinates[6],attached)>=number-1:
                return '복제·이동해야 하는 VML 댓글은 확장 미지원'
    for name in _relations(parts,sheet['part']).values():
        if name in parts and re.fullmatch(r'xl/(?:comments/)?comment[s]?\d+\.xml',name):
            if any(int(_cell(node.get('ref'))[4])>=number for node in _xml(parts[name]).xpath('.//*[local-name()="comment"]')):
                return '복제·이동해야 하는 댓글 주소는 확장 미지원'
    for merge in root.findall(f'{{{S}}}mergeCells/{{{S}}}mergeCell'):
        a,b=merge.get('ref').split(':'); lo,hi=int(_cell(a)[4]),int(_cell(b)[4])
        if lo!=hi and lo<=number<=hi: return '세로 병합 또는 병합 경계 행임'
    for reference in root.findall(f'{{{S}}}tableParts/{{{S}}}tablePart'):
        table=tables.get(_relations(parts,sheet['part']).get(reference.get(f'{{{R}}}id')))
        if table is None: return '표 관계를 확인할 수 없음'
        a,b=table.get('ref').split(':'); lo,hi=int(_cell(a)[4]),int(_cell(b)[4])
        if int(table.get('headerRowCount','1')) and number==lo: return '원본 Excel 표 머리글 행임'
        if int(table.get('totalsRowCount','0')) and number==hi: return '원본 Excel 표 합계 행임'
    cells=row.findall(f'{{{S}}}c'); values=[_text(cell,shared) for cell in cells]
    if any(re.match(r'^(?:합계|총계|소계|total|subtotal)(?:\s|[:：]|$)',value.strip(),re.I) for value in values): return '합계·소계 행은 반복 입력 행이 아님'
    if cells and cells[0].get('t') not in {'s','inlineStr','str'} and re.fullmatch(r'[0-9]+',values[0] or ''):
        return '고정된 숫자·순번 입력 행을 자동 복제하지 않음'
    rels=_relations(parts,sheet['part'])
    for drawing in root.findall(f'{{{S}}}drawing'):
        part=rels.get(drawing.get(f'{{{R}}}id'))
        if part not in parts: return '그림 관계를 확인할 수 없음'
        for anchor in _xml(parts[part]):
            points=[int(node.text) for node in anchor.xpath('./*[local-name()="from" or local-name()="to"]/*[local-name()="row"]')]
            if points and min(points)<=number-1<=max(points): return '그림·차트가 원본 반복 행을 가로지름'
            kind=etree.QName(anchor).localname
            if kind=='oneCellAnchor' and points and points[0]<number-1:
                height=anchor.xpath('./*[local-name()="ext"]/@cy')
                offset=anchor.xpath('./*[local-name()="from"]/*[local-name()="rowOff"]/text()')
                if not _above_fits(root,number,points[0],height,offset): return '위쪽 그림의 높이가 반복 행 위에 들어가는지 확인할 수 없음'
            elif kind=='absoluteAnchor':
                height=anchor.xpath('./*[local-name()="ext"]/@cy')
                offset=anchor.xpath('./*[local-name()="pos"]/@y')
                if not _above_fits(root,number,0,height,offset): return '절대 위치 그림이 반복 행 위에 들어가는지 확인할 수 없음'
            elif kind not in {'oneCellAnchor','twoCellAnchor'}:
                return '알 수 없는 그림 앵커의 행 의존성은 미지원'
    if row.find(f'{{{S}}}extLst') is not None: return '행 확장 metadata는 미지원'
    if not cells: return ''
    editable=any(not value.strip() or PLACEHOLDER.search(value) for value in values) or any(cell.find(f'{{{S}}}f') is not None for cell in cells)
    return '' if editable else '빈 칸·자리표시자·행 계산 입력이 없는 행임'


def _above_fits(root,cut,start,heights,offsets):
    """명시 행 높이의 안전한 하한으로만 위쪽 개체의 끝 위치를 검사함."""
    if len(heights)!=1 or len(offsets)!=1 or not heights[0].isdigit() or not offsets[0].isdigit(): return False
    format_node=root.find(f'{{{S}}}sheetFormatPr')
    if format_node is None or format_node.get('defaultRowHeight') is None: return False
    try:
        default=Decimal(format_node.get('defaultRowHeight'))
        if not default.is_finite() or default<=0: return False
        distance=default*(cut-1-start)
        for row in root.findall(f'{{{S}}}sheetData/{{{S}}}row'):
            index=int(row.get('r'))-1
            if start<=index<cut-1:
                actual=Decimal(row.get('ht',str(default)))
                if not actual.is_finite() or actual<0: return False
                if row.get('hidden') in {'1','true','on'}: actual=Decimal(0)
                distance+=actual-default
        return Decimal(heights[0])+Decimal(offsets[0])<=distance*12700
    except (InvalidOperation,ValueError,TypeError): return False


def inventory(parts):
    workbook,sheets,shared,tables=_package(parts)
    blocked=_global_reason(parts,workbook,sheets,tables)
    output=[]
    for sheet in sheets:
        rows=sheet['root'].findall(f'{{{S}}}sheetData/{{{S}}}row')
        information=[]; previous=0
        for row in rows:
            number=int(_row(row.get('r',''))[2])
            if number<=previous: raise TemplateError('XLSX 행 주소가 중복되거나 순서가 다름')
            previous=number; columns=[]; prior_column=0
            for cell in row.findall(f'{{{S}}}c'):
                match=_cell(cell.get('r')); column=column_index_from_string(match[2])
                if match[1] or match[3] or int(match[4])!=number or column<=prior_column: raise TemplateError('XLSX 셀과 행 주소가 다르거나 중복됨')
                prior_column=column; columns.append(_text(cell,shared))
            reason=_row_reason(parts,sheet,row,shared,tables,blocked)
            information.append({'index':number,'editable':not bool(reason),'reason':reason or '입력·행 계산 후보; 업무 역할은 사용자 확인 필요','columns':columns})
        output.append({'id':'xlsx:'+sheet['part'],'label':sheet['name'],'part':sheet['part'],
                       'row_count':len(rows),'max_row':previous,'rows':information})
    return output


def transform(parts,plan):
    if (not isinstance(plan,dict) or set(plan)!={'table_id','row','count'} or not isinstance(plan['table_id'],str)
            or type(plan['row']) is not int or type(plan['count']) is not int or plan['row']<1 or not 1<=plan['count']<=200):
        raise TemplateError('XLSX 반복 계획은 table_id, 실제 행 번호, count(1..200)이어야 함')
    record=next((item for item in inventory(parts) if item['id']==plan['table_id']),None)
    status=next((item for item in record['rows'] if item['index']==plan['row']),None) if record else None
    if status is None: raise TemplateError('원본에 없는 XLSX 시트 또는 XML 행임')
    if not status['editable']: raise TemplateError(status['reason'])
    if plan['count']==1: return {}
    workbook,sheets,shared,tables=_package(parts)
    selected=next(sheet for sheet in sheets if sheet['part']==record['part'])
    cut,delta=plan['row'],plan['count']-1
    roots={sheet['part']:sheet['root'] for sheet in sheets}
    roots['xl/workbook.xml']=workbook
    table_names={root.get('name') for root in tables.values()}
    for sheet in sheets:
        root=sheet['root']
        protected=root.find(f'{{{S}}}sheetProtection') is not None
        for formula in root.findall(f'.//{{{S}}}f'):
            changed=_formula(formula.text,sheet['name'],selected['name'],cut,delta,table_names)
            if protected and changed!=formula.text:
                raise TemplateError('참조가 바뀌는 다른 시트의 보호를 유지하며 확장을 보류함')
            formula.text=changed
            if not protected:
                for value in formula.getparent().findall(f'{{{S}}}v'): formula.getparent().remove(value)
        for formula in root.xpath('.//s:dataValidation/s:formula1 | .//s:dataValidation/s:formula2 | .//s:cfRule/s:formula | .//x14:dataValidation/x14:formula1/xm:f | .//x14:dataValidation/x14:formula2/xm:f',namespaces=NS):
            formula.text=_formula(formula.text,sheet['name'],selected['name'],cut,delta,table_names)
        if sheet is not selected:
            for node in root.findall(f'{{{S}}}hyperlinks/{{{S}}}hyperlink'):
                if node.get('location'):
                    node.set('location',_formula(node.get('location'),sheet['name'],selected['name'],cut,delta,table_names))
        if sheet is not selected and root.find(f'{{{S}}}sheetProtection') is not None and _serial(root)!=_serial(_xml(parts[sheet['part']])):
            raise TemplateError('참조가 바뀌는 다른 시트의 보호를 유지하며 확장을 보류함')
    root=selected['root']; data=root.find(f'{{{S}}}sheetData')
    prototype=next(row for row in data if row.get('r')==str(cut))
    for row in data:
        number=int(row.get('r'))
        if number>cut:
            if number+delta>MAX_ROW: raise TemplateError('행 확장 후 Excel 최대 행을 넘음')
            row.set('r',str(number+delta))
        for cell in row.findall(f'{{{S}}}c'):
            cell.set('r',_range(cell.get('r'),cut,delta,expand=False))
    index=data.index(prototype)
    for offset in range(1,plan['count']):
        cloned=deepcopy(prototype); cloned.set('r',str(cut+offset))
        for cell in cloned.findall(f'{{{S}}}c'):
            origin=cell.get('r'); destination=_range(origin,cut-1,offset,expand=False)
            cell.set('r',destination)
            formula=cell.find(f'{{{S}}}f')
            if formula is not None:
                formula.text=_copy_formula(formula.text,origin,destination)
        data.insert(index+offset,cloned)
    for node in root.xpath('./s:dimension | ./s:autoFilter | .//s:sortState | .//s:sortCondition | .//s:protectedRange',namespaces=NS):
        if node.get('ref'): node.set('ref',_range(node.get('ref'),cut,delta,single_expand=True))
    for node in root.xpath('./s:dataValidations/s:dataValidation | ./s:conditionalFormatting | .//s:ignoredError',namespaces=NS):
        node.set('sqref',' '.join(_range(value,cut,delta,single_expand=True) for value in node.get('sqref','').split()))
    for node in root.xpath('.//x14:dataValidation/xm:sqref',namespaces=NS):
        node.text=' '.join(_range(value,cut,delta,single_expand=True) for value in (node.text or '').split())
    merges=root.find(f'{{{S}}}mergeCells')
    if merges is not None:
        added=[]
        for merge in merges:
            old=merge.get('ref'); merge.set('ref',_range(old,cut,delta,expand=False))
            endpoints=old.split(':')
            if len(endpoints)==2 and all(int(_cell(value)[4])==cut for value in endpoints):
                for offset in range(1,plan['count']):
                    new=deepcopy(merge); new.set('ref',_range(old,cut-1,offset,expand=False)); added.append(new)
        merges.extend(added); merges.set('count',str(len(merges)))
    hyperlinks=[]
    for node in root.xpath('.//s:pane | .//s:sheetView | .//s:selection | ./s:hyperlinks/s:hyperlink',namespaces=NS):
        original_ref=node.get('ref')
        for attribute in ('topLeftCell','activeCell','ref'):
            if node.get(attribute): node.set(attribute,_range(node.get(attribute),cut,delta,expand=False))
        if node.get('sqref'): node.set('sqref',' '.join(_range(value,cut,delta,expand=False) for value in node.get('sqref').split()))
        if node.get('location'): node.set('location',_formula(node.get('location'),selected['name'],selected['name'],cut,delta,table_names))
        if node.tag==f'{{{S}}}hyperlink' and original_ref and all(int(_cell(value)[4])==cut for value in original_ref.split(':')):
            for offset in range(1,plan['count']):
                copied=deepcopy(node); copied.set('ref',_range(original_ref,cut-1,offset,expand=False)); hyperlinks.append(copied)
        if node.tag==f'{{{S}}}pane' and node.get('state') in {'frozen','frozenSplit'} and node.get('ySplit'):
            split=float(node.get('ySplit'))
            if split>cut: node.set('ySplit',str(int(split)+delta))
    if hyperlinks: root.find(f'{{{S}}}hyperlinks').extend(hyperlinks)
    for node in root.findall(f'{{{S}}}rowBreaks/{{{S}}}brk'):
        if int(node.get('id'))>cut: node.set('id',str(int(node.get('id'))+delta))
    selected_rels=_relations(parts,selected['part'])
    for ref in root.findall(f'{{{S}}}tableParts/{{{S}}}tablePart'):
        name=selected_rels[ref.get(f'{{{R}}}id')]; table=tables[name]
        for node in [table,*table.xpath('.//s:autoFilter | .//s:sortState | .//s:sortCondition',namespaces=NS)]:
            if node.get('ref'): node.set('ref',_range(node.get('ref'),cut,delta,single_expand=True))
        roots[name]=table
    for sheet in sheets:
        owners=_relations(parts,sheet['part'])
        for ref in sheet['root'].findall(f'{{{S}}}tableParts/{{{S}}}tablePart'):
            name=owners.get(ref.get(f'{{{R}}}id')); table=tables.get(name)
            if table is None: raise TemplateError('계산 열의 원본 표 관계를 찾지 못함')
            for formula in table.xpath('.//s:calculatedColumnFormula | .//s:totalsRowFormula',namespaces=NS):
                if formula.get('array') in {'1','true'}: raise TemplateError('표의 배열 계산 열은 확장 미지원')
                updated=_formula(formula.text,sheet['name'],selected['name'],cut,delta,table_names)
                if sheet['root'].find(f'{{{S}}}sheetProtection') is not None and updated!=formula.text:
                    raise TemplateError('다른 보호 시트의 표 계산 열을 수정할 수 없음')
                formula.text=updated
            roots[name]=table
    for node in workbook.findall(f'{{{S}}}definedNames/{{{S}}}definedName'):
        scope=node.get('localSheetId')
        context=sheets[int(scope)]['name'] if scope is not None and 0<=int(scope)<len(sheets) else None
        node.text=_formula(node.text,context,selected['name'],cut,delta,table_names)
    for drawing in root.findall(f'{{{S}}}drawing'):
        name=selected_rels[drawing.get(f'{{{R}}}id')]; drawing_root=_xml(parts[name])
        for node in drawing_root.xpath('./*/*[local-name()="from" or local-name()="to"]/*[local-name()="row"]'):
            if int(node.text)>=cut:
                if int(node.text)+delta>=MAX_ROW: raise TemplateError('그림 위치가 Excel 최대 행을 넘음')
                node.text=str(int(node.text)+delta)
        roots[name]=drawing_root
    for name,content in parts.items():
        if re.fullmatch(r'xl/charts/chart\d+\.xml',name):
            chart=_xml(content); changed=False
            for formula in chart.xpath('.//*[local-name()="f"]'):
                value=_formula(formula.text,None,selected['name'],cut,delta,table_names)
                if value!=formula.text: formula.text=value; changed=True
            if changed:
                for cache in chart.xpath('.//*[local-name()="numCache" or local-name()="strCache"]'): cache.getparent().remove(cache)
                roots[name]=chart
    calc=workbook.find(f'{{{S}}}calcPr')
    if calc is None:
        calc=etree.Element(f'{{{S}}}calcPr')
        after={'oleSize','customWorkbookViews','pivotCaches','smartTagPr','smartTagTypes','webPublishing','fileRecoveryPr','webPublishObjects','extLst'}
        position=next((index for index,node in enumerate(workbook) if etree.QName(node).localname in after),len(workbook))
        workbook.insert(position,calc)
    calc.set('fullCalcOnLoad','1'); calc.set('forceFullCalc','1')
    changes={name:_serial(xml) for name,xml in roots.items() if etree.tostring(xml,method='c14n')!=etree.tostring(_xml(parts[name]),method='c14n')}
    chains=[name for name in parts if re.fullmatch(r'xl/calcChain\d*\.xml',name)]
    for name in chains: changes[name]=None
    if chains:
        for name in ('xl/_rels/workbook.xml.rels','[Content_Types].xml'):
            xml=_xml(parts[name])
            for node in list(xml):
                related=_target('xl/workbook.xml',node.get('Target','')) if name.endswith('.rels') else node.get('PartName','').lstrip('/')
                if related in chains:
                    if name.endswith('.rels') and not node.get('Type','').endswith('/calcChain'):
                        raise TemplateError('계산 체인으로 위장한 다른 관계를 삭제할 수 없음')
                    if not name.endswith('.rels') and node.get('ContentType')!='application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml':
                        raise TemplateError('계산 체인으로 위장한 다른 부품을 삭제할 수 없음')
                    xml.remove(node)
            if etree.tostring(xml,method='c14n')!=etree.tostring(_xml(parts[name]),method='c14n'): changes[name]=_serial(xml)
    return changes
