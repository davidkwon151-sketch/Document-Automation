"""Word 원본의 폐쇄 선택과 편집 가능한 콤보 입력만 작성함."""

from .fill import TemplateError, WORD_NS, XML_SPACE


def native_choice(properties, content):
    ns = {"w": WORD_NS}
    choices = properties.xpath("./w:dropDownList | ./w:comboBox", namespaces=ns)
    if len(choices) != 1 or properties.xpath("./w:lock | ./w:dataBinding", namespaces=ns) or content.xpath("ancestor::w:sdt/w:sdtPr/w:lock", namespaces=ns):
        raise TemplateError("선택 정의가 중복되거나 잠금/XML 연결이 있음")
    if len(content.xpath(".//w:r", namespaces=ns)) != 1 or not content.xpath(".//w:t", namespaces=ns) or content.xpath(".//w:br | .//w:tab | .//w:tbl | .//w:sdt | .//w:drawing | .//w:fldChar", namespaces=ns) or len(content.xpath(".//w:p", namespaces=ns)) > 1:
        raise TemplateError("선택 입력은 한 문단·한 런의 텍스트 구조여야 함")
    node = choices[0]
    combo = node.tag == f"{{{WORD_NS}}}comboBox"
    items = []
    for item in node:
        if item.tag != f"{{{WORD_NS}}}listItem":
            raise TemplateError("알 수 없는 선택 항목 구조임")
        value = item.get(f"{{{WORD_NS}}}value")
        label = item.get(f"{{{WORD_NS}}}displayText", value)
        if not value or value != value.strip() or not label or not label.strip() or any(c in value + label for c in "\r\n\t"):
            raise TemplateError("빈 값·줄바꿈 또는 유효하지 않은 선택 정의임")
        items.append({"value": value, "label": label})
    if (not combo and not items) or len({item['value'] for item in items}) != len(items) or len({item['label'] for item in items}) != len(items):
        raise TemplateError("비어 있거나 중복된 선택 항목임")
    return {"kind": "docx_combobox" if combo else "docx_choice",
            "control_type": "combobox" if combo else "choice", "options": [item['value'] for item in items],
            "choice_items": items, "allow_custom": combo, "editable_native": combo,
            "input_required": True, "input_mode": "user_provided", "narrative_style_required": False}


def fill_native_choice(properties, content, field, value):
    native = native_choice(properties, content)
    if field['kind'] != native['kind'] or any(field.get(key) != native[key] for key in ('options', 'choice_items', 'allow_custom')):
        raise TemplateError("원본 선택 정의와 프로파일이 일치하지 않음")
    if not value.strip() or any(c in value for c in "\r\n\t"):
        raise TemplateError("선택·콤보 입력값은 비어 있지 않은 한 줄이어야 함")
    if content.xpath(".//w:t/text()", namespaces={"w": WORD_NS}) and ''.join(content.xpath(".//w:t/text()", namespaces={"w": WORD_NS})).strip() and properties.find(f"{{{WORD_NS}}}showingPlcHdr") is None:
        raise TemplateError("기존 선택값을 덮어쓸 수 없음")
    matches = [item['label'] for item in native['choice_items'] if item['value'] == value]
    if not matches and not native['allow_custom']:
        raise TemplateError("원본 목록에 없는 선택값임")
    label = matches[0] if matches else value
    nodes = content.xpath(".//w:t", namespaces={"w": WORD_NS})
    if not nodes:
        raise TemplateError("선택 컨트롤의 표시 문자 구조가 없음")
    nodes[0].text = label
    nodes[0].set(XML_SPACE, "preserve")
    for node in nodes[1:]:
        node.text = ""
    choice = properties.find(f"{{{WORD_NS}}}{'comboBox' if native['allow_custom'] else 'dropDownList'}")
    choice.set(f"{{{WORD_NS}}}lastValue", value)
    for node in properties.findall(f"{{{WORD_NS}}}showingPlcHdr"):
        properties.remove(node)
