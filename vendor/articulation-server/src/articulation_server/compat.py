from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

_OPENROUTER_MODULE = "articraft.agent.provider.openrouter"


def prepare_articraft(provider: str | None = None) -> None:
    """Work around the current local Articraft checkout's missing provider module.

    Articraft imports its OpenRouter provider unconditionally, even when another
    provider is selected.  Some checkouts declare that provider but do not ship
    ``openrouter.py``.  A placeholder keeps the other providers usable while an
    actual OpenRouter request still fails clearly.
    """
    if _module_file_exists() or _OPENROUTER_MODULE in sys.modules:
        return
    if provider == "openrouter":
        raise RuntimeError(
            "ARTICRAFT_PROVIDER=openrouter is unavailable because the configured "
            "Articraft checkout does not contain agent/provider/openrouter.py"
        )

    module = ModuleType(_OPENROUTER_MODULE)

    class OpenRouterModel:  # pragma: no cover - only instantiated by a bad upstream path
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("OpenRouter is unavailable in the configured Articraft checkout")

    module.OpenRouterModel = OpenRouterModel  # type: ignore[attr-defined]
    sys.modules[_OPENROUTER_MODULE] = module


def _module_file_exists() -> bool:
    relative = Path("articraft/agent/provider/openrouter.py")
    return any(entry and (Path(entry) / relative).is_file() for entry in sys.path)


__all__ = ["prepare_articraft"]
