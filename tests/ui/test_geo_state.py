"""
Tests for AppState geographic-selection helpers: GISJOIN normalization,
effective_geo_selection resolution, and apply_geo_filter behavior.
"""

from __future__ import annotations

import polars as pl

from buildstock_rate_impact_calculator.ui.state import AppState


# ===========================================================================
# _to_gisjoin
# ===========================================================================


class TestToGisjoin:
    def test_county_fips_converted(self) -> None:
        assert AppState._to_gisjoin("08069", "county") == "G0800690"

    def test_puma_code_converted(self) -> None:
        assert AppState._to_gisjoin("0800803", "puma") == "G08000803"

    def test_gisjoin_passthrough(self) -> None:
        assert AppState._to_gisjoin("G0800690", "county") == "G0800690"
        assert AppState._to_gisjoin("G08000803", "puma") == "G08000803"

    def test_unrecognized_returned_unchanged(self) -> None:
        assert AppState._to_gisjoin("XX", "county") == "XX"


# ===========================================================================
# effective_geo_selection
# ===========================================================================


class TestEffectiveGeoSelection:
    def test_empty_state(self) -> None:
        sel = AppState().effective_geo_selection()
        assert sel == {"states": [], "county_ids": [], "puma_ids": []}

    def test_map_selections_used_directly(self) -> None:
        state = AppState()
        state.selected_states = ["CO"]
        state.selected_county_ids = ["G0800690"]
        sel = state.effective_geo_selection()
        assert sel["states"] == ["CO"]
        assert sel["county_ids"] == ["G0800690"]

    def test_legacy_county_fips_normalized(self) -> None:
        state = AppState()
        state.geo_level = "county"
        state.selected_geo_ids = ["08069"]
        sel = state.effective_geo_selection()
        assert sel["county_ids"] == ["G0800690"]
        assert sel["states"] == []

    def test_legacy_state_ids(self) -> None:
        state = AppState()
        state.geo_level = "state"
        state.selected_geo_ids = ["CO", "NY"]
        sel = state.effective_geo_selection()
        assert sel["states"] == ["CO", "NY"]

    def test_map_selection_takes_priority_over_legacy(self) -> None:
        state = AppState()
        state.selected_states = ["TX"]
        state.geo_level = "county"
        state.selected_geo_ids = ["08069"]
        sel = state.effective_geo_selection()
        assert sel["states"] == ["TX"]
        assert sel["county_ids"] == []


# ===========================================================================
# apply_geo_filter
# ===========================================================================


def _meta() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "bldg_id": [1, 2, 3, 4],
            "in.state": ["CO", "CO", "AK", "TX"],
            "in.county": ["G0800690", "G0800010", "G0200130", "G4802810"],
            "in.puma": ["G08000803", "G08000801", "G02000101", "G48002000"],
        }
    )


class TestApplyGeoFilter:
    def test_no_selection_returns_all(self) -> None:
        assert len(AppState().apply_geo_filter(_meta())) == 4

    def test_state_filter(self) -> None:
        state = AppState()
        state.selected_states = ["CO"]
        assert AppState.apply_geo_filter(state, _meta())["bldg_id"].to_list() == [1, 2]

    def test_county_or_puma_logic(self) -> None:
        state = AppState()
        state.selected_county_ids = ["G0800690"]
        state.selected_puma_ids = ["G02000101"]
        result = state.apply_geo_filter(_meta())
        assert set(result["bldg_id"].to_list()) == {1, 3}

    def test_legacy_county_fips_matches_gisjoin_metadata(self) -> None:
        state = AppState()
        state.geo_level = "county"
        state.selected_geo_ids = ["08069"]  # FIPS for G0800690
        result = state.apply_geo_filter(_meta())
        assert result["bldg_id"].to_list() == [1]

    def test_missing_columns_no_filter(self) -> None:
        state = AppState()
        state.selected_county_ids = ["G0800690"]
        df = _meta().drop(["in.county", "in.puma"])
        assert len(state.apply_geo_filter(df)) == 4


# ===========================================================================
# geo_cache_key — Phase 4: additional_columns must invalidate cache
# ===========================================================================


class TestGeoCacheKey:
    def test_changes_when_dataset_changes(self) -> None:
        from buildstock_rate_impact_calculator.ui.metadata_session import (
            geo_cache_key,
        )

        a = AppState()
        a.dataset_type = "resstock"
        a.release = "2025_release_1"
        b = AppState()
        b.dataset_type = "comstock"
        b.release = "2025_release_3"
        assert geo_cache_key(a) != geo_cache_key(b)

    def test_includes_additional_columns(self) -> None:
        """Cache must invalidate when additional_columns changes."""
        from buildstock_rate_impact_calculator.ui.metadata_session import (
            geo_cache_key,
        )

        a = AppState()
        a.dataset_type = "resstock"
        a.release = "2025_release_1"

        baseline_key = geo_cache_key(a)
        a.additional_columns = ["in.vintage"]
        assert geo_cache_key(a) != baseline_key

    def test_order_independent_for_additional_columns(self) -> None:
        from buildstock_rate_impact_calculator.ui.metadata_session import (
            geo_cache_key,
        )

        a = AppState()
        a.dataset_type = "resstock"
        a.release = "2025_release_1"
        a.additional_columns = ["in.vintage", "in.heating_setpoint"]

        b = AppState()
        b.dataset_type = "resstock"
        b.release = "2025_release_1"
        b.additional_columns = ["in.heating_setpoint", "in.vintage"]

        assert geo_cache_key(a) == geo_cache_key(b)
