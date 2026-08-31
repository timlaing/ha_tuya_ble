"""Handler registry for YAML-referenced device behavior.

Only handlers referenced from YAML device descriptors live in this package.
Handlers are resolved by dotted module path (e.g. ``co2.alarm_enabled``).
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import cast

_HANDLER_PREFIX = "custom_components.tuya_ble.device_descriptors.handlers"


def resolve_handler(path: str) -> Callable[..., object]:
    """Resolve a dotted handler path to a callable.

    Paths are interpreted relative to the handlers package namespace, e.g.
    ``fingerbot.program.get_program`` resolves to the matching callable in the
    ``device_descriptors.handlers`` package.
    """
    module_name, _, attr = path.rpartition(".")
    if not module_name:
        raise ValueError(f"Invalid handler path: {path!r}")
    module = import_module(f"{_HANDLER_PREFIX}.{module_name}")
    handler = getattr(module, attr)
    if not callable(handler):
        raise TypeError(f"Handler is not callable: {path!r}")
    return cast(Callable[..., object], handler)
