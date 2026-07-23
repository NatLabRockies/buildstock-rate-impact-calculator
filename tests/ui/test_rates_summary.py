"""Unit tests for the Configured Rates summary helpers on the Results page."""

from __future__ import annotations

import pytest

from buildstock_rate_impact_calculator.data import upgrade_labels
from buildstock_rate_impact_calculator.ui.pages.page_06_results import (
    _build_rates_summary_rows,
    _electricity_charge_summary,
    _fuel_charge_summary,
)
from buildstock_rate_impact_calculator.ui.state import AppState

_BASE_URI = "https://example.test/dataset"


@pytest.fixture(autouse=True)
def _seed_label_cache():
    """Avoid network fetches for scenario labels."""
    upgrade_labels._label_cache[_BASE_URI] = {0: "Baseline", 1: "Heat Pump"}
    yield
    upgrade_labels._label_cache.pop(_BASE_URI, None)


class TestElectricityChargeSummary:
    def test_flat_single_tier(self) -> None:
        rate = {
            "energyratestructure": [[{"rate": 0.12}]],
            "fixedchargefirstmeter": 10.0,
            "fixedchargeunits": "$/month",
        }
        parts = _electricity_charge_summary(rate)
        assert parts["fixed"] == "$10.00/month"
        assert parts["energy"] == "$0.1200/kWh"
        assert parts["demand"] == "—"

    def test_tou_tiered_with_adj(self) -> None:
        rate = {
            "energyratestructure": [
                [{"rate": 0.08, "adj": 0.01}, {"rate": 0.10, "adj": 0.01}],
                [{"rate": 0.20}],
            ],
        }
        parts = _electricity_charge_summary(rate)
        assert parts["energy"] == "$0.0900–$0.2000/kWh (2 TOU periods, tiered)"
        assert parts["fixed"] == "—"

    def test_demand_charges(self) -> None:
        rate = {
            "energyratestructure": [[{"rate": 0.1}]],
            "demandratestructure": [[{"rate": 5.0}], [{"rate": 15.0}]],
            "flatdemandstructure": [[{"rate": 8.0}]],
        }
        parts = _electricity_charge_summary(rate)
        assert parts["demand"] == "$5.00–$15.00/kW (TOU); $8.00/kW (monthly)"

    def test_unfetched_urdb_rate(self) -> None:
        parts = _electricity_charge_summary({"label": "abc123"})
        assert "abc123" in parts["energy"]
        assert "fetched at run time" in parts["energy"]


class TestFuelChargeSummary:
    def test_flat_rate(self) -> None:
        rate = {
            "fixed_monthly_charge": 12.5,
            "is_block_rate": False,
            "flat_rate": 1.2,
            "rate_unit": "$/Therm",
        }
        parts = _fuel_charge_summary(rate)
        assert parts["fixed"] == "$12.50/month"
        assert parts["energy"] == "$1.20/Therm flat"
        assert parts["demand"] == "—"

    def test_block_rate(self) -> None:
        rate = {
            "fixed_monthly_charge": 0.0,
            "is_block_rate": True,
            "block_unit": "$/Therm",
            "blocks": [
                {"consumption_limit": 50.0, "rate": 0.9},
                {"consumption_limit": None, "rate": 1.5},
            ],
        }
        parts = _fuel_charge_summary(rate)
        assert parts["energy"] == "$0.90–$1.50/Therm (2 blocks)"


class TestBuildRatesSummaryRows:
    def _app_state(self) -> AppState:
        state = AppState()
        state.rate_library = {
            "Elec Flat": {
                "energyratestructure": [[{"rate": 0.12}]],
                "fixedchargefirstmeter": 10.0,
            }
        }
        state.rate_assignments = {"baseline": "Elec Flat", "upgrade_1": "Elec Flat"}
        state.fuel_rate_library = {
            "NG Flat": {
                "fuel_type": "natural_gas",
                "fixed_monthly_charge": 5.0,
                "is_block_rate": False,
                "flat_rate": 1.1,
                "rate_unit": "$/Therm",
            }
        }
        state.fuel_rate_assignments = {"upgrade_1": {"natural_gas": "NG Flat"}}
        return state

    def test_electricity_row_groups_scenarios(self) -> None:
        rows = _build_rates_summary_rows(
            self._app_state(),
            upgrade_ids=[0, 1],
            base_uri=_BASE_URI,
            result_columns={"electricity_annual_bill"},
        )
        elec = [r for r in rows if r["Energy Source"] == "Electricity"]
        assert len(elec) == 1
        assert elec[0]["Rate"] == "Elec Flat"
        assert "Baseline" in elec[0]["Applies To"]
        assert "Upgrade 1: Heat Pump" in elec[0]["Applies To"]

    def test_fuel_rows_split_assigned_and_default(self) -> None:
        rows = _build_rates_summary_rows(
            self._app_state(),
            upgrade_ids=[0, 1],
            base_uri=_BASE_URI,
            result_columns={"electricity_annual_bill", "natural_gas_bill"},
        )
        ng = [r for r in rows if r["Energy Source"] == "Natural Gas"]
        assert len(ng) == 2
        by_rate = {r["Rate"]: r for r in ng}
        # Baseline had no NG rate assigned → dataset default
        assert by_rate["Dataset default"]["Applies To"] == "Baseline"
        assert (
            by_rate["Dataset default"]["Energy Charges"]
            == "Annual fuel bill from dataset metadata"
        )
        assert by_rate["NG Flat"]["Applies To"] == "Upgrade 1: Heat Pump"
        assert by_rate["NG Flat"]["Energy Charges"] == "$1.10/Therm flat"

    def test_fuels_absent_from_results_are_skipped(self) -> None:
        rows = _build_rates_summary_rows(
            self._app_state(),
            upgrade_ids=[0, 1],
            base_uri=_BASE_URI,
            result_columns={"electricity_annual_bill"},  # no fuel bill columns
        )
        assert all(r["Energy Source"] == "Electricity" for r in rows)

    def test_no_assignments_yields_no_rows(self) -> None:
        state = AppState()
        rows = _build_rates_summary_rows(
            state,
            upgrade_ids=[0],
            base_uri=_BASE_URI,
            result_columns={"electricity_annual_bill"},
        )
        assert rows == []

    def test_extra_rates_appear_in_summary(self) -> None:
        state = self._app_state()
        state.rate_library["Elec TOU"] = {
            "energyratestructure": [[{"rate": 0.20}]],
        }
        # Baseline is also billed under the extra "Elec TOU" rate.
        state.scenario_extra_rates = {"baseline": ["Elec TOU"]}
        rows = _build_rates_summary_rows(
            state,
            upgrade_ids=[0, 1],
            base_uri=_BASE_URI,
            result_columns={"electricity_annual_bill"},
        )
        by_rate = {r["Rate"]: r for r in rows if r["Energy Source"] == "Electricity"}
        assert "Elec TOU" in by_rate
        assert by_rate["Elec TOU"]["Applies To"] == "Baseline"
