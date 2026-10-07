"""Mock HTTP errors at the shared generation, embedding, and OCR boundary."""
import json
import logging
import traceback
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from openai import APIStatusError
import pytest

from llm.client import LLMClient, LLMError

SECRET = 'sk-private-test-only-must-not-be-displayed'
PRIVATE = '비공개 문서·신청인·진단 정보'


@pytest.fixture
def prompts(tmp_path):
    for name in ('test', 'ocr'):
        (tmp_path / (name + '.md')).write_text('JSON 객체를 반환함', encoding='utf-8')
    return tmp_path


def invoke(client, operation):
    if operation == 'embedding':
        return client.embed([PRIVATE])
    if operation == 'ocr':
        return client.read_image_json(b'\x89PNG\r\n\x1a\nmock-image', payload={'문서': PRIVATE})
    return client.generate_json('test', {'문서': PRIVATE})


def success(operation):
    if operation == 'embedding':
        return {'object': 'list', 'model': 'embed-mock', 'data': [
            {'object': 'embedding', 'index': 0, 'embedding': [1.0, 0.0]}],
            'usage': {'prompt_tokens': 2, 'total_tokens': 2}}
    return {'id': 'resp_mock', 'object': 'response', 'created_at': 1, 'model': 'mock-model',
            'status': 'completed', 'output': [{'id': 'msg_mock', 'type': 'message',
            'status': 'completed', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': '{"확인":"mock"}', 'annotations': []}]}],
            'usage': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}}


@pytest.mark.parametrize('operation', ['generation', 'embedding', 'ocr'])
@pytest.mark.parametrize('code,kind', [
    ('insufficient_quota', 'quota'), ('credit_balance_exhausted', 'quota'),
    ('billing_hard_limit_reached', 'billing_limit'), ('billing_limit_reached', 'billing_limit'),
    ('project_spend_limit_exceeded', 'billing_limit'),
    ('organization_spend_limit_exceeded', 'billing_limit'),
    ('organization_usage_limit_exceeded', 'billing_limit'),
    ('usage_limit_exceeded', 'billing_limit'), ('usage_limit_reached', 'billing_limit'),
])
def test_permanent_quota_or_billing_does_not_retry_even_with_retry_after(
        prompts, operation, code, kind, caplog):
    calls, sleeper = [], Mock()

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={'retry-after': '3'}, json={'error': {
            'code': code, 'type': 'insufficient_quota', 'message': SECRET + PRIVATE}})

    client = LLMClient(api_key=SECRET, model='mock-model', prompt_dir=prompts, max_retries=3,
                       sleep=sleeper, transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.INFO), pytest.raises(LLMError) as caught:
        invoke(client, operation)
    error = caught.value
    assert error.kind == kind and error.status_code == 429
    assert error.error_code == code and error.error_type == 'insufficient_quota'
    assert len(calls) == 1 and not sleeper.called and client.client.max_retries == 0
    assert 'LLM retry' not in caplog.text
    visible = str(error) + repr(error) + caplog.text + ''.join(traceback.format_exception(error))
    assert SECRET not in visible and PRIVATE not in visible
    assert error.__suppress_context__
    if code == 'credit_balance_exhausted':
        assert str(error) == 'OpenAI API 크레딧 잔액이 부족함. API 결제 설정에서 잔액·결제 상태를 확인한 뒤 다시 작성해 주세요.'


@pytest.mark.parametrize('operation', ['generation', 'embedding', 'ocr'])
@pytest.mark.parametrize('code', ['rate_limit_exceeded', 'slow_down', None])
def test_temporary_limits_keep_shared_retry_after_boundary(prompts, operation, code):
    calls, sleeper = [], Mock()

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={'retry-after': '3'}, json={'error': {
                'type': 'rate_limit_error', 'code': code, 'message': SECRET + PRIVATE}})
        return httpx.Response(200, json=success(operation))

    client = LLMClient(api_key=SECRET, prompt_dir=prompts, max_retries=1,
                       sleep=sleeper, transport=httpx.MockTransport(handler))
    assert invoke(client, operation)
    assert len(calls) == 2
    sleeper.assert_called_once_with(3.0)


@pytest.mark.parametrize('body', [
    {'error': {'error': {'code': 'credit_balance_exhausted', 'type': 'insufficient_quota'}}},
    {'error': {'code': 'organization_spend_limit_exceeded', 'type': 'rate_limit_error'}},
    {'code': 'insufficient_quota', 'type': 'rate_limit_error'},
    {'error': {'code': 'unknown_future_code', 'type': 'insufficient_quota'}},
])
def test_nested_body_and_broad_type_do_not_hide_permanent_quota(prompts, body):
    calls, sleeper = [], Mock()

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json=body)

    client = LLMClient(api_key=SECRET, prompt_dir=prompts, max_retries=2,
                       sleep=sleeper, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as caught:
        client.generate_json('test', {})
    assert caught.value.kind in {'quota', 'billing_limit'}
    assert len(calls) == 1 and not sleeper.called
    assert caught.value.error_code in {None, 'credit_balance_exhausted', 'insufficient_quota',
                                      'organization_spend_limit_exceeded'}


@pytest.mark.parametrize('body', [
    {'error': {'message': 'insufficient_quota ' + SECRET, 'code': SECRET, 'type': PRIVATE}},
    {'error': {'code': {'nested': 'insufficient_quota'}, 'type': ['rate_limit_error']}},
    {'error': {'code': 429, 'type': None}}, {'error': [SECRET]}, [PRIVATE], None,
])
def test_unknown_or_malformed_429_labels_are_safe_and_do_not_assert_a_cause(prompts, body, caplog):
    calls, sleeper = [], Mock()

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json=body)

    client = LLMClient(api_key=SECRET, prompt_dir=prompts, max_retries=1,
                       sleep=sleeper, transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.INFO), pytest.raises(LLMError) as caught:
        client.generate_json('test', {'문서': PRIVATE})
    error = caught.value
    assert error.kind == 'rate_limit' and error.status_code == 429
    assert error.error_code is None and error.error_type is None
    assert '원인을 확인하지 못함' in str(error) and '단정하지 않음' in str(error)
    assert len(calls) == 2
    sleeper.assert_called_once_with(1)
    assert SECRET not in str(error) + caplog.text and PRIVATE not in str(error) + caplog.text


def test_non_json_429_response_is_unknown_and_never_echoed(prompts):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, text='<html>' + SECRET + PRIVATE + '</html>')

    client = LLMClient(api_key=SECRET, prompt_dir=prompts, max_retries=0,
                       transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as caught:
        client.generate_json('test', {})
    assert caught.value.error_code is None and '원인을 확인하지 못함' in str(caught.value)
    assert SECRET not in str(caught.value) and PRIVATE not in str(caught.value) and len(calls) == 1


def test_sdk_flat_body_labels_are_used_without_provider_message(prompts):
    request = httpx.Request('POST', 'https://api.openai.com/v1/responses')
    response = httpx.Response(429, request=request, text='malformed ' + SECRET)
    exception = APIStatusError(SECRET + PRIVATE, response=response,
                               body={'code': 'credit_balance_exhausted', 'type': 'insufficient_quota'})
    fake = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=exception)))
    sleeper = Mock()
    client = LLMClient(client=fake, prompt_dir=prompts, sleep=sleeper, max_retries=3)
    with pytest.raises(LLMError) as caught:
        client.generate_json('test', {})
    assert caught.value.kind == 'quota' and caught.value.error_code == 'credit_balance_exhausted'
    assert fake.responses.create.call_count == 1 and not sleeper.called
    assert SECRET not in ''.join(traceback.format_exception(caught.value))


def test_non_429_error_keeps_only_safe_diagnostic_attributes(prompts):
    client = LLMClient(api_key=SECRET, prompt_dir=prompts, transport=httpx.MockTransport(
        lambda request: httpx.Response(400, json={'error': {
            'code': SECRET, 'type': 'invalid_request_error', 'message': PRIVATE}})))
    with pytest.raises(LLMError) as caught:
        client.generate_json('test', {})
    error = caught.value
    assert error.kind == 'api' and error.status_code == 400
    assert error.error_code is None and error.error_type == 'invalid_request_error'
    assert SECRET not in str(error) and PRIVATE not in ''.join(traceback.format_exception(error))
