"""
AppState — central dataclass for all Streamlit session state.

All page modules read from and write to this dataclass via st.session_state.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AppState:
    """Holds all user selections and transient state for the app session."""

    # ── Dataset selection ────────────────────────────────────────────────────
    dataset_type: str = "resstock"
    release: str = "2024_release_2"
    selected_upgrades: list[int] = field(default_factory=lambda: [0])

    # ── Building filters ─────────────────────────────────────────────────────
    # Maps column name → list of selected values
    active_filters: dict[str, list[Any]] = field(default_factory=dict)

    # ── Geographic selection ─────────────────────────────────────────────────
    geo_level: str = "state"             # 'state' | 'county' | 'puma' | 'census_tract'
    selected_geo_ids: list[str] = field(default_factory=list)
    # New map-based geographic selections (support mixed county + PUMA)
    selected_states: list[str] = field(default_factory=list)
    selected_county_ids: list[str] = field(default_factory=list)
    selected_puma_ids: list[str] = field(default_factory=list)

    # ── Rate configuration ───────────────────────────────────────────────────
    # Maps scenario key (e.g. "baseline", "upgrade_1") → rate config dict
    rate_assignments: dict[str, dict] = field(default_factory=dict)
    # Maps scenario key → {fuel_type → fuel rate dict (or None for dataset default)}
    # fuel_type is one of "natural_gas", "propane", "fuel_oil"
    fuel_rate_assignments: dict[str, dict[str, dict]] = field(default_factory=dict)

    # ── API / credentials ────────────────────────────────────────────────────
    urdb_api_key: str = ""

    # ── Output ───────────────────────────────────────────────────────────────
    output_dir: Path = field(default_factory=lambda: Path.home() / "buildstock_results")

    # ── Runtime state ────────────────────────────────────────────────────────
    # Cached metadata DataFrame (not persisted across page navigations directly,
    # but held in session_state)
    metadata_loaded: bool = False
    building_count: int = 0
    estimated_download_bytes: int = 0
    results_available: bool = False

    # ── Serialization helpers ────────────────────────────────────────────────

    #: Keys that represent input configuration (saved/loaded); runtime keys excluded.
    _CONFIG_KEYS = (
        "dataset_type",
        "release",
        "selected_upgrades",
        "active_filters",
        "geo_level",
        "selected_geo_ids",
        "selected_states",
        "selected_county_ids",
        "selected_puma_ids",
        "rate_assignments",
        "fuel_rate_assignments",
        "urdb_api_key",
        "output_dir",
    )

    def apply_geo_filter(self, metadata_df: Any) -> Any:
        """Apply geographic filters to a metadata DataFrame.

        Uses OR logic when both county and PUMA selections exist.
        Falls back to legacy ``selected_geo_ids`` when the new fields are empty.
        """
        import polars as pl

        # New map-based selections take priority
        if self.selected_county_ids or self.selected_puma_ids:
            conditions = []
            if self.selected_county_ids and "in.nhgis_county_gisjoin" in metadata_df.columns:
                conditions.append(
                    pl.col("in.nhgis_county_gisjoin").is_in(self.selected_county_ids)
                )
            if self.selected_puma_ids and "in.nhgis_puma_gisjoin" in metadata_df.columns:
                conditions.append(
                    pl.col("in.nhgis_puma_gisjoin").is_in(self.selected_puma_ids)
                )
            if conditions:
                combined = conditions[0]
                for cond in conditions[1:]:
                    combined = combined | cond
                return metadata_df.filter(combined)
        elif self.selected_states:
            if "in.state" in metadata_df.columns:
                return metadata_df.filter(pl.col("in.state").is_in(self.selected_states))

        # Legacy fallback (CSV Upload / By Utility tabs)
        if self.selected_geo_ids:
            if self.geo_level == "state" and "in.state" in metadata_df.columns:
                return metadata_df.filter(pl.col("in.state").is_in(self.selected_geo_ids))
            elif self.geo_level == "county" and "in.nhgis_county_gisjoin" in metadata_df.columns:
                return metadata_df.filter(
                    pl.col("in.nhgis_county_gisjoin").is_in(self.selected_geo_ids)
                )
            elif self.geo_level == "puma" and "in.nhgis_puma_gisjoin" in metadata_df.columns:
                return metadata_df.filter(
                    pl.col("in.nhgis_puma_gisjoin").is_in(self.selected_geo_ids)
                )

        return metadata_df

    def to_config_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict of all input-configuration fields."""
        data: dict[str, Any] = {}
        for key in self._CONFIG_KEYS:
            value = getattr(self, key)
            if isinstance(value, Path):
                value = str(value)
            data[key] = value
        return data

    def apply_config_dict(self, data: dict[str, Any]) -> None:
        """Overwrite input-configuration fields from *data* (e.g. loaded from JSON)."""
        for key in self._CONFIG_KEYS:
            if key not in data:
                continue
            value = data[key]
            if key == "output_dir":
                value = Path(value)
            setattr(self, key, value)

    def save_config(self, path: Path | str) -> None:
        """Serialise input configuration to *path* as JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(self.to_config_dict(), fh, indent=2)

    @classmethod
    def load_config(cls, path: Path | str) -> "AppState":
        """Create a new :class:`AppState` populated from a JSON config file."""
        with Path(path).open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        state = cls()
        state.apply_config_dict(data)
        return state


def init_session_state() -> AppState:
    """
    Initialize st.session_state with an AppState if not already present.
    Returns the current AppState.
    """
    import streamlit as st  # imported locally so tests don't require streamlit runtime
    if "app_state" not in st.session_state:
        st.session_state["app_state"] = AppState()
    return st.session_state["app_state"]
