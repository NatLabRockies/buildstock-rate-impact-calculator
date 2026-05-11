"""
FlatRate — simple flat energy rate structure.

Provides a lightweight dataclass for defining flat (non-TOU) utility rates
and converting them to URDB v8-compatible dicts for use with PySAM.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import copy


@dataclass
class FlatRate:
    """
    A simple flat-rate electricity tariff.

    Args:
        energy_rate: Energy rate in $/kWh. Must be >= 0.
        fixed_monthly_charge: Fixed monthly charge in dollars. Default 0.
        name: Human-readable rate name.
        utility: Utility company name.
        sector: Rate sector ('Residential' or 'Commercial').
    """
    energy_rate: float
    fixed_monthly_charge: float = 0.0
    name: str = "Custom Flat Rate"
    utility: str = "Custom Utility"
    sector: str = "Residential"

    def __post_init__(self) -> None:
        if self.energy_rate < 0:
            raise ValueError(
                f"energy_rate cannot be negative, got {self.energy_rate}. "
                "Use 0.0 for a zero rate."
            )
        if self.fixed_monthly_charge < 0:
            raise ValueError(
                f"fixed_monthly_charge cannot be negative, got {self.fixed_monthly_charge}."
            )

    def to_urdb_dict(self) -> dict[str, Any]:
        """
        Convert to a minimal URDB v8-compatible dict suitable for PySAM.

        The returned dict contains:
        - A single energy tier at the flat rate
        - A flat 12x24 schedule (all period 0)
        - Fixed monthly charge
        - Name, utility, and sector

        Returns:
            URDB v8-compatible rate dict.
        """
        flat_schedule = [[0] * 24 for _ in range(12)]

        return {
            "name": self.name,
            "utility": self.utility,
            "sector": self.sector,
            "fixedchargefirstmeter": self.fixed_monthly_charge,
            "fixedchargeunits": "$/month",
            "energyratestructure": [[{"rate": self.energy_rate, "adj": 0.0, "sell": 0.0}]],
            "energyweekdayschedule": copy.deepcopy(flat_schedule),
            "energyweekendschedule": copy.deepcopy(flat_schedule),
            "demandratestructure": [],
            "demandweekdayschedule": None,
            "demandweekendschedule": None,
            "mincharge": 0.0,
            "minchargeunits": "$/month",
        }
