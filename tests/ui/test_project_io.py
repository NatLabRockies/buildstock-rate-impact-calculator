"""
Phase 2 tests for project_io module: rate file I/O.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from buildstock_rate_impact_calculator.ui.project_io import (
    rate_to_filename,
    save_project,
    load_project_rates,
)
from buildstock_rate_impact_calculator.ui.state import AppState


# ── rate_to_filename ──────────────────────────────────────────────────────────

def test_rate_to_filename_urdb():
    """URDB rate (has non-empty 'label') → '{label}.json'."""
    rate = {"label": "67b4e679f243c0e331042d79", "name": "ConEd Residential SC1"}
    assert rate_to_filename(rate) == "67b4e679f243c0e331042d79.json"


def test_rate_to_filename_custom():
    """Custom rate (no 'label') → sanitized name."""
    rate = {"name": "My Custom Rate!"}
    # non-alphanumeric chars become _
    assert rate_to_filename(rate) == "My_Custom_Rate_.json"


def test_rate_to_filename_custom_strips_spaces():
    """Spaces in name are replaced with underscores."""
    rate = {"name": "Flat Rate 2024"}
    assert rate_to_filename(rate) == "Flat_Rate_2024.json"


def test_rate_to_filename_empty_label_falls_back_to_name():
    """Empty string label falls back to name-based filename."""
    rate = {"label": "", "name": "My Rate"}
    assert rate_to_filename(rate) == "My_Rate.json"


# ── save_project ──────────────────────────────────────────────────────────────

def test_save_project_writes_config(tmp_path):
    """save_project writes config.json to project_dir."""
    state = AppState()
    state.output_dir = tmp_path
    state.project_name = "Test Project"
    state.dataset_type = "resstock"

    save_project(state)

    config_path = state.project_dir / "config.json"
    assert config_path.exists()
    config = json.loads(config_path.read_text())
    assert config["dataset_type"] == "resstock"
    assert config["project_name"] == "Test Project"


def test_save_project_config_excludes_rate_library(tmp_path):
    """config.json must not contain rate_library or fuel_rate_library."""
    state = AppState()
    state.output_dir = tmp_path
    state.project_name = "Test Project"
    state.rate_library = {"ConEd SC1": {"name": "ConEd SC1", "label": "abc123"}}
    state.fuel_rate_library = {"Gas Flat": {"name": "Gas Flat", "fuel_type": "natural_gas"}}

    save_project(state)

    config = json.loads((state.project_dir / "config.json").read_text())
    assert "rate_library" not in config
    assert "fuel_rate_library" not in config


def test_save_project_writes_rate_files(tmp_path):
    """Each rate in rate_library is written to rates/ as a JSON file."""
    state = AppState()
    state.output_dir = tmp_path
    state.project_name = "Test Project"
    state.rate_library = {"ConEd SC1": {"name": "ConEd SC1", "label": "abc123"}}
    state.fuel_rate_library = {"Gas Flat": {"name": "Gas Flat", "fuel_type": "natural_gas"}}

    save_project(state)

    assert (state.project_dir / "rates" / "abc123.json").exists()
    assert (state.project_dir / "rates" / "Gas_Flat.json").exists()


def test_save_project_rate_label_map_in_config(tmp_path):
    """rate_label_map is written to config.json mapping name → urdb_label."""
    state = AppState()
    state.output_dir = tmp_path
    state.project_name = "Test Project"
    state.rate_library = {
        "ConEd SC1": {"name": "ConEd SC1", "label": "abc123"},
        "Custom Flat": {"name": "Custom Flat"},  # no label
    }

    save_project(state)

    config = json.loads((state.project_dir / "config.json").read_text())
    assert "rate_label_map" in config
    assert config["rate_label_map"]["ConEd SC1"] == "abc123"
    assert "Custom Flat" not in config["rate_label_map"]


def test_save_project_creates_results_dir(tmp_path):
    """save_project creates the results/ subdirectory."""
    state = AppState()
    state.output_dir = tmp_path
    state.project_name = "Test Project"

    save_project(state)

    assert (state.project_dir / "results").is_dir()


# ── load_project_rates ────────────────────────────────────────────────────────

def test_load_project_rates_all_found(tmp_path):
    """All rate files present → full libraries returned, no missing names."""
    rates_dir = tmp_path / "rates"
    rates_dir.mkdir(parents=True)

    rate1 = {"name": "ConEd SC1", "label": "abc123"}
    (rates_dir / "abc123.json").write_text(json.dumps(rate1), encoding="utf-8")
    fuel1 = {"name": "Gas Flat", "fuel_type": "natural_gas"}
    (rates_dir / "Gas_Flat.json").write_text(json.dumps(fuel1), encoding="utf-8")

    rate_assignments = {"baseline": "ConEd SC1"}
    fuel_rate_assignments = {"baseline": {"natural_gas": "Gas Flat"}}
    rate_label_map = {"ConEd SC1": "abc123"}

    lib, fuel_lib, missing = load_project_rates(
        tmp_path, rate_assignments, fuel_rate_assignments, rate_label_map, api_key=""
    )

    assert "ConEd SC1" in lib
    assert "Gas Flat" in fuel_lib
    assert missing == []


def test_load_project_rates_missing_urdb(tmp_path, mocker):
    """Missing URDB rate file with known label → re-fetched via URDBClient."""
    rates_dir = tmp_path / "rates"
    rates_dir.mkdir(parents=True)
    # No files written — rate is missing

    mock_client = mocker.MagicMock()
    fetched_rate = {"name": "ConEd SC1", "label": "abc123", "energyratestructure": []}
    mock_client.get_rate_by_label.return_value = fetched_rate
    mocker.patch(
        "buildstock_rate_impact_calculator.ui.project_io.URDBClient",
        return_value=mock_client,
    )

    rate_assignments = {"baseline": "ConEd SC1"}
    rate_label_map = {"ConEd SC1": "abc123"}

    lib, fuel_lib, missing = load_project_rates(
        tmp_path, rate_assignments, {}, rate_label_map, api_key="test_key"
    )

    mock_client.get_rate_by_label.assert_called_once_with("abc123")
    assert "ConEd SC1" in lib
    assert missing == []


def test_load_project_rates_missing_custom(tmp_path):
    """Missing custom rate (no label) → appears in missing list."""
    rates_dir = tmp_path / "rates"
    rates_dir.mkdir(parents=True)
    # No files

    rate_assignments = {"baseline": "My Custom Rate"}
    rate_label_map = {}  # no label for custom rate

    lib, fuel_lib, missing = load_project_rates(
        tmp_path, rate_assignments, {}, rate_label_map, api_key=""
    )

    assert "My Custom Rate" not in lib
    assert "My Custom Rate" in missing


def test_load_project_rates_missing_urdb_no_api_key(tmp_path):
    """Missing URDB rate but no api_key → treated as missing (no re-fetch)."""
    rates_dir = tmp_path / "rates"
    rates_dir.mkdir(parents=True)

    rate_assignments = {"baseline": "ConEd SC1"}
    rate_label_map = {"ConEd SC1": "abc123"}

    lib, fuel_lib, missing = load_project_rates(
        tmp_path, rate_assignments, {}, rate_label_map, api_key=""
    )

    assert "ConEd SC1" not in lib
    assert "ConEd SC1" in missing


def test_load_project_rates_empty_assignments(tmp_path):
    """Empty assignments → empty libraries, no missing."""
    rates_dir = tmp_path / "rates"
    rates_dir.mkdir(parents=True)

    lib, fuel_lib, missing = load_project_rates(tmp_path, {}, {}, {}, api_key="")
    assert lib == {}
    assert fuel_lib == {}
    assert missing == []


# ── AppState.save_config delegation ──────────────────────────────────────────

def test_save_config_delegates_to_save_project_when_project_name_set(tmp_path):
    """save_config writes to project_dir/config.json when project_name is set."""
    state = AppState()
    state.output_dir = tmp_path
    state.project_name = "My Project"

    state.save_config("ignored_path.json")  # path should be ignored

    assert (state.project_dir / "config.json").exists()


def test_save_config_uses_path_when_no_project_name(tmp_path):
    """save_config writes to the given path when project_name is empty."""
    state = AppState()
    state.output_dir = tmp_path
    state.project_name = ""
    out = tmp_path / "out.json"

    state.save_config(out)

    assert out.exists()
