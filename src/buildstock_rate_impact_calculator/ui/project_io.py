"""
Project I/O — save and load project files.

Directory layout written by save_project:

    output_dir/
    └── projects/
        └── {project_name}/
            ├── config.json          (AppState config fields, no rate dicts)
            ├── rates/
            │   ├── {urdb_label}.json        (URDB electricity rates)
            │   └── {sanitized_name}.json    (custom / fuel rates)
            └── results/
                └── bill_results.csv
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from buildstock_rate_impact_calculator.data.buildstock_manager import (
    BuildStockDataManager,
)
from buildstock_rate_impact_calculator.rates.urdb_client import URDBClient

if TYPE_CHECKING:
    from buildstock_rate_impact_calculator.ui.state import AppState


def state_abbrs_from_geo_ids(
    county_ids: list[str] | None = None,
    puma_ids: list[str] | None = None,
) -> list[str]:
    """Return sorted unique USPS state abbreviations implied by GISJOIN ids.

    Each county/PUMA GISJOIN id (e.g. ``G0800690``, ``G08000803``) embeds a
    2-digit state FIPS code at positions ``[1:3]``.  This helper extracts that
    FIPS code via :meth:`BuildStockDataManager.state_for_gisjoin` and returns
    the deduplicated set of state abbreviations as a sorted list.  Ids that
    cannot be parsed are silently skipped.
    """
    abbrs: set[str] = set()
    for geo_id in list(county_ids or []) + list(puma_ids or []):
        abbr = BuildStockDataManager.state_for_gisjoin(geo_id)
        if abbr:
            abbrs.add(abbr)
    return sorted(abbrs)


def rate_to_filename(rate_dict: dict) -> str:
    """Return the filename (with ``.json``) for a rate dict.

    URDB rates (those with a non-empty ``"label"`` field) are named by their
    URDB label.  Custom rates are named by sanitising the ``"name"`` field
    (non-alphanumeric characters replaced with ``_``).
    """
    label = rate_dict.get("label", "")
    if label:
        return f"{label}.json"
    name = rate_dict.get("name", "unnamed_rate")
    sanitized = re.sub(r"[^\w]", "_", name)
    return f"{sanitized}.json"


def save_project(app_state: "AppState") -> None:
    """Save the project config and individual rate files to ``project_dir``.

    Creates the following structure (dirs created if absent)::

        project_dir/
        ├── config.json
        ├── rates/
        └── results/

    ``config.json`` contains all :attr:`AppState._CONFIG_KEYS` fields
    (``rate_library`` and ``fuel_rate_library`` are *not* among them).
    Each rate is written as a separate JSON file under ``rates/``.
    ``rate_label_map`` is populated from the current ``rate_library`` before
    serialising so that missing URDB files can be re-fetched on load.
    """
    project_dir = app_state.project_dir
    rates_dir = project_dir / "rates"
    results_dir = project_dir / "results"

    project_dir.mkdir(parents=True, exist_ok=True)
    rates_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    # Rebuild rate_label_map from current rate_library
    app_state.rate_label_map = {
        name: rate_dict["label"]
        for name, rate_dict in app_state.rate_library.items()
        if rate_dict.get("label")
    }

    # Write config.json (rate_library / fuel_rate_library excluded by _CONFIG_KEYS)
    config_path = project_dir / "config.json"
    with config_path.open("w", encoding="utf-8") as fh:
        json.dump(app_state.to_config_dict(), fh, indent=2)

    # Write individual rate files
    for rate_dict in app_state.rate_library.values():
        _write_rate_file(rates_dir, rate_dict)

    for rate_dict in app_state.fuel_rate_library.values():
        _write_rate_file(rates_dir, rate_dict)


def _write_rate_file(rates_dir: Path, rate_dict: dict) -> None:
    filename = rate_to_filename(rate_dict)
    rate_path = rates_dir / filename
    with rate_path.open("w", encoding="utf-8") as fh:
        json.dump(rate_dict, fh, indent=2)


def load_project_rates(
    project_dir: Path,
    rate_assignments: dict[str, str],
    fuel_rate_assignments: dict[str, dict[str, str | None]],
    rate_label_map: dict[str, str],
    api_key: str,
    scenario_extra_rates: dict[str, list[str]] | None = None,
) -> tuple[dict, dict, list[str]]:
    """Load rate dicts from ``project_dir/rates/``.

    Scans all ``*.json`` files in the rates directory and builds libraries
    keyed by rate name.  For any rate name referenced in the assignments but
    not found in the scanned files:

    - If ``rate_label_map`` contains a URDB label for the name **and**
      ``api_key`` is non-empty, attempts a live re-fetch from the URDB API.
    - Otherwise the name is added to *missing_names*.

    Returns ``(rate_library, fuel_rate_library, missing_names)``.
    """
    rates_dir = project_dir / "rates"

    # Scan all rate files once
    all_loaded: dict[str, dict] = {}
    if rates_dir.exists():
        for rate_file in rates_dir.glob("*.json"):
            try:
                rate_dict = json.loads(rate_file.read_text(encoding="utf-8"))
                name = rate_dict.get("name")
                if name:
                    all_loaded[name] = rate_dict
            except (json.JSONDecodeError, OSError):
                pass

    # Collect all rate names referenced in both assignment dicts
    needed_rate_names: set[str] = set()
    for name in rate_assignments.values():
        if name:
            needed_rate_names.add(name)
    for names in (scenario_extra_rates or {}).values():
        for name in names:
            if name:
                needed_rate_names.add(name)

    needed_fuel_names: set[str] = set()
    for ft_map in fuel_rate_assignments.values():
        if isinstance(ft_map, dict):
            for name in ft_map.values():
                if name:
                    needed_fuel_names.add(name)

    rate_library: dict[str, dict] = {}
    fuel_rate_library: dict[str, dict] = {}
    missing_names: list[str] = []

    for name in needed_rate_names | needed_fuel_names:
        if name in all_loaded:
            rate_dict = all_loaded[name]
            if rate_dict.get("fuel_type"):
                fuel_rate_library[name] = rate_dict
            else:
                rate_library[name] = rate_dict
        else:
            label = rate_label_map.get(name, "")
            if label and api_key:
                fetched = _urdb_fetch(label, api_key)
                if fetched:
                    rate_library[name] = fetched
                    continue
            missing_names.append(name)

    return rate_library, fuel_rate_library, missing_names


def _urdb_fetch(label: str, api_key: str) -> dict | None:
    """Attempt to fetch a single rate from URDB by label. Returns None on failure."""
    try:
        client = URDBClient(api_key=api_key)
        return client.get_rate_by_label(label)
    except Exception:
        return None


def load_project(
    project_dir: Path,
    app_state: "AppState",
    api_key: str,
    session_state: dict | None = None,
) -> list[str]:
    """Load a project from project_dir into app_state.

    Steps:
    1. Read config.json and call app_state.apply_config_dict(config)
    2. Call load_project_rates to restore rate_library and fuel_rate_library
    3. If results/bill_results.csv exists, read it as a Polars DataFrame
       and store in session_state["results_df"]; set session_state["_navigate_to_results"] = True
    4. Store missing rate names in session_state["_missing_rates"]

    Returns the list of missing rate names.

    ``session_state`` defaults to ``st.session_state`` when None (at runtime).
    Tests pass a plain dict to avoid importing Streamlit.
    """
    import polars as pl

    if session_state is None:
        import streamlit as st

        session_state = st.session_state  # type: ignore[assignment]

    # 1. Load config
    config_path = project_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    app_state.apply_config_dict(config)

    # 2. Restore rate libraries
    rate_library, fuel_rate_library, missing = load_project_rates(
        project_dir=project_dir,
        rate_assignments=app_state.rate_assignments,
        fuel_rate_assignments=app_state.fuel_rate_assignments,
        rate_label_map=app_state.rate_label_map,
        api_key=api_key,
        scenario_extra_rates=app_state.scenario_extra_rates,
    )
    app_state.rate_library = rate_library
    app_state.fuel_rate_library = fuel_rate_library

    # 3. Results CSV
    results_csv = project_dir / "results" / "bill_results.csv"
    if results_csv.exists():
        from buildstock_rate_impact_calculator.billing.results import (
            migrate_legacy_result_columns,
        )

        df = migrate_legacy_result_columns(pl.read_csv(results_csv))
        session_state["results_df"] = df
        session_state["_navigate_to_results"] = True

    # 4. Missing rates flag
    session_state["_missing_rates"] = missing

    # 5. Sync map UI state so the Geographic Selection map reflects the loaded
    #    selections instead of showing whatever was previously on screen.
    #    Keys are read by render_map_selector / _render_state_selection_view /
    #    _render_sub_level_view in map_selector.py.
    #
    #    When the saved project has county/PUMA selections but no (or only
    #    partial) explicit ``selected_states``, derive the implied state
    #    abbreviations from the GISJOIN ids so the national choropleth shows
    #    the relevant states highlighted when the user returns to the state
    #    view.
    derived_states = state_abbrs_from_geo_ids(
        county_ids=app_state.selected_county_ids,
        puma_ids=app_state.selected_puma_ids,
    )
    merged_states = sorted(set(app_state.selected_states) | set(derived_states))

    session_state["_map_states_sel"] = set(merged_states)
    session_state["_map_selected_states"] = list(merged_states)
    session_state["_map_county_sel"] = set(app_state.selected_county_ids)
    session_state["_map_puma_sel"] = set(app_state.selected_puma_ids)
    # Choose the most informative view mode automatically:
    #   county selections → county drill-down view
    #   puma selections only → puma drill-down view
    #   otherwise → national state view
    if app_state.selected_county_ids:
        session_state["_map_view_mode"] = "county"
    elif app_state.selected_puma_ids:
        session_state["_map_view_mode"] = "puma"
    else:
        session_state["_map_view_mode"] = "state"
    # Increment map version so the state choropleth Leaflet instance is
    # recreated on next render, causing fit_bounds to zoom to the loaded states.
    session_state["_map_version"] = session_state.get("_map_version", 0) + 1

    return missing
