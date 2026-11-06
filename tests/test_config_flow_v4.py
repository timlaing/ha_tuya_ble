"""FD50 discovery reaches version-aware identity decoding and cloud matching."""

# pylint: disable=protected-access

from typing import Any, cast
from unittest.mock import patch

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.tuya_ble.tuya_ble import SERVICE_UUID_V4
from tests.conftest import make_credentials
from tests.test_advertisement import v4_advertisement
from tests.test_config_flow import FakeDiscovery, FakeManager, build_flow


def discovery(version: int = 4) -> FakeDiscovery:
    """Build a synthetic FD50 scan response without real Bluetooth I/O."""
    info = FakeDiscovery()
    adv = v4_advertisement(bytes((version << 4 | 9, 0, 0, 8)) + b"abcdefgh")
    info.service_data = adv.service_data
    info.manufacturer_data = adv.manufacturer_data
    info.advertisement = cast(Any, adv)
    return info


@pytest.mark.parametrize("version", [4, 5])
async def test_fd50_setup(hass: HomeAssistant, version: int) -> None:
    """V4 matches by UUID; v5 reports decoding failure without cloud lookup."""
    flow = build_flow(hass)
    flow.context = {}
    info = discovery(version)
    flow.discovery_info = cast(Any, info)
    manager = FakeManager(make_credentials())
    flow._manager = cast(Any, manager)

    async def process_advertisements(
        _hass: HomeAssistant,
        predicate: Any,
        _matcher: Any,
        _mode: Any,
        _timeout: int,
    ) -> FakeDiscovery:
        assert predicate(info)
        incomplete = discovery(version)
        incomplete.service_data = {SERVICE_UUID_V4: b"\x41"}
        assert not predicate(incomplete)
        incomplete = discovery(version)
        incomplete.manufacturer_data = {2000: bytes(22)}
        assert not predicate(incomplete)
        incomplete.manufacturer_data = {}
        assert not predicate(incomplete)
        return info

    with patch(
        "custom_components.tuya_ble.config_flow.bluetooth.async_process_advertisements",
        side_effect=process_advertisements,
    ):
        result = await flow.async_step_discovered_device({})
    if version == 4:
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert manager.calls == [("1234567890abcdef", False)]
        assert result["data"]["uuid"] == "1234567890abcdef"
    else:
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "identity_not_decoded"}
        assert not manager.calls


async def test_fd50_manual_discovery(hass: HomeAssistant) -> None:
    """Include FD50 devices in the manual device selector."""
    flow = build_flow(hass)
    flow.context = {}
    info = discovery()
    with (
        patch(
            "custom_components.tuya_ble.config_flow.async_discovered_service_info",
            return_value=[info],
        ),
        patch.object(flow, "_async_current_ids", return_value=set()),
    ):
        result = await flow.async_step_device()
    assert result["type"] == FlowResultType.FORM
    assert (
        info.address in cast(Any, result["data_schema"]).schema[CONF_ADDRESS].container
    )
