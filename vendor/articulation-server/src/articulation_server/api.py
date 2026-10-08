from __future__ import annotations

import atexit
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import BadRequest, HTTPException, RequestEntityTooLarge
from werkzeug.utils import secure_filename

from articulation_server import __version__
from articulation_server.config import ServerConfig
from articulation_server.jobs import JobManager, QueueFullError
from articulation_server.runner import Runner, SubprocessRunner

_ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_MIME_TYPES = {
    "usdz": "model/vnd.usdz+zip",
    "usdc": "application/octet-stream",
    "manifest": "application/json",
    "record": "application/json",
    "source": "text/x-python",
}


def create_app(
    config: ServerConfig | None = None,
    *,
    runner: Runner | None = None,
    manager: JobManager | None = None,
) -> Flask:
    cfg = config or ServerConfig.from_env()
    job_manager = manager or JobManager(cfg, runner or SubprocessRunner(cfg))
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = cfg.max_upload_bytes
    app.extensions["articulation_job_manager"] = job_manager
    atexit.register(job_manager.shutdown)

    @app.get("/health")
    def health() -> Any:
        ok, detail = job_manager.health()
        payload = {
            "ok": ok,
            "status": "ready" if ok else "error",
            "detail": detail,
            "provider": cfg.provider or "articraft default",
            "model": cfg.model or "articraft default",
            "concurrency": cfg.concurrency,
            "server_version": __version__,
        }
        return jsonify(payload), 200 if ok else 503

    @app.post("/generate_articulation")
    def generate_articulation() -> Any:
        prompt, image_bytes, image_suffix = _read_generation_request(cfg)
        payload = job_manager.submit(
            prompt,
            image_bytes=image_bytes,
            image_suffix=image_suffix,
        )
        payload["status_url"] = f"/tasks/{payload['request_id']}"
        payload["cancel_url"] = f"/tasks/{payload['request_id']}/cancel"
        return jsonify(payload), 202

    @app.get("/tasks/<request_id>")
    def task_status(request_id: str) -> Any:
        payload = job_manager.get(request_id)
        if payload is None:
            return jsonify({"ok": False, "error": "task not found"}), 404
        return jsonify(_with_artifact_urls(payload))

    @app.post("/tasks/<request_id>/cancel")
    def cancel_task(request_id: str) -> Any:
        payload = job_manager.cancel(request_id)
        if payload is None:
            return jsonify({"ok": False, "error": "task not found"}), 404
        return jsonify(_with_artifact_urls(payload))

    @app.get("/assets/<request_id>/<name>")
    def get_asset(request_id: str, name: str) -> Any:
        if name not in _MIME_TYPES:
            return jsonify({"ok": False, "error": "unknown artifact"}), 404
        path = job_manager.artifact(request_id, name)
        if path is None:
            return jsonify({"ok": False, "error": "artifact not found"}), 404
        return send_file(
            path,
            mimetype=_MIME_TYPES[name],
            as_attachment=True,
            download_name=_download_name(name, path),
            conditional=True,
        )

    @app.errorhandler(BadRequest)
    def bad_request(exc: BadRequest) -> Any:
        return jsonify({"ok": False, "error": exc.description}), 400

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_: RequestEntityTooLarge) -> Any:
        return jsonify({"ok": False, "error": "request body is too large"}), 413

    @app.errorhandler(QueueFullError)
    def queue_full(exc: QueueFullError) -> Any:
        return jsonify({"ok": False, "error": str(exc)}), 429

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException) -> Any:
        return jsonify({"ok": False, "error": exc.description}), exc.code or 500

    @app.errorhandler(Exception)
    def internal_error(exc: Exception) -> Any:
        app.logger.exception("articulation request failed")
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500

    return app


def _read_generation_request(
    config: ServerConfig,
) -> tuple[str, bytes | None, str | None]:
    if request.is_json:
        document = request.get_json(silent=False)
        if not isinstance(document, dict):
            raise BadRequest("JSON request body must be an object")
        prompt = str(document.get("prompt") or "").strip()
        image_file = None
    else:
        prompt = request.form.get("prompt", "").strip()
        image_file = request.files.get("image")

    if not prompt:
        raise BadRequest("missing non-empty prompt")
    if len(prompt) > config.max_prompt_chars:
        raise BadRequest(f"prompt exceeds {config.max_prompt_chars} characters")
    if image_file is None:
        return prompt, None, None

    filename = secure_filename(image_file.filename or "")
    if not filename:
        raise BadRequest("uploaded image has an empty filename")
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_IMAGE_SUFFIXES:
        supported = ", ".join(sorted(_ALLOWED_IMAGE_SUFFIXES))
        raise BadRequest(f"uploaded image only supports: {supported}")
    data = image_file.read()
    if not data:
        raise BadRequest("uploaded image is empty")
    return prompt, data, suffix


def _with_artifact_urls(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    names = result.get("artifacts")
    if not isinstance(names, list):
        return payload
    request_id = payload["request_id"]
    result["artifacts"] = {str(name): f"/assets/{request_id}/{name}" for name in names}
    return payload


def _download_name(name: str, path: Path) -> str:
    return {
        "usdz": "model.usdz",
        "usdc": "model.usdc",
        "manifest": "model.json",
        "record": "record.json",
        "source": "main.py",
    }.get(name, path.name)


def main() -> None:
    from waitress import serve

    config = ServerConfig.from_env()
    app = create_app(config)
    serve(app, host=config.host, port=config.port, threads=config.http_threads)


if __name__ == "__main__":
    main()
