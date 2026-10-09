"""Verify CLI profile serialization against a loopback fake API, without paid inference."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from articraft.agent.provider.codex_cli import _OUTPUT_SCHEMA, CodexCliModel
from articraft.settings import Settings

from local import environment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cloud-profile", action="store_true")
    args = parser.parse_args()
    profile = environment(
        "codex",
        inherited={},
        **({} if args.cloud_profile else {"model": "gpt-6-luna", "effort": "xhigh", "fast": True}),
    )
    config = Settings(_env_file=None, **profile)
    model = CodexCliModel(config, runner=lambda *_args: None)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(
                {
                    "path": self.path,
                    "model": body.get("model"),
                    "service_tier": body.get("service_tier"),
                    "reasoning": body.get("reasoning"),
                    "dummy_auth_only": self.headers.get("Authorization") == "Bearer offline-dummy",
                }
            )
            payload = json.dumps(
                {
                    "error": {
                        "type": "invalid_request_error",
                        "message": "Offline serialization probe captured; no inference executed",
                    }
                }
            ).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = os.environ.copy()
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
        env.pop(key, None)
    env["CODEX_FAST_PROBE_TOKEN"] = "offline-dummy"
    env["NO_PROXY"] = "127.0.0.1,localhost"
    try:
        with tempfile.TemporaryDirectory(prefix="codex-profile-probe-") as cwd:
            root = Path(cwd)
            schema = root / "assistant-turn.schema.json"
            schema.write_text(json.dumps(_OUTPUT_SCHEMA))
            command = model._command(schema, root / "assistant-turn.json", [], root, ephemeral=True)
            # Override only the endpoint/auth source; reuse production model, effort and tier flags.
            command.pop()  # stdin prompt marker
            settings = [
                'model_provider="offline_probe"',
                'model_providers.offline_probe.name="Offline probe"',
                f'model_providers.offline_probe.base_url="http://127.0.0.1:{server.server_port}/v1"',
                'model_providers.offline_probe.wire_api="responses"',
                'model_providers.offline_probe.env_key="CODEX_FAST_PROBE_TOKEN"',
                "model_providers.offline_probe.requires_openai_auth=false",
                "model_providers.offline_probe.supports_websockets=false",
            ]
            for value in settings:
                command.extend(["-c", value])
            command.append("-")
            child = subprocess.Popen(
                command,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                child.communicate("Offline probe. Reply only ready.\n", timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGTERM)
                child.communicate(timeout=5)
            report = {
                "offline_only": True,
                "real_model_calls": 0,
                "profile": "cloud" if args.cloud_profile else "luna-fast",
                "cli_returncode": child.returncode,
                "captured_requests": requests,
            }
            print(json.dumps(report, indent=2))
            assert requests, "CLI did not send a request to the loopback endpoint"
            assert all(r["dummy_auth_only"] for r in requests), "Unexpected authentication source"
            assert all(
                r["model"] == config.codex_model
                and r["service_tier"]
                in ((None, "default") if args.cloud_profile else ("priority",))
                and r["reasoning"]["effort"] == config.selected_reasoning_effort
                for r in requests
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
