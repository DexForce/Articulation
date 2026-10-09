"""Controlled medium/xhigh benchmark of the explicitly requested GPT-5.6-Sol."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from articulation_server.client import ArticulationClient

from local import CODEX_RUNTIME_PROFILE

ROOT = Path(__file__).resolve().parents[1]
MODEL = "gpt-5.6-sol"
CONFIG_KEYS = {
    "ARTICRAFT_CODEX_MODEL",
    "ARTICRAFT_MODEL",
    "ARTICRAFT_REASONING_EFFORT",
    "ARTICRAFT_CODEX_SERVICE_TIER",
}


def controlled_environment(
    previous: dict[str, str],
    effort: str,
    *,
    model: str = MODEL,
    service_tier: str | None = None,
    align_runtime: bool = False,
) -> dict[str, str]:
    result = dict(previous)
    if align_runtime:
        result.update(CODEX_RUNTIME_PROFILE)
    result.update(
        {
            "ARTICRAFT_CODEX_MODEL": model,
            "ARTICRAFT_MODEL": model,
            "ARTICRAFT_REASONING_EFFORT": effort,
        }
    )
    if service_tier is not None:
        result["ARTICRAFT_CODEX_SERVICE_TIER"] = service_tier
    changed = {
        key for key in previous.keys() | result.keys() if previous.get(key) != result.get(key)
    }
    allowed_keys = CONFIG_KEYS | (CODEX_RUNTIME_PROFILE.keys() if align_runtime else set())
    if not changed <= allowed_keys:
        raise RuntimeError("Unrelated service environment changed")
    return result


def wait_for_exit(pid: int, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
        try:
            reaped, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            reaped = 0
        if reaped == pid:
            return
        time.sleep(0.1)
    if Path(f"/proc/{pid}").exists():
        raise RuntimeError("Old service did not exit; replacement not launched")


def restart(
    effort: str, *, model: str = MODEL, service_tier: str | None = None, align_runtime: bool = False
) -> dict:
    pid_file = ROOT / ".runtime/codex.pid"
    pid = int(pid_file.read_text())
    process = Path(f"/proc/{pid}")
    previous = dict(
        entry.decode().split("=", 1)
        for entry in (process / "environ").read_bytes().split(b"\0")
        if b"=" in entry
    )
    command = (process / "cmdline").read_bytes().split(b"\0")[:-1]
    expected = [str(ROOT / ".venv/bin/python").encode(), b"-m", b"articulation_server"]
    if (process / "cwd").resolve() != ROOT or command != expected:
        raise RuntimeError("Refusing to stop a process outside this service")
    if (
        previous.get("ARTICRAFT_PROVIDER") != "codex-cli"
        or previous.get("ARTICULATION_PORT") != "18689"
    ):
        raise RuntimeError("Service identity does not match the Codex backend")
    active = [
        p
        for p in (ROOT / "outputs/codex/jobs").glob("*/job.json")
        if json.loads(p.read_text()).get("status") not in {"succeeded", "failed", "cancelled"}
    ]
    if active:
        raise RuntimeError("Refusing to restart while existing jobs are active")
    environment = controlled_environment(
        previous, effort, model=model, service_tier=service_tier, align_runtime=align_runtime
    )
    changes = {
        key: {"before": previous.get(key), "after": environment.get(key)}
        for key in environment.keys() | previous.keys()
        if previous.get(key) != environment.get(key)
    }
    os.kill(pid, signal.SIGINT)
    wait_for_exit(pid)
    with (ROOT / ".runtime/codex.log").open("ab") as log:
        child = subprocess.Popen(
            [sys.executable, "-m", "articulation_server"],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pid_file.write_text(str(child.pid) + "\n")
    client = ArticulationClient("http://127.0.0.1:18689", timeout_seconds=10)
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError("Replacement service exited")
            try:
                health = client.health()
            except Exception:
                time.sleep(1)
                continue
            if health.get("ok") and health.get("model") == model:
                break
        else:
            raise RuntimeError("Replacement service health check timed out")
    finally:
        client.close()
    return {
        "old_pid": pid,
        "new_pid": child.pid,
        "model": model,
        "reasoning_effort": effort,
        "service_tier": environment.get("ARTICRAFT_CODEX_SERVICE_TIER", "default"),
        "changed_environment": changes,
        "health": health,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        print("Dry run: GPT-5.6-Sol medium and xhigh, four original cases each; no model calls")
        return 0
    output = (
        ROOT / "outputs/benchmarks" / datetime.now(UTC).strftime("sol56-compare-%Y%m%dT%H%M%SZ")
    )
    output.mkdir(parents=True, exist_ok=False)
    path = output / "comparison.json"
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "model": MODEL,
        "execution_order": ["medium", "xhigh"],
        "planned_submissions": 8,
        "only_service_changes": sorted(CONFIG_KEYS),
        "groups": [],
        "completed": False,
    }
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"COMPARISON_OUTPUT={output}", flush=True)
    for effort in report["execution_order"]:
        settings = restart(effort, service_tier="default")
        print(f"SERVICE_READY model={MODEL} effort={effort} pid={settings['new_pid']}", flush=True)
        group = {"effort": effort, "settings": settings, "output": str(output / effort)}
        report["groups"].append(group)
        path.write_text(json.dumps(report, indent=2) + "\n")
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/benchmark_codex.py"),
                "--run",
                "--continue-on-failure",
                "--output",
                str(output / effort),
            ],
            cwd=ROOT,
            check=False,
        )
        group["driver_returncode"] = completed.returncode
        benchmark = json.loads((output / effort / "benchmark.json").read_text())
        group["benchmark"] = benchmark
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        if not benchmark.get("completed") or len(benchmark.get("results", [])) != 4:
            raise RuntimeError("Incomplete group; stop without restarting or duplicating tasks")
    report.update({"completed": True, "finished_at": datetime.now(UTC).isoformat()})
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return (
        0 if all(r.get("pass") for g in report["groups"] for r in g["benchmark"]["results"]) else 1
    )


if __name__ == "__main__":
    sys.exit(main())
