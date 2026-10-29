"""Boolean coercion helpers for tool parameters and preferences."""

from __future__ import annotations

from typing import Any, Dict, Optional


_TRUE_STRINGS = {"1", "true", "yes", "on"}
_FALSE_STRINGS = {"0", "false", "no", "off"}


def coerce_bool(value: Any, default: bool = False) -> bool:
    """Return a predictable bool for user-facing params and config values."""
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
        return default
    return bool(value)


def explicit_bool_param(params: Optional[Dict[str, Any]], *keys: str) -> Optional[bool]:
    """Coerce the first present key, or None when none of the keys are present."""
    if not isinstance(params, dict):
        return None
    for key in keys:
        if key in params:
            return coerce_bool(params[key])
    return None


def recognised_bool(value: Any) -> Optional[bool]:
    """Coerce only values that unambiguously name a boolean, else None.

    ``coerce_bool`` falls back to a default for anything it does not recognise,
    which is right for an optional flag and wrong for a value that is forwarded
    to Resolve as the setting itself: ``"maybe"`` must be refused, not silently
    read as ``False``. Accepts real bools, the true/false strings
    ``coerce_bool`` knows, and the integers 0 and 1. Everything else is None,
    so the caller can refuse it with a reason.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
    return None
