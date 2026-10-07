"""State and download trust boundaries without UI networking/native applications."""
from copy import deepcopy
from hashlib import sha256

import pytest

from app.global_workflow_ui import build_global_downloads, example_for_context, sync_global_outputs
from tests.test_global_workflows import setup


def test_example_stable_bytes_until_document_context_changes():
    state = {'gw_workflow': 'commercial_invoice', 'gw_rows': 2}
    example = example_for_context('commercial_invoice', 2, state)
    state.update(gw_exports={'document': b'old'}, gw_transaction='OLD', gw_direct_a='Old signer')
    assert example_for_context('commercial_invoice', 2, state) == example
    replacement = example_for_context('packing_list', 2, state)
    assert replacement != example
    assert all(key not in state for key in ('gw_exports', 'gw_transaction', 'gw_direct_a'))
    assert sha256(replacement['document']).hexdigest() == replacement['profile']['source_sha256']


def test_source_confirmation_change_invalidates_proposals_and_downloads():
    state = {'gw_source_signature': 'first', 'gw_proposal': {}, 'gw_prepared': {},
             'gw_prepare_signature': 'first', 'gw_exports': {'document': b'old'}}
    assert not sync_global_outputs(state, 'first') and 'gw_exports' in state
    assert sync_global_outputs(state, 'second')
    assert all(key not in state for key in ('gw_proposal', 'gw_exports', 'gw_prepared', 'gw_prepare_signature'))


def test_build_downloads_binds_exact_example_profile_with_only_selected_order_added(tmp_path):
    template, profile, sources, bindings = setup(tmp_path)
    original = deepcopy(profile); original.pop('global_transaction_id')
    example = {'document': template.read_bytes(), 'profile': original}
    assert build_global_downloads(example, profile, sources, bindings, {})['output_verification']['status'] == 'passed'
    profile['fields'][0]['required'] = False
    with pytest.raises(ValueError): build_global_downloads(example, profile, sources, bindings, {})


def test_tampered_example_bytes_block_before_any_fill(tmp_path):
    template, profile, sources, bindings = setup(tmp_path)
    original = deepcopy(profile); original.pop('global_transaction_id')
    example = {'document': template.read_bytes() + b'changed', 'profile': original}
    with pytest.raises(ValueError): build_global_downloads(example, profile, sources, bindings, {})
