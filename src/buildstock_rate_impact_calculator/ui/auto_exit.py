"""Stop the Streamlit server when no browser tab is connected.

The app is a local desktop tool, so once every tab is closed the server keeps
running in a terminal the user may have forgotten about. A daemon watchdog
polls Streamlit's active-session count and, after ``GRACE_SECONDS`` of awake
time with no connected tab and no pipeline run in progress, raises SIGINT —
the same path as Ctrl+C.

Idle time is counted in consecutive polls rather than wall-clock time, so a
laptop sleep (during which the clock jumps but nothing polls) never counts as
idle time. The grace period also covers page reloads and the automatic
reconnect after a dropped connection.

Set ``BRIC_NO_AUTO_EXIT=1`` to disable (e.g. when hosting for several users).
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

OPT_OUT_ENV = "BRIC_NO_AUTO_EXIT"
POLL_SECONDS = 5
GRACE_SECONDS = 120
# Name given to the billing pipeline thread so runs are visible process-wide.
PIPELINE_THREAD_NAME = "bric-pipeline"

_started = False
_start_lock = threading.Lock()


def run_in_progress() -> bool:
    """True while any session's billing pipeline thread is running."""
    return any(
        t.name == PIPELINE_THREAD_NAME and t.is_alive() for t in threading.enumerate()
    )


def request_shutdown(delay: float = 0.0) -> None:
    """Stop the server as if Ctrl+C were pressed, optionally after *delay* s."""

    def _stop() -> None:
        signal.raise_signal(signal.SIGINT)

    if delay > 0:
        threading.Timer(delay, _stop).start()
    else:
        _stop()


def _active_sessions() -> int | None:
    """Number of connected browser sessions, or None if it can't be read."""
    try:
        from streamlit.runtime import Runtime

        if not Runtime.exists():
            return None
        return Runtime.instance()._session_mgr.num_active_sessions()
    except Exception:  # noqa: BLE001 — private API; fail safe (never shut down)
        return None


def watch(
    get_sessions: Callable[[], int | None],
    is_running: Callable[[], bool],
    on_idle: Callable[[], None],
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = POLL_SECONDS,
    grace_seconds: float = GRACE_SECONDS,
) -> None:
    """Poll until the app has been idle for the grace period, then call *on_idle*.

    Idle means no connected sessions and no run in progress. An unreadable
    session count resets the idle streak rather than counting toward it.
    """
    needed = max(1, round(grace_seconds / poll_seconds))
    idle_polls = 0
    while True:
        sleep(poll_seconds)
        sessions = get_sessions()
        if sessions == 0 and not is_running():
            idle_polls += 1
        else:
            idle_polls = 0
        if idle_polls >= needed:
            on_idle()
            return


def _on_idle() -> None:
    logger.info(
        "No browser tab connected for %ss; stopping the app (set %s=1 to disable).",
        GRACE_SECONDS,
        OPT_OUT_ENV,
    )
    request_shutdown()


def start() -> None:
    """Start the watchdog once per process (no-op if opted out)."""
    global _started
    if os.getenv(OPT_OUT_ENV):
        return
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(
        target=watch,
        args=(_active_sessions, run_in_progress, _on_idle),
        kwargs={"poll_seconds": POLL_SECONDS, "grace_seconds": GRACE_SECONDS},
        daemon=True,
        name="bric-auto-exit",
    ).start()
