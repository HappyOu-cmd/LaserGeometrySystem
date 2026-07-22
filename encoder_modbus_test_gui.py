#!/usr/bin/env python3
"""Диагностический GUI для линейного энкодера Modbus RTU с адресом 0."""

from __future__ import annotations

import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import serial
import tkinter as tk
from tkinter import messagebox, ttk


SLAVE_ADDRESS = 0
READ_FUNCTION = 0x03
WRITE_MULTIPLE_FUNCTION = 0x10
POSITION_START_REGISTER = 0
POSITION_REGISTER_COUNT = 2
CALIBRATION_REGISTER = 49
CALIBRATION_VALUE = 1


def modbus_crc16(data: bytes) -> int:
    """Вычисляет стандартный CRC-16/Modbus."""
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


def build_read_position_request() -> bytes:
    payload = bytes(
        (
            SLAVE_ADDRESS,
            READ_FUNCTION,
            (POSITION_START_REGISTER >> 8) & 0xFF,
            POSITION_START_REGISTER & 0xFF,
            (POSITION_REGISTER_COUNT >> 8) & 0xFF,
            POSITION_REGISTER_COUNT & 0xFF,
        )
    )
    return with_crc(payload)


def build_calibration_request() -> bytes:
    payload = bytes(
        (
            SLAVE_ADDRESS,
            WRITE_MULTIPLE_FUNCTION,
            (CALIBRATION_REGISTER >> 8) & 0xFF,
            CALIBRATION_REGISTER & 0xFF,
            0x00,
            0x01,
            0x02,
            (CALIBRATION_VALUE >> 8) & 0xFF,
            CALIBRATION_VALUE & 0xFF,
        )
    )
    return with_crc(payload)


class ProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class PositionSample:
    timestamp: float
    low_word: int
    high_word: int
    raw_value: int
    position_mm: float
    frequency_hz: float
    response_hex: str


class EncoderRtuWorker:
    """Единственный владелец serial-порта: опрос и калибровка идут в одном потоке."""

    def __init__(
        self,
        port: str,
        events: queue.Queue,
        baudrate: int = 9600,
        response_timeout: float = 0.12,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.response_timeout = response_timeout
        self.events = events
        self.commands: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.serial_port: Optional[serial.Serial] = None
        self.last_bus_activity = 0.0
        self.poll_timestamps: deque[float] = deque(maxlen=500)
        self.total_requests = 0
        self.successful_requests = 0
        self.error_count = 0
        self.consecutive_errors = 0

    @property
    def inter_frame_delay(self) -> float:
        # 8N1 = 10 бит на символ; Modbus RTU требует минимум 3.5 символа тишины.
        return 3.5 * 10.0 / self.baudrate

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="encoder-rtu", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.commands.put("stop")

    def calibrate(self) -> None:
        self.commands.put("calibrate")

    def _post(self, event_type: str, **payload) -> None:
        self.events.put({"type": event_type, **payload})

    def _open_port(self) -> None:
        self._post("status", connected=False, text=f"Открытие {self.port}…")
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
        self.last_bus_activity = time.monotonic()
        self.consecutive_errors = 0
        self._post("status", connected=True, text=f"Подключено: {self.port}, 9600 8N1")
        self._post("log", level="ok", text=f"Порт {self.port} открыт, начат максимальный опрос")

    def _close_port(self) -> None:
        port = self.serial_port
        self.serial_port = None
        if port is not None:
            try:
                port.close()
            except Exception:
                pass

    def _wait_inter_frame_gap(self) -> None:
        remaining = self.inter_frame_delay - (time.monotonic() - self.last_bus_activity)
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

    def _read_response(self, expected_function: int) -> bytes:
        deadline = time.monotonic() + self.response_timeout
        header = self._read_exact(2, deadline)
        if len(header) != 2:
            raise TimeoutError("нет заголовка ответа")

        address, function = header
        if address != SLAVE_ADDRESS:
            raise ProtocolError(f"неожиданный адрес ответа: {address}")

        if function == (expected_function | 0x80):
            tail = self._read_exact(3, deadline)
            frame = header + tail
            self._validate_crc(frame)
            if len(frame) != 5:
                raise ProtocolError("неполный кадр исключения")
            raise ProtocolError(f"Modbus exception 0x{frame[2]:02X}")

        if function != expected_function:
            raise ProtocolError(f"неожиданная функция ответа: 0x{function:02X}")

        if function == READ_FUNCTION:
            byte_count_raw = self._read_exact(1, deadline)
            if len(byte_count_raw) != 1:
                raise TimeoutError("нет длины данных в ответе")
            byte_count = byte_count_raw[0]
            tail = self._read_exact(byte_count + 2, deadline)
            frame = header + byte_count_raw + tail
        elif function == WRITE_MULTIPLE_FUNCTION:
            frame = header + self._read_exact(6, deadline)
        else:
            raise ProtocolError(f"неподдерживаемая функция 0x{function:02X}")

        self._validate_crc(frame)
        return frame

    @staticmethod
    def _validate_crc(frame: bytes) -> None:
        if len(frame) < 4:
            raise ProtocolError("слишком короткий кадр")
        received_crc = frame[-2] | (frame[-1] << 8)
        calculated_crc = modbus_crc16(frame[:-2])
        if received_crc != calculated_crc:
            raise ProtocolError(
                f"ошибка CRC: принято 0x{received_crc:04X}, рассчитано 0x{calculated_crc:04X}"
            )

    def _exchange(self, request: bytes, expected_function: int) -> bytes:
        if self.serial_port is None:
            raise serial.SerialException("порт не открыт")
        self._wait_inter_frame_gap()
        self.serial_port.reset_input_buffer()
        self.serial_port.write(request)
        self.serial_port.flush()
        response = self._read_response(expected_function)
        self.last_bus_activity = time.monotonic()
        return response

    def _poll_position(self) -> None:
        request = build_read_position_request()
        self.total_requests += 1
        response = self._exchange(request, READ_FUNCTION)

        if len(response) != 9:
            raise ProtocolError(f"ожидалось 9 байт, получено {len(response)}")
        if response[2] != 4:
            raise ProtocolError(f"ожидалось 4 байта данных, получено {response[2]}")

        low_word = (response[3] << 8) | response[4]
        high_word = (response[5] << 8) | response[6]
        raw_value = low_word + high_word * 65536
        position_mm = raw_value / 1000.0
        now = time.monotonic()
        self.poll_timestamps.append(now)
        cutoff = now - 2.0
        while self.poll_timestamps and self.poll_timestamps[0] < cutoff:
            self.poll_timestamps.popleft()
        if len(self.poll_timestamps) >= 2:
            duration = self.poll_timestamps[-1] - self.poll_timestamps[0]
            frequency = (len(self.poll_timestamps) - 1) / duration if duration > 0 else 0.0
        else:
            frequency = 0.0

        self.successful_requests += 1
        self.consecutive_errors = 0
        self._post(
            "sample",
            sample=PositionSample(
                timestamp=time.time(),
                low_word=low_word,
                high_word=high_word,
                raw_value=raw_value,
                position_mm=position_mm,
                frequency_hz=frequency,
                response_hex=response.hex(" ").upper(),
            ),
            total=self.total_requests,
            successful=self.successful_requests,
            errors=self.error_count,
            request_hex=request.hex(" ").upper(),
        )

    def _perform_calibration(self) -> None:
        request = build_calibration_request()
        self._post("log", level="info", text=f"Калибровка TX: {request.hex(' ').upper()}")
        try:
            response = self._exchange(request, WRITE_MULTIPLE_FUNCTION)
            if len(response) != 8:
                raise ProtocolError(f"ответ калибровки: ожидалось 8 байт, получено {len(response)}")
            start_register = (response[2] << 8) | response[3]
            quantity = (response[4] << 8) | response[5]
            if start_register != CALIBRATION_REGISTER or quantity != 1:
                raise ProtocolError(
                    f"ответ калибровки не совпал: регистр={start_register}, количество={quantity}"
                )
            self._post(
                "calibration",
                ok=True,
                text=f"Калибровка подтверждена. RX: {response.hex(' ').upper()}",
            )
        except TimeoutError:
            # Для стандартного broadcast-адреса 0 отсутствие ответа допустимо.
            self.last_bus_activity = time.monotonic()
            self._post(
                "calibration",
                ok=None,
                text="Команда калибровки отправлена, ответа нет (для адреса 0 это допустимо)",
            )

    def _handle_error(self, exc: Exception) -> None:
        self.error_count += 1
        self.consecutive_errors += 1
        now = time.monotonic()
        # Не забиваем GUI одинаковыми сообщениями на максимальной частоте.
        last_report = getattr(self, "_last_error_report", 0.0)
        if now - last_report >= 1.0:
            self._last_error_report = now
            self._post(
                "error",
                text=str(exc),
                total=self.total_requests,
                successful=self.successful_requests,
                errors=self.error_count,
            )

        if self.consecutive_errors >= 10:
            raise serial.SerialException(f"10 последовательных ошибок обмена: {exc}")

    def _run(self) -> None:
        reconnect_delay = 1.0
        try:
            while not self.stop_event.is_set():
                if self.serial_port is None:
                    try:
                        self._open_port()
                    except Exception as exc:
                        self._post("status", connected=False, text=f"Ошибка порта: {exc}")
                        self._post("log", level="error", text=f"Не удалось открыть порт: {exc}")
                        self.stop_event.wait(reconnect_delay)
                        continue

                try:
                    command = self.commands.get_nowait()
                except queue.Empty:
                    command = None

                if command == "stop":
                    break

                try:
                    if command == "calibrate":
                        self._perform_calibration()
                    else:
                        self._poll_position()
                except (TimeoutError, ProtocolError) as exc:
                    try:
                        self._handle_error(exc)
                    except serial.SerialException as fatal:
                        self._post("log", level="error", text=str(fatal))
                        self._close_port()
                        self._post("status", connected=False, text="Переподключение…")
                        self.stop_event.wait(reconnect_delay)
                except (serial.SerialException, OSError) as exc:
                    self._post("log", level="error", text=f"Ошибка serial: {exc}")
                    self._close_port()
                    self._post("status", connected=False, text="Связь потеряна, переподключение…")
                    self.stop_event.wait(reconnect_delay)
        finally:
            self._close_port()
            self._post("status", connected=False, text="Остановлено")
            self._post("stopped")


class EncoderTestApp:
    BG = "#0B1120"
    PANEL = "#111A2E"
    PANEL_ALT = "#17233B"
    TEXT = "#E6EDF7"
    MUTED = "#91A0B8"
    ACCENT = "#36D399"
    BLUE = "#60A5FA"
    WARNING = "#FBBF24"
    ERROR = "#FB7185"
    GRID = "#263653"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Линейный энкодер — Modbus RTU диагностика")
        self.root.geometry("1180x790")
        self.root.minsize(980, 680)
        self.root.configure(bg=self.BG)

        self.events: queue.Queue = queue.Queue()
        self.worker: Optional[EncoderRtuWorker] = None
        self.plot_samples: deque[tuple[float, float]] = deque(maxlen=600)
        self.latest_sample: Optional[PositionSample] = None

        self._configure_styles()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(40, self._process_events)

    def _configure_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=self.BG)
        style.configure("Panel.TFrame", background=self.PANEL)
        style.configure("TLabel", background=self.BG, foreground=self.TEXT, font=("DejaVu Sans", 10))
        style.configure("Title.TLabel", font=("DejaVu Sans", 20, "bold"), foreground=self.TEXT)
        style.configure("Muted.TLabel", foreground=self.MUTED)
        style.configure("CardTitle.TLabel", background=self.PANEL, foreground=self.MUTED, font=("DejaVu Sans", 9))
        style.configure("CardValue.TLabel", background=self.PANEL, foreground=self.TEXT, font=("DejaVu Sans Mono", 25, "bold"))
        style.configure("Accent.TButton", font=("DejaVu Sans", 10, "bold"), padding=(16, 9), background=self.ACCENT, foreground="#06130E")
        style.map("Accent.TButton", background=[("active", "#6EE7B7"), ("disabled", "#344257")])
        style.configure("Secondary.TButton", font=("DejaVu Sans", 10), padding=(14, 9), background=self.PANEL_ALT, foreground=self.TEXT)
        style.map("Secondary.TButton", background=[("active", "#243451")])
        style.configure("Danger.TButton", font=("DejaVu Sans", 10), padding=(14, 9), background="#4C1D2B", foreground="#FFDCE3")
        style.map("Danger.TButton", background=[("active", "#70283C")])
        style.configure("TEntry", fieldbackground="#0D1628", foreground=self.TEXT, insertcolor=self.TEXT, padding=8)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X, pady=(0, 16))
        ttk.Label(header, text="Линейный энкодер", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(
            header,
            text="Modbus RTU · адрес 0 · Holding 0–1 · 9600 8N1",
            style="Muted.TLabel",
        ).pack(anchor=tk.W, pady=(3, 0))

        controls = ttk.Frame(outer, style="Panel.TFrame", padding=14)
        controls.pack(fill=tk.X, pady=(0, 14))
        ttk.Label(controls, text="Порт", background=self.PANEL).pack(side=tk.LEFT, padx=(0, 8))
        self.port_var = tk.StringVar(value="/dev/ttyUSB0")
        self.port_entry = ttk.Entry(controls, textvariable=self.port_var, width=27)
        self.port_entry.pack(side=tk.LEFT, padx=(0, 12))
        self.start_button = ttk.Button(controls, text="▶ Запустить", style="Accent.TButton", command=self._start)
        self.start_button.pack(side=tk.LEFT, padx=4)
        self.stop_button = ttk.Button(controls, text="■ Остановить", style="Danger.TButton", command=self._stop, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=4)
        self.calibrate_button = ttk.Button(
            controls,
            text="Калибровать ноль",
            style="Secondary.TButton",
            command=self._calibrate,
            state=tk.DISABLED,
        )
        self.calibrate_button.pack(side=tk.LEFT, padx=(16, 4))

        self.status_dot = tk.Canvas(controls, width=18, height=18, bg=self.PANEL, highlightthickness=0)
        self.status_dot.pack(side=tk.RIGHT, padx=(8, 0))
        self.status_dot_id = self.status_dot.create_oval(4, 4, 14, 14, fill=self.MUTED, outline="")
        self.status_var = tk.StringVar(value="Остановлено")
        ttk.Label(controls, textvariable=self.status_var, background=self.PANEL, foreground=self.MUTED).pack(side=tk.RIGHT)

        cards = ttk.Frame(outer)
        cards.pack(fill=tk.X, pady=(0, 14))
        self.position_var = tk.StringVar(value="— мм")
        self.frequency_var = tk.StringVar(value="0.0 Гц")
        self.raw_var = tk.StringVar(value="—")
        self.quality_var = tk.StringVar(value="0 / 0")
        self._card(cards, "ПОЛОЖЕНИЕ", self.position_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self._card(cards, "ЧАСТОТА ОПРОСА", self.frequency_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self._card(cards, "RAW 32-BIT", self.raw_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self._card(cards, "УСПЕШНО / ВСЕГО", self.quality_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        graph_panel = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        graph_panel.pack(fill=tk.BOTH, expand=True, pady=(0, 14))
        graph_header = ttk.Frame(graph_panel, style="Panel.TFrame")
        graph_header.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(graph_header, text="Положение во времени", background=self.PANEL, foreground=self.TEXT, font=("DejaVu Sans", 11, "bold")).pack(side=tk.LEFT)
        ttk.Label(graph_header, text="последние 600 отсчётов", background=self.PANEL, foreground=self.MUTED).pack(side=tk.RIGHT)
        self.graph = tk.Canvas(graph_panel, bg="#0A1324", highlightthickness=0, height=280)
        self.graph.pack(fill=tk.BOTH, expand=True)
        self.graph.bind("<Configure>", lambda _event: self._draw_graph())

        bottom = ttk.Frame(outer)
        bottom.pack(fill=tk.X)
        details = ttk.Frame(bottom, style="Panel.TFrame", padding=12)
        details.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 7))
        self.low_word_var = tk.StringVar(value="—")
        self.high_word_var = tk.StringVar(value="—")
        self.last_tx_var = tk.StringVar(value=build_read_position_request().hex(" ").upper())
        self.last_rx_var = tk.StringVar(value="—")
        self._detail_row(details, "Регистр 0, младшее слово", self.low_word_var)
        self._detail_row(details, "Регистр 1, старшее слово", self.high_word_var)
        self._detail_row(details, "Последний TX", self.last_tx_var)
        self._detail_row(details, "Последний RX", self.last_rx_var)

        log_panel = ttk.Frame(bottom, style="Panel.TFrame", padding=12)
        log_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(7, 0))
        ttk.Label(log_panel, text="События", background=self.PANEL, foreground=self.TEXT, font=("DejaVu Sans", 11, "bold")).pack(anchor=tk.W, pady=(0, 6))
        self.log_text = tk.Text(
            log_panel,
            height=7,
            bg="#0A1324",
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief=tk.FLAT,
            font=("DejaVu Sans Mono", 9),
            wrap=tk.WORD,
            state=tk.DISABLED,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("ok", foreground=self.ACCENT)
        self.log_text.tag_configure("info", foreground=self.BLUE)
        self.log_text.tag_configure("warning", foreground=self.WARNING)
        self.log_text.tag_configure("error", foreground=self.ERROR)

    def _card(self, parent: ttk.Frame, title: str, variable: tk.StringVar) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=14)
        ttk.Label(frame, text=title, style="CardTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(frame, textvariable=variable, style="CardValue.TLabel").pack(anchor=tk.W, pady=(5, 0))
        return frame

    def _detail_row(self, parent: ttk.Frame, title: str, variable: tk.StringVar) -> None:
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text=title, background=self.PANEL, foreground=self.MUTED).pack(side=tk.LEFT)
        ttk.Label(
            row,
            textvariable=variable,
            background=self.PANEL,
            foreground=self.TEXT,
            font=("DejaVu Sans Mono", 9),
        ).pack(side=tk.RIGHT)

    def _start(self) -> None:
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("Порт не указан", "Укажите serial-порт энкодера")
            return
        self.plot_samples.clear()
        self.worker = EncoderRtuWorker(port=port, events=self.events)
        self.worker.start()
        self.port_entry.configure(state=tk.DISABLED)
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.calibrate_button.configure(state=tk.NORMAL)

    def _stop(self) -> None:
        if self.worker:
            self.worker.stop()
        self.stop_button.configure(state=tk.DISABLED)
        self.calibrate_button.configure(state=tk.DISABLED)

    def _calibrate(self) -> None:
        if not self.worker:
            return
        if not messagebox.askyesno(
            "Калибровка нуля",
            "Записать значение 1 функцией 0x10 в Holding-регистр 49?",
        ):
            return
        self.worker.calibrate()

    def _set_status(self, connected: bool, text: str) -> None:
        self.status_var.set(text)
        self.status_dot.itemconfigure(self.status_dot_id, fill=self.ACCENT if connected else self.ERROR)

    def _append_log(self, text: str, level: str = "info") -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{stamp}  {text}\n", level)
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 250:
            self.log_text.delete("1.0", "30.0")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _process_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                event_type = event["type"]
                if event_type == "status":
                    self._set_status(bool(event.get("connected")), event["text"])
                elif event_type == "log":
                    self._append_log(event["text"], event.get("level", "info"))
                elif event_type == "error":
                    self._append_log(event["text"], "error")
                    self.quality_var.set(f"{event['successful']} / {event['total']}")
                elif event_type == "calibration":
                    level = "ok" if event["ok"] is True else "warning"
                    self._append_log(event["text"], level)
                elif event_type == "sample":
                    self._show_sample(event)
                elif event_type == "stopped":
                    self.worker = None
                    self.port_entry.configure(state=tk.NORMAL)
                    self.start_button.configure(state=tk.NORMAL)
                    self.stop_button.configure(state=tk.DISABLED)
                    self.calibrate_button.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(40, self._process_events)

    def _show_sample(self, event: dict) -> None:
        sample: PositionSample = event["sample"]
        self.latest_sample = sample
        self.position_var.set(f"{sample.position_mm:,.3f} мм".replace(",", " "))
        self.frequency_var.set(f"{sample.frequency_hz:.1f} Гц")
        self.raw_var.set(str(sample.raw_value))
        self.quality_var.set(f"{event['successful']} / {event['total']}")
        self.low_word_var.set(f"{sample.low_word}  (0x{sample.low_word:04X})")
        self.high_word_var.set(f"{sample.high_word}  (0x{sample.high_word:04X})")
        self.last_tx_var.set(event["request_hex"])
        self.last_rx_var.set(sample.response_hex)
        self.plot_samples.append((sample.timestamp, sample.position_mm))
        self._draw_graph()

    def _draw_graph(self) -> None:
        canvas = self.graph
        canvas.delete("all")
        width = max(canvas.winfo_width(), 100)
        height = max(canvas.winfo_height(), 100)
        left, right, top, bottom = 68, 18, 18, 35
        plot_w = width - left - right
        plot_h = height - top - bottom

        for i in range(6):
            y = top + plot_h * i / 5
            canvas.create_line(left, y, width - right, y, fill=self.GRID)
        for i in range(7):
            x = left + plot_w * i / 6
            canvas.create_line(x, top, x, height - bottom, fill=self.GRID)

        if not self.plot_samples:
            canvas.create_text(width / 2, height / 2, text="Ожидание данных…", fill=self.MUTED, font=("DejaVu Sans", 12))
            return

        values = [value for _, value in self.plot_samples if math.isfinite(value)]
        if not values:
            return
        y_min, y_max = min(values), max(values)
        span = y_max - y_min
        padding = max(span * 0.12, 0.01)
        y_min -= padding
        y_max += padding
        span = y_max - y_min

        for i in range(6):
            value = y_max - span * i / 5
            y = top + plot_h * i / 5
            canvas.create_text(left - 8, y, text=f"{value:.3f}", fill=self.MUTED, anchor=tk.E, font=("DejaVu Sans Mono", 8))

        samples = list(self.plot_samples)
        t_min = samples[0][0]
        t_max = samples[-1][0]
        t_span = max(t_max - t_min, 0.001)
        points = []
        for timestamp, value in samples:
            x = left + (timestamp - t_min) / t_span * plot_w
            y = top + (y_max - value) / span * plot_h
            points.extend((x, y))
        if len(points) >= 4:
            canvas.create_line(*points, fill=self.ACCENT, width=2, smooth=False)
        elif len(points) == 2:
            x, y = points
            canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=self.ACCENT, outline="")
        canvas.create_text(left, height - 10, text=f"{t_span:.1f} с", fill=self.MUTED, anchor=tk.W, font=("DejaVu Sans", 8))
        canvas.create_text(width - right, height - 10, text="сейчас", fill=self.MUTED, anchor=tk.E, font=("DejaVu Sans", 8))

    def _on_close(self) -> None:
        if self.worker:
            self.worker.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    EncoderTestApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
