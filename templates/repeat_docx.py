"""입력용 Word 표 행만 원본 XML 그대로 반복함. 파일 입출력은 호출자가 맡음."""

from copy import deepcopy
import re

from lxml import etree

from parsers.extract import _xml
from .docx_choices import native_choice
from .fill import PLACEHOLDER, TemplateError, WORD_NS


W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
NS = {"w": WORD_NS, "w14": W14}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _text(node):
    return "".join("\n" if etree.QName(item).localname in {"br", "cr"} else item.text or ""
                   for item in node.iter() if isinstance(item.tag, str)
                   and item.tag in {f"{{{WORD_NS}}}t", f"{{{WORD_NS}}}br", f"{{{WORD_NS}}}cr"})


def _roots(parts):
    if not isinstance(parts, dict) or "word/document.xml" not in parts:
        raise TemplateError("DOCX 본문 부품이 없음")
    return {name: _xml(data) for name, data in parts.items()
            if name.startswith("word/") and name.endswith(".xml")}


def _protected(parts, roots):
    if any(name.startswith("_xmlsignatures/") for name in parts):
        return "전자 서명된 패키지는 행을 확장할 수 없음"
    settings = roots.get("word/settings.xml")
    if settings is not None and settings.xpath(
            ".//w:documentProtection[@w:enforcement='1' or @w:enforcement='true' or @w:enforcement='on'] | .//w:writeProtection", namespaces=NS):
        return "문서 보호를 유지하며 행 확장을 보류함"
    return ""


def _reason(row, following, table, blocked):
    if blocked:
        return blocked
    if table.xpath("ancestor::w:sdt/w:sdtPr/w:lock | ancestor::w:sdt/w:sdtPr/w:dataBinding", namespaces=NS):
        return "잠금 또는 XML 연결된 표임"
    if row.xpath("./w:trPr/w:tblHeader", namespaces=NS):
        return "원본 표 머리글 행임"
    if row.xpath(".//w:vMerge", namespaces=NS) or (following is not None and following.xpath(
            ".//w:vMerge[not(@w:val) or @w:val='continue']", namespaces=NS)):
        return "세로 병합 행 또는 병합 경계임"
    forbidden = {"fldChar", "instrText", "fldSimple", "bookmarkStart", "bookmarkEnd", "commentRangeStart",
                 "commentRangeEnd", "commentReference", "footnoteReference", "endnoteReference", "drawing", "pict",
                 "object", "altChunk", "hyperlink", "customXml", "permStart", "permEnd", "ins", "del", "moveFrom",
                 "moveTo", "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd"}
    if any(etree.QName(item).localname in forbidden or any(etree.QName(key).namespace == REL for key in item.attrib)
           for item in row.iter() if isinstance(item.tag, str)):
        return "복제할 수 없는 참조·필드·그림·변경 기록 구조가 있음"
    if row.xpath(".//w:tbl", namespaces=NS):
        return "중첩 표가 있는 행은 개별 내부 표를 선택해야 함"
    if row.xpath(".//w:sdtPr/w:lock | .//w:sdtPr/w:dataBinding | .//w:sdtPr/w:date | .//w:sdtPr/w:picture | .//w:sdtPr/w:group | .//w:sdtPr/*[local-name()='repeatingSection' or local-name()='repeatingSectionItem']", namespaces=NS):
        return "잠금·연결·미지원 구조화 입력 컨트롤이 있음"
    for item in row.iter():
        if not isinstance(item.tag, str):
            continue
        for key, value in item.attrib.items():
            local = etree.QName(key).localname
            if local.lower().endswith("id") and key not in {f"{{{W14}}}paraId", f"{{{W14}}}textId", f"{{{WORD_NS}}}rsidR", f"{{{WORD_NS}}}rsidRPr", f"{{{WORD_NS}}}rsidDel", f"{{{WORD_NS}}}rsidP", f"{{{WORD_NS}}}rsidTr"}:
                return "알 수 없는 행별 식별자 속성이 있음"
            if key in {f"{{{W14}}}paraId", f"{{{W14}}}textId"} and not re.fullmatch(r"[0-9A-Fa-f]{8}", value):
                return "문단 식별자 형식이 올바르지 않음"
    for identifier in row.xpath(".//w:sdtPr/w:id", namespaces=NS):
        if not re.fullmatch(r"-?\d+", identifier.get(f"{{{WORD_NS}}}val", "")):
            return "구조화 입력 ID 형식이 올바르지 않음"
    for sdt in row.xpath(".//w:sdt", namespaces=NS):
        props, content = sdt.find(f"{{{WORD_NS}}}sdtPr"), sdt.find(f"{{{WORD_NS}}}sdtContent")
        if props is None or content is None:
            return "구조화 입력의 속성 또는 본문이 없음"
        if props.xpath("./w:dropDownList | ./w:comboBox", namespaces=NS):
            try:
                native_choice(props, content)
            except TemplateError as exc:
                return str(exc)
        checked = props.xpath("./w14:checkbox/w14:checked/@w14:val", namespaces=NS)
        if props.find(f"{{{W14}}}checkbox") is not None and (len(checked) != 1 or checked[0] not in {"0", "false", "off"}):
            return "체크 상태가 미선택으로 확인되지 않음"
        if props.find(f"{{{W14}}}checkbox") is None and _text(content).strip() and not PLACEHOLDER.search(_text(content)) and props.find(f"{{{WORD_NS}}}showingPlcHdr") is None:
            return "이미 입력한 구조화 항목을 반복할 수 없음"
    columns = [_text(cell).strip() for cell in row.findall(f"{{{WORD_NS}}}tc")]
    if any(re.match(r"^(?:합계|총계|소계|총\s*합계|total|subtotal)(?:\s|[:：]|$)", text, re.I) for text in columns):
        return "합계·소계 행은 반복 입력 행이 아님"
    if columns and re.fullmatch(r"\d+[.)]?", columns[0]):
        return "원본의 고정 순번을 자동으로 반복하지 않음"
    editable = any(not text or PLACEHOLDER.search(text) for text in columns) or bool(row.xpath(".//w:sdtPr/w:showingPlcHdr | .//w:sdtPr/w14:checkbox", namespaces=NS))
    return "" if editable else "빈 칸 또는 명시적 자리표시자가 없는 행임"


def inventory(parts):
    """직접 표 행의 원문과 확장 가능 여부를 반환함. row index는 1부터임."""
    roots = _roots(parts)
    blocked = _protected(parts, roots)
    records = []
    for part, root in roots.items():
        for table in root.xpath(".//w:tbl", namespaces=NS):
            path = root.getroottree().getpath(table)
            rows = table.findall(f"{{{WORD_NS}}}tr")
            wrapped = any(item.tag not in {f"{{{WORD_NS}}}tblPr", f"{{{WORD_NS}}}tblGrid", f"{{{WORD_NS}}}tr"} for item in table if isinstance(item.tag, str))
            information = []
            for index, row in enumerate(rows):
                reason = "직접 행 이외의 표 구조가 있음" if wrapped else _reason(row, rows[index + 1] if index + 1 < len(rows) else None, table, blocked)
                information.append({"index": index + 1, "editable": not bool(reason), "reason": reason or "빈 입력칸·자리표시자 행; 업무 역할은 사용자 확인 필요",
                                    "columns": [_text(cell) for cell in row.findall(f"{{{WORD_NS}}}tc")]})
            label = table.xpath("./w:tblPr/w:tblCaption/@w:val", namespaces=NS)
            records.append({"id": f"docx:{part}:{path}", "part": part, "xpath": path,
                            "label": label[0] if label else " / ".join(information[0]['columns'])[:120] if information else "빈 표",
                            "row_count": len(rows), "rows": information})
    return records


def transform(parts, plan):
    """원본 행을 포함해 count개가 되도록 복제하고 변경된 부품만 반환함."""
    if not isinstance(plan, dict) or set(plan) != {"table_id", "row", "count"} or not isinstance(plan['table_id'], str) or type(plan['row']) is not int or type(plan['count']) is not int or not 1 <= plan['count'] <= 200:
        raise TemplateError("행 확장 계획은 table_id, 1-based row, count(1..200)이어야 함")
    records = [item for item in inventory(parts) if item['id'] == plan['table_id']]
    if len(records) != 1 or not 1 <= plan['row'] <= records[0]['row_count']:
        raise TemplateError("원본에 없는 표 또는 행을 지정함")
    record = records[0]
    status = record['rows'][plan['row'] - 1]
    if not status['editable']:
        raise TemplateError(status['reason'])
    if plan['count'] == 1:
        return {}
    roots = _roots(parts)
    root = roots[record['part']]
    table = next(item for item in root.xpath(".//w:tbl", namespaces=NS) if root.getroottree().getpath(item) == record['xpath'])
    row = table.findall(f"{{{WORD_NS}}}tr")[plan['row'] - 1]
    used = {"sdt": set(), "hex": set()}
    for node in roots.values():
        used['sdt'].update(int(value) for value in node.xpath(".//w:sdtPr/w:id/@w:val", namespaces=NS) if re.fullmatch(r"[+-]?\d+", value.strip()))
        used['hex'].update(int(value, 16) for value in node.xpath("//@w14:paraId | //@w14:textId", namespaces=NS) if re.fullmatch(r"[0-9A-Fa-f]{8}", value.strip()))
    def fresh(group):
        number = 1
        while number in used[group]:
            number += 1
        if number >= 0x80000000:
            raise TemplateError("행별 고유 식별자를 만들 수 없음")
        used[group].add(number)
        return str(number) if group == 'sdt' else f"{number:08X}"
    position = table.index(row)
    for offset in range(1, plan['count']):
        cloned = deepcopy(row)
        for item in cloned.xpath(".//w:sdtPr/w:id", namespaces=NS):
            item.set(f"{{{WORD_NS}}}val", fresh('sdt'))
        for item in cloned.iter():
            for attribute in (f"{{{W14}}}paraId", f"{{{W14}}}textId"):
                if attribute in item.attrib:
                    item.set(attribute, fresh('hex'))
        table.insert(position + offset, cloned)
    return {record['part']: etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)}
