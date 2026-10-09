import subprocess
import sys
from pathlib import Path

from local import CODEX_RUNTIME_PROFILE
from scripts.compare_sol56 import MODEL, controlled_environment, wait_for_exit


def test_sol56_switch_preserves_non_model_settings():
    original = {
        "ARTICRAFT_CODEX_MODEL": "codex-cli-default",
        "ARTICRAFT_MODEL": "codex-cli-default",
        "ARTICRAFT_REASONING_EFFORT": "xhigh",
        "HTTPS_PROXY": "http://127.0.0.1:7897",
        "ARTICRAFT_CODEX_CLI_TIMEOUT_SECONDS": "900",
        "ARTICRAFT_CODEX_CLI_RESPONSE_STALL_SECONDS": "120",
        "ARTICULATION_CONCURRENCY": "1",
    }
    medium = controlled_environment(original, "medium")
    assert medium["ARTICRAFT_CODEX_MODEL"] == medium["ARTICRAFT_MODEL"] == MODEL
    assert original["ARTICRAFT_CODEX_MODEL"] == "codex-cli-default"
    assert all(
        medium[key] == value
        for key, value in original.items()
        if key not in {"ARTICRAFT_CODEX_MODEL", "ARTICRAFT_MODEL", "ARTICRAFT_REASONING_EFFORT"}
    )
    xhigh = controlled_environment(medium, "xhigh")
    assert {key for key in medium if medium[key] != xhigh[key]} == {"ARTICRAFT_REASONING_EFFORT"}


def test_restart_wait_reaps_its_own_exited_child():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    wait_for_exit(child.pid, timeout=3)
    assert not Path(f"/proc/{child.pid}").exists()
    child.wait()


def test_explicit_alignment_changes_only_public_runtime_profile():
    original = {
        "ARTICRAFT_CODEX_MODEL": "gpt-6-luna",
        "ARTICRAFT_REASONING_EFFORT": "xhigh",
        "ARTICRAFT_CODEX_SERVICE_TIER": "fast",
        "ARTICRAFT_CODEX_CLI_RESPONSE_STALL_SECONDS": "120",
        "HTTPS_PROXY": "http://127.0.0.1:7897",
        "CODEX_HOME": "/existing/codex",
        "ARTICRAFT_CODEX_CLI_BIN": "/existing/codex/bin",
    }
    aligned = controlled_environment(original, "low", align_runtime=True)
    assert all(aligned[key] == value for key, value in CODEX_RUNTIME_PROFILE.items())
    assert all(
        aligned[key] == value for key, value in original.items() if key not in CODEX_RUNTIME_PROFILE
    )
    assert original["ARTICRAFT_CODEX_SERVICE_TIER"] == "fast"
