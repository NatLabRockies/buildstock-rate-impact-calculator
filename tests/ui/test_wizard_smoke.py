"""Smoke test for the wizard shell (app.py).

Guards the stepper wiring: a fresh session boots on the Dataset step, the
segmented-control header renders, and nothing raises. Behavioral coverage of
the step predicates lives in ``test_steps.py``; this only checks that the
single-page render + header + footer nav assemble without error.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "buildstock_rate_impact_calculator"
    / "app.py"
)


def test_app_boots_on_dataset_step() -> None:
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.run()
    assert not at.exception
    # The Dataset step's page header renders on a fresh session.
    assert any("Dataset & Upgrade Selection" in h.value for h in at.header)


def test_nav_controls_present() -> None:
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.run()
    assert not at.exception
    keys = {b.key for b in at.button}
    # Footer Next button and the sidebar per-step jump buttons are wired up.
    assert "wizard_next" in keys
    assert "status_jump_dataset" in keys
    assert "status_jump_results" in keys


def test_next_disabled_on_fresh_dataset_step() -> None:
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.run()
    assert not at.exception
    # Nothing selected yet → the Dataset step blocks advancing.
    next_btn = next(b for b in at.button if b.key == "wizard_next")
    assert next_btn.disabled is True


def test_next_advances_header_control_when_step_completes() -> None:
    """Regression: advancing must move the header indicator even when the click
    also flips the current step's label from a number to a ✓ (the completion
    mark change previously desynced the string-valued segmented control)."""
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.run()
    assert not at.exception

    # Complete the Dataset step so advancing also changes its label to a ✓.
    state = at.session_state["app_state"]
    state.dataset_type = "resstock"
    state.release = "2024_release_2"
    state.selected_upgrades = [0, 1]
    at.run()
    assert at.session_state["wizard_step"] == 0
    assert at.session_state["wizard_step_control"] == 0

    next_btn = next(b for b in at.button if b.key == "wizard_next")
    assert next_btn.disabled is False
    next_btn.click().run()
    assert not at.exception

    # Both the canonical index and the header control must advance together.
    assert at.session_state["wizard_step"] == 1
    assert at.session_state["wizard_step_control"] == 1
