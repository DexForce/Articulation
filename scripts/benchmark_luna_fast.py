"""Run one four-case benchmark of GPT-6-Luna xhigh with explicit Fast tier."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.compare_sol56 import restart

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        print("Dry run: gpt-6-luna / xhigh / Fast, four historical cases; no model calls")
        return 0
    output = (
        ROOT / "outputs/benchmarks" / datetime.now(UTC).strftime("luna6-xhigh-fast-%Y%m%dT%H%M%SZ")
    )
    output.mkdir(parents=True, exist_ok=False)
    print(f"LUNA_FAST_OUTPUT={output}", flush=True)
    settings = restart("xhigh", model="gpt-6-luna", service_tier="fast")
    (output / "runtime-settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    print("SERVICE_READY model=gpt-6-luna effort=xhigh service_tier=fast", flush=True)
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/benchmark_codex.py"),
            "--run",
            "--continue-on-failure",
            "--output",
            str(output / "cases"),
        ],
        cwd=ROOT,
        check=False,
    )
    benchmark = json.loads((output / "cases/benchmark.json").read_text())
    report = {
        "settings": settings,
        "driver_returncode": process.returncode,
        "benchmark": benchmark,
        "completed": bool(benchmark.get("completed")) and len(benchmark.get("results", [])) == 4,
    }
    (output / "result.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    if not report["completed"]:
        raise RuntimeError("Incomplete benchmark; do not resubmit ambiguous tasks")
    return 0 if all(row.get("pass") for row in benchmark["results"]) else 1


if __name__ == "__main__":
    sys.exit(main())
