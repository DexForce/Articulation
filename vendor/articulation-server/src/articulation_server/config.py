from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _env_bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _env_int(env: Mapping[str, str], name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(env.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _env_float(env: Mapping[str, str], name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(env.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value <= minimum:
        raise ValueError(f"{name} must be greater than {minimum:g}")
    return value


def _service_values(env: Mapping[str, str]) -> dict[str, Any]:
    """Read an optional ``articulation`` entry from the shared service config."""
    default_path = Path(__file__).resolve().parents[3] / "service_config.json"
    configured = env.get("SERVICE_CONFIG_PATH") or env.get("ARTICULATION_SERVICE_CONFIG_PATH")
    path = Path(configured).expanduser() if configured else default_path
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read service config {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"service config must contain a JSON object: {path}")
    value = document.get("articulation", {})
    if not isinstance(value, dict):
        raise ValueError("service config entry 'articulation' must be an object")
    return value


@dataclass(frozen=True, slots=True)
class ServerConfig:
    host: str
    port: int
    output_root: Path
    articraft_source_dir: Path
    python_executable: Path
    provider: str | None = None
    model: str | None = None
    # Deprecated compatibility fields. The HTTP API no longer authenticates requests.
    api_key: str | None = None
    allow_unauthenticated: bool = False
    concurrency: int = 1
    http_threads: int = 8
    max_queue_size: int = 32
    max_upload_bytes: int = 20 * 1024 * 1024
    max_prompt_chars: int = 20_000
    job_timeout_seconds: float = 7_200.0
    probe_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ServerConfig:
        values = os.environ if env is None else env
        service = _service_values(values)
        repository_root = Path(__file__).resolve().parents[2]
        source_root = repository_root.parent

        host = values.get("ARTICULATION_HOST", str(service.get("host") or "127.0.0.1"))
        port = _env_int(
            values,
            "ARTICULATION_PORT",
            int(service.get("port") or 8688),
        )
        output_root = Path(
            values.get(
                "ARTICULATION_OUTPUT_ROOT",
                str(repository_root / "outputs"),
            )
        ).expanduser()
        articraft_source = Path(
            values.get("ARTICRAFT_SOURCE_DIR", str(source_root / "Articraft"))
        ).expanduser()
        python_executable = Path(values.get("ARTICULATION_PYTHON", sys.executable)).expanduser()
        provider = values.get("ARTICRAFT_PROVIDER") or None
        model = values.get("ARTICRAFT_MODEL") or None
        api_key = values.get("ARTICULATION_API_KEY") or None

        config = cls(
            host=host.strip(),
            port=port,
            output_root=output_root.resolve(),
            articraft_source_dir=articraft_source.resolve(),
            # Local adaptation: resolving a venv symlink discards its site-packages.
            python_executable=python_executable.absolute(),
            provider=provider.strip() if provider else None,
            model=model.strip() if model else None,
            api_key=api_key,
            allow_unauthenticated=_env_bool(values, "ARTICULATION_ALLOW_UNAUTHENTICATED", False),
            concurrency=_env_int(values, "ARTICULATION_CONCURRENCY", 1),
            http_threads=_env_int(values, "ARTICULATION_HTTP_THREADS", 8),
            max_queue_size=_env_int(values, "ARTICULATION_MAX_QUEUE_SIZE", 32),
            max_upload_bytes=_env_int(values, "ARTICULATION_MAX_UPLOAD_BYTES", 20 * 1024 * 1024),
            max_prompt_chars=_env_int(values, "ARTICULATION_MAX_PROMPT_CHARS", 20_000),
            job_timeout_seconds=_env_float(values, "ARTICULATION_JOB_TIMEOUT_SECONDS", 7_200.0),
            probe_timeout_seconds=_env_float(values, "ARTICULATION_PROBE_TIMEOUT_SECONDS", 30.0),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.host:
            raise ValueError("ARTICULATION_HOST must not be empty")
        if not 1 <= self.port <= 65_535:
            raise ValueError("ARTICULATION_PORT must be between 1 and 65535")
        if self.provider is not None and self.provider not in {
            "openai",
            "gemini",
            "anthropic",
            "openrouter",
            "codex-cli",
            "compatible-api",
        }:
            raise ValueError(
                "ARTICRAFT_PROVIDER must be openai, gemini, anthropic, openrouter, "
                "codex-cli, or compatible-api"
            )


__all__ = ["ServerConfig"]
