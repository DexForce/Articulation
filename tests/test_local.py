import os
from pathlib import Path

import pytest
from articulation_server.config import ServerConfig

from local import environment, main


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
    assert env["ARTICRAFT_REASONING_EFFORT"] == "xhigh"
    assert env["ARTICULATION_PORT"] == "18689"
    assert env["ARTICRAFT_CODEX_CLI_ROTATE_ROUTE_ON_TRANSPORT_RETRY"] == "false"


def test_xhigh_does_not_change_codex_timeouts_or_proxy():
    inherited = {
        "HTTPS_PROXY": "http://127.0.0.1:7897",
        "ARTICRAFT_CODEX_CLI_RESPONSE_STALL_SECONDS": "120",
        "ARTICRAFT_CODEX_CLI_TIMEOUT_SECONDS": "900",
    }
    env = environment("codex", inherited=inherited)
    assert all(env[key] == value for key, value in inherited.items())
    assert env["ARTICRAFT_CODEX_MODEL"] == "codex-cli-default"


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
