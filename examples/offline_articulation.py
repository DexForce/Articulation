"""Deterministic offline fixture, not a model-generated or physics-qualified asset."""

from articraft.sdk import JointAxis, JointDOF, RigidBodyAssembly, TestContext, TestReport
from build123d import Box

object_model = RigidBodyAssembly("offline_hinge")
base = object_model.rigid_body("base")
base.add(Box(0.20, 0.20, 0.04), name="plinth", color=(0.2, 0.5, 0.6))
arm = object_model.rigid_body("arm")
arm.add(Box(0.04, 0.04, 0.12).translate((0, 0, 0.06)), name="arm", color=(0.7, 0.3, 0.4))
object_model.joint(
    "hinge",
    base.at((0, 0, 0.02)),
    arm.at(),
    dofs=(JointDOF(JointAxis.ROT_Y, limits=(-0.3, 0.3)),),
)
object_model.articulation("main", root=base, joints=["hinge"])


def run_tests() -> TestReport:
    context = TestContext(object_model)
    context.expect_contact("base", "arm", shape_a="plinth", shape_b="arm")
    return context.report()
