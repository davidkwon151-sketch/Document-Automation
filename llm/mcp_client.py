"""Resumable client-side inference; no HTTP, subscription credentials or fake model.

The MCP caller performs the requested inference in its own conversation. Answers
are client-supplied, not an attestation of which model or subscription was used.
Import the adapter through llm.client, the project's inference boundary.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import secrets
from pathlib import Path


class MCPInferenceError(ValueError):
    """Safe validation failure with no document or credential text."""


class PendingInference(BaseException):
    """Internal suspension, not a model error; bypass fail-closed error handlers.

    The MCP orchestration boundary must catch this explicitly. Generic Exception
    handlers in OCR and completeness must never turn a suspension into success.
    """

    def __init__(self, request: dict):
        super().__init__("Claude 연결 도구에서 작성 요청의 응답을 제공해야 함")
        self.request = _copy_json(request, limit=20 * 1024 * 1024)


def _copy_json(value, *, limit=4 * 1024 * 1024):
    def check(item, depth=0):
        if depth > 64:
            raise ValueError
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError
            for entry in item.values():
                check(entry, depth + 1)
        elif isinstance(item, list):
            for entry in item:
                check(entry, depth + 1)
        elif item is not None and type(item) not in (str, bool, int, float):
            raise ValueError
    try:
        check(value)
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > limit:
            raise ValueError
        return json.loads(encoded)
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise MCPInferenceError("연결 도구 입력은 크기 제한 안의 유효한 JSON이어야 함") from None


def _fingerprint(record):
    content = {key: record[key] for key in ("operation", "prompt_name", "instructions", "payload")}
    if "image" in record:
        content["image"] = {key: record["image"][key] for key in ("mime", "sha256")}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


class MCPInferenceClient:
    """Replay an unchanged pipeline until its next client-side inference request.

    A caller may answer only the last pending request. Replaying the whole
    pipeline binds earlier responses to the exact prompts and input content.
    assert_complete() must be called after a completed pipeline before export.
    """

    provider = "claude_mcp"
    model = "claude_mcp_client_supplied_unverified"
    embedding_model = "local-lexical-hash-v1"
    inference_provenance = "client_supplied_model_identity_unverified"
    sequential_inference = True

    def __init__(self, records=None, *, prompt_dir: str | Path | None = None):
        if records is None:
            records = []
        if not isinstance(records, list) or len(records) > 256:
            raise MCPInferenceError("연결 도구 작성 기록의 수가 유효하지 않음")
        records = _copy_json(records, limit=64 * 1024 * 1024)
        self._records = []
        self.prompt_dir = prompt_dir
        self._position = 0
        ids = set()
        for index, item in enumerate(records):
            item = _copy_json(item, limit=20 * 1024 * 1024)
            if (not isinstance(item, dict) or set(item) - {
                "request_id", "fingerprint", "operation", "prompt_name", "instructions", "payload", "image", "response"
            } or not {"request_id", "fingerprint", "operation", "prompt_name", "instructions", "payload"} <= set(item)
                or not isinstance(item["request_id"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", item["request_id"])
                or item["request_id"] in ids
                or item["operation"] not in {"generate_json", "read_image_json"}
                or not isinstance(item["prompt_name"], str)
                or not isinstance(item["instructions"], str) or not item["instructions"].strip()
                or not isinstance(item["payload"], dict)):
                raise MCPInferenceError("연결 도구 작성 기록이 유효하지 않음")
            _copy_json(item["payload"])
            if item["operation"] == "read_image_json":
                self._validate_image_record(item.get("image"))
            elif "image" in item:
                raise MCPInferenceError("작성 기록의 이미지 형식이 유효하지 않음")
            if item["fingerprint"] != _fingerprint(item):
                raise MCPInferenceError("작성 요청의 입력 지문이 일치하지 않음")
            if "response" in item:
                item["response"] = self._response(item["response"])
            elif index != len(records) - 1:
                raise MCPInferenceError("미완료 요청 뒤의 작성 기록은 사용할 수 없음")
            ids.add(item["request_id"])
            self._records.append(item)

    @property
    def records(self):
        return _copy_json(self._records, limit=64 * 1024 * 1024)

    @staticmethod
    def _response(response):
        if not isinstance(response, dict):
            raise MCPInferenceError("작성 응답은 JSON 객체 하나이어야 함")
        return _copy_json(response, limit=2 * 1024 * 1024)

    @staticmethod
    def _image(image, mime):
        signatures = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}
        if (not isinstance(image, bytes) or mime not in signatures
                or not image.startswith(signatures[mime]) or len(image) > 10 * 1024 * 1024):
            raise MCPInferenceError("이미지는 10 MiB 이하 PNG 또는 JPEG이어야 함")
        return {"mime": mime, "sha256": hashlib.sha256(image).hexdigest(),
                "data": base64.b64encode(image).decode("ascii")}

    @classmethod
    def _validate_image_record(cls, record):
        try:
            if not isinstance(record, dict) or set(record) != {"mime", "sha256", "data"}:
                raise ValueError
            image = base64.b64decode(record["data"], validate=True)
            if cls._image(image, record["mime"]) != record:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise MCPInferenceError("작성 기록의 이미지 지문이 유효하지 않음") from None

    def submit_response(self, request_id, fingerprint, response):
        if (not self._records or "response" in self._records[-1]
                or self._records[-1]["request_id"] != request_id
                or self._records[-1]["fingerprint"] != fingerprint):
            raise MCPInferenceError("현재 대기 중인 작성 요청과 응답이 일치하지 않음")
        self._records[-1]["response"] = self._response(response)

    def _request(self, operation, prompt_name, payload, image=None):
        # Runtime import keeps llm.client as the public boundary without a cycle.
        from llm.client import load_prompt
        if not isinstance(payload, dict) or not isinstance(prompt_name, str) or not prompt_name.strip():
            raise MCPInferenceError("작성 요청 입력은 JSON 객체이어야 함")
        prompt_name = prompt_name if Path(prompt_name).suffix else prompt_name + ".md"
        request = {"operation": operation, "prompt_name": prompt_name,
                   "instructions": load_prompt(prompt_name, self.prompt_dir),
                   "payload": _copy_json(payload)}
        if image is not None:
            request["image"] = image
        request["fingerprint"] = _fingerprint(request)
        if self._position < len(self._records):
            stored = self._records[self._position]
            if stored["fingerprint"] != request["fingerprint"]:
                raise MCPInferenceError("원자료·지시·프롬프트가 변경됨. 이전 작성 응답을 재사용할 수 없음")
            if "response" not in stored:
                raise PendingInference(stored)
            self._position += 1
            return self._response(stored["response"])
        if len(self._records) >= 256:
            raise MCPInferenceError("연결 도구 작성 요청 한도를 초과함")
        request["request_id"] = secrets.token_hex(16)
        self._records.append(request)
        raise PendingInference(request)

    def generate_json(self, prompt_name: str, payload: dict):
        return self._request("generate_json", prompt_name, payload)

    def read_image_json(self, image: bytes, *, mime="image/png", payload=None, prompt_name="ocr"):
        return self._request("read_image_json", prompt_name,
                             {} if payload is None else payload, self._image(image, mime))

    def assert_complete(self):
        if self._position != len(self._records) or any("response" not in item for item in self._records):
            raise MCPInferenceError("현재 작업에서 대조하지 않은 작성 응답이 남아 있음")

    def embed(self, texts):
        """Local lexical hashing only; never present these as model embeddings."""
        if not isinstance(texts, list) or len(texts) > 128:
            raise MCPInferenceError("검색 입력은 128개 이하의 문자열 목록이어야 함")
        vectors = []
        for text in texts:
            if not isinstance(text, str) or not text.strip() or len(text) > 1024 * 1024:
                raise MCPInferenceError("검색 입력은 분량 제한 안의 비어 있지 않은 문자열이어야 함")
            vector = [0.0] * 256
            words = re.findall(r"[가-힣A-Za-z0-9]+", text.lower())
            tokens = set(words) | {word[index:index + 2] for word in words for index in range(len(word) - 1)}
            for token in sorted(tokens):
                vector[int.from_bytes(hashlib.sha256(token.encode()).digest()[:2], "big") % 256] += 1.0
            norm = math.sqrt(sum(value * value for value in vector))
            vectors.append([value / norm if norm else value for value in vector])
        return vectors
