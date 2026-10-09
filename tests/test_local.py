import os
from pathlib import Path

import pytest
from articraft.agent.provider.codex_cli import CodexCliModel
from articraft.settings import Settings
from articulation_server.config import ServerConfig

from local import environment, main, select_env_file


def test_project_env_preferred_with_legacy_fallback(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    legacy = tmp_path / "baseline/embodichain/gen_sim/.env"
    legacy.parent.mkdir(parents=True)
    assert select_env_file(root) == root / ".env"
    legacy.write_text("legacy")
    assert select_env_file(root) == legacy
    (root / ".env").write_text("project")
    assert select_env_file(root) == root / ".env"


def test_api_mode_and_effort_can_come_from_dotenv(tmp_path):
    source = tmp_path / ".env"
    source.write_text(
        'OPENAI_API_KEY="secret"\nOPENAI_BASE_URL="https://example.invalid/v1"\nOPENAI_MODEL="openai/gpt-5.6-sol"\nOPENAI_API_MODE="responses"\nOPENAI_REASONING_EFFORT="low"\n'
    )
    env = environment("api", env_file=source, inherited={})
    assert env["ARTICRAFT_COMPATIBLE_API_MODE"] == "responses"
    assert env["ARTICRAFT_REASONING_EFFORT"] == "low"
    assert (
        environment("api", env_file=source, effort="medium", inherited={})[
            "ARTICRAFT_REASONING_EFFORT"
        ]
        == "medium"
    )


def test_benchmark_launcher_sets_runtime_libraries_and_forwards_args(monkeypatch):
    calls = []

    def execute(executable, command, env):
        calls.append((executable, command, env))
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr(
        os.sys,
        "argv",
        [
            "local.py",
            "benchmark",
            "--backend",
            "api",
            "--port",
            "18788",
            "--run",
            "--continue-on-failure",
        ],
    )
    monkeypatch.setattr(os, "execve", execute)
    with pytest.raises(RuntimeError, match="exec intercepted"):
        main()
    assert calls[0][1][-5:] == ["api", "--port", "18788", "--run", "--continue-on-failure"]
    assert calls[0][2]["LD_LIBRARY_PATH"].split(os.pathsep)[0] == str(
        Path(os.sys.base_prefix) / "lib"
    )


def test_api_loads_only_model_config_and_preserves_source(tmp_path):
    source = tmp_path / ".env"
    content = 'OPENAI_API_KEY="secret"\nOPENAI_BASE_URL="https://example.invalid/v1"\nOPENAI_MODEL="test-model"\nSCENE_ENGINE_IMAGE_GENERATION_BASE_URL="remote"\nEMBODICHAIN_LLM_PROXY=""\n'
    source.write_text(content)
    env = environment("api", env_file=source, inherited={"OPENAI_API_KEY": "wrong"})
    assert env["OPENAI_API_KEY"] == "secret"
    assert env["ARTICRAFT_MODEL"] == "test-model"
    assert env["ARTICRAFT_PROVIDER"] == "compatible-api"
    assert env["ARTICULATION_HOST"] == "127.0.0.1"
    assert "SCENE_ENGINE_IMAGE_GENERATION_BASE_URL" not in env
    assert source.read_text() == content
    ServerConfig.from_env(env)


def test_responses_api_effort_is_explicit_without_changing_dotenv(tmp_path):
    source = tmp_path / ".env"
    content = 'OPENAI_API_KEY="secret"\nOPENAI_BASE_URL="https://example.invalid/v1"\nOPENAI_MODEL="openai/gpt-5.6-sol"\n'
    source.write_text(content)
    env = environment("api", env_file=source, effort="low", api_mode="responses", inherited={})
    settings = Settings(_env_file=None, **env)
    assert settings.compatible_api_mode == "responses"
    assert settings.openai_reasoning_effort == "low"
    assert source.read_text() == content
    default = environment("api", env_file=source, inherited={})
    assert default["ARTICRAFT_COMPATIBLE_API_MODE"] == "chat"
    assert "ARTICRAFT_REASONING_EFFORT" not in default
    with pytest.raises(ValueError, match="Responses"):
        environment("api", env_file=source, effort="low", inherited={})


def test_codex_does_not_load_api_credentials(tmp_path):
    env = environment(
        "codex",
        env_file=tmp_path / "absent",
        inherited={
            "OPENAI_API_KEY": "do-not-inherit",
            "CODEX_HOME": "/existing/codex",
        },
    )
    assert "OPENAI_API_KEY" not in env
    assert env["CODEX_HOME"] == "/existing/codex"
    assert env["ARTICRAFT_PROVIDER"] == "codex-cli"
    assert env["ARTICRAFT_REASONING_EFFORT"] == "low"
    assert env["ARTICULATION_PORT"] == "18689"
    assert env["ARTICRAFT_CODEX_CLI_ROTATE_ROUTE_ON_TRANSPORT_RETRY"] == "false"


def test_codex_cloud_profile_preserves_proxy_and_login():
    inherited = {
        "HTTPS_PROXY": "http://127.0.0.1:7897",
        "ARTICRAFT_CODEX_CLI_RESPONSE_STALL_SECONDS": "120",
        "ARTICRAFT_CODEX_CLI_TIMEOUT_SECONDS": "900",
        "CODEX_HOME": "/existing/codex",
    }
    env = environment("codex", inherited=inherited)
    assert env["HTTPS_PROXY"] == inherited["HTTPS_PROXY"]
    assert env["CODEX_HOME"] == inherited["CODEX_HOME"]
    assert env["ARTICRAFT_CODEX_MODEL"] == "gpt-5.6-sol"
    assert env["ARTICRAFT_CODEX_SERVICE_TIER"] == "default"
    config = Settings(_env_file=None, **env)
    assert config.codex_cli_transport_mode == "https"
    assert config.codex_cli_force_https is True
    assert config.codex_cli_response_shadow_seconds == 120
    assert config.codex_cli_response_stall_seconds == 180
    assert config.codex_cli_reconnect_stall_seconds == 25
    assert config.codex_cli_max_attempts == 3
    assert config.codex_cli_timeout_seconds == 1800
    assert config.codex_cli_recovery_budget_seconds == 600
    assert config.codex_cli_retry_session_mode == "resume"
    assert config.codex_cli_retry_invalid_response is True
    assert config.max_turns == 100
    assert config.compile_timeout_seconds == 900
    server = ServerConfig.from_env(env)
    assert server.job_timeout_seconds == 7200
    assert server.concurrency == 1


@pytest.mark.parametrize("resume", [False, True])
def test_cloud_profile_reaches_initial_and_resume_commands(resume):
    config = Settings(_env_file=None, **environment("codex", inherited={}))
    model = CodexCliModel(config, runner=lambda *_args: None)
    schema, output = Path("schema.json"), Path("output.json")
    command = (
        model._resume_command("session", schema, output, [])
        if resume
        else model._command(schema, output, [], Path.cwd(), ephemeral=False)
    )
    assert 'model_reasoning_effort="low"' in command
    assert command[command.index("--model") + 1] == "gpt-5.6-sol"
    assert "model_providers.articraft_chatgpt_https.supports_websockets=false" in command
    assert 'service_tier="fast"' not in command
    assert 'service_tier="default"' in command
    assert "features.fast_mode=false" in command


def test_codex_effort_override_changes_only_effort():
    medium = environment("codex", effort="medium", inherited={})
    xhigh = environment("codex", effort="xhigh", inherited={})
    assert {key for key in medium if medium[key] != xhigh[key]} == {"ARTICRAFT_REASONING_EFFORT"}
    assert medium["ARTICRAFT_REASONING_EFFORT"] == "medium"


def test_missing_api_file_and_credentials_fail(tmp_path):
    source = tmp_path / ".env"
    with pytest.raises(FileNotFoundError):
        environment("api", env_file=source, inherited={})
    source.write_text('OPENAI_BASE_URL="https://example.invalid/v1"\n')
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        environment("api", env_file=source, inherited={})


def test_worker_keeps_virtual_environment_executable(tmp_path):
    executable = tmp_path / "venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(Path(os.sys.executable).resolve())
    config = ServerConfig.from_env({"ARTICULATION_PYTHON": str(executable)})
    assert config.python_executable == executable


def test_client_entrypoint_invokes_main(monkeypatch):
    commands = []

    def execute(executable, command):
        commands.append((executable, command))
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr(os.sys, "argv", ["local.py", "client", "--backend", "api", "health"])
    monkeypatch.setattr(os, "execv", execute)
    with pytest.raises(RuntimeError, match="exec intercepted"):
        main()
    assert commands[0][1][1:3] == [
        "-c",
        "from articulation_server.client import main; main()",
    ]
    assert commands[0][1][-3:] == ["--base-url", "http://127.0.0.1:18688", "health"]
