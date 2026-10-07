"""Explicit source-origin policy; synthetic checks are not real model evaluation."""
from hashlib import sha256
from unittest.mock import Mock

from PIL import Image
import pytest

from agent.global_workflows import _global_sources
from agent.multimodal_intake import (_source_fingerprint, collect_multimodal, confirm_intake,
                                     generation_sources, validate_generation_source, validate_source_origin)
from agent.ra_change_document import _scoped_sources
from agent.ra_workflows import _bound_quote, prepare_ra_workflow


def legacy_source():
    text = '제품명: SyntheticDrugA\n제형·함량: 정제 10 mg\n주소: 서울'
    return {'source_id': 'SOriginal', 'filename': 'original.txt', 'page': 1,
            'document_sha256': sha256(text.encode()).hexdigest(), 'text': text}


def ra_profile():
    return {'domain': 'pharmaceutical_ra', 'ra_workflow': 'product_approval',
            'ra_product_name': 'SyntheticDrugA', 'document_kind': 'application',
            'fields': [{'id': 'drug', 'value_key': '제품명', 'label': '제품명',
                        'required': True, 'input_required': False, 'evidence_role': 'product_name'}]}


@pytest.mark.parametrize('key', ['origin', 'source_origin', 'source_type', 'source_kind', 'filename'])
@pytest.mark.parametrize('value', ['model', 'llm', 'ai', 'generated', 'model_generated', 'llm_generated',
                                  'ai_generated', 'model-generated', 'llm-generated', 'ai-generated',
                                  'mock', 'demo', 'user', 'user_input', 'user-input', '사용자 입력',
                                  'ai 생성', '모델 생성', ' \tMoDeL-Generated\n', ' AI 생성 '])
def test_declared_non_evidence_origin_is_blocked_in_every_shared_path(key, value):
    source = {**legacy_source(), key: value}
    candidate = {**source, 'requires_verification': False, 'uncertain_items': []}
    candidate['verification_fingerprint'] = _source_fingerprint(candidate)
    validators = [lambda: validate_source_origin(source),
                  lambda: validate_generation_source(candidate),
                  lambda: _bound_quote({'quote': 'SyntheticDrugA'}, source),
                  lambda: _global_sources([source]),
                  lambda: _scoped_sources([source], 'before'),
                  lambda: prepare_ra_workflow(ra_profile(), [source], {
                      '제품명': {'source_id': source['source_id'], 'quote': 'SyntheticDrugA'}})]
    for validate in validators:
        with pytest.raises(ValueError, match='실제 원자료'):
            validate()


def test_plain_legacy_file_sources_still_prepare_without_a_receipt():
    source = legacy_source()
    assert validate_source_origin(source)
    assert _bound_quote({'quote': 'SyntheticDrugA'}, source) == ('SyntheticDrugA', len('제품명: '))
    assert _global_sources([source])[source['source_id']] == source
    assert _scoped_sources([source], 'before')[0][0]['original_source_record'] == source
    result = prepare_ra_workflow(ra_profile(), [source], {
        '제품명': {'source_id': source['source_id'], 'quote': 'SyntheticDrugA'}})
    assert result['ready_for_output_check'] and result['actual_model_requests'] == 0
    assert not result['submission_ready']


def test_reviewed_image_ocr_stays_valid_as_source_extraction(tmp_path):
    path = tmp_path / 'original.png'
    Image.new('RGB', (120, 60), 'white').save(path)
    client = Mock()
    client.read_image_json.return_value = {'본문': legacy_source()['text'],
                                          '표 목록': [], '불확실한 항목': ['원본 대조']}
    intake = collect_multimodal([path], client=client, allow_ocr=True)
    assert not generation_sources(intake)
    intake = confirm_intake(intake, [{'source_id': source['source_id'],
                                      'fingerprint': source['verification_fingerprint']}
                                     for source in intake['sources']], confirmed=True)
    sources = generation_sources(intake)
    assert all(source['extraction_mode'] == 'image_reading' and validate_generation_source(source)
               for source in sources)
    result = prepare_ra_workflow(ra_profile(), sources, {'제품명': {
        'source_id': sources[0]['source_id'], 'quote': 'SyntheticDrugA'}})
    assert result['ready_for_output_check'] and result['actual_model_requests'] == 0
    assert _global_sources(sources)
    assert _scoped_sources(sources, 'before')[0]
    client.read_image_json.assert_called_once()
