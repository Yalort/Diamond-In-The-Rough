from __future__ import annotations

import re
import socket
import threading
import time
import logging

from PIL import Image

from .config import (
    PRINTER_CONNECT_TIMEOUT_SECONDS,
    PRINTER_IO_TIMEOUT_SECONDS,
    RASTER_BAND_PAUSE_SECONDS,
)
from .rendering import raster_bands


ADDRESS_PATTERN = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
LOGGER = logging.getLogger("ctp500.printer")


class PrinterError(RuntimeError):
    pass


def normalize_address(address: str) -> str:
    value = address.strip().replace("-", ":").upper()
    if not ADDRESS_PATTERN.fullmatch(value):
        raise ValueError("Use a Bluetooth address such as AA:BB:CC:DD:EE:FF")
    return value


class BluetoothPrinter:
    def __init__(self, socket_factory=socket.socket) -> None:
        self._socket_factory = socket_factory
        self._socket: socket.socket | None = None
        self._address = ""
        self._state_lock = threading.RLock()
        self._io_lock = threading.Lock()
        self._abort = threading.Event()
        self._closed = False

    @property
    def connected(self) -> bool:
        with self._state_lock:
            return self._socket is not None

    @property
    def address(self) -> str:
        with self._state_lock:
            return self._address

    def connect(self, address: str) -> bytes:
        normalized = normalize_address(address)
        with self._state_lock:
            if self._closed:
                raise PrinterError("Printer has been closed")
            if self._socket is not None and self._address == normalized:
                return b""
        self.disconnect()
        with self._io_lock:
            sock = None
            try:
                self._abort.clear()
                sock = self._socket_factory(
                    socket.AF_BLUETOOTH,
                    socket.SOCK_STREAM,
                    socket.BTPROTO_RFCOMM,
                )
                sock.settimeout(PRINTER_CONNECT_TIMEOUT_SECONDS)
                sock.connect((normalized, 1))
                sock.settimeout(PRINTER_IO_TIMEOUT_SECONDS)
                sock.sendall(b"\x1e\x47\x03")
                status = sock.recv(38)
                with self._state_lock:
                    if self._abort.is_set() or self._closed:
                        raise PrinterError("Printer connection was canceled")
                    self._socket = sock
                    self._address = normalized
                LOGGER.info("Connected to printer %s", normalized)
                return status
            except Exception as exc:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
                LOGGER.warning("Printer connection failed for %s: %s", normalized, exc)
                if isinstance(exc, PrinterError):
                    raise
                raise PrinterError(f"Could not connect to printer: {exc}") from exc

    def disconnect(self) -> None:
        self._abort.set()
        with self._state_lock:
            self._disconnect_locked()

    def close(self) -> None:
        """Permanently close this instance and reject later connections."""
        with self._state_lock:
            self._closed = True
        self.disconnect()

    def _disconnect_locked(self) -> None:
        sock, self._socket = self._socket, None
        self._address = ""
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass
        LOGGER.info("Printer disconnected")

    def _current_socket(self) -> socket.socket:
        with self._state_lock:
            if self._socket is None:
                raise PrinterError("Printer is not connected")
            return self._socket

    def _send(self, sock: socket.socket, payload: bytes) -> None:
        if self._abort.is_set():
            raise PrinterError("Printing aborted")
        with self._state_lock:
            if self._socket is not sock:
                raise PrinterError("Printer connection changed during printing")
        sock.sendall(payload)

    def _drop_socket(self, sock: socket.socket) -> None:
        with self._state_lock:
            if self._socket is sock:
                self._disconnect_locked()

    def print_image(self, image: Image.Image) -> None:
        with self._io_lock:
            sock = self._current_socket()
            try:
                self._send(sock, b"\x1b\x40")
                time.sleep(0.1)
                self._send(sock, b"\x1d\x49\xf0\x19")
                time.sleep(0.1)
                for payload in raster_bands(image):
                    self._send(sock, payload)
                    time.sleep(RASTER_BAND_PAUSE_SECONDS)
                self._send(sock, b"\x0a\x0a\x0a\x9a")
            except Exception as exc:
                self._drop_socket(sock)
                LOGGER.warning("Print failed: %s", exc)
                if isinstance(exc, PrinterError):
                    raise
                raise PrinterError(f"Printing failed: {exc}") from exc
