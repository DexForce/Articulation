from pathlib import Path

import pytest
from articraft.agent.provider.codex_cli import CodexCliModel
from articraft.settings import Settings

from local import environment
from scripts.compare_sol56 import controlled_environment


@pytest.mark.parametrize("resume", [False, True])
def test_fast_is_forwarded_on_initial_and_resume(resume):
    config = Settings(
        _env_file=None,
        provider="codex-cli",
        codex_model="gpt-6-luna",
        codex_service_tier="fast",
        openai_reasoning_effort="xhigh",
    )
    model = CodexCliModel(config, runner=lambda *_args: None)
    schema, output = Path("schema.json"), Path("output.json")
    command = (
        model._resume_command("session", schema, output, [])
        if resume
        else model._command(schema, output, [], Path.cwd(), ephemeral=False)
    )
    assert 'service_tier="fast"' in command
    assert "features.fast_mode=true" in command
    assert 'model_reasoning_effort="xhigh"' in command
    assert command[command.index("--model") + 1] == "gpt-6-luna"
    assert "--ignore-user-config" in command


def test_standard_does_not_add_fast_arguments():
    model = CodexCliModel(Settings(_env_file=None, provider="codex-cli"), runner=lambda *_: None)
    assert model._service_tier_arguments() == [
        "-c",
        'service_tier="default"',
        "-c",
        "features.fast_mode=false",
    ]


def test_fast_environment_preserves_other_experiment_controls():
    old = {
        "HTTPS_PROXY": "http://127.0.0.1:7897",
        "ARTICRAFT_CODEX_CLI_RESPONSE_STALL_SECONDS": "120",
    }
    env = controlled_environment(old, "xhigh", model="gpt-6-luna", service_tier="fast")
    assert all(env[key] == value for key, value in old.items())
    assert env["ARTICRAFT_CODEX_SERVICE_TIER"] == "fast"
    launch = environment("codex", model="gpt-6-luna", effort="xhigh", fast=True, inherited=old)
    assert launch["ARTICRAFT_CODEX_SERVICE_TIER"] == "fast"
    with pytest.raises(ValueError, match="Codex"):
        environment("api", fast=True, inherited={})
