"""Runtime hooks the core gives to tools (set once by the gateway at start-up)."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from ..config import DEFAULT_SETTINGS

_settings: Callable[[], dict[str, Any]] = lambda: copy.deepcopy(DEFAULT_SETTINGS)


def bind_settings(provider: Callable[[], dict[str, Any]]) -> None:
    global _settings
    _settings = provider


def settings() -> dict[str, Any]:
    return _settings()
