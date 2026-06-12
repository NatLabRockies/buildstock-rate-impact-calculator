"""Tests for load_project orchestrator in project_io."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import pytest

from buildstock_rate_impact_calculator.ui.project_io import load_project
from buildstock_rate_impact_calculator.ui.state import AppState


def _make_project(tmp_path: Path, config: dict, rates: list[dict] | None = None, results_csv: str | None = None) -> Path:
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
            filename = f"{label}.json" if label else re.sub(r"[^\w]", "_", rate_dict.get("name", "rate")) + ".json"
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
