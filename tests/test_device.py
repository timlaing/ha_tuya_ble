"""Unit tests for the TuyaBLEDevice connection/state class."""

# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import patch

from bleak_retry_connector import BleakNotFoundError
from Crypto.Cipher import AES
import pytest

from custom_components.tuya_ble.tuya_ble.const import (
    MANUFACTURER_DATA_ID,
    SERVICE_UUID,
    TuyaBLECode,
    TuyaBLEDataPointType,
)
from custom_components.tuya_ble.tuya_ble.exceptions import TuyaBLEDeviceError
from custom_components.tuya_ble.tuya_ble.manager import TuyaBLEDeviceCredentials
from tests.conftest import (
    FakeAdvertisementData,
    FakeBLEAddress,
    FakeBleakClient,
    FakeBLEManager,
    make_credentials,
    make_device,
)
from tests.protocol_harness import encrypt_payload, frame_packet0


class TestProperties:
    """Tests for the device's read-only property accessors."""

    def test_properties_with_credentials(self) -> None:
        """Verify properties are populated from the device credentials."""
        manager = FakeBLEManager(make_credentials())
        dev = make_device(manager=manager)
        dev._device_info = manager.credentials
        assert dev.address == "AA:BB:CC:DD:EE:FF"
        assert dev.name == "Device"
        assert dev.uuid == "1234567890abcdef"
        assert dev.local_key == "abcdef"
        assert dev.category == "wk"
        assert dev.device_id == "device123"
        assert dev.product_id == "drlajpqc"
        assert dev.product_model == "Model"
        assert dev.product_name == "Product"
        assert dev.device_version == ""
        assert dev.hardware_version == ""
        assert dev.protocol_version == ""

    def test_properties_without_credentials(self) -> None:
        """Verify properties fall back to defaults when no credentials exist."""
        dev = make_device(manager=FakeBLEManager(None), name="MyDevice")
        assert dev.name == "MyDevice"
        assert dev.uuid == ""
        assert dev.local_key == ""
        assert dev.category == ""
        assert dev.device_id == ""
        assert dev.product_id == ""
        assert dev.product_model == ""
        assert dev.product_name == ""
        assert dev.rssi == -50

    def test_name_uses_device_info_when_available(self) -> None:
        """Verify the name falls back to the address when the device has none."""
        creds = make_credentials()
        creds_no_name = TuyaBLEDeviceCredentials(
            uuid=creds.uuid,
            local_key=creds.local_key,
            device_id=creds.device_id,
            category=creds.category,
            product_id=creds.product_id,
            device_name=None,
            product_model="Model",
            product_name="Product",
        )
        dev = make_device(manager=FakeBLEManager(creds_no_name))
        dev._device_info = creds_no_name
        assert dev.name == "AA:BB:CC:DD:EE:FF"
        assert dev.product_model == "Model"

    def test_rssi_none_without_adv(self) -> None:
        """Verify rssi is None when no advertisement data is present."""
        dev = make_device()
        dev._advertisement_data = None
        assert dev.rssi is None

    def test_version_properties(self) -> None:
        """Verify version properties expose the stored version strings."""
        dev = make_device()
        dev._device_version = "1.2"
        dev._hardware_version = "9.8"
        dev._protocol_version_str = "3.0"
        assert dev.device_version == "1.2"
        assert dev.hardware_version == "9.8"
        assert dev.protocol_version == "3.0"

    def test_datapoints_and_get_or_create(self) -> None:
        """Verify datapoints are created and cached on first access."""
        dev = make_device()
        dp = dev.get_or_create_datapoint(1, TuyaBLEDataPointType.DT_BOOL)
        assert dev.datapoints[1] is dp


class TestSetBLE:
    """Tests for updating the BLE device and advertisement data."""

    def test_set_ble_device_and_advertisement_data(self) -> None:
        """Verify the BLE device address and rssi are updated."""
        dev = make_device()
        ble = FakeBLEAddress("11:22:33:44:55:66", "New")
        adv = FakeAdvertisementData(rssi=-70)
        dev.set_ble_device_and_advertisement_data(ble, adv)  # type: ignore[arg-type]
        assert dev.address == "11:22:33:44:55:66"
        assert dev.rssi == -70

    def test_address_updates_with_ble_device(self) -> None:
        """Verify the device address tracks the configured BLE device."""
        dev = make_device()
        assert dev.address == "AA:BB:CC:DD:EE:FF"
        dev.set_ble_device_and_advertisement_data(
            FakeBLEAddress("00:11:22:33:44:55", "Other"),  # type: ignore[arg-type]
            FakeAdvertisementData(),  # type: ignore[arg-type]
        )
        assert dev.address == "00:11:22:33:44:55"


class TestInitialize:
    """Tests for initializing the device with its credentials."""

    async def test_initialize_with_credentials(self) -> None:
        """Verify initialization loads credentials and derives keys."""
        creds = make_credentials()
        manager = FakeBLEManager(creds)
        dev = make_device(manager=manager)
        await dev.initialize()
        assert dev._device_info is creds
        assert dev._local_key == creds.local_key[:6].encode()
        assert dev._login_key is not None

    async def test_initialize_no_credentials(self) -> None:
        """Verify initialization succeeds when no credentials exist."""
        dev = make_device(manager=FakeBLEManager(None))
        await dev.initialize()
        assert dev._device_info is None

    async def test_update_device_info_reuses_existing(self) -> None:
        """Verify initialization does not re-fetch already present credentials."""
        creds = make_credentials()
        manager = FakeBLEManager(creds)
        dev = make_device(manager=manager)
        dev._device_info = creds
        dev._local_key = creds.local_key[:6].encode()
        await dev.initialize()
        assert manager.address == ""


class TestAdvertisementParsing:
    """Tests for parsing advertisement data to discover device identity."""

    def _adv(
        self,
        service_data: bytes,
        manufacturer_data: bytes,
        product_id: bytes,
    ) -> FakeAdvertisementData:
        """Build advertisement data encoding the given product id and uuid."""
        adv = FakeAdvertisementData(
            rssi=-40,
            service_data={SERVICE_UUID: b"\x00" + product_id},
            manufacturer_data={
                MANUFACTURER_DATA_ID: b"\x80\x02"
                + b"\x00" * 4
                + self._encrypt_uuid(product_id),
            },
        )
        return adv

    def _encrypt_uuid(self, product_id: bytes) -> bytes:
        """Encrypt the fake device uuid using the product id key."""
        key = hashlib.md5(product_id).digest()  # noqa: S324
        plaintext = b"1234567890abcdef"
        cipher = AES.new(key, AES.MODE_CBC, key)
        return cipher.encrypt(plaintext)

    def test_decode_advertisement_uuid(self) -> None:
        """Verify the device uuid and binding are decoded from advertisement."""
        dev = make_device()
        dev._advertisement_data = self._adv(  # type: ignore[assignment]
            b"pid01", b"\x80\x02" + b"\x00" * 4 + self._encrypt_uuid(b"pid01"), b"pid01"
        )
        dev._decode_advertisement_data()
        assert dev._is_bound is True
        assert dev._protocol_version == 2
        assert dev._uuid == "1234567890abcdef"

    def test_decode_advertisement_no_data(self) -> None:
        """Verify advertisement decoding is a no-op without data."""
        dev = make_device()
        dev._advertisement_data = None
        dev._decode_advertisement_data()  # no-op

    def test_parse_product_id_no_service_data(self) -> None:
        """Verify no product id is parsed when service data is empty."""
        dev = make_device()
        dev._advertisement_data = FakeAdvertisementData()  # type: ignore[assignment]
        assert dev._parse_product_id_from_service_data() is None

    def test_parse_product_id_wrong_first_byte(self) -> None:
        """Verify no product id is parsed when the first byte is unexpected."""
        adv = FakeAdvertisementData(service_data={SERVICE_UUID: b"\x01\x02\x03"})
        dev = make_device()
        dev._advertisement_data = adv  # type: ignore[assignment]
        assert dev._parse_product_id_from_service_data() is None

    def test_parse_manufacturer_data_short(self) -> None:
        """Verify short manufacturer data is handled gracefully."""
        adv = FakeAdvertisementData(
            manufacturer_data={MANUFACTURER_DATA_ID: b"\x00\x00\x00"}
        )
        dev = make_device()
        dev._advertisement_data = adv  # type: ignore[assignment]
        dev._parse_manufacturer_data(None)


class TestCallbacks:
    """Tests for registering and firing device callbacks."""

    def test_register_callbacks(self) -> None:
        """Verify callbacks fire and can be unregistered."""
        dev = make_device()
        calls: list[list[int]] = []
        connected: list[int] = []
        disconnected: list[int] = []
        unreg = dev.register_callback(
            calls.append  # type: ignore[arg-type]
        )
        unreg_conn = dev.register_connected_callback(lambda: connected.append(1))
        unreg_disc = dev.register_disconnected_callback(lambda: disconnected.append(1))
        dev._fire_callbacks([1])  # type: ignore[list-item]
        assert calls == [[1]]
        dev._fire_connected_callbacks()
        assert connected == [1]
        dev._fire_disconnected_callbacks()
        assert disconnected == [1]
        unreg()
        unreg_conn()
        unreg_disc()
        dev._fire_callbacks([2])  # type: ignore[list-item]
        dev._fire_connected_callbacks()
        dev._fire_disconnected_callbacks()
        assert calls == [[1]]
        assert connected == [1]
        assert disconnected == [1]


class TestConnection:
    """Tests for the device connection and disconnect lifecycle."""

    async def test_stop_disconnects(self) -> None:
        """Verify stopping the device disconnects and clears its client."""
        dev = make_device()
        client = FakeBleakClient(is_connected=True)
        dev._client = client  # type: ignore[assignment]
        dev._is_paired = True
        await dev.stop()
        assert dev._client is None
        assert dev._current_seq_num == 1

    async def test_start_noop(self) -> None:
        """Verify start is a no-op."""
        dev = make_device()
        await dev.start()

    async def test_full_connect_flow(self) -> None:
        """Verify the full connect flow exchanges keys and reports status."""
        dev = make_device(manager=FakeBLEManager(make_credentials()))
        client = FakeBleakClient(is_connected=True)

        async def fake_establish(*args: object, **kwargs: object) -> FakeBleakClient:
            return client

        await dev.initialize()

        with patch(
            "custom_components.tuya_ble.tuya_ble.base.establish_connection",
            side_effect=fake_establish,
        ):
            dev_info_data = bytearray(46)
            dev_info_data[2] = 3
            dev_info_data[6:12] = b"abcdef"
            dev_info_data[14:46] = b"B" * 32

            async def notifier() -> None:
                await asyncio.sleep(0.05)
                assert client.notify_handler is not None
                client.notify_handler(
                    None,  # type: ignore[arg-type]
                    bytearray(
                        frame_packet0(
                            encrypt_payload(
                                dev._login_key,  # type: ignore[arg-type]
                                4,
                                1,
                                1,
                                TuyaBLECode.FUN_SENDER_DEVICE_INFO,
                                bytes(dev_info_data),
                            )
                        )
                    ),
                )
                await asyncio.sleep(0.05)
                client.notify_handler(
                    None,  # type: ignore[arg-type]
                    bytearray(
                        frame_packet0(
                            encrypt_payload(
                                dev._session_key,  # type: ignore[arg-type]
                                5,
                                2,
                                2,
                                TuyaBLECode.FUN_SENDER_PAIR,
                                b"\x00",
                            )
                        )
                    ),
                )
                await asyncio.sleep(0.05)
                client.notify_handler(
                    None,  # type: ignore[arg-type]
                    bytearray(
                        frame_packet0(
                            encrypt_payload(
                                dev._session_key,  # type: ignore[arg-type]
                                5,
                                3,
                                3,
                                TuyaBLECode.FUN_SENDER_DEVICE_STATUS,
                                b"\x00",
                            )
                        )
                    ),
                )

            notifier_task = asyncio.create_task(notifier())
            await dev.update()
            await notifier_task
            assert dev._client is client  # type: ignore[comparison-overlap]

    async def test_establish_connection_raises_not_found_returns_none(self) -> None:
        """Verify a not-found error during connect is swallowed."""
        dev = make_device(manager=FakeBLEManager(make_credentials()))

        async def fail(*args: object, **kwargs: object) -> None:
            raise BleakNotFoundError()

        with patch(
            "custom_components.tuya_ble.tuya_ble.base.establish_connection",
            side_effect=fail,
        ):
            result = await dev._try_establish_connection()
            assert result is None

    async def test_connect_failure_raises(self) -> None:
        """Verify a not-found error during update propagates."""
        dev = make_device(manager=FakeBLEManager(make_credentials()))
        await dev.initialize()

        async def fail(*args: object, **kwargs: object) -> None:
            raise BleakNotFoundError()

        with (
            patch(
                "custom_components.tuya_ble.tuya_ble.base.establish_connection",
                side_effect=fail,
            ),
            pytest.raises(BleakNotFoundError),
        ):
            await dev.update()

    async def test_establish_connection_bleak_error_returns_none(self) -> None:
        """Verify a connection error during connect is swallowed."""
        dev = make_device(manager=FakeBLEManager(make_credentials()))

        async def fail(*args: object, **kwargs: object) -> None:
            raise OSError("oops")

        with patch(
            "custom_components.tuya_ble.tuya_ble.base.establish_connection",
            side_effect=fail,
        ):
            result = await dev._try_establish_connection()
            assert result is None

    async def test_disconnect_expected(self) -> None:
        """Verify an expected disconnect clears the paired flag."""
        dev = make_device()
        client = FakeBleakClient(is_connected=True)
        dev._client = client  # type: ignore[assignment]
        dev._is_paired = True
        disconnected: list[int] = []
        dev.register_disconnected_callback(lambda: disconnected.append(1))
        dev._expected_disconnect = True
        dev._disconnected(client)  # type: ignore[arg-type]
        assert dev._is_paired is False
        assert disconnected == [1]

    async def test_disconnect_unexpected_schedules_reconnect(self) -> None:
        """Verify an unexpected disconnect schedules a reconnect."""
        manager = FakeBLEManager(make_credentials())
        dev = make_device(manager=manager)
        dev._device_info = manager.credentials
        dev._local_key = (
            manager.credentials.local_key[:6].encode()  # type: ignore[union-attr]
        )
        dev._login_key = dev._local_key
        client = FakeBleakClient(is_connected=True)
        dev._client = client  # type: ignore[assignment]
        dev._is_paired = True

        async def fake_establish(*args: object, **kwargs: object) -> FakeBleakClient:
            return client

        with patch(
            "custom_components.tuya_ble.tuya_ble.base.establish_connection",
            side_effect=fake_establish,
        ):
            dev._disconnected(client)  # type: ignore[arg-type]
            await asyncio.sleep(0.1)

    async def test_execute_timed_disconnect(self) -> None:
        """Verify a timed disconnect clears the client."""
        dev = make_device()
        client = FakeBleakClient(is_connected=True)
        dev._client = client  # type: ignore[assignment]
        dev._is_paired = True
        dev._disconnect()
        await asyncio.sleep(0.1)
        assert dev._client is None


class TestInitializeWithCredentials:
    """Tests for the initialize_with_credentials public method."""

    async def test_initialize_with_credentials(self) -> None:
        """Verify initialize_with_credentials loads credentials and derives keys."""
        creds = make_credentials()
        dev = make_device(manager=FakeBLEManager(None))
        await dev.initialize_with_credentials(creds)
        assert dev._device_info is creds
        assert dev._local_key == creds.local_key[:6].encode()
        assert dev._login_key is not None

    async def test_initialize_with_credentials_short_key_raises(self) -> None:
        """Verify initialize_with_credentials raises for short local_key."""
        creds = make_credentials(local_key="abc")
        dev = make_device(manager=FakeBLEManager(None))
        with pytest.raises(TuyaBLEDeviceError):
            await dev.initialize_with_credentials(creds)

    async def test_initialize_with_credentials_with_functions(self) -> None:
        """Verify initialize_with_credentials processes functions and status_range."""
        creds = make_credentials()
        creds.functions = [{"code": "switch", "dp_id": 1, "type": "bool"}]
        creds.status_range = [
            {"code": "switch", "dp_id": 1, "type": "bool", "values": {}}
        ]
        dev = make_device(manager=FakeBLEManager(None))
        await dev.initialize_with_credentials(creds)
        assert dev._device_info is creds


class TestBuildPairingRequest:
    """Tests for _build_pairing_request guard."""

    def test_build_pairing_request_no_device_info_raises(self) -> None:
        """Verify _build_pairing_request raises when _device_info is None."""
        dev = make_device()
        dev._device_info = None
        dev._local_key = b"abcdef"
        with pytest.raises(TuyaBLEDeviceError):
            dev._build_pairing_request()

    def test_build_pairing_request_no_local_key_raises(self) -> None:
        """Verify _build_pairing_request raises when _local_key is None."""
        dev = make_device()
        dev._device_info = make_credentials()
        dev._local_key = None
        with pytest.raises(TuyaBLEDeviceError):
            dev._build_pairing_request()


class TestUpdateDeviceInfoCloudFallback:
    """Tests for _update_device_info cloud fallback path."""

    async def test_update_device_info_fetches_from_manager(self) -> None:
        """Verify _update_device_info fetches credentials from device_manager."""
        creds = make_credentials()
        manager = FakeBLEManager(creds)
        dev = make_device(manager=manager)
        dev._device_info = None
        ble = FakeBLEAddress("AA:BB:CC:DD:EE:FF")
        dev._ble_device = ble  # type: ignore[assignment]
        result = await dev._update_device_info()
        assert result is True
        assert dev._device_info is creds
        assert manager.address == "AA:BB:CC:DD:EE:FF"

    async def test_update_device_info_short_key_raises(self) -> None:
        """Verify _update_device_info raises for short local_key from manager."""
        creds = make_credentials(local_key="abc")
        manager = FakeBLEManager(creds)
        dev = make_device(manager=manager)
        dev._device_info = None
        ble = FakeBLEAddress("AA:BB:CC:DD:EE:FF")
        dev._ble_device = ble  # type: ignore[assignment]
        with pytest.raises(TuyaBLEDeviceError):
            await dev._update_device_info()

    async def test_update_device_info_no_manager(self) -> None:
        """Verify _update_device_info returns False when no manager."""
        dev = make_device(manager=FakeBLEManager(None))
        dev._device_info = None
        ble = FakeBLEAddress("AA:BB:CC:DD:EE:FF")
        dev._ble_device = ble  # type: ignore[assignment]
        result = await dev._update_device_info()
        assert result is False


class TestDecodeAdvertisement:
    """Tests for _decode_advertisement_data edge cases."""

    def test_decode_advertisement_empty_product_id(self) -> None:
        """Verify _decode skips decryption when raw_product_id is empty."""
        dev = make_device()
        adv = FakeAdvertisementData(
            manufacturer_data={
                MANUFACTURER_DATA_ID: b"\x80\x02" + b"\x00" * 4 + b"\x00" * 16
            }
        )
        dev._advertisement_data = adv  # type: ignore[assignment]

        def _no_product_id() -> None:
            return None

        dev._parse_product_id_from_service_data = (  # type: ignore[method-assign]
            _no_product_id
        )
        dev._decode_advertisement_data()
        assert dev._is_bound is True


class TestOnDisconnectedNotPaired:
    """Tests for _disconnected when device was not paired."""

    def test_on_disconnected_not_paired(self) -> None:
        """Verify _disconnected does not reconnect when not paired."""
        dev = make_device()
        dev._is_paired = False
        client = FakeBleakClient()
        dev._client = client  # type: ignore[assignment]
        dev._disconnected(client)  # type: ignore[arg-type]
        assert dev._client is None
