"""
Tests for the applicability summary helpers: filter-independent caching of
per-upgrade applicable-id sets and summary computation with building filters.
"""

from __future__ import annotations

from types import SimpleNamespace

import polars as pl

from buildstock_rate_impact_calculator.ui.applicability import (
    applicable_sets_cache_key,
    compute_summary_cache_key,
    get_applicable_id_sets,
    load_applicability_summary,
)
from buildstock_rate_impact_calculator.ui.state import AppState


class FakeManager:
    """Stand-in for BuildStockDataManager — no network, counts applicability calls."""

    def __init__(self, applicable_by_upgrade: dict[int, set]):
        self.config = SimpleNamespace(building_id_column="bldg_id")
        self.applicable_by_upgrade = applicable_by_upgrade
        self.applicability_calls = 0

    def set_geo_selection(self, **kwargs) -> None:
        pass

    def filter_metadata(self, df: pl.DataFrame, filters: dict) -> pl.DataFrame:
        for col, vals in filters.items():
            df = df.filter(
                pl.col(col).is_in(vals if isinstance(vals, list) else [vals])
            )
        return df

    def get_upgrade_applicability(self, upgrade_id: int, building_ids: list) -> dict:
        self.applicability_calls += 1
        applicable = self.applicable_by_upgrade.get(upgrade_id, set())
        return {bid: bid in applicable for bid in building_ids}


def _app_state() -> AppState:
    state = AppState()
    state.dataset_type = "comstock"
    state.release = "2025_release_3"
    state.selected_upgrades = [0, 1]
    return state


def _metadata() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "bldg_id": [1, 2, 3, 4],
            "in.state": ["CO"] * 4,
            "in.heating_fuel": [
                "NaturalGas",
                "NaturalGas",
                "Electricity",
                "Electricity",
            ],
        }
    )


def test_summary_without_filters():
    state = _app_state()
    mgr = FakeManager({1: {1, 2}})
    summary = load_applicability_summary(state, metadata=_metadata(), manager=mgr)
    assert summary["total_buildings"] == 4
    rows = {r["upgrade_id"]: r for r in summary["rows"]}
    assert rows[0]["n_applicable"] == 4
    assert rows[1]["n_applicable"] == 2
    assert rows[1]["n_total"] == 4


def test_summary_with_filters_intersects_applicable_sets():
    state = _app_state()
    state.active_filters = {"in.heating_fuel": ["Electricity"]}  # bldgs 3, 4
    mgr = FakeManager({1: {1, 2, 3}})
    summary = load_applicability_summary(state, metadata=_metadata(), manager=mgr)
    rows = {r["upgrade_id"]: r for r in summary["rows"]}
    assert summary["total_buildings"] == 2
    assert rows[0]["n_applicable"] == 2
    assert rows[1]["n_applicable"] == 1  # only bldg 3 is both filtered and applicable


def test_applicable_sets_cached_across_filter_changes():
    state = _app_state()
    mgr = FakeManager({1: {1, 2}})
    cache: dict = {}

    load_applicability_summary(
        state, metadata=_metadata(), manager=mgr, applicable_sets_cache=cache
    )
    assert mgr.applicability_calls == 1

    # Changing building filters must not re-fetch applicability from OEDI
    state.active_filters = {"in.heating_fuel": ["NaturalGas"]}
    summary = load_applicability_summary(
        state, metadata=_metadata(), manager=mgr, applicable_sets_cache=cache
    )
    assert mgr.applicability_calls == 1
    rows = {r["upgrade_id"]: r for r in summary["rows"]}
    assert rows[1]["n_applicable"] == 2  # bldgs 1, 2 filtered AND applicable


def test_cache_keys_distinguish_filters_but_not_for_sets():
    state = _app_state()
    sets_key_before = applicable_sets_cache_key(state)
    summary_key_before = compute_summary_cache_key(state)

    state.active_filters = {"in.heating_fuel": ["Electricity"]}
    assert applicable_sets_cache_key(state) == sets_key_before
    assert compute_summary_cache_key(state) != summary_key_before

    state.selected_county_ids = ["G0800690"]
    assert applicable_sets_cache_key(state) != sets_key_before


def test_get_applicable_id_sets_baseline_is_all():
    state = _app_state()
    mgr = FakeManager({1: {2}})
    sets = get_applicable_id_sets(state, mgr, [1, 2, 3])
    assert sets[0] == {1, 2, 3}
    assert sets[1] == {2}


def test_summary_filters_ignore_columns_missing_from_metadata():
    state = _app_state()
    state.active_filters = {"in.not_a_column": ["x"]}
    mgr = FakeManager({1: {1}})
    summary = load_applicability_summary(state, metadata=_metadata(), manager=mgr)
    assert "error" not in summary
    assert summary["total_buildings"] == 4
