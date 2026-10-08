from pxr import Sdf, Usd, UsdGeom, UsdPhysics

from scripts.benchmark_codex import validate_usdc


def fixture(tmp_path):
    path = tmp_path / "model.usdc"
    stage = Usd.Stage.CreateNew(str(path))
    assembly = UsdGeom.Xform.Define(stage, "/World/assembly").GetPrim()
    UsdPhysics.ArticulationRootAPI.Apply(assembly)
    stage.SetDefaultPrim(assembly)
    for name in ("base", "arm"):
        prim = UsdGeom.Xform.Define(stage, f"/World/assembly/{name}").GetPrim()
        UsdPhysics.RigidBodyAPI.Apply(prim)
        cube = UsdGeom.Cube.Define(stage, f"/World/assembly/{name}/shape")
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    joint = UsdPhysics.RevoluteJoint.Define(stage, "/World/assembly/hinge")
    joint.CreateBody0Rel().SetTargets([Sdf.Path("/World/assembly/base")])
    joint.CreateBody1Rel().SetTargets([Sdf.Path("/World/assembly/arm")])
    joint.CreateLowerLimitAttr(-10)
    joint.CreateUpperLimitAttr(10)
    stage.GetRootLayer().Save()
    return path, stage, joint


EXPECTED = {"minimum_prismatic_joints": 0, "minimum_revolute_joints": 1, "minimum_total_joints": 1}


def test_static_usdc_does_not_claim_missing_mass_is_physical_proof(tmp_path):
    path, _stage, _joint = fixture(tmp_path)
    report = validate_usdc(path, EXPECTED)
    assert report["static_acceptance"]
    assert not report["mass_properties_complete"]
    assert report["physical_validation"] == "not_run"
    assert not validate_usdc(path, {**EXPECTED, "minimum_prismatic_joints": 1})["static_acceptance"]


def test_invalid_joint_targets_fail(tmp_path):
    path, stage, joint = fixture(tmp_path)
    joint.GetBody1Rel().SetTargets([Sdf.Path("/World/assembly/base")])
    stage.GetRootLayer().Save()
    assert not validate_usdc(path, EXPECTED)["static_acceptance"]
