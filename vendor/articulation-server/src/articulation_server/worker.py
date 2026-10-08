from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any


def _write_response(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(request_path: Path) -> int:
    response_path: Path | None = None
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValueError("worker request must be a JSON object")
        response_path = Path(str(request["response_path"])).resolve()

        from articulation_server.compat import prepare_articraft

        prepare_articraft(request.get("provider"))
        import articraft

        result = articraft.generate(
            str(request.get("prompt") or ""),
            provider=request.get("provider"),
            model=request.get("model"),
            image=request.get("image"),
            output_dir=str(request["output_dir"]),
        )
        payload: dict[str, Any] = {
            "status": result.status,
            "run_id": result.run_id,
            "run_dir": str(result.run_dir),
            "artifact": str(result.artifact) if result.artifact else "",
            "message": result.message,
            "error": result.error,
            "attempts": result.attempts,
            "cost": result.cost,
            "token_usage": result.token_usage,
            "compile_report": result.compile_report,
        }
        _write_response(response_path, payload)
        return 0 if result.succeeded else 1
    except BaseException as exc:
        if response_path is not None:
            _write_response(
                response_path,
                {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                },
            )
        else:
            traceback.print_exc()
        return 1


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m articulation_server.worker REQUEST_JSON")
    raise SystemExit(run(Path(sys.argv[1]).expanduser().resolve()))


if __name__ == "__main__":
    main()
