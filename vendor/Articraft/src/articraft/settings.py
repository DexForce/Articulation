from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_OUTPUT_DIR = Path("runs")
DEFAULT_MAX_TURNS = 100
DEFAULT_COMPILE_TIMEOUT_SECONDS = 900.0
DEFAULT_OPENAI_MODEL = "gpt-5.6"
DEFAULT_OPENAI_MAX_ATTEMPTS = 4
DEFAULT_OPENAI_REQUEST_TIMEOUT_SECONDS = 900.0
DEFAULT_PROVIDER = "openai"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_ANTHROPIC_MAX_ATTEMPTS = 4
DEFAULT_ANTHROPIC_REQUEST_TIMEOUT_SECONDS = 3600.0
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_GEMINI_MAX_ATTEMPTS = 4
DEFAULT_GEMINI_REQUEST_TIMEOUT_SECONDS = 900.0
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
DEFAULT_OPENROUTER_MAX_ATTEMPTS = 4
DEFAULT_OPENROUTER_REQUEST_TIMEOUT_SECONDS = 900.0
DEFAULT_CODEX_MODEL = "codex-cli-default"
DEFAULT_CODEX_CLI_MAX_ATTEMPTS = 2
DEFAULT_CODEX_CLI_RECOVERY_BUDGET_SECONDS = 600.0
DEFAULT_CODEX_CLI_RECONNECT_STALL_SECONDS = 25.0
DEFAULT_CODEX_CLI_RESPONSE_SHADOW_SECONDS = 120.0
DEFAULT_CODEX_CLI_RESPONSE_STALL_SECONDS = 120.0
DEFAULT_CODEX_CLI_TIMEOUT_SECONDS = 900.0
DEFAULT_CODEX_CLI_TELEMETRY_INTERVAL_SECONDS = 1.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ARTICRAFT_",
        extra="ignore",
        populate_by_name=True,
    )

    output_dir: Path = Field(
        default=DEFAULT_OUTPUT_DIR,
        validation_alias="ARTICRAFT_OUTPUT_DIR",
    )
    provider: Literal["openai", "gemini", "anthropic", "openrouter", "codex-cli", "compatible-api"] = Field(
        default=DEFAULT_PROVIDER,
        validation_alias="ARTICRAFT_PROVIDER",
    )
    openai_model: str = Field(
        default=DEFAULT_OPENAI_MODEL,
        validation_alias="ARTICRAFT_MODEL",
    )
    openai_reasoning_effort: str = Field(
        default="high",
        validation_alias="ARTICRAFT_REASONING_EFFORT",
    )
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    # Local adaptation: Chat Completions endpoints are separate from OpenAI Responses.
    openai_base_url: str = Field(default="", validation_alias="OPENAI_BASE_URL")
    compatible_proxy_url: str = Field(default="", validation_alias="ARTICRAFT_COMPATIBLE_PROXY_URL")
    compatible_api_mode: Literal["chat", "responses"] = Field(
        default="chat", validation_alias="ARTICRAFT_COMPATIBLE_API_MODE"
    )
    compatible_max_output_tokens: int = Field(
        default=16384, ge=1, validation_alias="ARTICRAFT_COMPATIBLE_MAX_OUTPUT_TOKENS"
    )
    openai_max_attempts: int = Field(
        default=DEFAULT_OPENAI_MAX_ATTEMPTS,
        ge=1,
        validation_alias="ARTICRAFT_OPENAI_MAX_ATTEMPTS",
    )
    openai_request_timeout_seconds: float = Field(
        default=DEFAULT_OPENAI_REQUEST_TIMEOUT_SECONDS,
        gt=0.0,
        validation_alias="ARTICRAFT_OPENAI_REQUEST_TIMEOUT_SECONDS",
    )
    anthropic_model: str = Field(
        default=DEFAULT_ANTHROPIC_MODEL,
        validation_alias="ARTICRAFT_ANTHROPIC_MODEL",
    )
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
    )
    anthropic_max_attempts: int = Field(
        default=DEFAULT_ANTHROPIC_MAX_ATTEMPTS,
        ge=1,
        validation_alias="ARTICRAFT_ANTHROPIC_MAX_ATTEMPTS",
    )
    anthropic_request_timeout_seconds: float = Field(
        default=DEFAULT_ANTHROPIC_REQUEST_TIMEOUT_SECONDS,
        gt=0.0,
        validation_alias="ARTICRAFT_ANTHROPIC_REQUEST_TIMEOUT_SECONDS",
    )
    gemini_model: str = Field(
        default=DEFAULT_GEMINI_MODEL,
        validation_alias="ARTICRAFT_GEMINI_MODEL",
    )
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias="GEMINI_API_KEY",
    )
    gemini_max_attempts: int = Field(
        default=DEFAULT_GEMINI_MAX_ATTEMPTS,
        ge=1,
        validation_alias="ARTICRAFT_GEMINI_MAX_ATTEMPTS",
    )
    gemini_request_timeout_seconds: float = Field(
        default=DEFAULT_GEMINI_REQUEST_TIMEOUT_SECONDS,
        gt=0.0,
        validation_alias="ARTICRAFT_GEMINI_REQUEST_TIMEOUT_SECONDS",
    )
    openrouter_model: str = Field(
        default=DEFAULT_OPENROUTER_MODEL,
        validation_alias="ARTICRAFT_OPENROUTER_MODEL",
    )
    openrouter_api_key: str | None = Field(
        default=None,
        validation_alias="OPENROUTER_API_KEY",
    )
    openrouter_max_attempts: int = Field(
        default=DEFAULT_OPENROUTER_MAX_ATTEMPTS,
        ge=1,
        validation_alias="ARTICRAFT_OPENROUTER_MAX_ATTEMPTS",
    )
    openrouter_request_timeout_seconds: float = Field(
        default=DEFAULT_OPENROUTER_REQUEST_TIMEOUT_SECONDS,
        gt=0.0,
        validation_alias="ARTICRAFT_OPENROUTER_REQUEST_TIMEOUT_SECONDS",
    )
    openrouter_http_referer: str | None = Field(
        default=None,
        validation_alias="OPENROUTER_HTTP_REFERER",
    )
    openrouter_app_title: str | None = Field(
        default=None,
        validation_alias="OPENROUTER_APP_TITLE",
    )
    codex_model: str = Field(
        default=DEFAULT_CODEX_MODEL,
        validation_alias="ARTICRAFT_CODEX_MODEL",
    )
    # Local adaptation: explicitly forward Fast despite --ignore-user-config.
    codex_service_tier: Literal["default", "fast"] = Field(
        default="default", validation_alias="ARTICRAFT_CODEX_SERVICE_TIER"
    )
    codex_cli_bin: str = Field(
        default="codex",
        validation_alias="ARTICRAFT_CODEX_CLI_BIN",
    )
    codex_cli_max_attempts: int = Field(
        default=DEFAULT_CODEX_CLI_MAX_ATTEMPTS,
        ge=1,
        validation_alias="ARTICRAFT_CODEX_CLI_MAX_ATTEMPTS",
    )
    codex_cli_timeout_seconds: float = Field(
        default=DEFAULT_CODEX_CLI_TIMEOUT_SECONDS,
        gt=0.0,
        validation_alias="ARTICRAFT_CODEX_CLI_TIMEOUT_SECONDS",
    )
    codex_cli_recovery_budget_seconds: float = Field(
        default=DEFAULT_CODEX_CLI_RECOVERY_BUDGET_SECONDS,
        gt=0.0,
        validation_alias="ARTICRAFT_CODEX_CLI_RECOVERY_BUDGET_SECONDS",
    )
    codex_cli_retry_session_mode: Literal["resume", "fresh"] = Field(
        default="resume",
        validation_alias="ARTICRAFT_CODEX_CLI_RETRY_SESSION_MODE",
    )
    codex_cli_retry_invalid_response: bool = Field(
        default=False,
        validation_alias="ARTICRAFT_CODEX_CLI_RETRY_INVALID_RESPONSE",
    )
    codex_cli_rotate_route_on_transport_retry: bool = Field(
        default=True,
        validation_alias="ARTICRAFT_CODEX_CLI_ROTATE_ROUTE_ON_TRANSPORT_RETRY",
    )
    codex_cli_reconnect_stall_seconds: float = Field(
        default=DEFAULT_CODEX_CLI_RECONNECT_STALL_SECONDS,
        gt=0.0,
        validation_alias="ARTICRAFT_CODEX_CLI_RECONNECT_STALL_SECONDS",
    )
    codex_cli_response_shadow_seconds: float = Field(
        default=DEFAULT_CODEX_CLI_RESPONSE_SHADOW_SECONDS,
        gt=0.0,
        validation_alias="ARTICRAFT_CODEX_CLI_RESPONSE_SHADOW_SECONDS",
    )
    codex_cli_response_stall_seconds: float = Field(
        default=DEFAULT_CODEX_CLI_RESPONSE_STALL_SECONDS,
        gt=0.0,
        validation_alias="ARTICRAFT_CODEX_CLI_RESPONSE_STALL_SECONDS",
    )
    codex_cli_force_https: bool = Field(
        default=False,
        validation_alias="ARTICRAFT_CODEX_CLI_FORCE_HTTPS",
    )
    codex_cli_transport_mode: Literal["auto", "https", "websocket"] = Field(
        default="auto",
        validation_alias="ARTICRAFT_CODEX_CLI_TRANSPORT_MODE",
    )
    codex_cli_route_controller_url: str = Field(
        default="",
        validation_alias="ARTICRAFT_CODEX_CLI_ROUTE_CONTROLLER_URL",
    )
    codex_cli_route_controller_secret_file: Path | None = Field(
        default=None,
        validation_alias="ARTICRAFT_CODEX_CLI_ROUTE_CONTROLLER_SECRET_FILE",
    )
    codex_cli_telemetry_interval_seconds: float = Field(
        default=DEFAULT_CODEX_CLI_TELEMETRY_INTERVAL_SECONDS,
        gt=0.0,
        validation_alias="ARTICRAFT_CODEX_CLI_TELEMETRY_INTERVAL_SECONDS",
    )
    codex_cli_egress_mode: Literal["proxy", "direct"] = Field(
        default="proxy",
        validation_alias="ARTICRAFT_CODEX_CLI_EGRESS_MODE",
    )
    codex_cli_experiment_label: str = Field(
        default="",
        validation_alias="ARTICRAFT_CODEX_CLI_EXPERIMENT_LABEL",
    )
    max_turns: int = Field(default=DEFAULT_MAX_TURNS, validation_alias="ARTICRAFT_MAX_TURNS")
    physics_enabled: bool = Field(
        default=False,
        validation_alias="ARTICRAFT_PHYSICS",
    )
    compile_timeout_seconds: float = Field(
        default=DEFAULT_COMPILE_TIMEOUT_SECONDS,
        gt=0.0,
        validation_alias="ARTICRAFT_COMPILE_TIMEOUT_SECONDS",
    )

    @property
    def selected_model(self) -> str:
        if self.provider == "codex-cli":
            return self.codex_model
        if self.provider == "openrouter":
            return self.openrouter_model
        if self.provider == "anthropic":
            return self.anthropic_model
        if self.provider == "gemini":
            return self.gemini_model
        return self.openai_model

    @property
    def selected_reasoning_effort(self) -> str:
        if self.provider in {"openai", "codex-cli"}:
            return self.openai_reasoning_effort
        return ""


@cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
