"""
Page: Geographic Selection (Step 2)

Three input modes:
  1. Interactive Map — state choropleth with drill-down to county/PUMA
  2. CSV Upload     — paste/upload a file of geographic IDs
  3. By Utility     — state → utility dropdown (URDB-backed)
"""
from __future__ import annotations
import streamlit as st

from buildstock_rate_impact_calculator.ui.state import AppState
from buildstock_rate_impact_calculator.ui.project_io import save_project as _save_project
from buildstock_rate_impact_calculator.data.geo_utils import (
    parse_csv_geo_ids,
    validate_geo_ids,
    get_utility_list,
)
from buildstock_rate_impact_calculator.ui.components.map_selector import render_map_selector, render_utility_map_selector
from buildstock_rate_impact_calculator.ui.applicability import (
    compute_summary_cache_key,
    render_applicability_table,
)
from buildstock_rate_impact_calculator.ui.metadata_session import (
    get_session_metadata,
    load_session_metadata,
)


def _apply_geographic_selection(app_state: AppState) -> None:
    """Load metadata for the current geographic selection and refresh the
    applicability summary.

    Called after the user clicks any Save / Apply button on this page. This is
    the point where metadata is first downloaded — at the appropriate
    geographic resolution (ComStock county/PUMA selections load the
    by_state_and_county / by_state_and_puma files) and restricted to the
    possibly-necessary columns.
    """
    if not app_state.dataset_type or not app_state.release:
        st.warning("Select a dataset on the Dataset tab before saving geographic selections.")
        return
    if not app_state.selected_upgrades:
        st.warning("Select upgrade scenarios on the Dataset tab before saving geographic selections.")
        return

    with st.spinner("Loading building metadata for the selected geographies…"):
        try:
            mgr, metadata = load_session_metadata(app_state)
        except Exception as exc:
            st.error(f"Failed to load metadata: {exc}")
            return

    geo_filtered = app_state.apply_geo_filter(metadata)
    if app_state.project_name:
        _save_project(app_state)

    sum_key = compute_summary_cache_key(app_state)
    with st.spinner("Updating upgrade applicability for selected geography…"):
        from buildstock_rate_impact_calculator.ui.applicability import (
            load_applicability_summary,
        )
        st.session_state[sum_key] = load_applicability_summary(
            app_state,
            metadata=geo_filtered,
            manager=mgr,
            applicable_sets_cache=st.session_state,
        )


def _render_applicability_table(app_state: AppState) -> None:
    """Render the per-upgrade applicability table for the current geo selection."""
    if not (
        app_state.dataset_type
        and app_state.release
        and app_state.selected_upgrades
    ):
        return

    has_geo_selections = bool(
        app_state.selected_states
        or app_state.selected_county_ids
        or app_state.selected_puma_ids
        or app_state.selected_geo_ids
    )
    summary_exists = st.session_state.get(compute_summary_cache_key(app_state)) is not None
    metadata_loaded = get_session_metadata(app_state) is not None

    if not (has_geo_selections or summary_exists or metadata_loaded):
        return

    # Auto-compute on first render when a loaded project has geo selections but
    # no metadata/summary has been loaded yet in this session.
    if has_geo_selections and not summary_exists and not metadata_loaded:
        _apply_geographic_selection(app_state)

    st.divider()
    render_applicability_table(
        app_state, title="Applicability after geographic filtering"
    )


def render_geography_page(app_state: AppState) -> None:
    """Render the geographic selection UI."""
    st.header("Geographic Selection")

    map_tab, csv_tab, utility_tab = st.tabs(["🗺 Map", "📄 CSV Upload", "🏢 By Utility"])

    # ── Tab 1: Interactive Map ────────────────────────────────────────────────
    with map_tab:
        st.markdown(
            "Select one or more states using the multiselect, then click "
            "**Select Counties** or **Select PUMAs** to drill into sub-state "
            "boundaries.  County and PUMA selections are tracked independently."
        )

        map_result = render_map_selector(
            dataset_type=app_state.dataset_type,
            initial_states=app_state.selected_states,
            initial_county_ids=app_state.selected_county_ids,
            initial_puma_ids=app_state.selected_puma_ids,
        )

        # ── Save Selections button ───────────────────────────────────────────
        st.divider()
        if st.button("💾 Save Selections", key="save_geo_selections", width='stretch'):
            app_state.selected_states = map_result["selected_states"]
            app_state.selected_county_ids = map_result["selected_county_ids"]
            app_state.selected_puma_ids = map_result["selected_puma_ids"]
            _apply_geographic_selection(app_state)
            st.success("Geographic selections saved.")

    # ── Tab 2: CSV Upload ─────────────────────────────────────────────────────
    with csv_tab:
        st.markdown(
            "Upload a single-column CSV file containing geographic IDs "
            "(state abbreviations, county FIPS codes, PUMA codes, or census tract codes)."
        )

        # Geo level selector for CSV
        csv_level_options = ["state", "county", "puma"]
        if app_state.dataset_type == "comstock":
            csv_level_options.append("census_tract")

        csv_geo_level = st.radio(
            "Geographic level in CSV",
            options=csv_level_options,
            index=csv_level_options.index(app_state.geo_level)
            if app_state.geo_level in csv_level_options
            else 0,
            horizontal=True,
            key="csv_geo_level",
        )

        uploaded_file = st.file_uploader(
            "Upload CSV",
            type=["csv", "txt"],
            key="geo_csv_uploader",
            help="Single-column CSV. Header row is auto-detected and skipped.",
        )

        if uploaded_file is not None:
            try:
                raw_bytes = uploaded_file.read()
                parsed_ids = parse_csv_geo_ids(raw_bytes, geo_level=csv_geo_level)
                valid_ids, invalid_ids = validate_geo_ids(
                    parsed_ids, geo_level=csv_geo_level, dataset_type=app_state.dataset_type
                )

                col1, col2 = st.columns(2)
                with col1:
                    st.metric("Valid IDs", len(valid_ids))
                with col2:
                    st.metric("Invalid / skipped", len(invalid_ids))

                if invalid_ids:
                    with st.expander("Invalid IDs"):
                        st.write(invalid_ids)

                if valid_ids and st.button("Apply these IDs", key="csv_apply_btn"):
                    app_state.selected_geo_ids = valid_ids
                    app_state.geo_level = csv_geo_level
                    _apply_geographic_selection(app_state)
                    st.success(f"Applied {len(valid_ids)} {csv_geo_level} IDs.")

            except ValueError as exc:
                st.error(str(exc))

    # ── Tab 3: By Utility ─────────────────────────────────────────────────────
    with utility_tab:
        st.markdown(
            "Search for a utility company by name to load its service territory "
            "counties on the map. Click counties to add or remove them from the "
            "selection, then click **Save Selected** to apply."
        )

        utility_map_result = render_utility_map_selector()

        st.divider()
        if st.button("💾 Save Selected", key="utility_save_counties_btn", width='stretch'):
            new_counties = utility_map_result["selected_county_ids"]
            if new_counties:
                existing = set(app_state.selected_county_ids)
                existing.update(new_counties)
                app_state.selected_county_ids = list(existing)
                _apply_geographic_selection(app_state)
                st.success(f"Saved {len(new_counties)} county(ies) to geographic selection.")
            else:
                st.info("No counties selected.")
    # ── Applicability table (above Selected IDs) ─────────────────────────
    _render_applicability_table(app_state)
    # ── Selected IDs pane (collapsible expander) ────────────────────────────────
    has_map_selections = (
        app_state.selected_states
        or app_state.selected_county_ids
        or app_state.selected_puma_ids
    )
    has_legacy_selections = bool(app_state.selected_geo_ids)

    if has_map_selections or has_legacy_selections:
        st.divider()
        with st.expander("Selected IDs", expanded=False):
            if app_state.selected_states:
                st.markdown(
                    f"**States ({len(app_state.selected_states)}):** "
                    f"{', '.join(sorted(app_state.selected_states))}"
                )
            if app_state.selected_county_ids:
                st.markdown(
                    f"**Counties ({len(app_state.selected_county_ids)}):** "
                    f"{', '.join(sorted(app_state.selected_county_ids))}"
                )
            if app_state.selected_puma_ids:
                st.markdown(
                    f"**PUMAs ({len(app_state.selected_puma_ids)}):** "
                    f"{', '.join(sorted(app_state.selected_puma_ids))}"
                )
            if has_legacy_selections and not has_map_selections:
                st.markdown(
                    f"**{app_state.geo_level.title()} IDs ({len(app_state.selected_geo_ids)}):** "
                    f"{', '.join(sorted(app_state.selected_geo_ids))}"
                )
