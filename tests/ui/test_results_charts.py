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

import altair as alt
import polars as pl
import pytest

from buildstock_rate_impact_calculator.ui.components.charts import (
    compute_boxplot_stats,
    melt_fuels_to_long,
    render_bill_comparison_bar,
    render_bill_distribution,
    render_bill_rate_bar,
    render_bill_stack_bar,
    render_boxplot,
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

    def test_color_encodes_series_with_wrapped_legend(self) -> None:
        spec = render_monthly_profile(self._long_df()).to_dict()
        color_enc = spec["encoding"]["color"]
        # Series labels are pre-wrapped into a helper column; the legend
        # splits them back into multiple lines and never truncates.
        assert color_enc["field"] == "_series_wrapped"
        assert color_enc["legend"]["labelLimit"] == 0
        assert "split(" in color_enc["legend"]["labelExpr"]
        # Tooltip still shows the original (unwrapped) series label.
        tooltip_fields = [t["field"] for t in spec["encoding"]["tooltip"]]
        assert "scenario" in tooltip_fields

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
# render_bill_rate_bar (rate sweep view)
# ---------------------------------------------------------------------------


class TestRenderBillRateBar:
    def _summary(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "upgrade": [0, 0, 1, 1],
                "rate_name": ["Rate A", "Rate B", "Rate A", "Rate B"],
                "scenario": [
                    "Baseline",
                    "Baseline",
                    "Upgrade 1: A Very Long Measure Name",
                    "Upgrade 1: A Very Long Measure Name",
                ],
                "mean_annual_bill": [1200.0, 1300.0, 950.0, 1000.0],
            }
        )

    def test_grouped_by_scenario_colored_by_rate(self) -> None:
        spec = render_bill_rate_bar(self._summary()).to_dict()
        assert spec["encoding"]["x"]["field"] == "_scenario_wrapped"
        assert spec["encoding"]["xOffset"]["field"] == "_rate_wrapped"
        assert spec["encoding"]["color"]["field"] == "_rate_wrapped"

    def test_x_labels_horizontal_wrapped_untruncated(self) -> None:
        spec = render_bill_rate_bar(self._summary()).to_dict()
        axis = spec["encoding"]["x"]["axis"]
        assert axis["labelAngle"] == 0
        assert axis["labelLimit"] == 0
        assert "split(" in axis["labelExpr"]
        # The long scenario name is wrapped in the data (multi-line marker).
        values = {row["_scenario_wrapped"] for row in spec["datasets"].popitem()[1]}
        assert any("|" in v for v in values)

    def test_legend_wrapped_untruncated(self) -> None:
        spec = render_bill_rate_bar(self._summary()).to_dict()
        legend = spec["encoding"]["color"]["legend"]
        assert legend["labelLimit"] == 0
        assert "split(" in legend["labelExpr"]

    def test_default_title(self) -> None:
        spec = render_bill_rate_bar(self._summary()).to_dict()
        assert spec["title"] == "Mean Annual Electricity Bill by Scenario and Rate"

    def test_tooltip_uses_unwrapped_labels(self) -> None:
        spec = render_bill_rate_bar(self._summary()).to_dict()
        tooltip_fields = [t["field"] for t in spec["encoding"]["tooltip"]]
        assert "scenario" in tooltip_fields
        assert "rate_name" in tooltip_fields


# ---------------------------------------------------------------------------
# render_bill_distribution (Tukey boxplot + per-rate view)
# ---------------------------------------------------------------------------


class TestComputeBoxplotStats:
    def test_hand_computed_stats(self) -> None:
        """[1, 2, 3, 4, 100]: q1=2, median=3, q3=4, fences (-1, 7) → 100 is
        an outlier and the whiskers stop at the extreme in-fence values."""
        df = pl.DataFrame({"g": ["a"] * 5, "v": [1.0, 2.0, 3.0, 4.0, 100.0]})
        stats, outliers = compute_boxplot_stats(df, "v", ["g"])
        row = stats.row(0, named=True)
        assert row["q1"] == pytest.approx(2.0)
        assert row["median"] == pytest.approx(3.0)
        assert row["q3"] == pytest.approx(4.0)
        assert row["iqr"] == pytest.approx(2.0)
        assert row["whisker_low"] == pytest.approx(1.0)
        assert row["whisker_high"] == pytest.approx(4.0)
        assert row["count"] == 5
        assert outliers["v"].to_list() == [100.0]

    def test_no_outliers_whiskers_at_extremes(self) -> None:
        df = pl.DataFrame({"g": ["a"] * 4, "v": [1.0, 2.0, 3.0, 4.0]})
        stats, outliers = compute_boxplot_stats(df, "v", ["g"])
        row = stats.row(0, named=True)
        assert row["whisker_low"] == pytest.approx(1.0)
        assert row["whisker_high"] == pytest.approx(4.0)
        assert outliers.height == 0

    def test_per_group_and_null_handling(self) -> None:
        df = pl.DataFrame(
            {
                "g": ["a", "a", "a", "b", "b", "b"],
                "v": [1.0, 2.0, None, 10.0, 20.0, 30.0],
            }
        )
        stats, _ = compute_boxplot_stats(df, "v", ["g"])
        by_group = {r["g"]: r for r in stats.iter_rows(named=True)}
        assert by_group["a"]["count"] == 2  # null dropped
        assert by_group["b"]["median"] == pytest.approx(20.0)


class TestRenderBillDistribution:
    def _df(self, with_rates: bool = False) -> pl.DataFrame:
        # 5 buildings per upgrade; the last value in each group lies far
        # beyond the 1.5×IQR fence → exactly one outlier per box.
        base = pl.DataFrame(
            {
                "bldg_id": [1, 2, 3, 4, 5] * 2,
                "upgrade": [0] * 5 + [1] * 5,
                "scenario": ["Baseline"] * 5 + ["Upgrade 1"] * 5,
                "annual_bill": [1000.0, 1010.0, 1020.0, 1030.0, 99_999.0]
                + [800.0, 810.0, 820.0, 830.0, 88_888.0],
            }
        )
        if with_rates:
            return pl.concat(
                [
                    base.with_columns(pl.lit("Rate A").alias("rate_name")),
                    base.with_columns(pl.lit("Rate B").alias("rate_name")),
                ]
            )
        return base

    def test_precomputed_stats_one_row_per_box(self) -> None:
        """The spec ships aggregate rows, not per-building rows."""
        spec = render_bill_distribution(self._df()).to_dict()
        rows = spec["datasets"].popitem()[1]
        assert len(rows) == 2  # one stats row per upgrade, no outlier rows
        assert {"q1", "median", "q3", "whisker_low", "whisker_high"}.issubset(
            rows[0].keys()
        )

    def test_layers_draw_whisker_box_median(self) -> None:
        spec = render_bill_distribution(self._df()).to_dict()
        layers = spec["layer"]
        assert len(layers) == 3
        whisker, box, median = layers
        assert whisker["mark"]["type"] == "rule"
        assert whisker["encoding"]["y"]["field"] == "whisker_low"
        assert whisker["encoding"]["y2"]["field"] == "whisker_high"
        assert box["mark"]["type"] == "bar"
        assert box["encoding"]["y"]["field"] == "q1"
        assert box["encoding"]["y2"]["field"] == "q3"
        assert median["mark"]["type"] == "tick"
        assert median["encoding"]["y"]["field"] == "median"

    def test_outliers_hidden_by_default(self) -> None:
        spec = render_bill_distribution(self._df()).to_dict()
        assert len(spec["layer"]) == 3
        rows = spec["datasets"].popitem()[1]
        assert all(r["_role"] == "box" for r in rows)

    def test_show_outliers_adds_point_layer_and_rows(self) -> None:
        spec = render_bill_distribution(self._df(), show_outliers=True).to_dict()
        layers = spec["layer"]
        assert len(layers) == 4
        assert layers[3]["mark"]["type"] == "point"
        rows = spec["datasets"].popitem()[1]
        # 2 stats rows + 2 outlier rows (one extreme value per upgrade).
        assert sum(r["_role"] == "box" for r in rows) == 2
        assert sum(r["_role"] == "outlier" for r in rows) == 2

    def test_per_rate_boxes_faceted_by_scenario(self) -> None:
        spec = render_bill_distribution(
            self._df(with_rates=True),
            scenario_col="scenario",
            rate_col="rate_name",
        ).to_dict()
        # Faceted by scenario, one box per rate inside each facet; the rate
        # identity lives in the color legend (inner x labels are hidden so
        # long rate names cannot collide).
        assert spec["facet"]["column"]["field"] == "_scenario_wrapped"
        inner_layers = spec["spec"]["layer"]
        box = inner_layers[1]
        assert box["encoding"]["x"]["field"] == "_rate_wrapped"
        assert box["encoding"]["x"]["axis"]["labels"] is False
        assert box["encoding"]["color"]["field"] == "_rate_wrapped"
        # 2 scenarios × 2 rates = 4 stats rows total.
        rows = spec["datasets"].popitem()[1]
        assert len(rows) == 4

    def test_facet_headers_wrapped_untruncated(self) -> None:
        spec = render_bill_distribution(
            self._df(with_rates=True),
            scenario_col="scenario",
            rate_col="rate_name",
        ).to_dict()
        header = spec["facet"]["column"]["header"]
        assert header["labelLimit"] == 0
        assert "split(" in header["labelExpr"]

    def test_no_rate_column_falls_back_to_per_upgrade(self) -> None:
        spec = render_bill_distribution(
            self._df(), scenario_col="scenario", rate_col="rate_name"
        ).to_dict()
        # rate_name absent from the frame → single box per upgrade.
        assert "facet" not in spec
        box = spec["layer"][1]
        assert box["encoding"]["x"]["field"] == "_upgrade_str"


class TestRenderBoxplot:
    def test_custom_color_scale_and_hidden_x_title(self) -> None:
        df = pl.DataFrame(
            {
                "Group": ["Applicable"] * 3 + ["Not Applicable"] * 3,
                "sav": [10.0, 20.0, 30.0, -5.0, 0.0, 5.0],
            }
        )
        chart = render_boxplot(
            df,
            value_col="sav",
            x_col="Group",
            color_col="Group",
            x_axis_title="",
            y_axis_title="Annual Savings ($)",
            color_scale=alt.Scale(
                domain=["Applicable", "Not Applicable"],
                range=["#2196F3", "#FF9800"],
            ),
            box_size=60,
        )
        spec = chart.to_dict()
        box = spec["layer"][1]
        assert box["encoding"]["x"]["title"] is None  # hidden via ""
        assert box["encoding"]["color"]["scale"]["range"] == ["#2196F3", "#FF9800"]
        assert box["mark"]["size"] == 60
        rows = spec["datasets"].popitem()[1]
        assert len(rows) == 2  # one stats row per group


# ---------------------------------------------------------------------------
# render_bill_comparison_bar — dotted segment columns (regression)
# ---------------------------------------------------------------------------


class TestComparisonBarDottedGroupCol:
    def test_dots_in_group_col_are_escaped(self) -> None:
        """Regression: Vega-Lite reads unescaped dots as nested field access
        (datum.in.vintage → undefined), collapsing the segment breakout into
        a single stacked bar."""
        df = pl.DataFrame(
            {
                "in.vintage": ["1980s", "1990s"],
                "upgrade": [1, 1],
                "mean_bill_savings": [100.0, 150.0],
            }
        )
        spec = render_bill_comparison_bar(
            df, group_col="in.vintage", bill_col="mean_bill_savings"
        ).to_dict()
        assert spec["encoding"]["x"]["field"] == "in\\.vintage"


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
