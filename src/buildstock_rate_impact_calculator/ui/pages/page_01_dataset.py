"""
Page 1: Dataset & Upgrade Selection

Lets the user choose:
- Dataset type (ResStock / ComStock)
- Dataset version / release
- Baseline and upgrade scenarios (multi-select with descriptive labels)
"""
from __future__ import annotations
import streamlit as st
from buildstock_rate_impact_calculator.data.buildstock_manager import BuildStockDataManager
from buildstock_rate_impact_calculator.data.upgrade_labels import fetch_upgrade_labels
from buildstock_rate_impact_calculator.ui.state import AppState

# Human-readable display names for dataset versions
_DATASET_DISPLAY_NAMES = {
    "resstock_2024_release_2": "ResStock 2024 Release 2",
    "resstock_2025_release_1": "ResStock 2025 Release 1",
    "comstock_2024_release_2": "ComStock 2024 Release 2",
}


def _clear_loaded_dataset_data() -> None:
    """Clear cached dataset managers/metadata and related summary caches."""
    for key in list(st.session_state.keys()):
        if key.startswith("mgr_") or key.startswith("metadata_") or key.startswith("applicability_summary_"):
            st.session_state.pop(key, None)


def render_dataset_page(app_state: AppState) -> None:
    """Render the dataset and upgrade selection UI."""
    st.header("Dataset & Upgrade Selection")

    # ── Dataset type ──────────────────────────────────────────────────────────
    dataset_options = list(BuildStockDataManager.DATASET_CONFIGS.keys())
    display_options = [_DATASET_DISPLAY_NAMES.get(k, k) for k in dataset_options]

    # Determine current selection index.
    # Keep this unselected on first app start so downstream tabs do not
    # initialize managers or load metadata before explicit user selection.
    current_key = f"{app_state.dataset_type}_{app_state.release}"
    has_existing_dataset = (
        app_state.dataset_type is not None
        and app_state.release is not None
        and current_key in dataset_options
    )
    current_idx = dataset_options.index(current_key) if has_existing_dataset else None

    selected_display = st.selectbox(
        "Dataset Version",
        options=display_options,
        index=current_idx,
        placeholder="Choose a dataset version",
        help="Select the ResStock or ComStock dataset version to use.",
    )

    if selected_display is None:
        st.info("Select a Dataset Version to continue.")
        return

    selected_key = dataset_options[display_options.index(selected_display)]
    parts = selected_key.split("_", 1)
    new_dataset_type = parts[0]
    new_release = parts[1]

    applied_key = (
        f"{app_state.dataset_type}_{app_state.release}"
        if app_state.dataset_type and app_state.release
        else None
    )

    # If user switches dataset version after applying one, discard scenarios
    # and any loaded manager/metadata cache so downstream pages do not reuse
    # stale data.
    if applied_key and selected_key != applied_key:
        app_state.selected_upgrades = None
        app_state.metadata_loaded = False
        _clear_loaded_dataset_data()

    # ── Upgrade selection ─────────────────────────────────────────────────────
    st.subheader("Scenarios")
    config = BuildStockDataManager.DATASET_CONFIGS[selected_key]
    upgrade_ids = config.supported_upgrades

    _labels = fetch_upgrade_labels(config.base_uri)

    # Build label list: "0 - Baseline", "1 - ENERGY STAR heat pump…", etc.
    upgrade_labels = [f"{u} - {_labels.get(u, f'Upgrade {u}')}" for u in upgrade_ids]

    # Always include baseline (0); user selects additional upgrades
    st.caption(
        "Upgrade 0 (Baseline) is always included. "
        "Select one or more upgrade scenarios to compare against baseline."
    )

    # Pre-select currently stored upgrades only for the currently applied dataset.
    current_selected_labels = [
        f"{u} - {_labels.get(u, f'Upgrade {u}')}"
        for u in ((app_state.selected_upgrades or []) if applied_key == selected_key else [])
        if u in upgrade_ids
    ]

    selected_labels = st.multiselect(
        "Upgrade Scenarios",
        options=upgrade_labels,
        default=current_selected_labels,
        key=f"upgrade_scenarios_{selected_key}",
        help="Select upgrade(s) to compare. Baseline (0) is always included.",
    )

    # Parse IDs back from labels
    selected_ids = [int(lbl.split(" - ")[0]) for lbl in selected_labels]
    if selected_ids and 0 not in selected_ids:
        selected_ids = [0] + selected_ids

    # ── Auto-apply when both fields are set ──────────────────────────────────
    if selected_ids:
        app_state.dataset_type = new_dataset_type
        app_state.release = new_release
        app_state.selected_upgrades = selected_ids
    elif applied_key == selected_key:
        # User cleared upgrade selection for the currently applied dataset.
        app_state.selected_upgrades = None

    # ── Summary ───────────────────────────────────────────────────────────────
    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        current_dataset = (
            _DATASET_DISPLAY_NAMES.get(f"{app_state.dataset_type}_{app_state.release}", "Not Applied")
            if app_state.dataset_type and app_state.release
            else "Not Applied"
        )
        st.metric("Current Dataset", current_dataset)
    with col2:
        st.metric("Current Scenarios", len(app_state.selected_upgrades or []))

    if selected_ids:
        st.success(
            f"Selected: {', '.join(str(u) + ' - ' + _labels.get(u, f'Upgrade {u}') for u in selected_ids)}"
        )
        st.caption("Selections auto-apply when dataset and one or more scenarios are set.")
    else:
        st.info("Select one or more Upgrade Scenarios to apply this dataset.")
