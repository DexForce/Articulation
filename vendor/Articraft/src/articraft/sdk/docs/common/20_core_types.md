# Shared units and types

## Units and coordinates

Use meters for all geometry and linear motion. This rule applies to these values:

- Build123d dimensions and mesh vertices.
- `JointFrame.xyz` and translational degrees of freedom.
- Bounds, distances, and tolerances.

The SDK uses right handed XYZ coordinates with Z as the up axis.

Use radians for `JointFrame.rpy`, rotational motion, rotational limits, and mesh rotations.
Build123d `Rot(...)` uses degrees.

## Geometry

`RigidBody.add(...)` accepts a build123d `Shape` or `MeshGeometry`. Both types use the local
frame of the body.

Give each shape a unique name within its body. A shape can also have a `Material`, coating,
and RGB or RGBA color.

Read [assemblies and rigid bodies](30_assembly.md) for object structure. Read
[mesh geometry](../mesh/00_mesh_geometry.md) for procedural mesh authoring.

## Physics graphs

`BodyFrame`, `JointFrame`, `JointDOF`, `JointAxis`, and `Joint` define physical constraints.
A `BodyFrame` binds local coordinates to a `RigidBody` or `WORLD`.

An `Articulation` selects the solver tree. A `PhysicsState` stores the world pose of each body.

Read [joints and articulations](35_joints.md) for frames, axes, loops, and motion.

## Testing and errors

`TestContext` records authored checks. `TestReport` contains failures, warnings, metrics, and
approved allowances.

A test context can inspect an articulation pose or a complete `PhysicsState`.

The public errors are `SDKError`, `ValidationError`, and `LoopClosureError`. Read
[errors](10_errors.md) and [testing](40_testing.md).

## Export

`export_assembly(...)` writes a validated USDZ file and a schema version 2 manifest. Read
[USDZ export](50_usdz_export.md) for the complete output structure.
