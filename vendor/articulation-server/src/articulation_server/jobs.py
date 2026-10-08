from __future__ import annotations

import json
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from articulation_server.config import ServerConfig
from articulation_server.runner import (
    RunCancelled,
    Runner,
    RunnerResult,
    RunTimedOut,
)

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


class QueueFullError(RuntimeError):
    pass


@dataclass(slots=True)
class Job:
    request_id: str
    prompt: str
    job_dir: Path
    image_path: Path | None = None
    status: str = "queued"
    stage: str = "waiting for a worker"
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())
    started_at: str | None = None
    finished_at: str | None = None
    error: str = ""
    run_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Path] = field(default_factory=dict)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class JobManager:
    """Own queued jobs and a bounded number of Articraft worker processes."""

    def __init__(self, config: ServerConfig, runner: Runner) -> None:
        self.config = config
        self.runner = runner
        self.jobs_root = config.output_root / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        (config.output_root / "runs").mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.RLock()
        self._stopping = False
        self._load_existing_jobs()
        self._threads = [
            threading.Thread(
                target=self._worker_loop,
                name=f"articulation-worker-{index + 1}",
                daemon=True,
            )
            for index in range(config.concurrency)
        ]
        for thread in self._threads:
            thread.start()

    @property
    def alive(self) -> bool:
        return not self._stopping and all(thread.is_alive() for thread in self._threads)

    def submit(
        self,
        prompt: str,
        *,
        image_bytes: bytes | None = None,
        image_suffix: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            active = sum(not job.terminal for job in self._jobs.values())
            if active >= self.config.max_queue_size:
                raise QueueFullError("the articulation task queue is full")
            if self._stopping:
                raise RuntimeError("the articulation task manager is stopping")

            request_id = uuid.uuid4().hex
            job_dir = self.jobs_root / request_id
            job_dir.mkdir(parents=False, exist_ok=False)
            image_path = None
            if image_bytes is not None and image_suffix is not None:
                image_path = job_dir / f"input{image_suffix}"
                image_path.write_bytes(image_bytes)
            job = Job(
                request_id=request_id,
                prompt=prompt,
                job_dir=job_dir,
                image_path=image_path,
            )
            self._jobs[request_id] = job
            self._persist(job)
            self._queue.put(request_id)
            return self._public_payload(job)

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(request_id)
            return self._public_payload(job) if job is not None else None

    def cancel(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(request_id)
            if job is None:
                return None
            if job.terminal:
                return self._public_payload(job)
            job.cancel_event.set()
            job.updated_at = _now()
            if job.status == "queued":
                job.status = "cancelled"
                job.stage = "cancelled before execution"
                job.finished_at = job.updated_at
            else:
                job.status = "cancelling"
                job.stage = "stopping Articraft worker"
            self._persist(job)
            return self._public_payload(job)

    def artifact(self, request_id: str, name: str) -> Path | None:
        with self._lock:
            job = self._jobs.get(request_id)
            if job is None or job.status != "succeeded":
                return None
            path = job.artifacts.get(name)
            if path is None or not path.is_file():
                return None
            try:
                path.resolve().relative_to(self.config.output_root.resolve())
            except ValueError:
                return None
            return path

    def health(self) -> tuple[bool, str]:
        if not self.alive:
            return False, "job manager is not running"
        return self.runner.probe()

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._lock:
            if self._stopping:
                return
            self._stopping = True
            for job in self._jobs.values():
                if not job.terminal:
                    job.cancel_event.set()
        for _ in self._threads:
            self._queue.put(None)
        for thread in self._threads:
            thread.join(timeout=timeout)

    def _worker_loop(self) -> None:
        while True:
            request_id = self._queue.get()
            try:
                if request_id is None:
                    return
                with self._lock:
                    job = self._jobs.get(request_id)
                    if job is None or job.terminal:
                        continue
                    job.status = "running"
                    job.stage = "starting generation"
                    job.started_at = _now()
                    job.updated_at = job.started_at
                    self._persist(job)
                self._execute(job)
            finally:
                self._queue.task_done()

    def _execute(self, job: Job) -> None:
        try:
            result = self.runner.run(
                request_id=job.request_id,
                prompt=job.prompt,
                image_path=job.image_path,
                job_dir=job.job_dir,
                cancel_event=job.cancel_event,
                on_progress=lambda stage: self._progress(job.request_id, stage),
            )
        except RunCancelled as exc:
            self._finish_error(job.request_id, "cancelled", str(exc))
        except RunTimedOut as exc:
            self._finish_error(job.request_id, "failed", str(exc))
        except Exception as exc:
            self._finish_error(job.request_id, "failed", f"{type(exc).__name__}: {exc}")
        else:
            self._finish_success(job.request_id, result)

    def _progress(self, request_id: str, stage: str) -> None:
        with self._lock:
            job = self._jobs.get(request_id)
            if job is None or job.terminal:
                return
            job.stage = stage[:500]
            job.updated_at = _now()
            self._persist(job)

    def _finish_error(self, request_id: str, status: str, error: str) -> None:
        with self._lock:
            job = self._jobs[request_id]
            job.status = status
            job.stage = "cancelled" if status == "cancelled" else "generation failed"
            job.error = error[:4_000]
            job.updated_at = _now()
            job.finished_at = job.updated_at
            self._persist(job)

    def _finish_success(self, request_id: str, result: RunnerResult) -> None:
        with self._lock:
            job = self._jobs[request_id]
            if job.cancel_event.is_set():
                job.status = "cancelled"
                job.stage = "cancelled"
                job.error = "generation cancelled"
            else:
                safe_artifacts = self._safe_artifacts(result.artifacts)
                if "usdz" not in safe_artifacts:
                    job.status = "failed"
                    job.stage = "generation failed"
                    job.error = "Articraft did not produce a safe downloadable USDZ artifact"
                else:
                    job.status = "succeeded"
                    job.stage = "complete"
                    job.run_id = result.run_id
                    job.details = result.details
                    job.artifacts = safe_artifacts
            job.updated_at = _now()
            job.finished_at = job.updated_at
            self._persist(job)

    def _safe_artifacts(self, artifacts: dict[str, Path]) -> dict[str, Path]:
        safe: dict[str, Path] = {}
        root = self.config.output_root.resolve()
        for name, path in artifacts.items():
            resolved = path.resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            if resolved.is_file():
                safe[name] = resolved
        return safe

    def _public_payload(self, job: Job) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": True,
            "request_id": job.request_id,
            "status": job.status,
            "stage": job.stage,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }
        if job.started_at:
            payload["started_at"] = job.started_at
        if job.finished_at:
            payload["finished_at"] = job.finished_at
        if job.error:
            payload["error"] = job.error
        if job.status == "succeeded":
            payload["result"] = {
                "run_id": job.run_id,
                **job.details,
                "artifacts": sorted(job.artifacts),
            }
        return payload

    def _persist(self, job: Job) -> None:
        payload = {
            "request_id": job.request_id,
            "prompt": job.prompt,
            "image_path": str(job.image_path) if job.image_path else None,
            "status": job.status,
            "stage": job.stage,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "error": job.error,
            "run_id": job.run_id,
            "details": job.details,
            "artifacts": {key: str(value) for key, value in job.artifacts.items()},
        }
        path = job.job_dir / "job.json"
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _load_existing_jobs(self) -> None:
        for path in sorted(self.jobs_root.glob("*/job.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                request_id = str(value["request_id"])
                if path.parent.name != request_id or not _valid_request_id(request_id):
                    continue
                status = str(value.get("status") or "failed")
                job = Job(
                    request_id=request_id,
                    prompt=str(value.get("prompt") or ""),
                    job_dir=path.parent.resolve(),
                    image_path=(
                        Path(str(value["image_path"])).resolve()
                        if value.get("image_path")
                        else None
                    ),
                    status=status,
                    stage=str(value.get("stage") or ""),
                    created_at=str(value.get("created_at") or _now()),
                    updated_at=str(value.get("updated_at") or _now()),
                    started_at=value.get("started_at"),
                    finished_at=value.get("finished_at"),
                    error=str(value.get("error") or ""),
                    run_id=str(value.get("run_id") or ""),
                    details=(
                        dict(value["details"]) if isinstance(value.get("details"), dict) else {}
                    ),
                    artifacts={
                        str(key): Path(str(item)).resolve()
                        for key, item in value.get("artifacts", {}).items()
                    }
                    if isinstance(value.get("artifacts"), dict)
                    else {},
                )
                job.artifacts = self._safe_artifacts(job.artifacts)
                if not job.terminal:
                    job.status = "failed"
                    job.stage = "interrupted by service restart"
                    job.error = "generation was interrupted by a service restart"
                    job.finished_at = _now()
                    job.updated_at = job.finished_at
                    self._persist(job)
                self._jobs[request_id] = job
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                continue


def _valid_request_id(value: str) -> bool:
    return len(value) == 32 and all(character in "0123456789abcdef" for character in value)


def _now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = ["JobManager", "QueueFullError", "TERMINAL_STATUSES"]
