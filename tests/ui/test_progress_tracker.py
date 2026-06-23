"""
Tests for the phase-aware ProgressTracker used by the Run page.
"""

from __future__ import annotations

import threading

from buildstock_rate_impact_calculator.ui.components.progress_tracker import (
    ProgressTracker,
)


def test_phases_progress_and_close_in_order():
    tracker = ProgressTracker()
    tracker.start_phase("Loading metadata")
    tracker.start_phase("Downloading files", total=10)
    tracker.advance(3, message="Downloaded 3")
    snap = tracker.snapshot()

    assert [p.name for p in snap.phases] == ["Loading metadata", "Downloading files"]
    assert snap.phases[0].done is True
    current = snap.current_phase
    assert current is not None
    assert current.name == "Downloading files"
    assert current.completed == 3
    assert current.fraction == 0.3
    assert snap.message == "Downloaded 3"


def test_indeterminate_phase_has_no_fraction():
    tracker = ProgressTracker()
    tracker.start_phase("Loading metadata")
    phase = tracker.snapshot().current_phase
    assert phase.total == 0
    assert phase.fraction is None
    assert phase.eta_seconds is None


def test_add_phase_total_grows_total():
    tracker = ProgressTracker()
    tracker.start_phase("Downloading", total=5)
    tracker.add_phase_total(5)
    assert tracker.snapshot().current_phase.total == 10


def test_done_closes_all_phases_and_sets_flags():
    tracker = ProgressTracker()
    tracker.start_phase("Working", total=4)
    tracker.advance(4)
    tracker.done("All finished")
    snap = tracker.snapshot()
    assert snap.is_done is True
    assert snap.is_error is False
    assert all(p.done for p in snap.phases)
    assert snap.finished_at is not None
    assert snap.message == "All finished"
    assert tracker.is_done()


def test_error_sets_error_state_and_log():
    tracker = ProgressTracker()
    tracker.start_phase("Working")
    tracker.error("boom")
    snap = tracker.snapshot()
    assert snap.is_done is True
    assert snap.is_error is True
    assert snap.error_message == "boom"
    assert any("ERROR: boom" in line for line in snap.log)


def test_legacy_update_maps_to_advance_and_log():
    tracker = ProgressTracker()
    tracker.start_phase("Working", total=2)
    tracker.update(1, message="one done")  # advance
    tracker.update(0, message="milestone note")  # log
    snap = tracker.snapshot()
    assert snap.current_phase.completed == 1
    assert any("milestone note" in line for line in snap.log)


def test_cancel_flow():
    tracker = ProgressTracker()
    assert tracker.is_cancelled() is False
    tracker.cancel()
    assert tracker.is_cancelled() is True
    assert tracker.cancel_event.is_set()
    assert tracker.snapshot().cancel_requested is True


def test_log_is_bounded():
    tracker = ProgressTracker()
    for i in range(100):
        tracker.log(f"line {i}")
    snap = tracker.snapshot()
    assert len(snap.log) == ProgressTracker.LOG_MAX_LINES
    assert "line 99" in snap.log[0]  # newest first


def test_snapshot_is_isolated_copy():
    tracker = ProgressTracker()
    tracker.start_phase("Working", total=10)
    snap = tracker.snapshot()
    tracker.advance(5)
    assert snap.current_phase.completed == 0  # snapshot unaffected by later updates


def test_concurrent_advance_is_consistent():
    tracker = ProgressTracker()
    tracker.start_phase("Downloading", total=32 * 50)

    def worker():
        for _ in range(50):
            tracker.advance(1, message="tick")

    threads = [threading.Thread(target=worker) for _ in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert tracker.snapshot().current_phase.completed == 32 * 50


def test_legacy_total_constructor_creates_anonymous_phase():
    tracker = ProgressTracker(total=7)
    tracker.update(3)
    phase = tracker.snapshot().current_phase
    assert phase.total == 7
    assert phase.completed == 3
