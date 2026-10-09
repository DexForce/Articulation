import asyncio
import json

import pytest
from articraft.agent.provider.codex_cli import CodexCliExecResult, CodexCliModel
from articraft.errors import ModelError
from articraft.settings import Settings

RECONNECT_TIMEOUT = "Reconnecting... 2/5 (request timed out)"
# Reduced from the failed medium/Stapler turn; no generated CAD or credentials.
STAPLER_RESPONSE = {
    "text": "",
    "tool_calls": [
        {
            "name": "view_image",
            "arguments": '{"path":"qa/previews/stapler_overall.png","detail":"original"}',
        },
        {
            "name": "view_image",
            "arguments": '{"path":"qa/previews/stapler_motion.png","detail":"original"}',
        },
    ],
}


def events(*items):
    return "\n".join(json.dumps(item) for item in items) + "\n"


def result(*items, response=STAPLER_RESPONSE, **kwargs):
    return CodexCliExecResult(
        returncode=kwargs.pop("returncode", 0),
        stdout=events(*items),
        stderr="",
        last_message=json.dumps(response),
        **kwargs,
    )


def run_query(tmp_path, results, monkeypatch, **settings):
    commands, timeouts = [], []

    async def runner(command, _prompt, timeout, _output):
        commands.append(command)
        timeouts.append(timeout)
        return results[len(commands) - 1]

    async def no_delay(_seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_delay)
    config = Settings(_env_file=None, output_dir=tmp_path, provider="codex-cli", **settings)
    model = CodexCliModel(config, runner=runner)
    return (
        asyncio.run(model.query([{"role": "user", "content": "offline replay"}])),
        commands,
        timeouts,
    )


def test_completed_stapler_turn_survives_intermediate_timeout(tmp_path, monkeypatch):
    response, commands, _ = run_query(
        tmp_path,
        [result({"type": "error", "message": RECONNECT_TIMEOUT}, {"type": "turn.completed"})],
        monkeypatch,
    )
    assert len(commands) == 1
    assert [call["name"] for call in response["tool_calls"]] == ["view_image", "view_image"]
    assert json.loads(response["tool_calls"][0]["arguments"])["path"].endswith(
        "stapler_overall.png"
    )


@pytest.mark.parametrize(
    "message",
    ["Unauthorized: 401", "Quota exceeded", "Unknown model failure", "Tool request timed out"],
)
def test_fatal_or_unknown_error_is_not_hidden_by_completed_turn(tmp_path, monkeypatch, message):
    with pytest.raises(ModelError, match="reported a model error"):
        run_query(
            tmp_path,
            [result({"type": "error", "message": message}, {"type": "turn.completed"})],
            monkeypatch,
        )


@pytest.mark.parametrize("terminal", ["turn.failed", "turn.started"])
def test_timeout_without_completed_turn_is_rejected(tmp_path, monkeypatch, terminal):
    with pytest.raises(ModelError):
        run_query(
            tmp_path,
            [result({"type": "error", "message": RECONNECT_TIMEOUT}, {"type": terminal})],
            monkeypatch,
            codex_cli_max_attempts=1,
        )


def test_terminal_failure_is_not_hidden_by_a_later_completed_event(tmp_path, monkeypatch):
    with pytest.raises(ModelError):
        run_query(
            tmp_path,
            [
                result(
                    {
                        "type": "turn.failed",
                        "error": {"message": "stream disconnected before completion"},
                    },
                    {"type": "turn.completed"},
                )
            ],
            monkeypatch,
        )


@pytest.mark.parametrize(
    "failure", [{"returncode": 1}, {"reconnect_stalled": True, "stall_reason": "response"}]
)
def test_completion_does_not_override_process_failure_or_watchdog(tmp_path, monkeypatch, failure):
    with pytest.raises(ModelError, match="attempt failed"):
        run_query(
            tmp_path,
            [
                result(
                    {"type": "error", "message": RECONNECT_TIMEOUT},
                    {"type": "turn.completed"},
                    **failure,
                )
            ],
            monkeypatch,
            codex_cli_max_attempts=1,
        )


@pytest.mark.parametrize(
    "invalid",
    [
        "not JSON",
        {"text": "", "tool_calls": []},
        {"text": "", "tool_calls": [{"name": "view_image", "arguments": "{invalid"}]},
    ],
)
def test_recovered_transport_still_requires_valid_response(tmp_path, monkeypatch, invalid):
    with pytest.raises(ModelError):
        run_query(
            tmp_path,
            [
                result(
                    {"type": "error", "message": RECONNECT_TIMEOUT},
                    {"type": "turn.completed"},
                    response=invalid,
                )
            ],
            monkeypatch,
            codex_cli_retry_invalid_response=False,
        )


def test_unrecovered_timeout_retries_by_resume_with_bounded_budget(tmp_path, monkeypatch):
    response, commands, timeouts = run_query(
        tmp_path,
        [
            result(
                {"type": "error", "message": RECONNECT_TIMEOUT},
                returncode=1,
                session_id="saved-session",
            ),
            result({"type": "turn.completed"}),
        ],
        monkeypatch,
        codex_cli_max_attempts=3,
        codex_cli_timeout_seconds=1800,
        codex_cli_recovery_budget_seconds=600,
        codex_cli_retry_session_mode="resume",
    )
    assert response["tool_calls"]
    assert commands[1][1:3] == ["exec", "resume"]
    assert "saved-session" in commands[1]
    assert timeouts[0] == 1800
    assert 0 < timeouts[1] <= 600


def test_invalid_response_retries_once_with_fresh_context(tmp_path, monkeypatch):
    response, commands, timeouts = run_query(
        tmp_path,
        [
            result(
                {"type": "turn.completed"},
                response={"text": "", "tool_calls": []},
                session_id="invalid-session",
            ),
            result({"type": "turn.completed"}),
        ],
        monkeypatch,
        codex_cli_retry_invalid_response=True,
        codex_cli_max_attempts=3,
        codex_cli_timeout_seconds=1800,
        codex_cli_recovery_budget_seconds=600,
    )
    assert response["tool_calls"]
    assert commands[1][1] == "exec" and "resume" not in commands[1]
    assert "invalid-session" not in commands[1]
    assert 0 < timeouts[1] <= 600
