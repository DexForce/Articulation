# USDZ export

Import the exporter from `articraft.sdk.export`:

```python
from articraft.sdk.export import export_assembly

result = export_assembly(object_model, "output")
```

`export_assembly(...)` resolves and validates the assembly. It writes the next numbered USDZ
file under `output/usdz/`.

The function also replaces `output/model.json` with one atomic file operation. It returns an
`AssemblyExportResult` with paths, texture results, and an `AssemblyExportAudit`.

Compile attempts write only numbered USDZ files. After a generation succeeds, articraft creates
one EmbodiChain-compatible `output/usdc/<final-version>/model.usdc` from the USDZ selected by
`record.result`. This compatibility layer makes the assembly the stage default prim and moves
`ArticulationRootAPI` to that assembly so DexSim discovers every sibling rigid body and joint.
The native USDZ attempts remain unchanged for QA and historical preview.

## Stage layout

The exporter uses this USD structure:

```text
/World/physicsScene
/World/<assembly>                         kind=assembly
/World/<assembly>/rigid_bodies/<body>
/World/<assembly>/rigid_bodies/<body>/shapes/<shape>
/World/<assembly>/joints/<joint>
```

Rigid bodies are siblings. Each body has its world transform from the reference state.

Each physical joint stores its two local endpoint frames and targets its two bodies.

- A joint with no free axis uses `UsdPhysics.FixedJoint`.
- A joint with one rotational axis uses `UsdPhysics.RevoluteJoint`.
- A joint with one translational axis uses `UsdPhysics.PrismaticJoint`.
- Other joints use `UsdPhysics.Joint` with an axis limit schema.

The exporter converts rotational limits from radians to degrees. Linear limits remain in
meters. Unlisted axes remain locked.

The selected root body or world joint gets `UsdPhysics.ArticulationRootAPI`. The assembly prim
does not get this API.

A physical joint outside the articulation tree gets `physics:excludeFromArticulation = true`.

## Manifest schema 2

`model.json` records these values:

- Rigid bodies, shapes, materials, mass, and body state.
- Joint endpoints, frames, free axes, limits, and articulation membership.
- Each articulation root and its selected tree joints.
- The complete reference `PhysicsState`.
- The numbered USDZ path.

The manifest describes the output. Do not use it as an authoring API.

## Validation and audit

Export runs OpenUSD validators and an internal topology audit. The audit checks these rules:

- Articulation edges form trees.
- Every physical joint is present.
- Excluded constraints have the required USD flag.
- Every body target exists.
- Every mesh has normals.
- Material bindings remain after packaging.

The exporter removes a partial USDZ file when validation fails. It does not overwrite an
existing numbered export.

USD can represent closed loops. Their stability depends on the physics backend, time step,
and solver settings.
