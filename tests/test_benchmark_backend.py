import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import benchmark_codex


@pytest.mark.parametrize(
    "module", ["scripts.compare_sol56", "scripts.benchmark_luna_fast", "scripts.probe_fast_tier"]
)
def test_documented_tool_module_entrypoints_do_not_call_models(module):
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_direct_script_entrypoint_is_independent_of_python_path(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_codex.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--backend" in result.stdout


@pytest.mark.parametrize(
    "backend,provider,port", [("api", "compatible-api", 18688), ("codex", "codex-cli", 18689)]
)
def test_dry_run_selects_backend_without_submitting(
    tmp_path, monkeypatch, capsys, backend, provider, port
):
    urls = []

    class Client:
        def __init__(self, url):
            urls.append(url)

        def health(self):
            return {"ok": True, "provider": provider, "model": "offline-model"}

        def close(self):
            pass

    monkeypatch.setattr(benchmark_codex, "ROOT", tmp_path)
    monkeypatch.setattr(benchmark_codex, "ArticulationClient", Client)
    monkeypatch.setattr(sys, "argv", ["benchmark", "--backend", backend])
    assert benchmark_codex.main() == 0
    assert urls == [f"http://127.0.0.1:{port}"]
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
