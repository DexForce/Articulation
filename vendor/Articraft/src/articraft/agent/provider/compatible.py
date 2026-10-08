"""Local Chat Completions adapter, including MiMo images and tool-call checkpoints."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlsplit

import httpx

from articraft.agent.provider.openrouter import _response_token_usage, _tools
from articraft.errors import ModelError
from articraft.settings import Settings, get_settings


class CompatibleModel:
    supports_images = True

    def __init__(
        self, settings: Settings | None = None, *, client: httpx.AsyncClient | None = None
    ):
        self.config = settings or get_settings()
        if not (self.config.openai_api_key or "").strip():
            raise ModelError("Set OPENAI_API_KEY for the compatible API backend")
        base = self.config.openai_base_url.rstrip("/")
        parsed = urlsplit(base)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            raise ModelError("Compatible API base URL must be an HTTP(S) URL without credentials")
        self._url = base + "/chat/completions"
        self._client = client

    @property
    def context_window_tokens(self) -> int:
        return 0

    async def query(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.config.openai_model,
            "messages": _messages(messages),
            "max_completion_tokens": self.config.compatible_max_output_tokens,
            "stream": False,
        }
        if tools:
            request["tools"] = _tools(tools)
        # MiMo's thinking default is provider-owned. Do not send OpenAI reasoning_effort.
        payload = await self._send(request)
        try:
            choice = payload["choices"][0]
            if choice.get("finish_reason") not in {"stop", "tool_calls"}:
                raise ModelError(
                    f"Compatible API returned incomplete output ({choice.get('finish_reason')})"
                )
            message = choice["message"]
            if message.get("refusal"):
                raise ModelError("Compatible API refused the request")
            text = message.get("content") or ""
            if not isinstance(text, str):
                raise ValueError("assistant content must be text")
            calls = []
            for call in message.get("tool_calls") or []:
                function = call["function"]
                arguments = function["arguments"]
                decoded = json.loads(arguments) if isinstance(arguments, str) else arguments
                if not call.get("id") or not function.get("name") or not isinstance(decoded, dict):
                    raise ValueError("tool call requires id, name, and JSON object arguments")
                calls.append(
                    {"id": call["id"], "name": function["name"], "arguments": json.dumps(decoded)}
                )
            if not text and not calls:
                raise ModelError("Compatible API response has no text or tool calls")
            provider_content = []
            reasoning = message.get("reasoning_content")
            if isinstance(reasoning, str):
                provider_content.append(
                    {"type": "compatible_reasoning", "reasoning_content": reasoning}
                )
            return {
                "text": text,
                "tool_calls": calls,
                "provider_content": provider_content,
                "token_usage": _response_token_usage(payload),
                "cost": 0.0,
                "response": payload,
            }
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise ModelError("Compatible API returned malformed text or tool output") from exc

    async def _send(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            self._client = httpx.AsyncClient(
                proxy=self.config.compatible_proxy_url or None,
                trust_env=False,
                follow_redirects=False,
            )
        for attempt in range(self.config.openai_max_attempts):
            try:
                response = await self._client.post(
                    self._url,
                    json=request,
                    headers={"Authorization": f"Bearer {self.config.openai_api_key}"},
                    timeout=self.config.openai_request_timeout_seconds,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt + 1 == self.config.openai_max_attempts:
                    raise ModelError(
                        f"Compatible API transport failed ({type(exc).__name__})"
                    ) from exc
            else:
                if response.status_code < 400:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise ModelError("Compatible API response is not JSON") from exc
                    if not isinstance(payload, dict) or payload.get("error"):
                        raise ModelError(
                            "Compatible API returned an invalid response or provider error"
                        )
                    return payload
                if (
                    response.status_code not in {408, 429, 500, 502, 503, 504}
                    or attempt + 1 == self.config.openai_max_attempts
                ):
                    # Never put raw provider responses or credentials in task errors.
                    raise ModelError(f"Compatible API request failed (HTTP {response.status_code})")
            await asyncio.sleep(min(2**attempt, 20))
        raise AssertionError("retry loop did not return or raise")

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    converted = []
    for item in content or []:
        if item.get("type") in {"input_text", "text"}:
            converted.append({"type": "text", "text": str(item.get("text") or "")})
        elif item.get("type") == "input_image":
            converted.append({"type": "image_url", "image_url": {"url": item["image_url"]}})
        else:
            raise ModelError("Unsupported compatible API content item")
    return converted


def _messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    pending_images = []
    for message in messages:
        if message.get("type") == "function_call_output":
            output = message.get("output")
            if not isinstance(output, (str, list)):
                output = json.dumps(output)
            content = _content(output)
            text = "\n".join(item["text"] for item in content if item["type"] == "text")
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": message["call_id"],
                    "content": text or "Tool returned images.",
                }
            )
            pending_images.extend(item for item in content if item["type"] == "image_url")
            continue
        # Keep all tool replies contiguous before appending their visual evidence.
        if pending_images:
            converted.append({"role": "user", "content": pending_images})
            pending_images = []
        role = message.get("role")
        if role not in {"system", "user", "assistant"}:
            raise ModelError("Unsupported compatible API message role")
        item: dict[str, Any] = {"role": role, "content": message.get("content") or ""}
        if role == "user" and isinstance(item["content"], list):
            item["content"] = _content(item["content"])
        if role == "assistant":
            if message.get("tool_calls"):
                item["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": call["arguments"]
                            if isinstance(call["arguments"], str)
                            else json.dumps(call["arguments"]),
                        },
                    }
                    for call in message["tool_calls"]
                ]
            for checkpoint in message.get("provider_content") or []:
                if checkpoint.get("type") == "compatible_reasoning":
                    item["reasoning_content"] = checkpoint["reasoning_content"]
        converted.append(item)
    if pending_images:
        converted.append({"role": "user", "content": pending_images})
    return converted
