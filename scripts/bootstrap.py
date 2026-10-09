"""Install this checkout into a fresh local venv using the configured Python 3.11."""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if sys.version_info[:2] != (3, 11):
        raise SystemExit("Use Python 3.11 from environment.yml before running bootstrap.")
    target = ROOT / ".venv"
    if target.exists():
        raise SystemExit("Refusing to replace an existing .venv; keep it or use a fresh checkout.")
    venv.EnvBuilder(with_pip=True).create(target)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "-r",
            str(ROOT / "requirements-cloud.lock"),
            "-e",
            str(ROOT / "vendor/Articraft"),
            "-e",
            str(ROOT / "vendor/articulation-server"),
            "pytest==8.4.2",
            "ruff==0.16.10",
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run([str(python), str(ROOT / "local.py"), "smoke"], cwd=ROOT, check=True)
    print("Installation and offline USDC smoke check passed. Configure .env before API generation.")


if __name__ == "__main__":
    main()
