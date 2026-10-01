"""Edge-case tests for the BLE protocol: bounds, rollback and malformed frames.

Split from `test_protocol.py`, which covers the packet-level happy paths, to keep
each module within pylint's line limit. Everything here asserts a failure mode
rather than a round trip.
"""

# pylint: disable=protected-access, redefined-outer-name
from __future__ import annotations

import logging
from unittest.mock import patch

from bleak.exc import BleakError
import pytest

from custom_components.tuya_ble.tuya_ble.const import (
    PAIRING_REQUEST_LENGTH,
    TuyaBLECode,
    TuyaBLEDataPointType,
)
from custom_components.tuya_ble.tuya_ble.exceptions import (
    TuyaBLEDataFormatError,
    TuyaBLEDataLengthError,
    TuyaBLEDeviceError,
    TuyaBLEOutgoingDataLengthError,
)
from tests.conftest import make_credentials
from tests.protocol_harness import ProtocolHarness, device_info_data


@pytest.fixture
async def h() -> ProtocolHarness:
    """Build a protocol harness with notifications registered."""
    harness = ProtocolHarness()
    await harness.register_notify()
    return harness


async def test_send_datapoints_v3_rejects_oversized_value(
    h: ProtocolHarness,
) -> None:
    """A value longer than the envelope's byte-sized length must be rejected."""
    h.device._protocol_version = 3
    dp = h.device.get_or_create_datapoint(1, TuyaBLEDataPointType.DT_RAW, b"")
    dp.set_value_no_notify(b"\x00" * 256)

    with pytest.raises(TuyaBLEOutgoingDataLengthError):
        await h.device.send_datapoints([1])


async def test_send_datapoints_v3_rejects_oversized_dp_id(
    h: ProtocolHarness,
) -> None:
    """A dp_id above 255 cannot be encoded in the envelope's byte-sized field."""
    h.device._protocol_version = 3
    dp = h.device.get_or_create_datapoint(1, TuyaBLEDataPointType.DT_BOOL, False)
    # Re-key the point under an id the envelope cannot encode, which is the only
    # way an out-of-range id reaches the serializer.
    dp._id = 300
    h.device.datapoints._datapoints[300] = h.device.datapoints._datapoints.pop(1)

    with pytest.raises(TuyaBLEOutgoingDataLengthError):
        await h.device.send_datapoints([300])


async def test_set_multiple_values_reverts_local_state_on_failure(
    h: ProtocolHarness,
) -> None:
    """A rejected bulk send must not leave the local values at the new ones."""
    h.device._protocol_version = 3
    first = h.device.get_or_create_datapoint(1, TuyaBLEDataPointType.DT_BOOL, False)
    second = h.device.get_or_create_datapoint(2, TuyaBLEDataPointType.DT_BOOL, True)

    with (
        patch.object(h.device, "send_datapoints", side_effect=TuyaBLEDeviceError(0)),
        pytest.raises(TuyaBLEDeviceError),
    ):
        await h.device.set_multiple_values({1: True, 2: False})

    assert first.value is False
    assert second.value is True


async def test_set_multiple_values_rejects_old_protocol(
    h: ProtocolHarness,
) -> None:
    """An unsupported protocol version is refused before any local value is written."""
    h.device._protocol_version = 2
    dp = h.device.get_or_create_datapoint(1, TuyaBLEDataPointType.DT_BOOL, False)

    with pytest.raises(TuyaBLEDeviceError):
        await h.device.set_multiple_values({1: True})

    assert dp.value is False


async def test_set_multiple_values_skips_unknown_id(
    h: ProtocolHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """An unknown dp id is reported rather than silently dropping the write."""
    h.device._protocol_version = 3
    known = h.device.get_or_create_datapoint(1, TuyaBLEDataPointType.DT_BOOL, False)

    with caplog.at_level(logging.WARNING):
        await h.device.set_multiple_values({99: True})

    assert known.value is False
    assert "Skipping unknown datapoint id 99" in caplog.text


async def test_set_multiple_values_no_ids_sends_nothing(
    h: ProtocolHarness,
) -> None:
    """An update of only unknown ids must not attempt a send."""
    h.device._protocol_version = 3

    with patch.object(h.device, "send_datapoints") as send:
        await h.device.set_multiple_values({99: True})

    send.assert_not_called()


async def test_receive_sign_dp_too_short(h: ProtocolHarness) -> None:
    """A sign_dp frame without the seq/flags header must be rejected."""
    with pytest.raises(TuyaBLEDataLengthError):
        h.device._handle_receive_sign_dp(1, b"\x00\x01")


async def test_receive_sign_time_dp_too_short(h: ProtocolHarness) -> None:
    """A sign_time_dp frame without the seq/flags header must be rejected."""
    with pytest.raises(TuyaBLEDataLengthError):
        h.device._handle_receive_sign_time_dp(1, b"\x00\x01")


async def test_decrypt_input_rejects_empty_buffer(h: ProtocolHarness) -> None:
    """An empty buffer must raise a Tuya error rather than IndexError."""
    h.device._input_buffer = bytearray()

    with pytest.raises(TuyaBLEDataFormatError):
        h.device._decrypt_input()


def test_pairing_request_is_padded_to_fixed_length(
    h: ProtocolHarness,
) -> None:
    """The pairing payload must be zero-padded to the fixed frame length."""
    credentials = h.device._device_info
    local_key = h.device._local_key
    assert credentials is not None
    assert local_key is not None

    assert h.device._build_pairing_request() == (
        credentials.uuid.encode() + local_key + credentials.device_id.encode()
    ).ljust(PAIRING_REQUEST_LENGTH, b"\x00")


def test_pairing_request_at_max_lengths_is_padded(h: ProtocolHarness) -> None:
    """Credentials at the protocol limits still gain the trailing pad bytes."""
    h.device._device_info = make_credentials(uuid="u" * 16, device_id="d" * 20)

    assert h.device._build_pairing_request() == (
        b"u" * 16 + b"abcdef" + b"d" * 20
    ).ljust(PAIRING_REQUEST_LENGTH, b"\x00")


def test_pairing_request_too_long_raises(h: ProtocolHarness) -> None:
    """A credential set that overflows the frame must be refused, not truncated."""
    # The cloud-side limit rejects this pair, so reaching the frame builder only
    # happens if that check is bypassed; the builder must still refuse it.
    h.device._device_info = make_credentials(uuid="u" * 25, device_id="d" * 20)

    with pytest.raises(TuyaBLEDeviceError):
        h.device._build_pairing_request()


def test_pairing_request_without_device_info_raises(h: ProtocolHarness) -> None:
    """Building a pairing request before credentials are known must fail."""
    h.device._device_info = None

    with pytest.raises(TuyaBLEDeviceError):
        h.device._build_pairing_request()


async def test_send_datapoints_v3_unknown_id_raises(h: ProtocolHarness) -> None:
    """A datapoint id the device does not know must be refused, not encoded."""
    h.device._protocol_version = 3

    with pytest.raises(TuyaBLEDeviceError):
        await h.device.send_datapoints([42])


@pytest.mark.parametrize("security_flag", [1, 4, 5])
def test_get_key_without_key_raises(h: ProtocolHarness, security_flag: int) -> None:
    """A missing key for any known security flag must be reported."""
    keys = {
        1: "_auth_key",
        4: "_login_key",
        5: "_session_key",
    }
    setattr(h.device, keys[security_flag], None)

    with pytest.raises(TuyaBLEDeviceError):
        h.device._get_key(security_flag)


def test_get_key_unknown_flag_raises(h: ProtocolHarness) -> None:
    """An unknown security flag is a format error, not a missing-key error."""
    with pytest.raises(TuyaBLEDataFormatError):
        h.device._get_key(9)


def test_select_encryption_key_device_info_without_login_key(
    h: ProtocolHarness,
) -> None:
    """A device_info request before the login key is known must fail."""
    h.device._login_key = None

    with pytest.raises(TuyaBLEDeviceError):
        h.device._select_encryption_key(TuyaBLECode.FUN_SENDER_DEVICE_INFO)


def test_select_encryption_key_without_session_key(h: ProtocolHarness) -> None:
    """Any other request before the session key is known must fail."""
    h.device._session_key = None

    with pytest.raises(TuyaBLEDeviceError):
        h.device._select_encryption_key(TuyaBLECode.FUN_SENDER_DPS)


def test_device_info_without_local_key_raises(h: ProtocolHarness) -> None:
    """A device_info response before the local key is known must fail."""
    h.device._local_key = None
    data = device_info_data()

    with pytest.raises(TuyaBLEDeviceError):
        h.device._handle_device_info_response(data)


async def test_set_multiple_values_reverts_on_ble_write_failure(
    h: ProtocolHarness,
) -> None:
    """A BLE-level failure must revert the local values too, not just a Tuya error."""
    h.device._protocol_version = 3
    dp = h.device.get_or_create_datapoint(1, TuyaBLEDataPointType.DT_BOOL, False)

    with (
        patch.object(h.device, "send_datapoints", side_effect=BleakError()),
        pytest.raises(BleakError),
    ):
        await h.device.set_multiple_values({1: True})

    assert dp.value is False
