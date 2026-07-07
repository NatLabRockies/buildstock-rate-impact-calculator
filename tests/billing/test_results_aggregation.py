"""Phase 5: results aggregation tests.

Covers the dataset-aware behavior of :func:`summarize_bills`,
:func:`compute_bill_savings`, and :func:`add_cost_per_sqft` in
``buildstock_rate_impact_calculator.billing.results``.

Synthetic fixtures match the shape produced by ``page_05_run`` /
``UtilityBillCalculator.calculate_batch_bills``:

* **ResStock** rows: one row per (bldg_id, upgrade); ``weight`` is the scalar
  dataset weight (the same on every row); ``sqft`` is ``None``; ``annual_bill``
  is the per-building dollar amount (pre-weighting).
* **ComStock** rows: one row per (bldg_id, _row_uid, upgrade); ``weight`` is
  the per-row geographic weight; ``sqft`` is the per-building floor area;
  ``annual_bill`` is already weighted real-world dollars (load × weight was
  applied before PySAM ran).
"""

from __future__ import annotations

import math

import polars as pl
import pytest

from buildstock_rate_impact_calculator.billing.results import (
    add_cost_per_sqft,
    bill_source_options,
    compute_bill_savings,
    summarize_bills,
    with_selected_bill,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _resstock_df() -> pl.DataFrame:
    """Two buildings × two upgrades; scalar weight = 100.0."""
    return pl.DataFrame(
        {
            "bldg_id": [1, 2, 1, 2],
            "upgrade": [0, 0, 1, 1],
            "annual_bill": [1000.0, 2000.0, 800.0, 1500.0],
            "weight": [100.0, 100.0, 100.0, 100.0],
            "sqft": [None, None, None, None],
            "_row_uid": ["1", "2", "1", "2"],
        }
    )


def _comstock_df() -> pl.DataFrame:
    """Two buildings × two upgrades; per-row weights differ.

    Each ``annual_bill`` is already weight × per-building bill.
    """
    # Underlying per-building bills (for reference):
    #   bldg 1 baseline  $5/ft² × 1000 ft² = $5000 × weight 10 = $50000
    #   bldg 2 baseline  $4/ft² × 2000 ft² = $8000 × weight 20 = $160000
    #   bldg 1 upgrade1  $4/ft² × 1000 ft² = $4000 × weight 10 = $40000
    #   bldg 2 upgrade1  $3/ft² × 2000 ft² = $6000 × weight 20 = $120000
    return pl.DataFrame(
        {
            "bldg_id": [1, 2, 1, 2],
            "upgrade": [0, 0, 1, 1],
            "annual_bill": [50_000.0, 160_000.0, 40_000.0, 120_000.0],
            "weight": [10.0, 20.0, 10.0, 20.0],
            "sqft": [1000.0, 2000.0, 1000.0, 2000.0],
            "_row_uid": ["1_a", "2_a", "1_a", "2_a"],
        }
    )


# ---------------------------------------------------------------------------
# add_cost_per_sqft
# ---------------------------------------------------------------------------


class TestAddCostPerSqft:
    def test_adds_column_for_comstock(self) -> None:
        df = _comstock_df()
        out = add_cost_per_sqft(df)
        assert "cost_per_sqft" in out.columns
        # cost_per_sqft = annual_bill / (weight × sqft)
        # bldg 1 base: 50000 / (10 × 1000) = 5.0
        # bldg 2 base: 160000 / (20 × 2000) = 4.0
        out_sorted = out.sort(["upgrade", "bldg_id"])
        assert out_sorted["cost_per_sqft"].to_list() == [5.0, 4.0, 4.0, 3.0]

    def test_handles_missing_weight_gracefully(self) -> None:
        df = pl.DataFrame(
            {
                "bldg_id": [1, 2],
                "upgrade": [0, 0],
                "annual_bill": [100.0, 200.0],
                "sqft": [10.0, 20.0],
            }
        )
        # No 'weight' column — should not raise; cost_per_sqft column is null
        # (or absent — accept either, but assert no crash and df shape stable).
        out = add_cost_per_sqft(df)
        # Either no column, or all-null column.
        if "cost_per_sqft" in out.columns:
            vals = out["cost_per_sqft"].to_list()
            assert all(v is None for v in vals)

    def test_handles_missing_sqft_gracefully(self) -> None:
        df = _resstock_df()  # sqft column is all None
        out = add_cost_per_sqft(df)
        # ResStock-style rows: sqft None → cost_per_sqft None.
        if "cost_per_sqft" in out.columns:
            assert all(v is None for v in out["cost_per_sqft"].to_list())

    def test_zero_weight_or_sqft_yields_null_not_inf(self) -> None:
        df = pl.DataFrame(
            {
                "bldg_id": [1, 2, 3],
                "upgrade": [0, 0, 0],
                "annual_bill": [100.0, 200.0, 300.0],
                "weight": [0.0, 10.0, 10.0],
                "sqft": [1000.0, 0.0, 1000.0],
            }
        )
        out = add_cost_per_sqft(df).sort("bldg_id")
        vals = out["cost_per_sqft"].to_list()
        assert vals[0] is None  # weight 0
        assert vals[1] is None  # sqft 0
        assert vals[2] == pytest.approx(300.0 / (10.0 * 1000.0))


# ---------------------------------------------------------------------------
# summarize_bills — ResStock
# ---------------------------------------------------------------------------


class TestSummarizeBillsResStock:
    def test_uses_scalar_weight_for_totals(self) -> None:
        """total_annual_bill = sum(weight × annual_bill)."""
        df = _resstock_df()
        summary = summarize_bills(df, dataset_type="resstock").sort("upgrade")
        # Baseline total: 100 × 1000 + 100 × 2000 = 300_000
        # Upgrade total:  100 × 800  + 100 × 1500 = 230_000
        totals = summary["total_annual_bill"].to_list()
        assert totals[0] == pytest.approx(300_000.0)
        assert totals[1] == pytest.approx(230_000.0)

    def test_mean_is_per_building(self) -> None:
        """ResStock mean = simple per-building mean of annual_bill (NOT weighted)."""
        df = _resstock_df()
        summary = summarize_bills(df, dataset_type="resstock").sort("upgrade")
        means = summary["mean_annual_bill"].to_list()
        assert means[0] == pytest.approx(1500.0)  # (1000 + 2000) / 2
        assert means[1] == pytest.approx(1150.0)  # (800 + 1500) / 2

    def test_units_equals_sum_weight(self) -> None:
        df = _resstock_df()
        summary = summarize_bills(df, dataset_type="resstock").sort("upgrade")
        # Each upgrade has 2 rows × weight 100 = 200 dwelling-units
        units = summary["units"].to_list()
        assert units[0] == pytest.approx(200.0)
        assert units[1] == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# summarize_bills — ComStock
# ---------------------------------------------------------------------------


class TestSummarizeBillsComStock:
    def test_reports_cost_per_sqft_stats(self) -> None:
        df = _comstock_df()
        summary = summarize_bills(df, dataset_type="comstock").sort("upgrade")
        # Baseline cost_per_sqft values: 5.0, 4.0 → mean 4.5, median 4.5
        # Upgrade1 cost_per_sqft values: 4.0, 3.0 → mean 3.5, median 3.5
        assert "mean_per_sqft" in summary.columns
        assert "median_per_sqft" in summary.columns
        means = summary["mean_per_sqft"].to_list()
        medians = summary["median_per_sqft"].to_list()
        assert means[0] == pytest.approx(4.5)
        assert means[1] == pytest.approx(3.5)
        assert medians[0] == pytest.approx(4.5)
        assert medians[1] == pytest.approx(3.5)

    def test_total_floor_area(self) -> None:
        """total_sqft = sum(weight × sqft)."""
        df = _comstock_df()
        summary = summarize_bills(df, dataset_type="comstock").sort("upgrade")
        # Per upgrade: 10 × 1000 + 20 × 2000 = 10_000 + 40_000 = 50_000
        totals = summary["total_sqft"].to_list()
        assert totals[0] == pytest.approx(50_000.0)
        assert totals[1] == pytest.approx(50_000.0)

    def test_total_bills_already_weighted(self) -> None:
        """For ComStock annual_bill is already weighted, so total = sum(annual_bill)."""
        df = _comstock_df()
        summary = summarize_bills(df, dataset_type="comstock").sort("upgrade")
        # Baseline: 50000 + 160000 = 210_000
        # Upgrade1: 40000 + 120000 = 160_000
        totals = summary["total_annual_bill"].to_list()
        assert totals[0] == pytest.approx(210_000.0)
        assert totals[1] == pytest.approx(160_000.0)


# ---------------------------------------------------------------------------
# summarize_bills — group_by
# ---------------------------------------------------------------------------


class TestSummarizeBillsGroupBy:
    def test_group_by_column_resstock(self) -> None:
        df = _resstock_df().with_columns(
            pl.Series("in.vintage", ["1980s", "1990s", "1980s", "1990s"])
        )
        summary = summarize_bills(
            df, dataset_type="resstock", group_by="in.vintage"
        ).sort(["in.vintage", "upgrade"])
        # 4 groups: (1980s,0), (1980s,1), (1990s,0), (1990s,1)
        assert summary.height == 4
        assert "in.vintage" in summary.columns
        # 1980s baseline: weight 100 × bill 1000 = 100_000
        row = summary.filter(
            (pl.col("in.vintage") == "1980s") & (pl.col("upgrade") == 0)
        ).row(0, named=True)
        assert row["total_annual_bill"] == pytest.approx(100_000.0)
        assert row["mean_annual_bill"] == pytest.approx(1000.0)

    def test_group_by_column_comstock(self) -> None:
        df = _comstock_df().with_columns(
            pl.Series("in.building_type", ["office", "retail", "office", "retail"])
        )
        summary = summarize_bills(
            df, dataset_type="comstock", group_by="in.building_type"
        ).sort(["in.building_type", "upgrade"])
        assert summary.height == 4
        # office baseline single row: cost_per_sqft = 5.0
        row = summary.filter(
            (pl.col("in.building_type") == "office") & (pl.col("upgrade") == 0)
        ).row(0, named=True)
        assert row["mean_per_sqft"] == pytest.approx(5.0)

    def test_unknown_group_by_raises(self) -> None:
        df = _resstock_df()
        with pytest.raises(ValueError, match="not found"):
            summarize_bills(df, dataset_type="resstock", group_by="does_not_exist")


# ---------------------------------------------------------------------------
# compute_bill_savings
# ---------------------------------------------------------------------------


class TestComputeBillSavings:
    def test_sign_baseline_minus_upgrade(self) -> None:
        """Positive bill_savings = baseline > upgrade (savings)."""
        df = _resstock_df()
        out = compute_bill_savings(df, baseline_upgrade=0).sort("bldg_id")
        # bldg 1: 1000 - 800 = 200 (saves $200)
        # bldg 2: 2000 - 1500 = 500 (saves $500)
        savings = out["bill_savings"].to_list()
        assert savings == [pytest.approx(200.0), pytest.approx(500.0)]

    def test_excludes_baseline_rows(self) -> None:
        df = _resstock_df()
        out = compute_bill_savings(df, baseline_upgrade=0)
        assert out.height == 2  # only upgrade=1 rows
        assert set(out["upgrade"].to_list()) == {1}

    def test_per_sqft_for_comstock(self) -> None:
        """ComStock case: bill_savings_per_sqft = baseline_cost_per_sqft - cost_per_sqft."""
        df = add_cost_per_sqft(_comstock_df())
        out = compute_bill_savings(df, baseline_upgrade=0).sort("bldg_id")
        assert "bill_savings_per_sqft" in out.columns
        # bldg 1: 5.0 - 4.0 = 1.0
        # bldg 2: 4.0 - 3.0 = 1.0
        savings_psf = out["bill_savings_per_sqft"].to_list()
        assert savings_psf == [pytest.approx(1.0), pytest.approx(1.0)]

    def test_missing_baseline_raises(self) -> None:
        df = _resstock_df().filter(pl.col("upgrade") != 0)
        with pytest.raises(ValueError, match="baseline"):
            compute_bill_savings(df, baseline_upgrade=0)

    def test_resstock_savings_have_no_per_sqft(self) -> None:
        """ResStock results have no cost_per_sqft column; per-sqft savings
        should be absent (or all-null) rather than crashing."""
        df = _resstock_df()
        out = compute_bill_savings(df, baseline_upgrade=0)
        if "bill_savings_per_sqft" in out.columns:
            vals = out["bill_savings_per_sqft"].to_list()
            assert all(v is None or math.isnan(v) for v in vals)

    def test_custom_bill_col(self) -> None:
        """Savings can be computed on the selected-sources bill column."""
        df = with_selected_bill(
            _resstock_df().with_columns(
                pl.Series("natural_gas_bill", [100.0, 200.0, 50.0, 100.0])
            ),
            ["annual_bill", "natural_gas_bill"],
        )
        out = compute_bill_savings(
            df, baseline_upgrade=0, bill_col="selected_bill"
        ).sort("bldg_id")
        # bldg 1: (1000+100) - (800+50) = 250; bldg 2: (2000+200) - (1500+100) = 600
        assert out["bill_savings"].to_list() == [
            pytest.approx(250.0),
            pytest.approx(600.0),
        ]


# ---------------------------------------------------------------------------
# energy-source selection helpers
# ---------------------------------------------------------------------------


class TestBillSourceSelection:
    def test_bill_source_options_orders_and_filters(self) -> None:
        df = _resstock_df().with_columns(
            pl.Series("natural_gas_bill", [100.0, 200.0, 50.0, 100.0])
        )
        options = bill_source_options(df)
        assert options == {
            "Electricity": "annual_bill",
            "Natural Gas": "natural_gas_bill",
        }

    def test_with_selected_bill_sums_and_fills_nulls(self) -> None:
        df = _resstock_df().with_columns(
            pl.Series("natural_gas_bill", [100.0, None, 50.0, None])
        )
        out = with_selected_bill(df, ["annual_bill", "natural_gas_bill"]).sort(
            ["upgrade", "bldg_id"]
        )
        assert out["selected_bill"].to_list() == [
            pytest.approx(1100.0),
            pytest.approx(2000.0),  # null gas treated as $0
            pytest.approx(850.0),
            pytest.approx(1500.0),
        ]

    def test_with_selected_bill_raises_when_no_columns_present(self) -> None:
        df = _resstock_df()
        with pytest.raises(ValueError, match="present"):
            with_selected_bill(df, ["does_not_exist"])

    def test_summarize_resstock_multi_source(self) -> None:
        df = _resstock_df().with_columns(
            pl.Series("natural_gas_bill", [100.0, 200.0, 50.0, 100.0])
        )
        summary = summarize_bills(
            df,
            dataset_type="resstock",
            bill_columns=["annual_bill", "natural_gas_bill"],
        ).sort("upgrade")
        # Baseline mean: ((1000+100) + (2000+200)) / 2 = 1650
        assert summary["mean_annual_bill"].to_list()[0] == pytest.approx(1650.0)
        # Weighted total: 100 × (1100 + 2200) = 330_000
        assert summary["total_annual_bill"].to_list()[0] == pytest.approx(330_000.0)
        # Per-source electricity mean is preserved separately.
        assert summary["mean_electricity_bill"].to_list()[0] == pytest.approx(1500.0)
        assert summary["mean_natural_gas_bill"].to_list()[0] == pytest.approx(150.0)

    def test_summarize_comstock_multi_source_per_sqft(self) -> None:
        df = _comstock_df().with_columns(
            # Already-weighted fuel bills: $1/ft² on each building.
            pl.Series("natural_gas_bill", [10_000.0, 40_000.0, 10_000.0, 40_000.0])
        )
        summary = summarize_bills(
            df,
            dataset_type="comstock",
            bill_columns=["annual_bill", "natural_gas_bill"],
        ).sort("upgrade")
        # Baseline electricity $/ft²: 5.0 and 4.0; gas adds 1.0 to each →
        # combined mean = (6.0 + 5.0) / 2 = 5.5
        assert summary["mean_per_sqft"].to_list()[0] == pytest.approx(5.5)
        # Totals: 210_000 + 50_000 = 260_000
        assert summary["total_annual_bill"].to_list()[0] == pytest.approx(260_000.0)
        # Per-source $/ft² means for the "By fuel" stacked bar.
        assert summary["mean_electricity_bill_per_sqft"].to_list()[0] == pytest.approx(
            4.5
        )
        assert summary["mean_natural_gas_bill_per_sqft"].to_list()[0] == pytest.approx(
            1.0
        )
        assert summary["total_natural_gas_bill"].to_list()[0] == pytest.approx(50_000.0)

    def test_summarize_bills_unknown_sources_raise(self) -> None:
        df = _resstock_df()
        with pytest.raises(ValueError, match="present"):
            summarize_bills(df, dataset_type="resstock", bill_columns=["propane_bill"])
