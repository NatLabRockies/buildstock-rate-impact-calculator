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

from buildstock_rate_impact_calculator.rates.urdb_client import URDBClient

if TYPE_CHECKING:
    from buildstock_rate_impact_calculator.ui.state import AppState


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
