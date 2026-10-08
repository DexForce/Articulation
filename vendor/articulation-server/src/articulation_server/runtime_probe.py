from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import get_args

from articulation_server.compat import prepare_articraft


def probe(provider: str | None = None) -> str:
    prepare_articraft(provider)

    import articraft.api as api

    supported = get_args(api.Provider)
    if provider and supported and provider not in supported:
        raise RuntimeError(
            f"configured Articraft does not support provider {provider!r}; "
            f"supported providers: {', '.join(supported)}"
        )
    if provider == "codex-cli":
        executable = os.getenv("ARTICRAFT_CODEX_CLI_BIN", "codex")
        resolved = shutil.which(executable)
        if resolved is None:
            raise RuntimeError(
                "codex-cli provider requires the Codex executable; install Codex CLI "
                "or set ARTICRAFT_CODEX_CLI_BIN"
            )
        completed = subprocess.run(
            [resolved, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode:
            detail = (completed.stdout or "").strip()[-500:]
            raise RuntimeError(f"Codex CLI check failed: {detail or completed.returncode}")
        version = (completed.stdout or "").strip()
        return f"ready ({version})" if version else "ready (Codex CLI)"
    return "ready"


def main() -> None:
    provider = sys.argv[1] if len(sys.argv) > 1 else None
    print(probe(provider))


if __name__ == "__main__":
    main()
