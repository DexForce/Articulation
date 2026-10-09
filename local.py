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


def select_env_file(root: Path) -> Path:
    project = root / ".env"
    legacy = root.parent / "baseline" / "embodichain" / "gen_sim" / ".env"
    return project if project.is_file() or not legacy.is_file() else legacy


DEFAULT_ENV = select_env_file(ROOT)
PORTS = {"api": 18688, "codex": 18689}
# Verified GPU08 process profile; local credentials, CLI and proxy stay independent.
CODEX_RUNTIME_PROFILE = {
    "ARTICRAFT_CODEX_MODEL": "gpt-5.6-sol",
    "ARTICRAFT_MODEL": "gpt-5.6-sol",
    "ARTICRAFT_REASONING_EFFORT": "low",
    "ARTICRAFT_CODEX_SERVICE_TIER": "default",
    "ARTICRAFT_CODEX_CLI_TRANSPORT_MODE": "https",
    "ARTICRAFT_CODEX_CLI_FORCE_HTTPS": "true",
    "ARTICRAFT_CODEX_CLI_RESPONSE_SHADOW_SECONDS": "120",
    "ARTICRAFT_CODEX_CLI_RESPONSE_STALL_SECONDS": "180",
    "ARTICRAFT_CODEX_CLI_RECONNECT_STALL_SECONDS": "25",
    "ARTICRAFT_CODEX_CLI_MAX_ATTEMPTS": "3",
    "ARTICRAFT_CODEX_CLI_TIMEOUT_SECONDS": "1800",
    "ARTICRAFT_CODEX_CLI_RECOVERY_BUDGET_SECONDS": "600",
    "ARTICRAFT_CODEX_CLI_RETRY_SESSION_MODE": "resume",
    "ARTICRAFT_CODEX_CLI_RETRY_INVALID_RESPONSE": "true",
    "ARTICRAFT_MAX_TURNS": "100",
    "ARTICRAFT_COMPILE_TIMEOUT_SECONDS": "900",
    "ARTICULATION_JOB_TIMEOUT_SECONDS": "7200",
    "ARTICULATION_CONCURRENCY": "1",
}


def runtime_environment(inherited: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if inherited is None else inherited)
    libraries = str(Path(sys.base_prefix) / "lib")
    env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, [libraries, env.get("LD_LIBRARY_PATH")]))
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", os.defpath)])
    return env


def environment(
    backend: str,
    *,
    env_file: Path = DEFAULT_ENV,
    model: str | None = None,
    effort: str | None = None,
    api_mode: str | None = None,
    fast: bool = False,
    port: int | None = None,
    inherited: dict[str, str] | None = None,
) -> dict[str, str]:
    if fast and backend != "codex":
        raise ValueError("Fast requires the Codex backend")
    env = runtime_environment(inherited)
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
    if backend == "api":
        if not env_file.is_file():
            raise FileNotFoundError(f"API configuration file not found: {env_file}")
        values = dotenv_values(env_file, interpolate=False)
        api_mode = api_mode or values.get("OPENAI_API_MODE") or "chat"
        effort = effort or values.get("OPENAI_REASONING_EFFORT") or None
        if api_mode not in {"chat", "responses"}:
            raise ValueError("OPENAI_API_MODE must be chat or responses")
        if effort is not None and api_mode != "responses":
            raise ValueError("Explicit API reasoning effort requires Responses mode")
        for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
            if not (values.get(name) or "").strip():
                raise ValueError(f"{name} is missing from {env_file}")
        env.update(
            {
                "OPENAI_API_KEY": values["OPENAI_API_KEY"],
                "OPENAI_BASE_URL": values["OPENAI_BASE_URL"],
                "ARTICRAFT_MODEL": model or values["OPENAI_MODEL"],
                "ARTICRAFT_PROVIDER": "compatible-api",
                "ARTICRAFT_COMPATIBLE_API_MODE": api_mode,
                "ARTICRAFT_COMPATIBLE_PROXY_URL": values.get("EMBODICHAIN_LLM_PROXY") or "",
                "ARTICRAFT_OPENAI_MAX_ATTEMPTS": values.get("OPENAI_MAX_ATTEMPTS") or "4",
            }
        )
        if api_mode == "responses":
            env["ARTICRAFT_REASONING_EFFORT"] = effort or "low"
        env.setdefault("ARTICRAFT_OPENAI_REQUEST_TIMEOUT_SECONDS", "300")
    elif backend == "codex":
        env.pop("OPENAI_API_KEY", None)
        env.pop("CODEX_API_KEY", None)
        env.pop("ARTICRAFT_MODEL", None)
        env.update(CODEX_RUNTIME_PROFILE)
        env.update(
            {
                "ARTICRAFT_PROVIDER": "codex-cli",
                "ARTICRAFT_CODEX_MODEL": model or "gpt-5.6-sol",
                "ARTICRAFT_MODEL": model or "gpt-5.6-sol",
                "ARTICRAFT_REASONING_EFFORT": effort or "low",
                "ARTICRAFT_CODEX_SERVICE_TIER": "fast" if fast else "default",
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
        command.add_argument("--effort", choices=("low", "medium", "high", "xhigh"))
        command.add_argument("--api-mode", choices=("chat", "responses"))
        command.add_argument("--fast", action="store_true")
        if name == "serve":
            command.add_argument("--port", type=int)
        if name == "generate":
            command.add_argument("prompt")
            command.add_argument("--image", type=Path)
    client = sub.add_parser("client")
    client.add_argument("--backend", choices=PORTS, default="api")
    client.add_argument("args", nargs=argparse.REMAINDER)
    sub.add_parser("smoke")
    benchmark = sub.add_parser("benchmark")
    benchmark.add_argument("--backend", choices=PORTS, default="api")
    benchmark.add_argument("--port", type=int)
    benchmark.add_argument("--manifest", type=Path)
    benchmark.add_argument("--output", type=Path)
    benchmark.add_argument("--cases", nargs="+")
    benchmark.add_argument("--run", action="store_true")
    benchmark.add_argument("--continue-on-failure", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "benchmark":
            command = [
                sys.executable,
                str(ROOT / "scripts/benchmark_codex.py"),
                "--backend",
                args.backend,
            ]
            for key in ("port", "manifest", "output"):
                if getattr(args, key) is not None:
                    command.extend(["--" + key, str(getattr(args, key))])
            if args.cases:
                command.extend(["--cases", *args.cases])
            for key in ("run", "continue_on_failure"):
                if getattr(args, key):
                    command.append("--" + key.replace("_", "-"))
            os.execve(sys.executable, command, runtime_environment())
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
            env = runtime_environment()
            os.execve(
                sys.executable, [sys.executable, str(ROOT / "scripts" / "offline_smoke.py")], env
            )
        env = environment(
            args.backend,
            env_file=args.env_file,
            model=args.model,
            effort=args.effort,
            api_mode=args.api_mode,
            fast=args.fast,
            port=getattr(args, "port", None),
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
                        "reasoning_effort": env.get("ARTICRAFT_REASONING_EFFORT"),
                        "api_mode": env.get("ARTICRAFT_COMPATIBLE_API_MODE"),
                        "service_tier": env.get("ARTICRAFT_CODEX_SERVICE_TIER"),
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
