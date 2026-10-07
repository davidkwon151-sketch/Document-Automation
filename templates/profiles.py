"""원본 해시가 일치하는 공개 빈 양식에만 검증된 입력 위치를 적용함."""

from hashlib import file_digest
import json
from pathlib import Path
import re
from .value_rules import validate_rule_profile


PROFILE_DIRECTORY = Path(__file__).with_name("profiles")

PDF_CHOICE_KEYS = ('control_type', 'options', 'choice_items', 'allow_custom', 'multiselect',
                   'selection_encoding', 'pdf_choice_flags', 'pdf_blank_options')


def refresh_pdf_choice_metadata(path, profile):
    """Keep confirmed names/rules while binding choices to the current PDF source.

    Missing metadata from older profiles may be recovered from the same source;
    contradictory options or edit/multiple permissions are never accepted.
    """
    if Path(path).suffix.lower() != '.pdf' or not any(
            field.get('kind') == 'pdf_form' for field in profile.get('fields', [])):
        return profile
    from .compatibility import analyze_template
    original = {field['id']: field for field in analyze_template(path)['fields']}
    for field in profile['fields']:
        if field.get('kind') != 'pdf_form':
            continue
        native = original.get(field['id'])
        declared_choice = field.get('control_type') in {'choice', 'combobox', 'choice_unresolved'} or 'pdf_choice_flags' in field
        if native is None:
            if declared_choice:
                raise ValueError('PDF 선택 항목의 원본 위치와 종류를 확인할 수 없음')
            continue
        if native.get('control_type') not in {'choice', 'combobox', 'choice_unresolved'}:
            if not declared_choice:
                continue
            raise ValueError('PDF 선택 항목의 원본 위치와 종류를 확인할 수 없음')
        for key in PDF_CHOICE_KEYS:
            if key in field and field[key] != native.get(key):
                raise ValueError('PDF 원본 선택 목록·편집·다중 선택 권한을 바꿀 수 없음')
            if key in native:
                field[key] = native[key]
        field.update(input_required=True, input_mode='user_provided', narrative_style_required=False)
        field['required'] = bool(field.get('required')) or bool(native.get('required'))
    return profile


def load_form_profile(path: str | Path, entry_id: str | None = None) -> dict | None:
    """이름이 같아도 원본 내용이 다르면 반환하지 않음. 시험값은 자동 적용하지 않음."""
    source = Path(path)
    if not source.is_file():
        return None
    if entry_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", entry_id):
        raise ValueError("등록 양식 ID가 올바르지 않음")
    candidates = [PROFILE_DIRECTORY / f"{entry_id}.json"] if entry_id else sorted(PROFILE_DIRECTORY.glob("*.json"))
    with source.open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()
    for candidate in candidates:
        if not candidate.is_file():
            continue
        profile = json.loads(candidate.read_text(encoding="utf-8"))
        if profile.get("source_sha256") != digest or profile.get("format") != source.suffix.lower().lstrip("."):
            continue
        if profile.get("resource_kind") != "blank_form" or profile.get("schema_version") != 1:
            continue
        if not profile.get("fields") or len({field["id"] for field in profile["fields"]}) != len(profile["fields"]):
            raise ValueError("등록 양식의 필드 구조가 잘못됨")
        if source.suffix.lower() in {'.docx', '.xlsx'}:
            # Keep the approved locations; newly discovered controls need their own
            # confirmation. A registered profile cannot relax the source's list.
            from .compatibility import analyze_template
            observed = analyze_template(source)
            native = {field['id']: field for field in observed['fields'] if field.get('control_type')
                      in {'choice', 'combobox', 'choice_unresolved'}}
            for field in profile['fields']:
                if original := native.get(field['id']):
                    for key in ('kind', 'control_type', 'options', 'choice_items', 'allow_custom', 'xlsx_list'):
                        if key in original:
                            field[key] = original[key]
                    field.update(input_required=True, input_mode='user_provided', narrative_style_required=False)
        refresh_pdf_choice_metadata(source, profile)
        validate_rule_profile(profile)
        return profile
    return None
