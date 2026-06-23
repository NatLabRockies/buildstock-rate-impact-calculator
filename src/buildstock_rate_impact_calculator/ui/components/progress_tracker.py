"""
ProgressTracker — thread-safe, phase-aware progress reporting for long-running
background tasks.

Worker threads report progress through named phases (start_phase / advance /
log). The Streamlit main thread calls snapshot() to get a consistent,
immutable view to render — there is no message queue to drain, so update
volume from many worker threads stays cheap and nothing is lost between
reruns.
"""

from __future__ import annotations
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Optional


@dataclass
class PhaseInfo:
    """Progress of one named pipeline phase."""

    name: str
    completed: int = 0
    total: int = 0  # 0 = indeterminate (no meaningful total)
    done: bool = False
    started_at: float = field(default_factory=time.time)

    @property
    def fraction(self) -> Optional[float]:
        """Completed fraction in [0, 1], or None when indeterminate."""
        if self.total <= 0:
            return None
        return min(self.completed / self.total, 1.0)

    @property
    def eta_seconds(self) -> Optional[float]:
        """Estimated remaining seconds based on the phase's average rate."""
        if self.total <= 0 or self.completed <= 0 or self.done:
            return None
        elapsed = time.time() - self.started_at
        if elapsed <= 0:
            return None
        rate = self.completed / elapsed
        if rate <= 0:
            return None
        return max(self.total - self.completed, 0) / rate


@dataclass
class ProgressSnapshot:
    """Immutable view of the tracker state for rendering."""

    phases: list[PhaseInfo]
    message: str
    log: list[str]
    started_at: float
    finished_at: Optional[float]
    is_done: bool
    is_error: bool
    error_message: str
    cancel_requested: bool

    @property
    def current_phase(self) -> Optional[PhaseInfo]:
        for phase in reversed(self.phases):
            if not phase.done:
                return phase
        return None

    @property
    def elapsed_seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(end - self.started_at, 0.0)


class ProgressTracker:
    """
    Thread-safe, phase-aware progress tracker for background pipelines.

    Producer API (worker threads):
        start_phase(name, total) — begin a named phase (closes the previous one)
        add_phase_total(n)       — grow the current phase's total
        advance(n, message)      — increment the current phase's completed count
        log(message)             — record a milestone message
        done(message) / error(message)

    Consumer API (Streamlit main thread):
        snapshot() — consistent copy of all state for rendering
        cancel() / is_cancelled() / is_done() / cancel_event
    """

    LOG_MAX_LINES = 30

    def __init__(self, total: int = 0) -> None:
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._done_event = threading.Event()
        self._phases: list[PhaseInfo] = []
        self._message = ""
        self._log: deque[str] = deque(maxlen=self.LOG_MAX_LINES)
        self._started_at = time.time()
        self._finished_at: Optional[float] = None
        self._is_error = False
        self._error_message = ""
        if total > 0:
            # Legacy single-counter mode: behave as one anonymous phase
            self._phases.append(PhaseInfo(name="Working", total=total))

    # ── Producer API (called from worker threads) ─────────────────────────────

    @staticmethod
    def _stamp(message: str) -> str:
        return f"{time.strftime('%H:%M:%S')}  {message}"

    def start_phase(self, name: str, total: int = 0) -> None:
        """Begin a new named phase, marking any previous phase as done."""
        with self._lock:
            if self._phases and not self._phases[-1].done:
                self._phases[-1].done = True
            self._phases.append(PhaseInfo(name=name, total=int(total)))
            self._message = name
            self._log.appendleft(self._stamp(name))

    def add_phase_total(self, n: int) -> None:
        """Grow the current phase's expected total by *n* units."""
        with self._lock:
            if self._phases:
                self._phases[-1].total += int(n)

    def advance(self, n: int = 1, message: str = "") -> None:
        """Increment the current phase's completed count."""
        with self._lock:
            if self._phases:
                self._phases[-1].completed += int(n)
            if message:
                self._message = message

    def log(self, message: str) -> None:
        """Record a milestone message (kept in the bounded log)."""
        with self._lock:
            self._message = message
            self._log.appendleft(self._stamp(message))

    def update(self, completed: int = 1, message: str = "") -> None:
        """Legacy producer call: positive *completed* advances the current
        phase; zero with a message records a milestone."""
        if completed:
            self.advance(completed, message)
        elif message:
            self.log(message)

    def done(self, message: str = "Complete") -> None:
        """Mark the task as fully done."""
        with self._lock:
            for phase in self._phases:
                phase.done = True
            self._message = message
            self._log.appendleft(self._stamp(message))
            self._finished_at = time.time()
        self._done_event.set()

    def error(self, message: str) -> None:
        """Mark the task as failed with an error message."""
        with self._lock:
            if self._phases:
                self._phases[-1].done = True
            self._is_error = True
            self._error_message = message
            self._message = message
            self._log.appendleft(self._stamp(f"ERROR: {message}"))
            self._finished_at = time.time()
        self._done_event.set()

    # ── Consumer API (called from Streamlit main thread) ─────────────────────

    def snapshot(self) -> ProgressSnapshot:
        """Return a consistent copy of the tracker state for rendering."""
        with self._lock:
            return ProgressSnapshot(
                phases=[replace(p) for p in self._phases],
                message=self._message,
                log=list(self._log),
                started_at=self._started_at,
                finished_at=self._finished_at,
                is_done=self._done_event.is_set(),
                is_error=self._is_error,
                error_message=self._error_message,
                cancel_requested=self._cancel_event.is_set(),
            )

    def cancel(self) -> None:
        """Signal the background task to stop."""
        self._cancel_event.set()
        self.log("Cancellation requested — waiting for workers to stop…")

    def is_cancelled(self) -> bool:
        """Return True if cancellation was requested."""
        return self._cancel_event.is_set()

    def is_done(self) -> bool:
        """Return True if the task has been marked done (or errored)."""
        return self._done_event.is_set()

    @property
    def cancel_event(self) -> threading.Event:
        """The threading.Event used to signal cancellation to workers."""
        return self._cancel_event
