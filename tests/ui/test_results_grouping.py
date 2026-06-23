"""Phase 7: results-page grouping & pills filter tests.

Streamlit's ``st.pills`` widget isn't itself testable from pytest without a
full app harness — but the filter *behavior* the pills drive is a thin
DataFrame transform. We exercise that transform here against the
``filter_df_by_group_values`` helper, plus a small integration check that
``summarize_bills`` produces a per-group row when a grouping column is
selected and the df has been pre-filtered.

The two ``groupable_columns`` tests required by the plan live in
``tests/billing/test_results_aggregation.py``.
"""

from __future__ import annotations

import polars as pl

from buildstock_rate_impact_calculator.billing.results import (
    filter_df_by_group_values,
    summarize_bills,
)


def _results_df() -> pl.DataFrame:
    """Synthetic per-building results across two states and two vintages."""
    return pl.DataFrame(
        {
            "bldg_id": [1, 2, 3, 4],
            "upgrade": [0, 0, 0, 0],
            "annual_bill": [1000.0, 1500.0, 2000.0, 1200.0],
            "weight": [1.0, 1.0, 1.0, 1.0],
            "in.state": ["CO", "CA", "CO", "CA"],
            "in.vintage": ["1980s", "1980s", "2000s", "2000s"],
        }
    )


# ---------------------------------------------------------------------------
# filter_df_by_group_values
# ---------------------------------------------------------------------------


class TestFilterDfByGroupValues:
    def test_no_pills_selected_yields_full_df(self) -> None:
        df = _results_df()
        out = filter_df_by_group_values(df, group_col="in.state", selected=[])
        assert out.height == df.height
        assert set(out["in.state"].to_list()) == {"CO", "CA"}

    def test_no_group_col_yields_full_df(self) -> None:
        df = _results_df()
        out = filter_df_by_group_values(df, group_col=None, selected=["CO"])
        assert out.height == df.height

    def test_pills_filter_df_to_selected_values(self) -> None:
        df = _results_df()
        out = filter_df_by_group_values(df, group_col="in.state", selected=["CO"])
        assert out.height == 2
        assert set(out["in.state"].to_list()) == {"CO"}

    def test_multiple_selected_values_keeps_all_matching(self) -> None:
        df = _results_df()
        out = filter_df_by_group_values(
            df, group_col="in.vintage", selected=["1980s", "2000s"]
        )
        assert out.height == 4

    def test_unknown_group_col_returns_df_unchanged(self) -> None:
        df = _results_df()
        out = filter_df_by_group_values(
            df, group_col="in.does_not_exist", selected=["foo"]
        )
        # No crash; nothing matches → returns the original df (no-op).
        assert out.height == df.height


# ---------------------------------------------------------------------------
# Integration: filter → summarize_bills
# ---------------------------------------------------------------------------


class TestGroupingWithFilteredDf:
    def test_grouping_with_filtered_df_groups_summary_by_column(self) -> None:
        """After pills filter to a subset, ``summarize_bills(group_by=col)``
        emits one summary row per (upgrade, group value) within that subset.
        """
        df = _results_df()
        # User selects vintage as the grouping column, then toggles only 1980s.
        filtered = filter_df_by_group_values(
            df, group_col="in.vintage", selected=["1980s"]
        )
        summary = summarize_bills(
            filtered, dataset_type="resstock", group_by="in.vintage"
        )
        assert summary.height == 1
        assert summary["in.vintage"].to_list() == ["1980s"]
        # Mean of [1000, 1500] = 1250
        assert summary["mean_annual_bill"].to_list() == [1250.0]
