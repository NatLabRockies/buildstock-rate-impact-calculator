"""Phase 1: bill-component and peak-demand extraction from PySAM.

``UtilityBillCalculator._calc_one`` must surface PySAM's charge
decomposition (fixed / energy / demand / minimum) and the annual peak
demand on every results row, so downstream aggregation can report bill
composition without re-running PySAM.
"""

from __future__ import annotations

import numpy as np
import pytest

from buildstock_rate_impact_calculator.billing.calculator import UtilityBillCalculator
from buildstock_rate_impact_calculator.rates.rate_builder import UrdbRateBuilder

COMPONENT_COLUMNS = (
    "annual_fixed_charge",
    "annual_energy_charge",
    "annual_demand_charge",
    "annual_min_charge",
    "peak_demand_kw",
)


@pytest.fixture
def flat_rate_config() -> dict:
    """Minimal real URDB rate: $0.10/kWh flat energy + $5/month fixed."""
    return (
        UrdbRateBuilder(validate_pysam=False)
        .basic_info("Test Flat Rate", "Test Utility", "Residential")
        .simple_flat_rate(0.10)
        .fixed_charge(5.0, "$/month")
        .to_pysam_dict()
    )


class TestComponentExtraction:
    def test_components_present_and_sum_to_annual_bill(
        self, flat_rate_config: dict
    ) -> None:
        calc = UtilityBillCalculator()
        records = [{"bldg_id": 1, "upgrade": 0, "load_array": np.ones(8760)}]
        df = calc.calculate_batch_bills(records, flat_rate_config)
        assert df.height == 1
        row = df.row(0, named=True)
        assert row["error"] is None
        for col in COMPONENT_COLUMNS:
            assert col in df.columns
            assert row[col] is not None
        component_sum = (
            row["annual_fixed_charge"]
            + row["annual_energy_charge"]
            + row["annual_demand_charge"]
            + row["annual_min_charge"]
        )
        assert component_sum == pytest.approx(row["annual_bill"], abs=0.05)
        # Constant 1 kW load → 1 kW peak
        assert row["peak_demand_kw"] == pytest.approx(1.0)

    def test_error_row_has_null_components(self, flat_rate_config: dict) -> None:
        calc = UtilityBillCalculator()
        records = [{"bldg_id": 1, "upgrade": 0, "load_array": np.ones(10)}]
        df = calc.calculate_batch_bills(records, flat_rate_config)
        assert df.height == 1
        row = df.row(0, named=True)
        assert row["error"] is not None
        for col in COMPONENT_COLUMNS:
            assert row[col] is None

    def test_empty_batch_schema_includes_components(
        self, flat_rate_config: dict
    ) -> None:
        calc = UtilityBillCalculator()
        df = calc.calculate_batch_bills([], flat_rate_config)
        assert df.height == 0
        import polars as pl

        for col in COMPONENT_COLUMNS:
            assert col in df.columns
            assert df.schema[col] == pl.Float64
