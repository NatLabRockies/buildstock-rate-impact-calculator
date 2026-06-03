"""
BuildStock Rate Impact Calculator — Streamlit App Entry Point
"""
import json
import streamlit as st
from pathlib import Path

from buildstock_rate_impact_calculator.ui.state import AppState, init_session_state
from buildstock_rate_impact_calculator.core.logger_config import setup_logging

# Configure the single package-level logger for the entire app session
setup_logging()


def _sync_rate_libraries(app_state: AppState) -> None:
    """Rebuild session-state rate libraries from loaded app_state assignments.

    ``rate_library`` and ``fuel_rate_library`` are stored in ``st.session_state``
    so the rate editor UI can reference them by name.  When a project config is
    loaded the assignment dicts already contain the full rate structures, so we
    reconstruct the libraries from those dicts here.
    """
    rate_library: dict[str, dict] = {}
    for rate_dict in app_state.rate_assignments.values():
        if isinstance(rate_dict, dict) and "name" in rate_dict:
            rate_library[rate_dict["name"]] = rate_dict
    st.session_state["rate_library"] = rate_library

    fuel_rate_library: dict[str, dict] = {}
    for fuel_scenario in app_state.fuel_rate_assignments.values():
        if isinstance(fuel_scenario, dict):
            for fuel_rate in fuel_scenario.values():
                if isinstance(fuel_rate, dict) and "name" in fuel_rate:
                    fuel_rate_library[fuel_rate["name"]] = fuel_rate
    st.session_state["fuel_rate_library"] = fuel_rate_library

st.set_page_config(
    page_title="BuildStock Rate Impact Calculator",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Initialize session state ─────────────────────────────────────────────────
app_state = init_session_state()

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚡ BuildStock Rate Impact Calculator")
    st.divider()

    st.subheader("Settings")
    api_key = st.text_input(
        "OpenEI API Key",
        value=app_state.urdb_api_key,
        type="password",
        help="Required for URDB rate search. Get a free key at openei.org",
    )
    if api_key != app_state.urdb_api_key:
        app_state.urdb_api_key = api_key

    output_dir_str = st.text_input(
        "Output Directory",
        value=str(app_state.output_dir),
        help="Local directory where downloaded data and results will be saved.",
    )
    try:
        app_state.output_dir = Path(output_dir_str)
    except Exception:
        st.warning("Invalid directory path.")

    st.divider()
    st.caption("Steps")
    steps = [
        "1. Dataset & Upgrades",
        "2. Geography",
        "3. Building Filters",
        "4. Utility Rates",
        "5. Run",
        "6. Results",
    ]
    for step in steps:
        st.markdown(f"- {step}")

    # ── Project config save / load ────────────────────────────────────────────
    st.divider()
    st.subheader("Project Config")

    config_path_str = st.text_input(
        "Config file path",
        value="project_config.json",
        help="Path to save or load the project input configuration (JSON).",
    )

    col_save, col_load = st.columns(2)

    with col_save:
        if st.button("Save", width='stretch', help="Save current settings to the config file"):
            try:
                app_state.save_config(config_path_str)
                st.success(f"Saved to `{config_path_str}`")
            except Exception as exc:
                st.error(f"Save failed: {exc}")

    with col_load:
        if st.button("Load", width='stretch', help="Load settings from the config file"):
            try:
                loaded = app_state.load_config(config_path_str)
                app_state.apply_config_dict(loaded.to_config_dict())
                _sync_rate_libraries(app_state)
                st.success(f"Loaded `{config_path_str}`")
                st.rerun()
            except FileNotFoundError:
                st.error(f"File not found: `{config_path_str}`")
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                st.error(f"Load failed: {exc}")

    uploaded = st.file_uploader(
        "…or upload a config file",
        type="json",
        help="Upload a previously saved project_config.json",
    )
    if uploaded is not None:
        try:
            data = json.load(uploaded)
            app_state.apply_config_dict(data)
            _sync_rate_libraries(app_state)
            st.success("Config applied from upload.")
            st.rerun()
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            st.error(f"Upload failed: {exc}")

    with st.sidebar.expander("🛠️ Debug Variables"):
        st.write(st.session_state)
        
# ── Main content ──────────────────────────────────────────────────────────────
st.title("BuildStock Rate Impact Calculator")
st.markdown(
    "Select a dataset, filter buildings, configure utility rates, and calculate "
    "rate impacts for ResStock or ComStock buildings."
)

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "1 · Dataset", "2 · Geography", "3 · Building Filters",
    "4 · Energy Rates", "5 · Run", "6 · Results"
])

with tab1:
    from buildstock_rate_impact_calculator.ui.pages.page_01_dataset import render_dataset_page
    render_dataset_page(app_state)

with tab2:
    from buildstock_rate_impact_calculator.ui.pages.page_03_geography import render_geography_page
    render_geography_page(app_state)

with tab3:
    from buildstock_rate_impact_calculator.ui.pages.page_02_building_filter import render_building_filter_page
    render_building_filter_page(app_state)

with tab4:
    from buildstock_rate_impact_calculator.ui.pages.page_04_rates import render_rates_page
    render_rates_page(app_state)

with tab5:
    from buildstock_rate_impact_calculator.ui.pages.page_05_run import render_run_page
    render_run_page(app_state)

with tab6:
    from buildstock_rate_impact_calculator.ui.pages.page_06_results import render_results_page
    render_results_page(app_state)
