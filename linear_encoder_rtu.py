#!/usr/bin/env python3
"""Отказоустойчивый фоновый опрос линейного энкодера Modbus RTU."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

import serial


SLAVE_ADDRESS = 0
READ_FUNCTION = 0x03
POSITION_START_REGISTER = 0
POSITION_REGISTER_COUNT = 2


def modbus_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def with_crc(payload: bytes) -> bytes:
    crc = modbus_crc16(payload)
    return payload + bytes((crc & 0xFF, (crc >> 8) & 0xFF))


def build_position_request() -> bytes:
    return with_crc(
        bytes(
            (
                SLAVE_ADDRESS,
                READ_FUNCTION,
                (POSITION_START_REGISTER >> 8) & 0xFF,
                POSITION_START_REGISTER & 0xFF,
                (POSITION_REGISTER_COUNT >> 8) & 0xFF,
                POSITION_REGISTER_COUNT & 0xFF,
            )
        )
    )


class EncoderProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class EncoderSample:
    timestamp: float
    low_word: int
    high_word: int
    raw_value: int
    position_mm: float
    frequency_hz: float


class LinearEncoderRtuReader:
    """Постоянно опрашивает энкодер, не влияя ошибками на основную программу."""

    def __init__(
        self,
        port: str = "/dev/ttyUSB1",
        baudrate: int = 9600,
        response_timeout: float = 0.12,
        offline_error_threshold: int = 3,
        reconnect_error_threshold: int = 10,
        reconnect_delay: float = 1.0,
        on_sample: Optional[Callable[[EncoderSample], None]] = None,
        on_connection_change: Optional[Callable[[bool, Optional[float]], None]] = None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.response_timeout = response_timeout
        self.offline_error_threshold = offline_error_threshold
        self.reconnect_error_threshold = reconnect_error_threshold
        self.reconnect_delay = reconnect_delay
        self.on_sample = on_sample
        self.on_connection_change = on_connection_change

        self.serial_port: Optional[serial.Serial] = None
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._online: Optional[bool] = None
        self._last_sample: Optional[EncoderSample] = None
        self._poll_timestamps: deque[float] = deque(maxlen=500)
        self._last_bus_activity = 0.0
        self.total_requests = 0
        self.successful_requests = 0
        self.error_count = 0
        self.consecutive_errors = 0

    @property
    def inter_frame_delay(self) -> float:
        # 8N1 = 10 бит/символ; RTU требует не менее 3.5 символа тишины.
        return 3.5 * 10.0 / self.baudrate

    @property
    def online(self) -> bool:
        with self._state_lock:
            return self._online is True

    @property
    def last_sample(self) -> Optional[EncoderSample]:
        with self._state_lock:
            return self._last_sample

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="linear-encoder-rtu", daemon=True)
        self.thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=timeout)
        self._close_port()

    def _notify_connection(self, online: bool) -> None:
        callback = self.on_connection_change
        if callback is None:
            return
        sample = self.last_sample
        try:
            callback(online, sample.position_mm if sample else None)
        except Exception:
            # Ошибка потребителя не должна остановить RTU-поток.
            pass

    def _set_online(self, online: bool) -> None:
        changed = False
        with self._state_lock:
            if self._online is not online:
                self._online = online
                changed = True
        if changed:
            self._notify_connection(online)

    def _publish_sample(self, sample: EncoderSample) -> None:
        with self._state_lock:
            self._last_sample = sample
        if self.on_sample:
            try:
                self.on_sample(sample)
            except Exception:
                pass
        # Статус online публикуется после записи первого корректного положения.
        self._set_online(True)

    def _open_port(self) -> None:
        self.serial_port = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.01,
            write_timeout=0.2,
            rtscts=False,
            dsrdtr=False,
            xonxoff=False,
        )
        self.serial_port.reset_input_buffer()
        self.serial_port.reset_output_buffer()
        self._last_bus_activity = time.monotonic()
        self.consecutive_errors = 0

    def _close_port(self) -> None:
        port = self.serial_port
        self.serial_port = None
        if port is not None:
            try:
                port.close()
            except Exception:
                pass

    def _wait_inter_frame_gap(self) -> None:
        remaining = self.inter_frame_delay - (time.monotonic() - self._last_bus_activity)
        if remaining > 0:
            self.stop_event.wait(remaining)

    def _read_exact(self, size: int, deadline: float) -> bytes:
        if self.serial_port is None:
            return b""
        data = bytearray()
        while len(data) < size and not self.stop_event.is_set():
            if time.monotonic() >= deadline:
                break
            chunk = self.serial_port.read(size - len(data))
            if chunk:
                data.extend(chunk)
        return bytes(data)

    def _read_position_response(self) -> bytes:
        deadline = time.monotonic() + self.response_timeout
        header = self._read_exact(3, deadline)
        if len(header) != 3:
            raise TimeoutError("нет ответа линейного энкодера")

        address, function, third_byte = header
        if address != SLAVE_ADDRESS:
            raise EncoderProtocolError(f"неожиданный адрес энкодера: {address}")
        if function == (READ_FUNCTION | 0x80):
            tail = self._read_exact(2, deadline)
            frame = header + tail
            self._validate_crc(frame)
            raise EncoderProtocolError(f"Modbus exception 0x{third_byte:02X}")
        if function != READ_FUNCTION:
            raise EncoderProtocolError(f"неожиданная функция энкодера: 0x{function:02X}")
        if third_byte != 4:
            raise EncoderProtocolError(f"ожидалось 4 байта данных, получено {third_byte}")

        frame = header + self._read_exact(third_byte + 2, deadline)
        if len(frame) != 9:
            raise TimeoutError(f"неполный ответ энкодера: {len(frame)} из 9 байт")
        self._validate_crc(frame)
        return frame

    @staticmethod
    def _validate_crc(frame: bytes) -> None:
        if len(frame) < 4:
            raise EncoderProtocolError("слишком короткий RTU-кадр энкодера")
        received = frame[-2] | (frame[-1] << 8)
        expected = modbus_crc16(frame[:-2])
        if received != expected:
            raise EncoderProtocolError(
                f"CRC энкодера: принято 0x{received:04X}, ожидалось 0x{expected:04X}"
            )

    def _calculate_frequency(self, timestamp: float) -> float:
        self._poll_timestamps.append(timestamp)
        cutoff = timestamp - 2.0
        while self._poll_timestamps and self._poll_timestamps[0] < cutoff:
            self._poll_timestamps.popleft()
        if len(self._poll_timestamps) < 2:
            return 0.0
        duration = self._poll_timestamps[-1] - self._poll_timestamps[0]
        return (len(self._poll_timestamps) - 1) / duration if duration > 0 else 0.0

    def _poll_once(self) -> EncoderSample:
        if self.serial_port is None:
            raise serial.SerialException("порт энкодера не открыт")

        self._wait_inter_frame_gap()
        request = build_position_request()
        self.serial_port.reset_input_buffer()
        self.serial_port.write(request)
        self.serial_port.flush()
        self.total_requests += 1
        try:
            response = self._read_position_response()
        finally:
            self._last_bus_activity = time.monotonic()

        low_word = (response[3] << 8) | response[4]
        high_word = (response[5] << 8) | response[6]
        raw_value = low_word + high_word * 65536
        position_mm = raw_value / 1000.0
        now = time.monotonic()
        return EncoderSample(
            timestamp=time.time(),
            low_word=low_word,
            high_word=high_word,
            raw_value=raw_value,
            position_mm=position_mm,
            frequency_hz=self._calculate_frequency(now),
        )

    def _run(self) -> None:
        try:
            while not self.stop_event.is_set():
                if self.serial_port is None:
                    try:
                        self._open_port()
                    except (serial.SerialException, OSError):
                        self._set_online(False)
                        self.stop_event.wait(self.reconnect_delay)
                        continue

                try:
                    sample = self._poll_once()
                    self.successful_requests += 1
                    self.consecutive_errors = 0
                    self._publish_sample(sample)
                except (TimeoutError, EncoderProtocolError):
                    self.error_count += 1
                    self.consecutive_errors += 1
                    if self.consecutive_errors >= self.offline_error_threshold:
                        self._set_online(False)
                    if self.consecutive_errors >= self.reconnect_error_threshold:
                        self._close_port()
                        self.stop_event.wait(self.reconnect_delay)
                except (serial.SerialException, OSError):
                    self.error_count += 1
                    self.consecutive_errors += 1
                    self._set_online(False)
                    self._close_port()
                    self.stop_event.wait(self.reconnect_delay)
                except Exception:
                    # Ни одна непредвиденная ошибка энкодера не должна завершить приложение.
                    self.error_count += 1
                    self.consecutive_errors += 1
                    self._set_online(False)
                    self._close_port()
                    self.stop_event.wait(self.reconnect_delay)
        finally:
            self._set_online(False)
            self._close_port()
