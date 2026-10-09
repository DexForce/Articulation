from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from articraft.agent._child_process import child_environment
from articraft.errors import ModelError
from articraft.settings import DEFAULT_CODEX_MODEL, Settings, get_settings

_CONTEXT_WINDOW_TOKENS = 272_000
_RETRY_BACKOFF_SECONDS = 1.0
_TERMINATE_GRACE_SECONDS = 1.0
_STREAM_LIMIT_BYTES = 10 * 1024 * 1024
_DIAGNOSTICS_DIRNAME = "codex-cli-diagnostics"
_TRANSIENT_TRANSPORT_MARKERS = (
    "stream disconnected before completion",
    "peer closed connection without sending tls close_notify",
    "error decoding response body",
    "transport error: network error",
    "connection reset by peer",
)
LOGGER = logging.getLogger(__name__)
_MIME_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


def context_window_tokens_for(model: str) -> int | None:
    return _CONTEXT_WINDOW_TOKENS if model == DEFAULT_CODEX_MODEL else None


@dataclass(frozen=True)
class CodexCliExecResult:
    returncode: int
    stdout: str
    stderr: str
    last_message: str
    reconnect_stalled: bool = False
    stall_reason: str | None = None
    session_id: str | None = None
    token_usage: dict[str, int] | None = None
    diagnostics_dir: Path | None = None


@dataclass(frozen=True)
class _RouteSwitchResult:
    previous_hash: str
    current_hash: str


CodexCliRunner = Callable[[list[str], str, float, Path], Awaitable[CodexCliExecResult]]


class CodexCliModel:
    """Use Codex CLI as the model behind the normal Articraft agent loop."""

    supports_images = True

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        runner: CodexCliRunner | None = None,
    ):
        self.config = settings or get_settings()
        self._runner = runner or partial(
            _run_codex_exec,
            reconnect_stall_seconds=self.config.codex_cli_reconnect_stall_seconds,
            response_shadow_seconds=self.config.codex_cli_response_shadow_seconds,
            response_stall_seconds=self.config.codex_cli_response_stall_seconds,
            route_controller_url=self.config.codex_cli_route_controller_url,
            route_controller_secret_file=self.config.codex_cli_route_controller_secret_file,
            telemetry_interval_seconds=self.config.codex_cli_telemetry_interval_seconds,
            retry_session_mode=self.config.codex_cli_retry_session_mode,
            egress_mode=self.config.codex_cli_egress_mode,
            transport_mode=self.config.codex_cli_transport_mode,
            experiment_label=self.config.codex_cli_experiment_label,
        )
        self._uses_default_runner = runner is None
        self._session_id: str | None = None
        self._seen_message_count = 0
        self._route_switched = False
        if runner is None and shutil.which(self.config.codex_cli_bin) is None:
            raise ValueError(
                "Codex CLI provider requires the `codex` executable. "
                "Install and log in to Codex CLI, or set ARTICRAFT_CODEX_CLI_BIN."
            )

    @property
    def context_window_tokens(self) -> int:
        return _CONTEXT_WINDOW_TOKENS

    async def query(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return await self._query(messages, tools=tools or [], summary=False)

    async def summarize_context(
        self,
        messages: list[dict[str, Any]],
        *,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        del max_output_tokens
        return await self._query(messages, tools=[], summary=True)

    async def close(self) -> None:
        if self._route_switched:
            try:
                result = await asyncio.to_thread(
                    _restore_mihomo_fallback_route,
                    self.config.codex_cli_route_controller_url,
                    self.config.codex_cli_route_controller_secret_file,
                )
                LOGGER.info(
                    "Restored Codex fallback route %s -> %s",
                    result.previous_hash,
                    result.current_hash,
                )
            except Exception:
                LOGGER.exception("Could not restore the Codex fallback route")
            self._route_switched = False
        self._session_id = None
        self._seen_message_count = 0

    async def _query(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        summary: bool,
    ) -> dict[str, Any]:
        max_attempts = self.config.codex_cli_max_attempts
        resume_messages = None if summary else self._resume_messages(messages)
        session_id = self._session_id if resume_messages is not None else None
        consecutive_transport_failures = 0
        query_started = time.monotonic()
        recovery_started: float | None = None
        invalid_response_retried = False
        for attempt in range(1, max_attempts + 1):
            attempt_timeout = self.config.codex_cli_timeout_seconds
            if recovery_started is not None:
                recovery_remaining = self.config.codex_cli_recovery_budget_seconds - (
                    time.monotonic() - recovery_started
                )
                if recovery_remaining <= 0.0:
                    raise ModelError("Codex CLI recovery budget exhausted before another attempt")
                attempt_timeout = min(attempt_timeout, recovery_remaining)
            diagnostics_dir = self._create_diagnostics_dir()
            work_dir = diagnostics_dir / "work"
            work_dir.mkdir()
            schema_path = diagnostics_dir / "assistant-turn.schema.json"
            output_path = diagnostics_dir / "assistant-turn.json"
            schema_path.write_text(json.dumps(_OUTPUT_SCHEMA), encoding="utf-8")
            image_dir = diagnostics_dir / "images"
            image_dir.mkdir()
            resume_interrupted = attempt > 1 and session_id is not None
            resume_existing = attempt == 1 and session_id is not None
            if resume_interrupted:
                images: list[Path] = []
                prompt = _render_interrupted_resume_prompt()
            elif resume_existing:
                assert resume_messages is not None
                images, image_names = _materialize_images(resume_messages, image_dir)
                prompt = _render_resume_prompt(resume_messages, image_names)
            else:
                images, image_names = _materialize_images(messages, image_dir)
                prompt = _render_prompt(messages, tools, image_names, summary=summary)
            command = (
                self._resume_command(session_id, schema_path, output_path, images)
                if session_id is not None
                else self._command(
                    schema_path,
                    output_path,
                    images,
                    work_dir,
                    ephemeral=summary,
                )
            )
            try:
                if self._uses_default_runner:
                    result = await self._runner(
                        command,
                        prompt,
                        attempt_timeout,
                        output_path,
                        query_attempt=attempt,
                    )
                else:
                    result = await self._runner(
                        command,
                        prompt,
                        attempt_timeout,
                        output_path,
                    )
            except BaseException:
                _annotate_query_outcome(
                    diagnostics_dir,
                    outcome="runner_failed",
                    attempt=attempt,
                    query_elapsed_seconds=time.monotonic() - query_started,
                    recovery_elapsed_seconds=_elapsed_since(recovery_started),
                )
                raise
            finally:
                shutil.rmtree(image_dir, ignore_errors=True)

            result_session_id = result.session_id or _thread_id(result.stdout)
            if result_session_id and not summary:
                session_id = result_session_id
                self._session_id = result_session_id
            model_errors, optional_mcp_auth = _event_errors(result.stdout)
            if result.returncode != 0 or result.reconnect_stalled:
                transient_error = (
                    f"{result.stall_reason or 'model'} stream made no effective progress"
                    if result.reconnect_stalled
                    else _transient_transport_error(model_errors)
                )
                if transient_error and attempt < max_attempts:
                    if recovery_started is None:
                        recovery_started = time.monotonic()
                    consecutive_transport_failures += 1
                    if (
                        consecutive_transport_failures >= 2
                        and self.config.codex_cli_rotate_route_on_transport_retry
                    ):
                        await self._rotate_route()
                    if self.config.codex_cli_retry_session_mode == "fresh":
                        session_id = None
                        self._session_id = None
                        self._seen_message_count = 0
                    delay = _RETRY_BACKOFF_SECONDS * attempt
                    recovery_remaining = self.config.codex_cli_recovery_budget_seconds - (
                        time.monotonic() - recovery_started
                    )
                    if recovery_remaining <= delay:
                        _annotate_query_outcome(
                            diagnostics_dir,
                            outcome="recovery_budget_exhausted",
                            attempt=attempt,
                            query_elapsed_seconds=time.monotonic() - query_started,
                            recovery_elapsed_seconds=_elapsed_since(recovery_started),
                        )
                        raise ModelError(
                            "Codex CLI recovery budget exhausted after transient failure; "
                            f"diagnostics: {diagnostics_dir}"
                        )
                    _annotate_query_outcome(
                        diagnostics_dir,
                        outcome="retrying",
                        attempt=attempt,
                        query_elapsed_seconds=time.monotonic() - query_started,
                        recovery_elapsed_seconds=_elapsed_since(recovery_started),
                    )
                    LOGGER.warning(
                        "Transient Codex CLI transport failure on attempt %d/%d; retrying "
                        "in %.1fs via %s; diagnostics: %s",
                        attempt,
                        max_attempts,
                        delay,
                        "session resume" if session_id is not None else "a fresh session",
                        diagnostics_dir,
                    )
                    await asyncio.sleep(delay)
                    continue
                detail = transient_error or (
                    model_errors[-1]
                    if model_errors
                    else _redact_stderr(result.stderr).strip() or "no stderr output"
                )
                _annotate_query_outcome(
                    diagnostics_dir,
                    outcome="failed",
                    attempt=attempt,
                    query_elapsed_seconds=time.monotonic() - query_started,
                    recovery_elapsed_seconds=_elapsed_since(recovery_started),
                )
                raise ModelError(
                    f"Codex CLI attempt failed with status {result.returncode}: "
                    f"{detail[-4000:]}; "
                    f"diagnostics: {diagnostics_dir}"
                )
            recovered_transient_errors = (
                bool(model_errors)
                and all(_is_transient_transport_error(error) for error in model_errors)
                and _last_event_type(result.stdout) == "turn.completed"
                and not any(
                    _event_type(line).endswith(".failed") for line in result.stdout.splitlines()
                )
            )
            if model_errors and not recovered_transient_errors:
                _annotate_query_outcome(
                    diagnostics_dir,
                    outcome="failed",
                    attempt=attempt,
                    query_elapsed_seconds=time.monotonic() - query_started,
                    recovery_elapsed_seconds=_elapsed_since(recovery_started),
                )
                raise ModelError(
                    f"Codex CLI reported a model error: {model_errors[-1][-4000:]}; "
                    f"diagnostics: {diagnostics_dir}"
                )
            try:
                payload = _parse_response(result.last_message)
                text = str(payload.get("text") or "")
                tool_calls = _tool_calls(payload.get("tool_calls"))
                if summary and not text.strip():
                    raise ModelError("Codex CLI summary response did not contain text")
                if not summary and not text.strip() and not tool_calls:
                    raise ModelError("Codex CLI response did not contain text or tool calls")
            except ModelError as exc:
                if self.config.codex_cli_retry_invalid_response and attempt < max_attempts:
                    if recovery_started is None:
                        recovery_started = time.monotonic()
                    delay = _RETRY_BACKOFF_SECONDS * attempt
                    recovery_remaining = self.config.codex_cli_recovery_budget_seconds - (
                        time.monotonic() - recovery_started
                    )
                    if recovery_remaining > delay:
                        invalid_response_retried = True
                        session_id = None
                        self._session_id = None
                        self._seen_message_count = 0
                        _annotate_query_outcome(
                            diagnostics_dir,
                            outcome="invalid_response_retrying",
                            attempt=attempt,
                            query_elapsed_seconds=time.monotonic() - query_started,
                            recovery_elapsed_seconds=_elapsed_since(recovery_started),
                        )
                        LOGGER.warning(
                            "Invalid Codex CLI structured response on attempt %d/%d; "
                            "retrying in %.1fs with a fresh session; diagnostics: %s",
                            attempt,
                            max_attempts,
                            delay,
                            diagnostics_dir,
                        )
                        await asyncio.sleep(delay)
                        continue
                _annotate_query_outcome(
                    diagnostics_dir,
                    outcome="invalid_response",
                    attempt=attempt,
                    query_elapsed_seconds=time.monotonic() - query_started,
                    recovery_elapsed_seconds=_elapsed_since(recovery_started),
                )
                raise ModelError(f"{exc}; diagnostics: {diagnostics_dir}") from exc
            redacted_stderr = _redact_stderr(result.stderr)
            stderr_lower = redacted_stderr.lower()
            optional_mcp_auth = optional_mcp_auth or (
                "mcp" in stderr_lower and "authrequirederror" in stderr_lower
            )
            if optional_mcp_auth:
                LOGGER.warning(
                    "Codex CLI reported optional MCP authentication failure; continuing because "
                    "the process exited successfully with a valid final response"
                )
            if recovered_transient_errors:
                LOGGER.warning(
                    "Codex CLI recovered from transient stream errors and completed with a "
                    "valid final response; diagnostics: %s",
                    diagnostics_dir,
                )

            if not summary and session_id is not None:
                self._session_id = session_id
                self._seen_message_count = len(messages)

            _annotate_query_outcome(
                diagnostics_dir,
                outcome=(
                    "invalid_response_recovered"
                    if invalid_response_retried
                    else "recovered"
                    if attempt > 1
                    else "succeeded"
                ),
                attempt=attempt,
                query_elapsed_seconds=time.monotonic() - query_started,
                recovery_elapsed_seconds=_elapsed_since(recovery_started),
            )

            return {
                "text": text,
                "tool_calls": tool_calls,
                "token_usage": result.token_usage
                if result.token_usage is not None
                else _token_usage(result.stdout, result.stderr),
                "cost": 0.0,
                "response": payload,
            }

        raise AssertionError("Codex CLI retry loop did not return or raise")

    async def _rotate_route(self) -> None:
        controller_url = self.config.codex_cli_route_controller_url.strip()
        secret_file = self.config.codex_cli_route_controller_secret_file
        if not controller_url or secret_file is None:
            LOGGER.warning(
                "A second consecutive Codex transport failure occurred, but route rotation "
                "is not configured"
            )
            return
        try:
            result = await asyncio.to_thread(
                _rotate_mihomo_fallback_route,
                controller_url,
                secret_file,
            )
        except Exception:
            LOGGER.exception(
                "A second consecutive Codex transport failure occurred, but route rotation failed"
            )
            return
        self._route_switched = True
        LOGGER.warning(
            "Rotated the dedicated Codex route %s -> %s after a second consecutive transport "
            "failure",
            result.previous_hash,
            result.current_hash,
        )

    def _resume_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        if self._session_id is None:
            return None
        assistant_index = self._seen_message_count
        if assistant_index >= len(messages) or messages[assistant_index].get("role") != "assistant":
            LOGGER.warning(
                "Codex CLI session conversation prefix changed; starting a new persisted session"
            )
            self._session_id = None
            self._seen_message_count = 0
            return None
        return messages[assistant_index + 1 :]

    def _create_diagnostics_dir(self) -> Path:
        root = self.config.output_dir.resolve() / _DIAGNOSTICS_DIRNAME
        root.mkdir(parents=True, exist_ok=True)
        path = root / uuid.uuid4().hex
        path.mkdir()
        return path

    def _command(
        self,
        schema_path: Path,
        output_path: Path,
        images: list[Path],
        work_dir: Path,
        *,
        ephemeral: bool,
    ) -> list[str]:
        command = [
            self.config.codex_cli_bin,
            "exec",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--json",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "-C",
            str(work_dir.resolve()),
        ]
        if ephemeral:
            command.append("--ephemeral")
        command.extend(self._transport_arguments())
        command.extend(self._service_tier_arguments())
        if self.config.codex_model != DEFAULT_CODEX_MODEL:
            command.extend(["--model", self.config.codex_model])
        for image in images:
            command.extend(["--image", str(image.resolve())])
        effort = self.config.selected_reasoning_effort.strip()
        if effort:
            command.extend(["-c", f'model_reasoning_effort="{effort}"'])
        command.append("-")
        return command

    def _resume_command(
        self,
        session_id: str,
        schema_path: Path,
        output_path: Path,
        images: list[Path],
    ) -> list[str]:
        command = [
            self.config.codex_cli_bin,
            "exec",
            "resume",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--json",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
        command.extend(self._transport_arguments())
        command.extend(self._service_tier_arguments())
        if self.config.codex_model != DEFAULT_CODEX_MODEL:
            command.extend(["--model", self.config.codex_model])
        for image in images:
            command.extend(["--image", str(image.resolve())])
        effort = self.config.selected_reasoning_effort.strip()
        if effort:
            command.extend(["-c", f'model_reasoning_effort="{effort}"'])
        command.extend([session_id, "-"])
        return command

    def _service_tier_arguments(self) -> list[str]:
        # Local adaptation: resumes must retain the same requested service tier.
        if self.config.codex_service_tier != "fast":
            return ["-c", 'service_tier="default"', "-c", "features.fast_mode=false"]
        return ["-c", 'service_tier="fast"', "-c", "features.fast_mode=true"]

    def _transport_arguments(self) -> list[str]:
        if self.config.codex_cli_transport_mode == "websocket":
            return []
        force_https = self.config.codex_cli_transport_mode == "https" or (
            self.config.codex_cli_transport_mode == "auto"
            and (
                self.config.codex_cli_force_https
                or bool(self.config.codex_cli_route_controller_url.strip())
            )
        )
        if not force_https:
            return []
        provider = "articraft_chatgpt_https"
        values = (
            f"model_provider={provider}",
            f"model_providers.{provider}.name=ArticraftChatGPTHttps",
            (f"model_providers.{provider}.base_url=https://chatgpt.com/backend-api/codex"),
            f"model_providers.{provider}.wire_api=responses",
            f"model_providers.{provider}.requires_openai_auth=true",
            f"model_providers.{provider}.supports_websockets=false",
        )
        arguments: list[str] = []
        for value in values:
            arguments.extend(["-c", value])
        return arguments


async def _run_codex_exec(
    command: list[str],
    prompt: str,
    timeout_seconds: float,
    output_path: Path,
    *,
    reconnect_stall_seconds: float | None = None,
    response_shadow_seconds: float | None = None,
    response_stall_seconds: float | None = None,
    route_controller_url: str = "",
    route_controller_secret_file: Path | None = None,
    query_attempt: int | None = None,
    telemetry_interval_seconds: float | None = None,
    retry_session_mode: str = "resume",
    egress_mode: str = "proxy",
    transport_mode: str = "auto",
    experiment_label: str = "",
) -> CodexCliExecResult:
    if (
        response_shadow_seconds is not None
        and response_stall_seconds is not None
        and response_shadow_seconds > response_stall_seconds
    ):
        raise ValueError("response shadow deadline must not exceed the hard deadline")
    diagnostics_dir = output_path.parent
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    events_path = diagnostics_dir / "events.jsonl"
    stderr_path = diagnostics_dir / "stderr.log"
    metadata_path = diagnostics_dir / "metadata.json"
    telemetry_path = diagnostics_dir / "telemetry.jsonl"
    output_path.touch(exist_ok=True)
    events_path.touch(exist_ok=True)
    stderr_path.touch(exist_ok=True)
    started = time.monotonic()
    event_lines: list[str] = []
    raw_event_lines: list[str] = []
    event_types: list[str] = []
    event_observations: list[dict[str, Any]] = []
    stderr_lines: list[str] = []
    raw_stderr_lines: list[str] = []
    termination_signals: list[str] = []
    stream_signals: asyncio.Queue[str] = asyncio.Queue()
    reconnect_stalled = False
    stall_reason: str | None = None
    would_have_killed_at_shadow = False
    response_hard_stall_hit = False
    shadow_hit_elapsed_seconds: float | None = None
    hard_stall_hit_elapsed_seconds: float | None = None
    stdout_bytes = 0
    stderr_bytes = 0
    last_stdout_activity_seconds: float | None = None
    last_stderr_activity_seconds: float | None = None
    observed_session_id: str | None = None
    telemetry_sample_count = 0
    telemetry_before = await asyncio.to_thread(
        _safe_mihomo_telemetry,
        route_controller_url,
        route_controller_secret_file,
    )

    async def observability_metadata() -> dict[str, Any]:
        telemetry_after = await asyncio.to_thread(
            _safe_mihomo_telemetry,
            route_controller_url,
            route_controller_secret_file,
        )
        return {
            "query_attempt": query_attempt,
            "session_hash_before": _resume_session_hash(command),
            "session_hash_after": _hash_identifier(observed_session_id),
            "event_observations": event_observations,
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            "last_stdout_activity_seconds": _rounded_optional(last_stdout_activity_seconds),
            "last_stderr_activity_seconds": _rounded_optional(last_stderr_activity_seconds),
            "response_shadow_seconds": response_shadow_seconds,
            "would_have_killed_at_shadow": would_have_killed_at_shadow,
            "shadow_hit_elapsed_seconds": _rounded_optional(shadow_hit_elapsed_seconds),
            "response_hard_stall_hit": response_hard_stall_hit,
            "hard_stall_hit_elapsed_seconds": _rounded_optional(hard_stall_hit_elapsed_seconds),
            "mihomo": _mihomo_telemetry_delta(telemetry_before, telemetry_after),
            "telemetry_sample_count": telemetry_sample_count,
            "retry_session_mode": retry_session_mode,
            "egress_mode": egress_mode,
            "transport_mode": transport_mode,
            "experiment_label": experiment_label,
        }

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_environment(),
            start_new_session=os.name != "nt",
            limit=_STREAM_LIMIT_BYTES,
        )
    except FileNotFoundError as exc:
        _write_metadata(
            metadata_path,
            command,
            duration_seconds=time.monotonic() - started,
            returncode=None,
            timed_out=False,
            event_types=event_types,
            termination_signals=termination_signals,
            output_path=output_path,
            observability=await observability_metadata(),
        )
        raise ModelError("Codex CLI executable was not found") from exc

    async def read_stdout() -> None:
        nonlocal stdout_bytes, last_stdout_activity_seconds, observed_session_id
        assert process.stdout is not None
        with events_path.open("w", encoding="utf-8") as destination:
            while raw_line := await process.stdout.readline():
                stdout_bytes += len(raw_line)
                last_stdout_activity_seconds = time.monotonic() - started
                raw_text = raw_line.decode("utf-8", errors="replace")
                raw_event_lines.append(raw_text)
                observed_session_id = observed_session_id or _thread_id(raw_text) or None
                line = _redact_jsonl_line(raw_text)
                destination.write(line)
                destination.flush()
                event_lines.append(line)
                event_type = _event_type(raw_text)
                if event_type:
                    event_types.append(event_type)
                    event_observations.append(
                        {
                            "type": event_type,
                            "elapsed_seconds": round(
                                last_stdout_activity_seconds,
                                4,
                            ),
                        }
                    )
                stream_signal = _stream_signal(raw_text)
                if stream_signal:
                    stream_signals.put_nowait(stream_signal)

    async def read_stderr() -> None:
        nonlocal stderr_bytes, last_stderr_activity_seconds
        assert process.stderr is not None
        redact_next_token_count = False
        with stderr_path.open("w", encoding="utf-8") as destination:
            while raw_line := await process.stderr.readline():
                stderr_bytes += len(raw_line)
                last_stderr_activity_seconds = time.monotonic() - started
                raw_text = raw_line.decode("utf-8", errors="replace")
                raw_stderr_lines.append(raw_text)
                if redact_next_token_count and re.fullmatch(
                    r"\s*[0-9][0-9,]*\s*",
                    raw_text,
                ):
                    line = "[REDACTED TOKEN COUNT]\n"
                else:
                    line = _redact_stderr(raw_text)
                redact_next_token_count = raw_text.strip().lower() == "tokens used"
                destination.write(line)
                destination.flush()
                stderr_lines.append(line)

    stdout_task = asyncio.create_task(read_stdout())
    stderr_task = asyncio.create_task(read_stderr())

    async def sample_runtime_telemetry() -> None:
        nonlocal telemetry_sample_count
        try:
            with telemetry_path.open("w", encoding="utf-8") as destination:
                while True:
                    mihomo = await asyncio.to_thread(
                        _safe_mihomo_telemetry,
                        route_controller_url,
                        route_controller_secret_file,
                    )
                    now = time.monotonic()
                    sample = {
                        "elapsed_seconds": round(now - started, 4),
                        "pids": _safe_cgroup_pids(),
                        "tcp_states": _safe_tcp_state_counts(),
                        "mihomo": mihomo,
                        "codex": {
                            "event_count": len(event_types),
                            "last_event_type": event_types[-1] if event_types else None,
                            "stdout_bytes": stdout_bytes,
                            "stderr_bytes": stderr_bytes,
                            "stdout_idle_seconds": _idle_seconds(
                                now,
                                started,
                                last_stdout_activity_seconds,
                            ),
                            "stderr_idle_seconds": _idle_seconds(
                                now,
                                started,
                                last_stderr_activity_seconds,
                            ),
                        },
                    }
                    destination.write(json.dumps(sample, sort_keys=True) + "\n")
                    destination.flush()
                    telemetry_sample_count += 1
                    assert telemetry_interval_seconds is not None
                    await asyncio.sleep(telemetry_interval_seconds)
        except OSError:
            LOGGER.exception("Could not persist Codex CLI runtime telemetry")

    telemetry_task = (
        asyncio.create_task(sample_runtime_telemetry())
        if telemetry_interval_seconds is not None
        else None
    )

    async def watch_stream_stall() -> None:
        nonlocal reconnect_stalled, stall_reason, termination_signals
        nonlocal would_have_killed_at_shadow, response_hard_stall_hit
        nonlocal shadow_hit_elapsed_seconds, hard_stall_hit_elapsed_seconds
        hard_deadline: float | None = None
        hard_deadline_reason: str | None = None
        shadow_deadline: float | None = None
        while True:
            deadlines = [
                deadline for deadline in (shadow_deadline, hard_deadline) if deadline is not None
            ]
            timeout = None if not deadlines else max(0.0, min(deadlines) - time.monotonic())
            try:
                stream_signal = await asyncio.wait_for(stream_signals.get(), timeout=timeout)
            except TimeoutError:
                now = time.monotonic()
                if shadow_deadline is not None and now >= shadow_deadline:
                    would_have_killed_at_shadow = True
                    shadow_hit_elapsed_seconds = now - started
                    shadow_deadline = None
                    continue
                if hard_deadline is None or now < hard_deadline:
                    continue
                reconnect_stalled = True
                response_hard_stall_hit = hard_deadline_reason == "response"
                hard_stall_hit_elapsed_seconds = now - started
                stall_reason = hard_deadline_reason
                termination_signals.extend(await _stop_process(process))
                return
            if (
                stream_signal == "turn_started"
                and response_stall_seconds is not None
                and hard_deadline is None
            ):
                now = time.monotonic()
                hard_deadline = now + response_stall_seconds
                hard_deadline_reason = "response"
                if response_shadow_seconds is not None:
                    shadow_deadline = now + response_shadow_seconds
            elif stream_signal == "reconnecting" and reconnect_stall_seconds is not None:
                reconnect_deadline = time.monotonic() + reconnect_stall_seconds
                if hard_deadline is None or reconnect_deadline < hard_deadline:
                    hard_deadline = reconnect_deadline
                    hard_deadline_reason = "reconnecting"
            elif stream_signal == "progress":
                now = time.monotonic()
                hard_deadline = (
                    None if response_stall_seconds is None else now + response_stall_seconds
                )
                hard_deadline_reason = None if hard_deadline is None else "response"
                shadow_deadline = (
                    None if response_shadow_seconds is None else now + response_shadow_seconds
                )
            elif stream_signal == "completed":
                hard_deadline = None
                hard_deadline_reason = None
                shadow_deadline = None

    watchdog_task = (
        asyncio.create_task(watch_stream_stall())
        if reconnect_stall_seconds is not None
        or response_stall_seconds is not None
        or response_shadow_seconds is not None
        else None
    )

    async def wait_for_process() -> None:
        assert process.stdin is not None
        try:
            process.stdin.write(prompt.encode("utf-8"))
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()
        await process.wait()

    timed_out = False
    try:
        await asyncio.wait_for(wait_for_process(), timeout=timeout_seconds)
    except TimeoutError as exc:
        timed_out = True
        termination_signals = await _stop_process(process)
        await _cancel_task(watchdog_task)
        await _cancel_task(telemetry_task)
        await asyncio.gather(stdout_task, stderr_task)
        _write_metadata(
            metadata_path,
            command,
            duration_seconds=time.monotonic() - started,
            returncode=process.returncode,
            timed_out=True,
            event_types=event_types,
            termination_signals=termination_signals,
            output_path=output_path,
            reconnect_stall_seconds=reconnect_stall_seconds,
            response_stall_seconds=response_stall_seconds,
            stall_reason=stall_reason,
            observability=await observability_metadata(),
        )
        last_event = event_types[-1] if event_types else "none"
        stderr_tail = "".join(stderr_lines).strip()[-1000:]
        detail = f"; redacted stderr: {stderr_tail}" if stderr_tail else ""
        raise ModelError(
            f"Codex CLI timed out after {timeout_seconds:g}s; last event: {last_event}; "
            f"diagnostics: {diagnostics_dir}{detail}"
        ) from exc
    except asyncio.CancelledError:
        termination_signals = await _stop_process(process)
        await _cancel_task(watchdog_task)
        await _cancel_task(telemetry_task)
        await asyncio.gather(stdout_task, stderr_task)
        _write_metadata(
            metadata_path,
            command,
            duration_seconds=time.monotonic() - started,
            returncode=process.returncode,
            timed_out=timed_out,
            event_types=event_types,
            termination_signals=termination_signals,
            output_path=output_path,
            reconnect_stall_seconds=reconnect_stall_seconds,
            response_stall_seconds=response_stall_seconds,
            stall_reason=stall_reason,
            observability=await observability_metadata(),
        )
        raise

    await _cancel_task(watchdog_task)
    await _cancel_task(telemetry_task)
    await asyncio.gather(stdout_task, stderr_task)
    last_message = output_path.read_text(encoding="utf-8") if output_path.is_file() else ""
    _write_metadata(
        metadata_path,
        command,
        duration_seconds=time.monotonic() - started,
        returncode=process.returncode,
        timed_out=False,
        event_types=event_types,
        termination_signals=termination_signals,
        output_path=output_path,
        reconnect_stalled=reconnect_stalled,
        reconnect_stall_seconds=reconnect_stall_seconds,
        response_stall_seconds=response_stall_seconds,
        stall_reason=stall_reason,
        observability=await observability_metadata(),
    )
    raw_stdout = "".join(raw_event_lines)
    raw_stderr = "".join(raw_stderr_lines)
    return CodexCliExecResult(
        returncode=int(process.returncode if process.returncode is not None else 0),
        stdout="".join(event_lines),
        stderr="".join(stderr_lines),
        last_message=last_message,
        reconnect_stalled=reconnect_stalled,
        stall_reason=stall_reason,
        session_id=observed_session_id,
        token_usage=_token_usage(raw_stdout, raw_stderr),
        diagnostics_dir=diagnostics_dir,
    )


async def _cancel_task(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _elapsed_since(started: float | None) -> float | None:
    if started is None:
        return None
    return max(0.0, time.monotonic() - started)


def _annotate_query_outcome(
    diagnostics_dir: Path,
    *,
    outcome: str,
    attempt: int,
    query_elapsed_seconds: float,
    recovery_elapsed_seconds: float | None,
) -> None:
    metadata_path = diagnostics_dir / "metadata.json"
    if not metadata_path.is_file():
        return
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(metadata, dict):
        return
    metadata["query_attempt"] = attempt
    metadata["query_outcome"] = outcome
    metadata["query_elapsed_seconds"] = round(query_elapsed_seconds, 4)
    metadata["recovery_elapsed_seconds"] = (
        None if recovery_elapsed_seconds is None else round(recovery_elapsed_seconds, 4)
    )
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


async def _stop_process(process: asyncio.subprocess.Process) -> list[str]:
    sent: list[str] = []
    if process.returncode is not None:
        return sent
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        sent.append("TERM")
    except ProcessLookupError:
        return sent
    try:
        await asyncio.wait_for(process.wait(), timeout=_TERMINATE_GRACE_SECONDS)
    except TimeoutError:
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            sent.append("KILL")
        except ProcessLookupError:
            return sent
        await process.wait()
    return sent


def _write_metadata(
    path: Path,
    command: list[str],
    *,
    duration_seconds: float,
    returncode: int | None,
    timed_out: bool,
    event_types: list[str],
    termination_signals: list[str],
    output_path: Path,
    reconnect_stalled: bool = False,
    reconnect_stall_seconds: float | None = None,
    response_stall_seconds: float | None = None,
    stall_reason: str | None = None,
    observability: Mapping[str, Any] | None = None,
) -> None:
    metadata = {
        "command": _command_summary(command),
        "duration_seconds": round(duration_seconds, 4),
        "returncode": returncode,
        "timed_out": timed_out,
        "event_count": len(event_types),
        "event_types": event_types,
        "last_event_type": event_types[-1] if event_types else None,
        "termination_signals": termination_signals,
        "reconnect_stalled": reconnect_stalled,
        "reconnect_stall_seconds": reconnect_stall_seconds,
        "response_stall_seconds": response_stall_seconds,
        "stall_reason": stall_reason,
        "last_message_present": bool(output_path.stat().st_size)
        if output_path.is_file()
        else False,
    }
    if observability:
        metadata.update(observability)
    path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def _command_summary(command: list[str]) -> dict[str, object]:
    return {
        "executable": Path(command[0]).name if command else None,
        "mode": "resume" if len(command) > 2 and command[2] == "resume" else "new",
        "argument_count": len(command),
    }


def _rounded_optional(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _hash_identifier(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode()).hexdigest()[:10]


def _resume_session_hash(command: list[str]) -> str | None:
    if len(command) <= 4 or command[2] != "resume" or command[-1] != "-":
        return None
    return _hash_identifier(command[-2])


def _idle_seconds(
    now: float,
    started: float,
    last_activity_seconds: float | None,
) -> float | None:
    if last_activity_seconds is None:
        return None
    return round(max(0.0, now - started - last_activity_seconds), 4)


def _safe_cgroup_pids() -> dict[str, int | str | None]:
    roots = (Path("/sys/fs/cgroup"), Path("/sys/fs/cgroup/pids"))
    for root in roots:
        current_path = root / "pids.current"
        max_path = root / "pids.max"
        if not current_path.is_file() or not max_path.is_file():
            continue
        try:
            current = int(current_path.read_text(encoding="utf-8").strip())
            raw_max = max_path.read_text(encoding="utf-8").strip()
            maximum: int | str = int(raw_max) if raw_max.isdigit() else raw_max
            events_max = 0
            events_path = root / "pids.events"
            if events_path.is_file():
                for line in events_path.read_text(encoding="utf-8").splitlines():
                    name, _, raw_value = line.partition(" ")
                    if name == "max" and raw_value.isdigit():
                        events_max = int(raw_value)
            return {"current": current, "max": maximum, "events_max": events_max}
        except (OSError, ValueError):
            break
    return {"current": None, "max": None, "events_max": None}


def _safe_tcp_state_counts() -> dict[str, int]:
    names = {
        "01": "established",
        "02": "syn_sent",
        "03": "syn_received",
        "04": "fin_wait_1",
        "05": "fin_wait_2",
        "06": "time_wait",
        "07": "closed",
        "08": "close_wait",
        "09": "last_ack",
        "0A": "listen",
        "0B": "closing",
    }
    counts: dict[str, int] = {}
    try:
        for path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines()[1:]:
                fields = line.split()
                if len(fields) < 4:
                    continue
                state = names.get(fields[3].upper(), "unknown")
                counts[state] = counts.get(state, 0) + 1
    except OSError:
        return {}
    return counts


def _safe_mihomo_telemetry(
    controller_url: str,
    controller_secret_file: Path | None,
) -> dict[str, Any]:
    if not controller_url.strip() or controller_secret_file is None:
        return {"available": False}
    try:
        connections = _mihomo_request(
            controller_url,
            controller_secret_file,
            "GET",
            "/connections",
        )
        proxies = _mihomo_proxies(controller_url, controller_secret_file)
        _, selector, fallback_name, fallback = _route_groups(proxies)
        selected = str(selector.get("now") or "")
        effective = str(fallback.get("now") or "") if selected == fallback_name else selected
        connection_values = connections.get("connections")
        connection_count = len(connection_values) if isinstance(connection_values, list) else 0
        return {
            "available": True,
            "download_total": int(connections.get("downloadTotal") or 0),
            "upload_total": int(connections.get("uploadTotal") or 0),
            "connection_count": connection_count,
            "route_hash": _hash_route(effective),
        }
    except Exception:
        return {"available": False}


def _mihomo_telemetry_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    if before.get("available") is not True or after.get("available") is not True:
        return {"available": False}
    before_download = int(before.get("download_total") or 0)
    after_download = int(after.get("download_total") or 0)
    before_upload = int(before.get("upload_total") or 0)
    after_upload = int(after.get("upload_total") or 0)
    return {
        "available": True,
        "route_hash_before": before.get("route_hash"),
        "route_hash_after": after.get("route_hash"),
        "download_bytes_delta": max(0, after_download - before_download),
        "upload_bytes_delta": max(0, after_upload - before_upload),
        "connections_before": int(before.get("connection_count") or 0),
        "connections_after": int(after.get("connection_count") or 0),
    }


def _event_type(line: str) -> str:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return ""
    return str(event.get("type") or "") if isinstance(event, dict) else ""


def _stream_signal(line: str) -> str:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return ""
    if not isinstance(event, dict):
        return ""
    event_type = str(event.get("type") or "")
    if event_type == "turn.started":
        return "turn_started"
    if event_type == "error":
        message = str(event.get("message") or "")
        if "reconnecting" in message.lower():
            return "reconnecting"
    item = event.get("item")
    item_type = str(item.get("type") or "") if isinstance(item, dict) else ""
    if event_type == "item.completed" and item_type != "error":
        return "progress"
    if event_type in {"turn.completed", "turn.failed"}:
        return "completed"
    return ""


def _rotate_mihomo_fallback_route(
    controller_url: str,
    controller_secret_file: Path,
) -> _RouteSwitchResult:
    proxies = _mihomo_proxies(controller_url, controller_secret_file)
    selector_name, selector, fallback_name, fallback = _route_groups(proxies)
    fallback_members = [str(name) for name in fallback.get("all") or []]
    selected = str(selector.get("now") or "")
    previous = str(fallback.get("now") or "") if selected == fallback_name else selected
    if previous not in fallback_members:
        raise RuntimeError("the selected Codex route is not a fallback member")
    previous_index = fallback_members.index(previous)
    candidates = fallback_members[previous_index + 1 :] + fallback_members[:previous_index]
    next_route = next(
        (
            name
            for name in candidates
            if name != previous and (proxies.get(name) or {}).get("alive") is True
        ),
        "",
    )
    if not next_route:
        raise RuntimeError("no alternate live Codex fallback route is available")
    _mihomo_select(
        controller_url,
        controller_secret_file,
        selector_name,
        next_route,
    )
    return _RouteSwitchResult(_hash_route(previous), _hash_route(next_route))


def _restore_mihomo_fallback_route(
    controller_url: str,
    controller_secret_file: Path | None,
) -> _RouteSwitchResult:
    if controller_secret_file is None:
        raise RuntimeError("the Mihomo controller secret file is not configured")
    proxies = _mihomo_proxies(controller_url, controller_secret_file)
    selector_name, selector, fallback_name, _ = _route_groups(proxies)
    previous = str(selector.get("now") or "")
    if previous != fallback_name:
        _mihomo_select(
            controller_url,
            controller_secret_file,
            selector_name,
            fallback_name,
        )
    return _RouteSwitchResult(_hash_route(previous), _hash_route(fallback_name))


def _mihomo_proxies(controller_url: str, controller_secret_file: Path) -> dict[str, Any]:
    payload = _mihomo_request(
        controller_url,
        controller_secret_file,
        "GET",
        "/proxies",
    )
    proxies = payload.get("proxies")
    if not isinstance(proxies, dict):
        raise RuntimeError("the Mihomo controller returned an invalid proxy map")
    return proxies


def _route_groups(
    proxies: dict[str, Any],
) -> tuple[str, dict[str, Any], str, dict[str, Any]]:
    matches: list[tuple[str, dict[str, Any], str, dict[str, Any]]] = []
    selectors = [
        (str(name), value)
        for name, value in proxies.items()
        if isinstance(value, dict) and str(value.get("type") or "").lower() == "selector"
    ]
    fallbacks = [
        (str(name), value)
        for name, value in proxies.items()
        if isinstance(value, dict) and str(value.get("type") or "").lower() == "fallback"
    ]
    for selector_name, selector in selectors:
        selector_members = {str(name) for name in selector.get("all") or []}
        for fallback_name, fallback in fallbacks:
            fallback_members = {str(name) for name in fallback.get("all") or []}
            expected_members = {fallback_name, *fallback_members}
            if fallback_members and selector_members == expected_members:
                matches.append((selector_name, selector, fallback_name, fallback))
    if len(matches) != 1:
        raise RuntimeError(f"expected one switchable Codex fallback route, found {len(matches)}")
    return matches[0]


def _mihomo_select(
    controller_url: str,
    controller_secret_file: Path,
    selector_name: str,
    route_name: str,
) -> None:
    _mihomo_request(
        controller_url,
        controller_secret_file,
        "PUT",
        "/proxies/" + urllib.parse.quote(selector_name, safe=""),
        {"name": route_name},
    )


def _mihomo_request(
    controller_url: str,
    controller_secret_file: Path,
    method: str,
    path: str,
    payload: dict[str, str] | None = None,
) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(controller_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "::1",
        "localhost",
        "mihomo-fallback",
    }:
        raise ValueError(
            "the Mihomo controller URL must use loopback or the isolated mihomo-fallback service"
        )
    secret = controller_secret_file.read_text(encoding="utf-8").strip()
    if not secret:
        raise ValueError("the Mihomo controller secret is empty")
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        controller_url.rstrip("/") + path,
        data=body,
        method=method,
        headers={
            "Authorization": "Bearer " + secret,
            "Content-Type": "application/json",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        raw = response.read()
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("the Mihomo controller returned a non-object response")
    return value


def _hash_route(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()[:10]


def _thread_id(stdout: str) -> str:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started":
            return str(event.get("thread_id") or "")
    return ""


def _event_errors(stdout: str) -> tuple[list[str], bool]:
    errors: list[str] = []
    optional_mcp_auth = False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        item = event.get("item")
        item_type = str(item.get("type") or "") if isinstance(item, dict) else ""
        if event_type != "error" and not event_type.endswith(".failed") and item_type != "error":
            continue
        detail = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        lowered = detail.lower()
        if "mcp" in lowered and "authrequirederror" in lowered:
            optional_mcp_auth = True
            continue
        message = event.get("message")
        error = event.get("error")
        if not message and isinstance(error, dict):
            message = error.get("message")
        if not message and isinstance(item, dict):
            message = item.get("message") or item.get("error")
        errors.append(str(message or detail))
    return errors, optional_mcp_auth


def _transient_transport_error(errors: list[str]) -> str:
    for error in reversed(errors):
        if _is_transient_transport_error(error):
            return error
    return ""


def _is_transient_transport_error(error: str) -> bool:
    lowered = error.lower()
    # CLI 0.159 reports retryable request timeouts without the older transport prefix.
    reconnect_timeout = re.fullmatch(r"reconnecting\.\.\. \d+/\d+ \(request timed out\)", lowered)
    return bool(reconnect_timeout) or any(marker in lowered for marker in _TRANSIENT_TRANSPORT_MARKERS)


def _last_event_type(stdout: str) -> str:
    last_event = ""
    for line in stdout.splitlines():
        event_type = _event_type(line)
        if event_type:
            last_event = event_type
    return last_event


def _redact_jsonl_line(line: str) -> str:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return _redact_stderr(line)
    redacted = _redact_json_value(event)
    if redacted == event:
        return line
    suffix = "\n" if line.endswith("\n") else ""
    return json.dumps(redacted, ensure_ascii=False, separators=(",", ":")) + suffix


def _redact_json_value(value: Any, *, key: str = "") -> Any:
    sensitive_keys = {
        "access_token",
        "account_id",
        "api_key",
        "auth",
        "authentication",
        "authorization",
        "cookie",
        "credentials",
        "cached_input_tokens",
        "email",
        "id_token",
        "input_tokens",
        "output_tokens",
        "password",
        "refresh_token",
        "secret",
        "thread_id",
        "token",
        "token_usage",
        "total_tokens",
        "turn_id",
    }
    if key.lower().replace("-", "_") in sensitive_keys:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): _redact_json_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json_value(item) for item in value]
    if isinstance(value, str):
        return _redact_stderr(value)
    return value


def _redact_stderr(value: str) -> str:
    if len(re.findall(r"\b[A-Z][A-Z0-9_]{2,}=", value)) >= 3:
        return (
            "[REDACTED ENVIRONMENT DUMP]\n"
            if value.endswith("\n")
            else "[REDACTED ENVIRONMENT DUMP]"
        )
    redacted = re.sub(
        r"(?i)(www[_-]?authenticate[_-]?header\s*:\s*)(\"(?:\\.|[^\"])*\"|[^\r\n)}]+)",
        r"\1[REDACTED]",
        value,
    )
    redacted = re.sub(
        r"(?i)(authorization\s*:\s*(?:bearer\s+)?)[^\s,;]+",
        r"\1[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|account[_-]?id|"
        r"password|secret|cookie)\b(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1\2[REDACTED]",
        redacted,
    )
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", redacted)
    redacted = re.sub(
        r"(?im)(tokens used\s*(?:\r?\n|\s)+)[0-9][0-9,]*",
        r"\1[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "[REDACTED_EMAIL]",
        redacted,
    )
    return re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[REDACTED]", redacted)


def _render_prompt(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    image_names: dict[str, str],
    *,
    summary: bool,
) -> str:
    task = (
        "Summarize the supplied conversation into a compact checkpoint. Return the summary in "
        "text and return an empty tool_calls list."
        if summary
        else (
            "Return exactly one assistant turn. Use tool_calls when Articraft should act next; "
            "return text without tool_calls only when concluding or reporting a blocker."
        )
    )
    return "\n\n".join(
        [
            "You are the Codex CLI model transport for Articraft's existing agent.",
            (
                "Do not edit files, run shell commands, or use Codex tools. Articraft will execute "
                "the declared function calls, compile the model, and preserve the run record."
            ),
            task,
            "<available_tools>\n"
            + json.dumps(_tool_reference(tools), indent=2, ensure_ascii=False)
            + "\n</available_tools>",
            "<conversation>\n"
            + json.dumps(
                _conversation_reference(messages, image_names),
                indent=2,
                ensure_ascii=False,
            )
            + "\n</conversation>",
            (
                "Each tool call must name an available tool and encode its arguments as a JSON "
                "object string. Return JSON matching the required output schema."
            ),
        ]
    )


def _render_resume_prompt(
    messages: list[dict[str, Any]],
    image_names: dict[str, str],
) -> str:
    return "\n\n".join(
        [
            "Continue the same Articraft task in this existing Codex session.",
            (
                "Articraft executed your previous function calls. Use only the new conversation "
                "items below to decide the next assistant turn."
            ),
            "Do not edit files, run shell commands, or use Codex tools.",
            "<conversation_delta>\n"
            + json.dumps(
                _conversation_reference(messages, image_names),
                indent=2,
                ensure_ascii=False,
            )
            + "\n</conversation_delta>",
            (
                "Return exactly one assistant turn matching the required output schema. Use "
                "tool_calls when Articraft should act next; otherwise return concluding text."
            ),
        ]
    )


def _render_interrupted_resume_prompt() -> str:
    return (
        "Continue the response interrupted by a transport failure. Do not restart the task or "
        "repeat completed work. Return exactly one assistant turn matching the required output "
        "schema."
    )


def _tool_reference(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": str(tool.get("name") or ""),
            "description": str(tool.get("description") or ""),
            "parameters": tool.get("parameters") or {"type": "object"},
        }
        for tool in tools
        if tool.get("type") == "function"
    ]


def _conversation_reference(
    messages: list[dict[str, Any]],
    image_names: dict[str, str],
) -> list[dict[str, Any]]:
    rendered: list[dict[str, Any]] = []
    for message in messages:
        if message.get("type") == "function_call_output":
            rendered.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("call_id") or ""),
                    "output": _content_reference(message.get("output"), image_names),
                }
            )
            continue
        item: dict[str, Any] = {
            "role": str(message.get("role") or ""),
            "content": _content_reference(message.get("content"), image_names),
        }
        calls = message.get("tool_calls")
        if isinstance(calls, list) and calls:
            item["tool_calls"] = calls
        rendered.append(item)
    return rendered


def _content_reference(content: Any, image_names: dict[str, str]) -> Any:
    if not isinstance(content, list):
        return content
    rendered: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") in {"input_text", "text"}:
            rendered.append({"type": "text", "text": str(part.get("text") or "")})
        elif part.get("type") in {"input_image", "image"}:
            url = str(part.get("image_url") or "")
            rendered.append(
                {
                    "type": "image",
                    "attachment": image_names.get(url, "unavailable"),
                    "detail": str(part.get("detail") or ""),
                }
            )
        else:
            rendered.append(dict(part))
    return rendered


def _materialize_images(
    messages: list[dict[str, Any]],
    directory: Path,
) -> tuple[list[Path], dict[str, str]]:
    paths: list[Path] = []
    names: dict[str, str] = {}
    for message in messages:
        for field in ("content", "output"):
            value = message.get(field)
            if not isinstance(value, list):
                continue
            for part in value:
                if not isinstance(part, dict) or part.get("type") not in {
                    "input_image",
                    "image",
                }:
                    continue
                url = str(part.get("image_url") or "")
                if not url or url in names:
                    continue
                mime_type, data = _decode_data_url(url)
                digest = hashlib.sha256(data).hexdigest()[:12]
                path = directory / f"image-{len(paths) + 1}-{digest}{_MIME_SUFFIXES[mime_type]}"
                path.write_bytes(data)
                paths.append(path)
                names[url] = path.name
    return paths, names


def _decode_data_url(url: str) -> tuple[str, bytes]:
    match = re.fullmatch(r"data:([^;,]+);base64,(.+)", url, flags=re.DOTALL)
    if match is None or match.group(1) not in _MIME_SUFFIXES:
        raise ModelError("Codex CLI image input must be a PNG, JPEG, or WebP data URL")
    try:
        return match.group(1), base64.b64decode(match.group(2), validate=True)
    except ValueError as exc:
        raise ModelError("Codex CLI image input contains invalid base64 data") from exc


def _parse_response(raw: str) -> dict[str, Any]:
    if not raw.strip():
        raise ModelError("Codex CLI did not write an assistant response")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelError("Codex CLI assistant response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ModelError("Codex CLI assistant response must be a JSON object")
    if set(payload) != {"text", "tool_calls"}:
        raise ModelError("Codex CLI assistant response did not match the required schema")
    if not isinstance(payload["text"], str):
        raise ModelError("Codex CLI assistant response text must be a string")
    return payload


def _tool_calls(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ModelError("Codex CLI tool_calls must be a list")
    calls: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            raise ModelError("Codex CLI returned an invalid tool call")
        arguments = item.get("arguments")
        try:
            arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError as exc:
            raise ModelError("Codex CLI tool arguments were not valid JSON") from exc
        if not isinstance(arguments, dict):
            raise ModelError("Codex CLI tool arguments must be a JSON object")
        calls.append(
            {
                "id": f"call_codex_{uuid.uuid4().hex}",
                "name": str(item["name"]),
                "arguments": json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
            }
        )
    return calls


def _token_usage(stdout: str, stderr: str) -> dict[str, int]:
    matches = re.findall(
        r"tokens used\s*(?:\r?\n|\s)+([0-9][0-9,]*)",
        "\n".join((stdout, stderr)),
        flags=re.IGNORECASE,
    )
    return {"total_tokens": int(matches[-1].replace(",", ""))} if matches else {}


_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text": {"type": "string"},
        "tool_calls": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "arguments": {
                        "type": "string",
                        "description": "JSON object string containing the tool arguments.",
                    },
                },
                "required": ["name", "arguments"],
            },
        },
    },
    "required": ["text", "tool_calls"],
}


__all__ = ["CodexCliExecResult", "CodexCliModel", "context_window_tokens_for"]
