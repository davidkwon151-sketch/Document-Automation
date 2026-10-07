import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from llm.client import ConfigurationError, LLMClient, LLMError, load_prompt


@pytest.fixture
def prompt_dir(tmp_path):
    (tmp_path / "test.md").write_text("한국어 JSON 객체를 반환함", encoding="utf-8")
    return tmp_path


def response(text='{"제목":"결과보고"}', status="completed"):
    return {"id": "resp_mock", "object": "response", "created_at": 1, "model": "mock-model", "status": status,
            "output": [{"id": "msg_mock", "type": "message", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": text, "annotations": []}]}],
            "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}}


def test_generate_json_uses_prompt_timeout_and_token_log(prompt_dir, caplog):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response())

    llm = LLMClient(api_key="test-only", model="mock-model", prompt_dir=prompt_dir,
                    timeout=7, transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.INFO):
        assert llm.generate_json("test.md", {"지시": "비공개 입력"}) == {"제목": "결과보고"}
    body = json.loads(requests[0].content)
    assert body["text"]["format"] == {"type": "json_object"}
    assert body["store"] is False
    assert "한국어" in body["instructions"]
    assert requests[0].extensions["timeout"]["read"] == 7
    assert "total_tokens" in caplog.text
    assert "비공개 입력" not in caplog.text
    assert "test-only" not in caplog.text


def test_prompt_stem_name_uses_markdown_file(prompt_dir):
    llm = LLMClient(api_key="test", prompt_dir=prompt_dir,
                    transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response())))
    assert llm.generate_json("test", {}) == {"제목": "결과보고"}


def test_json_mode_explicit_input_requirement_preserves_payload(prompt_dir):
    payload = {"지시": "원문 유지", "input": "줄1\n줄2", "JSON": "사용자 원값"}
    original = dict(payload)
    def handler(request):
        body = json.loads(request.content)
        instruction, _, data = body['input'].partition('\n')
        if 'JSON' not in instruction:
            return httpx.Response(400, json={'error': {'message': 'input must mention JSON'}})
        assert json.loads(data) == original
        return httpx.Response(200, json=response())
    llm = LLMClient(api_key='test-only', prompt_dir=prompt_dir,
                    transport=httpx.MockTransport(handler))
    assert llm.generate_json('test', payload) == {'제목': '결과보고'}
    assert payload == original


def test_environment_file_and_explicit_override(prompt_dir, tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_EMBEDDING_MODEL"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / "example.env"
    env.write_text("OPENAI_API_KEY=file-test-key\nOPENAI_MODEL=file-model\nOPENAI_EMBEDDING_MODEL=file-embedding\n")
    llm = LLMClient(env_file=env, prompt_dir=prompt_dir, provider='openai')
    assert llm.model == "file-model"
    assert llm.embedding_model == "file-embedding"
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    assert LLMClient(env_file=env, provider='openai').model == "env-model"
    assert LLMClient(env_file=env, model="explicit", provider='openai').model == "explicit"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="API_KEY"):
        LLMClient(env_file=tmp_path / "missing.env", provider='openai')


def test_retries_rate_limit_once_and_honors_retry_after(prompt_dir):
    attempts = []
    sleeper = Mock()

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "quota", "type": "rate_limit_error"}})
        return httpx.Response(200, json=response())

    llm = LLMClient(api_key="test", prompt_dir=prompt_dir, max_retries=1, sleep=sleeper,
                    transport=httpx.MockTransport(handler))
    assert llm.generate_json("test.md", {})["제목"] == "결과보고"
    assert len(attempts) == 2
    sleeper.assert_called_once_with(3.0)
    assert llm.client.max_retries == 0


@pytest.mark.parametrize("hint,expected", [("300", 30.0), ("inf", 1), ("nan", 1), ("-5", 1), ("invalid", 1)])
def test_retry_after_is_finite_and_bounded(prompt_dir, hint, expected):
    attempts = []
    sleeper = Mock()

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"retry-after": hint}, json={"error": {"message": "quota", "type": "rate_limit_error"}})
        return httpx.Response(200, json=response())

    llm = LLMClient(api_key="test", prompt_dir=prompt_dir, max_retries=1, sleep=sleeper,
                    transport=httpx.MockTransport(handler))
    llm.generate_json("test", {})
    sleeper.assert_called_once_with(expected)


@pytest.mark.parametrize("status,kind,attempt_count", [(401, "authentication", 1), (403, "authentication", 1), (400, "api", 1), (429, "rate_limit", 2), (500, "api", 2)])
def test_api_errors_and_retry_limit(prompt_dir, status, kind, attempt_count):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "test error", "type": "test"}})

    llm = LLMClient(api_key="test", prompt_dir=prompt_dir, max_retries=1, sleep=lambda _: None,
                    transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as error:
        llm.generate_json("test.md", {})
    assert error.value.kind == kind
    assert len(calls) == attempt_count


def test_timeout_is_reported_and_retried(prompt_dir):
    attempts = []

    def handler(request):
        attempts.append(request)
        raise httpx.ReadTimeout("test timeout", request=request)

    llm = LLMClient(api_key="test", prompt_dir=prompt_dir, max_retries=1, sleep=lambda _: None,
                    transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as error:
        llm.generate_json("test.md", {})
    assert error.value.kind == "timeout"
    assert len(attempts) == 2


@pytest.mark.parametrize("text", ["not JSON", "[]", '"string"'])
def test_invalid_response_json(prompt_dir, text):
    llm = LLMClient(api_key="test", prompt_dir=prompt_dir,
                    transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response(text))))
    with pytest.raises(LLMError) as error:
        llm.generate_json("test.md", {})
    assert error.value.kind == "invalid_json"


def test_incomplete_response_rejected(prompt_dir):
    llm = LLMClient(api_key="test", prompt_dir=prompt_dir,
                    transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response(status="incomplete"))))
    with pytest.raises(LLMError) as error:
        llm.generate_json("test.md", {})
    assert error.value.kind == "incomplete"


def test_prompt_paths_and_payload_validation(prompt_dir, tmp_path):
    assert "한국어" in load_prompt("test.md", prompt_dir)
    for name in ("../outside.md", "test.txt", "missing.md"):
        with pytest.raises(ConfigurationError):
            load_prompt(name, prompt_dir)
    (prompt_dir / "empty.md").touch()
    with pytest.raises(ConfigurationError, match="비어"):
        load_prompt("empty.md", prompt_dir)
    llm = LLMClient(client=Mock(), prompt_dir=prompt_dir)
    with pytest.raises(ValueError, match="dict"):
        llm.generate_json("test.md", [])


def test_embeddings_keep_input_order_and_validate_output(caplog):
    def handler(request):
        assert request.url.path.endswith("/embeddings")
        return httpx.Response(200, json={"object": "list", "model": "embed-mock", "usage": {"prompt_tokens": 4, "total_tokens": 4},
                                       "data": [{"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
                                                {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}]})
    llm = LLMClient(api_key="test", transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.INFO):
        assert llm.embed(["첫 문단", "둘째 문단"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert "total_tokens" in caplog.text
    assert llm.embed([]) == []
    with pytest.raises(ValueError):
        llm.embed([""])
    llm.client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(data=[])))
    with pytest.raises(LLMError) as error:
        llm.embed(["자료"])
    assert error.value.kind == "invalid_embedding"
