# Simulation settings

Simulation settings define the world and the initial motion of each body. They do not change
geometry.

Articraft writes these settings into the USDZ file. The local viewer and `simulate_usdz` use
Earth gravity with free bodies at rest.

```python
from articraft.sdk import BodyState, PhysicsScene
```

One `PhysicsScene` belongs to the assembly. One `BodyState` belongs to each rigid body.

## `PhysicsScene`

Use `PhysicsScene` to set gravity for the exported world:

```python
PhysicsScene(direction: Vec3 = (0.0, 0.0, -1.0), magnitude: float = 9.81)
```

The default is Earth gravity along the negative Z axis:

```python
from articraft.sdk import PhysicsScene, RigidBodyAssembly

moon = RigidBodyAssembly("rover", scene=PhysicsScene(magnitude=1.62))
```

`direction` is a world direction. The constructor normalizes it, so its original length has no
effect. The direction must be nonzero.

`magnitude` uses meters per second squared. It must be zero or positive. Use `EARTH_GRAVITY`
when you need the named default constant.

## `BodyState`

Use `BodyState` to set the initial state of one body:

```python
BodyState(
    enabled: bool = True,
    kinematic: bool = False,
    linear_velocity: Vec3 = (0.0, 0.0, 0.0),
    angular_velocity: Vec3 = (0.0, 0.0, 0.0),
    starts_asleep: bool = False,
)
```

The default is an enabled body at rest:

```python
from articraft.sdk import BodyState

base = model.rigid_body("base", body_state=BodyState(kinematic=True))
flywheel = model.rigid_body(
    "flywheel",
    body_state=BodyState(angular_velocity=(0.0, 0.0, 12.0)),
)
```

- Set `enabled=False` to make a static collider. Forces do not move a disabled body.
- Set `kinematic=True` when animation moves the body. A disabled body cannot be kinematic.
- Set `linear_velocity` in meters per second and world coordinates.
- Set `angular_velocity` in radians per second and world coordinates.
- Set `starts_asleep=True` to keep the body asleep until an interaction wakes it.

USD stores angular velocity in degrees per second. The exporter converts the SDK value.
