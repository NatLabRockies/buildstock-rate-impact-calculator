"""Tests for load_project orchestrator in project_io."""

from __future__ import annotations
import json
from pathlib import Path

from buildstock_rate_impact_calculator.ui.project_io import (
    load_project,
    state_abbrs_from_geo_ids,
)
from buildstock_rate_impact_calculator.ui.state import AppState


def _make_project(
    tmp_path: Path,
    config: dict,
    rates: list[dict] | None = None,
    results_csv: str | None = None,
) -> Path:
    """Helper: create a minimal project directory structure."""
    project_dir = tmp_path
    rates_dir = project_dir / "rates"
    results_dir = project_dir / "results"
    rates_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    (project_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    if rates:
        for rate_dict in rates:
            # Use label as filename if present, else sanitize name
            import re

            label = rate_dict.get("label", "")
            filename = (
                f"{label}.json"
                if label
                else re.sub(r"[^\w]", "_", rate_dict.get("name", "rate")) + ".json"
            )
            (rates_dir / filename).write_text(json.dumps(rate_dict), encoding="utf-8")

    if results_csv is not None:
        (results_dir / "bill_results.csv").write_text(results_csv, encoding="utf-8")

    return project_dir


def test_load_project_applies_config(tmp_path):
    """load_project reads config.json and applies it to app_state."""
    config = {
        "dataset_type": "resstock",
        "release": "2025_release_1",
        "selected_upgrades": [0, 1],
        "active_filters": {},
        "geo_level": "state",
        "selected_geo_ids": [],
        "selected_states": ["CO"],
        "selected_county_ids": [],
        "selected_puma_ids": [],
        "rate_assignments": {"baseline": "ConEd SC1"},
        "fuel_rate_assignments": {},
        "rate_label_map": {"ConEd SC1": "abc123"},
        "urdb_api_key": "",
        "output_dir": str(tmp_path),
        "project_name": "Test Project",
    }
    rate = {"name": "ConEd SC1", "label": "abc123"}
    project_dir = _make_project(tmp_path, config, rates=[rate])

    state = AppState()
    session: dict = {}
    missing = load_project(project_dir, state, api_key="", session_state=session)

    assert state.dataset_type == "resstock"
    assert state.release == "2025_release_1"
    assert state.selected_states == ["CO"]
    assert state.rate_assignments == {"baseline": "ConEd SC1"}


def test_load_project_loads_rate_library(tmp_path):
    """load_project populates rate_library from rate files."""
    config = {
        "dataset_type": "resstock",
        "release": "2025_release_1",
        "selected_upgrades": [0],
        "active_filters": {},
        "geo_level": "state",
        "selected_geo_ids": [],
        "selected_states": [],
        "selected_county_ids": [],
        "selected_puma_ids": [],
        "rate_assignments": {"baseline": "ConEd SC1"},
        "fuel_rate_assignments": {},
        "rate_label_map": {"ConEd SC1": "abc123"},
        "urdb_api_key": "",
        "output_dir": str(tmp_path),
        "project_name": "Test",
    }
    rate = {"name": "ConEd SC1", "label": "abc123"}
    project_dir = _make_project(tmp_path, config, rates=[rate])

    state = AppState()
    session: dict = {}
    load_project(project_dir, state, api_key="", session_state=session)

    assert "ConEd SC1" in state.rate_library


def test_load_project_loads_results(tmp_path):
    """load_project reads bill_results.csv → session_state['results_df'] and sets _navigate_to_results."""
    csv_content = "bldg_id,upgrade,annual_bill\n1,0,1200.0\n2,0,950.0\n"
    config = {
        "dataset_type": "resstock",
        "release": "2025_release_1",
        "selected_upgrades": [0],
        "active_filters": {},
        "geo_level": "state",
        "selected_geo_ids": [],
        "selected_states": [],
        "selected_county_ids": [],
        "selected_puma_ids": [],
        "rate_assignments": {},
        "fuel_rate_assignments": {},
        "rate_label_map": {},
        "urdb_api_key": "",
        "output_dir": str(tmp_path),
        "project_name": "Test",
    }
    project_dir = _make_project(tmp_path, config, results_csv=csv_content)

    state = AppState()
    session: dict = {}
    load_project(project_dir, state, api_key="", session_state=session)

    assert session.get("results_df") is not None
    assert session.get("_navigate_to_results") is True


def test_load_project_no_results_no_flag(tmp_path):
    """When no results CSV, session_state should not have _navigate_to_results."""
    config = {
        "dataset_type": "resstock",
        "release": "2025_release_1",
        "selected_upgrades": [0],
        "active_filters": {},
        "geo_level": "state",
        "selected_geo_ids": [],
        "selected_states": [],
        "selected_county_ids": [],
        "selected_puma_ids": [],
        "rate_assignments": {},
        "fuel_rate_assignments": {},
        "rate_label_map": {},
        "urdb_api_key": "",
        "output_dir": str(tmp_path),
        "project_name": "Test",
    }
    project_dir = _make_project(tmp_path, config)

    state = AppState()
    session: dict = {}
    load_project(project_dir, state, api_key="", session_state=session)

    assert "_navigate_to_results" not in session


def test_load_project_missing_rate_sets_flag(tmp_path):
    """Missing custom rate (no file, no label) → _missing_rates in session state."""
    config = {
        "dataset_type": "resstock",
        "release": "2025_release_1",
        "selected_upgrades": [0],
        "active_filters": {},
        "geo_level": "state",
        "selected_geo_ids": [],
        "selected_states": [],
        "selected_county_ids": [],
        "selected_puma_ids": [],
        "rate_assignments": {"baseline": "My Missing Rate"},
        "fuel_rate_assignments": {},
        "rate_label_map": {},  # no label → cannot re-fetch
        "urdb_api_key": "",
        "output_dir": str(tmp_path),
        "project_name": "Test",
    }
    project_dir = _make_project(tmp_path, config)  # no rate files

    state = AppState()
    session: dict = {}
    missing = load_project(project_dir, state, api_key="", session_state=session)

    assert "My Missing Rate" in missing
    assert "My Missing Rate" in session.get("_missing_rates", [])


def test_state_abbrs_from_geo_ids_county_and_puma():
    """state_abbrs_from_geo_ids derives unique sorted state abbrs from GISJOIN ids."""
    # G02… = AK, G08… = CO
    abbrs = state_abbrs_from_geo_ids(
        county_ids=["G0200130", "G0800690", "G0800710"],
        puma_ids=["G02000101"],
    )
    assert abbrs == ["AK", "CO"]


def test_state_abbrs_from_geo_ids_ignores_unparseable():
    """Ids that cannot be parsed are silently skipped."""
    abbrs = state_abbrs_from_geo_ids(
        county_ids=["G0800690", "", "bogus", "G99"],
        puma_ids=None,
    )
    assert abbrs == ["CO"]


def test_load_project_derives_states_from_county_ids(tmp_path):
    """When the saved project has county_ids but no selected_states, the map
    session state is populated with derived state abbreviations so the
    national choropleth still highlights the relevant states."""
    config = {
        "dataset_type": "comstock",
        "release": "2025_release_3",
        "selected_upgrades": [0],
        "active_filters": {},
        "geo_level": "county",
        "selected_geo_ids": [],
        "selected_states": [],  # intentionally empty
        "selected_county_ids": ["G0800690", "G0200130"],  # CO, AK
        "selected_puma_ids": [],
        "rate_assignments": {},
        "fuel_rate_assignments": {},
        "rate_label_map": {},
        "urdb_api_key": "",
        "output_dir": str(tmp_path),
        "project_name": "Test",
    }
    project_dir = _make_project(tmp_path, config)

    state = AppState()
    session: dict = {}
    load_project(project_dir, state, api_key="", session_state=session)

    assert session["_map_selected_states"] == ["AK", "CO"]
    assert session["_map_states_sel"] == {"AK", "CO"}
    assert session["_map_county_sel"] == {"G0800690", "G0200130"}
    assert session["_map_view_mode"] == "county"


def test_load_project_derives_states_from_puma_ids(tmp_path):
    """PUMA-only selections also populate the map state set."""
    config = {
        "dataset_type": "comstock",
        "release": "2025_release_3",
        "selected_upgrades": [0],
        "active_filters": {},
        "geo_level": "puma",
        "selected_geo_ids": [],
        "selected_states": [],
        "selected_county_ids": [],
        "selected_puma_ids": ["G02000101", "G08000803"],  # AK, CO
        "rate_assignments": {},
        "fuel_rate_assignments": {},
        "rate_label_map": {},
        "urdb_api_key": "",
        "output_dir": str(tmp_path),
        "project_name": "Test",
    }
    project_dir = _make_project(tmp_path, config)

    state = AppState()
    session: dict = {}
    load_project(project_dir, state, api_key="", session_state=session)

    assert session["_map_selected_states"] == ["AK", "CO"]
    assert session["_map_states_sel"] == {"AK", "CO"}
    assert session["_map_view_mode"] == "puma"


def test_load_project_merges_explicit_and_derived_states(tmp_path):
    """Explicit selected_states are merged with states derived from county_ids."""
    config = {
        "dataset_type": "comstock",
        "release": "2025_release_3",
        "selected_upgrades": [0],
        "active_filters": {},
        "geo_level": "county",
        "selected_geo_ids": [],
        "selected_states": ["NY"],
        "selected_county_ids": ["G0800690"],  # CO
        "selected_puma_ids": [],
        "rate_assignments": {},
        "fuel_rate_assignments": {},
        "rate_label_map": {},
        "urdb_api_key": "",
        "output_dir": str(tmp_path),
        "project_name": "Test",
    }
    project_dir = _make_project(tmp_path, config)

    state = AppState()
    session: dict = {}
    load_project(project_dir, state, api_key="", session_state=session)

    assert session["_map_selected_states"] == ["CO", "NY"]
    assert session["_map_states_sel"] == {"CO", "NY"}
