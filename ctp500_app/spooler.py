from __future__ import annotations

import threading
import logging
from dataclasses import replace
from collections.abc import Iterable
from typing import Callable

from .models import GearItem, JobStatus, PrintDocument, PrintJob
from .printer import BluetoothPrinter
from .rendering import render_printable


JobListener = Callable[[], None]
LOGGER = logging.getLogger("ctp500.spooler")


class PrintSpooler:
    def __init__(
        self,
        printer: BluetoothPrinter,
        listener: JobListener | None = None,
        renderer=render_printable,
    ) -> None:
        self.printer = printer
        self._listener = listener or (lambda: None)
        self._renderer = renderer
        self._jobs: list[PrintJob] = []
        self._condition = threading.Condition()
        self._stopping = False
        self._suspended = False
        self._thread = threading.Thread(
            target=self._worker,
            name="ctp500-print-spooler",
            daemon=True,
        )
        self._thread.start()

    def _notify(self) -> None:
        try:
            self._listener()
        except Exception:
            # Listener failures must never stop the print worker.
            pass

    def jobs(self) -> tuple[PrintJob, ...]:
        with self._condition:
            return tuple(replace(job) for job in self._jobs)

    def enqueue(self, items: list[GearItem | PrintDocument], copies: int, priority: bool = False) -> list[str]:
        new_jobs = [PrintJob.create(item, copies) for item in items]
        with self._condition:
            if self._stopping:
                raise RuntimeError("Print spooler is stopping")
            if priority:
                insert_at = 0
                while insert_at < len(self._jobs) and self._jobs[insert_at].status in {
                    JobStatus.PRINTING,
                    JobStatus.FAILED,
                }:
                    insert_at += 1
                self._jobs[insert_at:insert_at] = new_jobs
            else:
                self._jobs.extend(new_jobs)
            self._condition.notify_all()
        self._notify()
        return [job.id for job in new_jobs]

    def cancel(self, job_id: str) -> bool:
        changed = False
        with self._condition:
            job = self._find(job_id)
            if job and not job._clipboard_claimed and job.status == JobStatus.PENDING:
                job.status = JobStatus.CANCELED
                job.remaining_copies = 0
                changed = True
            elif job and not job._clipboard_claimed and job.status == JobStatus.PRINTING:
                job.cancel_requested = True
                changed = True
            if changed:
                self._condition.notify_all()
        if changed:
            self._notify()
        return changed

    def retry(self, job_id: str) -> bool:
        changed = False
        with self._condition:
            job = self._find(job_id)
            if job and not job._clipboard_claimed and job.status == JobStatus.FAILED:
                job.status = JobStatus.PENDING
                job.error = ""
                job.cancel_requested = False
                changed = True
                self._condition.notify_all()
        if changed:
            self._notify()
        return changed

    def remove(self, job_id: str) -> bool:
        changed = False
        with self._condition:
            for index, job in enumerate(self._jobs):
                if (
                    job.id == job_id
                    and job.status != JobStatus.PRINTING
                    and not job._clipboard_claimed
                ):
                    self._jobs.pop(index)
                    changed = True
                    self._condition.notify_all()
                    break
        if changed:
            self._notify()
        return changed

    def clear_finished(self) -> None:
        with self._condition:
            self._jobs = [
                job
                for job in self._jobs
                if job.status not in {JobStatus.COMPLETED, JobStatus.CANCELED}
            ]
            self._condition.notify_all()
        self._notify()

    def wake(self) -> None:
        with self._condition:
            self._condition.notify_all()

    @property
    def suspended(self) -> bool:
        with self._condition:
            return self._suspended

    def set_suspended(self, suspended: bool) -> None:
        changed = False
        with self._condition:
            suspended = bool(suspended)
            if self._suspended != suspended:
                self._suspended = suspended
                changed = True
                self._condition.notify_all()
        if changed:
            self._notify()

    def claim_for_clipboard(self, job_ids: Iterable[str]) -> tuple[PrintJob, ...]:
        """Atomically reserve eligible queue entries for clipboard output."""
        requested = set(job_ids)
        claimed: list[PrintJob] = []
        with self._condition:
            for job in self._jobs:
                if (
                    job.id in requested
                    and job.status in {JobStatus.PENDING, JobStatus.FAILED}
                    and job.remaining_copies > 0
                    and not job._clipboard_claimed
                ):
                    job._clipboard_claimed = True
                    claimed.append(replace(job))
            if claimed:
                self._condition.notify_all()
        if claimed:
            self._notify()
        return tuple(claimed)

    def finish_clipboard_claim(self, job_ids: Iterable[str]) -> tuple[str, ...]:
        """Complete clipboard-reserved entries after the clipboard write succeeds."""
        requested = set(job_ids)
        completed: list[str] = []
        with self._condition:
            for job in self._jobs:
                if job.id in requested and job._clipboard_claimed:
                    job.status = JobStatus.COMPLETED
                    job.remaining_copies = 0
                    job.error = ""
                    job.cancel_requested = False
                    job._clipboard_claimed = False
                    completed.append(job.id)
            if completed:
                self._condition.notify_all()
        if completed:
            self._notify()
        return tuple(completed)

    def release_clipboard_claim(self, job_ids: Iterable[str]) -> tuple[str, ...]:
        """Return clipboard-reserved entries to their original queue state."""
        requested = set(job_ids)
        released: list[str] = []
        with self._condition:
            for job in self._jobs:
                if job.id in requested and job._clipboard_claimed:
                    job._clipboard_claimed = False
                    released.append(job.id)
            if released:
                self._condition.notify_all()
        if released:
            self._notify()
        return tuple(released)

    def has_unfinished_jobs(self) -> bool:
        with self._condition:
            return any(
                job.status in {JobStatus.PENDING, JobStatus.PRINTING, JobStatus.FAILED}
                for job in self._jobs
            )

    def stop(self, timeout: float = 2.0, *, abort: bool = False) -> bool:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if abort:
            disconnect = getattr(self.printer, "disconnect", None)
            if disconnect:
                disconnect()
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def _find(self, job_id: str) -> PrintJob | None:
        return next((job for job in self._jobs if job.id == job_id), None)

    def _next_job(self) -> PrintJob | None:
        # A failed job intentionally blocks later work until Retry or Remove.
        if any(job.status == JobStatus.FAILED for job in self._jobs):
            return None
        return next(
            (
                job
                for job in self._jobs
                if job.status == JobStatus.PENDING and not job._clipboard_claimed
            ),
            None,
        )

    def _worker(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._stopping
                    or (
                        not self._suspended
                        and self.printer.connected
                        and self._next_job() is not None
                    ),
                    timeout=1.0,
                )
                if self._stopping:
                    return
                job = self._next_job()
                if job is None or not self.printer.connected or self._suspended:
                    continue
                job.status = JobStatus.PRINTING
            self._notify()

            try:
                while job.remaining_copies > 0:
                    with self._condition:
                        if self._suspended:
                            job.status = JobStatus.PENDING
                            break
                    image = self._renderer(job.item)
                    self.printer.print_image(image)
                    with self._condition:
                        job.remaining_copies -= 1
                        if job.cancel_requested:
                            job.remaining_copies = 0
                            job.status = JobStatus.CANCELED
                            break
                        if self._suspended and job.remaining_copies > 0:
                            job.status = JobStatus.PENDING
                            break
                    self._notify()
                with self._condition:
                    if job.status == JobStatus.PRINTING and job.remaining_copies == 0:
                        job.status = JobStatus.COMPLETED
            except Exception as exc:
                with self._condition:
                    if self._stopping:
                        job.status = JobStatus.CANCELED
                        job.remaining_copies = 0
                    else:
                        job.status = JobStatus.FAILED
                        job.error = str(exc)
                        LOGGER.warning("Print job %s failed: %s", job.id, exc)
            self._notify()
