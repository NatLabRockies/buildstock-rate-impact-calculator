"""
Tests for the ComStock default-mode fuel-bill converter
``billing.conversions.convert_default_fuel_bill``.
"""

from __future__ import annotations

import pytest

from buildstock_rate_impact_calculator.billing.conversions import (
    convert_default_fuel_bill,
)
from buildstock_rate_impact_calculator.data.buildstock_manager import (
    DatasetConfig,
)


def _cfg(dataset_type: str, release: str) -> DatasetConfig:
    """Build a minimal DatasetConfig with only the dispatch fields populated."""
    return DatasetConfig(
        base_uri="",
        metadata_structure="parquet",
        timeseries_structure="by_state",
        default_load_column="",
        building_id_column="bldg_id",
        state_column="in.state",
        expected_resolution_minutes=15,
        supported_upgrades=[0],
        dataset_type=dataset_type,
        release=release,
    )


class TestConvertDefaultFuelBill:
    def test_convert_default_fuel_bill_comstock_2024_multiplies_by_weight(self) -> None:
        cfg = _cfg("comstock", "2024_release_2")
        assert convert_default_fuel_bill(1234.5, cfg, weight=10.0) == pytest.approx(
            1234.5 * 10.0
        )

    def test_convert_default_fuel_bill_comstock_2025_scales_by_1e9(self) -> None:
        cfg = _cfg("comstock", "2025_release_3")
        # weight argument should be ignored for the 2025 r3 dispatch
        assert convert_default_fuel_bill(1.0e-6, cfg, weight=99.0) == pytest.approx(
            1.0e-6 * 1.0e9
        )

    def test_convert_default_fuel_bill_resstock_returns_value_unchanged(self) -> None:
        cfg = _cfg("resstock", "2025_release_1")
        assert convert_default_fuel_bill(500.0, cfg, weight=188.0) == 500.0

    def test_convert_default_fuel_bill_unknown_release_raises(self) -> None:
        cfg = _cfg("comstock", "9999_release_99")
        with pytest.raises(ValueError, match="default-mode fuel bill"):
            convert_default_fuel_bill(1.0, cfg, weight=1.0)
