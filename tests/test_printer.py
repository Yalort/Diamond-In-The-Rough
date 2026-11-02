from __future__ import annotations

import unittest
import threading
import time

from PIL import Image

from ctp500_app.printer import BluetoothPrinter, PrinterError, normalize_address


class FakeSocket:
    def __init__(self) -> None:
        self.timeout = None
        self.target = None
        self.sent: list[bytes] = []
        self.closed = False

    def settimeout(self, value) -> None:
        self.timeout = value

    def connect(self, target) -> None:
        self.target = target

    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)

    def recv(self, count: int) -> bytes:
        return b"ready".ljust(count, b"\x00")

    def shutdown(self, _how) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class SocketFactory:
    def __init__(self) -> None:
        self.socket = FakeSocket()

    def __call__(self, *_args):
        return self.socket


class BlockingSocket(FakeSocket):
    def __init__(self) -> None:
        super().__init__()
        self.print_started = threading.Event()
        self.released = threading.Event()

    def sendall(self, payload: bytes) -> None:
        if payload == b"\x1e\x47\x03":
            return super().sendall(payload)
        self.print_started.set()
        self.released.wait(2)
        if self.closed:
            raise OSError("socket closed")
        super().sendall(payload)

    def shutdown(self, _how) -> None:
        self.released.set()

    def close(self) -> None:
        super().close()
        self.released.set()


class PrinterTests(unittest.TestCase):
    def test_address_validation_and_normalization(self) -> None:
        self.assertEqual(normalize_address("aa-bb-cc-dd-ee-ff"), "AA:BB:CC:DD:EE:FF")
        with self.assertRaises(ValueError):
            normalize_address("not an address")

    def test_connection_and_print_command_sequence(self) -> None:
        factory = SocketFactory()
        printer = BluetoothPrinter(socket_factory=factory)
        status = printer.connect("AA:BB:CC:DD:EE:FF")
        self.assertTrue(printer.connected)
        self.assertEqual(factory.socket.target, ("AA:BB:CC:DD:EE:FF", 1))
        self.assertEqual(factory.socket.sent[0], b"\x1e\x47\x03")
        self.assertEqual(len(status), 38)

        printer.print_image(Image.new("L", (384, 150), 255))
        self.assertEqual(factory.socket.sent[1], b"\x1b\x40")
        self.assertEqual(factory.socket.sent[2], b"\x1d\x49\xf0\x19")
        self.assertTrue(factory.socket.sent[3].startswith(b"\x1d\x76\x30\x00"))
        self.assertEqual(factory.socket.sent[-1], b"\x0a\x0a\x0a\x9a")

        printer.disconnect()
        self.assertFalse(printer.connected)
        self.assertTrue(factory.socket.closed)

    def test_print_requires_connection(self) -> None:
        with self.assertRaises(PrinterError):
            BluetoothPrinter().print_image(Image.new("1", (384, 8)))

    def test_disconnect_interrupts_blocked_print_without_waiting_for_io_lock(self) -> None:
        socket_instance = BlockingSocket()
        printer = BluetoothPrinter(socket_factory=lambda *_args: socket_instance)
        printer.connect("AA:BB:CC:DD:EE:FF")
        errors: list[Exception] = []

        def print_work() -> None:
            try:
                printer.print_image(Image.new("1", (384, 8)))
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=print_work)
        worker.start()
        self.assertTrue(socket_instance.print_started.wait(1))
        started = time.monotonic()
        printer.disconnect()
        elapsed = time.monotonic() - started
        worker.join(1)

        self.assertLess(elapsed, 0.25)
        self.assertFalse(worker.is_alive())
        self.assertFalse(printer.connected)
        self.assertIsInstance(errors[0], PrinterError)


if __name__ == "__main__":
    unittest.main()
