"""A four-bar linkage: the mechanism a tree of joints cannot describe.

Count the pivots. `coupler` is pinned to `left_crank` at one end and to
`right_crank` at the other, and both cranks are pinned to `ground`. That is a
ring of four joints around four bodies, so one joint has to close it.

Author every joint the mechanism physically has, then name the spanning tree in
`model.articulation(...)`. The joint left out is exported with
`excludeFromArticulation`, and a simulator solves it as an ordinary constraint.

Leaving that joint out entirely is the mistake this example exists to prevent:
the parts still export, and then flap loose the moment anything touches them.
"""

from __future__ import annotations

from build123d import Align, Axis, Box
from build123d.topology import Shape

from articraft.sdk import (
    BodyFrame,
    JointAxis,
    JointDOF,
    Material,
    RigidBody,
    RigidBodyAssembly,
    TestContext,
    TestReport,
)

SPAN = 0.120  # between the two ground pivots
RISE = 0.080  # crank length
BAR = 0.010  # bar thickness


def pin(body: RigidBody, direction: Axis, end: int) -> BodyFrame:
    """Select a bar endpoint from its geometry and give the pin a Y axis."""

    shape = body.shape("arm")
    if not isinstance(shape, Shape):
        raise TypeError("this example expects build123d bar geometry")
    face = shape.faces().sort_by(direction)[end]
    return body.at(Axis(face.center(), Axis.Y.direction))


def build_object_model() -> RigidBodyAssembly:
    model = RigidBodyAssembly("four_bar_linkage")

    def bar(name: str, length: float, upright: bool = False):
        body = model.rigid_body(name)
        size = (BAR, BAR, length) if upright else (length, BAR, BAR)
        align = (
            (Align.CENTER, Align.CENTER, Align.MIN)
            if upright
            else (Align.MIN, Align.CENTER, Align.CENTER)
        )
        body.add(
            Box(*size, align=align),
            name="arm",
            material=Material.STEEL if name == "ground" else Material.ALUMINUM,
        )
        return body

    ground = bar("ground", SPAN)
    left_crank = bar("left_crank", RISE, upright=True)
    coupler = bar("coupler", SPAN)
    right_crank = bar("right_crank", RISE, upright=True)

    # `pin(...)` aligns each selected physical axis with frame-local Z.
    swing = (JointDOF(JointAxis.ROT_Z, limits=(-0.6, 0.6)),)

    # Around the ring. Each pair of frames coincides at rest, so the rectangle
    # closes exactly in the authored pose.
    model.joint(
        "ground_left",
        pin(ground, Axis.X, 0),
        pin(left_crank, Axis.Z, 0),
        dofs=swing,
    )
    model.joint(
        "left_coupler",
        pin(left_crank, Axis.Z, -1),
        pin(coupler, Axis.X, 0),
        dofs=swing,
    )
    model.joint(
        "coupler_right",
        pin(coupler, Axis.X, -1),
        pin(right_crank, Axis.Z, -1),
        dofs=swing,
    )

    model.articulation(
        "main",
        root=ground,
        joints=["ground_left", "left_coupler", "coupler_right"],
    )

    # Derive the second endpoint from the already-authored spanning tree. The
    # closure is exact even when upstream dimensions or transforms change.
    ground_right = pin(ground, Axis.X, -1)
    model.joint(
        "ground_right",
        ground_right,
        model.frame_in(ground_right, right_crank),
        dofs=swing,
    )
    return model


object_model = build_object_model()


def run_tests() -> TestReport:
    ctx = TestContext(object_model)
    resolved = object_model.resolve()

    ctx.check(
        "linkage_closes_a_loop",
        resolved.has_closed_loops,
        "a four-bar is a ring, not a chain",
    )
    excluded = [item.joint.name for item in resolved.joints if item.exclude_from_articulation]
    ctx.check(
        "one_joint_closes_the_ring",
        excluded == ["ground_right"],
        f"expected ground_right to be the loop closer, found {excluded}",
    )
    ctx.expect_coincident(
        pin(object_model.get_rigid_body("ground"), Axis.X, -1),
        pin(object_model.get_rigid_body("right_crank"), Axis.Z, 0),
        name="right_pin_lands_on_both_bars",
    )
    with ctx.pose(ground_left=0.3):
        ctx.expect_coaxial(
            pin(object_model.get_rigid_body("ground"), Axis.X, -1),
            pin(object_model.get_rigid_body("right_crank"), Axis.Z, 0),
            position_tol=1e-5,
            angle_tol=1e-5,
            name="right_pin_stays_coaxial_while_moving",
        )
    return ctx.report()
