"""
Tests for UrdbRateBuilder fixed-charge units, TOU demand, and flat (monthly)
demand support, including conversion to PySAM ElectricityRates parameters.
"""
from __future__ import annotations

import pytest

from buildstock_rate_impact_calculator.rates.rate_builder import (
    FixedChargeUnits,
    RateValidationError,
    UrdbRateBuilder,
)


def _base_builder() -> UrdbRateBuilder:
    """Builder with the minimum required fields: name, utility, one energy tier."""
    return (
        UrdbRateBuilder(validate_pysam=False)
        .basic_info("Test Rate", "Test Utility", "Residential")
        .simple_flat_rate(0.10)
    )


# ── Fixed charge units ────────────────────────────────────────────────────────

def test_fixed_charge_monthly_passthrough():
    rate = _base_builder().fixed_charge(12.5, FixedChargeUnits.MONTHLY).to_pysam_dict()
    assert rate["fixedchargefirstmeter"] == 12.5
    assert rate["fixedchargeunits"] == "$/month"

    params = UrdbRateBuilder.prepare_for_pysam(rate)
    assert params["ur_monthly_fixed_charge"] == pytest.approx(12.5)


def test_fixed_charge_daily_converts_to_monthly():
    rate = _base_builder().fixed_charge(1.0, "$/day").to_pysam_dict()
    assert rate["fixedchargeunits"] == "$/day"

    params = UrdbRateBuilder.prepare_for_pysam(rate)
    assert params["ur_monthly_fixed_charge"] == pytest.approx(365 / 12)


def test_monthly_fixed_charge_delegates_to_fixed_charge():
    rate = _base_builder().monthly_fixed_charge(7.0).to_pysam_dict()
    assert rate["fixedchargefirstmeter"] == 7.0
    assert rate["fixedchargeunits"] == "$/month"


def test_fixed_charge_rejects_unknown_units():
    with pytest.raises(ValueError):
        _base_builder().fixed_charge(5.0, "$/fortnight")


# ── TOU energy periods ────────────────────────────────────────────────────────

def test_multi_period_energy_tou_mat():
    builder = (
        UrdbRateBuilder(validate_pysam=False)
        .basic_info("TOU Rate", "Test Utility", "Commercial")
        .add_energy_tier(0, None, 0.08)
        .add_energy_tier(1, None, 0.15)
        .add_energy_tier(2, None, 0.30)
    )
    # Period 1 in summer afternoons, period 2 in summer evenings
    weekday = [[0] * 24 for _ in range(12)]
    for m in (5, 6, 7):  # Jun-Aug
        for h in range(12, 17):
            weekday[m][h] = 1
        for h in range(17, 21):
            weekday[m][h] = 2
    builder.set_tou_schedule(weekday)

    params = UrdbRateBuilder.prepare_for_pysam(builder.to_pysam_dict())

    rates_by_period = {row[0]: row[4] for row in params["ur_ec_tou_mat"]}
    assert rates_by_period == {1: pytest.approx(0.08), 2: pytest.approx(0.15), 3: pytest.approx(0.30)}
    # PySAM schedules are 1-based
    assert params["ur_ec_sched_weekday"][6][13] == 2
    assert params["ur_ec_sched_weekday"][6][18] == 3
    assert params["ur_ec_sched_weekday"][0][0] == 1


# ── TOU demand charges ────────────────────────────────────────────────────────

def test_tou_demand_periods_and_schedules():
    builder = (
        _base_builder()
        .enable_demand_charges()
        .add_demand_tier(0, None, 0.0)
        .add_demand_tier(1, None, 12.0)
        .add_demand_tier(2, None, 20.0)
    )
    weekday = [[0] * 24 for _ in range(12)]
    for m in range(12):
        for h in range(16, 21):
            weekday[m][h] = 1 if m < 6 else 2
    builder.set_demand_schedule(weekday)

    params = UrdbRateBuilder.prepare_for_pysam(builder.to_pysam_dict())

    assert params["ur_dc_enable"] == 1
    demand_rates = {row[0]: row[3] for row in params["ur_dc_tou_mat"]}
    assert demand_rates == {1: pytest.approx(0.0), 2: pytest.approx(12.0), 3: pytest.approx(20.0)}
    assert params["ur_dc_sched_weekday"][0][17] == 2
    assert params["ur_dc_sched_weekday"][11][17] == 3
    assert params["ur_dc_sched_weekday"][0][0] == 1
    # PySAM fills a $0 flat demand table when only TOU demand is present
    assert all(row[3] == 0 for row in params["ur_dc_flat_mat"])


# ── Flat (monthly) demand charges ─────────────────────────────────────────────

def test_flat_demand_only():
    builder = _base_builder()
    builder.add_flat_demand_tier(0, None, 15.0)   # summer
    builder.add_flat_demand_tier(1, None, 0.0)    # rest of year
    months = [1, 1, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1]  # Jun-Sep = period 0
    builder.set_flat_demand_months(months)

    params = UrdbRateBuilder.prepare_for_pysam(builder.to_pysam_dict())

    assert params["ur_dc_enable"] == 1
    flat_by_month = {row[0]: row[3] for row in params["ur_dc_flat_mat"]}
    assert flat_by_month[5] == pytest.approx(15.0)   # June (0-based)
    assert flat_by_month[0] == pytest.approx(0.0)    # January
    # PySAM fills an empty TOU demand structure when only flat demand is present
    assert params["ur_dc_tou_mat"][0][3] == 0


def test_unused_flat_demand_fields_removed():
    built = UrdbRateBuilder.from_dict(_base_builder().to_pysam_dict()).build()
    assert "flatdemandstructure" not in built
    assert "flatdemandmonths" not in built

    params = UrdbRateBuilder.prepare_for_pysam(_base_builder().to_pysam_dict())
    assert params["ur_dc_enable"] == 0


def test_flat_demand_months_must_reference_existing_periods():
    builder = _base_builder()
    builder.add_flat_demand_tier(0, None, 10.0)
    builder.set_flat_demand_months([1] * 12)  # period 1 has no tiers

    errors = builder.validate()
    assert any("flatdemandmonths references period 1" in e for e in errors)
    with pytest.raises(RateValidationError):
        builder.build()


def test_set_flat_demand_months_requires_12_entries():
    with pytest.raises(ValueError):
        _base_builder().set_flat_demand_months([0] * 11)
