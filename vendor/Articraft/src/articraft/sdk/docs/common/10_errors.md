# Errors

Import the public SDK errors from `articraft.sdk`:

```python
from articraft.sdk import LoopClosureError, SDKError, ValidationError
```

## `SDKError`

`SDKError` is the base class for errors from the Articraft SDK. Catch it only when you can
handle all SDK validation failures in one place.

## `ValidationError`

The SDK raises `ValidationError` when an object does not follow the public contract. The error
can apply to these values:

- An assembly, body, or shape.
- A joint or articulation.
- A physics state.
- A named test selector.

Common causes include duplicate names, invalid geometry, and joint limits that exclude zero.
The SDK also rejects invalid physical graphs and articulation trees.

Value objects validate during construction. `RigidBody.add(...)` validates the shape that you
give it. `model.joint(...)` resolves its endpoints immediately.

Call `model.validate()` to check the complete assembly:

```python
try:
    model.validate()
except ValidationError as exc:
    print(exc)
```

Authored test assertions do not raise this error. `TestContext.check(...)` records failures in
its `TestReport`.

## `LoopClosureError`

`LoopClosureError` is a type of `ValidationError`. The solver raises it when tree coordinates
cannot satisfy a closed loop.

If the message names joints at their limits, those limits prevent the pose. Change the limits
only when the mechanism must reach that pose.

Otherwise, the linkage geometry cannot reach the pose. Check the drive range, link lengths,
and joint frames.

## Built in Python errors

Public helpers can also raise these Python errors:

- `TypeError` means that an argument is missing or has the wrong Python type.
- `ValueError` means that a value cannot produce valid geometry or motion.
- `FileNotFoundError` means that a required external asset does not exist.
- Another `OSError` means that an external asset operation failed.

Catch the narrow error that the operation documents. Do not continue with geometry that did
not build correctly.

## Lookups

A lookup raises `ValidationError` when it cannot resolve a name:

```python
body = model.get_rigid_body("body")
shape = body.shape("housing")
joint = model.get_joint("body_to_lid")
```

Body and joint names are unique within an assembly. Shape names are unique within a body.

Import Python values from `articraft.sdk`. A path such as `docs/sdk/common/20_core_types.md` is
documentation, not a Python module.
