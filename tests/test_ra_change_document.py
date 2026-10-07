"""Synthetic change evidence in an actual public original; no AI/KPI claim."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from agent.ra_change_document import prepare_ra_change_document, export_ra_change_document


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def registered():
    catalog = json.loads((ROOT / 'templates/ra_workflow_catalog.json').read_text(encoding='utf-8'))
    entry = next(row for row in catalog['workflows'] if row['id'] == 'variation')
    profile = json.loads((ROOT / entry['profile_path']).read_text(encoding='utf-8'))
    return entry, profile


def source(side, values=None, **updates):
    pairs = {'주소': '서울' if side == 'before' else '부산',
             '포장': '병' if side == 'before' else '상자',
             '보관조건': '냉장' if side == 'before' else '실온'}
    pairs.update(values or {})
    text = '제품명: SyntheticDrugA\n제형·함량: 정제 10 mg\n' + '\n'.join(key + ': ' + value for key, value in pairs.items())
    return {'source_id': 'S1', 'filename': side + '.txt', 'page': 1, 'text': text,
            'document_sha256': sha256((side + '\0' + text).encode()).hexdigest(), **updates}


def prepare(registered, **updates):
    options = dict(product_name='SyntheticDrugA', variant='정제 10 mg', selected_labels=['주소'],
                   confirmed_labels=['주소'], direct_reasons={'주소': '주소 변경 요청'},
                   direct_values={'신청인 성명': '합성 테스트'})
    options.update(updates)
    return prepare_ra_change_document(registered[1], [source('before')], [source('after')], **options)


def test_exact_confirmed_row_with_direct_reason_is_flat_source_bound_and_original_unchanged(registered):
    before = deepcopy(registered)
    result = prepare(registered)
    p = result['prepared']
    assert p['ready_for_output_check'], p['review']
    assert p['draft']['변경 전 1'].startswith('서울 [SChangebefore')
    assert p['draft']['변경 후 1'].startswith('부산 [SChangeafter')
    assert p['draft']['변경 항목 1'].startswith('주소 [SChangeafter')
    assert p['locked_fields']['변경 사유 1']['value'] == '주소 변경 요청'
    assert not p['draft']['변경 항목 2'] and not p['draft']['변경 항목 3']
    assert registered == before and not registered[1]['fields'][9]['input_required']
    source_ids = [row['source_id'] for row in p['sources']]
    assert len(source_ids) == len(set(source_ids))
    assert {row['comparison_side'] for row in result['sources']} == {'before', 'after'}
    assert result['row_count'] == 1 and result['row_capacity'] == 3
    assert result['actual_model_requests'] == 0 and not result['human_kpi_measured'] and not result['submission_ready']
    for item in p['evidence'].values():
        assert item['source']['text'][item['start']:item['end']] == item['quote']


def test_all_three_fixed_rows_retain_unique_source_positions_and_selected_order(registered):
    selected = ['포장', '주소', '보관조건']
    result = prepare(registered, selected_labels=selected, confirmed_labels=list(reversed(selected)),
                     direct_reasons={key: key + ' 변경 요청' for key in selected})
    assert result['prepared']['ready_for_output_check'], result['prepared']['review']
    assert [result['prepared']['evidence'][f'변경 항목 {index}']['quote'] for index in range(1, 4)] == selected
    assert len(result['prepared']['evidence']) == 10


@pytest.mark.parametrize('labels', [[], ['주소'] * 2, ['주소', '포장', '보관조건', '제조원'], [None]])
def test_fixed_row_limit_and_duplicate_or_empty_selection_rejected_without_truncating(registered, labels):
    with pytest.raises(ValueError, match='1~3개'):
        prepare(registered, selected_labels=labels, confirmed_labels=labels, direct_reasons={})


@pytest.mark.parametrize('confirmed', [[], ['포장'], ['주소', '주소']])
def test_every_selected_row_needs_its_own_explicit_confirmation(registered, confirmed):
    with pytest.raises(ValueError, match='담당자 확인'):
        prepare(registered, confirmed_labels=confirmed)


def test_missing_reason_remains_blank_blocks_export_and_group_is_not_relaxed(registered):
    result = prepare(registered, direct_reasons={})
    assert result['prepared']['review']['blocking']
    assert result['prepared']['draft']['변경 사유 1'] == ''
    assert any(row['code'] == 'form_group_required' for row in result['prepared']['review']['issues'])
    with pytest.raises(ValueError, match='사유'):
        export_ra_change_document(registered[0], result, record_kind='synthetic_test')


def test_source_reason_requires_exact_quote_and_declared_same_product_scope(registered):
    reason = source('reason', {'변경 사유': '주소 변경 요청'})
    result = prepare(registered, direct_reasons={}, reason_sources=[reason],
                     source_reasons={'주소': {'source_id': 'S1', 'quote': '주소 변경 요청'}})
    assert result['prepared']['ready_for_output_check'], result['prepared']['review']
    assert '변경 사유 1' not in result['prepared']['locked_fields']
    assert result['prepared']['evidence']['변경 사유 1']['source']['comparison_side'] == 'reason'
    reason['text'] = reason['text'].replace('SyntheticDrugA', 'OtherDrug')
    blocked = prepare(registered, direct_reasons={}, reason_sources=[reason],
                       source_reasons={'주소': {'source_id': 'S1', 'quote': '주소 변경 요청'}})
    assert not blocked['prepared']['ready_for_output_check']
    assert any(row['code'] == 'ra_change_reason_scope' for row in blocked['prepared']['review']['issues'])
    with pytest.raises(ValueError, match='인용'):
        prepare(registered, direct_reasons={}, reason_sources=[source('reason')],
                source_reasons={'주소': {'source_id': 'S1', 'quote': '자동으로 추정한 이유'}})


@pytest.mark.parametrize('change', ['identical', 'missing', 'ambiguous', 'other_product'])
def test_uncomparable_rows_cannot_be_promoted_to_form_change(registered, change):
    b, a = source('before'), source('after')
    if change == 'identical': a['text'] = b['text']
    if change == 'missing': a['text'] = a['text'].replace('주소: 부산', '소재지: 부산')
    if change == 'ambiguous': a['text'] += '\n주소: 제주'
    if change == 'other_product': a['text'] = a['text'].replace('SyntheticDrugA', 'OtherDrug')
    with pytest.raises(ValueError, match='모호'):
        prepare_ra_change_document(registered[1], [b], [a], product_name='SyntheticDrugA', variant='정제 10 mg',
                                   selected_labels=['주소'], confirmed_labels=['주소'], direct_reasons={'주소': '확인한 요청'})


def test_explicit_quote_with_no_actual_label_does_not_invent_item_name(registered):
    b, a = source('before'), source('after')
    b['text'] = b['text'].replace('주소: 서울', '소재지: 서울')
    a['text'] = a['text'].replace('주소: 부산', '소재지: 부산')
    result = prepare_ra_change_document(registered[1], [b], [a], product_name='SyntheticDrugA', variant='정제 10 mg',
        selected_labels=['주소'], confirmed_labels=['주소'], direct_reasons={'주소': '주소 변경 요청'},
        selections={'주소': {'before': {'source_id': 'S1', 'quote': '서울'}, 'after': {'source_id': 'S1', 'quote': '부산'}}})
    assert result['prepared']['draft']['변경 항목 1'] == ''
    assert any(row['code'] == 'ra_change_original_label_missing' for row in result['prepared']['review']['issues'])


@pytest.mark.parametrize('mutation', ['source', 'draft', 'reason', 'confirmed', 'position', 'fingerprint'])
def test_any_stale_preparation_or_value_or_mapping_blocks_export(registered, mutation):
    result = prepare(registered)
    if mutation == 'source': result['before_sources'][0]['text'] += '\n다른 내용'
    if mutation == 'draft': result['prepared']['draft']['변경 전 1'] = '다른 값'
    if mutation == 'reason': result['inputs']['direct_reasons']['주소'] = '다른 이유'
    if mutation == 'confirmed': result['inputs']['confirmed_labels'] = []
    if mutation == 'position': result['profile']['fields'][7]['x'] += 1
    if mutation == 'fingerprint': result['fingerprint'] = 'f' * 64
    with pytest.raises(ValueError):
        export_ra_change_document(registered[0], result, record_kind='synthetic_test')


def test_only_reason_permission_changes_and_long_reason_blocks_without_cropping(registered):
    result = prepare(registered, direct_reasons={'주소': '원문을 임의로 잘라내지 않아야 하는 매우 긴 사용자 변경 사유입니다'})
    assert result['prepared']['review']['blocking']
    assert result['prepared']['locked_fields']['변경 사유 1']['value'].endswith('사유입니다')
    assert any(row['code'] == 'ra_field_length' for row in result['prepared']['review']['issues'])
    original = {field['value_key']: field for field in registered[1]['fields']}
    for field in result['profile']['fields']:
        if field['value_key'] == '변경 사유 1':
            assert field == {**original[field['value_key']], 'input_required': True, 'input_mode': 'user_provided'}
        else:
            assert field == original[field['value_key']]


def test_public_original_actual_filling_independent_positions_and_sha_workpack(registered):
    """Real official blank PDF; synthetic values, not a completed RA application."""
    entry, _ = registered
    original = (ROOT / entry['source_path']).read_bytes()
    result = prepare(registered, selected_labels=['주소', '포장', '보관조건'],
                     confirmed_labels=['주소', '포장', '보관조건'],
                     direct_reasons={'주소': '주소 변경 요청', '포장': '포장 변경 요청', '보관조건': '보관 변경 요청'})
    export = export_ra_change_document(entry, result, record_kind='synthetic_test')
    proof = export['output_verification']
    assert proof['status'] == 'passed'
    assert proof['sha']['template'] == sha256(original).hexdigest()
    assert proof['sha']['output'] == sha256(export['document']).hexdigest()
    assert (ROOT / entry['source_path']).read_bytes() == original
    sidecar = json.loads(export['evidence'])
    assert sidecar['change_document']['row_count'] == 3
    assert sidecar['change_document']['direct_reason_keys'] == ['변경 사유 1', '변경 사유 2', '변경 사유 3']
    assert not sidecar['submission_ready'] and sidecar['actual_model_requests'] == 0
    summary = export['workpack']['summary']
    assert summary['registered_fields'] == 18 and summary['filled_fields'] == 14
    assert summary['attachments_pending'] > 0 and not summary['human_kpi_measured']
    with ZipFile(BytesIO(export['workpack']['zip_bytes'])) as archive:
        assert archive.read('document/' + export['filename']) == export['document']
        assert json.loads(archive.read('work-status.json')) == summary


def test_real_parser_chunks_identity_remapping_and_missing_reason_are_not_inferred(registered, tmp_path):
    from agent.retrieve import chunk_documents, load_documents
    paths = []
    for side in ('before', 'after'):
        path = tmp_path / (side + '.txt')
        path.write_text(source(side)['text'], encoding='utf-8')
        paths.append(path)
    b, a = [chunk_documents(load_documents([path], allow_ocr=False)) for path in paths]
    result = prepare_ra_change_document(registered[1], b, a, product_name='SyntheticDrugA', variant='정제 10 mg',
                                       selected_labels=['주소'], confirmed_labels=['주소'], direct_reasons={'주소': '주소 변경 요청'})
    assert result['prepared']['ready_for_output_check'], result['prepared']['review']
    assert {row['document_sha256'] for row in result['sources']} == {sha256(path.read_bytes()).hexdigest() for path in paths}


def test_confirmed_multimodal_receipt_preserved_before_alias_and_tampering_rejected(registered, tmp_path):
    from PIL import Image
    from agent.multimodal_intake import collect_multimodal, confirm_intake, generation_sources
    sources = []
    for index, side in enumerate(('before', 'after')):
        image = tmp_path / (side + '.png')
        Image.new('RGB', (20, 20), (255, 255, index)).save(image)
        digest = sha256(image.read_bytes()).hexdigest()
        intake = collect_multimodal([image], transcriptions={digest: [{'page': 1, 'text': source(side)['text']}]})
        confirmed = confirm_intake(intake, [{'source_id': item['source_id'], 'fingerprint': item['verification_fingerprint']}
                                           for item in intake['sources']], confirmed=True)
        sources.append(generation_sources(confirmed))
    options = dict(product_name='SyntheticDrugA', variant='정제 10 mg', selected_labels=['주소'],
                   confirmed_labels=['주소'], direct_reasons={'주소': '주소 변경 요청'})
    result = prepare_ra_change_document(registered[1], *sources, **options)
    assert result['prepared']['ready_for_output_check'], result['prepared']['review']
    assert all(item['original_source_record']['verification_receipt']['confirmed']
               for item in result['sources'])
    changed = deepcopy(sources)
    changed[0][0]['verification_receipt']['confirmed'] = False
    with pytest.raises(ValueError, match='확인 기록'):
        prepare_ra_change_document(registered[1], *changed, **options)
    changed = deepcopy(sources)
    changed[0][0]['filename'] = '다른 원자료.png'
    with pytest.raises(ValueError, match='변경됨'):
        prepare_ra_change_document(registered[1], *changed, **options)
