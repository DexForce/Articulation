from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests


class ArticulationClientError(RuntimeError):
    pass


class ArticulationClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        trust_environment: bool = False,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.session.trust_env = trust_environment
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"

    @classmethod
    def from_env(cls) -> ArticulationClient:
        return cls(
            os.getenv("ARTICULATION_SERVER_BASE_URL", "http://127.0.0.1:8688"),
            api_key=os.getenv("ARTICULATION_SERVER_API_KEY") or None,
            timeout_seconds=float(os.getenv("ARTICULATION_SERVER_TIMEOUT_S", "30")),
            trust_environment=os.getenv("ARTICULATION_SERVER_TRUST_ENV", "0") == "1",
        )

    def health(self) -> dict[str, Any]:
        return self._json("GET", "/health")

    def submit(self, prompt: str, *, image: str | Path | None = None) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt must not be empty")
        if image is None:
            return self._json("POST", "/generate_articulation", json={"prompt": prompt})

        image_path = Path(image).expanduser().resolve()
        if not image_path.is_file():
            raise FileNotFoundError(f"reference image not found: {image_path}")
        with image_path.open("rb") as file:
            return self._json(
                "POST",
                "/generate_articulation",
                data={"prompt": prompt},
                files={"image": (image_path.name, file)},
            )

    def status(self, request_id: str) -> dict[str, Any]:
        return self._json("GET", f"/tasks/{request_id}")

    def cancel(self, request_id: str) -> dict[str, Any]:
        return self._json("POST", f"/tasks/{request_id}/cancel")

    def wait(
        self,
        request_id: str,
        *,
        timeout_seconds: float = 7_200.0,
        poll_interval_seconds: float = 1.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            task = self.status(request_id)
            if task.get("status") in {"succeeded", "failed", "cancelled"}:
                return task
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for articulation task {request_id}")
            time.sleep(poll_interval_seconds)

    def download(
        self,
        request_id: str,
        artifact: str,
        destination: str | Path,
    ) -> Path:
        task = self.status(request_id)
        result = task.get("result")
        artifacts = result.get("artifacts") if isinstance(result, dict) else None
        relative_url = artifacts.get(artifact) if isinstance(artifacts, dict) else None
        if not isinstance(relative_url, str):
            raise ArticulationClientError(f"task {request_id} has no artifact named {artifact!r}")
        response = self.session.get(
            self._url(relative_url),
            timeout=self.timeout_seconds,
        )
        self._raise_for_response(response)
        target = Path(destination).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.part")
        temporary.write_bytes(response.content)
        temporary.replace(target)
        return target

    def close(self) -> None:
        self.session.close()

    def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self.session.request(
            method,
            self._url(path),
            timeout=self.timeout_seconds,
            **kwargs,
        )
        self._raise_for_response(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ArticulationClientError("server response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ArticulationClientError("server response must be a JSON object")
        return payload

    def _url(self, path: str) -> str:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc:
            raise ArticulationClientError("server path must be relative to the configured base URL")
        return urljoin(self.base_url, path.lstrip("/"))

    @staticmethod
    def _raise_for_response(response: requests.Response) -> None:
        if response.ok:
            return
        try:
            payload = response.json()
            detail = payload.get("error") if isinstance(payload, dict) else None
        except ValueError:
            detail = None
        message = detail or response.text[:500] or response.reason
        raise ArticulationClientError(f"server returned HTTP {response.status_code}: {message}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Call an articulation-server instance")
    parser.add_argument(
        "--base-url",
        default=os.getenv("ARTICULATION_SERVER_BASE_URL", "http://127.0.0.1:8688"),
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("ARTICULATION_SERVER_API_KEY"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health")

    generate = subparsers.add_parser("generate")
    generate.add_argument("prompt")
    generate.add_argument("--image", type=Path)
    generate.add_argument("--no-wait", action="store_true")
    generate.add_argument("--wait-timeout", type=float, default=7_200.0)
    generate.add_argument("--output", type=Path)
    generate.add_argument("--artifact", default="usdz")

    status = subparsers.add_parser("status")
    status.add_argument("request_id")
    cancel = subparsers.add_parser("cancel")
    cancel.add_argument("request_id")
    download = subparsers.add_parser("download")
    download.add_argument("request_id")
    download.add_argument("artifact")
    download.add_argument("destination", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    client = ArticulationClient(args.base_url, api_key=args.api_key)
    try:
        if args.command == "health":
            payload = client.health()
        elif args.command == "status":
            payload = client.status(args.request_id)
        elif args.command == "cancel":
            payload = client.cancel(args.request_id)
        elif args.command == "download":
            path = client.download(args.request_id, args.artifact, args.destination)
            payload = {"ok": True, "path": str(path)}
        else:
            payload = client.submit(args.prompt, image=args.image)
            if not args.no_wait:
                payload = client.wait(payload["request_id"], timeout_seconds=args.wait_timeout)
                if args.output and payload.get("status") == "succeeded":
                    path = client.download(payload["request_id"], args.artifact, args.output)
                    payload["downloaded_to"] = str(path)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    finally:
        client.close()


__all__ = ["ArticulationClient", "ArticulationClientError", "main"]
