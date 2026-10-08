"""Local entrypoint; credentials stay in the user's external dotenv file."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
DEFAULT_ENV = ROOT.parent / "baseline" / "embodichain" / "gen_sim" / ".env"
PORTS = {"api": 18688, "codex": 18689}


def environment(
    backend: str,
    *,
    env_file: Path = DEFAULT_ENV,
    model: str | None = None,
    port: int | None = None,
    inherited: dict[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if inherited is None else inherited)
    env.update(
        {
            "ARTICULATION_HOST": "127.0.0.1",
            "ARTICULATION_PORT": str(PORTS[backend] if port is None else port),
            "ARTICULATION_OUTPUT_ROOT": str(ROOT / "outputs" / backend),
            "ARTICRAFT_OUTPUT_DIR": str(ROOT / "outputs" / backend / "runs"),
            "ARTICRAFT_SOURCE_DIR": str(ROOT / "vendor" / "Articraft"),
            "ARTICULATION_PYTHON": sys.executable,
            "ARTICULATION_CONCURRENCY": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    # The Conda interpreter provides newer C++ runtime libraries for CAD wheels.
    libraries = str(Path(sys.base_prefix) / "lib")
    env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, [libraries, env.get("LD_LIBRARY_PATH")]))
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", os.defpath)])
    if backend == "api":
        if not env_file.is_file():
            raise FileNotFoundError(f"API configuration file not found: {env_file}")
        values = dotenv_values(env_file, interpolate=False)
        for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
            if not (values.get(name) or "").strip():
                raise ValueError(f"{name} is missing from {env_file}")
        env.update(
            {
                "OPENAI_API_KEY": values["OPENAI_API_KEY"],
                "OPENAI_BASE_URL": values["OPENAI_BASE_URL"],
                "ARTICRAFT_MODEL": model or values["OPENAI_MODEL"],
                "ARTICRAFT_PROVIDER": "compatible-api",
                "ARTICRAFT_COMPATIBLE_PROXY_URL": values.get("EMBODICHAIN_LLM_PROXY") or "",
                "ARTICRAFT_OPENAI_MAX_ATTEMPTS": values.get("OPENAI_MAX_ATTEMPTS") or "4",
            }
        )
        env.setdefault("ARTICRAFT_OPENAI_REQUEST_TIMEOUT_SECONDS", "300")
    elif backend == "codex":
        env.pop("OPENAI_API_KEY", None)
        env.pop("CODEX_API_KEY", None)
        env.pop("ARTICRAFT_MODEL", None)
        env.update(
            {
                "ARTICRAFT_PROVIDER": "codex-cli",
                "ARTICRAFT_CODEX_MODEL": model or "codex-cli-default",
                "ARTICRAFT_MODEL": model or "codex-cli-default",
                "ARTICRAFT_REASONING_EFFORT": "xhigh",
                "ARTICRAFT_CODEX_CLI_BIN": shutil.which("codex") or "codex",
                "ARTICRAFT_CODEX_CLI_ROTATE_ROUTE_ON_TRANSPORT_RETRY": "false",
                "ARTICRAFT_CODEX_CLI_ROUTE_CONTROLLER_URL": "",
            }
        )
        env.pop("ARTICRAFT_CODEX_CLI_ROUTE_CONTROLLER_SECRET_FILE", None)
    else:
        raise ValueError(f"Unknown backend: {backend}")
    return env


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Articraft API or Codex backend")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "doctor", "generate"):
        command = sub.add_parser(name)
        command.add_argument("--backend", choices=PORTS, default="api")
        command.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
        command.add_argument("--model")
        if name == "serve":
            command.add_argument("--port", type=int)
        if name == "generate":
            command.add_argument("prompt")
            command.add_argument("--image", type=Path)
    client = sub.add_parser("client")
    client.add_argument("--backend", choices=PORTS, default="api")
    client.add_argument("args", nargs=argparse.REMAINDER)
    sub.add_parser("smoke")
    args = parser.parse_args()
    try:
        if args.command == "client":
            os.execv(
                sys.executable,
                [
                    sys.executable,
                    "-c",
                    "from articulation_server.client import main; main()",
                    "--base-url",
                    f"http://127.0.0.1:{PORTS[args.backend]}",
                    *args.args,
                ],
            )
        if args.command == "smoke":
            env = environment("codex")
            os.execve(
                sys.executable, [sys.executable, str(ROOT / "scripts" / "offline_smoke.py")], env
            )
        env = environment(
            args.backend, env_file=args.env_file, model=args.model, port=getattr(args, "port", None)
        )
        if args.command == "doctor":
            command = [
                sys.executable,
                "-m",
                "articulation_server.runtime_probe",
                env["ARTICRAFT_PROVIDER"],
            ]
            subprocess.run(command, env=env, check=True, timeout=60)
            if args.backend == "codex":
                subprocess.run(
                    [env["ARTICRAFT_CODEX_CLI_BIN"], "login", "status"],
                    env=env,
                    check=True,
                    timeout=15,
                )
            else:
                subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "from articraft.agent.provider import create_model; "
                        "from articraft.settings import get_settings; "
                        "create_model(get_settings()); "
                        "print('API configuration valid; no request sent')",
                    ],
                    env=env,
                    check=True,
                    timeout=60,
                )
            print(
                json.dumps(
                    {
                        "backend": args.backend,
                        "model": env.get("ARTICRAFT_MODEL") or env["ARTICRAFT_CODEX_MODEL"],
                        "output_root": env["ARTICULATION_OUTPUT_ROOT"],
                        "model_request_sent": False,
                    },
                    indent=2,
                )
            )
            return
        if args.command == "serve":
            if not 1 <= int(env["ARTICULATION_PORT"]) <= 65535:
                raise ValueError("Port must be between 1 and 65535")
            os.execve(sys.executable, [sys.executable, "-m", "articulation_server"], env)
        command = [sys.executable, "-m", "articraft.app", args.prompt]
        if args.image:
            command.extend(["--image", str(args.image.resolve())])
        os.execve(sys.executable, command, env)
    except (ValueError, FileNotFoundError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
