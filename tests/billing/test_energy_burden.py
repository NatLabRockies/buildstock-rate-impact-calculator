"""Phase 6: energy-burden calculations (ResStock).

Covers the income-bin midpoint parser, ``add_energy_burden`` null/zero
guards, and ``summarize_energy_burden`` on a hand-built frame. Bin labels
match the actual ``in.income_recs_2020`` enumerations verified against the
ResStock 2024.2 / 2025.1 enumeration dictionaries: ``<20000``,
``20000-39999``, ``40000-59999``, ``60000-99999``, ``100000-149999``,
``150000+``, ``Not Available``.
"""

from __future__ import annotations

import polars as pl
import pytest

from buildstock_rate_impact_calculator.billing.energy_burden import (
    HIGH_BURDEN,
    SEVERE_BURDEN,
    add_energy_burden,
    income_bin_midpoint,
    summarize_energy_burden,
)


class TestIncomeBinMidpoint:
    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("<20000", 10_000.0),
            ("20000-39999", 29_999.5),
            ("40000-59999", 49_999.5),
            ("60000-99999", 79_999.5),
            ("100000-149999", 124_999.5),
            ("150000+", 187_500.0),
            ("Not Available", None),
            ("", None),
            (None, None),
            # Formatting variants
            ("$20,000-$39,999", 29_999.5),
            ("  150000+  ", 187_500.0),
        ],
    )
    def test_parses_actual_bin_labels(
        self, label: str | None, expected: float | None
    ) -> None:
        result = income_bin_midpoint(label)
        if expected is None:
            assert result is None
        else:
            assert result == pytest.approx(expected)


def _burden_df() -> pl.DataFrame:
    """Two buildings × two upgrades with real income bins.

    bldg 1: income <20000 → 10 000; bills 1000 / 800 → burden 10 % / 8 %
    bldg 2: income 150000+ → 187 500; bills 2000 / 1500 → burden ~1.07 % / 0.8 %
    """
    return pl.DataFrame(
        {
            "bldg_id": [1, 2, 1, 2],
            "upgrade": [0, 0, 1, 1],
            "electricity_annual_bill": [1000.0, 2000.0, 800.0, 1500.0],
            "weight": [100.0, 100.0, 100.0, 100.0],
            "in.income_recs_2020": ["<20000", "150000+", "<20000", "150000+"],
        }
    )


class TestAddEnergyBurden:
    def test_burden_computed_from_annual_bill_fallback(self) -> None:
        out = add_energy_burden(_burden_df(), "in.income_recs_2020").sort(
            ["upgrade", "bldg_id"]
        )
        assert "energy_burden" in out.columns
        burdens = out["energy_burden"].to_list()
        assert burdens[0] == pytest.approx(1000.0 / 10_000.0)  # 10 %
        assert burdens[1] == pytest.approx(2000.0 / 187_500.0)
        assert burdens[2] == pytest.approx(800.0 / 10_000.0)  # 8 %
        assert burdens[3] == pytest.approx(1500.0 / 187_500.0)

    def test_explicit_bill_col(self) -> None:
        df = _burden_df().with_columns(
            (pl.col("electricity_annual_bill") * 2).alias("total_energy_bill")
        )
        out = add_energy_burden(df, "in.income_recs_2020").sort(["upgrade", "bldg_id"])
        assert out["energy_burden"].to_list()[0] == pytest.approx(0.2)

    def test_unparseable_income_yields_null(self) -> None:
        df = _burden_df().with_columns(
            pl.Series(
                "in.income_recs_2020",
                ["Not Available", "150000+", "Not Available", "150000+"],
            )
        )
        out = add_energy_burden(df, "in.income_recs_2020").sort(["upgrade", "bldg_id"])
        assert out["energy_burden"].to_list()[0] is None
        assert out["energy_burden"].to_list()[1] is not None

    def test_null_bill_yields_null(self) -> None:
        df = _burden_df().with_columns(
            pl.Series("electricity_annual_bill", [None, 2000.0, 800.0, 1500.0])
        )
        out = add_energy_burden(df, "in.income_recs_2020").sort(["upgrade", "bldg_id"])
        assert out["energy_burden"].to_list()[0] is None

    def test_missing_income_column_raises(self) -> None:
        with pytest.raises(ValueError, match="income column"):
            add_energy_burden(_burden_df(), "in.does_not_exist")

    def test_missing_bill_columns_raise(self) -> None:
        df = _burden_df().drop("electricity_annual_bill")
        with pytest.raises(ValueError, match="electricity_annual_bill"):
            add_energy_burden(df, "in.income_recs_2020")


class TestSummarizeEnergyBurden:
    def test_per_upgrade_stats_and_weighted_shares(self) -> None:
        out = summarize_energy_burden(
            add_energy_burden(_burden_df(), "in.income_recs_2020")
        ).sort("upgrade")
        assert out.height == 2
        base = out.row(0, named=True)
        # Burdens at baseline: 0.10 and ~0.01067 → mean ≈ 0.0553
        assert base["mean_burden"] == pytest.approx((0.10 + 2000.0 / 187_500.0) / 2)
        # bldg 1 (weight 100) is at exactly 10 % → severe and high; bldg 2 not.
        assert base["pct_high"] == pytest.approx(0.5)
        assert base["pct_severe"] == pytest.approx(0.5)
        upg = out.row(1, named=True)
        # Upgrade burdens: 0.08 (high, not severe) and 0.008.
        assert upg["pct_high"] == pytest.approx(0.5)
        assert upg["pct_severe"] == pytest.approx(0.0)

    def test_thresholds_are_lead_conventions(self) -> None:
        assert HIGH_BURDEN == pytest.approx(0.06)
        assert SEVERE_BURDEN == pytest.approx(0.10)

    def test_requires_energy_burden_column(self) -> None:
        with pytest.raises(ValueError, match="add_energy_burden"):
            summarize_energy_burden(_burden_df())
