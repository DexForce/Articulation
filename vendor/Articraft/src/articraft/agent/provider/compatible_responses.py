"""HTTP Responses transport using the existing OpenAI conversation implementation."""

from __future__ import annotations

import json
from typing import Any

import httpx

from articraft.agent.provider.compatible import _CompatibleHTTPClient
from articraft.agent.provider.openai import OpenAIModel
from articraft.errors import ModelError
from articraft.settings import Settings


class CompatibleResponsesModel(OpenAIModel):
    supports_images = True

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None):
        super().__init__(settings)
        self._http = _CompatibleHTTPClient(
            settings,
            "responses",
            client=client,
            diagnostics_dir=settings.output_dir / "compatible-api-diagnostics",
        )

    def _request(self, messages, input_items, previous_response_id, tools):
        # Gateways need stateless replay, including encrypted reasoning and tool results.
        items = [*self._input_items, *input_items] if previous_response_id else input_items
        request = super()._request(messages, items, None, tools)
        request["max_output_tokens"] = self.config.compatible_max_output_tokens
        return request

    async def _send_with_retries(self, request: dict[str, Any], fallback_request: dict[str, Any]):
        del fallback_request
        payload = await self._http.send(request)
        if payload.get("status") != "completed":
            raise ModelError("Compatible Responses returned an incomplete response")
        if not isinstance(payload.get("id"), str) or not payload["id"]:
            raise ModelError("Compatible Responses returned no response id")
        output = payload.get("output")
        if not isinstance(output, list) or any(not isinstance(item, dict) for item in output):
            raise ModelError("Compatible Responses returned malformed output")
        for item in output:
            if item.get("type") != "function_call":
                continue
            try:
                valid = all(
                    isinstance(item.get(key), str) and item[key] for key in ("call_id", "name")
                ) and isinstance(json.loads(item["arguments"]), dict)
            except (KeyError, TypeError, ValueError) as exc:
                raise ModelError("Compatible Responses returned invalid tool arguments") from exc
            if not valid:
                raise ModelError("Compatible Responses returned invalid tool arguments")
        return payload

    async def query(self, messages, *, tools=None):
        result = await super().query(messages, tools=tools)
        # Gateway billing is not the SDK's static OpenAI price table.
        result["cost"] = 0.0
        return result

    async def summarize_context(self, messages, *, max_output_tokens):
        result = await super().summarize_context(messages, max_output_tokens=max_output_tokens)
        result["cost"] = 0.0
        return result

    async def close(self):
        await self._http.close()
