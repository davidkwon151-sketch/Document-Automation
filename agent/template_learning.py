"""미지 양식의 기입 위치를 제안하고 사용자가 확정한 매핑만 재사용함."""

from copy import deepcopy
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from hashlib import file_digest, sha256
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import tempfile
from zipfile import ZipFile

from lxml import etree

from templates import analyze_template, load_form_profile
from templates.profiles import PDF_CHOICE_KEYS, refresh_pdf_choice_metadata
from templates.value_rules import validate_rule_profile, mapped_rule_profile
from agent.brief import model_profile


ENGINE_VERSION = "template-mapping-6"
DEFAULT_CACHE = Path(__file__).resolve().parents[1] / ".runtime/template_mappings"
DIRECT_INPUT = re.compile(
    r"성명|이름|작성자|담당자|대표자|소속|부서|주소|전화|연락|메일|생년|주민|등록번호|서명|직인|도장|인감|"
    r"계좌|비밀번호|투표|찬성|반대|기권|동의|위임|의결권|주식수|결재|"
    r"(?<![A-Za-z0-9])(?:name|(?:first|last|full|given|family)[\s_-]*name|author|prepared[\s_-]*by|"
    r"requested[\s_-]*by|requester|submitted[\s_-]*by|department|applicant|address|signature|"
    r"signed[\s_-]*by|e-?mail|phone|telephone|mobile|consent|vote|(?:bank[\s_-]*)?account(?:[\s_-]*number)?|password|ssn|"
    r"tax[\s_-]*(?:id|identification(?:[\s_-]*number)?)|employee[\s_-]*id|"
    r"contact[\s_-]*(?:person|name)|date[\s_-]*of[\s_-]*birth|dob|"
    r"social[\s_-]*security(?:[\s_-]*(?:number|no\.?))?|"
    r"approved[\s_-]*by|authorized[\s_-]*by|approver|approval|iban)(?![A-Za-z0-9])", re.I)
NUMBER = re.compile(r"금액|예산|비용|수수료|주식수|수량|인원|인건비|비율|날짜|일자|기간|마감|"
                    r"(?<![A-Za-z0-9])(?:amount|budget|cost|expense|fee|quantity|headcount|rate|ratio|number|date|period|deadline|price|count)(?![A-Za-z0-9])", re.I)


def _digest(path):
    with Path(path).open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _cache_directory(directory, repeat_expansion=None):
    directory = Path(directory)
    if repeat_expansion is not None:
        if not isinstance(repeat_expansion, dict) or set(repeat_expansion) != {'original_sha256', 'prepared_sha256', 'plan'}:
            raise ValueError('반복 매핑 캐시에 원본·준비본·계획 바인딩이 필요함')
        # count=1 may keep identical bytes: file SHA alone cannot distinguish these mappings.
        directory = directory / 'repeat' / sha256(_json_bytes(repeat_expansion)).hexdigest()
    return directory


def _cache_key(directory, create=False):
    path = directory / ".integrity.key"
    if path.is_symlink():
        raise ValueError("매핑 캐시 키의 링크는 허용하지 않음")
    if create:
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as stream:
                stream.write(secrets.token_bytes(32))
            path.chmod(0o600)
        except FileExistsError:
            pass
    if not path.is_file():
        return None
    key = path.read_bytes()
    if len(key) != 32:
        raise ValueError("매핑 캐시 무결성 키가 손상됨")
    return key


def _check_fields(fields, base_fields=None):
    if not isinstance(fields, list) or any(not isinstance(field, dict) for field in fields):
        raise ValueError("양식 필드는 JSON 객체 목록이어야 함")
    known = {field["id"]: field for field in base_fields or []}
    ids, keys = set(), {}
    for field in fields:
        identifier, key = field.get("id"), field.get("value_key")
        if not isinstance(identifier, str) or identifier in ids or (base_fields is not None and identifier not in known):
            raise ValueError("미등록 또는 중복 입력칸 ID임")
        if not isinstance(key, str) or not key.strip() or len(key) > 100 or re.search(r"[\x00-\x1f]", key):
            raise ValueError("잘못된 항목 이름임")
        if base_fields is not None and field.get("kind") != known[identifier]["kind"]:
            raise ValueError("입력칸 종류는 원본과 같아야 함")
        if any(type(field.get(flag)) is not bool for flag in ("required", "input_required")):
            raise ValueError("필수 입력 여부는 boolean이어야 함")
        if type(field.get("max_chars")) is not int or not 1 <= field["max_chars"] <= 10000:
            raise ValueError("입력 길이는 1~10000의 정수여야 함")
        if field.get("input_mode") not in {"user_provided", "source_grounded"}:
            raise ValueError("직접 입력 또는 자료 근거 모드만 허용함")
        confidence = field.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("확신도는 0~1의 유한수여야 함")
        original = known.get(identifier, field)
        if base_fields is not None and original.get('kind') == 'pdf_form' and original.get('control_type') in {'choice', 'combobox', 'choice_unresolved'}:
            if any(field.get(key) != original.get(key) for key in PDF_CHOICE_KEYS):
                raise ValueError('원본 PDF 선택 목록·표시 이름·편집/다중 선택 권한은 매핑으로 바꿀 수 없음')
            if original.get('allow_custom') and not original.get('validation') and field.get('validation', {}).get('type') == 'choice':
                raise ValueError('편집 가능한 원본 콤보를 AI가 닫힌 목록으로 바꿀 수 없음')
        if (base_fields is not None and original.get('repeat_info') and original.get('required')
                and not field['required']):
            raise ValueError('원본 반복 행의 필수 입력은 AI 매핑으로 완화할 수 없음')
        if base_fields is not None and original.get('validation') and field.get('validation') != original['validation']:
            raise ValueError('원본에서 확인한 입력 규칙은 AI 매핑으로 바꿀 수 없음')
        direct = original.get("input_required") or original.get("control_type") in {"checkbox", "radio", "choice", "combobox", "choice_unresolved"} or DIRECT_INPUT.search(str(original.get("label", "")) + " " + key)
        if (direct or field["input_required"]) and (not field["input_required"] or field["input_mode"] != "user_provided"):
            raise ValueError("개인정보·서명·동의·투표·선택값은 사용자 직접 입력이어야 함")
        ids.add(identifier)
        rules = tuple(field[name] for name in ("input_required", "input_mode", "max_chars"))
        if key in keys and keys[key] != rules:
            raise ValueError("동일 항목을 여러 칸에 넣을 때 입력 방식과 길이 제한이 같아야 함")
        keys[key] = rules
    validate_rule_profile({'fields': fields})


def save_learned_profile(path, profile, mapping=None, *, cache_dir=DEFAULT_CACHE, user_confirmed=False):
    """사용자 확인 전에는 저장하지 않으며 실제 개인 값은 캐시에 저장하지 않음."""
    if user_confirmed is not True:
        raise ValueError("사용자가 입력 위치와 매핑을 확인해야 저장할 수 있음")
    source = Path(path)
    digest = _digest(source)
    if profile.get("source_sha256") != digest or profile.get("format") != source.suffix.lower().lstrip("."):
        raise ValueError("현재 원본과 양식 프로필이 일치하지 않음")
    _check_fields(profile.get("fields"))
    validate_rule_profile(profile)
    mapping = mapping if mapping is not None else {field["id"]: field["value_key"] for field in profile["fields"]}
    known = {field["id"] for field in profile["fields"]}
    if not isinstance(mapping, dict) or not mapping or any(identifier not in known or not isinstance(key, str) or not key.strip() for identifier, key in mapping.items()):
        raise ValueError("확정 매핑에는 등록된 입력칸과 문자열 키만 허용함")
    stored = model_profile(mapped_rule_profile(profile, mapping))
    refresh_pdf_choice_metadata(source, stored)
    _check_fields(stored["fields"])
    validate_rule_profile(stored)
    stored['configured'] = True
    stored["learning"] = {"origin": "confirmed_cache", "needs_confirmation": False, "engine_version": ENGINE_VERSION}
    envelope = {"engine_version": ENGINE_VERSION, "source_sha256": digest, "user_confirmed": True,
                "profile": stored, "mapping": mapping}
    directory = _cache_directory(cache_dir, stored.get('repeat_expansion'))
    key = _cache_key(directory, create=True)
    envelope["hmac_sha256"] = hmac.new(key, _json_bytes(envelope), sha256).hexdigest()
    target = directory / f"{digest}.json"
    if target.is_symlink():
        raise ValueError("매핑 캐시의 링크는 허용하지 않음")
    descriptor, temporary = tempfile.mkstemp(dir=directory, suffix=".json")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes(envelope))
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


def load_learned_profile(path, cache_dir=DEFAULT_CACHE, *, repeat_expansion=None):
    source, directory = Path(path), _cache_directory(cache_dir, repeat_expansion)
    if not source.is_file():
        return None
    digest = _digest(source)
    target = directory / f"{digest}.json"
    if not target.is_file():
        return None
    if target.is_symlink() or target.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("매핑 캐시 경로 또는 크기가 잘못됨")
    envelope = json.loads(target.read_text(encoding="utf-8"))
    signature = envelope.pop("hmac_sha256", "")
    key = _cache_key(directory)
    if key is None or not isinstance(signature, str) or not hmac.compare_digest(signature, hmac.new(key, _json_bytes(envelope), sha256).hexdigest()):
        raise ValueError("매핑 캐시 변조 또는 손상이 발견됨")
    if envelope.get("engine_version") != ENGINE_VERSION:
        return None
    profile = envelope["profile"]
    if profile.get('repeat_expansion') != repeat_expansion:
        return None
    if envelope.get("source_sha256") != digest or envelope.get("user_confirmed") is not True or profile.get("source_sha256") != digest or profile.get("format") != source.suffix.lower().lstrip("."):
        raise ValueError("매핑 캐시의 원본·확인 정보가 잘못됨")
    refresh_pdf_choice_metadata(source, profile)
    _check_fields(profile.get("fields"))
    validate_rule_profile(profile)
    return model_profile(profile)


def _contexts(path, fields):
    contexts = []
    archive = ZipFile(path) if Path(path).suffix.lower() in {".docx", ".hwpx", ".xlsx", ".pptx"} else None
    roots, shared_strings, xlsx_contexts = {}, None, {}

    def part_root(part):
        if part not in roots:
            roots[part] = etree.fromstring(archive.read(part), etree.XMLParser(resolve_entities=False, no_network=True))
        return roots[part]

    try:
        for field in fields:
            context = field["label"]
            if archive and field["kind"].startswith(("docx_", "hwpx_")):
                _, part, xpath = field["id"].split(":", 2)
                if field['kind'] in {'docx_repeat_placeholder', 'hwpx_repeat_placeholder'}:
                    xpath = field['xml_path']
                root = part_root(part)
                nodes = root.xpath(xpath, namespaces={key: value for key, value in root.nsmap.items() if key})
                if nodes:
                    rows = nodes[0].xpath("ancestor::*[local-name()='tr']")
                    paragraphs = nodes[0].xpath("ancestor::*[local-name()='p']") if field['kind'] == 'docx_legacy_text' else []
                    container = rows[-1] if rows else (paragraphs[-1] if paragraphs else nodes[0].getparent())
                    context = " ".join(container.itertext())[:800] or field["label"]
            elif archive and field["kind"].startswith("xlsx_"):
                from templates.compatibility import _xlsx_cell_text, _xlsx_parts
                from templates.repeat_xlsx_profile import _header_rows
                _, part, coordinate, *_ = field["id"].split(":")
                if shared_strings is None:
                    shared_strings, _ = _xlsx_parts(archive)
                root = part_root(part)
                row = re.search(r"\d+", coordinate).group()
                key = part, row
                if key not in xlsx_contexts:
                    all_rows = root.xpath(".//*[local-name()='sheetData']/*[local-name()='row']")
                    nearby = [item for item in all_rows if int(row) - 8 <= int(item.get('r')) < int(row)]
                    headers = list(reversed(_header_rows(nearby, shared_strings, int(row))[:3]))
                    headers += [item for item in all_rows if item.get('r') == row]
                    xlsx_contexts[key] = " | ".join(
                        f"{cell.get('r')}: {text}" for item in headers for cell in item
                        if (text := _xlsx_cell_text(cell, shared_strings).strip()))[:800]
                context = xlsx_contexts[key] or field['label']
            elif archive and field["kind"] in {"pptx_text", "pptx_cell"}:
                _, part, xpath = field["id"].split(":", 2)
                root = part_root(part)
                nodes = root.xpath(xpath, namespaces={key: value for key, value in root.nsmap.items() if key})
                if nodes:
                    titles = root.xpath(".//*[local-name()='sp' and .//*[local-name()='ph' and (@type='title' or @type='ctrTitle')]]//*[local-name()='t']/text()")
                    rows = nodes[0].xpath("ancestor::*[local-name()='tr']")
                    if rows:
                        surrounding = rows[-1].xpath(".//*[local-name()='t']/text()")
                    else:
                        shape = nodes[0].getparent()
                        surrounding = shape.xpath(".//*[local-name()='cNvPr']/@name")
                        for adjacent in (shape.getprevious(), shape.getnext()):
                            if adjacent is not None:
                                surrounding.extend(adjacent.xpath(".//*[local-name()='t']/text()"))
                    context = " | ".join([" ".join(titles)[:300], *surrounding])[:800].strip(' |') or field['label']
            location = {key: field[key] for key in ("page", "x", "y", "width", "height") if key in field}
            if field.get('repeat_info'):
                location['repeat_info'] = deepcopy(field['repeat_info'])
            contexts.append({"id": field["id"], "kind": field["kind"], "label": field["label"], "context": context,
                             "location": location,
                             "options": field.get("options", []), "control_type": field.get("control_type"),
                             "input_required": bool(field.get("input_required")), "max_chars": field.get("max_chars", 0)})
            if field.get('validation'):
                contexts[-1]['validation'] = deepcopy(field['validation'])
            if field.get('choice_items'):
                contexts[-1]['choice_items'] = deepcopy(field['choice_items'])
            if 'allow_custom' in field:
                contexts[-1]['allow_custom'] = field['allow_custom']
            for key in ('multiselect', 'selection_encoding'):
                if key in field:
                    contexts[-1][key] = field[key]
    finally:
        if archive:
            archive.close()
    return contexts


def _image_fields(path, profile, client, max_pages, workers):
    from parsers.extended import render_pdf_page
    proposed = []
    pages = profile.get("pages", [])
    selected = pages if max_pages is None else pages[:max_pages]

    def collect(page, future):
        response = future.result()
        if not isinstance(response, dict) or not isinstance(response.get("fields"), list):
            raise ValueError("이미지 양식 응답에는 fields 목록이 필요함")
        for index, field in enumerate(response["fields"]):
            if not isinstance(field, dict) or field.get("kind", "pdf_overlay") != "pdf_overlay" or field.get("page", page["page"]) != page["page"]:
                raise ValueError("이미지 입력칸의 종류 또는 페이지가 잘못됨")
            if any(isinstance(field.get(key), bool) or not isinstance(field.get(key), (int, float)) or not math.isfinite(field[key]) for key in ("x", "y", "width", "height")):
                raise ValueError("이미지 입력칸 좌표는 유한수여야 함")
            if field["x"] < 0 or field["y"] < 0 or field["width"] <= 0 or field["height"] <= 0 or field["x"] + field["width"] > page["width"] or field["y"] + field["height"] > page["height"]:
                raise ValueError("이미지 입력칸이 실제 PDF 페이지 경계를 넘음")
            if set(field) - {"id", "page", "kind", "x", "y", "width", "height", "font_size", "label", "value_key", "confidence", "required", "input_required", "input_mode", "max_chars", "validation"}:
                raise ValueError("이미지 분석에서는 입력 위치와 항목 정보만 허용함")
            item = dict(field, id=f"learned-pdf:{page['page']}:{index}", page=page["page"], kind="pdf_overlay")
            item.setdefault("label", f"입력 영역 {index+1}")
            item.setdefault("value_key", item["label"])
            item.setdefault("font_size", 10)
            if isinstance(item["font_size"], bool) or not isinstance(item["font_size"], (int, float)) or not math.isfinite(item["font_size"]) or not 4 <= item["font_size"] <= 40:
                raise ValueError("이미지 입력칸 글자 크기는 4~40pt여야 함")
            proposed.append(item)

    # PDFium 렌더는 한 스레드에서 수행하고 외부 요청만 제한된 병렬로 실행함.
    pending = deque()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for page in selected:
            image = render_pdf_page(path, page["page"])
            future = executor.submit(client.read_image_json, image, prompt_name="template_image", payload={
                "page": page["page"], "width": page["width"], "height": page["height"],
                "coordinate_system": "top-left points 72pt/in", "task": "identify blank input rectangles; never modify original"})
            pending.append((page, future))
            if len(pending) >= workers:
                collect(*pending.popleft())
        while pending:
            collect(*pending.popleft())
    remaining = [page["page"] for page in pages[len(selected):]]
    if remaining:
        profile["warnings"].append(f"이미지 입력칸은 앞 {len(selected)}쪽만 읽었음. 나머지 쪽의 수동 확인이 필요함")
    result = analyze_template(path, manual_fields=proposed) if proposed else profile
    if not proposed:
        result["warnings"].append("이미지에서 확실한 빈 입력 영역을 찾지 못함. 기존 자동 후보를 직접 확인해야 함")
    result["warnings"] = list(dict.fromkeys(result["warnings"] + profile["warnings"]))
    result["image_analysis"] = {"analyzed_pages": [page["page"] for page in selected],
                                "remaining_pages": remaining, "complete": not remaining}
    return result


def learn_template(path, client=None, *, cache_dir=DEFAULT_CACHE, force=False, allow_images=True,
                   max_image_pages=3, image_workers=4, base_profile=None):
    """원본 위치를 바꾸지 않는 AI 매핑 제안. 미지 양식은 사용자 확인이 필요함."""
    source = Path(path)
    registered = load_form_profile(source) if base_profile is None else None
    if registered:
        return registered | {"learning": {"origin": "registered", "needs_confirmation": False, "engine_version": ENGINE_VERSION}}
    if not force:
        cached = load_learned_profile(source, cache_dir, repeat_expansion=(base_profile or {}).get('repeat_expansion'))
        if (cached and cached.get('repeat_expansion') == (base_profile or {}).get('repeat_expansion')
                and cached.get('repeat_source_profile') == (base_profile or {}).get('repeat_source_profile')):
            return cached
    if base_profile is None:
        profile = analyze_template(source)
    else:
        from templates.repeat_fields import repeat_profile
        binding = base_profile.get('repeat_expansion', {})
        expected = repeat_profile(source, binding.get('plan'), binding)
        if base_profile.get('repeat_source_profile'):
            from templates.repeat_rules import inherit_repeat_rules
            expected = inherit_repeat_rules(None, expected, base_profile['repeat_source_profile'], prepared_path=source)
        comparable = lambda value: {key: item for key, item in value.items() if key != 'repeat_rule_verification'}
        if comparable(base_profile) != comparable(expected):
            raise ValueError('반복 행 원본과 분석 기초 프로파일이 일치하지 않음')
        profile = deepcopy(expected)
    if max_image_pages is not None and (type(max_image_pages) is not int or max_image_pages < 1):
        raise ValueError("이미지 분석 쪽 수는 양의 정수 또는 전체 분석(None)이어야 함")
    if type(image_workers) is not int or not 1 <= image_workers <= 4:
        raise ValueError("이미지 요청 병렬 수는 1~4의 정수여야 함")
    if not profile.get("supported"):
        return profile | {"learning": {"origin": "unsupported", "needs_confirmation": True, "engine_version": ENGINE_VERSION}}
    if client is None:
        from llm.client import LLMClient
        client = LLMClient()
    if profile.get("format") == "pdf" and profile.get("render_mode") == "overlay" and allow_images:
        profile = _image_fields(source, profile, client, max_image_pages, image_workers)
    base = profile["fields"]
    if not base:
        profile["warnings"].append("입력칸을 찾지 못함. 사용자 영역 지정이 필요함")
        return profile | {"learning": {"origin": "no_fields", "needs_confirmation": True, "engine_version": ENGINE_VERSION}}
    known = {field["id"]: field for field in base}
    fields = []
    contexts = _contexts(source, base)
    for start in range(0, len(contexts), 64):
        batch = contexts[start:start + 64]
        response = client.generate_json("template", {"format": profile["format"], "source_sha256": profile["source_sha256"],
            "fields": batch, "task": "mapping_only_no_values", "batch_number": start // 64 + 1,
            "batch_count": (len(contexts) + 63) // 64, "total_field_count": len(contexts)})
        if not isinstance(response, dict) or not isinstance(response.get("fields"), list):
            raise ValueError("양식 분석 응답에는 fields 목록이 필요함")
        batch_ids = {field["id"] for field in batch}
        batch_fields = []
        for item in response["fields"]:
            if not isinstance(item, dict) or item.get("id") not in batch_ids:
                raise ValueError("미등록 입력칸 ID가 응답에 포함됨")
            if set(item) - {"id", "kind", "value_key", "required", "input_required", "input_mode", "max_chars", "confidence", "validation"}:
                raise ValueError("기입값·좌표·임의 속성을 양식 매핑 응답으로 허용하지 않음")
            batch_fields.append(known[item["id"]] | item)
        _check_fields(batch_fields, [known[field["id"]] for field in batch])
        if {field["id"] for field in batch_fields} != batch_ids:
            raise ValueError("응답에서 입력칸을 누락하면 안 됨")
        fields.extend(batch_fields)
    _check_fields(fields, base)
    if {field["id"] for field in fields} != set(known):
        raise ValueError("응답에서 입력칸을 누락하면 안 됨")
    for field in fields:
        field.setdefault("narrative_style_required", not (field["input_required"] or field.get('validation') or NUMBER.search(field["label"] + " " + field["value_key"]) or field.get("control_type")))
        if field["confidence"] < 0.75:
            profile["warnings"].append(f"낮은 확신도의 입력칸을 직접 확인해야 함: {field['label']}")
    overlays = [field for field in fields if field["kind"] == "pdf_overlay"]
    for index, left in enumerate(overlays):
        for right in overlays[index+1:]:
            if left["page"] == right["page"] and max(left["x"], right["x"]) < min(left["x"]+left["width"], right["x"]+right["width"]) and max(left["y"], right["y"]) < min(left["y"]+left["height"], right["y"]+right["height"]):
                profile["warnings"].append("PDF 입력 영역이 겹침. 위치를 직접 조정해야 함")
    profile = mapped_rule_profile(profile, {field['id']: field['value_key'] for field in fields})
    profile["fields"] = fields
    validate_rule_profile(profile)
    if profile.get('repeat_expansion'):
        from templates.repeat_rows import validate_repeat_fields
        validate_repeat_fields(source, profile)
    profile["learning"] = {"origin": "ai_proposal", "needs_confirmation": True, "engine_version": ENGINE_VERSION}
    return profile


def reusable_profile(candidate, authority):
    """Keep confirmed names while rejecting obsolete registered writing rules.

    authority is the current registered or prepared profile, never an older
    cached/proposed/resumed copy. Unknown PDF manual mappings have no authority
    and retain their existing explicit confirmation flow.
    """
    from templates.value_rules import _validation
    if not isinstance(candidate, dict) or not isinstance(authority, dict):
        return False
    if any(candidate.get(key) != authority.get(key) for key in
           ('source_sha256', 'format', 'repeat_expansion', 'repeat_source_profile')):
        return False
    if any(candidate.get(key) != authority[key] for key in
           ('domain', 'ra_workflow', 'business_workflow', 'office_workflow',
            'ra_product_name', 'ra_product_variant', 'product_name', 'product_variant')
           if key in authority):
        return False
    try:
        originals = {field['id']: field for field in authority['fields']}
        fields = candidate['fields']
        if not fields:
            return False
        mapping = {field['id']: field['value_key'] for field in fields}
        expected = mapped_rule_profile(authority, mapping)
        bases = deepcopy(authority['fields'])
        by_id = {field['id']: field for field in bases}
        for field in fields:
            original = originals[field['id']]
            if original.get('required') and not field.get('required'):
                return False
            if original.get('evidence_role') != field.get('evidence_role'):
                return False
            source_keys = [key for key in ('evidence_scope', 'evidence_document_labels') if key in original]
            if any(field.get(key) != original[key] for key in source_keys):
                return False
            if source_keys and field.get('label') != original.get('label'):
                return False
            if original.get('control_type') in {'choice', 'combobox', 'choice_unresolved', 'radio', 'checkbox'}:
                if any(field.get(key) != original.get(key) for key in
                       (*PDF_CHOICE_KEYS, 'xlsx_list')):
                    return False
            if original.get('validation'):
                registered = _validation(original['validation'])
                restored = _validation(field.get('validation'))
                if any(restored.get(key) != value for key, value in registered.items()):
                    return False
                # Rule editor roundtrips bounds as strings and explicit defaults;
                # their normalized contract above must still be identical.
                by_id[field['id']]['validation'] = deepcopy(field['validation'])
        _check_fields(fields, bases)
        validate_rule_profile(candidate)
        for name in ('relations', 'groups'):
            required = expected.get('constraints', {}).get(name, [])
            restored = candidate.get('constraints', {}).get(name, [])
            if any(item not in restored for item in required):
                return False
    except (KeyError, TypeError, ValueError):
        return False
    return True
