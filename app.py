"""
BuildStock Rate Impact Calculator — Streamlit App Entry Point
"""
import streamlit as st
from pathlib import Path

from buildstock_rate_impact_calculator.ui.state import init_session_state
from buildstock_rate_impact_calculator.core.logger_config import setup_logging
from buildstock_rate_impact_calculator.ui.sidebar_helpers import (
    list_projects,
    default_project_name,
    browse_folder,
)
from buildstock_rate_impact_calculator.ui.project_io import load_project as _load_project

# Configure the single package-level logger for the entire app session
setup_logging()


_API_KEY_FILENAME = ".openei_api_key"


def _api_key_path(data_dir: Path) -> Path:
    return data_dir / _API_KEY_FILENAME


def _load_api_key_from_disk(data_dir: Path) -> str | None:
    """Read the stored API key file. Returns None if absent or unreadable."""
    try:
        p = _api_key_path(data_dir)
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return None


def _save_api_key_to_disk(data_dir: Path, api_key: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    _api_key_path(data_dir).write_text(api_key.strip(), encoding="utf-8")


st.set_page_config(
    page_title="BuildStock Rate Impact Calculator",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Initialize session state ─────────────────────────────────────────────────
app_state = init_session_state()

# Auto-load API key from disk on first run (before sidebar renders)
if not app_state.urdb_api_key:
    stored = _load_api_key_from_disk(app_state.output_dir)
    if stored:
        app_state.urdb_api_key = stored


def _do_load_project(project_dir: Path, state) -> None:
    """Load project from directory and rerun."""
    try:
        missing = _load_project(project_dir, state, api_key=state.urdb_api_key)
        if missing:
            st.warning(f"Loaded project — {len(missing)} rate(s) could not be restored: {', '.join(missing)}")
        else:
            st.success(f"Project loaded: {project_dir.name}")
        st.rerun()
    except FileNotFoundError:
        st.error(f"No config.json found in: {project_dir}")
    except Exception as exc:
        st.error(f"Failed to load project: {exc}")


# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚡ BuildStock Rate Impact Calculator")
    st.divider()

    # ── Data Directory ────────────────────────────────────────────────────────
    st.subheader("Data Directory")
    st.caption("Location where all BuildStock data and project information will be stored.")
    col_dir, col_browse = st.columns([4, 1])
    with col_dir:
        output_dir_str = st.text_input(
            "Data Directory",
            value=str(app_state.output_dir),
            label_visibility="collapsed",
            help="Local directory where downloaded data and results will be saved.",
        )
    with col_browse:
        if st.button("📂", help="Browse for folder"):
            picked = browse_folder()
            if picked:
                output_dir_str = picked
                app_state.output_dir = Path(picked)

    if output_dir_str != str(app_state.output_dir):
        try:
            new_dir = Path(output_dir_str)
            app_state.output_dir = new_dir
            # Try to load API key from the new directory
            stored = _load_api_key_from_disk(new_dir)
            if stored:
                app_state.urdb_api_key = stored
        except Exception:
            st.warning("Invalid directory path.")

    # ── OpenEI API Key ────────────────────────────────────────────────────────
    _key_on_disk = _load_api_key_from_disk(app_state.output_dir)
    _key_stored = bool(_key_on_disk)

    if _key_stored:
        st.success("API key stored in Data Directory")
    else:
        st.error("OpenEI API key not set — required for URDB rate search.")

    api_key = st.text_input(
        "OpenEI API Key",
        value=app_state.urdb_api_key,
        type="password",
        help="Required for URDB rate search. Get a free key at openei.org",
    )
    if api_key != app_state.urdb_api_key:
        app_state.urdb_api_key = api_key
        if api_key:
            _save_api_key_to_disk(app_state.output_dir, api_key)
            st.rerun()

    st.divider()

    # ── Project Name ──────────────────────────────────────────────────────────
    st.subheader("New Project")
    project_name_default = (
        app_state.project_name
        if app_state.project_name
        else default_project_name(app_state.output_dir)
    )
    project_name_input = st.text_input(
        "Project Name",
        value=project_name_default,
        help="Name for the current project.",
    )
    if project_name_input != app_state.project_name:
        app_state.project_name = project_name_input

    # ── Load Project ──────────────────────────────────────────────────────────
    st.subheader("Load Project")
    selected_project = st.selectbox(
        "Select project",
        options=[""] + list_projects(app_state.output_dir),
    )
    if st.button("Load", key="load_from_dropdown"):
        if selected_project:
            _do_load_project(app_state.output_dir / "projects" / selected_project, app_state)

    st.caption("— or load from path —")
    load_path_str = st.text_input(
        "Project path",
        value="",
        help="Full path to a project directory to load.",
    )
    if st.button("Load", key="load_from_path"):
        if load_path_str:
            _do_load_project(Path(load_path_str), app_state)

    st.divider()

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
