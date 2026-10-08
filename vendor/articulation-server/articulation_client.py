#!/usr/bin/env python3
"""Dependency-free client for articulation-server.

Copy this one file to another machine or a Cline workspace. It only requires
Python 3.10+ and can submit text/images, poll jobs, cancel them, and download
USDZ or EmbodiChain USDC artifacts.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import OpenerDirector, ProxyHandler, Request, build_opener


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
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query or fragment")
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.opener: OpenerDirector = (
            build_opener() if trust_environment else build_opener(ProxyHandler({}))
        )

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
            body = json.dumps({"prompt": prompt}).encode("utf-8")
            return self._json(
                "POST",
                "/generate_articulation",
                body=body,
                headers={"Content-Type": "application/json"},
            )

        image_path = Path(image).expanduser().resolve()
        if not image_path.is_file():
            raise FileNotFoundError(f"reference image not found: {image_path}")
        body, content_type = _multipart_body(prompt, image_path)
        return self._json(
            "POST",
            "/generate_articulation",
            body=body,
            headers={"Content-Type": content_type},
        )

    def status(self, request_id: str) -> dict[str, Any]:
        return self._json("GET", f"/tasks/{_request_id(request_id)}")

    def cancel(self, request_id: str) -> dict[str, Any]:
        return self._json("POST", f"/tasks/{_request_id(request_id)}/cancel", body=b"")

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

        target = Path(destination).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.part")
        try:
            with self._open("GET", relative_url) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            temporary.replace(target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return target

    def _json(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        with self._open(method, path, body=body, headers=headers) as response:
            raw = response.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArticulationClientError("server response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ArticulationClientError("server response must be a JSON object")
        return payload

    def _open(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        request_headers = {"Accept": "application/json", **(headers or {})}
        if self.api_key:
            request_headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            self._url(path),
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            return self.opener.open(request, timeout=self.timeout_seconds)
        except HTTPError as exc:
            detail = _error_detail(exc.read())
            raise ArticulationClientError(
                f"server returned HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except URLError as exc:
            raise ArticulationClientError(
                f"could not reach articulation-server: {exc.reason}"
            ) from exc

    def _url(self, path: str) -> str:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc:
            raise ArticulationClientError("server artifact URL must be relative")
        return urljoin(self.base_url, path.lstrip("/"))


def _request_id(value: str) -> str:
    value = value.strip()
    if not value or any(character not in "0123456789abcdef-" for character in value.lower()):
        raise ValueError("request_id contains unsupported characters")
    return value


def _multipart_body(prompt: str, image_path: Path) -> tuple[bytes, str]:
    boundary = f"articulation-{uuid.uuid4().hex}"
    image_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    safe_name = image_path.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
    parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="prompt"\r\n\r\n',
        prompt.encode("utf-8"),
        b"\r\n",
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{safe_name}"\r\n'.encode(),
        f"Content-Type: {image_type}\r\n\r\n".encode(),
        image_path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _error_detail(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", errors="replace")[:500]
    return str(payload.get("error") or "") if isinstance(payload, dict) else ""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Single-file articulation-server client")
    parser.add_argument(
        "--base-url",
        default=os.getenv("ARTICULATION_SERVER_BASE_URL", "http://127.0.0.1:8688"),
    )
    parser.add_argument("--api-key", default=os.getenv("ARTICULATION_SERVER_API_KEY"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health")

    generate = commands.add_parser("generate")
    generate.add_argument("prompt")
    generate.add_argument("--image", type=Path)
    generate.add_argument("--no-wait", action="store_true")
    generate.add_argument("--wait-timeout", type=float, default=7_200.0)
    generate.add_argument("--poll-interval", type=float, default=1.0)
    generate.add_argument("--artifact", choices=("usdc", "usdz"), default="usdc")
    generate.add_argument("--output", type=Path)

    status = commands.add_parser("status")
    status.add_argument("request_id")
    cancel = commands.add_parser("cancel")
    cancel.add_argument("request_id")
    download = commands.add_parser("download")
    download.add_argument("request_id")
    download.add_argument("artifact", choices=("usdc", "usdz", "manifest", "record", "source"))
    download.add_argument("destination", type=Path)
    return parser


def main() -> int:
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
                payload = client.wait(
                    payload["request_id"],
                    timeout_seconds=args.wait_timeout,
                    poll_interval_seconds=args.poll_interval,
                )
                if payload.get("status") == "succeeded" and args.output:
                    downloaded = client.download(payload["request_id"], args.artifact, args.output)
                    payload["downloaded_to"] = str(downloaded)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload.get("ok", True) and payload.get("status") != "failed" else 2
    except (ArticulationClientError, OSError, TimeoutError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
