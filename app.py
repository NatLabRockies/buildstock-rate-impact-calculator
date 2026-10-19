"""
BuildStock Rate Impact Calculator — Streamlit App Entry Point
"""

import os
import streamlit as st
from pathlib import Path

from buildstock_rate_impact_calculator.ui.state import init_session_state
from buildstock_rate_impact_calculator.ui import steps
from buildstock_rate_impact_calculator.core.logger_config import setup_logging
from buildstock_rate_impact_calculator.ui.sidebar_helpers import (
    list_projects,
    default_project_name,
    browse_folder,
)
from buildstock_rate_impact_calculator.ui.project_io import (
    load_project as _load_project,
)
from buildstock_rate_impact_calculator.ui import asyncio_windows_fix

# Configure the single package-level logger for the entire app session
setup_logging()

# Keep Ctrl+C from hanging at "Stopping..." on Windows after a browser closes
# its connection abruptly (see asyncio_windows_fix).
asyncio_windows_fix.apply()


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
            st.warning(
                f"Loaded project — {len(missing)} rate(s) could not be restored: {', '.join(missing)}"
            )
        else:
            st.success(f"Project loaded: {project_dir.name}")
        # Drop the user at the first unfinished step so work resumes where it left off.
        for i, spec in enumerate(steps.STEPS):
            if not spec.is_complete(state):
                st.session_state[steps.WIZARD_STEP_KEY] = i
                break
        else:
            st.session_state[steps.WIZARD_STEP_KEY] = steps.STEP_RESULTS
        st.session_state[steps.WIZARD_SYNC_FLAG] = True
        st.rerun()
    except FileNotFoundError:
        st.error(f"No config.json found in: {project_dir}")
    except Exception as exc:
        st.error(f"Failed to load project: {exc}")


# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚡ BuildStock Rate Impact Calculator")
    st.divider()

    # ── Workflow status panel (jump-to-step navigation) ───────────────────────
    steps.render_status_panel(app_state)
    st.divider()

    # ── Setup (data directory + API key) ──────────────────────────────────────
    _key_on_disk = _load_api_key_from_disk(app_state.output_dir)
    _key_stored = bool(_key_on_disk)
    if not _key_stored:
        st.error("OpenEI API key not set — required for URDB rate search.")

    with st.expander("⚙ Setup", expanded=not _key_stored):
        # ── Data Directory ────────────────────────────────────────────────────
        st.caption(
            "Location where all BuildStock data and project information will be stored."
        )
        col_dir, col_browse = st.columns([4, 1])
        with col_dir:
            output_dir_str = st.text_input(
                "Data Directory",
                value=str(app_state.output_dir),
                help="Local directory where downloaded data and results will be saved.",
            )
        with col_browse:
            st.write("")
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

        # ── OpenEI API Key ────────────────────────────────────────────────────
        if _key_stored:
            st.success("API key stored in Data Directory")

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

    # ── Project ───────────────────────────────────────────────────────────────
    st.subheader("Project")
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

    with st.expander("Load existing project", expanded=False):
        selected_project = st.selectbox(
            "Select project",
            options=[""] + list_projects(app_state.output_dir),
        )
        if st.button("Load", key="load_from_dropdown"):
            if selected_project:
                _do_load_project(
                    app_state.output_dir / "projects" / selected_project, app_state
                )

        st.caption("— or load from path —")
        load_path_str = st.text_input(
            "Project path",
            value="",
            help="Full path to a project directory to load.",
        )
        if st.button("Load", key="load_from_path"):
            if load_path_str:
                _do_load_project(Path(load_path_str), app_state)

    # ── Debug (gated behind BRIC_DEBUG env var) ──────────────────────────────
    if os.getenv("BRIC_DEBUG"):
        st.divider()
        with st.expander("🛠️ Debug Variables"):
            st.write(st.session_state)


# ── Main content ──────────────────────────────────────────────────────────────
def _render_step(index: int, state) -> None:
    """Render the active step's page. Pages are imported lazily so their
    heavier dependencies (polars, folium, PySAM) load only when reached."""
    if index == steps.STEP_DATASET:
        from buildstock_rate_impact_calculator.ui.pages.page_01_dataset import (
            render_dataset_page,
        )

        render_dataset_page(state)
    elif index == steps.STEP_GEOGRAPHY:
        from buildstock_rate_impact_calculator.ui.pages.page_02_geography import (
            render_geography_page,
        )

        render_geography_page(state)
    elif index == steps.STEP_FILTERS:
        from buildstock_rate_impact_calculator.ui.pages.page_03_building_filter import (
            render_building_filter_page,
        )

        render_building_filter_page(state)
    elif index == steps.STEP_RATES:
        from buildstock_rate_impact_calculator.ui.pages.page_04_rates import (
            render_rates_page,
        )

        render_rates_page(state)
    elif index == steps.STEP_RUN:
        from buildstock_rate_impact_calculator.ui.pages.page_05_run import (
            render_run_page,
        )

        render_run_page(state)
    else:
        from buildstock_rate_impact_calculator.ui.pages.page_06_results import (
            render_results_page,
        )

        render_results_page(state)


st.title("BuildStock Rate Impact Calculator")
st.markdown(
    "Select a dataset, filter buildings, configure utility rates, and calculate "
    "rate impacts for ResStock or ComStock buildings."
)

# ── Stepper header ────────────────────────────────────────────────────────────
current = steps.current_step()

# Seed / resync the header control to the canonical step index. Option values
# are stable integers (0..5); the ✓/number label is rendered by format_func, so
# the widget's stored value never desyncs when a step's completion mark flips.
if (
    steps.WIZARD_CONTROL_KEY not in st.session_state
    or st.session_state.pop(steps.WIZARD_SYNC_FLAG, False)
):
    st.session_state[steps.WIZARD_CONTROL_KEY] = current


def _step_label(i: int) -> str:
    spec = steps.STEPS[i]
    mark = "✓" if spec.is_complete(app_state) else str(i + 1)
    return f"{mark} · {spec.title}"


_choice = st.segmented_control(
    "Workflow step",
    options=list(range(len(steps.STEPS))),
    format_func=_step_label,
    key=steps.WIZARD_CONTROL_KEY,
    label_visibility="collapsed",
)
if _choice is None:
    # Deselection isn't meaningful for a stepper — snap back to the current step.
    st.session_state[steps.WIZARD_SYNC_FLAG] = True
    st.rerun()
if _choice != current:
    current = _choice
    st.session_state[steps.WIZARD_STEP_KEY] = current

st.divider()

# ── Active page ───────────────────────────────────────────────────────────────
_render_step(current, app_state)

# ── Footer navigation ─────────────────────────────────────────────────────────
st.divider()
_back_col, _mid_col, _next_col = st.columns([1, 4, 1])
with _back_col:
    if current > 0 and st.button(
        "← Back", key="wizard_back", use_container_width=True
    ):
        steps.goto_step(current - 1)
with _next_col:
    if current < len(steps.STEPS) - 1:
        _block_reason = steps.STEPS[current].blocks_next(app_state)
        _next_label = f"Next: {steps.STEPS[current + 1].title} →"
        if st.button(
            _next_label,
            key="wizard_next",
            type="primary",
            use_container_width=True,
            disabled=_block_reason is not None,
            help=_block_reason,
        ):
            # Geography commits its pending map selection (and downloads
            # metadata) at the moment the user advances.
            if current == steps.STEP_GEOGRAPHY:
                from buildstock_rate_impact_calculator.ui.pages.page_02_geography import (
                    commit_pending_geography,
                )

                with st.spinner("Applying geographic selection…"):
                    _ok = commit_pending_geography(app_state)
                if not _ok:
                    st.stop()  # errors already rendered by the hook; stay put
            steps.goto_step(current + 1)

# ── Scroll to top on step change ──────────────────────────────────────────────
# Streamlit swaps the page content in place, so the main pane keeps its scroll
# position (and browser scroll anchoring pins it to the footer buttons). Reset
# it once on the run where the active step changes. The counter makes each
# script element unique so React mounts it (and runs it) every time.
if st.session_state.get("_last_rendered_step") != current:
    st.session_state["_last_rendered_step"] = current
    _n = st.session_state["_scroll_reset_count"] = (
        st.session_state.get("_scroll_reset_count", 0) + 1
    )
    st.html(
        f"<script>/* {_n} */"
        "document.querySelector('[data-testid=\"stMain\"]')?.scrollTo(0, 0);"
        "</script>",
        unsafe_allow_javascript=True,
    )
