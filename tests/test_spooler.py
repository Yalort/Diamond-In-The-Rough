from __future__ import annotations

import threading
import time
import unittest

from PIL import Image

from ctp500_app.catalog import parse_category_csv
from ctp500_app.models import JobStatus
from ctp500_app.spooler import PrintSpooler


class FakePrinter:
    def __init__(self, connected: bool = False) -> None:
        self.connected = connected
        self.printed = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.error: Exception | None = None

    def print_image(self, _image) -> None:
        self.started.set()
        if self.block:
            self.release.wait(2)
        if not self.connected:
            raise RuntimeError("printer disconnected")
        if self.error:
            raise self.error
        self.printed += 1

    def disconnect(self) -> None:
        self.connected = False
        self.release.set()


def item(name: str):
    return parse_category_csv(
        "Apparel",
        1,
        f"Manufacturer,Name,Coverage,Rank,Price\nAcme,{name},Chest,1,5\n".encode(),
    )[0]


def wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for spooler state")


class SpoolerTests(unittest.TestCase):
    def test_priority_and_fifo_order_while_paused(self) -> None:
        printer = FakePrinter(connected=False)
        spooler = PrintSpooler(printer, renderer=lambda _item: Image.new("1", (8, 8)))
        try:
            spooler.enqueue([item("A"), item("B")], 1)
            spooler.enqueue([item("Quick")], 1, priority=True)
            self.assertEqual([job.item.name for job in spooler.jobs()], ["Quick", "A", "B"])
        finally:
            spooler.stop()

    def test_cancel_active_finishes_current_copy_only(self) -> None:
        printer = FakePrinter(connected=True)
        printer.block = True
        spooler = PrintSpooler(printer, renderer=lambda _item: Image.new("1", (8, 8)))
        try:
            job_id = spooler.enqueue([item("A")], 3)[0]
            self.assertTrue(printer.started.wait(1))
            self.assertTrue(spooler.cancel(job_id))
            printer.release.set()
            wait_for(lambda: spooler.jobs()[0].status == JobStatus.CANCELED)
            self.assertEqual(printer.printed, 1)
            self.assertEqual(spooler.jobs()[0].remaining_copies, 0)
        finally:
            printer.release.set()
            spooler.stop()

    def test_failure_pauses_later_jobs_until_removed(self) -> None:
        printer = FakePrinter(connected=True)
        printer.error = RuntimeError("paper problem")
        spooler = PrintSpooler(printer, renderer=lambda _item: Image.new("1", (8, 8)))
        try:
            first, second = spooler.enqueue([item("A"), item("B")], 1)
            wait_for(lambda: spooler.jobs()[0].status == JobStatus.FAILED)
            self.assertEqual(spooler.jobs()[1].status, JobStatus.PENDING)
            printer.error = None
            self.assertTrue(spooler.remove(first))
            spooler.wake()
            wait_for(lambda: spooler.jobs()[0].status == JobStatus.COMPLETED)
            self.assertEqual(spooler.jobs()[0].id, second)
        finally:
            spooler.stop()

    def test_abort_stop_interrupts_active_job_and_joins_worker(self) -> None:
        printer = FakePrinter(connected=True)
        printer.block = True
        spooler = PrintSpooler(printer, renderer=lambda _item: Image.new("1", (8, 8)))
        spooler.enqueue([item("A")], 2)
        self.assertTrue(printer.started.wait(1))
        started = time.monotonic()
        stopped = spooler.stop(timeout=1, abort=True)

        self.assertTrue(stopped)
        self.assertLess(time.monotonic() - started, 0.25)
        self.assertEqual(spooler.jobs()[0].status, JobStatus.CANCELED)


if __name__ == "__main__":
    unittest.main()
