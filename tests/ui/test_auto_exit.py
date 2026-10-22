"""Tests for the auto-exit watchdog's idle detection."""

from __future__ import annotations

import threading

from buildstock_rate_impact_calculator.ui import auto_exit


class _Done(Exception):
    """Raised by the fake sleep to end a watch() loop that never went idle."""


def _run_watch(session_counts, running=None, poll=5, grace=15):
    """Drive watch() with scripted polls; return the poll index it quit on."""
    counts = iter(session_counts)
    running_flags = iter(running) if running is not None else None
    polls = {"n": 0}
    quit_at: list[int] = []

    def fake_sleep(_seconds):
        polls["n"] += 1
        if polls["n"] > len(session_counts):
            raise _Done

    def get_sessions():
        return next(counts)

    def is_running():
        return next(running_flags) if running_flags is not None else False

    try:
        auto_exit.watch(
            get_sessions,
            is_running,
            lambda: quit_at.append(polls["n"]),
            sleep=fake_sleep,
            poll_seconds=poll,
            grace_seconds=grace,
        )
    except _Done:
        pass
    return quit_at[0] if quit_at else None


def test_quits_after_grace_period_of_consecutive_idle_polls():
    # grace 15s / poll 5s = 3 consecutive idle polls needed.
    assert _run_watch([1, 0, 0, 0]) == 4


def test_reconnect_resets_idle_streak():
    """A reload or reconnect within the grace period keeps the app alive."""
    assert _run_watch([0, 0, 1, 0, 0]) is None


def test_run_in_progress_blocks_shutdown():
    assert _run_watch([0, 0, 0, 0], running=[True, True, True, True]) is None


def test_quits_once_run_finishes():
    assert _run_watch([0, 0, 0, 0, 0], running=[True, True, False, False, False]) == 5


def test_unreadable_session_count_never_counts_as_idle():
    assert _run_watch([None, None, None, None]) is None


def test_run_in_progress_detects_named_pipeline_thread():
    release = threading.Event()
    t = threading.Thread(
        target=release.wait, name=auto_exit.PIPELINE_THREAD_NAME, daemon=True
    )
    t.start()
    try:
        assert auto_exit.run_in_progress()
    finally:
        release.set()
        t.join()
    assert not auto_exit.run_in_progress()


def test_start_respects_opt_out(monkeypatch):
    monkeypatch.setenv(auto_exit.OPT_OUT_ENV, "1")
    monkeypatch.setattr(auto_exit, "_started", False)
    created = []
    monkeypatch.setattr(auto_exit.threading, "Thread", lambda *a, **k: created.append(k))
    auto_exit.start()
    assert not created
    assert auto_exit._started is False
