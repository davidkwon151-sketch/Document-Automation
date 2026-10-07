"""Offline tests for resumable client inference, not real Claude model tests."""

from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image

from llm.mcp_client import MCPInferenceClient, MCPInferenceError, PendingInference


@pytest.fixture
def prompts(tmp_path):
    (tmp_path / "step.md").write_text("원자료를 근거로 JSON만 작성함", encoding="utf-8")
    return tmp_path


def suspend(client, payload=None, name="step"):
    with pytest.raises(PendingInference) as pending:
        client.generate_json(name, payload or {"instruction": "합성 요청"})
    return pending.value.request


def answer(client, request, response):
    client.submit_response(request["request_id"], request["fingerprint"], response)


def test_replay_two_steps_independent_responses_and_no_network(prompts, monkeypatch):
    import httpx
    monkeypatch.setattr(httpx.Client, "__init__", lambda *a, **k: pytest.fail("network client created"))
    first = MCPInferenceClient(prompt_dir=prompts)
    request = suspend(first, {"raw": "제품 A: 5 mg"})
    answer(first, request, {"amount": "5 mg"})
    records = first.records
    resumed = MCPInferenceClient(records, prompt_dir=prompts)
    returned = resumed.generate_json("step", {"raw": "제품 A: 5 mg"})
    assert returned == {"amount": "5 mg"}
    returned["amount"] = "fabricated change"
    second_request = suspend(resumed, {"review": "제품 A: 5 mg"})
    assert second_request["request_id"] != request["request_id"]
    answer(resumed, second_request, {"issues": []})
    final = MCPInferenceClient(resumed.records, prompt_dir=prompts)
    assert final.generate_json("step", {"raw": "제품 A: 5 mg"}) == {"amount": "5 mg"}
    assert final.generate_json("step", {"review": "제품 A: 5 mg"}) == {"issues": []}
    final.assert_complete()
    assert "unverified" in final.model
    assert "unverified" in final.inference_provenance
    assert not hasattr(final, "client")  # No provider SDK, key or quota fallback.


@pytest.mark.parametrize("change", ["payload", "prompt", "name", "order"])
def test_replay_blocks_changed_input_or_prompt(prompts, change):
    client = MCPInferenceClient(prompt_dir=prompts)
    request = suspend(client, {"a": "original"})
    answer(client, request, {"ok": True})
    replay = MCPInferenceClient(client.records, prompt_dir=prompts)
    name, payload = "step", {"a": "original"}
    if change == "payload":
        payload["a"] = "new source"
    elif change == "prompt":
        (prompts / "step.md").write_text("new prompt", encoding="utf-8")
    elif change == "name":
        (prompts / "other.md").write_text("원자료를 근거로 JSON만 작성함", encoding="utf-8")
        name = "other"
    else:
        payload = {"review": "different stage"}
    with pytest.raises(MCPInferenceError, match="변경"):
        replay.generate_json(name, payload)


def test_same_pending_id_reappears_and_cannot_export(prompts):
    original = MCPInferenceClient(prompt_dir=prompts)
    request = suspend(original)
    restored = MCPInferenceClient(original.records, prompt_dir=prompts)
    assert suspend(restored) == request
    with pytest.raises(MCPInferenceError):
        restored.assert_complete()
    answer(restored, request, {})
    with pytest.raises(MCPInferenceError):
        restored.assert_complete()  # Responding alone does not verify the replay.
    restored.generate_json("step", {"instruction": "합성 요청"})
    restored.assert_complete()


@pytest.mark.parametrize("response", [[], "{}", None, {"x": float("nan")},
                                      {"x": float("inf")}, {1: "numeric key"},
                                      {"x": (1, 2)}, {"x": "\ud800"},
                                      {"x": "a" * (2 * 1024 * 1024)}])
def test_invalid_response_is_rejected_without_mutation(prompts, response):
    client = MCPInferenceClient(prompt_dir=prompts)
    request = suspend(client)
    with pytest.raises(MCPInferenceError):
        answer(client, request, response)
    assert "response" not in client.records[-1]


def test_wrong_id_fingerprint_duplicate_or_stale_answer(prompts):
    client = MCPInferenceClient(prompt_dir=prompts)
    request = suspend(client)
    for request_id, fingerprint in (("0" * 32, request["fingerprint"]),
                                    (request["request_id"], "0" * 64)):
        with pytest.raises(MCPInferenceError):
            client.submit_response(request_id, fingerprint, {})
    answer(client, request, {})
    with pytest.raises(MCPInferenceError):
        answer(client, request, {})


@pytest.mark.parametrize("tamper", ["fingerprint", "id", "payload", "operation", "extra", "duplicate", "pending_before_tail"])
def test_stored_records_cannot_be_corrupted(prompts, tamper):
    client = MCPInferenceClient(prompt_dir=prompts)
    request = suspend(client)
    records = client.records
    if tamper == "fingerprint":
        records[0]["fingerprint"] = "0" * 64
    elif tamper == "id":
        records[0]["request_id"] = "user-controlled/path"
    elif tamper == "payload":
        records[0]["payload"] = {"other": "payload"}
    elif tamper == "operation":
        records[0]["operation"] = "paid_api"
    elif tamper == "extra":
        records[0]["api_key"] = "must not be accepted"
    else:
        records.append(deepcopy(records[0]))
        if tamper == "pending_before_tail":
            records[-1]["request_id"] = "1" * 32
    with pytest.raises(MCPInferenceError):
        MCPInferenceClient(records, prompt_dir=prompts)
    assert request["request_id"] == client.records[0]["request_id"]


def test_images_bind_actual_bytes_and_replay_ocr_requires_confirmation(prompts, tmp_path):
    (prompts / "ocr.md").write_text("사진을 원문 그대로 읽고 불확실한 항목을 기록함", encoding="utf-8")
    from agent.multimodal_intake import collect_multimodal
    image = Image.new("RGB", (8, 8), color="white")
    source = tmp_path / "source.png"
    image.save(source)
    original = MCPInferenceClient(prompt_dir=prompts)
    with pytest.raises(PendingInference) as pending:
        collect_multimodal([source], client=original, allow_ocr=True)
    request = pending.value.request
    assert request["operation"] == "read_image_json"
    assert request["image"]["mime"] == "image/png"
    answer(original, request, {"본문": "제품명: 합성품\n성상: 흰색 정제", "표 목록": [], "불확실한 항목": []})
    replay = MCPInferenceClient(original.records, prompt_dir=prompts)
    result = collect_multimodal([source], client=replay, allow_ocr=True)
    replay.assert_complete()
    assert result["files"][0]["status"] == "needs_confirmation"
    assert all(item["requires_verification"] for item in result["sources"])
    changed = BytesIO()
    Image.new("RGB", (8, 8), "red").save(changed, format="PNG")
    changed_client = MCPInferenceClient(original.records, prompt_dir=prompts)
    with pytest.raises(MCPInferenceError):
        changed_client.read_image_json(changed.getvalue(), payload=request["payload"])


def test_internal_yield_bypasses_generic_error_handler(prompts):
    client = MCPInferenceClient(prompt_dir=prompts)
    with pytest.raises(PendingInference):
        try:
            client.generate_json("step", {})
        except Exception:
            pytest.fail("a pending inference was falsely converted into a model error")


def test_lexical_vectors_are_local_deterministic_bounded_and_not_model_embeddings():
    from agent.retrieve import search_chunks
    client = MCPInferenceClient()
    assert client.embed([]) == []
    assert client.embed(["성상 흰색 정제"])[0] == client.embed(["성상 흰색 정제"])[0]
    chunks = [{"source_id": "A", "text": "성상 흰색 정제"},
              {"source_id": "B", "text": "제품 출시 계획"}]
    found = search_chunks("성상 흰색 정제", chunks, client.embed)
    assert found[0]["source_id"] == "A"
    assert len(client.embed(["a"])[0]) == 256
    assert client.embedding_model == "local-lexical-hash-v1"
    assert client.records == []


@pytest.mark.parametrize("texts", [[""], [" "], [None], "not a list", ["a"] * 129])
def test_invalid_lexical_input(texts):
    with pytest.raises(MCPInferenceError):
        MCPInferenceClient().embed(texts)


def test_nested_and_nonfinite_payload_are_rejected(prompts):
    nested = {}
    for _ in range(66):
        nested = {"value": nested}
    client = MCPInferenceClient(prompt_dir=prompts)
    for payload in (nested, {"value": float("nan")}, {"x": "\ud800"}):
        with pytest.raises(MCPInferenceError):
            client.generate_json("step", payload)
    assert client.records == []
