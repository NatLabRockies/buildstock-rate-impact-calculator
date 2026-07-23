"""Fuel-rate fixed charges accrue only for fuels the building consumes.

A building with zero (or null) consumption of a fuel has no service for that
fuel, so the monthly fixed charge must not be billed. Covers the scalar API
(``calculate_fuel_bill``) and both vectorized expression factories.
"""

from __future__ import annotations

import polars as pl
import pytest

from buildstock_rate_impact_calculator.rates.fuel_rate import (
    calculate_fuel_bill,
    flat_fuel_bill_expr,
    block_fuel_bill_expr,
    kwh_per_unit,
)

_THERM_KWH = kwh_per_unit("natural_gas", "$/Therm")

_FLAT_RATE = {
    "name": "NG Flat",
    "fuel_type": "natural_gas",
    "fixed_monthly_charge": 10.0,
    "is_block_rate": False,
    "flat_rate": 1.0,
    "rate_unit": "$/Therm",
}

_BLOCK_RATE = {
    "name": "NG Block",
    "fuel_type": "natural_gas",
    "fixed_monthly_charge": 10.0,
    "is_block_rate": True,
    "block_unit": "$/Therm",
    "blocks": [
        {"consumption_limit": 1.0, "rate": 1.0},
        {"consumption_limit": None, "rate": 5.0},
    ],
}


class TestCalculateFuelBillFixedCharge:
    def test_zero_consumption_no_fixed_charge(self) -> None:
        result = calculate_fuel_bill(0.0, _FLAT_RATE)
        assert result["annual_fuel_bill"] == 0.0
        assert all(m == 0.0 for m in result["monthly_fuel_bills"])

    def test_none_consumption_no_fixed_charge(self) -> None:
        assert calculate_fuel_bill(None, _FLAT_RATE)["annual_fuel_bill"] == 0.0

    def test_zero_consumption_block_no_fixed_charge(self) -> None:
        assert calculate_fuel_bill(0.0, _BLOCK_RATE)["annual_fuel_bill"] == 0.0

    def test_positive_consumption_accrues_fixed_charge(self) -> None:
        annual_kwh = 12.0 * _THERM_KWH  # 1 Therm per month
        result = calculate_fuel_bill(annual_kwh, _FLAT_RATE)
        # 12 × $10 fixed + 12 Therm × $1
        assert result["annual_fuel_bill"] == pytest.approx(132.0, abs=0.01)


class TestFlatFuelBillExprFixedCharge:
    def test_fixed_only_for_rows_with_consumption(self) -> None:
        df = pl.DataFrame({"kwh": [0.0, 12.0 * _THERM_KWH, None]})
        out = df.select(
            flat_fuel_bill_expr(pl.col("kwh"), _FLAT_RATE).alias("bill")
        )
        bills = out["bill"].to_list()
        assert bills[0] == 0.0  # zero consumption → no fixed charge
        assert bills[1] == pytest.approx(132.0)  # 12×$10 + 12 Therm × $1
        assert bills[2] == 0.0  # null consumption → no fixed charge


class TestBlockFuelBillExprFixedCharge:
    def test_fixed_only_for_rows_with_consumption(self) -> None:
        # Row 0: no consumption at all. Row 1: 1 Therm every month.
        data = {f"m{i}": [0.0, _THERM_KWH] for i in range(1, 13)}
        df = pl.DataFrame(data)
        monthly_exprs = [pl.col(f"m{i}") for i in range(1, 13)]
        out = df.select(
            block_fuel_bill_expr(monthly_exprs, _BLOCK_RATE).alias("bill")
        )
        bills = out["bill"].to_list()
        assert bills[0] == 0.0  # no consumption → no fixed charge
        # 12 × $10 fixed + 12 months × (1 Therm in first block × $1)
        assert bills[1] == pytest.approx(132.0)

    def test_single_month_consumption_still_accrues_full_year_fixed(self) -> None:
        # Consumption in one month only → the fuel is connected, so the
        # 12 monthly fixed charges all accrue.
        data = {f"m{i}": [_THERM_KWH if i == 1 else 0.0] for i in range(1, 13)}
        df = pl.DataFrame(data)
        monthly_exprs = [pl.col(f"m{i}") for i in range(1, 13)]
        out = df.select(
            block_fuel_bill_expr(monthly_exprs, _BLOCK_RATE).alias("bill")
        )
        # 12 × $10 fixed + 1 Therm × $1
        assert out["bill"][0] == pytest.approx(121.0)
