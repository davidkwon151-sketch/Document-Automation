from copy import deepcopy
import hashlib
from pathlib import Path
from zipfile import ZipFile

from lxml import etree
import pytest

from parsers import parse_file
from templates.repeat_hwpx import HP, NS, UINT_MAX, inventory, transform


ROOT = Path(__file__).resolve().parents[1]
SECTION = "Contents/section0.xml"


def _parts(path=ROOT / "samples/sample_company_form.hwpx"):
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _xml(parts):
    return etree.fromstring(parts[SECTION])


def _table(root):
    return root.find(".//hp:tbl", NS)


def _save(parts, root):
    return {**parts, SECTION: etree.tostring(root, encoding="UTF-8", xml_declaration=True)}


def _fixture():
    parts = _parts()
    root = _xml(parts)
    table = _table(root)
    rows = table.findall("hp:tr", NS)
    for c in rows[0].findall("hp:tc", NS):
        c.set("header", "1")
    prototype = rows[1]
    total = deepcopy(prototype)
    for cell in total.findall("hp:tc", NS):
        cell.find("hp:cellAddr", NS).set("rowAddr", "2")
        p = cell.find("hp:subList/hp:p", NS)
        for child in list(p):
            p.remove(child)
        run = etree.SubElement(p, f"{{{HP}}}run", charPrIDRef="0")
        etree.SubElement(run, f"{{{HP}}}t").text = "합계"
    table.append(total)
    table.set("rowCnt", "3")
    table.set("cellSpacing", "20")
    table.find("hp:sz", NS).set("height", "3846")
    before = etree.Element(f"{{{HP}}}p", id="17", paraPrIDRef="0", styleIDRef="0")
    etree.SubElement(before, f"{{{HP}}}linesegarray")
    root.insert(0, before)
    after = etree.SubElement(root, f"{{{HP}}}p", id="18", paraPrIDRef="0", styleIDRef="0")
    etree.SubElement(after, f"{{{HP}}}linesegarray")
    anchor = next(p for p in table.iterancestors() if p.tag == f"{{{HP}}}p")
    etree.SubElement(anchor, f"{{{HP}}}linesegarray")
    for cell in prototype.findall("hp:tc", NS):
        p = cell.find("hp:subList/hp:p", NS)
        cache = etree.SubElement(p, f"{{{HP}}}linesegarray")
        etree.SubElement(cache, f"{{{HP}}}lineseg", vertpos="0", vertsize="1000")
    return _save(parts, root)


def _plan(parts, row=2, count=3):
    return {"table_id": inventory(parts)[0]["id"], "row": row, "count": count}


def _canonical(element):
    return etree.tostring(element, method="c14n", exclusive=True)


def test_inventory_original_physical_rows_and_input_only():
    parts = _fixture()
    original = deepcopy(parts)
    records = inventory(parts)
    assert parts == original
    assert records[0]["id"].startswith("hwpx:Contents/section0.xml:/hs:sec/")
    assert records[0]["row_count"] == 3
    assert [r["index"] for r in records[0]["rows"]] == [1, 2, 3]
    assert [r["editable"] for r in records[0]["rows"]] == [False, True, False]
    assert records[0]["rows"][0]["columns"] == ["제목", "요약", "내용"]
    assert "합계" in records[0]["rows"][2]["reason"]


def test_expand_preserves_originals_assets_styles_and_local_caches():
    parts = _fixture()
    original = deepcopy(parts)
    oldroot = _xml(parts)
    oldtable = _table(oldroot)
    oldrows = oldtable.findall("hp:tr", NS)
    changes = transform(parts, _plan(parts))
    assert parts == original
    assert set(changes) == {SECTION}
    newroot = _xml(changes)
    table = _table(newroot)
    rows = table.findall("hp:tr", NS)
    assert len(rows) == int(table.get("rowCnt")) == 5
    assert _canonical(rows[0]) == _canonical(oldrows[0])
    assert _canonical(rows[1]) == _canonical(oldrows[1])
    total = deepcopy(rows[4])
    for address in total.findall("hp:tc/hp:cellAddr", NS):
        address.set("rowAddr", "2")
    assert _canonical(total) == _canonical(oldrows[2])
    # Original 282 minimum height, 1000 cached line + 282 table padding.
    assert int(table.find("hp:sz", NS).get("height")) == 3846 + 2 * (1282 + 20)
    original_ids = {n.get("id") for n in oldroot.iter() if n.get("id")}
    clone_ids = []
    for i, row in enumerate(rows):
        assert {a.get("rowAddr") for a in row.findall("hp:tc/hp:cellAddr", NS)} == {str(i)}
        if i in (2, 3):
            copied = deepcopy(row)
            for new, old in zip(copied.iter(), oldrows[1].iter()):
                if etree.QName(new).localname == "p":
                    clone_ids.append(new.get("id"))
                    assert 0 < int(new.get("id")) <= UINT_MAX
                    new.set("id", old.get("id"))
                if etree.QName(new).localname == "cellAddr":
                    new.set("rowAddr", old.get("rowAddr"))
            assert _canonical(copied) == _canonical(oldrows[1])
    assert len(clone_ids) == len(set(clone_ids))
    assert not original_ids.intersection(clone_ids)
    assert newroot[0].find("hp:linesegarray", NS) is not None
    anchor = next(p for p in table.iterancestors() if p.tag == f"{{{HP}}}p")
    assert anchor.find("hp:linesegarray", NS) is None
    assert newroot[-1].find("hp:linesegarray", NS) is None
    assert len(rows[1].findall(".//hp:linesegarray", NS)) == 3


def test_horizontal_merge_and_split_run_placeholders_survive():
    parts = _fixture()
    root = _xml(parts)
    table = _table(root)
    row = table.findall("hp:tr", NS)[1]
    cells = row.findall("hp:tc", NS)
    cells[0].find("hp:cellSpan", NS).set("colSpan", "2")
    cells[0].find("hp:cellSz", NS).set("width", "27968")
    row.remove(cells[1])
    p = cells[0].find("hp:subList/hp:p", NS)
    for value, style in (("{{금", "0"), ("액}}원", "1")):
        run = etree.SubElement(p, f"{{{HP}}}run", charPrIDRef=style)
        etree.SubElement(run, f"{{{HP}}}t").text = value
    parts = _save(parts, root)
    result = _table(_xml(transform(parts, _plan(parts, count=2)))).findall("hp:tr", NS)
    assert len(result[2].findall("hp:tc", NS)) == 2
    assert result[2].find("hp:tc/hp:cellSpan", NS).get("colSpan") == "2"
    assert "".join(result[2].find("hp:tc", NS).itertext()).strip() == "{{금액}}원"
    assert [r.get("charPrIDRef") for r in result[2].find("hp:tc", NS).findall("hp:subList/hp:p/hp:run", NS)][-2:] == ["0", "1"]


def test_vertical_merge_below_insertion_shifts_without_resize():
    parts = _fixture()
    root = _xml(parts)
    table = _table(root)
    last = table.findall("hp:tr", NS)[2]
    fourth = deepcopy(last)
    for a in fourth.findall("hp:tc/hp:cellAddr", NS):
        a.set("rowAddr", "3")
    last.find("hp:tc/hp:cellSpan", NS).set("rowSpan", "2")
    fourth.remove(fourth.find("hp:tc", NS))
    table.append(fourth)
    table.set("rowCnt", "4")
    parts = _save(parts, root)
    table = _table(_xml(transform(parts, _plan(parts, count=2))))
    cell = table.findall("hp:tr", NS)[3].find("hp:tc", NS)
    assert cell.find("hp:cellSpan", NS).get("rowSpan") == "2"
    assert cell.find("hp:cellAddr", NS).get("rowAddr") == "3"


def test_crossing_vertical_merge_is_blocked_even_if_other_cells_blank():
    parts = _fixture()
    root = _xml(parts)
    table = _table(root)
    rows = table.findall("hp:tr", NS)
    rows[0].find("hp:tc/hp:cellSpan", NS).set("rowSpan", "2")
    rows[1].remove(rows[1].find("hp:tc", NS))
    parts = _save(parts, root)
    assert not inventory(parts)[0]["rows"][1]["editable"]
    with pytest.raises(ValueError, match="세로 병합"):
        transform(parts, _plan(parts))


@pytest.mark.parametrize("mutation,match", [
    ("protect", "보호"), ("lock", "잠금"), ("name", "참조"),
    ("field", "제어"), ("linked", "참조"), ("tracking", "추적"),
    ("relative", "상대 높이"), ("grid", "주소"), ("unknown_namespace", "알 수 없는"),
    ("id", "ID"), ("pagebreak", "나눔"), ("zeroheight", "높이"),
])
def test_unsupported_rows_are_visible_and_fail_closed(mutation, match):
    parts = _fixture()
    root = _xml(parts)
    table = _table(root)
    cell = table.findall("hp:tr", NS)[1].find("hp:tc", NS)
    if mutation == "protect": cell.set("protect", "1")
    elif mutation == "lock": table.set("lock", "1")
    elif mutation == "name": cell.set("name", "named_amount")
    elif mutation == "field": etree.SubElement(cell.find("hp:subList/hp:p", NS), f"{{{HP}}}ctrl")
    elif mutation == "linked": cell.find("hp:subList", NS).set("linkListIDRef", "42")
    elif mutation == "tracking": cell.find("hp:subList/hp:p", NS).set("paraTcId", "0")
    elif mutation == "relative": table.find("hp:sz", NS).set("heightRelTo", "PAGE")
    elif mutation == "grid": cell.find("hp:cellAddr", NS).set("colAddr", "1")
    elif mutation == "unknown_namespace": etree.SubElement(cell, "{urn:unknown}t")
    elif mutation == "id": cell.find("hp:subList/hp:p", NS).set("id", "invalid")
    elif mutation == "pagebreak": cell.find("hp:subList/hp:p", NS).set("pageBreak", "1")
    elif mutation == "zeroheight":
        for c in table.findall("hp:tr", NS)[1].findall("hp:tc", NS):
            c.find("hp:cellSz", NS).set("height", "0")
            p = c.find("hp:subList/hp:p", NS)
            p.remove(p.find("hp:linesegarray", NS))
    parts = _save(parts, root)
    row = inventory(parts)[0]["rows"][1]
    assert not row["editable"] and match in row["reason"]
    with pytest.raises(ValueError, match=match): transform(parts, _plan(parts))


def test_new_ids_avoid_other_sections_header_and_uint_overflow():
    parts = _fixture()
    parts["Contents/section1.xml"] = f'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="{HP}"><hp:p id="{UINT_MAX}"/></hs:sec>'.encode()
    root = _xml(parts)
    table = _table(root)
    table.findall("hp:tr", NS)[1].find("hp:tc/hp:subList", NS).set("id", "42")
    parts = _save(parts, root)
    result = _xml(transform(parts, _plan(parts, count=2)))
    row = _table(result).findall("hp:tr", NS)[2]
    ids = [n.get("id") for n in row.iter() if n.get("id")]
    assert "42" not in ids and str(UINT_MAX) not in ids
    assert len(ids) == len(set(ids))
    assert all(0 < int(value) <= UINT_MAX for value in ids)


@pytest.mark.parametrize("field,value", [("count",0),("count",201),("count",True),("row",0),("row",4),("row",1.0),("table_id","wrong")])
def test_invalid_plan_has_no_changes(field, value):
    parts = _fixture()
    before = deepcopy(parts)
    plan = _plan(parts)
    plan[field] = value
    with pytest.raises(ValueError): transform(parts, plan)
    assert parts == before


def test_noop_and_maximum_200_rows():
    parts = _fixture()
    assert transform(parts, _plan(parts, count=1)) == {}
    table = _table(_xml(transform(parts, _plan(parts, count=200))))
    assert len(table.findall("hp:tr", NS)) == 202


def test_repeated_rows_cannot_exceed_package_size_cap(monkeypatch):
    from templates import repeat_hwpx
    parts = _fixture()
    row = _table(_xml(parts)).findall("hp:tr", NS)[1]
    monkeypatch.setattr(repeat_hwpx, "MAX_PACKAGE_BYTES", sum(map(len, parts.values())) + len(etree.tostring(row)))
    with pytest.raises(ValueError, match="100 MiB"):
        transform(parts, _plan(parts, count=3))


@pytest.mark.parametrize("part", ["META-INF/signatures.xml", "encrypt.xml"])
def test_document_protection_never_bypassed(part):
    parts = {**_fixture(), part: b"<protection/>"}
    assert all(not row["editable"] for record in inventory(parts) for row in record["rows"])
    with pytest.raises(ValueError, match="보호"): transform(parts, _plan(parts))


def test_xml_entities_rejected():
    parts = _fixture()
    parts[SECTION] = b'<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///secret">]><foo>&x;</foo>'
    with pytest.raises(ValueError, match="DTD"): inventory(parts)


def test_missing_xml_schema_or_ambiguous_plan_rejected():
    parts = _fixture()
    with pytest.raises(ValueError, match="계획"):
        transform(parts, {**_plan(parts), "unsafe":True})
    with pytest.raises(ValueError, match="XML"):
        inventory({**parts, SECTION:b"<bad"})


@pytest.mark.parametrize("tag", ["cellzoneList", "label", "caption"])
def test_row_address_dependent_table_extensions_rejected(tag):
    parts = _fixture()
    root = _xml(parts)
    etree.SubElement(_table(root), f"{{{HP}}}{tag}")
    parts = _save(parts, root)
    with pytest.raises(ValueError, match="표 구조"):
        transform(parts, _plan(parts))


def test_nested_table_not_duplicated():
    parts = _fixture()
    root = _xml(parts)
    table = _table(root)
    outer = deepcopy(table)
    p = outer.find("hp:tr/hp:tc/hp:subList/hp:p", NS)
    run = etree.SubElement(p, f"{{{HP}}}run", charPrIDRef="0")
    run.append(deepcopy(table))
    table.getparent().replace(table, outer)
    records = inventory(_save(parts, root))
    assert "중첩" in records[1]["rows"][1]["reason"]


def test_signature_picture_name_is_not_security_manifest():
    parts = {**_fixture(), "BinData/signature.png":b"image"}
    assert inventory(parts)[0]["rows"][1]["editable"]


@pytest.mark.parametrize("name", ["business/business_mss_allinone_plan_2026.hwpx", "office/office_hanbat_plan_2026.hwpx"])
def test_real_official_source_expand_reopen_with_m1_and_source_hash(name, tmp_path):
    path = ROOT / "data/public_templates" / name
    if not path.exists(): pytest.skip("공식 실자료 snapshot 없음")
    before_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    parts = _parts(path)
    original = parse_file(path)
    records = inventory(parts)
    candidates = [(record, row) for record in records for row in record["rows"] if row["editable"]]
    assert candidates, "실자료에서 안전한 빈 입력 행이 없음"
    record, row = candidates[0]
    changes = transform(parts, {"table_id":record["id"], "row":row["index"], "count":3})
    output = tmp_path / path.name
    with ZipFile(output, "w") as archive:
        for part, data in parts.items(): archive.writestr(part, changes.get(part, data))
    parsed = parse_file(output)
    assert parsed["본문"] == original["본문"]  # duplicated row was genuinely blank
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_sha
    assert len(inventory({**parts, **changes})[next(i for i,r in enumerate(records) if r["id"]==record["id"])]["rows"]) == record["row_count"] + 2
    with ZipFile(output) as archive:
        assert all(archive.read(part) == data for part,data in parts.items() if part not in changes)
