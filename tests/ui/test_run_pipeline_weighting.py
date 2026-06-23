"""
Unit tests for weight-aware billing pipeline helpers in
``ui.pages.page_05_run``.

These tests target the pure-data helpers (``_assign_row_uid``,
``_build_load_records``, refactored ``_calculate_fuel_bills``) which are
factored out of the Streamlit pipeline so they can be exercised without a
running Streamlit session.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import polars as pl
import pytest


# ===========================================================================
# Helpers
# ===========================================================================


def _make_ts_data(bldg_ids: list[Any], upgrade: int, load_column: str) -> dict:
    """Build a fake ts_data dict mapping '{bldg}_{upgrade}' → 1.0-filled 8760 DataFrame."""
    return {
        f"{bldg}_{upgrade}": pl.DataFrame({load_column: [1.0] * 8760})
        for bldg in bldg_ids
    }


def _comstock_metadata() -> pl.DataFrame:
    """Metadata with one bldg_id appearing in TWO counties (different weights)."""
    return pl.DataFrame(
        {
            "bldg_id": [1, 1, 2],
            "weight": [0.4, 0.6, 1.2],
            "in.sqft..ft2": [10_000.0, 10_000.0, 5_000.0],
            "in.county": ["G0100010", "G0100030", "G0100010"],
            "in.puma": [None, None, None],
        }
    )


def _resstock_metadata() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "bldg_id": [1, 2, 3],
            "weight": [188.0, 188.0, 188.0],
            "in.county": ["G0100010", "G0100020", "G0100030"],
            "in.puma": [None, None, None],
        }
    )


# ===========================================================================
# _assign_row_uid
# ===========================================================================


class TestAssignRowUid:
    def test_assign_row_uid_comstock_distinguishes_per_geo_rows(self) -> None:
        from buildstock_rate_impact_calculator.ui.pages.page_05_run import (
            _assign_row_uid,
        )

        df = _comstock_metadata()
        out = _assign_row_uid(df, "comstock", "bldg_id")
        uids = out["_row_uid"].to_list()
        assert len(set(uids)) == len(uids), "row_uid must be unique per metadata row"

    def test_assign_row_uid_resstock_uses_bldg_id_only(self) -> None:
        from buildstock_rate_impact_calculator.ui.pages.page_05_run import (
            _assign_row_uid,
        )

        df = _resstock_metadata()
        out = _assign_row_uid(df, "resstock", "bldg_id")
        # Each ResStock bldg appears once → row_uid should match bldg_id
        assert out["_row_uid"].to_list() == ["1", "2", "3"]


# ===========================================================================
# _build_load_records
# ===========================================================================


class TestBuildLoadRecords:
    def test_resstock_record_per_unique_bldg_with_scalar_weight(self) -> None:
        from buildstock_rate_impact_calculator.ui.pages.page_05_run import (
            _assign_row_uid,
            _build_load_records,
        )

        meta = _assign_row_uid(_resstock_metadata(), "resstock", "bldg_id")
        ts = _make_ts_data([1, 2, 3], 0, "load")
        records = _build_load_records(
            metadata=meta,
            ts_data=ts,
            dataset_type="resstock",
            load_column="load",
            scalar_weight=188.0,
            upgrade_id=0,
            applicable_set={1, 2, 3},
            bldg_col="bldg_id",
        )
        assert len(records) == 3
        for rec in records:
            assert rec["weight"] == 188.0
            assert rec["sqft"] is None
            # ResStock load is NOT scaled by weight (scaling happens at totals time)
            np.testing.assert_array_equal(rec["load_array"], np.full(8760, 1.0))

    def test_comstock_record_per_metadata_row_load_scaled_by_weight(self) -> None:
        from buildstock_rate_impact_calculator.ui.pages.page_05_run import (
            _assign_row_uid,
            _build_load_records,
        )

        meta = _assign_row_uid(_comstock_metadata(), "comstock", "bldg_id")
        # Use unique bldg_ids in ts_data; the SAME bldg_id is reused across rows
        ts = _make_ts_data([1, 2], 0, "load")
        records = _build_load_records(
            metadata=meta,
            ts_data=ts,
            dataset_type="comstock",
            load_column="load",
            scalar_weight=None,
            upgrade_id=0,
            applicable_set={1, 2},
            bldg_col="bldg_id",
        )
        # 3 metadata rows → 3 records
        assert len(records) == 3
        weights = sorted(r["weight"] for r in records)
        assert weights == [0.4, 0.6, 1.2]
        # ComStock loads ARE scaled per-row by weight
        for rec in records:
            expected = np.full(8760, rec["weight"])
            np.testing.assert_allclose(rec["load_array"], expected)

    def test_comstock_records_carry_sqft_and_row_uid(self) -> None:
        from buildstock_rate_impact_calculator.ui.pages.page_05_run import (
            _assign_row_uid,
            _build_load_records,
        )

        meta = _assign_row_uid(_comstock_metadata(), "comstock", "bldg_id")
        ts = _make_ts_data([1, 2], 0, "load")
        records = _build_load_records(
            metadata=meta,
            ts_data=ts,
            dataset_type="comstock",
            load_column="load",
            scalar_weight=None,
            upgrade_id=0,
            applicable_set={1, 2},
            bldg_col="bldg_id",
        )
        for rec in records:
            assert rec["sqft"] is not None
            assert rec["_row_uid"] is not None
        uids = [r["_row_uid"] for r in records]
        assert len(set(uids)) == 3, "Each metadata row needs a distinct _row_uid"


# ===========================================================================
# _calculate_fuel_bills — weight handling
# ===========================================================================


def _make_mock_mgr(
    dataset_type: str,
    release: str,
    fuel_meta_df: pl.DataFrame,
) -> MagicMock:
    """Build a mock manager that returns ``fuel_meta_df`` from load_upgrade_metadata."""
    from buildstock_rate_impact_calculator.data.buildstock_manager import (
        BuildStockDataManager,
        DatasetConfig,
    )

    config_key = f"{dataset_type}_{release}"
    base_config = BuildStockDataManager.DATASET_CONFIGS[config_key]
    config = DatasetConfig(**{**base_config.__dict__})
    config.dataset_type = dataset_type
    config.release = release

    mgr = MagicMock()
    mgr.config = config
    mgr.load_upgrade_metadata.return_value = fuel_meta_df
    mgr.get_fuel_columns_for_type.side_effect = lambda ft: (
        BuildStockDataManager.get_fuel_columns_for_type(mgr, ft)
    )
    return mgr


class TestCalculateFuelBillsComstock:
    """ComStock-specific fuel-bill behavior (no dedup, weight applied)."""

    def test_default_mode_uses_convert_default_fuel_bill_per_row_for_comstock_2025(
        self,
    ) -> None:
        """ComStock 2025 r3: per-row default fuel = value * 1e9 (weight argument ignored)."""
        from buildstock_rate_impact_calculator.ui.pages.page_05_run import (
            _calculate_fuel_bills,
        )

        # ComStock 2025 r3 default-mode column for natural gas
        ng_bill_col = (
            "calc.weighted.utility_bills.natural_gas_bill_state_average..billion_usd"
        )
        # Mock returns metadata where same bldg_id appears twice with different weights
        fuel_meta = pl.DataFrame(
            {
                "bldg_id": [1, 1, 2],
                "weight": [0.4, 0.6, 1.0],
                ng_bill_col: [1.0e-6, 2.0e-6, 3.0e-6],
                "in.county": ["A", "B", "A"],
                "in.puma": [None, None, None],
            }
        )
        mgr = _make_mock_mgr("comstock", "2025_release_3", fuel_meta)
        tracker = MagicMock()
        df = _calculate_fuel_bills(
            mgr=mgr,
            building_ids=[1, 2],
            upgrade_id=0,
            fuel_rate_assignments_for_scenario={},  # no rates → default mode
            tracker=tracker,
        )
        assert df is not None
        # 3 metadata rows → 3 output rows (no dedup for ComStock)
        assert df.height == 3
        # Per-row: value * 1e9 (weight argument ignored for 2025 r3)
        bills = df.sort("_row_uid")["natural_gas_bill"].to_list()
        assert bills == pytest.approx([1.0e-6 * 1e9, 2.0e-6 * 1e9, 3.0e-6 * 1e9])

    def test_default_mode_for_comstock_2024_multiplies_by_weight(self) -> None:
        """ComStock 2024 r2: per-row default fuel = value * weight (no dedup)."""
        from buildstock_rate_impact_calculator.ui.pages.page_05_run import (
            _calculate_fuel_bills,
        )

        ng_bill_col = "out.utility_bills.natural_gas_bill..usd"
        fuel_meta = pl.DataFrame(
            {
                "bldg_id": [1, 1, 2],
                "weight": [0.4, 0.6, 1.0],
                ng_bill_col: [100.0, 200.0, 300.0],
                "in.county": ["A", "B", "A"],
                "in.puma": [None, None, None],
            }
        )
        mgr = _make_mock_mgr("comstock", "2024_release_2", fuel_meta)
        tracker = MagicMock()
        df = _calculate_fuel_bills(
            mgr=mgr,
            building_ids=[1, 2],
            upgrade_id=0,
            fuel_rate_assignments_for_scenario={},
            tracker=tracker,
        )
        assert df is not None
        assert df.height == 3
        bills = df.sort("_row_uid")["natural_gas_bill"].to_list()
        assert bills == pytest.approx([100.0 * 0.4, 200.0 * 0.6, 300.0 * 1.0])

    def test_rate_mode_multiplies_bill_by_weight_for_comstock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ComStock rate-mode: per-row bill = calculated_bill * weight."""
        from buildstock_rate_impact_calculator.ui.pages.page_05_run import (
            _calculate_fuel_bills,
        )
        from buildstock_rate_impact_calculator.rates import fuel_rate as fuel_rate_mod

        ng_energy = "out.natural_gas.total.energy_consumption..kwh"
        fuel_meta = pl.DataFrame(
            {
                "bldg_id": [1, 2],
                "weight": [0.5, 2.0],
                ng_energy: [100.0, 200.0],
                "in.county": ["A", "A"],
                "in.puma": [None, None],
            }
        )
        mgr = _make_mock_mgr("comstock", "2024_release_2", fuel_meta)

        # Stub fuel_rate.calculate_fuel_bill to return a fixed per-building bill
        def _fake_bill(energy: float, rate_dict: dict) -> dict:
            # Return 10.0 / kWh regardless, so weight effect is visible
            return {
                "annual_fuel_bill": energy * 10.0,
                "monthly_fuel_bills": [energy * 10.0 / 12.0] * 12,
                "rate_name": "fake",
            }

        # Import path inside _calculate_fuel_bills uses `from ... import calculate_fuel_bill`.
        # Patch the symbol at the module that imports it (page_05_run reimports per call).
        monkeypatch.setattr(fuel_rate_mod, "calculate_fuel_bill", _fake_bill)

        tracker = MagicMock()
        df = _calculate_fuel_bills(
            mgr=mgr,
            building_ids=[1, 2],
            upgrade_id=0,
            fuel_rate_assignments_for_scenario={"natural_gas": {"name": "fake"}},
            tracker=tracker,
        )
        assert df is not None
        bills = df.sort("bldg_id")["natural_gas_bill"].to_list()
        # bldg 1: 100 * 10 * 0.5 = 500; bldg 2: 200 * 10 * 2.0 = 4000
        assert bills == pytest.approx([500.0, 4000.0])


class TestCalculateFuelBillsResstock:
    """ResStock fuel-bill behavior is unchanged from Phase 1 (per-building, dedup)."""

    def test_rate_mode_unchanged_for_resstock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ResStock rate-mode bills are NOT multiplied by weight (scalar applied later)."""
        from buildstock_rate_impact_calculator.ui.pages.page_05_run import (
            _calculate_fuel_bills,
        )
        from buildstock_rate_impact_calculator.rates import fuel_rate as fuel_rate_mod

        ng_energy = "out.natural_gas.total.energy_consumption..kwh"
        fuel_meta = pl.DataFrame(
            {
                "bldg_id": [1, 2],
                "weight": [188.0, 188.0],
                ng_energy: [100.0, 200.0],
            }
        )
        mgr = _make_mock_mgr("resstock", "2025_release_1", fuel_meta)

        def _fake_bill(energy: float, rate_dict: dict) -> dict:
            return {
                "annual_fuel_bill": energy * 10.0,
                "monthly_fuel_bills": [energy * 10.0 / 12.0] * 12,
                "rate_name": "fake",
            }

        monkeypatch.setattr(fuel_rate_mod, "calculate_fuel_bill", _fake_bill)

        tracker = MagicMock()
        df = _calculate_fuel_bills(
            mgr=mgr,
            building_ids=[1, 2],
            upgrade_id=0,
            fuel_rate_assignments_for_scenario={"natural_gas": {"name": "fake"}},
            tracker=tracker,
        )
        assert df is not None
        bills = df.sort("bldg_id")["natural_gas_bill"].to_list()
        # ResStock: per-building bill, NO weight multiplication
        assert bills == pytest.approx([1000.0, 2000.0])

    def test_default_mode_unchanged_for_resstock(self) -> None:
        """ResStock default-mode bills are stored as-is (no weight applied)."""
        from buildstock_rate_impact_calculator.ui.pages.page_05_run import (
            _calculate_fuel_bills,
        )

        ng_bill_col = "out.utility_bills.natural_gas_bill..usd"
        fuel_meta = pl.DataFrame(
            {
                "bldg_id": [1, 2],
                "weight": [188.0, 188.0],
                ng_bill_col: [100.0, 200.0],
            }
        )
        mgr = _make_mock_mgr("resstock", "2025_release_1", fuel_meta)

        tracker = MagicMock()
        df = _calculate_fuel_bills(
            mgr=mgr,
            building_ids=[1, 2],
            upgrade_id=0,
            fuel_rate_assignments_for_scenario={},
            tracker=tracker,
        )
        assert df is not None
        bills = df.sort("bldg_id")["natural_gas_bill"].to_list()
        # ResStock pass-through: value unchanged
        assert bills == pytest.approx([100.0, 200.0])
