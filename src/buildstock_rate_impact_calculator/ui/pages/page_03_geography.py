"""
Page: Geographic Selection (Step 2)

Three input modes:
  1. Interactive Map — state choropleth with drill-down to county/PUMA
  2. CSV Upload     — paste/upload a file of geographic IDs
  3. By Utility     — state → utility dropdown (URDB-backed)
"""
from __future__ import annotations
import streamlit as st
import polars as pl

from buildstock_rate_impact_calculator.ui.state import AppState
from buildstock_rate_impact_calculator.ui.project_io import save_project as _save_project
from buildstock_rate_impact_calculator.data.geo_utils import (
    parse_csv_geo_ids,
    validate_geo_ids,
    get_utility_list,
)
from buildstock_rate_impact_calculator.data.buildstock_manager import BuildStockDataManager
from buildstock_rate_impact_calculator.data.upgrade_labels import get_upgrade_label
from buildstock_rate_impact_calculator.ui.components.map_selector import render_map_selector, render_utility_map_selector
from buildstock_rate_impact_calculator.ui.applicability import (
    compute_summary_cache_key,
    load_applicability_summary,
)


def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024
    return f"{n:.1f} PB"


def _apply_geographic_selection(app_state: AppState) -> None:
    """Filter cached metadata to current geo selections and refresh the
    applicability summary in session state.

    Called after the user clicks any Save / Apply button on this page.
    Preserves a raw, unfiltered copy of the metadata so subsequent selection
    changes can re-filter from the full dataset.
    """
    if not app_state.dataset_type or not app_state.release:
        st.warning("Select a dataset on the Dataset tab before saving geographic selections.")
        return
    if not app_state.selected_upgrades:
        st.warning("Select upgrade scenarios on the Dataset tab before saving geographic selections.")
        return

    manager_key = f"mgr_{app_state.dataset_type}_{app_state.release}"
    metadata_key = f"metadata_{app_state.dataset_type}_{app_state.release}"
    metadata_raw_key = f"metadata_raw_{app_state.dataset_type}_{app_state.release}"

    if manager_key not in st.session_state:
        st.session_state[manager_key] = BuildStockDataManager(
            dataset_type=app_state.dataset_type,
            release=app_state.release,
            cache_enabled=True,
            cache_dir=app_state.output_dir,
        )
    mgr: BuildStockDataManager = st.session_state[manager_key]

    # Ensure we have a raw copy to filter from.
    if metadata_raw_key not in st.session_state:
        if metadata_key in st.session_state:
            st.session_state[metadata_raw_key] = st.session_state[metadata_key]
        else:
            with st.spinner("Loading building metadata…"):
                try:
                    st.session_state[metadata_raw_key] = mgr.load_metadata()
                except Exception as exc:
                    st.error(f"Failed to load metadata: {exc}")
                    return

    raw_metadata = st.session_state[metadata_raw_key]
    filtered_metadata = app_state.apply_geo_filter(raw_metadata)
    st.session_state[metadata_key] = filtered_metadata
    app_state.metadata_loaded = True
    if app_state.project_name:
        _save_project(app_state)

    sum_key = compute_summary_cache_key(app_state)
    with st.spinner("Updating upgrade applicability for selected geography…"):
        st.session_state[sum_key] = load_applicability_summary(
            app_state, metadata=filtered_metadata, manager=mgr
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

    sum_key = compute_summary_cache_key(app_state)
    summary = st.session_state.get(sum_key)

    # Auto-compute the summary on first render when a project config has
    # populated geo selections but no summary has been cached yet.
    if summary is None and has_geo_selections:
        _apply_geographic_selection(app_state)
        summary = st.session_state.get(sum_key)

    if not summary:
        return

    st.divider()
    st.markdown("**Applicability after geographic filtering**")

    if "error" in summary:
        st.warning(f"Could not load applicability data: {summary['error']}")
        return

    cfg_key = f"{app_state.dataset_type}_{app_state.release}"
    base_uri = BuildStockDataManager.DATASET_CONFIGS[cfg_key].base_uri

    tbl_rows = []
    for row in summary.get("rows", []):
        uid = row["upgrade_id"]
        try:
            label = get_upgrade_label(base_uri, uid)
        except Exception:
            label = f"Upgrade {uid}"
        scenario_label = label if uid == 0 else f"Upgrade {uid}: {label}"
        pct = row["n_applicable"] / row["n_total"] * 100 if row["n_total"] > 0 else 0.0
        tbl_rows.append({
            "Scenario": scenario_label,
            "Applicable": f"{row['n_applicable']:,} / {row['n_total']:,} ({pct:.0f}%)",
            "Est. Download": _format_bytes(row["est_bytes"]),
        })

    if tbl_rows:
        st.dataframe(
            pl.DataFrame(tbl_rows).to_pandas(),
            hide_index=True,
            width="stretch",
        )
        total_est = sum(r["est_bytes"] for r in summary.get("rows", []))
        st.caption(f"Total estimated download: {_format_bytes(total_est)}")


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
