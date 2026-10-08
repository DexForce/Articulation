# Materials and mass

Use `Material` to define the substance of a shape. A material provides mass, contact values,
and appearance.

When physics mode is on, each body must have measurable mass. Give every shape a material or
set `MassProperties` on the body.

Use these units:

- Use kilograms for mass.
- Use kilograms per cubic meter for density.
- Use meters for lengths.
- Use kilogram square meters for inertia.

## Use a library material

Pass a material when you add a shape:

```python
from articraft.sdk import Material, RigidBodyAssembly

model = RigidBodyAssembly("kettle")
base = model.rigid_body("base")
base.add(shell, name="shell", material=Material.STEEL)
```

The library provides these values:

| Material | Density | Static and dynamic friction | Restitution |
| --- | --- | --- | --- |
| `Material.STEEL` | 7850 | 0.42 and 0.36 | 0.55 |
| `Material.ALUMINUM` | 2700 | 0.45 and 0.38 | 0.40 |
| `Material.ABS_PLASTIC` | 1050 | 0.40 and 0.32 | 0.45 |
| `Material.GLASS` | 2500 | 0.40 and 0.35 | 0.60 |
| `Material.HARDWOOD` | 700 | 0.50 and 0.40 | 0.35 |
| `Material.RUBBER` | 1200 | 0.95 and 0.85 | 0.75 |

## Create a material variant

Use `Material.but(...)` to copy a material and replace selected values. The result keeps the
texture of its source material.

```python
BRUSHED = Material.STEEL.but(roughness=0.75)
GRIPPY = Material.RUBBER.but(name="tacky", friction=(1.1, 0.95))
```

Create a new `Material` only when the library has no suitable substance:

```python
CERAMIC = Material(name="ceramic", density=2400.0, friction=(0.45, 0.40))
```

`density` is required. `friction` is optional. If you omit friction, export does not add
contact values.

Use a library material when possible. The manifest records the library source.

## Use multiple materials in one body

Material belongs to a shape, so one body can contain several materials:

```python
box = model.rigid_body("toolbox")
box.add(shell, name="shell", material=Material.STEEL)
box.add(grip, name="grip", material=Material.HARDWOOD)
box.add(pad, name="foot", material=Material.RUBBER)
```

The SDK measures each shape with its material. It combines the results for body mass, center of
mass, and inertia.

## Understand mass measurement

The SDK measures volume, center of mass, and the complete inertia tensor from meshes. Each
shape must be a closed solid.

The SDK unites shapes with the same material before measurement. Shared volume from intentional
overlap is counted once.

A hollow shell has the mass of its walls. A shape that is not closed cannot have measured
mass, so compilation fails.

The SDK repairs inverted face winding before measurement.

If you set `center_of_mass`, the SDK shifts a measured inertia tensor to that point. It uses
the parallel axis theorem.

If you set `diagonal_inertia`, the SDK uses the tensor without changes.

## Override mass values

Use `MassProperties` when geometry cannot provide the correct physical values. Each field is
optional, and the SDK measures omitted fields.

```python
model.rigid_body(
    "stone",
    mass_properties=MassProperties(density=2600.0),
)
model.rigid_body(
    "motor",
    mass_properties=MassProperties(
        mass=0.85,
        center_of_mass=(0.0, 0.0, 0.04),
    ),
)
```

An explicit body `mass` or `density` replaces material based mass measurement. Shape materials
still control contact values and appearance.

## Use physics mode

Enable physics requirements with `articraft generate --physics ...`. Compilation fails if any
body does not have measurable mass.

The error names each body that the SDK cannot measure. Add shape materials or explicit mass
properties to fix it.

When physics mode is off, the exporter can write a body without mass.

## Model hollow objects

Mass comes from the authored geometry. A solid block has the mass of a solid block.

Create walls and cavities for objects that are hollow. Dense materials make an incorrect solid
model much heavier than the intended object.

```python
shell = boolean_difference(outer, inner)
```

Use measured dimensions when you have them. The following values can help with an initial
model:

| Object | Typical wall thickness |
| --- | --- |
| Sheet metal panel or appliance body | 0.5 to 1.5 mm |
| Cast metal housing or cookware | 2 to 4 mm |
| Injection molded plastic shell | 1.5 to 3 mm |
| Wooden board or panel | 10 to 20 mm |
| Glass bottle or window | 2 to 5 mm |

Compare the final mass with a known object of the same type. If the mass is too large, check
for geometry that must be hollow.

## Export materials and mass

The exporter applies `UsdPhysics.MassAPI` to each body with measurable mass. It writes these
attributes:

- `physics:mass`.
- `physics:centerOfMass`.
- `physics:diagonalInertia`.
- `physics:principalAxes`.

The exporter applies `UsdPhysics.MaterialAPI` to each collider with contact values. A coating
replaces the surface material, but it does not replace core density.

Friction belongs to each shape. For example, rubber feet can provide friction for a steel
frame.

Read `docs/sdk/common/50_usdz_export.md` for the complete USD structure.
