"""Repeat a plain HWPX input row without rebuilding its styles or package."""

from copy import deepcopy
import re

from lxml import etree

from parsers.extract import MAX_PACKAGE_BYTES, _hwpx_text, _xml
from templates.fill import PLACEHOLDER


HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
NS = {"hp": HP}
UINT_MAX = 2**32 - 1
_PLAIN_TAGS = {
    "tr", "tc", "subList", "p", "run", "t", "lineBreak", "tab", "nbSpace",
    "fwSpace", "hyphen", "cellAddr", "cellSpan", "cellSz", "cellMargin",
    "linesegarray", "lineseg",
}
_STYLE_REFS = {"paraPrIDRef", "styleIDRef", "charPrIDRef", "borderFillIDRef"}


def _uint(value, *, positive=False) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise ValueError("HWPX 행/주소/크기/ID는 명시적 정수이어야 함")
    number = int(value)
    if number > UINT_MAX or (positive and number == 0):
        raise ValueError("HWPX 행/주소/크기/ID 범위를 벗어남")
    return number


def _active(value) -> bool:
    return value is not None and value.lower() not in {"", "0", "false", "none"}


def _roots(parts: dict[str, bytes]) -> tuple[dict, str]:
    if not isinstance(parts, dict) or any(not isinstance(k, str) or not isinstance(v, bytes)
                                          for k, v in parts.items()):
        raise ValueError("HWPX 부품은 dict[str, bytes]이어야 함")
    if sum(map(len, parts.values())) > MAX_PACKAGE_BYTES:
        raise ValueError("HWPX 압축 해제 크기는 100 MiB 이하이어야 함")
    try:
        roots = {name: _xml(data) for name, data in parts.items() if name.endswith(".xml")}
    except etree.XMLSyntaxError as exc:
        raise ValueError("HWPX XML 부품을 읽을 수 없음") from exc
    blocked = ""
    if any(re.search(r"(?:^|/)(?:encrypt(?:ion)?|signatures?[0-9]*|distribut(?:ion)?|drm)\.xml$", name, re.I) for name in parts):
        blocked = "암호·배포용·서명 보호 패키지는 반복 행 확장 미지원"
    for root in roots.values():
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            local = etree.QName(node).localname.lower()
            if local in {"encryption-data", "encrypteddata", "signature", "docsecurity",
                         "documentprotection", "trackchangeprotect"}:
                blocked = "문서 보안·암호·서명 보호는 해제하지 않음"
    return roots, blocked


def _sections(roots):
    return [(name, root) for name, root in sorted(roots.items())
            if re.fullmatch(r"Contents/section[0-9]+\.xml", name)]


def _geometry(table):
    rows = table.findall("hp:tr", NS)
    row_count = _uint(table.get("rowCnt"), positive=True)
    col_count = _uint(table.get("colCnt"), positive=True)
    if row_count != len(rows) or row_count * col_count > 200_000:
        raise ValueError("표의 rowCnt/실제 행수 불일치 또는 안전한 격자 범위 초과")
    covered = set()
    spans = []
    for index, row in enumerate(rows):
        previous = -1
        for cell in row.findall("hp:tc", NS):
            addr, span, size = (cell.find("hp:" + name, NS)
                                for name in ("cellAddr", "cellSpan", "cellSz"))
            if any(element is None for element in (addr, span, size)):
                raise ValueError("셀 주소·병합·크기 정보가 누락됨")
            y, x = _uint(addr.get("rowAddr")), _uint(addr.get("colAddr"))
            height, width = _uint(span.get("rowSpan"), positive=True), _uint(span.get("colSpan"), positive=True)
            _uint(size.get("width"), positive=True)
            _uint(size.get("height"))
            if y != index or x <= previous or y + height > row_count or x + width > col_count:
                raise ValueError("셀 주소/병합/순서가 실제 표 격자와 불일치함")
            previous = x
            occupied = {(yy, xx) for yy in range(y, y + height) for xx in range(x, x + width)}
            if covered.intersection(occupied):
                raise ValueError("표의 셀 또는 병합 영역이 겹침")
            covered.update(occupied)
            spans.append((y, height))
    if len(covered) != row_count * col_count:
        raise ValueError("표 격자에 주소가 없는 빈 영역이 있음")
    return rows, spans


def _row_height(table, row) -> int:
    """Use source sizes/cached local lines only; never estimate text or fonts."""
    heights = []
    for cell in row.findall("hp:tc", NS):
        size = cell.find("hp:cellSz", NS)
        height = _uint(size.get("height"))
        lines = cell.findall(".//hp:lineseg", NS)
        if lines:
            margin = cell.find("hp:cellMargin", NS) if _active(cell.get("hasMargin")) else table.find("hp:inMargin", NS)
            if margin is None:
                raise ValueError("원본 줄 배치의 셀 여백을 확인할 수 없음")
            padding = _uint(margin.get("top")) + _uint(margin.get("bottom"))
            content = max(_uint(line.get("vertpos")) + _uint(line.get("vertsize")) for line in lines)
            height = max(height, content + padding)
        heights.append(height)
    result = max(heights, default=0)
    if not result:
        raise ValueError("복제 행 높이가 0이며 원본 줄 배치 근거가 없음")
    return result


def _table_reason(table) -> str:
    if any(ancestor.tag == f"{{{HP}}}tbl" for ancestor in table.iterancestors()):
        return "중첩 표 확장은 상위 셀 높이/참조 갱신 미지원"
    anchor = next((node for node in table.iterancestors() if node.tag == f"{{{HP}}}p"), None)
    if anchor is None or anchor.getparent() is not table.getroottree().getroot():
        return "본문 구역 직속 문단의 표만 확장 지원"
    if _active(table.get("lock")) or any(_active(c.get("protect")) for c in table.findall("hp:tr/hp:tc", NS)):
        return "잠금/보호된 표 또는 셀은 확장하지 않음"
    if any(not isinstance(child.tag, str) or etree.QName(child).namespace != HP
           or etree.QName(child).localname not in {"sz", "pos", "outMargin", "inMargin", "tr"}
           for child in table):
        return "셀 구역·라벨·캡션·알 수 없는 표 구조의 주소/배치 갱신은 미지원"
    size = table.find("hp:sz", NS)
    if size is None or _active(size.get("protect")) or size.get("heightRelTo") != "ABSOLUTE":
        return "보호 또는 상대 높이의 표는 확장 미지원"
    try:
        _uint(size.get("height"), positive=True)
        _uint(table.get("cellSpacing", "0"))
        _geometry(table)
    except ValueError as exc:
        return str(exc)
    return ""


def _row_reason(table, rows, spans, index) -> str:
    row = rows[index]
    if any(start <= index < start + span for start, span in spans if span > 1):
        return "선택 행/삽입 경계를 지나는 세로 병합은 확장 미지원"
    for cell in row.findall("hp:tc", NS):
        if _active(cell.get("header")):
            return "원본 표머리 행은 복제하지 않음"
        if cell.get("name", ""):
            return "이름을 가진 셀의 외부 참조를 안전하게 복제할 수 없음"
        paragraphs = cell.findall("hp:subList/hp:p", NS)
        text = "\n".join(_hwpx_text(p) for p in paragraphs)
        remaining = PLACEHOLDER.sub("", text).strip()
        if remaining and not (PLACEHOLDER.search(text) and re.fullmatch(r"[\s()\[\]:/\-]*(?:원|천원|백만원|명|개|건|%|kg|mg|년|월|일)[\s()\[\]:/\-]*", remaining)):
            return "빈 입력칸/자리표시자 행만 복제하며 원본 문구·실적·합계는 유지함"
        for node in cell.iter():
            if not isinstance(node.tag, str) or etree.QName(node).namespace != HP or etree.QName(node).localname not in _PLAIN_TAGS:
                return "그림·중첩표·필드·제어·알 수 없는 참조 구조는 복제 미지원"
            local = etree.QName(node).localname
            for key, value in node.attrib.items():
                if key in {"paraTcId", "charTcId"} or (key.endswith("IDRef") and key not in _STYLE_REFS and value not in {"", "0"}):
                    return "연결/변경 추적 참조가 있는 입력행은 복제 미지원"
                if key == "id" and local not in {"p", "subList"}:
                    return "복제할 수 없는 객체 ID가 있음"
            if local in {"p", "subList"} and (local == "p" or node.get("id", "")):
                try:
                    _uint(node.get("id"))
                except ValueError:
                    return "문단/문단목록 ID가 안전한 숫자 범위가 아님"
            if local == "p" and any(_active(node.get(k)) for k in ("pageBreak", "columnBreak", "merged")):
                return "페이지/단 나눔 또는 연결 문단은 복제 미지원"
            if local == "subList" and any(_active(node.get(k)) for k in ("hasTextRef", "hasNumRef")):
                return "외부 본문/번호를 참조하는 문단목록은 복제 미지원"
    try:
        _row_height(table, row)
    except ValueError as exc:
        return str(exc)
    return ""


def inventory(parts: dict[str, bytes]) -> list[dict]:
    """Return physical tables and one-based rows, including unsupported reasons."""
    roots, protected = _roots(parts)
    records = []
    for part, root in _sections(roots):
        for table in root.findall(".//hp:tbl", NS):
            path = root.getroottree().getpath(table)
            rows = table.findall("hp:tr", NS)
            reason = protected or _table_reason(table)
            spans = _geometry(table)[1] if not reason else []
            items = []
            for index, row in enumerate(rows):
                why = reason or _row_reason(table, rows, spans, index)
                columns = ["\n".join(_hwpx_text(p) for p in cell.findall("hp:subList/hp:p", NS))
                           for cell in row.findall("hp:tc", NS)]
                items.append({"index": index + 1, "editable": not why, "reason": why, "columns": columns})
            label = " | ".join(next((item["columns"] for item in items if any(c.strip() for c in item["columns"])), []))
            records.append({"id": f"hwpx:{part}:{path}", "part": part, "xpath": path,
                            "label": label, "row_count": len(rows), "rows": items})
    return records


def transform(parts: dict[str, bytes], plan: dict) -> dict[str, bytes]:
    """Return only modified section bytes; never write or mutate original parts."""
    if not isinstance(plan, dict) or set(plan) != {"table_id", "row", "count"}:
        raise ValueError("행 확장 계획은 table_id/row/count만 포함해야 함")
    if (not isinstance(plan["table_id"], str) or type(plan["row"]) is not int
            or type(plan["count"]) is not int or not 1 <= plan["count"] <= 200):
        raise ValueError("행은 1부터, count는 원본 포함 1~200 정수이어야 함")
    record = next((record for record in inventory(parts) if record["id"] == plan["table_id"]), None)
    if record is None or not 1 <= plan["row"] <= record["row_count"]:
        raise ValueError("선택한 원본 표/행을 찾을 수 없음")
    selected = record["rows"][plan["row"] - 1]
    if not selected["editable"]:
        raise ValueError(selected["reason"])
    if plan["count"] == 1:
        return {}
    roots, _ = _roots(parts)
    root = roots[record["part"]]
    table = next(t for t in root.findall(".//hp:tbl", NS) if root.getroottree().getpath(t) == record["xpath"])
    rows, _ = _geometry(table)
    prototype = rows[plan["row"] - 1]
    delta = plan["count"] - 1
    if sum(map(len, parts.values())) + delta * len(etree.tostring(prototype)) > MAX_PACKAGE_BYTES:
        raise ValueError("반복 행 확장 후 HWPX 압축 해제 크기가 100 MiB 한도를 넘음")
    if len(rows) + delta > UINT_MAX:
        raise ValueError("HWPX 행수가 숫자 범위를 벗어남")
    step = _row_height(table, prototype) + _uint(table.get("cellSpacing", "0"))
    size = table.find("hp:sz", NS)
    height = _uint(size.get("height")) + delta * step
    if height > UINT_MAX:
        raise ValueError("확장된 표 높이가 HWPX 숫자 범위를 벗어남")
    used = {int(n.get("id")) for r in roots.values() for n in r.iter()
            if isinstance(n.tag, str) and re.fullmatch(r"[0-9]+", n.get("id", ""))}
    next_id = max(used, default=0) + 1
    insertion = table.index(prototype) + 1
    for offset in range(delta):
        clone = deepcopy(prototype)
        for node in clone.iter():
            local = etree.QName(node).localname
            if local == "cellAddr":
                node.set("rowAddr", str(plan["row"] + offset))
            if local in {"p", "subList"} and node.get("id", ""):
                if next_id > UINT_MAX:
                    next_id = 1
                while next_id in used:
                    next_id += 1
                if next_id > UINT_MAX:
                    raise ValueError("새 문단 ID를 배정할 수 없음")
                node.set("id", str(next_id))
                used.add(next_id)
                next_id += 1
        table.insert(insertion + offset, clone)
    for row in rows[plan["row"]:]:
        for address in row.findall("hp:tc/hp:cellAddr", NS):
            address.set("rowAddr", str(_uint(address.get("rowAddr")) + delta))
    table.set("rowCnt", str(len(rows) + delta))
    size.set("height", str(height))
    # ponytail: local cell line caches remain valid for unchanged cloned text;
    # only this and subsequent body paragraphs move after the table expands.
    anchor = next(p for p in table.iterancestors() if p.tag == f"{{{HP}}}p")
    for paragraph in list(root)[root.index(anchor):]:
        if paragraph.tag == f"{{{HP}}}p":
            for cache in paragraph.findall("hp:linesegarray", NS):
                paragraph.remove(cache)
    _geometry(table)
    data = etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)
    if sum(map(len, parts.values())) - len(parts[record["part"]]) + len(data) > MAX_PACKAGE_BYTES:
        raise ValueError("반복 행 확장 후 HWPX 압축 해제 크기가 100 MiB 한도를 넘음")
    return {record["part"]: data}
