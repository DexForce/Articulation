import asyncio
import json

import httpx
import pytest
from articraft.agent.provider import create_model
from articraft.agent.provider.compatible import CompatibleModel, _messages
from articraft.api import _resolved_settings
from articraft.errors import ModelError
from articraft.settings import Settings


def settings(**overrides):
    return Settings(
        _env_file=None,
        provider="compatible-api",
        openai_api_key="test-secret",
        openai_base_url="https://example.invalid/v1",
        openai_model="mimo-v2.6-pro",
        openai_max_attempts=1,
        **overrides,
    )


def test_native_registration_and_model_override():
    config = _resolved_settings(settings(), model="other-model")
    assert config.selected_model == "other-model"
    assert isinstance(create_model(config), CompatibleModel)


@pytest.mark.parametrize("url", ["", "file:///tmp/key", "https://user:secret@host/v1"])
def test_invalid_endpoint_rejected(url):
    with pytest.raises(ModelError, match="base URL"):
        CompatibleModel(Settings(_env_file=None, openai_api_key="key", openai_base_url=url))


def test_images_tools_and_reasoning_roundtrip():
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert str(request.url) == "https://example.invalid/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "reasoning_content": "provider-checkpoint",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "compile",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            },
        )

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        model = CompatibleModel(settings(), client=client)
        result = await model.query(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "make this"},
                        {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="},
                    ],
                }
            ],
            tools=[{"type": "function", "name": "compile", "parameters": {"type": "object"}}],
        )
        await model.close()
        return result

    result = asyncio.run(run())
    assert result["tool_calls"] == [{"id": "call_1", "name": "compile", "arguments": "{}"}]
    assert result["token_usage"]["total_tokens"] == 30
    assert captured[0]["messages"][0]["content"][1]["type"] == "image_url"
    assert captured[0]["tools"][0]["function"]["name"] == "compile"
    assert captured[0]["max_completion_tokens"] == 16384
    assert "reasoning_effort" not in captured[0]
    history = _messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": result["tool_calls"],
                "provider_content": result["provider_content"],
            },
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": [
                    {"type": "input_text", "text": "ok"},
                    {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="},
                ],
            },
        ]
    )
    assert history[0]["reasoning_content"] == "provider-checkpoint"
    assert history[1] == {"role": "tool", "tool_call_id": "call_1", "content": "ok"}
    assert history[2]["role"] == "user"


@pytest.mark.parametrize(
    "finish,arguments",
    [
        ("length", "{}"),
        ("content_filter", "{}"),
        ("repetition_truncation", "{}"),
        ("tool_calls", "{invalid"),
        ("tool_calls", "[]"),
    ],
)
def test_incomplete_or_malformed_tool_output_rejected(finish, arguments):
    async def run():
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": finish,
                                "message": {
                                    "tool_calls": [
                                        {
                                            "id": "c",
                                            "function": {"name": "compile", "arguments": arguments},
                                        },
                                    ]
                                },
                            }
                        ],
                    },
                )
            )
        )
        model = CompatibleModel(settings(), client=client)
        try:
            with pytest.raises(ModelError):
                await model.query([{"role": "user", "content": "hello"}])
        finally:
            await model.close()

    asyncio.run(run())


def test_error_redacts_key_and_does_not_retry_auth_failure():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(401, json={"error": {"message": "invalid test-secret"}})

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        model = CompatibleModel(settings(), client=client)
        try:
            with pytest.raises(ModelError, match="401") as error:
                await model.query([{"role": "user", "content": "hello"}])
            assert "test-secret" not in str(error.value)
        finally:
            await model.close()

    asyncio.run(run())
    assert len(requests) == 1


def test_parallel_tool_images_follow_all_tool_replies():
    converted = _messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "a", "name": "view_image", "arguments": "{}"},
                    {"id": "b", "name": "read", "arguments": "{}"},
                ],
            },
            {
                "type": "function_call_output",
                "call_id": "a",
                "output": [
                    {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="},
                ],
            },
            {"type": "function_call_output", "call_id": "b", "output": "text"},
            {"role": "assistant", "content": "finished"},
        ]
    )
    assert [message["role"] for message in converted] == [
        "assistant",
        "tool",
        "tool",
        "user",
        "assistant",
    ]
