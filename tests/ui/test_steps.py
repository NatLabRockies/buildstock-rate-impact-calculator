"""Unit tests for the wizard step registry: completion predicates,
blocks_next policies, and sidebar summaries.

These are pure functions over ``AppState`` and do not require a Streamlit
runtime (the only ``st.session_state`` access, in ``_block_run``'s fallback,
is avoided by setting ``results_available`` on the state under test).
"""

from __future__ import annotations

from buildstock_rate_impact_calculator.ui import steps
from buildstock_rate_impact_calculator.ui.state import AppState


def _dataset_state() -> AppState:
    s = AppState()
    s.dataset_type = "resstock"
    s.release = "2024_release_2"
    s.selected_upgrades = [0, 1]
    return s


# ── dataset_complete ─────────────────────────────────────────────────────────


class TestDatasetComplete:
    def test_fresh_state_incomplete(self) -> None:
        assert steps.dataset_complete(AppState()) is False

    def test_full_selection_complete(self) -> None:
        assert steps.dataset_complete(_dataset_state()) is True

    def test_no_upgrades_incomplete(self) -> None:
        s = _dataset_state()
        s.selected_upgrades = None
        assert steps.dataset_complete(s) is False


# ── geography_complete ───────────────────────────────────────────────────────


class TestGeographyComplete:
    def test_requires_dataset(self) -> None:
        s = AppState()
        s.selected_states = ["CO"]
        assert steps.geography_complete(s) is False

    def test_with_selection(self) -> None:
        s = _dataset_state()
        s.selected_states = ["CO"]
        assert steps.geography_complete(s) is True

    def test_metadata_loaded_no_selection(self) -> None:
        s = _dataset_state()
        s.metadata_loaded = True
        assert steps.geography_complete(s) is True

    def test_dataset_only_incomplete(self) -> None:
        assert steps.geography_complete(_dataset_state()) is False


# ── rates_complete ───────────────────────────────────────────────────────────


class TestRatesComplete:
    def test_all_assigned(self) -> None:
        s = _dataset_state()  # scenarios [0, 1]
        s.rate_assignments = {"baseline": "R1", "upgrade_1": "R1"}
        assert steps.rates_complete(s) is True

    def test_partial_assigned_incomplete(self) -> None:
        s = _dataset_state()
        s.rate_assignments = {"baseline": "R1"}
        assert steps.rates_complete(s) is False

    def test_empty_assignment_incomplete(self) -> None:
        s = _dataset_state()
        s.rate_assignments = {"baseline": "", "upgrade_1": ""}
        assert steps.rates_complete(s) is False


# ── run_complete ─────────────────────────────────────────────────────────────


class TestRunComplete:
    def test_mirrors_results_available(self) -> None:
        s = _dataset_state()
        assert steps.run_complete(s) is False
        s.results_available = True
        assert steps.run_complete(s) is True


# ── blocks_next policies ─────────────────────────────────────────────────────


class TestBlocksNext:
    def test_dataset_blocked_until_complete(self) -> None:
        assert steps._block_dataset(AppState()) is not None
        assert steps._block_dataset(_dataset_state()) is None

    def test_geography_resstock_no_selection_allowed(self) -> None:
        # ResStock with no geo filter is a legitimate whole-dataset run.
        assert steps._block_geography(_dataset_state()) is None

    def test_geography_comstock_requires_selection(self) -> None:
        s = _dataset_state()
        s.dataset_type = "comstock"
        s.release = "2024_release_2"
        assert steps._block_geography(s) is not None

    def test_geography_comstock_with_county_allowed(self) -> None:
        s = _dataset_state()
        s.dataset_type = "comstock"
        s.release = "2024_release_2"
        s.selected_county_ids = ["G0800690"]
        assert steps._block_geography(s) is None

    def test_rates_blocked_until_one_assigned(self) -> None:
        s = _dataset_state()
        assert steps._block_rates(s) is not None
        s.rate_assignments = {"baseline": "R1"}
        assert steps._block_rates(s) is None

    def test_run_unblocked_when_results_available(self) -> None:
        s = _dataset_state()
        s.results_available = True
        assert steps._block_run(s) is None


# ── summaries ────────────────────────────────────────────────────────────────


class TestSummaries:
    def test_rates_summary_counts(self) -> None:
        s = _dataset_state()
        s.rate_assignments = {"baseline": "R1"}
        assert "1/2 scenarios assigned" in steps._summary_rates(s)

    def test_rates_summary_includes_sweep(self) -> None:
        s = _dataset_state()
        s.rate_assignments = {"baseline": "R1", "upgrade_1": "R1"}
        s.comparison_rates = ["R2"]
        summary = steps._summary_rates(s)
        assert "2/2 scenarios assigned" in summary
        assert "sweep" in summary

    def test_geography_summary_states(self) -> None:
        s = _dataset_state()
        s.selected_states = ["CO", "TX"]
        assert "2 state" in steps._summary_geography(s)

    def test_dataset_summary_has_name_and_scenarios(self) -> None:
        summary = steps._summary_dataset(_dataset_state())
        assert "ResStock 2024 Release 2" in summary
        assert "2 scenario" in summary


# ── registry integrity ───────────────────────────────────────────────────────


def test_registry_shape() -> None:
    assert len(steps.STEPS) == 6
    assert steps.STEP_TITLES == [
        "Dataset",
        "Geography",
        "Building Filters",
        "Energy Rates",
        "Run",
        "Results",
    ]
    # Titles must be unique — segmented_control requires distinct option labels.
    assert len(set(steps.STEP_TITLES)) == len(steps.STEP_TITLES)
