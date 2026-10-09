"""Real agent/worker/compiler flow with a loopback fake model, not paid inference."""

import asyncio
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from articraft.agent.provider.codex_cli import CodexCliExecResult, CodexCliModel
from articraft.settings import Settings
from articulation_server.api import create_app
from articulation_server.config import ServerConfig
from pxr import Usd, UsdPhysics

from local import ROOT, environment


@pytest.mark.parametrize("api_mode", ["chat", "responses"])
def test_real_worker_agent_and_compile_with_loopback_api(tmp_path, monkeypatch, api_mode):
    source = (ROOT / "examples" / "offline_articulation.py").read_text()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.headers["Authorization"] == "Bearer offline-dummy"
            assert self.path == (
                "/v1/responses" if api_mode == "responses" else "/v1/chat/completions"
            )
            assert request["model"] == "offline-fixture"
            if api_mode == "responses":
                assert request["reasoning"] == {"effort": "low"}
                assert request["store"] is False
                assert "previous_response_id" not in request
            requests.append(request)
            turn = len(requests)
            message = {"content": "", "reasoning_content": "offline-checkpoint"}
            if turn < 3:
                name = "write" if turn == 1 else "compile"
                arguments = {"path": "main.py", "content": source} if turn == 1 else {}
                message["tool_calls"] = [
                    {
                        "id": f"call_{turn}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ]
            else:
                if api_mode == "responses":
                    assert request["input"][-1]["type"] == "function_call_output"
                    last_output = request["input"][-1]["output"]
                else:
                    assert request["messages"][-1]["role"] == "tool"
                    last_output = request["messages"][-1]["content"]
                assert json.loads(last_output)["result"]["status"] == "success"
                message["content"] = "Offline fixture compiled successfully."
            if api_mode == "responses":
                output = (
                    [
                        {
                            "type": "function_call",
                            "call_id": f"call_{turn}",
                            "name": name,
                            "arguments": json.dumps(arguments),
                        }
                    ]
                    if turn < 3
                    else [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": message["content"]}],
                        }
                    ]
                )
                response = json.dumps(
                    {"id": f"response_{turn}", "status": "completed", "output": output}
                ).encode()
            else:
                response = json.dumps(
                    {
                        "choices": [
                            {
                                "finish_reason": "tool_calls" if turn < 3 else "stop",
                                "message": message,
                            }
                        ]
                    }
                ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env_file = tmp_path / ".env"
    env_file.write_text(
        f'OPENAI_API_KEY="offline-dummy"\nOPENAI_BASE_URL="http://127.0.0.1:{server.server_port}/v1"\nOPENAI_MODEL="offline-fixture"\n'
    )
    env = environment("api", env_file=env_file, api_mode=api_mode)
    env["ARTICULATION_OUTPUT_ROOT"] = str(tmp_path / "output")
    env["ARTICRAFT_OUTPUT_DIR"] = str(tmp_path / "output" / "runs")
    env["ARTICRAFT_OPENAI_MAX_ATTEMPTS"] = "1"
    env["ARTICRAFT_MAX_TURNS"] = "5"
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config = ServerConfig.from_env(env)
    assert str(config.python_executable) == sys.executable
    app = create_app(config)
    client = app.test_client()
    manager = app.extensions["articulation_job_manager"]
    try:
        submitted = client.post("/generate_articulation", json={"prompt": "offline test"})
        assert submitted.status_code == 202
        request_id = submitted.get_json()["request_id"]
        deadline = time.monotonic() + 60
        while True:
            task = client.get(f"/tasks/{request_id}").get_json()
            if task["status"] in {"succeeded", "failed", "cancelled"}:
                break
            assert time.monotonic() < deadline, task
            time.sleep(0.05)
        assert task["status"] == "succeeded", task
        response = client.get(f"/assets/{request_id}/usdc")
        assert response.status_code == 200
        assert response.data.startswith(b"PXR-USDC")
        downloaded = tmp_path / "download.usdc"
        downloaded.write_bytes(response.data)
        stage = Usd.Stage.Open(str(downloaded))
        assert stage is not None
        assert sum(p.HasAPI(UsdPhysics.ArticulationRootAPI) for p in stage.Traverse()) == 1
        assert len(requests) == 3
        if api_mode == "chat":
            assert requests[1]["messages"][-2]["reasoning_content"] == "offline-checkpoint"
        else:
            assert any(item.get("type") == "function_call" for item in requests[1]["input"])
    finally:
        manager.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_codex_command_and_protocol_without_inference(tmp_path):
    commands = []

    async def runner(command, _prompt, _timeout, _output):
        commands.append(command)
        return CodexCliExecResult(
            returncode=0,
            stdout='{"type":"turn.completed"}\n',
            stderr="",
            last_message=json.dumps({"text": "offline", "tool_calls": []}),
        )

    async def run():
        settings = Settings(
            _env_file=None,
            provider="codex-cli",
            output_dir=tmp_path,
            openai_reasoning_effort="medium",
        )
        model = CodexCliModel(settings, runner=runner)
        try:
            response = await model.query([{"role": "user", "content": "offline"}])
            assert response["text"] == "offline"
        finally:
            await model.close()

    asyncio.run(run())
    command = commands[0]
    assert command[:2] == ["codex", "exec"]
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in command
    assert 'model_reasoning_effort="medium"' in command
