"""Compile a fixed CAD fixture and exercise the HTTP queue without model calls."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path

from articraft.agent.workspace import LocalWorkspace
from articraft.sdk.embodichain import export_embodichain_result
from articulation_server.api import create_app
from articulation_server.config import ServerConfig
from articulation_server.runner import RunnerResult
from pxr import Usd, UsdPhysics, UsdUtils

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    output = ROOT / "outputs" / "offline"
    workspace = LocalWorkspace(output_dir=output / "runs", timeout_seconds=120)
    run_id = "smoke-" + uuid.uuid4().hex
    run = workspace.create_run(run_id)
    shutil.copyfile(ROOT / "examples" / "offline_articulation.py", run / "workspace" / "main.py")
    result = workspace.compile_path(run)
    (run / "compile.json").write_text(json.dumps(result, indent=2) + "\n")
    if result["status"] != "success":
        raise RuntimeError(json.dumps(result, indent=2))
    usdz = Path(result["usdz"])
    if not usdz.is_absolute():
        usdz = run / usdz
    usdc = export_embodichain_result(run, usdz)
    if usdc is None:
        raise RuntimeError("Offline fixture did not export an articulated USDC")
    if not usdc.read_bytes().startswith(b"PXR-USDC"):
        raise RuntimeError("USDC binary header is invalid")
    stage = Usd.Stage.Open(str(usdc))
    if stage is None:
        raise RuntimeError("OpenUSD could not reopen USDC")
    roots = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.ArticulationRootAPI)]
    bodies = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.RigidBodyAPI)]
    joints = [p for p in stage.Traverse() if p.IsA(UsdPhysics.Joint)]
    if len(roots) != 1 or len(bodies) != 2 or len(joints) != 1:
        raise RuntimeError("Unexpected articulation structure")
    for prim in joints:
        joint = UsdPhysics.Joint(prim)
        targets = [*joint.GetBody0Rel().GetTargets(), *joint.GetBody1Rel().GetTargets()]
        if (
            len(targets) != 2
            or targets[0] == targets[1]
            or not all(stage.GetPrimAtPath(p) for p in targets)
        ):
            raise RuntimeError("Invalid joint body targets")
    _, assets, unresolved = UsdUtils.ComputeAllDependencies(str(usdc))
    if assets or unresolved:
        raise RuntimeError("Offline USDC has external or unresolved assets")

    class FixtureRunner:
        def probe(self):
            return True, "offline fixture; no model"

        def run(self, **_kwargs):
            return RunnerResult(
                run_id=run_id, run_dir=run, artifact=usdz, artifacts={"usdz": usdz, "usdc": usdc}
            )

    config = ServerConfig.from_env({"ARTICULATION_OUTPUT_ROOT": str(output)})
    app = create_app(config, runner=FixtureRunner())
    client = app.test_client()
    manager = app.extensions["articulation_job_manager"]
    try:
        assert client.get("/health").status_code == 200
        assert client.post("/generate_articulation", json={}).status_code == 400
        submitted = client.post("/generate_articulation", json={"prompt": "offline fixture"})
        assert submitted.status_code == 202
        request_id = submitted.get_json()["request_id"]
        deadline = time.monotonic() + 10
        while True:
            task = client.get(f"/tasks/{request_id}").get_json()
            if task["status"] in {"succeeded", "failed", "cancelled"}:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("Offline queue timed out")
            time.sleep(0.02)
        assert task["status"] == "succeeded", task
        downloaded = client.get(f"/assets/{request_id}/usdc")
        assert downloaded.status_code == 200
        assert downloaded.data == usdc.read_bytes()
        report = {
            "mode": "offline-fixture",
            "real_model_calls": 0,
            "run_dir": str(run),
            "request_id": request_id,
            "compile_status": result["status"],
            "usdc": str(usdc),
            "usdc_bytes": usdc.stat().st_size,
            "sha256": hashlib.sha256(downloaded.data).hexdigest(),
            "articulation_roots": len(roots),
            "rigid_bodies": len(bodies),
            "joints": len(joints),
            "http_queue_and_download": "passed",
            "physical_validation": "not_run",
        }
        (run / "smoke-report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    finally:
        manager.shutdown()


if __name__ == "__main__":
    main()
