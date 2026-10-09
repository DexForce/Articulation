from __future__ import annotations

from articraft.agent.protocols import Model
from articraft.agent.provider.anthropic import AnthropicModel
from articraft.agent.provider.anthropic import (
    context_window_tokens_for as anthropic_context_window_tokens_for,
)
from articraft.agent.provider.codex_cli import CodexCliModel
from articraft.agent.provider.codex_cli import (
    context_window_tokens_for as codex_context_window_tokens_for,
)
from articraft.agent.provider.compatible import CompatibleModel
from articraft.agent.provider.compatible_responses import CompatibleResponsesModel
from articraft.agent.provider.gemini import GeminiModel
from articraft.agent.provider.gemini import (
    context_window_tokens_for as gemini_context_window_tokens_for,
)
from articraft.agent.provider.openai import OpenAIModel
from articraft.agent.provider.openai import (
    context_window_tokens_for as openai_context_window_tokens_for,
)
from articraft.agent.provider.openrouter import OpenRouterModel
from articraft.settings import Settings


def create_model(settings: Settings) -> Model:
    # Local adaptation: select the configured HTTP protocol without changing the agent loop.
    if settings.provider == "compatible-api":
        if settings.compatible_api_mode == "responses":
            return CompatibleResponsesModel(settings)
        return CompatibleModel(settings)
    if settings.provider == "codex-cli":
        return CodexCliModel(settings)
    if settings.provider == "openrouter":
        return OpenRouterModel(settings)
    if settings.provider == "anthropic":
        return AnthropicModel(settings)
    if settings.provider == "gemini":
        return GeminiModel(settings)
    return OpenAIModel(settings)


def context_window_tokens_for(model: str) -> int | None:
    return (
        openai_context_window_tokens_for(model)
        or gemini_context_window_tokens_for(model)
        or anthropic_context_window_tokens_for(model)
        or codex_context_window_tokens_for(model)
    )


__all__ = [
    "AnthropicModel",
    "CodexCliModel",
    "CompatibleModel",
    "GeminiModel",
    "OpenAIModel",
    "OpenRouterModel",
    "context_window_tokens_for",
    "create_model",
]
