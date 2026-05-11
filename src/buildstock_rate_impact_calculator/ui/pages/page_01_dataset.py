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
from buildstock_rate_impact_calculator.data.upgrade_labels import get_upgrade_label
from buildstock_rate_impact_calculator.ui.state import AppState

# Human-readable display names for dataset versions
_DATASET_DISPLAY_NAMES = {
    "resstock_2024_release_2": "ResStock 2024 Release 2",
    "resstock_2025_release_1": "ResStock 2025 Release 1",
    "comstock_2024_release_2": "ComStock 2024 Release 2",
}


def render_dataset_page(app_state: AppState) -> None:
    """Render the dataset and upgrade selection UI."""
    st.header("Dataset & Upgrade Selection")

    # ── Dataset type ──────────────────────────────────────────────────────────
    dataset_options = list(BuildStockDataManager.DATASET_CONFIGS.keys())
    display_options = [_DATASET_DISPLAY_NAMES.get(k, k) for k in dataset_options]

    # Determine current selection index
    current_key = f"{app_state.dataset_type}_{app_state.release}"
    try:
        current_idx = dataset_options.index(current_key)
    except ValueError:
        current_idx = 0

    selected_display = st.selectbox(
        "Dataset Version",
        options=display_options,
        index=current_idx,
        help="Select the ResStock or ComStock dataset version to use.",
    )
    selected_key = dataset_options[display_options.index(selected_display)]
    parts = selected_key.split("_", 1)
    new_dataset_type = parts[0]
    new_release = parts[1]

    # Update state if changed
    if new_dataset_type != app_state.dataset_type or new_release != app_state.release:
        app_state.dataset_type = new_dataset_type
        app_state.release = new_release
        # Reset upgrades when dataset changes
        app_state.selected_upgrades = [0]

    # ── Upgrade selection ─────────────────────────────────────────────────────
    st.subheader("Scenarios")
    config = BuildStockDataManager.DATASET_CONFIGS[selected_key]
    upgrade_ids = config.supported_upgrades

    # Build label list: "0 – Baseline", "1 – Basic Enclosure", etc.
    upgrade_labels = [f"{u} – {get_upgrade_label(app_state.dataset_type, u)}" for u in upgrade_ids]

    # Always include baseline (0); user selects additional upgrades
    st.caption(
        "Upgrade 0 (Baseline) is always included. "
        "Select one or more upgrade scenarios to compare against baseline."
    )

    # Pre-select currently stored upgrades
    current_selected_labels = [
        f"{u} – {get_upgrade_label(app_state.dataset_type, u)}"
        for u in app_state.selected_upgrades
        if u in upgrade_ids
    ]

    selected_labels = st.multiselect(
        "Upgrade Scenarios",
        options=upgrade_labels,
        default=current_selected_labels,
        help="Select upgrade(s) to compare. Baseline (0) is always included.",
    )

    # Parse IDs back from labels
    selected_ids = [int(lbl.split(" – ")[0]) for lbl in selected_labels]
    # Always ensure baseline is included
    if 0 not in selected_ids:
        selected_ids = [0] + selected_ids

    app_state.selected_upgrades = selected_ids

    # ── Summary ───────────────────────────────────────────────────────────────
    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Dataset", _DATASET_DISPLAY_NAMES.get(selected_key, selected_key))
    with col2:
        st.metric("Scenarios Selected", len(selected_ids))

    st.success(
        f"Selected: {', '.join(str(u) + ' – ' + get_upgrade_label(app_state.dataset_type, u) for u in selected_ids)}"
    )
