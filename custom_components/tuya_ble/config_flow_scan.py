"""Active scanning for a Tuya BLE identity during configuration."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.core import HomeAssistant

from .tuya_ble import (
    MANUFACTURER_DATA_ID,
    SERVICE_UUID,
    SERVICE_UUID_V4,
    TuyaBLEAdvertisementInfo,
    decode_classic_record,
    decode_tuya_ble_advertisement,
)

ACTIVE_SCAN_TIMEOUT = 60

# Scan messages stay on the documented config-flow logger.
_LOGGER = logging.getLogger(f"{__package__}.config_flow")


def has_complete_identity(service_info: BluetoothServiceInfoBleak) -> bool:
    """Return True when the scan response carries a usable Tuya identity."""
    manufacturer_data = service_info.manufacturer_data
    if service_info.service_data is None or manufacturer_data is None:
        return False
    manufacturer_data_raw = manufacturer_data.get(MANUFACTURER_DATA_ID)
    if manufacturer_data_raw is None:
        return False
    if SERVICE_UUID_V4 in service_info.service_data:
        return (
            len(service_info.service_data[SERVICE_UUID_V4]) >= 4
            and len(manufacturer_data_raw) == 20
            and decode_tuya_ble_advertisement(service_info.advertisement) is not None
        )
    # A merged snapshot can pair a stale A201 record with a bound identity
    # (#155): keep scanning until the pair decodes consistently.
    return (
        SERVICE_UUID in service_info.service_data
        and len(manufacturer_data_raw) > 6
        and (len(manufacturer_data_raw) - 6) % 16 == 0
        and decode_tuya_ble_advertisement(service_info.advertisement) is not None
    )


async def async_scan_device(
    hass: HomeAssistant, address: str
) -> BluetoothServiceInfoBleak | None:
    """Actively scan until Tuya service and manufacturer data are available.

    On timeout, fall back to the latest well-formed classic record so a bound
    device whose identity never decodes can still be verified by probe.
    """
    _LOGGER.debug("%s: active scan started, timeout %ss", address, ACTIVE_SCAN_TIMEOUT)
    classic: BluetoothServiceInfoBleak | None = None

    def is_complete(service_info: BluetoothServiceInfoBleak) -> bool:
        """Retain the latest classic record, preferring a readable product ID."""
        nonlocal classic
        if has_complete_identity(service_info):
            return True
        record = decode_classic_record(service_info.advertisement)
        if record is not None and (
            record.product_id is not None or not _has_product_id(classic)
        ):
            classic = service_info
        return False

    try:
        return await bluetooth.async_process_advertisements(
            hass,
            is_complete,
            {"address": address, "connectable": True},
            bluetooth.BluetoothScanningMode.ACTIVE,
            ACTIVE_SCAN_TIMEOUT,
        )
    except TimeoutError:
        if classic is not None:
            _LOGGER.debug(
                "%s: active scan timed out, using the last classic record", address
            )
            return classic
        _LOGGER.debug("%s: active scan timed out", address)
        return None


def _has_product_id(service_info: BluetoothServiceInfoBleak | None) -> bool:
    """Return True when a retained classic record carries a readable product ID."""
    if service_info is None:
        return False
    record = decode_classic_record(service_info.advertisement)
    return record is not None and record.product_id is not None


def bound_classic_advertisement(
    discovery_info: BluetoothServiceInfoBleak,
) -> TuyaBLEAdvertisementInfo | None:
    """Describe a bound classic record whose identity did not decrypt.

    The empty UUID means no identity was decoded: only an authenticated probe
    of cloud candidates can associate such a device with a cloud record.
    """
    record = decode_classic_record(discovery_info.advertisement)
    if record is None or not record.is_bound:
        return None
    return TuyaBLEAdvertisementInfo(
        product_id=record.product_id,
        uuid="",
        is_bound=True,
        protocol_version=record.protocol_version,
    )
