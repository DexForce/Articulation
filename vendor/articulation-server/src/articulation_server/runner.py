from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from typing import Any, Protocol

from articulation_server.config import ServerConfig


class RunCancelled(RuntimeError):
    pass


class RunTimedOut(RuntimeError):
    pass


@dataclass(slots=True)
class RunnerResult:
    run_id: str
    run_dir: Path
    artifact: Path
    details: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Path] = field(default_factory=dict)


class Runner(Protocol):
    def probe(self) -> tuple[bool, str]: ...

    def run(
        self,
        *,
        request_id: str,
        prompt: str,
        image_path: Path | None,
        job_dir: Path,
        cancel_event: Event,
        on_progress: Callable[[str], None],
    ) -> RunnerResult: ...


class SubprocessRunner:
    """Run each Articraft request in a cancellable process group."""

    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self._probe_lock = Lock()
        self._probe_cache: tuple[float, bool, str] | None = None

    def probe(self) -> tuple[bool, str]:
        with self._probe_lock:
            now = time.monotonic()
            if self._probe_cache is not None and now - self._probe_cache[0] < 30:
                return self._probe_cache[1], self._probe_cache[2]

            if not self.config.python_executable.is_file():
                result = (False, f"Python executable not found: {self.config.python_executable}")
            elif not (self.config.articraft_source_dir / "src" / "articraft").is_dir():
                result = (
                    False,
                    f"Articraft source not found: {self.config.articraft_source_dir}",
                )
            else:
                try:
                    probe_command = [
                        str(self.config.python_executable),
                        "-m",
                        "articulation_server.runtime_probe",
                    ]
                    if self.config.provider:
                        probe_command.append(self.config.provider)
                    completed = subprocess.run(
                        probe_command,
                        cwd=self.config.articraft_source_dir,
                        env=self._environment(),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        timeout=self.config.probe_timeout_seconds,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    result = (False, f"Articraft runtime probe failed: {exc}")
                else:
                    output = (completed.stdout or "").strip()
                    result = (
                        (True, output[-500:] or "ready")
                        if completed.returncode == 0
                        else (False, f"Articraft runtime is unavailable: {output[-1000:]}")
                    )
            self._probe_cache = (now, result[0], result[1])
            return result

    def run(
        self,
        *,
        request_id: str,
        prompt: str,
        image_path: Path | None,
        job_dir: Path,
        cancel_event: Event,
        on_progress: Callable[[str], None],
    ) -> RunnerResult:
        request_path = job_dir / "worker-request.json"
        response_path = job_dir / "worker-response.json"
        log_path = job_dir / "worker.log"
        request_payload = {
            "request_id": request_id,
            "prompt": prompt,
            "image": str(image_path) if image_path else None,
            "output_dir": str(self.config.output_root / "runs"),
            "provider": self.config.provider,
            "model": self.config.model,
            "response_path": str(response_path),
        }
        _write_json(request_path, request_payload)
        on_progress("starting Articraft worker")

        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [
                    str(self.config.python_executable),
                    "-m",
                    "articulation_server.worker",
                    str(request_path),
                ],
                cwd=self.config.articraft_source_dir,
                env=self._environment(),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            started = time.monotonic()
            while process.poll() is None:
                if cancel_event.wait(timeout=0.25):
                    _terminate_process_tree(process)
                    raise RunCancelled("generation cancelled")
                if time.monotonic() - started > self.config.job_timeout_seconds:
                    _terminate_process_tree(process)
                    raise RunTimedOut(
                        f"generation exceeded {self.config.job_timeout_seconds:g} seconds"
                    )

        if not response_path.is_file():
            suffix = _tail(log_path)
            raise RuntimeError(
                f"Articraft worker exited with code {process.returncode} without a response"
                + (f": {suffix}" if suffix else "")
            )
        try:
            response = json.loads(response_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Articraft worker returned invalid JSON: {exc}") from exc
        if not isinstance(response, dict):
            raise RuntimeError("Articraft worker response must be an object")
        if response.get("status") != "success":
            error = str(response.get("error") or "Articraft generation failed")
            raise RuntimeError(error)

        run_dir = Path(str(response.get("run_dir") or "")).resolve()
        artifact = Path(str(response.get("artifact") or "")).resolve()
        run_id = str(response.get("run_id") or "")
        if not run_id or run_dir.name != run_id:
            raise RuntimeError("Articraft worker returned an invalid run id")
        _require_inside(run_dir, self.config.output_root / "runs", "run directory")
        _require_inside(artifact, run_dir, "artifact")
        if not artifact.is_file() or artifact.suffix.lower() != ".usdz":
            raise RuntimeError(f"Articraft USDZ artifact is unavailable: {artifact}")

        artifacts = {"usdz": artifact}
        candidates = {
            "manifest": run_dir / "result" / "model.json",
            "record": run_dir / "record.json",
            "source": run_dir / "workspace" / "main.py",
        }
        for name, path in candidates.items():
            if path.is_file():
                artifacts[name] = path.resolve()
        manifest = candidates["manifest"]
        usdc = _manifest_usdc(manifest, run_dir)
        if usdc is not None:
            artifacts["usdc"] = usdc

        details = {
            key: response[key]
            for key in (
                "message",
                "attempts",
                "cost",
                "token_usage",
                "compile_report",
            )
            if key in response
        }
        on_progress("Articraft generation completed")
        return RunnerResult(
            run_id=run_id,
            run_dir=run_dir,
            artifact=artifact,
            details=details,
            artifacts=artifacts,
        )

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        server_src = Path(__file__).resolve().parents[1]
        paths = [str(server_src), str(self.config.articraft_source_dir / "src")]
        inherited = env.get("PYTHONPATH")
        if inherited:
            paths.append(inherited)
        env["PYTHONPATH"] = os.pathsep.join(paths)
        env["PYTHONUNBUFFERED"] = "1"

        # The HTTP process may be started without activating its Conda/prefix
        # environment. Always prefer the C/C++ runtime next to the configured
        # worker interpreter so CAD/USD imports cannot bind to an older system
        # libstdc++ merely because a different entrypoint launched the server.
        library_dir = self.config.python_executable.parent.parent / "lib"
        if library_dir.is_dir():
            library = str(library_dir)
            inherited_libraries = [
                path
                for path in env.get("LD_LIBRARY_PATH", "").split(os.pathsep)
                if path and path != library
            ]
            env["LD_LIBRARY_PATH"] = os.pathsep.join([library, *inherited_libraries])
        return env


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Stop the worker and descendants that created their own process groups."""
    if process.poll() is not None:
        return
    descendants = _descendant_process_ids(process.pid)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    for process_id in descendants:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(process_id, signal.SIGTERM)

    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)
    remaining = {process_id for process_id in descendants if _process_is_running(process_id)}
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    for process_id in remaining:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(process_id, signal.SIGKILL)
    if process.poll() is None:
        process.wait()


def _descendant_process_ids(parent_pid: int) -> set[int]:
    children: dict[int, set[int]] = {}
    for status_path in Path("/proc").glob("[0-9]*/status"):
        try:
            process_id = int(status_path.parent.name)
            parent_line = next(
                line for line in status_path.read_text().splitlines() if line.startswith("PPid:")
            )
            process_parent = int(parent_line.partition(":")[2].strip())
        except (OSError, StopIteration, ValueError):
            continue
        children.setdefault(process_parent, set()).add(process_id)

    descendants: set[int] = set()
    pending = list(children.get(parent_pid, set()))
    while pending:
        process_id = pending.pop()
        if process_id in descendants:
            continue
        descendants.add(process_id)
        pending.extend(children.get(process_id, set()))
    return descendants


def _process_is_running(process_id: int) -> bool:
    try:
        fields = (Path("/proc") / str(process_id) / "stat").read_text().split()
    except OSError:
        return False
    return len(fields) > 2 and fields[2] != "Z"


def _tail(path: Path, limit: int = 2_000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode("utf-8", errors="replace").strip()


def _require_inside(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"Articraft {label} escaped the configured output root") from exc


def _manifest_usdc(manifest_path: Path, run_dir: Path) -> Path | None:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest.get("files", {})
        value = files.get("embodichain_usdc") or files.get("usdc")
        if not isinstance(value, str) or not value.strip():
            return None
        path = (manifest_path.parent / value).resolve()
        _require_inside(path, run_dir, "USDC artifact")
        return path if path.is_file() and path.suffix.lower() == ".usdc" else None
    except (OSError, json.JSONDecodeError, AttributeError, RuntimeError):
        return None


__all__ = [
    "RunCancelled",
    "Runner",
    "RunnerResult",
    "RunTimedOut",
    "SubprocessRunner",
]
