"""
Page: Building Characteristic Filters (Step 3)

Lets the user:
- Filter by building type
- Add additional 'in.*' characteristic filters dynamically
- See a live count of matching buildings and estimated download size
  (reflecting both geographic selections from Step 2 and building filters)
"""
from __future__ import annotations
import streamlit as st
from buildstock_rate_impact_calculator.data.buildstock_manager import BuildStockDataManager
from buildstock_rate_impact_calculator.ui.state import AppState


def _format_bytes(n: int) -> str:
    """Format a byte count into a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024
    return f"{n:.1f} PB"


def render_building_filter_page(app_state: AppState) -> None:
    """Render the building characteristic filter UI."""
    st.header("Building Filters")

    if not app_state.dataset_type or not app_state.release:
        st.warning("Please select a dataset on the Dataset tab first.")
        return

    if not app_state.selected_upgrades:
        st.warning("Please select one or more upgrade scenarios on the Dataset tab first.")
        return

    # ── Load or reuse cached metadata ────────────────────────────────────────
    manager_key = f"mgr_{app_state.dataset_type}_{app_state.release}"
    metadata_key = f"metadata_{app_state.dataset_type}_{app_state.release}"

    if manager_key not in st.session_state:
        st.session_state[manager_key] = BuildStockDataManager(
            dataset_type=app_state.dataset_type,
            release=app_state.release,
            cache_enabled=True,
            cache_dir=app_state.output_dir,
        )
    mgr: BuildStockDataManager = st.session_state[manager_key]

    # Metadata loading section
    if metadata_key not in st.session_state:
        with st.spinner("Loading building metadata…"):
            try:
                metadata = mgr.load_metadata()
                st.session_state[metadata_key] = metadata
                app_state.metadata_loaded = True
            except Exception as exc:
                st.error(f"Failed to load metadata: {exc}")
                return

    metadata = st.session_state[metadata_key]
    state_col = mgr.config.state_column
    filter_columns = mgr.get_available_filter_columns(metadata)

    # ── Building type filter ──────────────────────────────────────────────────
    st.subheader("Building Type")
    btype_col = "in.geometry_building_type_recs" if app_state.dataset_type == "resstock" \
        else "in.building_type"

    if btype_col in metadata.columns:
        all_types = mgr.get_column_unique_values(metadata, btype_col)
        selected_types = st.multiselect(
            "Building Types",
            options=all_types,
            default=app_state.active_filters.get(btype_col, []),
            help="Leave empty to include all building types.",
        )
        if selected_types:
            app_state.active_filters[btype_col] = selected_types
        elif btype_col in app_state.active_filters:
            del app_state.active_filters[btype_col]

    # ── Additional filters ────────────────────────────────────────────────────
    st.subheader("Additional Filters")
    # Exclude state column (handled on Geography page) and building type (above)
    additional_cols = [c for c in filter_columns if c not in (state_col, btype_col)]

    if additional_cols:
        chosen_cols = st.multiselect(
            "Add filter columns",
            options=additional_cols,
            default=[c for c in app_state.active_filters if c in additional_cols],
        )

        for col in chosen_cols:
            unique_vals = mgr.get_column_unique_values(metadata, col)
            selected_vals = st.multiselect(
                f"{col}",
                options=unique_vals,
                default=app_state.active_filters.get(col, []),
                key=f"filter_{col}",
            )
            if selected_vals:
                app_state.active_filters[col] = selected_vals
            elif col in app_state.active_filters:
                del app_state.active_filters[col]

        # Remove filters for columns no longer chosen
        for stale_col in list(app_state.active_filters):
            if stale_col in additional_cols and stale_col not in chosen_cols:
                del app_state.active_filters[stale_col]

    # ── Remove any legacy state filter from active_filters (now on Geo page)
    if state_col in app_state.active_filters:
        del app_state.active_filters[state_col]

    # ── Apply geographic filters (Step 2) then building filters ──────────────
    st.divider()

    # Apply geographic filter first (supports OR logic for mixed county+PUMA)
    geo_filtered = app_state.apply_geo_filter(metadata)

    # Then apply building characteristic filters
    if app_state.active_filters:
        filtered = mgr.filter_metadata(geo_filtered, app_state.active_filters)
    else:
        filtered = geo_filtered

    building_ids = filtered[mgr.config.building_id_column].to_list()
    app_state.building_count = len(building_ids)
    app_state.estimated_download_bytes = mgr.estimate_download_size(
        building_ids, upgrades=app_state.selected_upgrades
    )

    # Count active geo filters for display
    geo_filter_count = sum([
        bool(app_state.selected_states or app_state.selected_geo_ids),
        bool(app_state.selected_county_ids),
        bool(app_state.selected_puma_ids),
    ])
    total_filter_count = len(app_state.active_filters) + geo_filter_count

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Matching Buildings", f"{app_state.building_count:,}")
    with col2:
        st.metric("Active Filters", total_filter_count)
    with col3:
        st.metric("Est. Download Size", _format_bytes(app_state.estimated_download_bytes))

    if app_state.building_count == 0:
        st.warning("No buildings match the current filters. Relax one or more filters.")
    else:
        st.info(
            f"Click **Run** (step 5) to download data for "
            f"{app_state.building_count:,} buildings across "
            f"{len(app_state.selected_upgrades)} scenario(s)."
        )
