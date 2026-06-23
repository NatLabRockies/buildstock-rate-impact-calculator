"""
Page: Building Characteristic Filters (Step 3)

Shows the dataset's configured characteristic filter columns
(DatasetConfig.filter_columns) as individual range-sliders (for numeric
columns) or multi-select dropdowns (for categorical columns). After each
selection the summary of matching/applicable buildings for the selected
upgrades, geographies, and filters is refreshed.

Metadata is loaded only after geographic selections are made (Geography tab);
an explicit button covers the no-geographic-filter (entire dataset) case.

Tooltips for each filter are pulled from the dataset's ``data_dictionary.tsv``
(fetched lazily on first render and cached on disk and in
``st.session_state``).
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
            name = name[len(prefix) :]
            break
    if ".." in name:
        name, unit = name.split("..", 1)
        return f"{name.replace('_', ' ').title()} ({unit})"
    return name.replace("_", " ").title()


def _get_data_dictionary(app_state: AppState, base_uri: str) -> dict:
    """Lazy-load and cache the data dictionary for the current dataset/release.

    Cached in ``st.session_state['data_dictionary']`` keyed by
    ``(dataset_type, release)``; the on-disk TSV cache is shared across
    sessions.
    """
    from buildstock_rate_impact_calculator.data.data_dictionary import (
        fetch_data_dictionary,
    )

    cache_key = (app_state.dataset_type, app_state.release)
    cache = st.session_state.setdefault("data_dictionary", {})
    if cache_key not in cache:
        try:
            cache[cache_key] = fetch_data_dictionary(
                base_uri=base_uri,
                cache_dir=app_state.output_dir,
                dataset_type=app_state.dataset_type or "",
                release=app_state.release or "",
            )
        except Exception:
            cache[cache_key] = {}
    return cache[cache_key]


def _help_text(data_dict: dict, column: str) -> str:
    """Tooltip text for a filter column.

    Returns ``"{field_description} (column: {col})"`` when a description is
    available; otherwise just the column name.
    """
    from buildstock_rate_impact_calculator.data.data_dictionary import (
        get_field_description,
    )

    desc = get_field_description(data_dict, column, default="")
    if desc and desc != column:
        return f"{desc} (column: {column})"
    return f"Metadata column: {column}"


def render_building_filter_page(app_state: AppState) -> None:
    """Render the building characteristic filter UI."""
    st.header("Building Filters")

    if not app_state.dataset_type or not app_state.release:
        st.warning("Please select a dataset on the Dataset tab first.")
        return

    if not app_state.selected_upgrades:
        st.warning(
            "Please select one or more upgrade scenarios on the Dataset tab first."
        )
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
    data_dict = _get_data_dictionary(app_state, mgr.config.base_uri)

    geo_filtered = app_state.apply_geo_filter(metadata)

    # ── Characteristic filter dropdowns (from DATASET_CONFIGS) ───────────────
    st.subheader("Characteristic Filters")
    st.caption(
        "Numeric columns show a range slider; categorical columns show a "
        "multiselect (empty = include all). The applicability summary below "
        "updates after each selection."
    )

    configured = list(mgr.config.filter_columns or [])
    filter_cols = [c for c in configured if c in geo_filtered.columns]
    missing_cols = [c for c in configured if c not in geo_filtered.columns]
    if missing_cols:
        st.caption(
            f"Not available in this dataset's metadata: {', '.join(missing_cols)}"
        )

    for col in filter_cols:
        label = _filter_label(col)
        help_text = _help_text(data_dict, col)

        if mgr.is_numeric_column(geo_filtered, col):
            # Range slider for numeric columns. Default to full range from the
            # currently visible data; restore prior numeric range if present.
            col_series = geo_filtered[col].drop_nulls()
            if col_series.is_empty():
                continue
            col_min = float(col_series.min())
            col_max = float(col_series.max())
            if col_min == col_max:
                # Degenerate range — show a caption, don't store a filter.
                st.caption(f"{label}: all rows have value {col_min:g}")
                if col in app_state.active_filters:
                    del app_state.active_filters[col]
                continue

            prior = app_state.active_filters.get(col)
            if isinstance(prior, dict):
                default_lo = float(prior.get("min", col_min))
                default_hi = float(prior.get("max", col_max))
                # Clamp to current data range so a stale saved range stays valid.
                default_lo = max(col_min, min(col_max, default_lo))
                default_hi = max(col_min, min(col_max, default_hi))
            else:
                default_lo, default_hi = col_min, col_max

            lo, hi = st.slider(
                label,
                min_value=col_min,
                max_value=col_max,
                value=(default_lo, default_hi),
                key=f"bf_{app_state.dataset_type}_{col}",
                help=help_text,
            )
            # Only persist the filter when the user has narrowed the range.
            if lo > col_min or hi < col_max:
                app_state.active_filters[col] = {"min": lo, "max": hi}
            elif col in app_state.active_filters:
                del app_state.active_filters[col]
        else:
            # Categorical multiselect (preserves prior list-shape filter).
            options = mgr.get_column_unique_values(geo_filtered, col)
            prior_val = app_state.active_filters.get(col, [])
            prior = (
                [v for v in prior_val if v in options]
                if isinstance(prior_val, list)
                else []
            )
            selected = st.multiselect(
                label,
                options=options,
                default=prior,
                key=f"bf_{app_state.dataset_type}_{col}",
                help=help_text,
                placeholder="All",
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
        st.metric(
            "Est. Download Size", format_bytes(app_state.estimated_download_bytes)
        )

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
