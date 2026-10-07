"""Local provider contracts; MockTransport only, no real config or model access."""

import json
import logging

import httpx
import pytest

import llm.client as client_module
from llm.client import ConfigurationError, LLMClient, LLMError


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    monkeypatch.setattr(client_module, "dotenv_values", lambda *args, **kwargs: {})
    for name in ("LLM_PROVIDER", "LOCAL_LLM_BASE_URL", "LOCAL_LLM_MODEL",
                 "LOCAL_EMBEDDING_MODEL", "OPENAI_API_KEY", "OPENAI_MODEL",
                 "OPENAI_EMBEDDING_MODEL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def prompts(tmp_path):
    for name in ("test", "ocr", "draft"):
        (tmp_path / f"{name}.md").write_text("로컬 모델은 JSON 객체 하나를 반환함", encoding="utf-8")
    return tmp_path


def chat(content='{"제목":"로컬 보고"}', finish="stop", **message):
    return {"id": "chat_mock", "object": "chat.completion", "created": 1,
            "model": "test-local", "choices": [{"index": 0, "finish_reason": finish,
            "message": {"role": "assistant", "content": content, **message}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def local(prompts, handler=None, **kwargs):
    return LLMClient(provider="local", base_url="http://127.0.0.1:11434/v1",
                     model="test-local", embedding_model="test-embed", prompt_dir=prompts,
                     transport=httpx.MockTransport(handler or (lambda request: httpx.Response(200, json=chat()))),
                     **kwargs)


def test_generation_json_prompt_dummy_auth_timeout_and_safe_log(prompts, caplog):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=chat())

    llm = local(prompts, handler, api_key="synthetic-cloud-key-ignored")
    with caplog.at_level(logging.INFO):
        assert llm.generate_json("test", {"지시": "private-local-input"}) == {"제목": "로컬 보고"}
    request = requests[0]
    body = json.loads(request.content)
    assert request.url == "http://127.0.0.1:11434/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer local-no-key"
    assert body["model"] == "test-local"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert "로컬 모델" in body["messages"][0]["content"]
    assert json.loads(body["messages"][1]["content"]) == {"지시": "private-local-input"}
    assert request.extensions["timeout"]["read"] == 180
    assert "total_tokens" in caplog.text
    assert "private-local-input" not in caplog.text
    assert "synthetic-cloud-key-ignored" not in caplog.text


def test_explicit_timeout(prompts):
    requests = []
    llm = local(prompts, lambda request: (requests.append(request) or httpx.Response(200, json=chat())), timeout=7)
    llm.generate_json("test", {})
    assert requests[0].extensions["timeout"]["read"] == 7


def test_local_environment_without_cloud_key(prompts, monkeypatch):
    for key, value in {"LLM_PROVIDER": "local", "LOCAL_LLM_BASE_URL": "http://localhost:11434/v1",
                       "LOCAL_LLM_MODEL": "env-local", "LOCAL_EMBEDDING_MODEL": "env-embed"}.items():
        monkeypatch.setenv(key, value)
    requests = []
    llm = LLMClient(prompt_dir=prompts, transport=httpx.MockTransport(
        lambda request: (requests.append(request) or httpx.Response(200, json=chat()))))
    assert llm.model == "env-local"
    assert llm.embedding_model == "env-embed"
    llm.generate_json("test", {})
    assert requests[0].url.host == "127.0.0.1"
    assert requests[0].headers["authorization"] == "Bearer local-no-key"


def test_embedding_local_endpoint_and_index_order(prompts):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"object": "list", "model": "test-embed", "data": [
            {"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
            {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}],
            "usage": {"prompt_tokens": 2, "total_tokens": 2}})

    assert local(prompts, handler).embed(["첫 자료", "둘째 자료"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert requests[0].url.path == "/v1/embeddings"
    body = json.loads(requests[0].content)
    assert body["input"] == ["첫 자료", "둘째 자료"]
    assert body["model"] == "test-embed"


def test_image_reading_uses_local_multimodal_chat(prompts):
    requests = []
    llm = local(prompts, lambda request: (requests.append(request) or httpx.Response(200, json=chat('{"본문":"읽음"}'))))
    assert llm.read_image_json(b"\x89PNG\r\n\x1a\nlocal-fixture", payload={"page": 2}) == {"본문": "읽음"}
    body = json.loads(requests[0].content)
    content = body["messages"][1]["content"]
    assert content[0]["type"] == "text"
    assert json.loads(content[0]["text"]) == {"page": 2}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert requests[0].url.path == "/v1/chat/completions"


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1", "http://example.com/v1", "http://localhost.example.com/v1",
    "http://127.0.0.1.example.com/v1", "http://192.168.1.10:11434/v1", "http://0.0.0.0:11434/v1",
    "http://user:password@localhost:11434/v1", "http://localhost:11434/v1?target=cloud",
    "http://localhost:11434/v1#fragment", "http://2130706433:11434/v1", "ftp://localhost/v1",
])
def test_non_loopback_or_ambiguous_urls_rejected(prompts, url):
    with pytest.raises(ConfigurationError):
        LLMClient(provider="local", base_url=url, model="test-local", embedding_model="test-embed",
                  prompt_dir=prompts, transport=httpx.MockTransport(lambda request: pytest.fail("HTTP forbidden")))


@pytest.mark.parametrize("url", ["http://localhost:11434/v1", "http://127.0.0.1:11434/v1", "http://[::1]:11434/v1"])
def test_explicit_loopback_hosts_accepted(prompts, url):
    requests = []
    llm = LLMClient(provider="local", base_url=url, model="test-local", embedding_model="test-embed",
                    prompt_dir=prompts, transport=httpx.MockTransport(
                    lambda request: (requests.append(request) or httpx.Response(200, json=chat()))))
    assert llm.generate_json("test", {}) == {"제목": "로컬 보고"}
    assert len(requests) == 1


@pytest.mark.parametrize("argument,value", [("model", "large-cloud"), ("model", "large:cloud"),
                                            ("embedding_model", "embed-cloud"), ("embedding_model", "embed:cloud")])
def test_cloud_model_names_rejected(prompts, argument, value):
    values = {"model": "test-local", "embedding_model": "test-embed", argument: value}
    with pytest.raises(ConfigurationError):
        LLMClient(provider="local", base_url="http://127.0.0.1:11434/v1", prompt_dir=prompts,
                  transport=httpx.MockTransport(lambda request: pytest.fail("HTTP forbidden")), **values)


@pytest.mark.parametrize("missing", ["model", "embedding_model"])
def test_local_models_must_be_configured(prompts, missing):
    values = {"model": "test-local", "embedding_model": "test-embed"}
    values.pop(missing)
    with pytest.raises(ConfigurationError):
        LLMClient(provider="local", base_url="http://127.0.0.1:11434/v1", prompt_dir=prompts, **values)


@pytest.mark.parametrize("finish", ["length", "tool_calls", "content_filter", None])
def test_only_completed_stop_response_accepted(prompts, finish):
    with pytest.raises(LLMError):
        local(prompts, lambda request: httpx.Response(200, json=chat(finish=finish))).generate_json("test", {})


@pytest.mark.parametrize("body", [chat(content=None), chat(refusal="cannot comply"),
    chat(tool_calls=[{"id": "tool_mock", "type": "function", "function": {"name": "action", "arguments": "{}"}}]),
    {"id": "empty", "object": "chat.completion", "created": 1, "model": "test-local", "choices": []}])
def test_empty_refusal_or_tools_not_treated_as_success(prompts, body):
    with pytest.raises(LLMError):
        local(prompts, lambda request: httpx.Response(200, json=body)).generate_json("test", {})


@pytest.mark.parametrize("content", ["[]", "null", "not-json"])
def test_local_output_requires_json_object(prompts, content):
    with pytest.raises(LLMError):
        local(prompts, lambda request: httpx.Response(200, json=chat(content))).generate_json("test", {})


def test_redirect_is_not_followed_and_has_no_cloud_fallback(prompts):
    requests = []
    llm = local(prompts, lambda request: (requests.append(request) or httpx.Response(
        307, headers={"location": "https://api.openai.com/v1/chat/completions"})), max_retries=0)
    with pytest.raises(LLMError):
        llm.generate_json("test", {})
    assert len(requests) == 1
    assert requests[0].url.host == "127.0.0.1"


def test_http_client_ignores_proxy_environment_and_redirects(prompts, monkeypatch):
    captured = []
    real_client = httpx.Client

    class RecordingClient(real_client):
        def __init__(self, *args, **kwargs):
            captured.append(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(client_module.httpx, "Client", RecordingClient)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8888")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8888")
    local(prompts)
    assert captured[0].get("trust_env") is False
    assert captured[0].get("follow_redirects", False) is False


def test_draft_pipeline_uses_same_local_boundary_and_source_ids(prompts):
    from agent.draft import create_draft

    draft = {"제목": "성과 결과", "요약": "□ 성과를 달성함 [S1]", "본문": "□ 성과를 달성함 [S1]"}
    client = local(prompts, lambda request: httpx.Response(200, json=chat(json.dumps(draft, ensure_ascii=False))))
    brief = {"목적": "결과 보고", "보고 대상": "팀장", "보고서 유형": "결과보고서", "마감": "오늘",
             "분량": "1쪽", "부족한 정보": []}
    assert create_draft(brief, [{"source_id": "S1", "text": "성과를 달성함"}], client=client) == draft


def test_default_openai_keeps_responses_endpoint(prompts):
    requests = []
    response = {"id": "resp_mock", "object": "response", "created_at": 1, "model": "test-openai",
                "status": "completed", "output": [{"id": "msg_mock", "type": "message",
                "status": "completed", "role": "assistant", "content": [{"type": "output_text",
                "text": '{"제목":"기존 응답"}', "annotations": []}]}]}
    llm = LLMClient(api_key="synthetic-only", model="test-openai", prompt_dir=prompts,
                    transport=httpx.MockTransport(lambda request: (
                    requests.append(request) or httpx.Response(200, json=response))))
    assert llm.generate_json("test", {}) == {"제목": "기존 응답"}
    assert requests[0].url.path == "/v1/responses"
    assert json.loads(requests[0].content)["store"] is False


@pytest.mark.parametrize('status', [429, 503])
def test_local_temporary_failure_retries_only_local_server(prompts, status):
    requests, delays = [], []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(status, json={'error': {'type': 'rate_limit_error'}})
        return httpx.Response(200, json=chat())

    assert local(prompts, handler, max_retries=1, sleep=delays.append).generate_json('test', {}) == {'제목': '로컬 보고'}
    assert len(requests) == 2 and delays == [1]
    assert all(request.url.host == '127.0.0.1' for request in requests)


@pytest.mark.parametrize('kind', ['timeout', 'connection'])
def test_local_connection_failure_is_sanitized_and_has_no_fallback(prompts, kind):
    requests = []

    def handler(request):
        requests.append(request)
        error = httpx.ReadTimeout if kind == 'timeout' else httpx.ConnectError
        raise error('private-server-error-marker', request=request)

    with pytest.raises(LLMError) as caught:
        local(prompts, handler, max_retries=1, sleep=lambda _: None).generate_json('test', {})
    assert caught.value.kind == kind
    assert 'private-server-error-marker' not in str(caught.value)
    assert len(requests) == 2 and all(request.url.host == '127.0.0.1' for request in requests)


@pytest.mark.parametrize('code', ['rate_limit_exceeded', 'insufficient_quota', 'billing_limit_reached'])
def test_local_429_guidance_does_not_request_openai_credit(prompts, code):
    requests = []
    with pytest.raises(LLMError) as caught:
        local(prompts, lambda request: (requests.append(request) or httpx.Response(429,
            json={'error': {'code': code, 'message': 'private-error-message'}})),
            max_retries=1, sleep=lambda _: None).generate_json('test', {})
    assert '로컬 모델 서버' in str(caught.value)
    assert 'OpenAI' not in str(caught.value) and 'private-error-message' not in str(caught.value)
    assert len(requests) == (2 if code == 'rate_limit_exceeded' else 1)
