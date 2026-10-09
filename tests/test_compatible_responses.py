import asyncio
import json

import httpx
import pytest
from articraft.agent.provider import create_model
from articraft.errors import ModelError
from articraft.settings import Settings


def config(tmp_path, **overrides):
    return Settings(
        _env_file=None,
        provider="compatible-api",
        compatible_api_mode="responses",
        openai_model="openai/gpt-5.6-sol",
        openai_reasoning_effort="low",
        openai_api_key="offline-secret",
        openai_base_url="https://example.invalid/v1",
        output_dir=tmp_path,
        **overrides,
    )


def completed(output, response_id="response_1"):
    return {"id": response_id, "status": "completed", "model": "gpt-5.6-sol", "output": output}


def text_output(text):
    return [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
    ]


def test_responses_registration(tmp_path):
    model = create_model(config(tmp_path))
    assert type(model).__name__ == "CompatibleResponsesModel"
    assert model.supports_images


def test_stateless_responses_replays_reasoning_and_tools(tmp_path, monkeypatch):
    captured = []
    reasoning = {
        "type": "reasoning",
        "id": "reason_1",
        "summary": [],
        "encrypted_content": "offline-checkpoint",
    }
    call = {"type": "function_call", "call_id": "call_1", "name": "compile", "arguments": "{}"}

    def handle(request):
        assert str(request.url) == "https://example.invalid/v1/responses"
        assert request.headers["Authorization"] == "Bearer offline-secret"
        body = json.loads(request.content)
        captured.append(body)
        assert body["reasoning"] == {"effort": "low"}
        assert body["store"] is False
        assert "previous_response_id" not in body
        assert body["max_output_tokens"] == 16384
        assert body["model"] == "openai/gpt-5.6-sol"
        return httpx.Response(
            200, json=completed([reasoning, call] if len(captured) == 1 else text_output("done"))
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: client)

    async def run():
        model = create_model(config(tmp_path))
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "offline"},
                    {"type": "input_image", "image_url": "data:image/png;base64,offline"},
                ],
            }
        ]
        tools = [{"type": "function", "name": "compile", "parameters": {"type": "object"}}]
        try:
            first = await model.query(messages, tools=tools)
            assert first["tool_calls"][0]["id"] == "call_1"
            second = await model.query(
                messages
                + [
                    {
                        "role": "assistant",
                        "content": first["text"],
                        "tool_calls": first["tool_calls"],
                    },
                    {"type": "function_call_output", "call_id": "call_1", "output": "compiled"},
                ],
                tools=tools,
            )
            assert second["text"] == "done"
            assert second["cost"] == 0
        finally:
            await model.close()

    asyncio.run(run())
    inputs = captured[1]["input"]
    assert inputs.count(reasoning) == 1
    assert inputs.count(call) == 1
    assert inputs[-1] == {"type": "function_call_output", "call_id": "call_1", "output": "compiled"}
    assert len(list((tmp_path / "compatible-api-diagnostics").glob("*.json"))) == 2
    assert all(
        "offline-secret" not in p.read_text()
        for p in (tmp_path / "compatible-api-diagnostics").glob("*.json")
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "incomplete", "output": text_output("partial")},
        completed(
            [{"type": "function_call", "call_id": "c", "name": "compile", "arguments": "{invalid"}]
        ),
        {"id": "r", "status": "completed", "output": ["not an output item"]},
        {"status": "completed", "output": text_output("no id")},
        completed(
            [{"type": "function_call", "call_id": "c", "name": "compile", "arguments": "[]"}]
        ),
    ],
)
def test_incomplete_or_invalid_tool_response_rejected(tmp_path, monkeypatch, payload):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: client)

    async def run():
        model = create_model(config(tmp_path))
        try:
            with pytest.raises(ModelError):
                await model.query([{"role": "user", "content": "offline"}])
        finally:
            await model.close()

    asyncio.run(run())


@pytest.mark.parametrize("status,expected_attempts", [(500, 2), (401, 1)])
def test_http_retries_are_bounded_and_auth_is_not_retried(
    tmp_path, monkeypatch, status, expected_attempts
):
    calls = []

    def handle(request):
        calls.append(request)
        return (
            httpx.Response(status, json={"error": {"message": "offline-secret"}})
            if len(calls) == 1
            else httpx.Response(200, json=completed(text_output("done")))
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: client)

    async def no_delay(_seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_delay)

    async def run():
        model = create_model(config(tmp_path, openai_max_attempts=2))
        try:
            if status == 401:
                with pytest.raises(ModelError, match="401") as exc:
                    await model.query([{"role": "user", "content": "offline"}])
                assert "offline-secret" not in str(exc.value)
            else:
                assert (await model.query([{"role": "user", "content": "offline"}]))[
                    "text"
                ] == "done"
        finally:
            await model.close()

    asyncio.run(run())
    assert len(calls) == expected_attempts


def test_summary_resets_cached_response_items(tmp_path, monkeypatch):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completed(text_output("checkpoint")))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: client)

    async def run():
        model = create_model(config(tmp_path))
        try:
            await model.query([{"role": "user", "content": "old state"}])
            await model.summarize_context(
                [{"role": "user", "content": "old state"}], max_output_tokens=100
            )
            await model.query([{"role": "user", "content": "new checkpoint"}])
        finally:
            await model.close()

    asyncio.run(run())
    assert requests[1]["max_output_tokens"] == 100
    assert requests[2]["input"] == [{"role": "user", "content": "new checkpoint"}]
