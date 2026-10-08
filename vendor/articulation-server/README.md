# Articulation Server

`articulation-server` exposes the sibling Articraft checkout as a queued HTTP
service. A request may take many model and compile turns, so submission always
returns immediately and clients poll a task URL. Every generation runs in its
own cancellable process group and writes only beneath the configured output
root.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check the queue and Articraft runtime. |
| `POST` | `/generate_articulation` | Submit text and an optional image. |
| `GET` | `/tasks/<request_id>` | Read task status and artifact URLs. |
| `POST` | `/tasks/<request_id>/cancel` | Cancel a queued or running task. |
| `GET` | `/assets/<request_id>/<name>` | Download `usdz`, `usdc`, `manifest`, `record`, or `source`. |

The server always provides the native Articraft USDZ. It also exposes `usdc`
when the selected Articraft fork writes `files.embodichain_usdc` or
`files.usdc` in `result/model.json`. The local `../Articraft` version currently
produces USDZ only.

Submit text as JSON:

```bash
curl -sS -X POST http://127.0.0.1:8688/generate_articulation \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"a cabinet with two hinged doors"}'
```

Or submit a reference image as multipart data:

```bash
curl -sS -X POST http://127.0.0.1:8688/generate_articulation \
  -F 'prompt=reconstruct this articulated desk lamp' \
  -F 'image=@reference.png'
```

The response has a request id and relative status URL:

```json
{
  "ok": true,
  "request_id": "7e5d...",
  "status": "queued",
  "status_url": "/tasks/7e5d..."
}
```

A completed task contains server-relative download URLs:

```json
{
  "ok": true,
  "request_id": "7e5d...",
  "status": "succeeded",
  "result": {
    "run_id": "20260820-cabinet-...",
    "artifacts": {
      "usdz": "/assets/7e5d.../usdz",
      "manifest": "/assets/7e5d.../manifest"
    }
  }
}
```

## Local installation in the current container

The existing base, SAM3, SAM3D and Z-Image environments do not contain the full
Articraft dependency set. Create a dedicated Python 3.11 environment inside
this directory:

```bash
cd /workspace/source/articulation-server
./deploy/create_env.sh
cp .env.example .env
```

The setup script deliberately overrides machine-wide Conda channels because a
host may contain stale or private mirror entries. Override its public default
with `ARTICULATION_CONDA_CHANNEL` when the deployment requires an internal
mirror.

Set the provider key and a random API key in `.env`, then start the server:

```bash
./deploy/start_server.sh
```

The startup script prioritizes the configured Articraft source tree and the
Conda environment's native libraries. The current sibling Articraft checkout
references an `openrouter.py` file that it does not contain, so this server
keeps OpenAI, Gemini, and Anthropic usable and reports OpenRouter as unavailable
until that upstream module is present.

The server does not enforce application-level authentication and must stay on a
trusted private network. Restrict the published host address and firewall to
approved Cline devices. Legacy `ARTICULATION_API_KEY` and
`ARTICULATION_ALLOW_UNAUTHENTICATED` settings are accepted but ignored.

Configuration may also come from an `articulation` entry in
`SERVICE_CONFIG_PATH`; environment variables take precedence. See
`config/service_config.example.json` and `.env.example`.

## Docker deployment

The Dockerfile uses a named BuildKit context so the large sibling SAM model
directories are not sent to the builder:

```bash
cd /workspace/source/articulation-server
cp .env.example .env
# Replace the API/provider placeholders in .env first.
python3 deploy/preflight.py --env-file .env
docker compose --env-file .env build articulation-server
docker compose --env-file .env up -d --no-deps articulation-server
curl http://127.0.0.1:18688/health
```

The health payload includes `server_version`. A deployment of this revision
must report `"server_version":"0.2.0"`; a missing field identifies an older
image. Worker probes also prepend the configured Python prefix's `lib` directory
themselves, so Conda CAD/USD extensions do not depend on the shell entrypoint to
select a compatible `libstdc++`.

Equivalent direct build and run commands are:

```bash
docker build --build-context articraft=../Articraft \
  -t articulation-server .
docker run --rm --init \
  --env-file .env \
  -p 127.0.0.1:18688:8688 \
  -v articulation-server-data:/data/articulation \
  -v articulation-server-codex:/home/articulation/.codex \
  articulation-server
```

Binding `0.0.0.0` inside an already-running container does not publish a host
port retroactively. The supplied Compose file safely publishes only
`127.0.0.1:18688` until `ARTICULATION_PUBLISH_ADDRESS` is explicitly changed.
Recreate a container with an explicit `-p` mapping or route it through the
deployment ingress when remote access is intended.

For a remote host that already runs other services, follow the isolated,
blue-green procedure in
[`docs/host-deployment-low-risk.md`](docs/host-deployment-low-risk.md). It uses
a new Compose project, test port and named volumes, and includes read-only
preflight and non-generating verification scripts.

For the current Docker host `192.168.3.23`, the intended remote endpoint is
`http://192.168.3.23:18688`. See the concise
[`docs/host-port-mapping.md`](docs/host-port-mapping.md) handoff for the exact
host commands and client settings.

## Python and command-line client

```python
from articulation_server import ArticulationClient

client = ArticulationClient(
    "http://articulation-server:8688",
    api_key="server-api-key",
)
submitted = client.submit("a folding chair", image="chair.png")
task = client.wait(submitted["request_id"])
if task["status"] == "succeeded":
    client.download(task["request_id"], "usdz", "chair.usdz")
```

The installed CLI provides the same workflow:

```bash
export ARTICULATION_SERVER_BASE_URL=http://127.0.0.1:8688
articulation-client health
articulation-client generate --image chair.png --output chair.usdz "a folding chair"
```

For Cline, CI, or another repository that should not install this package, copy
the dependency-free `articulation_client.py` file by itself. It uses only the
Python standard library:

```bash
export ARTICULATION_SERVER_BASE_URL=http://articulation-server:8688
python articulation_client.py health
python articulation_client.py generate \
  --image cabinet.png \
  --artifact usdc \
  --output cabinet.usdc \
  "a cabinet with two hinged doors"
```

The single file also exposes `ArticulationClient` for direct import. Its default
CLI artifact is `usdc`, matching the target EmbodiChain workflow; use
`--artifact usdz` for the native Articraft package.

The client ignores inherited HTTP proxy variables by default so local Docker
service traffic cannot be sent to an external proxy. Set
`ARTICULATION_SERVER_TRUST_ENV=1` only when reaching the server itself requires
the process proxy configuration.

`pxc-articulation` can use this client instead of cloning Articraft and launching
`conda run` itself. In a second container its base URL should use the Compose
service name, for example `http://articulation-server:8688`.

## Switching to the EmbodiChain Articraft fork

The target fork currently keeps the same blocking `articraft.generate()` Python
API used by this server. With `provider="codex-cli"`, a successful generation
post-processes the selected USDZ and records this relative file in
`result/model.json`:

```json
{
  "files": {
    "embodichain_usdc": "usdc/<result-version>/model.usdc"
  }
}
```

The server already recognizes that field and publishes it as
`/assets/<request_id>/usdc`. The remaining deployment switch is therefore:

1. Clone `https://github.com/XuanchaoPENG/Articraft.git` into a new dedicated
   directory and pin a reviewed commit; do not overwrite the existing checkout.
2. Re-run `deploy/create_env.sh` with `ARTICRAFT_SOURCE_DIR` pointing at that
   checkout. For Compose, set `ARTICRAFT_BUILD_CONTEXT` to the same checkout.
3. Install Codex CLI in the runtime image/environment and authenticate it as the
   same non-root user that runs this service. Do not bake credentials into the
   image. Follow the official Codex CLI installation and authentication docs.
4. Set `ARTICRAFT_PROVIDER=codex-cli` and
   `ARTICRAFT_CODEX_CLI_BIN=codex`. Remove `ARTICRAFT_MODEL` to retain the Codex
   CLI default, or set an explicitly tested model.
5. Require `/health` to report ready, run one controlled generation, and verify
   both `usdz` and `usdc` appear in the completed task before moving traffic.

For programmatic container authentication, the official flow accepts an API key
on stdin with `codex login --with-api-key`. Keep the key in a runtime secret and
persist or mount only the non-root service user's Codex state directory. See the
[Codex CLI guide](https://developers.openai.com/codex/cli/) and
[authentication guide](https://developers.openai.com/codex/auth/).

## Persistence and security

Jobs are stored under `ARTICULATION_OUTPUT_ROOT/jobs`; Articraft runs are stored
under `ARTICULATION_OUTPUT_ROOT/runs`. Completed jobs survive restart. A job that
was queued or running when the service stopped is marked failed as interrupted,
rather than being silently executed again.

Articraft executes model-authored Python and shell commands. A process group
makes cancellation reliable, but it is not a security sandbox. For access
beyond a trusted development machine:

- keep this service in a dedicated non-root container;
- never mount a Docker socket, home directory, credential store, or unrelated
  source directory;
- mount only its output volume;
- keep ingress allowlists, rate limits and request quotas enabled;
- restrict outbound traffic to the configured model provider;
- retain the default single generation worker unless resource limits have been
  tested.

## Tests

The HTTP and queue tests use a fake Articraft runner and do not call a model:

```bash
python -m pip install -e '.[dev]'
pytest -q
ruff check .
```
