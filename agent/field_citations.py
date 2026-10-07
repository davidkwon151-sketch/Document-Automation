"""Separate provenance from exact native values without changing either input.

Native export codes take precedence over citation syntax. Editable/direct
input needs its actual user-provided literal to disambiguate a trailing [S...].
Unknown citation IDs remain visible to the existing source validators.
"""

import json
import re

CITATION_PATTERN = re.compile(r"\[(S[A-Za-z0-9_-]+)\]")
_TRAILING = re.compile(r"\s*\[(S[A-Za-z0-9_-]+)\]\s*$")
_ONLY_CITATIONS = re.compile(r"(?:\s*\[S[A-Za-z0-9_-]+\])*\s*\Z")


def is_selection_field(field):
    return bool(field and field.get('control_type') in {'checkbox', 'radio', 'choice', 'combobox'})


def profile_field(profile, key):
    return next((field for field in (profile or {}).get('fields', [])
                 if field.get('value_key') == key), None)


def _native_literal(value, field):
    if not is_selection_field(field):
        return False
    options = field.get('options', [])
    if not field.get('multiselect'):
        return value in options
    try:
        selected = json.loads(value)
    except (ValueError, TypeError):
        return False
    return (isinstance(selected, list) and bool(selected)
            and all(isinstance(item, str) and item in options for item in selected)
            and len(set(selected)) == len(selected))


def split_field_citations(value, field=None, *, literal=None):
    """Return (plain value, citation IDs); exact codes and JSON arrays stay data.

    ``literal`` must come from actual explicit input/locked_fields, never a
    model response. Ordinary narrative keeps its existing inline citations.
    No unknown IDs are silently accepted or discarded.
    """
    if not isinstance(value, str):
        raise ValueError('양식 값은 문자열이어야 함')
    text = value.strip()
    if isinstance(literal, str) and literal.strip():
        actual = literal.strip()
        if text.startswith(actual) and _ONLY_CITATIONS.fullmatch(text[len(actual):]):
            return actual, CITATION_PATTERN.findall(text[len(actual):])
    candidate, removed = text, []
    while True:
        if _native_literal(candidate, field):
            return candidate, list(reversed(removed))
        match = _TRAILING.search(candidate)
        if match is None:
            break
        removed.append(match[1])
        candidate = candidate[:match.start()].rstrip()
    if is_selection_field(field):
        # Never join fragments into a valid option by erasing an internal ID.
        return candidate, CITATION_PATTERN.findall(value)
    return CITATION_PATTERN.sub('', value).strip(), CITATION_PATTERN.findall(value)


def field_citations(value, field=None, *, literal=None):
    return split_field_citations(value, field, literal=literal)[1]
