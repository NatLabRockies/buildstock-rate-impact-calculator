"""Wizard step registry, completion predicates, and navigation helpers.

This module is the single source of truth for the six-step workflow: which
steps exist, when each one counts as complete, when the user may advance past
it, and how it summarizes itself in the sidebar. The stepper header, the
Next-button gating, the per-page prerequisite gates, and the sidebar status
panel all read from the same predicates here.

It must not import any page modules — pages import ``steps`` — so keep this
dependency-free apart from Streamlit and :class:`AppState`.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional

import streamlit as st

from buildstock_rate_impact_calculator.ui.state import AppState

# Session keys
WIZARD_STEP_KEY = "wizard_step"  # int index of the active step (canonical)
WIZARD_CONTROL_KEY = "wizard_step_control"  # widget key of the header control
# Set by programmatic navigation to force the header control to re-seed from
# WIZARD_STEP_KEY on the next run (see the header block in app.py).
WIZARD_SYNC_FLAG = "wizard_step_sync"

# Step index constants (used across app.py and pages for readability)
STEP_DATASET = 0
STEP_GEOGRAPHY = 1
STEP_FILTERS = 2
STEP_RATES = 3
STEP_RUN = 4
STEP_RESULTS = 5

# Human-readable display names for dataset versions. Kept here (rather than in
# page_01_dataset) so the sidebar summary can reuse them without importing a
# page module.
DATASET_DISPLAY_NAMES = {
    "resstock_2024_release_2": "ResStock 2024 Release 2",
    "resstock_2025_release_1": "ResStock 2025 Release 1",
    "comstock_2024_release_2": "ComStock 2024 Release 2",
    "comstock_2025_release_3": "ComStock 2025 Release 3",
}


@dataclass(frozen=True)
class StepSpec:
    """One wizard step: its identity plus the predicates that drive the UI."""

    key: str  # machine key: "dataset", "geography", ...
    title: str  # display title: "Dataset", "Geography", ...
    is_complete: Callable[[AppState], bool]
    # Returns None when the user may advance past this step, else a short
    # human-readable reason used to disable the Next button (shown as help text).
    blocks_next: Callable[[AppState], Optional[str]]
    # Short status line for the sidebar panel, e.g. "3 counties". Empty = none.
    summary: Callable[[AppState], str]


# ── Completion predicates (pure functions over AppState) ─────────────────────


def dataset_complete(state: AppState) -> bool:
    return bool(state.dataset_type and state.release and state.selected_upgrades)


def geography_complete(state: AppState) -> bool:
    """Complete when a selection exists or metadata was loaded (the
    whole-dataset, no-filter path sets ``metadata_loaded``)."""
    if not dataset_complete(state):
        return False
    sel = state.effective_geo_selection()
    has_selection = bool(sel["states"] or sel["county_ids"] or sel["puma_ids"])
    return has_selection or state.metadata_loaded


def filters_complete(state: AppState) -> bool:
    """Filters are optional; the step counts as complete once geography is done
    (with or without characteristic filters)."""
    return geography_complete(state)


def rates_complete(state: AppState) -> bool:
    """Complete when every selected scenario has an assigned electricity rate."""
    if not dataset_complete(state):
        return False
    n_scenarios = len(state.selected_upgrades or [])
    assigned = sum(1 for v in state.rate_assignments.values() if v)
    return n_scenarios > 0 and assigned >= n_scenarios


def run_complete(state: AppState) -> bool:
    return bool(state.results_available)


# ── blocks_next policies ─────────────────────────────────────────────────────


def _block_dataset(state: AppState) -> Optional[str]:
    if dataset_complete(state):
        return None
    return "Select a dataset and at least one upgrade scenario"


def _block_geography(state: AppState) -> Optional[str]:
    if not dataset_complete(state):
        return "Select a dataset and at least one upgrade scenario"
    # ComStock metadata is per-county; whole-dataset runs are not possible.
    if (state.dataset_type or "").lower() == "comstock":
        sel = state.effective_geo_selection()
        if not (sel["states"] or sel["county_ids"]):
            return "ComStock requires a state or county selection"
    # ResStock: no geographic filter is a legitimate (whole-dataset) choice.
    return None


def _block_filters(_state: AppState) -> Optional[str]:
    return None  # filters are optional


def _block_rates(state: AppState) -> Optional[str]:
    if any(v for v in state.rate_assignments.values()):
        return None
    return "Assign an electricity rate to at least one scenario"


def _has_results(state: AppState) -> bool:
    if run_complete(state):
        return True
    run_results = st.session_state.get("run_results", {})
    if isinstance(run_results, dict) and run_results.get("df") is not None:
        return True
    return st.session_state.get("results_df") is not None


def _block_run(state: AppState) -> Optional[str]:
    if _has_results(state):
        return None
    return "Run the calculation to view results"


# ── Summaries (sidebar status panel) ─────────────────────────────────────────


def _summary_dataset(state: AppState) -> str:
    if not (state.dataset_type and state.release):
        return ""
    key = f"{state.dataset_type}_{state.release}"
    name = DATASET_DISPLAY_NAMES.get(key, key)
    n = len(state.selected_upgrades or [])
    return f"{name} · {n} scenario(s)" if n else name


def _summary_geography(state: AppState) -> str:
    sel = state.effective_geo_selection()
    parts = []
    if sel["states"]:
        parts.append(f"{len(sel['states'])} state(s)")
    if sel["county_ids"]:
        parts.append(f"{len(sel['county_ids'])} county(ies)")
    if sel["puma_ids"]:
        parts.append(f"{len(sel['puma_ids'])} PUMA(s)")
    if parts:
        return " · ".join(parts)
    return "entire dataset" if state.metadata_loaded else ""


def _summary_filters(state: AppState) -> str:
    n = len(state.active_filters)
    return f"{n} filter(s)" if n else "none"


def _summary_rates(state: AppState) -> str:
    n_scenarios = len(state.selected_upgrades or [])
    if not n_scenarios:
        return ""
    assigned = sum(1 for v in state.rate_assignments.values() if v)
    text = f"{assigned}/{n_scenarios} scenarios assigned"
    n_sweep = sum(len(v) for v in (state.scenario_extra_rates or {}).values())
    n_sweep += len(state.comparison_rates or [])
    if n_sweep:
        text += f" · {n_sweep} sweep rate(s)"
    return text


def _summary_run(state: AppState) -> str:
    return "results ready" if run_complete(state) else ""


def _summary_none(_state: AppState) -> str:
    return ""


# ── Step registry ────────────────────────────────────────────────────────────

STEPS: list[StepSpec] = [
    StepSpec(
        key="dataset",
        title="Dataset",
        is_complete=dataset_complete,
        blocks_next=_block_dataset,
        summary=_summary_dataset,
    ),
    StepSpec(
        key="geography",
        title="Geography",
        is_complete=geography_complete,
        blocks_next=_block_geography,
        summary=_summary_geography,
    ),
    StepSpec(
        key="building_filters",
        title="Building Filters",
        is_complete=filters_complete,
        blocks_next=_block_filters,
        summary=_summary_filters,
    ),
    StepSpec(
        key="rates",
        title="Energy Rates",
        is_complete=rates_complete,
        blocks_next=_block_rates,
        summary=_summary_rates,
    ),
    StepSpec(
        key="run",
        title="Run",
        is_complete=run_complete,
        blocks_next=_block_run,
        summary=_summary_run,
    ),
    StepSpec(
        key="results",
        title="Results",
        is_complete=lambda _s: False,
        blocks_next=lambda _s: None,
        summary=_summary_none,
    ),
]

STEP_TITLES: list[str] = [spec.title for spec in STEPS]


# ── Navigation helpers ───────────────────────────────────────────────────────


def current_step() -> int:
    return int(st.session_state.setdefault(WIZARD_STEP_KEY, 0))


def goto_step(index: int) -> None:
    """Programmatic navigation. Sets the canonical step index and flags the
    header control to re-seed from it on the next run, then reruns.

    The header control uses stable integer option values, so we resync by
    writing its session-state key (not by popping and relying on ``default``),
    which keeps the indicator correct even when a step's completion mark flips.
    """
    st.session_state[WIZARD_STEP_KEY] = index
    st.session_state[WIZARD_SYNC_FLAG] = True
    st.rerun()


def render_gate(message: str, step_index: int, *, key: str) -> None:
    """Actionable prerequisite gate: warning + jump button.

    The caller must ``return`` immediately after calling this.
    """
    st.warning(message)
    if st.button(f"← Go to {STEP_TITLES[step_index]}", key=key):
        goto_step(step_index)


def render_status_panel(state: AppState) -> None:
    """Compact workflow status in the sidebar; each row jumps to its step.

    Each row is a left-justified step-title button with its detail summary on
    an indented line below. CSS is scoped to the keyed container so it does not
    restyle other sidebar buttons.
    """
    st.subheader("Workflow")
    # Left-justify the jump buttons. Each keyed button's wrapper gets a
    # ``st-key-<key>`` class; target those so we override Streamlit's default
    # flex-centering (needs !important) without touching other sidebar buttons.
    st.markdown(
        "<style>"
        '[class*="st-key-status_jump_"] button {'
        " justify-content: flex-start !important; text-align: left !important;"
        " padding-left: 0.25rem !important; }"
        # The button's inner content wrapper is a flex row centered by default —
        # this is the element that actually positions the label, so override it.
        '[class*="st-key-status_jump_"] button > div {'
        " justify-content: flex-start !important; width: 100%; }"
        '[class*="st-key-status_jump_"] button p {'
        " text-align: left !important; width: 100%; }"
        "</style>",
        unsafe_allow_html=True,
    )
    cur = current_step()
    for i, spec in enumerate(STEPS):
        icon = "✅" if spec.is_complete(state) else ("🔵" if i == cur else "⚪")
        if st.button(
            f"{icon} {i + 1}. {spec.title}",
            key=f"status_jump_{spec.key}",
            use_container_width=True,
            type="tertiary",
        ):
            goto_step(i)
        detail = spec.summary(state)
        if detail:
            st.markdown(
                "<div style='margin:-0.5rem 0 0.4rem 1.9rem; font-size:0.8rem;"
                f" opacity:0.65;'>{detail}</div>",
                unsafe_allow_html=True,
            )
