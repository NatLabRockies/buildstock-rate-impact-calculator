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
    # dataset_type: str = "resstock"
    dataset_type: None | str = (
        None  # e.g. "resstock" or "comstock"; None if not yet selected
    )
    # release: str = "2024_release_2"
    release: None | str = None  # e.g. "2024_release_2"; None if not yet selected
    # selected_upgrades: list[int] = field(default_factory=lambda: [0])
    selected_upgrades: None | list[int] = (
        None  # List of selected upgrade IDs; None if not yet selected
    )

    # ── Building filters ─────────────────────────────────────────────────────
    # Maps column name to either:
    #   - list[Any]  — categorical multiselect (column must match any value)
    #   - dict       — numeric range ``{"min": float, "max": float}`` (either
    #                  bound optional, inclusive on both sides)
    active_filters: dict[str, list[Any] | dict[str, float]] = field(
        default_factory=dict
    )

    # Extra ``in.*`` columns to include in metadata loads and pass through to
    # results. Empty list = use the dataset's default app-metadata columns.
    additional_columns: list[str] = field(default_factory=list)

    # ── Geographic selection ─────────────────────────────────────────────────
    geo_level: str = "state"  # 'state' | 'county' | 'puma' | 'census_tract'
    selected_geo_ids: list[str] = field(default_factory=list)
    # New map-based geographic selections (support mixed county + PUMA)
    selected_states: list[str] = field(default_factory=list)
    selected_county_ids: list[str] = field(default_factory=list)
    selected_puma_ids: list[str] = field(default_factory=list)

    # ── Rate configuration ───────────────────────────────────────────────────
    # Rate libraries — full rate dicts indexed by name (serialised to config)
    rate_library: dict[str, dict] = field(default_factory=dict)
    fuel_rate_library: dict[str, dict] = field(default_factory=dict)
    # Maps scenario key (e.g. "baseline", "upgrade_1") → rate name (key in rate_library)
    rate_assignments: dict[str, str] = field(default_factory=dict)
    # Maps scenario key → {fuel_type → rate name (key in fuel_rate_library) or None}
    # fuel_type is one of "natural_gas", "propane", "fuel_oil"
    fuel_rate_assignments: dict[str, dict[str, str | None]] = field(
        default_factory=dict
    )
    # Maps rate name → URDB label (for URDB rates only); used to re-fetch missing rate files
    rate_label_map: dict[str, str] = field(default_factory=dict)
    # Rate sweep: names of extra electricity rates (keys in rate_library) that
    # every scenario is additionally billed under.
    comparison_rates: list[str] = field(default_factory=list)

    # ── Fast preview sampling ────────────────────────────────────────────────
    # When on, the run pipeline bills a stratified sample of the filtered
    # metadata (n rows per stratum) with weights re-scaled to keep totals
    # unbiased.
    preview_mode: bool = False
    preview_n_per_stratum: int = 20

    # ── API / credentials ────────────────────────────────────────────────────
    urdb_api_key: str = ""

    # ── Output ───────────────────────────────────────────────────────────────
    output_dir: Path = field(default_factory=lambda: Path.home() / "buildstock_results")
    project_name: str = ""

    @property
    def project_dir(self) -> Path:
        """Absolute path to this project's directory: output_dir/projects/{project_name}."""
        return self.output_dir / "projects" / self.project_name

    # ── Runtime state ────────────────────────────────────────────────────────
    # Cached metadata DataFrame (not persisted across page navigations directly,
    # but held in session_state)
    metadata_loaded: bool = False
    building_count: int = 0
    estimated_download_bytes: int = 0
    results_available: bool = False
    # Dataset-wide scalar weight for ResStock (housing units represented by each
    # row); None for ComStock (per-row weight is read from the metadata) and
    # before the first metadata load. Runtime-only; re-derived on dataset load.
    resstock_weight: float | None = None

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
        # rate_library and fuel_rate_library are intentionally excluded:
        # they are saved as individual JSON files under project_dir/rates/
        "rate_assignments",
        "fuel_rate_assignments",
        "rate_label_map",
        "comparison_rates",
        "preview_mode",
        "preview_n_per_stratum",
        "urdb_api_key",
        "output_dir",
        "project_name",
        "additional_columns",
    )

    @staticmethod
    def _to_gisjoin(geo_id: str, level: str) -> str:
        """Normalize a county/PUMA id to GISJOIN format.

        County FIPS '08069' → 'G0800690'; PUMA '0800803' → 'G08000803'.
        Ids already in GISJOIN format (leading 'G') are returned unchanged.
        """
        gid = str(geo_id).strip()
        if gid[:1].upper() == "G":
            return gid
        digits = "".join(ch for ch in gid if ch.isdigit())
        if level == "county" and len(digits) == 5:
            return f"G{digits[:2]}0{digits[2:]}0"
        if level == "puma" and len(digits) == 7:
            return f"G{digits[:2]}0{digits[2:]}"
        return gid

    def effective_geo_selection(self) -> dict[str, list[str]]:
        """Resolve the active geographic selection as GISJOIN-normalized lists.

        Map-based selections take priority; legacy ``selected_geo_ids`` (CSV
        upload) are folded in by ``geo_level`` when the map fields are empty.
        PUMA selections are dropped for ComStock — its metadata is only
        available per county (aggregate PUMA weights are not valid for bill
        calculations), so stale PUMA ids from loaded projects are ignored.

        Returns:
            Dict with keys ``states``, ``county_ids``, ``puma_ids``.
        """
        states = list(self.selected_states)
        counties = [self._to_gisjoin(g, "county") for g in self.selected_county_ids]
        pumas = [self._to_gisjoin(g, "puma") for g in self.selected_puma_ids]

        if not (states or counties or pumas) and self.selected_geo_ids:
            if self.geo_level == "state":
                states = list(self.selected_geo_ids)
            elif self.geo_level == "county":
                counties = [
                    self._to_gisjoin(g, "county") for g in self.selected_geo_ids
                ]
            elif self.geo_level == "puma":
                pumas = [self._to_gisjoin(g, "puma") for g in self.selected_geo_ids]

        if (self.dataset_type or "").lower() == "comstock":
            pumas = []

        return {"states": states, "county_ids": counties, "puma_ids": pumas}

    def apply_geo_filter(self, metadata_df: Any) -> Any:
        """Apply geographic filters to a metadata DataFrame.

        Uses OR logic when both county and PUMA selections exist; falls back to
        state filtering otherwise. Legacy ``selected_geo_ids`` are resolved via
        effective_geo_selection().
        """
        import polars as pl

        sel = self.effective_geo_selection()

        conditions = []
        if sel["county_ids"] and "in.county" in metadata_df.columns:
            conditions.append(pl.col("in.county").is_in(sel["county_ids"]))
        if sel["puma_ids"] and "in.puma" in metadata_df.columns:
            conditions.append(pl.col("in.puma").is_in(sel["puma_ids"]))
        if conditions:
            combined = conditions[0]
            for cond in conditions[1:]:
                combined = combined | cond
            return metadata_df.filter(combined)

        if sel["states"] and "in.state" in metadata_df.columns:
            return metadata_df.filter(pl.col("in.state").is_in(sel["states"]))

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
            # Migrate old-format rate_assignments (full dicts → name strings)
            elif key == "rate_assignments" and isinstance(value, dict):
                value = {
                    sk: (rv.get("name") if isinstance(rv, dict) else rv)
                    for sk, rv in value.items()
                    if rv is not None
                }
            elif key == "fuel_rate_assignments" and isinstance(value, dict):
                value = {
                    sk: {
                        ft: (fv.get("name") if isinstance(fv, dict) else fv)
                        for ft, fv in ft_map.items()
                    }
                    for sk, ft_map in value.items()
                    if isinstance(ft_map, dict)
                }
            setattr(self, key, value)

    def save_config(self, path: Path | str) -> None:
        """Serialise input configuration to *path* as JSON.

        When ``project_name`` is set, delegates to
        :func:`~buildstock_rate_impact_calculator.ui.project_io.save_project`
        which writes ``config.json`` and individual rate files into
        ``project_dir/``.  The *path* argument is ignored in that case.
        Otherwise falls back to writing the config dict to *path*.
        """
        if self.project_name:
            from buildstock_rate_impact_calculator.ui.project_io import save_project

            save_project(self)
            return
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
