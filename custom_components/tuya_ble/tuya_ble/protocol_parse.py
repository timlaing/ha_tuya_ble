"""Tuya BLE protocol parse mixin: decrypting, parsing and dispatching packets."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from struct import pack, unpack
import time

from Crypto.Cipher import AES

from .const import (
    TuyaBLECode,
    TuyaBLEDataPointType,
)
from .datapoints import TuyaBLEDataPoint
from .exceptions import (
    TuyaBLEDataCRCError,
    TuyaBLEDataFormatError,
    TuyaBLEDataLengthError,
    TuyaBLEDeviceError,
)
from .protocol_send import TuyaBLEProtocolSendMixin

_LOGGER = logging.getLogger(__name__)

# The v3 datapoint envelope encodes each datapoint as three single-byte fields
# (dp_id, dp_type, value length), which bounds both to this value.
MAX_BYTE = 255


class TuyaBLEProtocolParseMixin(TuyaBLEProtocolSendMixin):
    """Mixin providing packet decryption, parsing and dispatch."""

    def _get_key(self, security_flag: int) -> bytes:
        """Return the encryption key for the given security flag."""
        if security_flag == 1:
            if self._auth_key is None:
                raise TuyaBLEDeviceError(0)
            return self._auth_key
        if security_flag == 4:
            if self._login_key is None:
                raise TuyaBLEDeviceError(0)
            return self._login_key
        if security_flag == 5:
            if self._session_key is None:
                raise TuyaBLEDeviceError(0)
            return self._session_key
        raise TuyaBLEDataFormatError()

    def _parse_timestamp(self, data: bytes, start_pos: int) -> tuple[float, int]:
        """Decode a timestamp field (type 0: ms-string, type 1: 4-byte big-endian)."""
        timestamp: float
        pos = start_pos
        if pos >= len(data):
            raise TuyaBLEDataLengthError()
        time_type = data[pos]
        pos += 1
        end_pos = pos
        match time_type:
            case 0:
                end_pos += 13
                if end_pos > len(data):
                    raise TuyaBLEDataLengthError()
                timestamp = int(data[pos:end_pos].decode()) / 1000
            case 1:
                end_pos += 4
                if end_pos > len(data):
                    raise TuyaBLEDataLengthError()
                timestamp = int.from_bytes(data[pos:end_pos], "big") * 1.0
            case _:
                raise TuyaBLEDataFormatError()

        _LOGGER.debug(
            "%s: Received timestamp: %s",
            self.address,
            time.ctime(timestamp),
        )
        return (timestamp, end_pos)

    def _parse_datapoints_v3(
        self, timestamp: float, flags: int, data: bytes, start_pos: int
    ) -> None:
        """Parse a v3 datapoint payload and fire callbacks."""
        datapoints: list[TuyaBLEDataPoint] = []

        pos = start_pos
        while len(data) - pos >= 4:
            dp_id: int = data[pos]
            pos += 1
            raw_type: int = data[pos]
            if raw_type > TuyaBLEDataPointType.DT_BITMAP.value:
                raise TuyaBLEDataFormatError()
            dp_type: TuyaBLEDataPointType = TuyaBLEDataPointType(raw_type)
            pos += 1
            data_len: int = data[pos]
            pos += 1
            next_pos = pos + data_len
            if next_pos > len(data):
                raise TuyaBLEDataLengthError()
            raw_value = data[pos:next_pos]
            value: bytes | bool | int | str
            match dp_type:
                case TuyaBLEDataPointType.DT_RAW | TuyaBLEDataPointType.DT_BITMAP:
                    value = raw_value
                case TuyaBLEDataPointType.DT_BOOL:
                    value = int.from_bytes(raw_value, "big") != 0
                case TuyaBLEDataPointType.DT_VALUE | TuyaBLEDataPointType.DT_ENUM:
                    value = int.from_bytes(raw_value, "big", signed=True)
                case TuyaBLEDataPointType.DT_STRING:
                    value = raw_value.decode()

            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "%s: Received DP id=%s type=%s flags=0x%02x raw=%s decoded=%s",
                    self.address,
                    dp_id,
                    dp_type.name,
                    flags,
                    raw_value.hex(),
                    value,
                )
            datapoints.append(
                self._datapoints.update_from_device(
                    dp_id, timestamp, flags, dp_type, value, raw_value
                )
            )
            pos = next_pos

        self._fire_callbacks(datapoints)

    def _handle_command_or_response(
        self, seq_num: int, response_to: int, code: TuyaBLECode, data: bytes
    ) -> None:
        """Dispatch an incoming command or response to its handler."""
        result = self._dispatch_command(code, seq_num, data)
        self._resolve_expected_response(response_to, result)

    def _dispatch_command(self, code: TuyaBLECode, seq_num: int, data: bytes) -> int:
        """Dispatch an incoming command to its handler. Returns result code."""
        result = 0
        match code:
            case TuyaBLECode.FUN_SENDER_DEVICE_INFO:
                self._handle_device_info_response(data)
            case TuyaBLECode.FUN_SENDER_PAIR:
                result = self._handle_pair_response(data)
            case TuyaBLECode.FUN_SENDER_DEVICE_STATUS:
                result = self._handle_device_status_response(data)
            case TuyaBLECode.FUN_RECEIVE_TIME1_REQ:
                self._handle_time1_request(seq_num)
            case TuyaBLECode.FUN_RECEIVE_TIME2_REQ:
                self._handle_time2_request(seq_num)
            case TuyaBLECode.FUN_RECEIVE_DP:
                self._handle_receive_dp(seq_num, data)
            case TuyaBLECode.FUN_RECEIVE_SIGN_DP:
                self._handle_receive_sign_dp(seq_num, data)
            case TuyaBLECode.FUN_RECEIVE_TIME_DP:
                self._handle_receive_time_dp(seq_num, data)
            case TuyaBLECode.FUN_RECEIVE_SIGN_TIME_DP:
                self._handle_receive_sign_time_dp(seq_num, data)
        return result

    def _handle_device_info_response(self, data: bytes) -> None:
        """Handle FUN_SENDER_DEVICE_INFO response: extract version and session key."""
        if len(data) < 46:
            raise TuyaBLEDataLengthError()

        self._device_version = f"{data[0]}.{data[1]}"
        self._protocol_version_str = f"{data[2]}.{data[3]}"
        self._hardware_version = f"{data[12]}.{data[13]}"

        self._protocol_version = data[2]
        self._flags = data[4]
        self._is_bound = data[5] != 0

        srand = data[6:12]
        if self._local_key is None:
            raise TuyaBLEDeviceError(0)
        self._session_key = hashlib.md5(self._local_key + srand).digest()  # noqa: S4790
        self._auth_key = data[14:46]

    def _handle_pair_response(self, data: bytes) -> int:
        """Handle FUN_SENDER_PAIR response: parse pairing status."""
        if len(data) != 1:
            raise TuyaBLEDataLengthError()
        result = data[0]
        if result == 2:
            _LOGGER.debug(
                "%s: Device is already paired",
                self.address,
            )
            result = 0
        self._is_paired = result == 0
        return result

    def _handle_device_status_response(self, data: bytes) -> int:
        """Handle FUN_SENDER_DEVICE_STATUS response: parse status code."""
        if len(data) != 1:
            raise TuyaBLEDataLengthError()
        return data[0]

    def _handle_time1_request(self, seq_num: int) -> None:
        """Handle FUN_RECEIVE_TIME1_REQ: respond with millisecond timestamp."""
        timestamp = int(time.time_ns() / 1000000)
        timezone = -int(time.timezone / 36)
        data = str(timestamp).encode() + pack(">h", timezone)
        self._track_send_response_task(
            asyncio.create_task(
                self._send_response(TuyaBLECode.FUN_RECEIVE_TIME1_REQ, data, seq_num)
            )
        )

    def _handle_time2_request(self, seq_num: int) -> None:
        """Handle FUN_RECEIVE_TIME2_REQ: respond with structured time fields."""
        time_str: time.struct_time = time.localtime()
        timezone = -int(time.timezone / 36)
        data = pack(
            ">BBBBBBBh",
            time_str.tm_year % 100,
            time_str.tm_mon,
            time_str.tm_mday,
            time_str.tm_hour,
            time_str.tm_min,
            time_str.tm_sec,
            time_str.tm_wday,
            timezone,
        )
        self._track_send_response_task(
            asyncio.create_task(
                self._send_response(TuyaBLECode.FUN_RECEIVE_TIME2_REQ, data, seq_num)
            )
        )

    def _handle_receive_dp(self, seq_num: int, data: bytes) -> None:
        """Handle FUN_RECEIVE_DP: parse datapoints and send ack."""
        self._parse_datapoints_v3(time.time(), 0, data, 0)
        self._track_send_response_task(
            asyncio.create_task(
                self._send_response(TuyaBLECode.FUN_RECEIVE_DP, bytes(0), seq_num)
            )
        )

    def _handle_receive_sign_dp(self, seq_num: int, data: bytes) -> None:
        """Handle FUN_RECEIVE_SIGN_DP: parse signed datapoints and send ack."""
        if len(data) < 3:
            raise TuyaBLEDataLengthError()
        dp_seq_num = int.from_bytes(data[:2], "big")
        flags = data[2]
        self._parse_datapoints_v3(time.time(), flags, data, 3)
        response = pack(">HBB", dp_seq_num, flags, 0)
        self._track_send_response_task(
            asyncio.create_task(
                self._send_response(TuyaBLECode.FUN_RECEIVE_SIGN_DP, response, seq_num)
            )
        )

    def _handle_receive_time_dp(self, seq_num: int, data: bytes) -> None:
        """Handle FUN_RECEIVE_TIME_DP: parse timestamped datapoints and send ack."""
        ts: float
        dp_pos: int
        ts, dp_pos = self._parse_timestamp(data, 0)
        self._parse_datapoints_v3(ts, 0, data, dp_pos)
        self._track_send_response_task(
            asyncio.create_task(
                self._send_response(TuyaBLECode.FUN_RECEIVE_TIME_DP, bytes(0), seq_num)
            )
        )

    def _handle_receive_sign_time_dp(self, seq_num: int, data: bytes) -> None:
        """Handle FUN_RECEIVE_SIGN_TIME_DP: parse signed timestamped datapoints."""
        if len(data) < 3:
            raise TuyaBLEDataLengthError()
        dp_seq_num = int.from_bytes(data[:2], "big")
        flags = data[2]
        _ts, dp_pos = self._parse_timestamp(data, 3)
        self._parse_datapoints_v3(time.time(), flags, data, dp_pos)
        response = pack(">HBB", dp_seq_num, flags, 0)
        self._track_send_response_task(
            asyncio.create_task(
                self._send_response(
                    TuyaBLECode.FUN_RECEIVE_SIGN_TIME_DP, response, seq_num
                )
            )
        )

    def _resolve_expected_response(self, response_to: int, result: int) -> None:
        """Resolve or reject a pending response future."""
        if response_to == 0:
            return
        future = self._input_expected_responses.pop(response_to, None)
        if not future:
            return
        _LOGGER.debug(
            "%s: Received expected response to #%s, result: %s",
            self.address,
            response_to,
            result,
        )
        if result == 0:
            future.set_result(result)
        else:
            future.set_exception(TuyaBLEDeviceError(result))

    def _clean_input(self) -> None:
        """Reset the input buffer and expected packet counter."""
        self._input_buffer = None
        self._input_expected_packet_num = 0
        self._input_expected_length = 0
        self._input_reassembly_deadline = 0.0

    def _parse_input(self) -> None:
        """Decrypt and process the buffered input packet."""
        raw = self._decrypt_input()
        result = self._validate_and_parse_packet(raw)
        if result is None:
            return
        seq_num, response_to, code, data = result
        if response_to != 0:
            _LOGGER.debug(
                "%s: Received: #%s %s, response to #%s",
                self.address,
                seq_num,
                code.name,
                response_to,
            )
        else:
            _LOGGER.debug(
                "%s: Received: #%s %s",
                self.address,
                seq_num,
                code.name,
            )
        self._handle_command_or_response(seq_num, response_to, code, data)

    def _decrypt_input(self) -> bytes:
        """Decrypt the input buffer and return raw bytes."""
        if not self._input_buffer:
            raise TuyaBLEDataFormatError()
        security_flag = self._input_buffer[0]
        key = self._get_key(security_flag)
        iv = self._input_buffer[1:17]
        encrypted = self._input_buffer[17:]
        self._clean_input()
        cipher = AES.new(key, AES.MODE_CBC, iv)  # noqa: S5542
        raw: bytes = cipher.decrypt(encrypted)
        return raw

    def _validate_and_parse_packet(
        self, raw: bytes
    ) -> tuple[int, int, TuyaBLECode, bytes] | None:
        """Validate a decrypted packet: check length, CRC, and extract fields."""
        if len(raw) < 12:
            raise TuyaBLEDataLengthError()
        seq_num, response_to, _code, length = unpack(">IIHH", raw[:12])

        data_end_pos = length + 12
        if len(raw) < data_end_pos + 2:
            raise TuyaBLEDataLengthError()
        # The CRC is the only integrity check this protocol has: the security
        # flag and IV sit outside the payload and are not covered by it.
        calc_crc = self._calc_crc16(raw[:data_end_pos])
        (data_crc,) = unpack(
            ">H",
            raw[data_end_pos : data_end_pos + 2],  # fmt: skip
        )
        if calc_crc != data_crc:
            raise TuyaBLEDataCRCError()
        data = raw[12:data_end_pos]

        try:
            code = TuyaBLECode(_code)
        except ValueError:
            _LOGGER.debug(
                "%s: Received unknown message: #%s %x, response to #%s, data %s",
                self.address,
                seq_num,
                _code,
                response_to,
                data.hex(),
            )
            return None
        return seq_num, response_to, code, data
