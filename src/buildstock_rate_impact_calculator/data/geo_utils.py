"""
Geographic utilities for BuildStock TOU Calculator.

Provides helpers for parsing, validating, and querying geographic identifiers
(states, counties, PUMAs, census tracts) and utility company lists from URDB.
"""

from __future__ import annotations
import json
from functools import lru_cache
from importlib.resources import files

import requests
from typing import Tuple

# Canonical set of US state abbreviations (50 states + DC + territories used in BuildStock)
_US_STATES: frozenset[str] = frozenset(
    {
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "DC",
    }
)

# Geo levels supported per dataset type. ComStock excludes PUMA: only the
# non-aggregate by_state_and_county metadata carries per-row weights that are
# valid for utility-bill calculations (aggregate weights are not).
_SUPPORTED_GEO_LEVELS: dict[str, set[str]] = {
    "resstock": {"state", "county", "puma"},
    "comstock": {"state", "county", "census_tract"},
}

_URDB_BASE_URL = "https://api.openei.org/utility_rates"


@lru_cache(maxsize=1)
def _county_gisjoins_by_state() -> dict[str, tuple[str, ...]]:
    """Map USPS state abbreviation → sorted county GISJOIN ids, parsed once
    from the bundled Census county boundary file."""
    # Imported lazily to avoid a circular module import.
    from buildstock_rate_impact_calculator.data.buildstock_manager import (
        _STATEFP_TO_USPS,
    )

    data_path = files("buildstock_rate_impact_calculator.data").joinpath(
        "cb_2019_us_county_500k.geojson"
    )
    with data_path.open("r", encoding="utf-8") as fh:
        geojson = json.load(fh)

    by_state: dict[str, set[str]] = {}
    for feat in geojson.get("features", []):
        props = feat.get("properties", {})
        statefp = props.get("STATEFP")
        countyfp = props.get("COUNTYFP")
        abbr = _STATEFP_TO_USPS.get(statefp)
        if abbr and countyfp:
            by_state.setdefault(abbr, set()).add(f"G{statefp}0{countyfp}0")
    return {abbr: tuple(sorted(ids)) for abbr, ids in by_state.items()}


def counties_for_states(states: list[str] | tuple[str, ...]) -> list[str]:
    """Return the sorted county GISJOIN ids covering the given states.

    Used to expand whole-state selections into per-county metadata files for
    datasets without a national metadata file (ComStock). Unknown state
    abbreviations are silently skipped.
    """
    mapping = _county_gisjoins_by_state()
    result: set[str] = set()
    for state in states:
        result.update(mapping.get(str(state).strip().upper(), ()))
    return sorted(result)


def parse_csv_geo_ids(csv_bytes: bytes, geo_level: str) -> list[str]:
    """
    Parse geographic IDs from raw CSV bytes.

    Accepts a single-column CSV (with or without header). Strips whitespace,
    uppercases state codes, and returns a deduplicated list.

    Args:
        csv_bytes: Raw bytes of the uploaded CSV file.
        geo_level: One of 'state', 'county', 'puma', 'census_tract'.

    Returns:
        List of geographic ID strings.

    Raises:
        ValueError: If csv_bytes is empty or no IDs could be parsed.
    """
    if not csv_bytes or not csv_bytes.strip():
        raise ValueError("CSV content is empty — no geographic IDs found.")

    text = csv_bytes.decode("utf-8", errors="replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    ids: list[str] = []
    for line in lines:
        # Take only the first column if comma-separated
        value = line.split(",")[0].strip().strip('"')
        if not value:
            continue
        # Skip obvious header rows
        if value.lower() in ("state", "county", "puma", "census_tract", "geo_id", "id"):
            continue
        if geo_level == "state":
            value = value.upper()
        ids.append(value)

    if not ids:
        raise ValueError("CSV content is empty — no geographic IDs found.")

    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for v in ids:
        if v not in seen:
            seen.add(v)
            result.append(v)
    return result


def validate_geo_ids(
    ids: list[str],
    geo_level: str,
    dataset_type: str,
) -> Tuple[list[str], list[str]]:
    """
    Validate a list of geographic IDs for a given level and dataset type.

    Args:
        ids: List of geographic ID strings to validate.
        geo_level: One of 'state', 'county', 'puma', 'census_tract'.
        dataset_type: One of 'resstock' or 'comstock'.

    Returns:
        Tuple of (valid_ids, invalid_ids).

    Raises:
        ValueError: If geo_level is not supported for the given dataset_type.
    """
    dtype = dataset_type.lower()
    supported = _SUPPORTED_GEO_LEVELS.get(dtype, set())
    if geo_level not in supported:
        raise ValueError(
            f"geo_level '{geo_level}' is not supported for {dataset_type}. "
            f"Supported levels: {sorted(supported)}"
        )

    valid: list[str] = []
    invalid: list[str] = []

    for geo_id in ids:
        if geo_level == "state":
            if geo_id.upper() in _US_STATES:
                valid.append(geo_id)
            else:
                invalid.append(geo_id)
        elif geo_level == "county":
            # FIPS county codes are 5 digits
            stripped = geo_id.replace("-", "").replace(" ", "")
            if stripped.isdigit() and len(stripped) == 5:
                valid.append(geo_id)
            else:
                invalid.append(geo_id)
        elif geo_level == "puma":
            # PUMAs are 7-digit codes (2-digit state FIPS + 5-digit PUMA)
            stripped = geo_id.replace("-", "").replace(" ", "")
            if stripped.isdigit() and len(stripped) in (5, 7):
                valid.append(geo_id)
            else:
                invalid.append(geo_id)
        elif geo_level == "census_tract":
            # Census tract codes are 11 digits
            stripped = geo_id.replace("-", "").replace(" ", "")
            if stripped.isdigit() and len(stripped) == 11:
                valid.append(geo_id)
            else:
                invalid.append(geo_id)
        else:
            invalid.append(geo_id)

    return valid, invalid


def get_utility_list(
    api_key: str, state: str, sector: str = "Residential"
) -> list[dict]:
    """
    Fetch a list of utility companies serving a given state from URDB.

    Makes a direct URDB API call to search for rates by state abbreviation,
    then deduplicates by utility name.

    Args:
        api_key: OpenEI API key.
        state: Two-letter US state abbreviation.
        sector: Rate sector ('Residential', 'Commercial', etc.).

    Returns:
        List of dicts with keys 'utility' (name) and 'label' (URDB label/ID).
        Empty list if no results or API error.
    """
    params = {
        "api_key": api_key,
        "version": "latest",
        "format": "json",
        "detail": "minimal",
        "state": state,
        "sector": sector,
        "limit": 100,
        "orderby": "utility",
        "direction": "asc",
    }
    try:
        response = requests.get(_URDB_BASE_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        items = data.get("items", [])
    except Exception:
        return []

    # Deduplicate by utility name, keep first occurrence
    seen_names: set[str] = set()
    result: list[dict] = []
    for item in items:
        name = item.get("utility", "").strip()
        if name and name not in seen_names:
            seen_names.add(name)
            result.append({"utility": name, "label": item.get("label", "")})

    return result
