"""Gemini BYOK contracts use mock HTTP only; no account or network access."""
import json
import logging

import httpx
import pytest

from llm.client import ConfigurationError, LLMClient, LLMError


def answer(text='{"제목":"검토 초안"}', finish='STOP'):
    return {'candidates': [{'finishReason': finish, 'content': {'parts': [{'text': text}]}}],
            'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 5}}


def client(tmp_path, handler, **kwargs):
    (tmp_path / 'draft.md').write_text('원자료에 없는 사실은 쓰지 않음', encoding='utf-8')
    (tmp_path / 'ocr.md').write_text('원문 문자 그대로 읽음', encoding='utf-8')
    return LLMClient(provider='gemini', api_key='test-gemini-secret-0123456789',
                     model='gemini-test', embedding_model='embed-test', prompt_dir=tmp_path,
                     transport=httpx.MockTransport(handler), **kwargs)


def test_gemini_json_uses_server_header_prompt_and_safe_usage(tmp_path, caplog):
    seen = []
    llm = client(tmp_path, lambda request: (seen.append(request) or httpx.Response(200, json=answer())))
    with caplog.at_level(logging.INFO):
        assert llm.generate_json('draft', {'private': '비공개 원문'}) == {'제목': '검토 초안'}
    request = seen[0]
    assert request.url == 'https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent'
    assert request.headers['x-goog-api-key'] == 'test-gemini-secret-0123456789'
    assert request.extensions['timeout']['read'] == 180
    body = json.loads(request.content)
    assert body['generationConfig']['responseMimeType'] == 'application/json'
    assert '원자료' in body['systemInstruction']['parts'][0]['text']
    assert json.loads(body['contents'][0]['parts'][0]['text']) == {'private': '비공개 원문'}
    assert 'input=10 output=5' in caplog.text
    assert '비공개 원문' not in caplog.text and 'test-gemini-secret' not in caplog.text


def test_gemini_3_uses_fast_supported_thinking_level_and_allows_explicit_quality_setting(tmp_path):
    (tmp_path / 'draft.md').write_text('출처를 확인함', encoding='utf-8')
    seen = []
    transport = httpx.MockTransport(lambda request: (seen.append(request) or httpx.Response(200, json=answer())))
    env = tmp_path / 'settings.env'
    env.write_text('', encoding='utf-8')
    llm = LLMClient(provider='gemini', api_key='test-gemini-secret-0123456789',
                    prompt_dir=tmp_path, env_file=env, transport=transport)
    llm.generate_json('draft', {})
    assert json.loads(seen[-1].content)['generationConfig']['thinkingConfig'] == {'thinkingLevel': 'low'}
    env.write_text('GEMINI_THINKING_LEVEL=medium\n', encoding='utf-8')
    llm = LLMClient(provider='gemini', api_key='test-gemini-secret-0123456789',
                    prompt_dir=tmp_path, env_file=env, transport=transport)
    llm.generate_json('draft', {})
    assert json.loads(seen[-1].content)['generationConfig']['thinkingConfig'] == {'thinkingLevel': 'medium'}


def test_gemini_image_and_embedding_paths(tmp_path):
    seen = []
    def handler(request):
        seen.append(request)
        if request.url.path.endswith(':batchEmbedContents'):
            return httpx.Response(200, json={'embeddings': [{'values': [1, 0]}, {'values': [0, 1]}]})
        return httpx.Response(200, json=answer('{"본문":"읽음"}'))
    llm = client(tmp_path, handler)
    assert llm.read_image_json(b'\x89PNG\r\n\x1a\ntest', payload={'page': 1}) == {'본문': '읽음'}
    parts = json.loads(seen[0].content)['contents'][0]['parts']
    assert parts[1]['inlineData']['mimeType'] == 'image/png'
    assert parts[1]['inlineData']['data'].startswith('iVBOR')
    assert llm.embed(['첫 자료', '둘째 자료']) == [[1.0, 0.0], [0.0, 1.0]]
    body = json.loads(seen[1].content)
    assert [r['content']['parts'][0]['text'] for r in body['requests']] == ['첫 자료', '둘째 자료']
    assert all(r['model'] == 'models/embed-test' for r in body['requests'])


def test_gemini_is_default_without_explicit_legacy_key_and_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv('LLM_PROVIDER', raising=False)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    env = tmp_path / 'settings.env'
    env.write_text('OPENAI_API_KEY=legacy-key-only\n', encoding='utf-8')
    with pytest.raises(ConfigurationError, match='GEMINI_API_KEY'):
        LLMClient(env_file=env)


def test_explicit_legacy_key_keeps_openai_when_env_defaults_to_gemini(tmp_path):
    env = tmp_path / 'settings.env'
    env.write_text('LLM_PROVIDER=gemini\nGEMINI_API_KEY=gemini-key\n', encoding='utf-8')
    llm = LLMClient(api_key='legacy-openai-key', env_file=env,
                    transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})))
    assert llm.provider == 'openai'


@pytest.mark.parametrize('body', [answer('[]'), answer('not-json'), answer(finish='MAX_TOKENS'),
                                  {'candidates': []}])
def test_invalid_or_partial_gemini_response_is_not_accepted(tmp_path, body):
    llm = client(tmp_path, lambda _: httpx.Response(200, json=body))
    with pytest.raises(LLMError):
        llm.generate_json('draft', {})


def test_gemini_rate_limit_is_safe_and_does_not_call_openai(tmp_path):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(429, json={'error': {'message': 'sensitive provider message'}})
    llm = client(tmp_path, handler, max_retries=0)
    with pytest.raises(LLMError) as error:
        llm.generate_json('draft', {})
    assert error.value.kind == 'rate_limit'
    assert 'sensitive' not in str(error.value)
    assert len(seen) == 1 and seen[0].url.host == 'generativelanguage.googleapis.com'
