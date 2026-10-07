from copy import deepcopy
from hashlib import sha256
from unittest.mock import Mock

import pytest
from PIL import Image

from agent.ctd_mapping import suggest_ctd_section_map
from agent.multimodal_intake import collect_multimodal, confirm_intake, generation_sources


def _intake(tmp_path):
    source = tmp_path / '품질기록.txt'
    source.write_text('제품명: 시험정\n제형: 정제\n함량: 10 mg\n'
                      '제조번호: B-001\n배치 분석 결과: 98.7 %\n'
                      '안정성 보관조건: 25℃ / 60% RH\n', encoding='utf-8')
    return collect_multimodal([source])


class Model:
    provider = 'mock'

    def __init__(self, select=None):
        self.calls = []
        self.select = select or (lambda source: None)

    def generate_json(self, name, payload):
        self.calls.append((name, payload))
        return {'assignments': [
            {'source_id': source['source_id'], 'section_id': self.select(source)}
            for source in payload['sources']
        ]}


def _suggest(intake, client, **kwargs):
    return suggest_ctd_section_map(intake, product_name='시험정',
                                   product_variant='정제 10 mg', client=client, **kwargs)


def test_headingless_quality_records_are_review_only_and_id_bound(tmp_path):
    intake = _intake(tmp_path)
    model = Model(lambda row: ('3.2.P.5.4' if '배치 분석 결과' in row['text'] else
                               '3.2.P.8.3' if '안정성 보관조건' in row['text'] else None))
    result = _suggest(intake, model, selected_sections=['3.2.P.5.4', '3.2.P.8.3'])
    by_text = {source['text']: source['source_id'] for source in generation_sources(intake)}
    assert result['section_map'] == {
        '3.2.P.5.4': [by_text['배치 분석 결과: 98.7 %']],
        '3.2.P.8.3': [by_text['안정성 보관조건: 25℃ / 60% RH']]}
    assert result['requires_confirmation'] and not result['submission_ready']
    assert result['mapping_origin'] == 'model_proposal'
    assert result['model_request_attempts'] == 1 and result['actual_model_requests'] == 0
    assert model.calls[0][0] == 'ctd_map'
    assert set(model.calls[0][1]['sources'][0]) == {'source_id', 'text', 'page', 'sheet', 'source_scope'}
    assert not any('98.7 %' in str(value) for value in result.values())


@pytest.mark.parametrize('bad', [
    lambda batch: {'assignments': batch[:-1]},
    lambda batch: {'assignments': batch + batch[:1]},
    lambda batch: {'assignments': [{**batch[0], 'source_id': 'Sunknown'}] + batch[1:]},
    lambda batch: {'assignments': [{**batch[0], 'section_id': '3.2.P.9'}] + batch[1:]},
    lambda batch: {'assignments': [{**batch[0], 'explanation': '99%'}] + batch[1:]},
])
def test_model_cannot_omit_duplicate_invent_or_add_facts(tmp_path, bad):
    intake = _intake(tmp_path)
    model = Model()
    def answer(name, payload):
        batch = [{'source_id': row['source_id'], 'section_id': None} for row in payload['sources']]
        return bad(batch)
    model.generate_json = answer
    with pytest.raises(ValueError):
        _suggest(intake, model)


def test_explicit_wrong_heading_or_other_product_is_rejected(tmp_path):
    path = tmp_path / '절기록.txt'
    path.write_text('제품명: 시험정\n제형: 정제\n함량: 10 mg\n'
                    '3.2.S.4: 원료 규격\n', encoding='utf-8')
    intake = collect_multimodal([path])
    model = Model(lambda row: '3.2.P.5' if '3.2.S.4' in row['text'] else None)
    with pytest.raises(ValueError, match='명시 CTD 절'):
        _suggest(intake, model, selected_sections=['3.2.S.4', '3.2.P.5'])
    with pytest.raises(ValueError, match='선택 제품'):
        suggest_ctd_section_map(intake, product_name='다른정', product_variant='정제 10 mg', client=model)


def test_unconfirmed_image_is_not_sent_and_source_tampering_blocks_model(tmp_path):
    text = tmp_path / '품질기록.txt'
    text.write_text('제품명: 시험정\n제형: 정제\n함량: 10 mg\n배치 분석 결과: 98.7 %', encoding='utf-8')
    photo = tmp_path / '사진.png'
    Image.new('RGB', (120, 60), 'white').save(photo)
    reader = Mock()
    reader.read_image_json.return_value = {'본문': '제품명: 시험정\n안정성 결과: 99.1 %',
                                           '표 목록': [], '불확실한 항목': []}
    intake = collect_multimodal([text, photo], client=reader, allow_ocr=True)
    model = Model()
    result = _suggest(intake, model)
    sent = {row['source_id'] for _, payload in model.calls for row in payload['sources']}
    assert sent.isdisjoint({source['source_id'] for source in intake['sources']
                            if source['filename'] == photo.name})
    assert result['source_count'] == len(generation_sources(intake))
    changed = deepcopy(intake)
    changed['sources'][0]['text'] = '배치 분석 결과: 100 %'
    with pytest.raises(ValueError, match='변경됨'):
        _suggest(changed, model)
    assert len(model.calls) == 1


def test_confirmed_photo_enters_proposal_and_receipt_changes_fingerprint(tmp_path):
    photo = tmp_path / '품질사진.png'
    Image.new('RGB', (120, 60), 'white').save(photo)
    reader = Mock()
    reader.read_image_json.return_value = {
        '본문': '제품명: 시험정\n제형: 정제\n함량: 10 mg\n배치 분석 결과: 98.7 %',
        '표 목록': [], '불확실한 항목': []}
    intake = collect_multimodal([photo], client=reader, allow_ocr=True)
    model = Model(lambda row: '3.2.P.5.4' if '배치 분석 결과' in row['text'] else None)
    with pytest.raises(ValueError, match='선택 제품'):
        _suggest(intake, model, selected_sections=['3.2.P.5.4'])
    assert not model.calls
    receipts = [{'source_id': source['source_id'],
                 'fingerprint': source['verification_fingerprint']}
                for source in intake['sources']]
    confirmed = confirm_intake(intake, receipts, confirmed=True)
    result = _suggest(confirmed, model, selected_sections=['3.2.P.5.4'])
    assert len(result['section_map']['3.2.P.5.4']) == 1
    assert len(result['proposal_fingerprint']) == 64
    assert result['requires_confirmation'] and not result['submission_ready']


def test_long_source_is_deferred_without_model_text_truncation(tmp_path):
    path = tmp_path / '장문기록.txt'
    path.write_text('제품명: 시험정\n제형: 정제\n함량: 10 mg\n'
                    '안정성 자료: ' + '가' * 1300, encoding='utf-8')
    intake = collect_multimodal([path])
    model = Model()
    result = _suggest(intake, model, selected_sections=['3.2.P.8.3'])
    assert result['deferred_sources']
    assert any('1200자' in row['reason'] or '중간에서 잘림' in row['reason']
               for row in result['deferred_sources'])
    assert not any('가' * 100 in str(payload)
                   for _, payload in model.calls)


def test_confirmed_headingless_api_coa_is_proposed_only_for_s_section(tmp_path):
    coa = tmp_path / 'api_coa.txt'
    coa.write_text('원료명: API-X\n배치번호: API-B-01\n함량 시험 결과: 99.5 %\n',
                   encoding='utf-8')
    finished = tmp_path / '완제기록.txt'
    finished.write_text('제품명: 시험정\n제형: 정제\n함량: 10 mg\n'
                        '배치 분석 결과: 98.7 %\n', encoding='utf-8')
    intake = collect_multimodal([coa, finished])
    digest = sha256(coa.read_bytes()).hexdigest()
    link = {digest: {'substance_name': 'API-X', 'product_name': '시험정', 'confirmed': True}}
    model = Model(lambda row: ('3.2.S.4' if '99.5 %' in row['text'] else
                               '3.2.P.5.4' if '98.7 %' in row['text'] else None))
    result = _suggest(intake, model, selected_sections=['3.2.S.4', '3.2.P.5.4'],
                      confirmed_substance_links=link)
    by_text = {source['text']: source['source_id'] for source in generation_sources(intake)}
    assert result['section_map'] == {
        '3.2.S.4': [by_text['함량 시험 결과: 99.5 %']],
        '3.2.P.5.4': [by_text['배치 분석 결과: 98.7 %']]}
    assert len(result['proposal_fingerprint']) == 64
    assert result['requires_confirmation'] and not result['submission_ready']
    assert result['model_request_attempts'] == 1
    assert {row['source_scope'] for row in model.calls[0][1]['sources']} == {
        'confirmed_substance_link', 'selected_product_document'}

    model.calls.clear()
    wrong = Model(lambda row: '3.2.P.5.4' if '99.5 %' in row['text'] else None)
    with pytest.raises(ValueError, match='3.2.S'):
        _suggest(intake, wrong, selected_sections=['3.2.S.4', '3.2.P.5.4'],
                 confirmed_substance_links=link)


@pytest.mark.parametrize('replacement', [
    lambda digest: {'0' * 64: {'substance_name': 'API-X', 'product_name': '시험정', 'confirmed': True}},
    lambda digest: {digest: {'substance_name': 'API-Y', 'product_name': '시험정', 'confirmed': True}},
    lambda digest: {digest: {'substance_name': 'API-X', 'product_name': '시험정', 'confirmed': False}},
])
def test_unconfirmed_or_conflicting_api_link_rejected_before_model(tmp_path, replacement):
    coa = tmp_path / 'api_coa.txt'
    coa.write_text('원료명: API-X\n함량 시험 결과: 99.5 %', encoding='utf-8')
    intake = collect_multimodal([coa])
    model = Model()
    with pytest.raises(ValueError):
        _suggest(intake, model, selected_sections=['3.2.S.4'],
                 confirmed_substance_links=replacement(sha256(coa.read_bytes()).hexdigest()))
    assert model.calls == []


def test_64_source_batches_are_complete_and_isolated(tmp_path):
    path = tmp_path / '긴기록.txt'
    path.write_text('제품명: 시험정\n제형: 정제\n함량: 10 mg\n' +
                    '\n'.join(f'시험 결과 {number:03d}: 98.7 %' for number in range(130)),
                    encoding='utf-8')
    intake = collect_multimodal([path])
    model = Model()
    result = _suggest(intake, model, selected_sections=['3.2.P.5.4'])
    assert result['model_request_attempts'] == 3
    batches = [{row['source_id'] for row in payload['sources']} for _, payload in model.calls]
    assert [len(batch) for batch in batches] == [64, 64, 5]
    assert not batches[0] & batches[1] and not batches[1] & batches[2]
    first_id = next(iter(batches[0]))
    def foreign(name, payload):
        entries = [{'source_id': row['source_id'], 'section_id': None}
                   for row in payload['sources']]
        if len(model.calls) > 1:
            entries[0]['source_id'] = first_id
        model.calls.append((name, payload))
        return {'assignments': entries}
    model.calls.clear()
    model.generate_json = foreign
    with pytest.raises(ValueError, match='다른 배치'):
        _suggest(intake, model, selected_sections=['3.2.P.5.4'])


def test_model_failure_has_no_mock_or_paid_fallback(tmp_path):
    intake = _intake(tmp_path)
    model = Model()
    model.generate_json = Mock(side_effect=RuntimeError('model unavailable'))
    with pytest.raises(RuntimeError, match='model unavailable'):
        _suggest(intake, model)
    model.generate_json.assert_called_once()
