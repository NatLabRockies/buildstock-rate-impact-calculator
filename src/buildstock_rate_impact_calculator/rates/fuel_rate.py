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
    monthly fixed charges and block rate tiers.

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
