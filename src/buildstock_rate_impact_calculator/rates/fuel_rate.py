"""
FuelRate — flat and tiered fuel (natural gas, propane, fuel oil) rate calculation.

Unit conversion constants assume standard energy content values:
  - kBtu:         1 kBtu        = 0.293071 kWh
  - Therm:        1 Therm       = 29.3071  kWh  (= 100 kBtu)
  - Ccf (NG):     1 Ccf         = 29.89    kWh  (≈ 102 kBtu at 1020 BTU/cf)
  - Mcf (NG):     1 Mcf         = 298.9    kWh  (= 10 Ccf)
  - gal propane:  1 gallon      = 26.796   kWh  (91,452 BTU/gal)
  - gal fuel oil: 1 gallon      = 40.576   kWh  (138,500 BTU/gal for No. 2)
"""

from __future__ import annotations

from typing import Any

import polars as pl

# ── Constants ────────────────────────────────────────────────────────────────

FUEL_TYPES = ("natural_gas", "propane", "fuel_oil")

FUEL_TYPE_LABELS: dict[str, str] = {
    "natural_gas": "Natural Gas",
    "propane": "Propane",
    "fuel_oil": "Fuel Oil",
}

# kWh per one unit of each rate denomination, keyed by fuel type
FUEL_RATE_UNITS: dict[str, dict[str, float]] = {
    "natural_gas": {
        "$/Mcf": 298.9,
        "$/Ccf": 29.89,
        "$/Therm": 29.3071,
        "$/kBtu": 0.293071,
    },
    "propane": {
        "$/gallon": 26.796,
        "$/Therm": 29.3071,
        "$/kBtu": 0.293071,
    },
    "fuel_oil": {
        "$/gallon": 40.576,
        "$/Therm": 29.3071,
        "$/kBtu": 0.293071,
    },
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def kwh_per_unit(fuel_type: str, unit: str) -> float:
    """Return kWh equivalent of one unit for the given fuel type and rate unit.

    Raises:
        ValueError: If the unit is not supported for the given fuel type.
    """
    units = FUEL_RATE_UNITS.get(fuel_type, {})
    if unit not in units:
        raise ValueError(
            f"Unknown rate unit '{unit}' for fuel type '{fuel_type}'. "
            f"Supported: {list(units.keys())}"
        )
    return units[unit]


def _apply_block_rate(consumption_units: float, blocks: list[dict]) -> float:
    """Apply tiered block rates to a consumption amount (in units).

    Blocks should be ordered by ascending consumption_limit. The final block
    should have ``consumption_limit=None`` to capture unlimited consumption.
    """
    if not blocks or consumption_units <= 0.0:
        return 0.0

    charge = 0.0
    remaining = consumption_units

    for block in blocks:
        limit = block.get("consumption_limit")
        rate = float(block.get("rate", 0.0))
        if limit is None:
            # Unlimited top block — charge all remaining
            charge += remaining * rate
            remaining = 0.0
            break
        limit = float(limit)
        consumed = min(remaining, limit)
        charge += consumed * rate
        remaining -= consumed
        if remaining <= 0.0:
            break

    # Safety: if blocks didn't cover all consumption, apply last block rate
    if remaining > 0.0 and blocks:
        charge += remaining * float(blocks[-1].get("rate", 0.0))

    return charge


# ── Main calculation ─────────────────────────────────────────────────────────


def calculate_fuel_bill(
    annual_energy_kwh: float,
    fuel_rate_dict: dict[str, Any],
) -> dict[str, Any]:
    """Calculate the annual fuel bill from annual energy consumption in kWh.

    Annual energy is distributed evenly across 12 months before applying
    monthly fixed charges and block rate tiers. The monthly fixed charge
    accrues only when the building actually consumes this fuel
    (``annual_energy_kwh > 0``) — a building with no consumption has no
    service to be charged for.

    Args:
        annual_energy_kwh: Total annual fuel energy in kWh (≥ 0; None treated as 0).
        fuel_rate_dict: Fuel rate configuration with keys:
            - ``fuel_type`` (str): one of "natural_gas", "propane", "fuel_oil"
            - ``name`` (str): human-readable rate name
            - ``fixed_monthly_charge`` (float): monthly fixed charge in $
            - ``is_block_rate`` (bool): True → use block rate structure
            - flat mode: ``flat_rate`` (float) + ``rate_unit`` (str)
            - block mode: ``blocks`` (list[dict]) + ``block_unit`` (str)

    Returns:
        dict with:
            - ``annual_fuel_bill`` (float): total annual bill in $.
            - ``monthly_fuel_bills`` (list[float]): 12 equal monthly bill values.
            - ``rate_name`` (str): name of the rate applied.
    """
    annual_energy_kwh = max(0.0, annual_energy_kwh or 0.0)
    monthly_kwh = annual_energy_kwh / 12.0

    fuel_type = fuel_rate_dict.get("fuel_type", "natural_gas")
    name = fuel_rate_dict.get("name", "Custom Fuel Rate")
    fixed = float(fuel_rate_dict.get("fixed_monthly_charge", 0.0))
    # No consumption of this fuel → no service → no fixed charge.
    if annual_energy_kwh <= 0.0:
        fixed = 0.0
    is_block = fuel_rate_dict.get("is_block_rate", False)

    if not is_block:
        rate = float(fuel_rate_dict.get("flat_rate", 0.0))
        unit = fuel_rate_dict.get("rate_unit", "$/Therm")
        kph = kwh_per_unit(fuel_type, unit)
        monthly_energy_charge = rate * (monthly_kwh / kph)
        monthly_bill = fixed + monthly_energy_charge
        monthly_bills = [round(monthly_bill, 4)] * 12
    else:
        unit = fuel_rate_dict.get("block_unit", "$/Therm")
        kph = kwh_per_unit(fuel_type, unit)
        monthly_units = monthly_kwh / kph
        blocks = fuel_rate_dict.get("blocks", [])
        energy_charge = _apply_block_rate(monthly_units, blocks)
        monthly_bill = fixed + energy_charge
        monthly_bills = [round(monthly_bill, 4)] * 12

    annual_bill = round(sum(monthly_bills), 2)
    return {
        "annual_fuel_bill": annual_bill,
        "monthly_fuel_bills": monthly_bills,
        "rate_name": name,
    }


# ── Polars-expression factories (vectorized billing) ─────────────────────────
#
# The functions below return Polars expressions that compute the annual fuel
# bill across a whole DataFrame in a single pass. They are used by
# ``ui.pages.page_05_run._calculate_fuel_bills`` to avoid per-row Python.
#
# The expressions assume the caller has already applied any per-row scaling
# (e.g. ComStock weight × consumption). The expressions operate on the columns
# you pass in.


def flat_fuel_bill_expr(
    annual_kwh_expr: pl.Expr,
    fuel_rate_dict: dict[str, Any],
) -> pl.Expr:
    """Return a Polars expression computing the annual flat-rate fuel bill.

    For flat rates the monthly distribution is irrelevant
    (``12 × fixed + (annual_kwh / kph) × rate``), so the timeseries is not
    needed. The fixed charge accrues only for rows with any consumption of
    this fuel (``annual_kwh > 0``) — buildings without the fuel pay nothing.

    Args:
        annual_kwh_expr: Expression that evaluates to annual fuel kWh per row
            (already scaled by per-row weight if applicable).
        fuel_rate_dict: Rate configuration (see ``calculate_fuel_bill``).
    """
    fuel_type = fuel_rate_dict.get("fuel_type", "natural_gas")
    fixed = float(fuel_rate_dict.get("fixed_monthly_charge", 0.0))
    rate = float(fuel_rate_dict.get("flat_rate", 0.0))
    unit = fuel_rate_dict.get("rate_unit", "$/Therm")
    kph = kwh_per_unit(fuel_type, unit)

    annual_kwh = pl.max_horizontal(annual_kwh_expr.fill_null(0.0), pl.lit(0.0))
    annual_fixed = (
        pl.when(annual_kwh > 0.0).then(pl.lit(12.0 * fixed)).otherwise(pl.lit(0.0))
    )
    return annual_fixed + (annual_kwh / pl.lit(kph)) * pl.lit(rate)


def block_fuel_bill_expr(
    monthly_kwh_exprs: list[pl.Expr],
    fuel_rate_dict: dict[str, Any],
) -> pl.Expr:
    """Return a Polars expression computing the annual block-rate fuel bill.

    For each of the 12 months the consumption (in rate-unit) is run through
    the tiered block rate, then summed plus 12 × monthly fixed charge. The
    fixed charge accrues only for rows with any consumption of this fuel
    across the year — buildings without the fuel pay nothing.

    The tier math is expressed fully vectorized: for tier ``i`` with lower
    bound ``L_i`` and width ``W_i`` (or ``+inf`` for the unbounded top tier),
    the units billed at that tier's rate are
    ``min(max(consumption - L_i, 0), W_i)``.

    Args:
        monthly_kwh_exprs: 12 expressions, one per month, each evaluating to
            that month's fuel consumption in kWh (already scaled by per-row
            weight if applicable). Must contain exactly 12 expressions.
        fuel_rate_dict: Rate configuration (see ``calculate_fuel_bill``).

    Raises:
        ValueError: If ``monthly_kwh_exprs`` does not have length 12.
    """
    if len(monthly_kwh_exprs) != 12:
        raise ValueError(
            f"block_fuel_bill_expr expects 12 monthly expressions, got "
            f"{len(monthly_kwh_exprs)}"
        )
    fuel_type = fuel_rate_dict.get("fuel_type", "natural_gas")
    fixed = float(fuel_rate_dict.get("fixed_monthly_charge", 0.0))
    unit = fuel_rate_dict.get("block_unit", "$/Therm")
    kph = kwh_per_unit(fuel_type, unit)
    blocks = fuel_rate_dict.get("blocks", []) or []

    # Pre-compute (lower_bound, width_or_None, rate) for each tier.
    tiers: list[tuple[float, float | None, float]] = []
    lower = 0.0
    for block in blocks:
        limit = block.get("consumption_limit")
        rate = float(block.get("rate", 0.0))
        if limit is None:
            tiers.append((lower, None, rate))
            break
        width = float(limit) - lower
        if width < 0.0:
            width = 0.0
        tiers.append((lower, width, rate))
        lower = float(limit)

    monthly_clamped_kwh: list[pl.Expr] = []
    monthly_energy_exprs: list[pl.Expr] = []
    for monthly_kwh in monthly_kwh_exprs:
        clamped_kwh = pl.max_horizontal(monthly_kwh.fill_null(0.0), pl.lit(0.0))
        monthly_clamped_kwh.append(clamped_kwh)
        monthly_units = clamped_kwh / pl.lit(kph)
        if not tiers:
            energy_charge: pl.Expr = pl.lit(0.0)
        else:
            tier_exprs: list[pl.Expr] = []
            for lower_b, width, rate in tiers:
                over_lower = pl.max_horizontal(
                    monthly_units - pl.lit(lower_b), pl.lit(0.0)
                )
                if width is None:
                    units_in_tier = over_lower
                else:
                    units_in_tier = pl.min_horizontal(over_lower, pl.lit(width))
                tier_exprs.append(units_in_tier * pl.lit(rate))
            energy_charge = pl.sum_horizontal(tier_exprs)
        monthly_energy_exprs.append(energy_charge)

    # Fixed charges accrue only when the row consumes this fuel at all.
    total_kwh = pl.sum_horizontal(monthly_clamped_kwh)
    annual_fixed = (
        pl.when(total_kwh > 0.0).then(pl.lit(12.0 * fixed)).otherwise(pl.lit(0.0))
    )
    return annual_fixed + pl.sum_horizontal(monthly_energy_exprs)


def fuel_bill_expr(
    fuel_rate_dict: dict[str, Any],
    *,
    annual_kwh_expr: pl.Expr | None = None,
    monthly_kwh_exprs: list[pl.Expr] | None = None,
) -> pl.Expr:
    """Return a Polars expression for the annual fuel bill.

    Dispatches to :func:`flat_fuel_bill_expr` or :func:`block_fuel_bill_expr`
    based on ``fuel_rate_dict["is_block_rate"]``.

    Args:
        fuel_rate_dict: Rate configuration.
        annual_kwh_expr: Required for flat-rate calculations.
        monthly_kwh_exprs: Required (length 12) for block-rate calculations.

    Raises:
        ValueError: If the required expression for the chosen mode is missing.
    """
    if fuel_rate_dict.get("is_block_rate", False):
        if monthly_kwh_exprs is None:
            raise ValueError("block_fuel_bill_expr requires 12 monthly_kwh_exprs")
        return block_fuel_bill_expr(monthly_kwh_exprs, fuel_rate_dict)
    if annual_kwh_expr is None:
        raise ValueError("flat_fuel_bill_expr requires annual_kwh_expr")
    return flat_fuel_bill_expr(annual_kwh_expr, fuel_rate_dict)
