"""Large forms retain source quotes and global positions across review batches."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from agent.grounding import evidence_fingerprint, inspect_grounding
from agent.pipeline import review_result


def large_draft(count):
    return {'제목': 'RA 자료 검토', '요약': '',
            '본문': '\n'.join('□ 보관 온도 25°C임 [S1]' for _ in range(count))}


def sources():
    return [{'source_id': 'S1', 'text': '보관 온도 25°C임', 'filename': '원자료.pdf',
             'page': 3, 'sheet': None, 'location': '보관 조건'}]


def supported(payload):
    return {'claims': [
        {'field': item['field'], 'line': item['line'], 'status': 'supported',
         'evidence': [{'source_id': 'S1', 'quote': '보관 온도 25°C임'}]}
        for item in payload['claims']]}


@pytest.mark.parametrize('count', [0, 1, 160, 161, 320, 351])
def test_large_review_batches_keep_every_original_line_and_source(count):
    draft, evidence = large_draft(count), sources()
    before = deepcopy((draft, evidence))
    client = Mock()
    client.generate_json.side_effect = lambda name, payload: supported(payload)
    result = inspect_grounding(draft, evidence, client)
    calls = client.generate_json.call_args_list
    assert len(calls) == result['batch_count'] == (count + 159) // 160
    assert result['claim_count'] == result['reviewed_count'] == count
    assert [item['line'] for item in result['claims']] == list(range(1, count + 1))
    assert all(len(call.args[1]['claims']) <= 160 for call in calls)
    assert all(call.args[1]['sources'] == evidence for call in calls)
    assert all(call.args[0] == 'grounding' for call in calls)
    assert result['fingerprint'] == evidence_fingerprint(draft, evidence)
    assert not result['blocking'] and result['warnings'] == []
    assert (draft, evidence) == before


def test_batches_cross_fields_without_resetting_per_field_line_numbers():
    draft = large_draft(159)
    draft['성분'] = '\n'.join('보관 온도 25°C임 [S1]' for _ in range(4))
    client = Mock()
    client.generate_json.side_effect = lambda name, payload: supported(payload)
    proof = inspect_grounding(draft, sources(), client)
    second = client.generate_json.call_args_list[1].args[1]['claims']
    assert [(item['field'], item['line']) for item in second] == [
        ('성분', 2), ('성분', 3), ('성분', 4)]
    assert proof['reviewed_count'] == 163 and not proof['blocking']


@pytest.mark.parametrize('omitted_line', [1, 160, 161, 321, 351])
def test_any_batch_omission_blocks_complete_proof(omitted_line):
    client = Mock()

    def response(name, payload):
        answer = supported(payload)
        answer['claims'] = [item for item in answer['claims'] if item['line'] != omitted_line]
        return answer

    client.generate_json.side_effect = response
    proof = inspect_grounding(large_draft(351), sources(), client)
    assert proof['blocking'] and proof['reviewed_count'] == 350
    assert proof['warnings'] == [
        {'code': 'semantic_missing', 'field': '본문', 'line': omitted_line,
         'severity': 'error', 'message': f'본문 {omitted_line}줄: 의미 검수 응답에서 누락됨'}]


@pytest.mark.parametrize('mutation', ['first_batch_repeated', 'future_batch', 'renumbered',
                                    'duplicate', 'boolean_line', 'invalid_response'])
def test_wrong_batch_or_invalid_later_response_is_never_partial_success(mutation):
    client = Mock()
    calls = 0

    def response(name, payload):
        nonlocal calls
        calls += 1
        answer = supported(payload)
        if mutation == 'future_batch' and calls == 1:
            answer['claims'][0]['line'] = 321
        if calls == 2:
            if mutation in {'first_batch_repeated', 'renumbered'}:
                answer['claims'][0]['line'] = 1
            elif mutation == 'duplicate':
                answer['claims'].append(deepcopy(answer['claims'][0]))
            elif mutation == 'boolean_line':
                answer['claims'][0]['line'] = True
            elif mutation == 'invalid_response':
                return {'claims': None}
        return answer

    client.generate_json.side_effect = response
    with pytest.raises(ValueError):
        inspect_grounding(large_draft(351), sources(), client)
    assert calls <= 2


@pytest.mark.parametrize('bad_line', [1, 161, 321])
@pytest.mark.parametrize('mutation', ['unsupported', 'unclear', 'wrong_quote', 'wrong_id', 'empty_quote'])
def test_each_batch_keeps_exact_citation_and_meaning_guards(bad_line, mutation):
    client = Mock()

    def response(name, payload):
        answer = supported(payload)
        for claim in answer['claims']:
            if claim['line'] != bad_line:
                continue
            if mutation in {'unsupported', 'unclear'}:
                claim['status'] = mutation
            elif mutation == 'wrong_id':
                claim['evidence'][0]['source_id'] = 'S2'
            else:
                claim['evidence'][0]['quote'] = '' if mutation == 'empty_quote' else '보관 온도 35°C임'
        return answer

    client.generate_json.side_effect = response
    proof = inspect_grounding(large_draft(351), sources(), client)
    assert proof['blocking'] and proof['reviewed_count'] == 351
    assert [(item['code'], item['line']) for item in proof['warnings']] == [
        ('semantic_grounding', bad_line)]


def test_large_proof_cannot_hide_changed_sources_in_pipeline_review():
    draft = large_draft(161)
    draft['요약'] = '□ 보관 온도 25°C임 [S1]'
    client = Mock()
    client.generate_json.side_effect = lambda name, payload: supported(payload)
    evidence = sources()
    proof = inspect_grounding(draft, evidence, client)
    result = {'draft': draft, 'sources': evidence, 'grounding': proof, 'semantic_required': True}
    assert not review_result(result)['blocking']
    evidence[0]['page'] = 4
    checked = review_result(result)
    assert checked['blocking']
    assert any(item['code'] == 'semantic_stale' for item in checked['warnings'])


def test_transport_error_in_last_batch_does_not_return_partial_proof():
    client = Mock()
    calls = 0

    def response(name, payload):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise TimeoutError('mock timeout')
        return supported(payload)

    client.generate_json.side_effect = response
    with pytest.raises(TimeoutError, match='mock timeout'):
        inspect_grounding(large_draft(351), sources(), client)
    assert calls == 3
