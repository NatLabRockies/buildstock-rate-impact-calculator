"""
Page: Building Characteristic Filters (Step 3)

Shows the dataset's configured characteristic filter columns
(DatasetConfig.filter_columns) as individual multi-select dropdowns of the
values present in the geographically-filtered metadata. After each selection
the summary of matching/applicable buildings for the selected upgrades,
geographies, and filters is refreshed.

Metadata is loaded only after geographic selections are made (Geography tab);
an explicit button covers the no-geographic-filter (entire dataset) case.
"""
from __future__ import annotations
import streamlit as st

from buildstock_rate_impact_calculator.ui.state import AppState
from buildstock_rate_impact_calculator.ui.applicability import (
    format_bytes,
    render_applicability_table,
)
from buildstock_rate_impact_calculator.ui.metadata_session import (
    get_session_metadata,
    load_session_metadata,
)


def _filter_label(column: str) -> str:
    """Human-friendly label for a metadata column, e.g. 'in.heating_fuel' →
    'Heating Fuel', 'in.sqft..ft2' → 'Sqft (ft2)'."""
    name = column
    for prefix in ("in.", "out.", "calc."):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    if ".." in name:
        name, unit = name.split("..", 1)
        return f"{name.replace('_', ' ').title()} ({unit})"
    return name.replace("_", " ").title()


def render_building_filter_page(app_state: AppState) -> None:
    """Render the building characteristic filter UI."""
    st.header("Building Filters")

    if not app_state.dataset_type or not app_state.release:
        st.warning("Please select a dataset on the Dataset tab first.")
        return

    if not app_state.selected_upgrades:
        st.warning("Please select one or more upgrade scenarios on the Dataset tab first.")
        return

    # ── Metadata for the active geographic selection ──────────────────────────
    metadata = get_session_metadata(app_state)
    if metadata is None:
        sel = app_state.effective_geo_selection()
        has_geo = any(sel.values())
        if has_geo:
            # Geo selections exist (e.g. project just loaded) — load the
            # appropriate metadata automatically.
            with st.spinner("Loading building metadata for the selected geographies…"):
                try:
                    _, metadata = load_session_metadata(app_state)
                except Exception as exc:
                    st.error(f"Failed to load metadata: {exc}")
                    return
        else:
            st.info(
                "No geographic selections have been made yet. Select geographies "
                "on the Geography tab, or load metadata for the entire dataset below."
            )
            if st.button("Load metadata for the entire dataset (no geographic filter)"):
                with st.spinner("Loading building metadata…"):
                    try:
                        _, metadata = load_session_metadata(app_state)
                    except Exception as exc:
                        st.error(f"Failed to load metadata: {exc}")
                        return
                st.rerun()
            return

    from buildstock_rate_impact_calculator.ui.metadata_session import get_manager
    mgr = get_manager(app_state)

    geo_filtered = app_state.apply_geo_filter(metadata)

    # ── Characteristic filter dropdowns (from DATASET_CONFIGS) ───────────────
    st.subheader("Characteristic Filters")
    st.caption(
        "Select one or more values per characteristic; leave empty to include "
        "all. The applicability summary below updates after each selection."
    )

    configured = list(mgr.config.filter_columns or [])
    filter_cols = [c for c in configured if c in geo_filtered.columns]
    missing_cols = [c for c in configured if c not in geo_filtered.columns]
    if missing_cols:
        st.caption(
            f"Not available in this dataset's metadata: {', '.join(missing_cols)}"
        )

    for col in filter_cols:
        options = mgr.get_column_unique_values(geo_filtered, col)
        prior = [v for v in app_state.active_filters.get(col, []) if v in options]
        selected = st.multiselect(
            _filter_label(col),
            options=options,
            default=prior,
            key=f"bf_{app_state.dataset_type}_{col}",
            help=f"Metadata column: {col}",
        )
        if selected:
            app_state.active_filters[col] = selected
        elif col in app_state.active_filters:
            del app_state.active_filters[col]

    # Drop filters that no longer correspond to a configured column
    for stale_col in list(app_state.active_filters):
        if stale_col not in filter_cols:
            del app_state.active_filters[stale_col]

    # ── Matching-building summary ─────────────────────────────────────────────
    st.divider()

    if app_state.active_filters:
        filtered = mgr.filter_metadata(geo_filtered, app_state.active_filters)
    else:
        filtered = geo_filtered

    bldg_col = mgr.config.building_id_column
    building_ids = filtered[bldg_col].unique().to_list()
    app_state.building_count = len(building_ids)
    app_state.estimated_download_bytes = mgr.estimate_download_size(
        building_ids, upgrades=app_state.selected_upgrades
    )

    sel = app_state.effective_geo_selection()
    geo_filter_count = sum(
        1 for ids in (sel["states"], sel["county_ids"], sel["puma_ids"]) if ids
    )
    total_filter_count = len(app_state.active_filters) + geo_filter_count

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Matching Buildings", f"{app_state.building_count:,}")
    with col2:
        st.metric("Active Filters", total_filter_count)
    with col3:
        st.metric("Est. Download Size", format_bytes(app_state.estimated_download_bytes))

    if app_state.building_count == 0:
        st.warning("No buildings match the current filters. Relax one or more filters.")
        return

    # ── Applicability summary (updates with every filter change) ─────────────
    render_applicability_table(
        app_state,
        manager=mgr,
        metadata=geo_filtered,
        title="Upgrade Applicability Summary",
    )

    st.info(
        f"Click **Run** (step 5) to download data for "
        f"{app_state.building_count:,} buildings across "
        f"{len(app_state.selected_upgrades)} scenario(s)."
    )
