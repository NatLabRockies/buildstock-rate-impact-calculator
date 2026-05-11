"""
Page 4: Utility Rate Configuration

Three modes:
  1. Search URDB — find and load existing utility rates
  2. Build Custom TOU — use the interactive rate builder
  3. Flat Rate — simple $/kWh

Rate Assignment:
  After defining one or more rates, assign each rate to one or more
  scenario (baseline, upgrade_1, upgrade_2, ...) via checkboxes.
"""
from __future__ import annotations
import streamlit as st

from buildstock_rate_impact_calculator.ui.state import AppState
from buildstock_rate_impact_calculator.ui.components.rate_editor import (
    render_urdb_search,
    render_tou_builder,
    render_flat_rate_form,
)
from buildstock_rate_impact_calculator.data.upgrade_labels import get_upgrade_label


def _scenario_key(upgrade_id: int) -> str:
    return "baseline" if upgrade_id == 0 else f"upgrade_{upgrade_id}"


def render_rates_page(app_state: AppState) -> None:
    """Render the rate configuration and assignment UI."""
    st.header("Utility Rate Configuration")

    # ── Rate library (session-level dict: rate_name → rate_dict) ─────────────
    if "rate_library" not in st.session_state:
        st.session_state["rate_library"] = {}
    rate_library: dict[str, dict] = st.session_state["rate_library"]

    # ── Rate input tabs ───────────────────────────────────────────────────────
    urdb_tab, tou_tab, flat_tab = st.tabs([
        "🔍 Search URDB", "⏱ Build TOU Rate", "📏 Flat Rate"
    ])

    with urdb_tab:
        new_rate = render_urdb_search(api_key=app_state.urdb_api_key, dataset_type=app_state.dataset_type)
        if new_rate:
            key = new_rate.get("name", f"URDB Rate {len(rate_library) + 1}")
            rate_library[key] = new_rate
            st.session_state["rate_library"] = rate_library

    with tou_tab:
        new_rate = render_tou_builder()
        if new_rate:
            key = new_rate.get("name", f"Custom TOU {len(rate_library) + 1}")
            rate_library[key] = new_rate
            st.session_state["rate_library"] = rate_library

    with flat_tab:
        new_rate = render_flat_rate_form()
        if new_rate:
            key = new_rate.get("name", f"Flat Rate {len(rate_library) + 1}")
            rate_library[key] = new_rate
            st.session_state["rate_library"] = rate_library

    st.divider()

    # ── Configured rates list ─────────────────────────────────────────────────
    st.subheader("Configured Rates")
    if not rate_library:
        st.info("No rates configured yet. Use one of the tabs above to add a rate.")
        return

    for rate_name, rate_dict in list(rate_library.items()):
        col1, col2 = st.columns([5, 1])
        with col1:
            with st.expander(f"📋 {rate_name}"):
                st.json({
                    k: v for k, v in rate_dict.items()
                    if k in ("name", "utility", "sector", "fixedchargefirstmeter",
                             "energyratestructure")
                })
        with col2:
            if st.button("🗑 Remove", key=f"remove_rate_{rate_name}"):
                del rate_library[rate_name]
                st.session_state["rate_library"] = rate_library
                st.rerun()

    st.divider()

    # ── Rate assignment matrix ────────────────────────────────────────────────
    st.subheader("Assign Rates to Scenarios")
    st.caption(
        "For each scenario (baseline + selected upgrades), choose which rate "
        "structure to apply. One rate per scenario."
    )

    rate_names = list(rate_library.keys())
    scenarios = app_state.selected_upgrades or [0]

    for upgrade_id in scenarios:
        scenario_key = _scenario_key(upgrade_id)
        label = get_upgrade_label(app_state.dataset_type, upgrade_id)
        scenario_display = f"Upgrade {upgrade_id}: {label}" if upgrade_id > 0 else f"Baseline: {label}"

        current_assignment = app_state.rate_assignments.get(scenario_key, {})
        current_name = current_assignment.get("name") if current_assignment else None

        try:
            current_idx = rate_names.index(current_name) + 1 if current_name in rate_names else 0
        except ValueError:
            current_idx = 0

        chosen = st.selectbox(
            scenario_display,
            options=["— None —"] + rate_names,
            index=current_idx,
            key=f"rate_assign_{scenario_key}",
        )

        if chosen == "— None —":
            if scenario_key in app_state.rate_assignments:
                del app_state.rate_assignments[scenario_key]
        else:
            app_state.rate_assignments[scenario_key] = rate_library[chosen]

    # ── Assignment summary ────────────────────────────────────────────────────
    assigned_count = len(app_state.rate_assignments)
    total_scenarios = len(scenarios)
    if assigned_count == total_scenarios:
        st.success(f"All {total_scenarios} scenario(s) have a rate assigned. Ready for Phase 5.")
    else:
        st.warning(f"{assigned_count} of {total_scenarios} scenario(s) have rates assigned.")
