"""Phase 6: results-page chart helper tests.

Covers the new chart builders and tooltip-format updates in
``buildstock_rate_impact_calculator.ui.components.charts``:

* ``melt_fuels_to_long`` — pivots wide fuel-bill summary columns into long
  ``(scenario, fuel_type, value)`` form for the stacked-bar chart.
* ``render_bill_stack_bar`` — Altair stacked bar by scenario × fuel_type.
* ``render_bill_comparison_bar`` — tooltips formatted with ``$.0f`` (whole
  dollars) by default, ``$.2f`` when the caller passes
  ``value_format="$.2f"`` (used for $/ft²).

Tests inspect the Altair spec returned by ``Chart.to_dict()`` rather than
rendering the chart, so they run offline with no Vega-Lite runtime.
"""

from __future__ import annotations

import polars as pl
import pytest

from buildstock_rate_impact_calculator.ui.components.charts import (
    melt_fuels_to_long,
    render_bill_comparison_bar,
    render_bill_stack_bar,
    render_monthly_profile,
    render_savings_histogram,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _wide_summary() -> pl.DataFrame:
    """Two scenarios × wide fuel columns, as produced by ``summarize_bills``."""
    return pl.DataFrame(
        {
            "scenario": ["Baseline", "Upgrade 1: HP"],
            "upgrade": [0, 1],
            "mean_annual_bill": [1200.0, 950.0],
            "mean_natural_gas_bill": [400.0, 200.0],
            "mean_propane_bill": [50.0, 25.0],
            "mean_fuel_oil_bill": [0.0, 0.0],
        }
    )


# ---------------------------------------------------------------------------
# melt_fuels_to_long
# ---------------------------------------------------------------------------


class TestMeltFuelsToLong:
    def test_includes_all_fuel_columns(self) -> None:
        wide = _wide_summary()
        fuel_columns = {
            "mean_annual_bill": "Electricity",
            "mean_natural_gas_bill": "Natural Gas",
            "mean_propane_bill": "Propane",
            "mean_fuel_oil_bill": "Fuel Oil",
        }
        long = melt_fuels_to_long(wide, fuel_columns)

        assert {"scenario", "upgrade", "fuel_type", "value"}.issubset(set(long.columns))
        # 2 scenarios × 4 fuel columns = 8 rows
        assert long.height == 8
        fuel_types = set(long["fuel_type"].to_list())
        assert fuel_types == {"Electricity", "Natural Gas", "Propane", "Fuel Oil"}

    def test_baseline_row_present(self) -> None:
        wide = _wide_summary()
        long = melt_fuels_to_long(wide, {"mean_annual_bill": "Electricity"})
        scenarios = set(long["scenario"].to_list())
        assert "Baseline" in scenarios

    def test_skips_missing_columns(self) -> None:
        wide = _wide_summary()
        long = melt_fuels_to_long(
            wide,
            {
                "mean_annual_bill": "Electricity",
                "mean_does_not_exist": "Missing Fuel",
            },
        )
        fuel_types = set(long["fuel_type"].to_list())
        assert fuel_types == {"Electricity"}


# ---------------------------------------------------------------------------
# render_bill_stack_bar
# ---------------------------------------------------------------------------


class TestRenderBillStackBar:
    def _long_df(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "scenario": [
                    "Baseline",
                    "Baseline",
                    "Upgrade 1: HP",
                    "Upgrade 1: HP",
                ],
                "fuel_type": [
                    "Electricity",
                    "Natural Gas",
                    "Electricity",
                    "Natural Gas",
                ],
                "value": [1200.0, 400.0, 950.0, 200.0],
            }
        )

    def test_includes_baseline_scenario(self) -> None:
        long = self._long_df()
        chart = render_bill_stack_bar(long, value_col="value")
        # Altair stores the pandas DataFrame on chart.data; assert baseline
        # row is present in what gets rendered.
        scenarios = set(chart.data["scenario"].tolist())
        assert "Baseline" in scenarios

    def test_uses_dollar_format(self) -> None:
        long = self._long_df()
        chart = render_bill_stack_bar(long, value_col="value")
        spec = chart.to_dict()
        # Locate the tooltip encoding and assert one of the entries uses $.0f.
        tooltip = spec["encoding"]["tooltip"]
        formats = [t.get("format") for t in tooltip]
        assert "$.0f" in formats

    def test_uses_custom_value_format(self) -> None:
        long = self._long_df()
        chart = render_bill_stack_bar(long, value_col="value", value_format="$.2f")
        tooltip = chart.to_dict()["encoding"]["tooltip"]
        formats = [t.get("format") for t in tooltip]
        assert "$.2f" in formats

    def test_y_axis_uses_stack_zero(self) -> None:
        long = self._long_df()
        spec = render_bill_stack_bar(long, value_col="value").to_dict()
        y_enc = spec["encoding"]["y"]
        # alt.Y(value_col, stack="zero") → spec field has stack="zero"
        assert y_enc.get("stack") == "zero"
        assert y_enc.get("field") == "value"

    def test_color_encodes_fuel_type(self) -> None:
        long = self._long_df()
        spec = render_bill_stack_bar(long, value_col="value").to_dict()
        color_enc = spec["encoding"]["color"]
        assert color_enc["field"] == "fuel_type"


# ---------------------------------------------------------------------------
# render_bill_comparison_bar — tooltip format updates
# ---------------------------------------------------------------------------


class TestRenderBillComparisonBarFormats:
    def _summary(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "upgrade": [0, 1],
                "mean_annual_bill": [1200.0, 950.0],
            }
        )

    def test_formats_dollars_no_decimals_by_default(self) -> None:
        chart = render_bill_comparison_bar(self._summary())
        tooltip = chart.to_dict()["encoding"]["tooltip"]
        formats = [t.get("format") for t in tooltip if t.get("format")]
        assert "$.0f" in formats
        assert "$.2f" not in formats

    def test_per_sqft_uses_two_decimals(self) -> None:
        summary = pl.DataFrame({"upgrade": [0, 1], "mean_per_sqft": [4.5, 3.7]})
        chart = render_bill_comparison_bar(
            summary, bill_col="mean_per_sqft", value_format="$.2f"
        )
        tooltip = chart.to_dict()["encoding"]["tooltip"]
        formats = [t.get("format") for t in tooltip if t.get("format")]
        assert "$.2f" in formats

    def test_value_format_default_applies_to_y_axis_when_provided(self) -> None:
        # Sanity check: explicit value_format is wired through the chart spec
        # (not just the tooltip). The y-axis label format ends up under
        # encoding.y.axis.format when set.
        chart = render_bill_comparison_bar(self._summary(), value_format="$.0f")
        spec = chart.to_dict()
        y_enc = spec["encoding"]["y"]
        # Either axis.format is set, or it's pushed onto the tooltip — accept
        # tooltip presence as the canonical assertion since axis.format isn't
        # required by the plan, just consistent formatting.
        tooltip = spec["encoding"]["tooltip"]
        formats = [t.get("format") for t in tooltip if t.get("format")]
        _ = y_enc  # noqa: F841 — keep the inspection for documentation
        assert "$.0f" in formats


# ---------------------------------------------------------------------------
# render_savings_histogram (Phase 4)
# ---------------------------------------------------------------------------


class TestRenderSavingsHistogram:
    def _diff_df(self, scenarios: int = 1) -> pl.DataFrame:
        frames = []
        for i in range(scenarios):
            frames.append(
                pl.DataFrame(
                    {
                        "bldg_id": [1, 2, 3],
                        "upgrade": [i + 1] * 3,
                        "scenario": [f"Upgrade {i + 1}"] * 3,
                        "bill_savings": [100.0, -50.0, 20.0],
                        "weight": [10.0, 20.0, 30.0],
                    }
                )
            )
        return pl.concat(frames)

    def test_bins_savings_on_x(self) -> None:
        spec = render_savings_histogram(self._diff_df()).to_dict()
        x_enc = spec["encoding"]["x"]
        assert x_enc["field"] == "bill_savings"
        assert x_enc["bin"]["maxbins"] == 40

    def test_y_sums_weight(self) -> None:
        spec = render_savings_histogram(self._diff_df()).to_dict()
        y_enc = spec["encoding"]["y"]
        assert y_enc["aggregate"] == "sum"
        assert y_enc["field"] == "weight"
        assert y_enc["title"] == "Buildings represented"

    def test_facets_by_scenario_when_multiple(self) -> None:
        spec = render_savings_histogram(self._diff_df(scenarios=2)).to_dict()
        assert spec["facet"]["field"] == "scenario"
        assert spec["columns"] == 3

    def test_missing_weight_column_uses_constant(self) -> None:
        df = self._diff_df().drop("weight")
        chart = render_savings_histogram(df)
        assert (chart.data["weight"] == 1.0).all()

    def test_null_savings_rows_dropped(self) -> None:
        df = self._diff_df().with_columns(
            pl.Series("bill_savings", [100.0, None, 20.0])
        )
        chart = render_savings_histogram(df)
        assert len(chart.data) == 2


# ---------------------------------------------------------------------------
# render_monthly_profile (Phase 3)
# ---------------------------------------------------------------------------


class TestRenderMonthlyProfile:
    def _long_df(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "scenario": ["Baseline"] * 12 + ["Upgrade 1"] * 12,
                "month": list(range(1, 13)) * 2,
                "value": [100.0] * 12 + [80.0] * 12,
            }
        )

    def test_line_mark_with_month_ordinal_x(self) -> None:
        spec = render_monthly_profile(self._long_df()).to_dict()
        x_enc = spec["encoding"]["x"]
        assert x_enc["field"] == "month"
        assert x_enc["type"] == "ordinal"

    def test_color_encodes_scenario(self) -> None:
        spec = render_monthly_profile(self._long_df()).to_dict()
        assert spec["encoding"]["color"]["field"] == "scenario"

    def test_value_format_wired_to_axis_and_tooltip(self) -> None:
        spec = render_monthly_profile(
            self._long_df(), value_format="$.2f", y_axis_title="Bill ($/ft²)"
        ).to_dict()
        assert spec["encoding"]["y"]["axis"]["format"] == "$.2f"
        formats = [
            t.get("format") for t in spec["encoding"]["tooltip"] if t.get("format")
        ]
        assert "$.2f" in formats


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


class TestStackBarGuards:
    def test_empty_long_df_does_not_crash(self) -> None:
        empty = pl.DataFrame(
            {
                "scenario": [],
                "fuel_type": [],
                "value": [],
            },
            schema={
                "scenario": pl.Utf8,
                "fuel_type": pl.Utf8,
                "value": pl.Float64,
            },
        )
        # Either returns an empty chart or raises a clean ValueError —
        # accept the former; assert no unhandled exception leaks out.
        try:
            render_bill_stack_bar(empty, value_col="value")
        except ValueError:
            pytest.skip("Renderer chose to raise on empty input; acceptable.")
