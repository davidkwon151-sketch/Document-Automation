"""텍스트 조각만 수정하며 문단/글꼴/표 속성과 나머지 ZIP 부품을 보존함."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import tempfile
from zipfile import BadZipFile, ZipFile

from lxml import etree

from parsers.extract import _check_zip, _path, _xml, ParseError


PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


class TemplateError(ValueError):
    pass


@dataclass
class _Segment:
    element: object
    attribute: str
    value: str
    editable: bool = True


def _segments(paragraph, kind: str) -> list[_Segment]:
    segments = []

    def visit(element):
        name = etree.QName(element).localname
        if element is not paragraph and name == "p":
            return  # 표/글상자/머리말 내부의 별도 문단은 다음 순회에서 처리함.
        if kind == "docx":
            if element.tag == f"{{{WORD_NS}}}t":
                segments.append(_Segment(element, "text", element.text or ""))
            elif element.tag in {f"{{{WORD_NS}}}br", f"{{{WORD_NS}}}tab", f"{{{WORD_NS}}}cr"}:
                segments.append(_Segment(element, "text", "\t" if name == "tab" else "\n", False))
            else:
                for child in element:
                    visit(child)
        else:
            if name == "t":
                segments.append(_Segment(element, "text", element.text or ""))
                for child in element:
                    boundary = {"lineBreak": "\n", "tab": "\t", "nbSpace": " ",
                                "fwSpace": "　", "hyphen": "-"}.get(etree.QName(child).localname, "")
                    if boundary:
                        segments.append(_Segment(child, "text", boundary, False))
                    segments.append(_Segment(child, "tail", child.tail or ""))
            else:
                for child in element:
                    visit(child)

    visit(paragraph)
    return segments


def _set_segment(segment: _Segment, value: str, kind: str) -> None:
    """값의 개행을 문서 형식의 명시적 줄 나눔으로 기록함."""
    lines = value.split("\n")
    element = segment.element
    setattr(element, segment.attribute, lines[0])
    if kind == "docx":
        element.set(XML_SPACE, "preserve")
        parent = element.getparent()
        position = parent.index(element) + 1
        for line in lines[1:]:
            line_break = etree.Element(f"{{{WORD_NS}}}br")
            text = etree.Element(f"{{{WORD_NS}}}t")
            text.text = line
            text.set(XML_SPACE, "preserve")
            parent.insert(position, line_break)
            parent.insert(position + 1, text)
            position += 2
    else:
        # HWPX의 hp:t는 mixed content이며 줄 나눔 뒤의 텍스트는 tail에 저장함.
        parent = element if segment.attribute == "text" else element.getparent()
        position = 0 if segment.attribute == "text" else parent.index(element) + 1
        namespace = etree.QName(parent).namespace
        for line in lines[1:]:
            line_break = etree.Element(f"{{{namespace}}}lineBreak")
            line_break.tail = line
            parent.insert(position, line_break)
            position += 1


def _replace_paragraph(paragraph, values: dict[str, str], kind: str) -> set[str]:
    segments = _segments(paragraph, kind)
    text = "".join(segment.value for segment in segments)
    matches = list(PLACEHOLDER.finditer(text))
    names = {match[1].strip() for match in matches}
    if not matches:
        return names
    missing = [name for name in names if name not in values or not values[name].strip()]
    if missing:
        raise TemplateError("양식 필수 값이 누락됨: " + ", ".join(sorted(missing)))
    starts, offset = [], 0
    for segment in segments:
        starts.append(offset)
        offset += len(segment.value)
    # 원본 매치만 역순 처리해 사용자 값 안의 {{...}}를 재치환하지 않음.
    for match in reversed(matches):
        affected = [index for index, segment in enumerate(segments)
                    if starts[index] < match.end() and starts[index] + len(segment.value) > match.start()]
        if not affected or any(not segments[index].editable for index in affected):
            raise TemplateError("자리표시자는 줄 나눔/제어 문자를 가로질러 배치할 수 없음")
        first, last = affected[0], affected[-1]
        prefix = getattr(segments[first].element, segments[first].attribute) or ""
        prefix = prefix[:match.start() - starts[first]]
        last_value = getattr(segments[last].element, segments[last].attribute) or ""
        suffix = last_value[match.end() - starts[last]:]
        value = values[match[1].strip()].replace("\r\n", "\n").replace("\r", "\n")
        if first == last:
            _set_segment(segments[first], prefix + value + suffix, kind)
        else:
            _set_segment(segments[first], prefix + value, kind)
            for index in affected[1:-1]:
                _set_segment(segments[index], "", kind)
            _set_segment(segments[last], suffix, kind)
    if kind == "hwpx":
        # 한컴 공식 안내: 변경된 본문의 캐시된 줄 위치는 제거해 한글이 재계산하게 함.
        for child in list(paragraph):
            if etree.QName(child).localname == "linesegarray":
                paragraph.remove(child)
    return names


def fill_template(template_path: str | Path, values: dict[str, str], output_path: str | Path) -> Path:
    """{{이름}}을 문단·표·머리말·꼬리말에서 치환하고 다른 경로에 원자적으로 저장함.

    여러 run에 나뉜 값은 첫 글자의 서식을 상속함. 표·글꼴·문단 속성은
    보존하지만 내용 길이에 따른 페이지 재배치와 표 넘침은 별도 검토해야 함.
    """
    try:
        source = _path(template_path)
    except ParseError as exc:
        raise TemplateError(str(exc)) from exc
    target = Path(output_path)
    kind = source.suffix.lower().lstrip(".")
    if kind not in {"docx", "hwpx"} or target.suffix.lower() != source.suffix.lower():
        raise TemplateError("원본과 출력은 같은 DOCX 또는 HWPX 형식이어야 함")
    if source.resolve() == target.resolve():
        raise TemplateError("원본 양식에 덮어쓸 수 없음")
    if not isinstance(values, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                            for k, v in values.items()):
        raise TemplateError("자리표시자 값은 dict[str, str]이어야 함")
    if any(re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value) for value in values.values()):
        raise TemplateError("XML에서 허용하지 않는 제어 문자가 포함됨")
    changes, all_names = {}, set()
    try:
        with ZipFile(source) as archive:
            _check_zip(archive)
            if kind == "docx":
                if "word/document.xml" not in archive.namelist():
                    raise TemplateError("DOCX의 본문 XML이 없음")
                parts = [name for name in archive.namelist() if name.startswith("word/") and name.endswith(".xml")]
            else:
                if archive.read("mimetype").strip() != b"application/hwp+zip":
                    raise TemplateError("HWPX mimetype이 올바르지 않음")
                required = {"version.xml", "Contents/header.xml", "Contents/content.hpf", "META-INF/container.xml"}
                if not required.issubset(archive.namelist()):
                    raise TemplateError("HWPX 필수 패키지 부품이 없음")
                parts = [name for name in archive.namelist()
                         if re.fullmatch(r"Contents/section\d+\.xml", name)
                         or re.fullmatch(r"Contents/masterpage\d+\.xml", name)]
                if "Contents/section0.xml" not in parts:
                    raise TemplateError("HWPX 본문 구역이 없음")
            for name in parts:
                root = _xml(archive.read(name))
                found = set()
                paragraphs = root.xpath(".//w:p", namespaces={"w": WORD_NS}) if kind == "docx" else root.xpath(".//*[local-name()='p']")
                for paragraph in paragraphs:
                    found.update(_replace_paragraph(paragraph, values, kind))
                if found:
                    changes[name] = etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)
                    all_names.update(found)
            if not all_names:
                raise TemplateError("양식에서 {{이름}} 자리표시자를 찾을 수 없음")
            if kind == "hwpx" and "Preview/PrvText.txt" in archive.namelist():
                # 텍스트 미리보기도 실제 본문과 동기화함. 이미지는 재렌더링할 수 없음.
                texts = []
                for name in parts:
                    root = _xml(changes.get(name, archive.read(name)))
                    from parsers.extract import _hwpx_text
                    texts.extend(_hwpx_text(p) for p in root.xpath(".//*[local-name()='p']"))
                changes["Preview/PrvText.txt"] = "\n".join(texts).encode("utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(dir=target.parent, suffix="." + kind)
            os.close(descriptor)
            try:
                with ZipFile(temporary, "w") as result:
                    result.comment = archive.comment
                    for item in archive.infolist():
                        result.writestr(item, changes.get(item.filename, archive.read(item.filename)))
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    except TemplateError:
        raise
    except (BadZipFile, KeyError, ValueError, etree.XMLSyntaxError, OSError) as exc:
        raise TemplateError(f"양식을 채울 수 없음: {source.name}") from exc
    return target
