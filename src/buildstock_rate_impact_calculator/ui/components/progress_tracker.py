"""
ProgressTracker — thread-safe progress reporting for long-running background tasks.

Uses a queue.Queue for thread-safe communication between background worker threads
and the Streamlit main thread. Streamlit UI components must only be updated from
the main thread; this tracker accumulates updates that are drained by the main
thread on each rerun.
"""
from __future__ import annotations
import queue
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProgressUpdate:
    """A single progress update message."""
    completed: int
    total: int
    message: str = ""
    is_done: bool = False
    is_error: bool = False
    error_message: str = ""


class ProgressTracker:
    """
    Thread-safe progress tracker for background download/calculation tasks.

    Worker threads call update() / done() / error(). The Streamlit main thread
    calls drain() on each rerun to get pending updates and render them.

    Args:
        total: Total number of units of work expected.
    """

    def __init__(self, total: int) -> None:
        self._total = total
        self._completed = 0
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._done_event = threading.Event()
        self._queue: queue.Queue[ProgressUpdate] = queue.Queue()

    # ── Producer API (called from worker threads) ─────────────────────────────

    def update(self, completed: int = 1, message: str = "") -> None:
        """Increment the completed count and enqueue an update."""
        with self._lock:
            self._completed += completed
            snapshot = self._completed
        self._queue.put(ProgressUpdate(
            completed=snapshot,
            total=self._total,
            message=message,
        ))

    def done(self, message: str = "Complete") -> None:
        """Mark the task as fully done."""
        self._done_event.set()
        self._queue.put(ProgressUpdate(
            completed=self._total,
            total=self._total,
            message=message,
            is_done=True,
        ))

    def error(self, message: str) -> None:
        """Mark the task as failed with an error message."""
        self._done_event.set()
        self._queue.put(ProgressUpdate(
            completed=self._completed,
            total=self._total,
            message=message,
            is_done=True,
            is_error=True,
            error_message=message,
        ))

    # ── Consumer API (called from Streamlit main thread) ─────────────────────

    def cancel(self) -> None:
        """Signal the background task to stop."""
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        """Return True if cancellation was requested."""
        return self._cancel_event.is_set()

    def is_done(self) -> bool:
        """Return True if the task has been marked done (or errored)."""
        return self._done_event.is_set()

    def drain(self) -> list[ProgressUpdate]:
        """Return all pending updates without blocking."""
        updates: list[ProgressUpdate] = []
        while True:
            try:
                updates.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return updates

    @property
    def cancel_event(self) -> threading.Event:
        """The threading.Event used to signal cancellation to workers."""
        return self._cancel_event

    @property
    def total(self) -> int:
        return self._total

    @total.setter
    def total(self, value: int) -> None:
        self._total = value

    @property
    def completed(self) -> int:
        with self._lock:
            return self._completed
