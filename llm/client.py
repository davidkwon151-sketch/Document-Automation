"""Only external LLM/embedding call site. No user content or credentials in logs."""

import json
import base64
import ipaddress
import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from llm.mcp_client import MCPInferenceClient, PendingInference, MCPInferenceError

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_DEFAULT_MODEL = "gemini-3.8-flash"
GEMINI_DEFAULT_EMBEDDING = "gemini-embedding-001"
QUOTA_CODES = {"insufficient_quota", "credit_balance_exhausted"}
BILLING_CODES = {"billing_hard_limit_reached", "billing_limit_reached", "usage_limit_reached",
                 "usage_limit_exceeded", "organization_spend_limit_exceeded",
                 "project_spend_limit_exceeded", "organization_usage_limit_exceeded"}
TEMPORARY_LIMIT_CODES = {"rate_limit_exceeded", "slow_down"}
SAFE_ERROR_CODES = QUOTA_CODES | BILLING_CODES | TEMPORARY_LIMIT_CODES | {
    "invalid_api_key", "invalid_json_schema", "unsupported_value", "invalid_value",
    "server_is_overloaded", "context_length_exceeded", "model_not_found"}
SAFE_ERROR_TYPES = {"insufficient_quota", "rate_limit_error", "invalid_request_error",
                    "authentication_error", "permission_error", "server_error",
                    "service_unavailable_error"}


def _api_error_fields(exc):
    """Retain only recognized labels, never provider messages or request data."""
    codes, types = [], []
    bodies = [getattr(exc, "body", None)]
    for values, label, allowed in ((codes, getattr(exc, "code", None), SAFE_ERROR_CODES),
                                   (types, getattr(exc, "type", None), SAFE_ERROR_TYPES)):
        if isinstance(label, str) and label in allowed:
            values.append(label)
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            bodies.append(response.json())
        except (ValueError, TypeError):
            pass
    for body in bodies:
        # The SDK can provide an unwrapped error or a nested HTTP error envelope.
        for _ in range(4):
            if not isinstance(body, dict):
                break
            for key, values, allowed in (("code", codes, SAFE_ERROR_CODES),
                                         ("type", types, SAFE_ERROR_TYPES)):
                label = body.get(key)
                if isinstance(label, str) and label in allowed:
                    values.append(label)
            body = body.get("error")
    # A permanent quota indication takes precedence over a broad rate-limit type.
    code = next((value for value in codes if value in QUOTA_CODES | BILLING_CODES),
                codes[0] if codes else None)
    error_type = "insufficient_quota" if "insufficient_quota" in types else types[0] if types else None
    return code, error_type


def _limit_error(code, error_type):
    if code == "credit_balance_exhausted":
        return "quota", "OpenAI API 크레딧 잔액이 부족함. API 결제 설정에서 잔액·결제 상태를 확인한 뒤 다시 작성해 주세요."
    if code in BILLING_CODES:
        return "billing_limit", "API 조직·프로젝트의 지출 또는 사용 한도에 도달함. API Platform의 사용량·한도를 확인하고 조정한 뒤 다시 실행해 주세요. 자동 재시도하지 않음."
    if code in QUOTA_CODES or error_type == "insufficient_quota":
        return "quota", "API 잔액 또는 할당량이 부족함. API Platform의 결제·크레딧·사용 한도와 선택 프로젝트를 확인한 뒤 다시 실행해 주세요. 자동 재시도하지 않음."
    if code in TEMPORARY_LIMIT_CODES or error_type == "rate_limit_error":
        return "rate_limit", "API 요청 속도 제한에 도달함. 잠시 기다린 뒤 다시 실행하고 동시 요청 수를 줄여 주세요."
    return "rate_limit", "API가 HTTP 429를 반환했으나 원인을 확인하지 못함. API Platform의 사용량·결제·한도를 확인한 뒤 다시 실행해 주세요. 잔액 부족이나 일시 제한으로 단정하지 않음."


class LLMError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "response", status_code=None,
                 error_code=None, error_type=None):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.error_code = error_code
        self.error_type = error_type


class ConfigurationError(LLMError):
    def __init__(self, message: str):
        super().__init__(message, kind="configuration")


def load_prompt(name: str, prompt_dir: str | Path | None = None) -> str:
    root = Path(prompt_dir or PROJECT_ROOT / "prompts").resolve()
    path = (root / name).resolve()
    if path.suffix != ".md" or not path.is_relative_to(root):
        raise ConfigurationError("프롬프트는 prompts/ 안의 .md 파일이어야 함")
    try:
        prompt = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError("프롬프트 파일을 읽을 수 없음") from exc
    if not prompt.strip():
        raise ConfigurationError("프롬프트가 비어 있음")
    return prompt


def _local_url(value: str) -> str:
    """The local backend must never send documents to a remote API or proxy."""
    try:
        url = urlsplit(value)
        host = url.hostname
        address = ipaddress.ip_address("127.0.0.1" if host == "localhost" else host)
        if (url.scheme not in {"http", "https"} or not address.is_loopback
                or url.username is not None or url.password is not None
                or url.query or url.fragment or url.path.rstrip("/") != "/v1"
                or (url.port is not None and url.port <= 0)):
            raise ValueError
        authority = f"[{address}]" if address.version == 6 else str(address)
        if url.port is not None:
            authority += f":{url.port}"
        return f"{url.scheme}://{authority}/v1"
    except (ValueError, TypeError, AttributeError):
        raise ConfigurationError("로컬 모델 주소는 인증 정보 없는 loopback HTTP(S) /v1 주소여야 함") from None


def _local_model(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"로컬 모델의 {label} 설정이 필요함")
    value = value.strip()
    if "cloud" in value.casefold().split(":") or "-cloud" in value.casefold():
        raise ConfigurationError("로컬 모드에는 클라우드 모델을 지정할 수 없음")
    return value


def _gemini_model(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", value):
        raise ConfigurationError("Gemini 모델명이 올바르지 않음")
    return value


class LLMClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        embedding_model: str | None = None,
        provider: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int = 2,
        prompt_dir: str | Path | None = None,
        env_file: str | Path | None = None,
        transport: httpx.BaseTransport | None = None,
        client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        config = {**dotenv_values(env_file or PROJECT_ROOT / ".env"), **os.environ}
        # Existing explicit OpenAI test/caller credentials retain their meaning.
        self.provider = provider or ("openai" if api_key is not None or client is not None else None) or config.get("LLM_PROVIDER") or "gemini"
        if self.provider not in {"openai", "local", "gemini"}:
            raise ConfigurationError("LLM_PROVIDER는 gemini, openai 또는 local이어야 함")
        timeout = timeout if timeout is not None else 180.0 if self.provider in {"local", "gemini"} else 30.0
        if not math.isfinite(timeout) or timeout <= 0 or max_retries < 0:
            raise ConfigurationError("타임아웃은 양수, 재시도 횟수는 0 이상이어야 함")
        if self.provider == "gemini":
            if base_url is not None:
                raise ConfigurationError("Gemini API 주소는 변경할 수 없음")
            self.model = _gemini_model(model or config.get("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL)
            self.embedding_model = _gemini_model(embedding_model or config.get("GEMINI_EMBEDDING_MODEL") or GEMINI_DEFAULT_EMBEDDING)
            configured_thinking = config.get("GEMINI_THINKING_LEVEL")
            if self.model.startswith("gemini-3"):
                self.thinking_level = configured_thinking or "low"
                if self.thinking_level not in {"low", "medium", "high"}:
                    raise ConfigurationError("GEMINI_THINKING_LEVEL은 low, medium 또는 high여야 함")
            else:
                if configured_thinking:
                    raise ConfigurationError("GEMINI_THINKING_LEVEL은 Gemini 3 모델에서만 사용함")
                self.thinking_level = None
            key = api_key if api_key is not None else config.get("GEMINI_API_KEY")
            endpoint = GEMINI_API
        elif self.provider == "local":
            endpoint = _local_url(base_url or config.get("LOCAL_LLM_BASE_URL") or "http://127.0.0.1:11434/v1")
            self.model = _local_model(model or config.get("LOCAL_LLM_MODEL"), "LOCAL_LLM_MODEL")
            self.embedding_model = _local_model(embedding_model or config.get("LOCAL_EMBEDDING_MODEL"), "LOCAL_EMBEDDING_MODEL")
            key = "local-no-key"
        else:
            if base_url is not None:
                raise ConfigurationError("base_url은 명시적 local 모드에서만 지정할 수 있음")
            endpoint = None
            self.model = model or config.get("OPENAI_MODEL") or "gpt-5-mini"
            self.embedding_model = embedding_model or config.get("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small"
            key = api_key if api_key is not None else config.get("OPENAI_API_KEY")
        self.max_retries = max_retries
        self.prompt_dir = prompt_dir
        self.sleep = sleep
        if client is None and (not key or not key.strip()):
            raise ConfigurationError(".env의 GEMINI_API_KEY를 설정해야 함" if self.provider == "gemini" else
                                     ".env의 OPENAI_API_KEY를 설정해야 함")
        if self.provider == "gemini":
            self.api_key = key
            self.client = client or httpx.Client(transport=transport, timeout=timeout,
                                                 trust_env=False, follow_redirects=False)
            return
        self.client = client or OpenAI(
            api_key=key,
            base_url=endpoint,
            timeout=timeout,
            max_retries=0,
            http_client=httpx.Client(transport=transport, timeout=timeout,
                                     trust_env=False, follow_redirects=False)
            if self.provider == "local" else httpx.Client(transport=transport, timeout=timeout) if transport else None,
        )

    def _call(self, operation: Callable[[], Any]) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                response = operation()
                usage = getattr(response, "usage", None)
                if usage is not None:
                    if hasattr(usage, "model_dump"):
                        usage = usage.model_dump()
                    LOGGER.info("LLM token usage model=%s usage=%s", getattr(response, "model", self.model), usage)
                return response
            except (APITimeoutError, APIConnectionError, APIStatusError,
                    httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as exc:
                status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
                kind = "timeout" if isinstance(exc, (APITimeoutError, httpx.TimeoutException)) else "connection"
                code, error_type = _api_error_fields(exc) if status is not None else (None, None)
                if status is not None:
                    kind = "authentication" if status in (401, 403) else "rate_limit" if status == 429 else "api"
                message = f"LLM 호출 실패 ({kind})"
                if status == 429:
                    kind, message = _limit_error(code, error_type)
                    if self.provider == "gemini":
                        message = "Gemini API가 할당량 또는 요청 속도 제한을 반환함. Google AI Studio의 사용량·한도를 확인해 주세요."
                    elif self.provider == "local":
                        message = ("로컬 모델 서버가 할당/사용 제한을 반환함. 서버 설정과 모델을 확인해 주세요. 자동 재시도하거나 유료 API로 전환하지 않음."
                                   if kind in {"quota", "billing_limit"} else
                                   "로컬 모델 서버 요청이 제한됨. 서버 상태와 처리 중인 요청을 확인하고 잠시 후 다시 실행해 주세요.")
                retryable = status is None or status in (408, 409, 429) or status >= 500
                if kind in {"quota", "billing_limit"}:
                    retryable = False
                if not retryable or attempt == self.max_retries:
                    raise LLMError(message, kind=kind, status_code=status,
                                   error_code=code, error_type=error_type) from None
                delay = min(2 ** attempt, 8)
                headers = getattr(getattr(exc, "response", None), "headers", {})
                hint = headers.get("retry-after")
                if hint:
                    try:
                        seconds = float(hint)
                        if math.isfinite(seconds):
                            delay = max(delay, seconds)
                    except ValueError:
                        try:
                            retry_at = parsedate_to_datetime(hint)
                            delay = max(delay, (retry_at - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError, OverflowError):
                            pass
                # Bound a server's Retry-After hint so interactive users can retry.
                delay = min(delay, 30.0)
                LOGGER.warning("LLM retry kind=%s attempt=%s delay=%s", kind, attempt + 1, delay)
                self.sleep(delay)
        raise AssertionError("unreachable")

    def generate_json(self, prompt_name: str, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("LLM 입력은 dict여야 함")
        prompt = load_prompt(prompt_name if Path(prompt_name).suffix else prompt_name + ".md", self.prompt_dir)
        if self.provider == "gemini":
            return self._gemini_json([{"text": json.dumps(payload, ensure_ascii=False)}], prompt)
        if self.provider == "local":
            return self._chat_json(prompt, json.dumps(payload, ensure_ascii=False))
        response = self._call(lambda: self.client.responses.create(
            model=self.model,
            instructions=prompt + "\n응답은 JSON 객체 하나만 반환함.",
            input="Return one JSON object.\n" + json.dumps(payload, ensure_ascii=False),
            text={"format": {"type": "json_object"}},
            store=False,
        ))
        return self._json_response(response)

    def read_image_json(self, image: bytes, *, mime="image/png", payload=None, prompt_name="ocr") -> dict:
        """Read a scanned page through the same timeout/retry/logging boundary."""
        signatures = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}
        if not isinstance(image, bytes) or mime not in signatures or not image.startswith(signatures[mime]):
            raise ValueError("유효한 PNG 또는 JPEG 이미지가 필요함")
        if len(image) > 10 * 1024 * 1024:
            raise ValueError("OCR 이미지는 10 MiB 이하이어야 함")
        prompt = load_prompt(prompt_name if Path(prompt_name).suffix else prompt_name + ".md", self.prompt_dir)
        encoded = base64.b64encode(image).decode("ascii")
        if self.provider == "gemini":
            return self._gemini_json([{"text": json.dumps(payload or {}, ensure_ascii=False)},
                                      {"inlineData": {"mimeType": mime, "data": encoded}}], prompt)
        if self.provider == "local":
            return self._chat_json(prompt, [
                {"type": "text", "text": json.dumps(payload or {}, ensure_ascii=False)},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}", "detail": "high"}},
            ])
        response = self._call(lambda: self.client.responses.create(
            model=self.model, instructions=prompt,
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": "Return one JSON object.\n" + json.dumps(payload or {}, ensure_ascii=False)},
                {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}", "detail": "high"},
            ]}], text={"format": {"type": "json_object"}}, store=False,
        ))
        return self._json_response(response)

    def _gemini_post(self, model, operation, body):
        def request():
            response = self.client.post(f"{GEMINI_API}/{model}:{operation}",
                headers={"x-goog-api-key": self.api_key}, json=body)
            response.raise_for_status()
            return response
        response = self._call(request)
        try:
            result = response.json()
        except (ValueError, TypeError):
            raise LLMError("Gemini 응답이 유효한 JSON이 아님", kind="invalid_json") from None
        if not isinstance(result, dict):
            raise LLMError("Gemini 응답 형식이 올바르지 않음", kind="invalid_json")
        usage = result.get("usageMetadata")
        if isinstance(usage, dict):
            LOGGER.info("LLM token usage provider=gemini model=%s input=%s output=%s",
                        model, usage.get("promptTokenCount"), usage.get("candidatesTokenCount"))
        return result

    def _gemini_json(self, parts, prompt):
        generation_config = {"responseMimeType": "application/json"}
        if self.thinking_level:
            generation_config["thinkingConfig"] = {"thinkingLevel": self.thinking_level}
        result = self._gemini_post(self.model, "generateContent", {
            "systemInstruction": {"parts": [{"text": prompt + "\n응답은 JSON 객체 하나만 반환함."}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation_config,
        })
        candidates = result.get("candidates")
        if not isinstance(candidates, list) or len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
            raise LLMError("Gemini 응답이 완료되지 않음", kind="incomplete")
        text_parts = candidates[0].get("content", {}).get("parts", [])
        if (not isinstance(text_parts, list) or not text_parts
                or any(not isinstance(part, dict) or ("text" not in part and not part.get("thought"))
                       or ("text" in part and not isinstance(part["text"], str))
                       for part in text_parts)):
            raise LLMError("Gemini 응답 형식이 올바르지 않음", kind="incomplete")
        content = "".join(part.get("text", "") for part in text_parts if not part.get("thought"))
        if not content:
            raise LLMError("Gemini 응답이 완료되지 않음", kind="incomplete")
        try:
            data = json.loads(content)
        except ValueError:
            raise LLMError("Gemini 응답이 유효한 JSON이 아님", kind="invalid_json") from None
        if not isinstance(data, dict):
            raise LLMError("Gemini 응답은 JSON 객체여야 함", kind="invalid_json")
        return data

    def _chat_json(self, prompt, content):
        response = self._call(lambda: self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": prompt + "\n응답은 JSON 객체 하나만 반환함."},
                      {"role": "user", "content": content}],
            response_format={"type": "json_object"},
        ))
        choices = getattr(response, "choices", None)
        if not choices or len(choices) != 1 or getattr(choices[0], "index", None) != 0:
            raise LLMError("로컬 모델 응답이 완료되지 않음", kind="incomplete")
        choice = choices[0]
        message = getattr(choice, "message", None)
        if (message is None or getattr(choice, "finish_reason", None) != "stop" or getattr(message, "refusal", None)
                or getattr(message, "tool_calls", None) or getattr(message, "function_call", None)):
            raise LLMError("로컬 모델 응답이 완료되지 않음", kind="incomplete")
        try:
            result = json.loads(getattr(message, "content", None))
        except (ValueError, TypeError):
            raise LLMError("로컬 모델 응답이 유효한 JSON이 아님", kind="invalid_json") from None
        if not isinstance(result, dict):
            raise LLMError("로컬 모델 응답은 JSON 객체여야 함", kind="invalid_json")
        return result

    @staticmethod
    def _json_response(response):
        if getattr(response, "status", "completed") != "completed":
            raise LLMError("LLM 응답이 완료되지 않음", kind="incomplete")
        try:
            result = json.loads(response.output_text)
        except (ValueError, TypeError, AttributeError) as exc:
            raise LLMError("LLM 응답이 유효한 JSON이 아님", kind="invalid_json") from exc
        if not isinstance(result, dict):
            raise LLMError("LLM 응답은 JSON 객체여야 함", kind="invalid_json")
        return result

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("임베딩 입력은 비어 있지 않은 문자열이어야 함")
        if self.provider == "gemini":
            result = self._gemini_post(self.embedding_model, "batchEmbedContents", {
                "requests": [{"model": "models/" + self.embedding_model,
                              "content": {"parts": [{"text": item}]}} for item in texts]})
            try:
                embeddings = result["embeddings"]
                vectors = [[float(value) for value in item["values"]] for item in embeddings]
                if (len(vectors) != len(texts) or not vectors or not vectors[0]
                        or len({len(vector) for vector in vectors}) != 1
                        or any(not math.isfinite(value) for vector in vectors for value in vector)):
                    raise ValueError
            except (KeyError, ValueError, TypeError):
                raise LLMError("유효하지 않은 Gemini 임베딩 응답임", kind="invalid_embedding") from None
            return vectors
        response = self._call(lambda: self.client.embeddings.create(model=self.embedding_model, input=texts))
        try:
            items = sorted(response.data, key=lambda item: item.index)
            if [item.index for item in items] != list(range(len(texts))):
                raise ValueError("임베딩 응답 개수 또는 인덱스가 다름")
            vectors = [[float(value) for value in item.embedding] for item in items]
            if not vectors[0] or len({len(vector) for vector in vectors}) != 1:
                raise ValueError("임베딩 차원이 잘못됨")
            if any(not math.isfinite(value) for vector in vectors for value in vector):
                raise ValueError("임베딩 값이 유한수가 아님")
        except (ValueError, TypeError, AttributeError, IndexError) as exc:
            raise LLMError("유효하지 않은 임베딩 응답임", kind="invalid_embedding") from exc
        return vectors
