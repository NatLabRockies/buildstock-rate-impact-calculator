"""Tests for upgrade label fetching and known-typo corrections."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from buildstock_rate_impact_calculator.data import upgrade_labels

RESSTOCK_2025_1 = (
    "https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
    "end-use-load-profiles-for-us-building-stock/2025/resstock_amy2018_release_1"
)
RESSTOCK_2024_2 = (
    "https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
    "end-use-load-profiles-for-us-building-stock/2024/resstock_amy2018_release_2"
)


@pytest.fixture
def fake_lookup(monkeypatch):
    """Serve *payload* as upgrades_lookup.json and clear the label cache."""
    monkeypatch.setattr(upgrade_labels, "_label_cache", {})

    def install(payload: dict[str, str]) -> None:
        response = MagicMock()
        response.json.return_value = payload
        monkeypatch.setattr(upgrade_labels.requests, "get", lambda *a, **k: response)

    return install


def test_resstock_2025_1_heat_pump_water_heater_typo_corrected(fake_lookup):
    fake_lookup({"0": "Baseline", "9": "Heat Pumps Water Heater"})
    labels = upgrade_labels.fetch_upgrade_labels(RESSTOCK_2025_1)
    assert labels[9] == "Heat Pump Water Heater"
    assert labels[0] == "Baseline"


def test_correction_is_noop_once_fixed_upstream(fake_lookup):
    fake_lookup({"9": "Heat Pump Water Heater (Fixed Upstream)"})
    labels = upgrade_labels.fetch_upgrade_labels(RESSTOCK_2025_1)
    assert labels[9] == "Heat Pump Water Heater (Fixed Upstream)"


def test_correction_scoped_to_its_release(fake_lookup):
    fake_lookup({"9": "Heat Pumps Water Heater"})
    labels = upgrade_labels.fetch_upgrade_labels(RESSTOCK_2024_2)
    assert labels[9] == "Heat Pumps Water Heater"
