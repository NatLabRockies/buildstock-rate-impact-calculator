"""
Page 4: Energy Rate Configuration

Electricity rates:
  1. Search URDB — find and load existing utility rates
  2. Build Custom TOU — use the interactive rate builder
  3. Flat Rate — simple $/kWh

Fuel rates (natural gas, propane, fuel oil):
  - Flat rate or tiered block rate with monthly fixed charge.

Rate Assignment:
  After defining rates, assign each rate to one or more scenarios
  (baseline, upgrade_1, upgrade_2, ...).
"""
from __future__ import annotations
import streamlit as st

from buildstock_rate_impact_calculator.ui.state import AppState
from buildstock_rate_impact_calculator.ui.components.rate_editor import (
    render_urdb_search,
    render_tou_builder,
    render_flat_rate_form,
)
from buildstock_rate_impact_calculator.data.buildstock_manager import BuildStockDataManager
from buildstock_rate_impact_calculator.data.upgrade_labels import get_upgrade_label
from buildstock_rate_impact_calculator.rates.fuel_rate import (
    FUEL_TYPES,
    FUEL_TYPE_LABELS,
    FUEL_RATE_UNITS,
)


def _scenario_key(upgrade_id: int) -> str:
    return "baseline" if upgrade_id == 0 else f"upgrade_{upgrade_id}"


# ── Fuel rate form ────────────────────────────────────────────────────────────

def _render_fuel_rate_form(fuel_rate_library: dict) -> dict | None:
    """Render the fuel rate input form. Returns a new rate dict when submitted."""

    # ── Fuel type selector ────────────────────────────────────────────────────
    fuel_type = st.selectbox(
        "Fuel Type",
        options=list(FUEL_TYPES),
        format_func=lambda ft: FUEL_TYPE_LABELS[ft],
        key="fuel_form_type",
    )

    unit_options = list(FUEL_RATE_UNITS[fuel_type].keys())

    # ── Rate name ─────────────────────────────────────────────────────────────
    default_name = f"Custom {FUEL_TYPE_LABELS[fuel_type]} Rate"
    rate_name = st.text_input("Rate Name", value=default_name, key="fuel_form_name")

    # ── Fixed charge ──────────────────────────────────────────────────────────
    fixed_charge = st.number_input(
        "Monthly Fixed Charge ($)",
        min_value=0.0,
        value=0.0,
        step=0.50,
        format="%.2f",
        key="fuel_form_fixed",
    )

    # ── Block rate toggle ─────────────────────────────────────────────────────
    is_block = st.checkbox("Add Blocks (tiered rate)", key="fuel_form_is_block")

    if not is_block:
        # ── Flat rate ─────────────────────────────────────────────────────────
        col_rate, col_unit = st.columns([3, 2])
        with col_rate:
            flat_rate = st.number_input(
                "Monthly Fuel Rate",
                min_value=0.0,
                value=0.0,
                step=0.01,
                format="%.4f",
                key="fuel_form_flat_rate",
            )
        with col_unit:
            rate_unit = st.selectbox(
                "Unit",
                options=unit_options,
                key="fuel_form_flat_unit",
                label_visibility="hidden",
            )

        rate_dict = {
            "type": "fuel_rate",
            "fuel_type": fuel_type,
            "name": rate_name,
            "fixed_monthly_charge": fixed_charge,
            "is_block_rate": False,
            "flat_rate": flat_rate,
            "rate_unit": rate_unit,
        }

    else:
        # ── Block rate ────────────────────────────────────────────────────────
        block_unit = st.selectbox(
            "Unit (applies to all blocks)",
            options=unit_options,
            key="fuel_form_block_unit",
        )

        st.caption(
            "Leave the Consumption field blank for the highest (unlimited) block."
        )

        # Manage block rows in session state
        if "fuel_blocks" not in st.session_state or st.session_state.get("fuel_blocks_fuel_type") != fuel_type:
            st.session_state["fuel_blocks"] = [
                {"consumption_limit": None, "rate": 0.0},
                {"consumption_limit": None, "rate": 0.0},
            ]
            st.session_state["fuel_blocks_fuel_type"] = fuel_type

        blocks_state: list[dict] = st.session_state["fuel_blocks"]

        # Table header
        h1, h2, h3 = st.columns([3, 3, 1])
        h1.markdown(f"**Consumption ({block_unit.split('/')[1]})**")
        h2.markdown(f"**Rate ({block_unit})**")
        h3.markdown("** **")

        updated_blocks: list[dict] = []
        to_delete: int | None = None

        for i, block in enumerate(blocks_state):
            c1, c2, c3 = st.columns([3, 3, 1])
            cons_val = block.get("consumption_limit")
            rate_val = block.get("rate", 0.0)

            cons_str = c1.text_input(
                f"Consumption {i}",
                value="" if cons_val is None else str(cons_val),
                key=f"fb_cons_{i}",
                label_visibility="collapsed",
                placeholder="(blank = no limit)",
            )
            rate_str = c2.text_input(
                f"Rate {i}",
                value=str(rate_val),
                key=f"fb_rate_{i}",
                label_visibility="collapsed",
            )
            if c3.button("✕", key=f"fb_del_{i}", help="Remove this block"):
                to_delete = i

            try:
                cons_parsed: float | None = float(cons_str) if cons_str.strip() else None
            except ValueError:
                cons_parsed = None
            try:
                rate_parsed = float(rate_str) if rate_str.strip() else 0.0
            except ValueError:
                rate_parsed = 0.0

            updated_blocks.append({"consumption_limit": cons_parsed, "rate": rate_parsed})

        if to_delete is not None:
            updated_blocks.pop(to_delete)
            st.session_state["fuel_blocks"] = updated_blocks
            st.rerun()

        st.session_state["fuel_blocks"] = updated_blocks

        if st.button("＋ Add Block", key="fuel_add_block"):
            st.session_state["fuel_blocks"].append({"consumption_limit": None, "rate": 0.0})
            st.rerun()

        rate_dict = {
            "type": "fuel_rate",
            "fuel_type": fuel_type,
            "name": rate_name,
            "fixed_monthly_charge": fixed_charge,
            "is_block_rate": True,
            "blocks": updated_blocks,
            "block_unit": block_unit,
        }

    # ── Submit ────────────────────────────────────────────────────────────────
    if st.button("Add Fuel Rate", type="primary", key="fuel_form_submit"):
        if not rate_name.strip():
            st.error("Rate Name is required.")
            return None
        # Prevent duplicate names
        if rate_name in fuel_rate_library:
            st.warning(f"A fuel rate named '{rate_name}' already exists. Rename it or remove the existing one.")
            return None
        return rate_dict

    return None


def render_rates_page(app_state: AppState) -> None:
    """Render the energy rate configuration and assignment UI."""
    st.header("Energy Rate Configuration")

    if not app_state.dataset_type or not app_state.release:
        st.warning("Please select a dataset on the Dataset tab first.")
        return

    # ── Derive dataset base_uri for upgrade label lookups ─────────────────────
    _config_key = f"{app_state.dataset_type}_{app_state.release}"
    _base_uri = BuildStockDataManager.DATASET_CONFIGS[_config_key].base_uri

    # ── Rate libraries (session-level dicts) ──────────────────────────────────
    if "rate_library" not in st.session_state:
        st.session_state["rate_library"] = {}
    if "fuel_rate_library" not in st.session_state:
        st.session_state["fuel_rate_library"] = {}
    rate_library: dict[str, dict] = st.session_state["rate_library"]
    fuel_rate_library: dict[str, dict] = st.session_state["fuel_rate_library"]

    # ── Rate input tabs ───────────────────────────────────────────────────────
    urdb_tab, tou_tab, flat_tab, fuel_tab = st.tabs([
        "🔍 Search URDB", "⏱ Build TOU Rate", "📏 Flat Rate", "⛽ Fuel Rates"
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

    with fuel_tab:
        new_fuel_rate = _render_fuel_rate_form(fuel_rate_library)
        if new_fuel_rate:
            fuel_rate_library[new_fuel_rate["name"]] = new_fuel_rate
            st.session_state["fuel_rate_library"] = fuel_rate_library
            st.success(f"Added fuel rate: {new_fuel_rate['name']}")
            st.rerun()

    st.divider()

    # ── Configured rates ──────────────────────────────────────────────────────
    st.subheader("Configured Rates")
    st.caption(
        "ℹ️ Energy charges not configured will be taken from dataset defaults."
    )

    # Electricity rates
    st.markdown("**⚡ Electricity Rates**")
    if not rate_library:
        st.info("No electricity rates configured yet.")
    else:
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
                    # Clear any assignments pointing to this rate
                    for sk in list(app_state.rate_assignments.keys()):
                        if app_state.rate_assignments[sk].get("name") == rate_name:
                            del app_state.rate_assignments[sk]
                    st.rerun()

    # Fuel rates per type
    for ft in FUEL_TYPES:
        ft_label = FUEL_TYPE_LABELS[ft]
        icon = {"natural_gas": "🔥", "propane": "🛢️", "fuel_oil": "🛢️"}[ft]
        st.markdown(f"**{icon} {ft_label} Rates**")
        ft_rates = {n: r for n, r in fuel_rate_library.items() if r.get("fuel_type") == ft}
        if not ft_rates:
            st.info(f"No {ft_label} rates configured. Using dataset defaults.")
        else:
            for rate_name, rate_dict in list(ft_rates.items()):
                col1, col2 = st.columns([5, 1])
                with col1:
                    with st.expander(f"📋 {rate_name}"):
                        display = {
                            "fuel_type": rate_dict.get("fuel_type"),
                            "fixed_monthly_charge": rate_dict.get("fixed_monthly_charge"),
                            "is_block_rate": rate_dict.get("is_block_rate"),
                        }
                        if rate_dict.get("is_block_rate"):
                            display["block_unit"] = rate_dict.get("block_unit")
                            display["blocks"] = rate_dict.get("blocks")
                        else:
                            display["flat_rate"] = rate_dict.get("flat_rate")
                            display["rate_unit"] = rate_dict.get("rate_unit")
                        st.json(display)
                with col2:
                    if st.button("🗑 Remove", key=f"remove_fuel_rate_{rate_name}"):
                        del fuel_rate_library[rate_name]
                        st.session_state["fuel_rate_library"] = fuel_rate_library
                        # Clear any assignments pointing to this rate
                        for sk in list(app_state.fuel_rate_assignments.keys()):
                            if app_state.fuel_rate_assignments[sk].get(ft, {}).get("name") == rate_name:
                                app_state.fuel_rate_assignments[sk][ft] = None
                        st.rerun()

    st.divider()

    # ── Electricity rate assignment matrix ────────────────────────────────────
    st.subheader("Assign Rates to Scenarios")
    st.caption(
        "For each scenario (baseline + selected upgrades), choose which rate "
        "structure to apply. One rate per scenario."
    )

    rate_names = list(rate_library.keys())
    scenarios = app_state.selected_upgrades or [0]

    # Electricity
    st.markdown("**⚡ Electricity**")
    for upgrade_id in scenarios:
        scenario_key = _scenario_key(upgrade_id)
        label = get_upgrade_label(_base_uri, upgrade_id)
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

    # Fuel types — only show assignment if rates exist for that type
    for ft in FUEL_TYPES:
        ft_label = FUEL_TYPE_LABELS[ft]
        ft_rate_names = [n for n, r in fuel_rate_library.items() if r.get("fuel_type") == ft]
        if not ft_rate_names:
            continue

        icon = {"natural_gas": "🔥", "propane": "🛢️", "fuel_oil": "🛢️"}[ft]
        st.markdown(f"**{icon} {ft_label}**")

        for upgrade_id in scenarios:
            scenario_key = _scenario_key(upgrade_id)
            label = get_upgrade_label(_base_uri, upgrade_id)
            scenario_display = (
                f"Upgrade {upgrade_id}: {label}" if upgrade_id > 0 else f"Baseline: {label}"
            )

            # Initialise nested dict if needed
            if scenario_key not in app_state.fuel_rate_assignments:
                app_state.fuel_rate_assignments[scenario_key] = {}

            current_fuel = app_state.fuel_rate_assignments[scenario_key].get(ft)
            current_fuel_name = current_fuel.get("name") if isinstance(current_fuel, dict) else None

            try:
                current_idx = ft_rate_names.index(current_fuel_name) + 1 if current_fuel_name in ft_rate_names else 0
            except ValueError:
                current_idx = 0

            chosen_fuel = st.selectbox(
                scenario_display,
                options=["— Dataset Default —"] + ft_rate_names,
                index=current_idx,
                key=f"fuel_assign_{scenario_key}_{ft}",
            )

            if chosen_fuel == "— Dataset Default —":
                app_state.fuel_rate_assignments[scenario_key][ft] = None
            else:
                app_state.fuel_rate_assignments[scenario_key][ft] = fuel_rate_library[chosen_fuel]

    # ── Assignment summary ────────────────────────────────────────────────────
    assigned_count = len(app_state.rate_assignments)
    total_scenarios = len(scenarios)
    if assigned_count == total_scenarios:
        st.success(f"All {total_scenarios} scenario(s) have an electricity rate assigned. Ready for Phase 5.")
    else:
        st.warning(f"{assigned_count} of {total_scenarios} scenario(s) have electricity rates assigned.")

