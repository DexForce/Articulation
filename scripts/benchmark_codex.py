"""Run the historical image benchmark locally and independently validate USDC."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from articulation_server.client import ArticulationClient
from PIL import Image
from pxr import Usd, UsdGeom, UsdPhysics, UsdUtils

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    ROOT.parent / "baseline2/gym_project/articulation/benchmark/benchmark-manifest.json"
)


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_usdc(path: Path, expected: dict) -> dict:
    report = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
    report["binary_usdc"] = path.read_bytes().startswith(b"PXR-USDC")
    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise ValueError("OpenUSD could not open the downloaded USDC")
    roots = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.ArticulationRootAPI)]
    bodies = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.RigidBodyAPI)]
    collisions = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.CollisionAPI)]
    joints = []
    for prim in stage.Traverse():
        joint = UsdPhysics.Joint(prim)
        if not joint:
            continue
        body0 = joint.GetBody0Rel().GetTargets()
        body1 = joint.GetBody1Rel().GetTargets()
        valid_targets = (
            len(body0) == len(body1) == 1
            and body0[0] != body1[0]
            and all(
                stage.GetPrimAtPath(p).HasAPI(UsdPhysics.RigidBodyAPI) for p in [*body0, *body1]
            )
        )
        row = {
            "path": str(prim.GetPath()),
            "type": prim.GetTypeName(),
            "body0": list(map(str, body0)),
            "body1": list(map(str, body1)),
            "valid_body_targets": valid_targets,
            "enabled": joint.GetJointEnabledAttr().Get(),
        }
        typed = None
        if prim.IsA(UsdPhysics.PrismaticJoint):
            typed = UsdPhysics.PrismaticJoint(prim)
        elif prim.IsA(UsdPhysics.RevoluteJoint):
            typed = UsdPhysics.RevoluteJoint(prim)
        if typed is not None:
            lower, upper = typed.GetLowerLimitAttr().Get(), typed.GetUpperLimitAttr().Get()
            row.update(
                {
                    "axis": typed.GetAxisAttr().Get(),
                    "lower": lower,
                    "upper": upper,
                    "valid_limits": all(v is not None and math.isfinite(v) for v in (lower, upper))
                    and lower < upper
                    and lower <= 0 <= upper,
                }
            )
        joints.append(row)
    masses = []
    transforms_finite = True
    for prim in bodies:
        api = UsdPhysics.MassAPI(prim)
        mass = api.GetMassAttr().Get() if api else None
        inertia = api.GetDiagonalInertiaAttr().Get() if api else None
        masses.append(
            {
                "path": str(prim.GetPath()),
                "mass": mass,
                "diagonal_inertia": list(inertia) if inertia is not None else None,
                "valid_if_authored": (mass is None or math.isfinite(mass) and mass > 0)
                and (inertia is None or all(math.isfinite(v) and v > 0 for v in inertia)),
            }
        )
        matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        transforms_finite &= all(math.isfinite(v) for row in matrix for v in row)
    _, assets, unresolved = UsdUtils.ComputeAllDependencies(str(path))
    counts = Counter(j["type"] for j in joints)
    count_pass = (
        counts["PhysicsPrismaticJoint"] >= expected["minimum_prismatic_joints"]
        and counts["PhysicsRevoluteJoint"] >= expected["minimum_revolute_joints"]
        and len(joints) >= expected["minimum_total_joints"]
    )
    root_contains_assembly = len(roots) == 1 and all(
        p.GetPath().HasPrefix(roots[0].GetPath()) for p in [*bodies, *collisions]
    )
    report.update(
        {
            "stage_open": True,
            "articulation_roots": len(roots),
            "rigid_bodies": len(bodies),
            "collision_prims": len(collisions),
            "joints": joints,
            "joint_type_counts": dict(counts),
            "expected_joint_count_pass": count_pass,
            "masses": masses,
            "mass_properties_complete": all(
                m["mass"] is not None and m["diagonal_inertia"] is not None for m in masses
            ),
            "transforms_finite": transforms_finite,
            "external_assets": list(map(str, assets)),
            "unresolved_assets": list(map(str, unresolved)),
            "assembly_under_root": root_contains_assembly,
            "physical_validation": "not_run",
            "visual_semantic_validation": "human_review_required",
        }
    )
    report["static_acceptance"] = bool(
        report["binary_usdc"]
        and count_pass
        and root_contains_assembly
        and len(bodies) >= 2
        and collisions
        and joints
        and all(
            j["valid_body_targets"] and j["enabled"] and j.get("valid_limits", True) for j in joints
        )
        and all(m["valid_if_authored"] for m in masses)
        and transforms_finite
        and not assets
        and not unresolved
    )
    return report


def run_stats(run_dir: Path) -> dict:
    conversation = run_dir / "conversation.jsonl"
    events = (
        [json.loads(line) for line in conversation.read_text().splitlines()]
        if conversation.is_file()
        else []
    )
    calls = [call for event in events for call in event.get("tool_calls", [])]
    previews = [str(p) for p in (run_dir / "workspace").rglob("*.png") if "docs" not in p.parts]
    return {
        "assistant_turns": sum(e.get("role") == "assistant" for e in events),
        "compile_calls": sum(c.get("name") == "compile" for c in calls),
        "preview_images": previews,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--continue-on-failure", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    order = args.cases or manifest["execution_order"]
    prepared = []
    for name in order:
        case = manifest["cases"][name]
        image = (args.manifest.parent / case["image"]).resolve()
        with Image.open(image) as opened:
            dimensions = opened.size
        if sha256(image) != case["sha256"] or dimensions != (case["width"], case["height"]):
            raise ValueError(f"Historical benchmark image has changed: {name}")
        prepared.append(
            {
                "case_id": name,
                **case,
                "source_image": str(image),
                "prompt_sha256": hashlib.sha256(case["prompt"].encode()).hexdigest(),
            }
        )
    client = ArticulationClient("http://127.0.0.1:18689")
    try:
        health = client.health()
        if health.get("provider") != "codex-cli" or not health.get("ok"):
            raise RuntimeError(f"Local Codex backend is unavailable: {health}")
        active = [
            p
            for p in (ROOT / "outputs/codex/jobs").glob("*/job.json")
            if json.loads(p.read_text()).get("status") not in {"succeeded", "failed", "cancelled"}
        ]
        if active:
            raise RuntimeError("Local backend has existing active jobs; do not duplicate them")
        if not args.run:
            print(
                json.dumps(
                    {"dry_run": True, "health": health, "cases": prepared},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        output = args.output or ROOT / "outputs/benchmarks" / datetime.now(UTC).strftime(
            "codex-%Y%m%dT%H%M%SZ"
        )
        output.mkdir(parents=True, exist_ok=False)
        (output / "images").mkdir()
        report = {
            "started_at": datetime.now(UTC).isoformat(),
            "source_manifest": str(args.manifest),
            "seed_metadata": manifest["seed"],
            "inference_seed_controlled": False,
            "health_before": health,
            "backend": "local-codex-login",
            "reasoning_effort": codex_runtime_effort(),
            "continue_on_failure": args.continue_on_failure,
            "planned_cases": order,
            "results": [],
            "completed": False,
        }
        write_json(output / "benchmark.json", report)
        print(f"BENCHMARK_OUTPUT={output}", flush=True)
        started_all = time.monotonic()
        for case in prepared:
            case_id = case["case_id"]
            directory = output / case_id
            directory.mkdir()
            image = output / "images" / Path(case["source_image"]).name
            shutil.copyfile(case["source_image"], image)
            row = {"case_id": case_id, "input": case, "status": "submitting"}
            report["results"].append(row)
            write_json(output / "benchmark.json", report)
            started = time.monotonic()
            # POST is intentionally not retried: an uncertain reply could duplicate paid work.
            try:
                submitted = client.submit(case["prompt"], image=image)
            except Exception as exc:
                row.update({"status": "submission_outcome_unknown", "error": str(exc)})
                write_json(output / "benchmark.json", report)
                raise
            request_id = submitted["request_id"]
            row.update({"status": "running", "request_id": request_id})
            write_json(output / "benchmark.json", report)
            print(f"SUBMITTED {case_id} request_id={request_id}", flush=True)
            try:
                task = client.wait(
                    request_id,
                    timeout_seconds=manifest["limits"]["task_timeout_seconds"],
                    poll_interval_seconds=2,
                )
            except (TimeoutError, KeyboardInterrupt):
                write_json(directory / "cancellation.json", client.cancel(request_id))
                task = client.wait(request_id, timeout_seconds=60, poll_interval_seconds=1)
            write_json(directory / "task.json", task)
            row.update(
                {"status": task["status"], "elapsed_seconds": round(time.monotonic() - started, 3)}
            )
            if task["status"] == "succeeded":
                try:
                    artifact = client.download(request_id, "usdc", directory / f"{case_id}.usdc")
                except Exception as exc:
                    row.update({"error": f"USDC download failed: {exc}", "pass": False})
                    write_json(output / "benchmark.json", report)
                    if not args.continue_on_failure:
                        report["stopped_reason"] = "service succeeded without a downloadable USDC"
                        break
                    continue
                job = json.loads(
                    (ROOT / "outputs/codex/jobs" / request_id / "job.json").read_text()
                )
                original = Path(job["artifacts"]["usdc"])
                run_dir = ROOT / "outputs/codex/runs" / task["result"]["run_id"]
                validation = validate_usdc(artifact, case["expected"])
                validation["download_hash_matches_server"] = sha256(original) == sha256(artifact)
                write_json(directory / "validation.json", validation)
                row.update(
                    {
                        "run_dir": str(run_dir),
                        "artifact": str(artifact),
                        "validation": validation,
                        "stats": run_stats(run_dir),
                        "pass": validation["static_acceptance"]
                        and validation["download_hash_matches_server"],
                    }
                )
            else:
                row.update({"error": task.get("error", ""), "pass": False})
            write_json(output / "benchmark.json", report)
            print(
                json.dumps(
                    {
                        k: row.get(k)
                        for k in (
                            "case_id",
                            "request_id",
                            "status",
                            "elapsed_seconds",
                            "pass",
                            "error",
                        )
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if not row["pass"] and not args.continue_on_failure:
                report["stopped_reason"] = (
                    "first failed case; preserve evidence and diagnose before more model calls"
                )
                break
            if time.monotonic() - started_all >= manifest["limits"]["total_timeout_seconds"]:
                report["stopped_reason"] = "total benchmark time limit reached"
                break
        report.update(
            {
                "completed": len(report["results"]) == len(order),
                "finished_at": datetime.now(UTC).isoformat(),
                "health_after": client.health(),
            }
        )
        write_json(output / "benchmark.json", report)
        return 0 if report["completed"] and all(r.get("pass") for r in report["results"]) else 1
    finally:
        client.close()


def codex_runtime_effort() -> str:
    pid = int((ROOT / ".runtime/codex.pid").read_text())
    process = Path(f"/proc/{pid}")
    if (process / "cwd").resolve() != ROOT:
        raise RuntimeError("Codex service PID does not belong to this workspace")
    values = dict(
        entry.decode().split("=", 1)
        for entry in (process / "environ").read_bytes().split(b"\0")
        if b"=" in entry
    )
    return values["ARTICRAFT_REASONING_EFFORT"]


if __name__ == "__main__":
    sys.exit(main())
