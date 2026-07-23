"""
Unit tests for per-row weight / sqft pass-through in ``UtilityBillCalculator``.

The calculator itself does NOT scale loads by weight — that is the caller's
responsibility (page_05_run.py). The calculator's only weighting contract is
that ``weight`` and ``sqft`` from each load record are surfaced as columns on
the output DataFrame so downstream aggregation can use them.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from buildstock_rate_impact_calculator.billing.calculator import UtilityBillCalculator
from buildstock_rate_impact_calculator.rates.rate_builder import UrdbRateBuilder


def _stub_calc_one_result(bldg_id: Any, upgrade: int, **extras: Any) -> dict:
    """Build a fake _calc_one return value matching the production schema."""
    row = {
        "bldg_id": bldg_id,
        "upgrade": upgrade,
        "electricity_annual_bill": 1000.0,
        "electricity_annual_energy_kwh": 10000.0,
        "electricity_average_rate_ct_kwh": 10.0,
        "electricity_monthly_bills": "[83.0]*12",
        "error": None,
    }
    row.update(extras)
    return row


@pytest.fixture
def minimal_rate_config() -> dict:
    """A placeholder rate config; UrdbRateBuilder.prepare_for_pysam is bypassed."""
    return {"name": "test_flat_rate"}


@pytest.fixture(autouse=True)
def _bypass_rate_prep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace UrdbRateBuilder.prepare_for_pysam with a no-op for these tests."""
    monkeypatch.setattr(
        UrdbRateBuilder,
        "prepare_for_pysam",
        staticmethod(lambda rate_config: {}),
    )


@pytest.fixture
def calc_one_spy(monkeypatch: pytest.MonkeyPatch) -> list:
    """Replace ``UtilityBillCalculator._calc_one`` with a recording stub.

    The stub returns a deterministic result row that surfaces any kwargs
    (``weight``, ``sqft``, etc.) supplied by ``calculate_batch_bills``.
    Returns a list to which call records are appended for inspection.
    """
    calls: list[dict] = []

    def fake_calc_one(
        self: UtilityBillCalculator,
        bldg_id: Any,
        upgrade: int,
        load_array: np.ndarray,
        pysam_params: dict,
        **per_row_meta: Any,
    ) -> dict:
        calls.append(
            {
                "bldg_id": bldg_id,
                "upgrade": upgrade,
                "load_array": load_array,
                "per_row_meta": per_row_meta,
            }
        )
        return _stub_calc_one_result(bldg_id, upgrade, **per_row_meta)

    monkeypatch.setattr(UtilityBillCalculator, "_calc_one", fake_calc_one)
    return calls


class TestCalculatorPerRowMetadata:
    """The calculator surfaces per-row weight/sqft from the load_records into output."""

    def test_load_records_accept_weight_and_sqft_fields(
        self, minimal_rate_config: dict, calc_one_spy: list
    ) -> None:
        """``calculate_batch_bills`` accepts load records with weight + sqft, no error."""
        calc = UtilityBillCalculator()
        records = [
            {
                "bldg_id": 1,
                "upgrade": 0,
                "load_array": np.full(8760, 1.0),
                "weight": 2.5,
                "sqft": 10000.0,
            },
        ]
        df = calc.calculate_batch_bills(records, minimal_rate_config)
        assert "weight" in df.columns
        assert "sqft" in df.columns

    def test_results_df_includes_weight_and_sqft_for_comstock(
        self, minimal_rate_config: dict, calc_one_spy: list
    ) -> None:
        """When weight + sqft provided, both surface as output columns with correct values."""
        calc = UtilityBillCalculator()
        records = [
            {
                "bldg_id": 1,
                "upgrade": 0,
                "load_array": np.full(8760, 1.0),
                "weight": 2.5,
                "sqft": 10000.0,
            },
            {
                "bldg_id": 2,
                "upgrade": 0,
                "load_array": np.full(8760, 1.0),
                "weight": 1.5,
                "sqft": 5000.0,
            },
        ]
        df = calc.calculate_batch_bills(records, minimal_rate_config)
        df_sorted = df.sort("bldg_id")
        assert df_sorted["weight"].to_list() == [2.5, 1.5]
        assert df_sorted["sqft"].to_list() == [10000.0, 5000.0]

    def test_results_df_includes_weight_for_resstock(
        self, minimal_rate_config: dict, calc_one_spy: list
    ) -> None:
        """ResStock-style records (sqft=None) produce null sqft, populated weight."""
        calc = UtilityBillCalculator()
        records = [
            {
                "bldg_id": 1,
                "upgrade": 0,
                "load_array": np.full(8760, 1.0),
                "weight": 188.0,
                "sqft": None,
            },
        ]
        df = calc.calculate_batch_bills(records, minimal_rate_config)
        assert df["weight"].to_list() == [188.0]
        assert df["sqft"].to_list() == [None]

    def test_calculator_does_not_scale_load_internally(
        self, minimal_rate_config: dict, calc_one_spy: list
    ) -> None:
        """The calculator must pass the load array AS-IS to _calc_one (no internal scaling).

        Load scaling is the caller's responsibility (page_05_run.py for ComStock).
        """
        calc = UtilityBillCalculator()
        base_load = np.full(8760, 1.0)
        records = [
            {
                "bldg_id": 1,
                "upgrade": 0,
                "load_array": base_load,
                "weight": 5.0,
                "sqft": 1000.0,
            },
        ]
        calc.calculate_batch_bills(records, minimal_rate_config)
        assert len(calc_one_spy) == 1
        np.testing.assert_array_equal(calc_one_spy[0]["load_array"], base_load)

    def test_multiple_records_same_bldg_id_each_produce_output_row(
        self, minimal_rate_config: dict, calc_one_spy: list
    ) -> None:
        """Two records with the same bldg_id (different weights) produce two output rows."""
        calc = UtilityBillCalculator()
        records = [
            {
                "bldg_id": 7,
                "upgrade": 0,
                "load_array": np.full(8760, 1.0),
                "weight": 1.0,
                "sqft": 1000.0,
            },
            {
                "bldg_id": 7,
                "upgrade": 0,
                "load_array": np.full(8760, 1.0),
                "weight": 2.0,
                "sqft": 1000.0,
            },
        ]
        df = calc.calculate_batch_bills(records, minimal_rate_config)
        assert df.height == 2
        assert sorted(df["weight"].to_list()) == [1.0, 2.0]

    def test_legacy_tuple_load_records_still_accepted(
        self, minimal_rate_config: dict, calc_one_spy: list
    ) -> None:
        """Backward compatibility: old (bldg_id, upgrade, load_array) tuples still work."""
        calc = UtilityBillCalculator()
        records = [(1, 0, np.full(8760, 1.0))]
        df = calc.calculate_batch_bills(records, minimal_rate_config)
        assert df.height == 1
        # No per-row meta supplied → output has no weight/sqft columns
        assert "weight" not in df.columns
        assert "sqft" not in df.columns
