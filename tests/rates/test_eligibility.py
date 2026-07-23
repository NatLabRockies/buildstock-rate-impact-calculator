"""Phase 8: rate eligibility screening.

Covers URDB bounds parsing (``rate_eligibility_bounds``), per-building
normalization in ``flag_rate_eligibility`` (ComStock rows carry weighted
quantities), and the config plumbing for the annual-consumption filter
column.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from buildstock_rate_impact_calculator.data.buildstock_manager import (
    BuildStockDataManager,
)
from buildstock_rate_impact_calculator.rates.eligibility import (
    flag_rate_eligibility,
    rate_eligibility_bounds,
)


class TestRateEligibilityBounds:
    def test_all_missing_returns_none(self) -> None:
        assert rate_eligibility_bounds({"name": "flat"}) is None

    def test_zero_bounds_treated_as_unbounded(self) -> None:
        """UrdbRateBuilder writes 0.0 defaults — a $0 window is no window."""
        rate = {
            "peakkwcapacitymin": 0.0,
            "peakkwcapacitymax": 0.0,
            "peakkwhusagemin": 0.0,
            "peakkwhusagemax": 0.0,
        }
        assert rate_eligibility_bounds(rate) is None

    def test_partial_bounds(self) -> None:
        rate = {"peakkwcapacitymax": 50.0, "peakkwhusagemin": 100.0}
        bounds = rate_eligibility_bounds(rate)
        assert bounds == {
            "kw_min": None,
            "kw_max": 50.0,
            "kwh_min": 100.0,
            "kwh_max": None,
        }

    def test_non_numeric_values_ignored(self) -> None:
        assert rate_eligibility_bounds({"peakkwcapacitymax": "n/a"}) is None


class TestFlagRateEligibility:
    def _resstock_df(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "bldg_id": [1, 2, 3],
                "upgrade": [0, 0, 0],
                "electricity_annual_energy_kwh": [12_000.0, 120_000.0, None],
                "electricity_peak_demand_kw": [4.0, 40.0, None],
                "weight": [100.0, 100.0, 100.0],
            }
        )

    def test_none_bounds_marks_all_eligible(self) -> None:
        out = flag_rate_eligibility(self._resstock_df(), None, is_comstock=False)
        assert out["rate_eligible"].to_list() == [True, True, True]

    def test_kw_max_flags_resstock(self) -> None:
        bounds = {"kw_min": None, "kw_max": 10.0, "kwh_min": None, "kwh_max": None}
        out = flag_rate_eligibility(
            self._resstock_df(), bounds, is_comstock=False
        ).sort("bldg_id")
        # bldg 1: 4 kW ok; bldg 2: 40 kW > 10 → ineligible; bldg 3 null → eligible
        assert out["rate_eligible"].to_list() == [True, False, True]

    def test_monthly_kwh_window(self) -> None:
        """Annual kWh is compared as annual/12 against the monthly window."""
        bounds = {
            "kw_min": None,
            "kw_max": None,
            "kwh_min": None,
            "kwh_max": 2_000.0,
        }
        out = flag_rate_eligibility(
            self._resstock_df(), bounds, is_comstock=False
        ).sort("bldg_id")
        # bldg 1: 12000/12 = 1000 ok; bldg 2: 120000/12 = 10000 > 2000 → out
        assert out["rate_eligible"].to_list() == [True, False, True]

    def test_comstock_normalizes_by_weight(self) -> None:
        """A weighted row with a tiny weight but normal per-building kWh must
        stay eligible; only genuinely large buildings should be flagged."""
        df = pl.DataFrame(
            {
                "bldg_id": [1, 2],
                "upgrade": [0, 0],
                # Both rows show 240,000 weighted kWh; bldg 1 represents 20
                # buildings (12,000 each — small), bldg 2 is one big building.
                "electricity_annual_energy_kwh": [240_000.0, 240_000.0],
                "electricity_peak_demand_kw": [80.0, 80.0],
                "weight": [20.0, 1.0],
            }
        )
        bounds = {
            "kw_min": None,
            "kw_max": 50.0,
            "kwh_min": None,
            "kwh_max": None,
        }
        out = flag_rate_eligibility(df, bounds, is_comstock=True).sort("bldg_id")
        # Per-building peaks: 80/20 = 4 kW (ok) vs 80/1 = 80 kW (over 50).
        assert out["rate_eligible"].to_list() == [True, False]

    def test_kw_min_lower_bound(self) -> None:
        bounds = {"kw_min": 10.0, "kw_max": None, "kwh_min": None, "kwh_max": None}
        out = flag_rate_eligibility(
            self._resstock_df(), bounds, is_comstock=False
        ).sort("bldg_id")
        # bldg 1: 4 kW < 10 → ineligible; bldg 2: 40 kW ok; null → eligible
        assert out["rate_eligible"].to_list() == [False, True, True]

    def test_missing_columns_all_eligible(self) -> None:
        df = pl.DataFrame({"bldg_id": [1], "upgrade": [0], "electricity_annual_bill": [100.0]})
        bounds = {"kw_min": None, "kw_max": 5.0, "kwh_min": None, "kwh_max": None}
        out = flag_rate_eligibility(df, bounds, is_comstock=False)
        assert out["rate_eligible"].to_list() == [True]


class TestElectricityEnergyColumnConfig:
    def test_in_app_metadata_columns(self, tmp_path: Path) -> None:
        for dataset_type, release in (
            ("resstock", "2024_release_2"),
            ("resstock", "2025_release_1"),
            ("comstock", "2025_release_3"),
        ):
            mgr = BuildStockDataManager(
                dataset_type=dataset_type,
                release=release,
                cache_enabled=True,
                cache_dir=tmp_path / "cache",
            )
            col = mgr.config.electricity_energy_column
            assert col, f"{dataset_type}_{release} missing electricity_energy_column"
            assert col in mgr.get_app_metadata_columns()
            assert col in (mgr.config.filter_columns or [])
