from __future__ import annotations


class MiniArticraftError(Exception):
    """Base error for articraft."""


class SDKError(MiniArticraftError):
    """Base error for the articraft SDK."""


class ValidationError(SDKError):
    """Raised when an articulated object definition is invalid."""


class LoopClosureError(ValidationError):
    """Raised when a pose cannot keep a closed loop assembled.

    No placement of the follower joints keeps the loop's pin closed at this
    pose. The message says which of two causes applies: when it names solved
    joint positions pinned at their limits, those limits are what stop the
    loop -- widen them if the pose should be reachable. Otherwise the link
    geometry itself cannot reach the pose: shorten the drive's range or fix
    the link lengths that decide it.
    """
