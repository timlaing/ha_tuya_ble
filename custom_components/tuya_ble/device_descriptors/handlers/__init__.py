"""Handler registry for YAML-referenced device behavior.

Only handlers referenced from YAML device descriptors live in this package.
Handlers are resolved by dotted module path (e.g. ``battery.battery_enum``).
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import TypeVar, cast

_HANDLER_PREFIX = "custom_components.tuya_ble.device_descriptors.handlers"

_HandlerT = TypeVar("_HandlerT", bound=Callable[..., object])


def requires_dp_ids(*keys: str) -> Callable[[_HandlerT], _HandlerT]:
    """Declare the auxiliary ``*_dp_id`` descriptor fields a handler reads.

    The registry rejects a descriptor that references the handler without
    giving each of them a data point id, so a handler never has to cope with a
    missing one at runtime.
    """

    def decorate(handler: _HandlerT) -> _HandlerT:
        setattr(handler, "required_dp_ids", keys)  # noqa: B010
        return handler

    return decorate


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
