"""
Session-level helpers for dataset managers and geographically-scoped metadata.

Metadata is loaded only after geographic selections are made (or explicitly for
the whole dataset), restricted to the possibly-necessary columns
(BuildStockDataManager.get_app_metadata_columns), and at the appropriate
geographic resolution: ComStock county/PUMA selections load the
by_state_and_county / by_state_and_puma aggregate files because the national
aggregate only resolves location to state level.
"""

from __future__ import annotations
import hashlib
from typing import Optional, Tuple, Any

import streamlit as st

from buildstock_rate_impact_calculator.data.buildstock_manager import (
    BuildStockDataManager,
)
from buildstock_rate_impact_calculator.ui.state import AppState


def get_manager(app_state: AppState) -> BuildStockDataManager:
    """Return the session-cached manager for the current dataset, with the
    active geographic selection applied."""
    key = f"mgr_{app_state.dataset_type}_{app_state.release}"
    if key not in st.session_state:
        st.session_state[key] = BuildStockDataManager(
            dataset_type=app_state.dataset_type,
            release=app_state.release,
            cache_enabled=True,
            cache_dir=app_state.output_dir,
        )
    mgr: BuildStockDataManager = st.session_state[key]
    sel = app_state.effective_geo_selection()
    mgr.set_geo_selection(
        states=sel["states"],
        county_ids=sel["county_ids"],
        puma_ids=sel["puma_ids"],
    )
    return mgr


def geo_cache_key(app_state: AppState) -> str:
    """Stable hash of the dataset + active geographic selection."""
    sel = app_state.effective_geo_selection()
    parts = "|".join(
        [
            app_state.dataset_type or "",
            app_state.release or "",
            str(sorted(sel["states"])),
            str(sorted(sel["county_ids"])),
            str(sorted(sel["puma_ids"])),
        ]
    )
    return hashlib.md5(parts.encode()).hexdigest()[:12]


def _metadata_session_key(app_state: AppState) -> str:
    return f"metadata_{app_state.dataset_type}_{app_state.release}"


def get_session_metadata(app_state: AppState) -> Optional[Any]:
    """Return the session-cached metadata for the current dataset and
    geographic selection, or None if not loaded yet. Never downloads."""
    entry = st.session_state.get(_metadata_session_key(app_state))
    if isinstance(entry, dict) and entry.get("geo_key") == geo_cache_key(app_state):
        return entry["df"]
    return None


def load_session_metadata(
    app_state: AppState, force_reload: bool = False
) -> Tuple[BuildStockDataManager, Any]:
    """Load (or reuse) metadata for the current dataset + geographic selection.

    Downloads only the possibly-necessary columns, using per-geometry files
    when a county/PUMA selection is active on ComStock. The result is cached in
    session state keyed by the geographic selection.

    Returns:
        (manager, metadata DataFrame)
    """
    mgr = get_manager(app_state)
    gkey = geo_cache_key(app_state)
    mkey = _metadata_session_key(app_state)
    entry = st.session_state.get(mkey)

    if force_reload or not isinstance(entry, dict) or entry.get("geo_key") != gkey:
        metadata = mgr.load_metadata(force_reload=force_reload)
        st.session_state[mkey] = {"geo_key": gkey, "df": metadata}
        app_state.metadata_loaded = True
    else:
        metadata = entry["df"]
        # A manager rebuilt in a fresh session needs its state mapping repopulated
        if not mgr._state_mapping:
            mgr._update_state_mapping(metadata)

    # Populate the dataset-wide scalar weight for ResStock (no-op for ComStock,
    # which uses per-row weights from the metadata). Cached on both the manager
    # and AppState; subsequent calls are free.
    if app_state.resstock_weight is None:
        app_state.resstock_weight = mgr.get_dataset_weight()

    return mgr, metadata
