"""Source scope contracts invalidate semantic approval without runtime fixture files."""

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from agent.grounding import evidence_fingerprint
from tests.test_ra_source_scope_cache import profile


@pytest.mark.parametrize('key', ['evidence_document_labels', 'evidence_scope', 'label', 'value_key', 'id'])
@pytest.mark.parametrize('operation', ['remove', 'change'])
def test_scope_rules_and_field_binding_invalidate_semantic_review(key, operation):
    first = profile()
    second = deepcopy(first)
    field = second['fields'][0]
    if operation == 'remove':
        field.pop(key)
    else:
        field[key] = ['설명서'] if key == 'evidence_document_labels' else '다른 항목/제약'
    draft = {'임상시험 목적': '합성 검토임 [STestScope]'}
    assert evidence_fingerprint(draft, [], template_profile=first) != evidence_fingerprint(draft, [], template_profile=second)


def test_scope_copy_is_stable():
    first = profile()
    assert evidence_fingerprint({}, [], template_profile=first) == evidence_fingerprint({}, [], template_profile=deepcopy(first))


def test_profiles_without_scope_keep_existing_fingerprint_contract():
    first = profile()
    for field in first['fields']:
        field.pop('evidence_scope', None)
        field.pop('evidence_document_labels', None)
    payload = {'draft': {}, 'sources': []}
    expected = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert evidence_fingerprint({}, [], template_profile=first) == expected
