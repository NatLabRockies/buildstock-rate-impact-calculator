"""
Page: Building Characteristic Filters (Step 3)

Shows the dataset's configured characteristic filter columns
(DatasetConfig.filter_columns) as individual range-sliders (for numeric
columns) or multi-select dropdowns (for categorical columns). After each
selection the summary of matching/applicable buildings for the selected
upgrades, geographies, and filters is refreshed.

Metadata is loaded only after geographic selections are made (Geography step);
an explicit button covers the no-geographic-filter (entire dataset) case.

Tooltips for each filter are pulled from the dataset's ``data_dictionary.tsv``
(fetched lazily on first render and cached on disk and in
``st.session_state``).
"""

from __future__ import annotations
import re
import streamlit as st

from buildstock_rate_impact_calculator.ui.state import AppState
from buildstock_rate_impact_calculator.ui import steps
from buildstock_rate_impact_calculator.ui.applicability import (
    format_bytes,
    render_applicability_table,
)
from buildstock_rate_impact_calculator.ui.metadata_session import (
    get_session_metadata,
    load_session_metadata,
)


# Tokens that should render all-caps in filter labels rather than title-cased.
_ACRONYMS = {
    "RECS",
    "HVAC",
    "IECC",
    "ASHRAE",
    "DHW",
    "HSPF",
    "SEER",
    "EER",
    "AMI",
    "PV",
    "EV",
    "LED",
    "ACH",
    "WWR",
    "EUI",
    "TOU",
    "MSHP",
    "ASHP",
    "GSHP",
    "AFUE",
    "COP",
}

# Full-label overrides for columns whose auto-generated label reads poorly.
_LABEL_OVERRIDES = {
    "in.income_recs_2020": "RECS 2020 Standard Income Bins",
}

# Column whose categorical values are RECS dollar income bins (special display).
_INCOME_RECS_COL = "in.income_recs_2020"


def _titleize(text: str) -> str:
    """Title-case a snake/space string, keeping known acronyms all-caps."""
    parts = text.replace("_", " ").split()
    return " ".join(
        w.upper() if w.upper() in _ACRONYMS else w.capitalize() for w in parts
    )


def _filter_label(column: str) -> str:
    """Human-friendly label for a metadata column, e.g. 'in.heating_fuel' →
    'Heating Fuel', 'in.sqft..ft2' → 'Sqft (ft2)', 'in.geometry..recs' →
    'Geometry (RECS)'. Known acronyms (RECS, HVAC, …) render all-caps."""
    if column in _LABEL_OVERRIDES:
        return _LABEL_OVERRIDES[column]
    name = column
    for prefix in ("in.", "out.", "calc."):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    if ".." in name:
        name, unit = name.split("..", 1)
        return f"{_titleize(name)} ({unit})"
    return _titleize(name)


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
    available; otherwise just the column name. Adds a note for the tenure
    column that ``Not Available`` denotes a vacant dwelling unit.
    """
    from buildstock_rate_impact_calculator.data.data_dictionary import (
        get_field_description,
    )

    desc = get_field_description(data_dict, column, default="")
    if desc and desc != column:
        text = f"{desc} (column: {column})"
    else:
        text = f"Metadata column: {column}"
    if column.split(".")[-1] == "tenure":
        text += " 'Not Available' means the dwelling unit is vacant."
    return text


_BIN_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")
_BIN_LT_RE = re.compile(r"^<(\d+)$")
_BIN_PLUS_RE = re.compile(r"^(\d+)\+$")


def _income_bin_sort_key(value: object) -> tuple[int, float]:
    """Sort RECS income bins low→high; unparseable (e.g. 'Not Available') last."""
    s = str(value).strip().replace("$", "").replace(",", "")
    if _BIN_LT_RE.match(s):
        return (0, -1.0)  # "<20000" is the lowest bin
    if m := _BIN_RANGE_RE.match(s):
        return (0, float(m.group(1)))
    if m := _BIN_PLUS_RE.match(s):
        return (0, float(m.group(1)))
    return (1, 0.0)


def _format_income_bin(value: object) -> str:
    """Render a RECS income bin with dollar signs and thousands separators."""
    s = str(value).strip().replace("$", "").replace(",", "")
    if m := _BIN_LT_RE.match(s):
        return f"< ${int(m.group(1)):,}"
    if m := _BIN_RANGE_RE.match(s):
        return f"${int(m.group(1)):,} – ${int(m.group(2)):,}"
    if m := _BIN_PLUS_RE.match(s):
        return f"${int(m.group(1)):,}+"
    return str(value)


def render_building_filter_page(app_state: AppState) -> None:
    """Render the building characteristic filter UI."""
    st.header("Building Filters")

    if not steps.dataset_complete(app_state):
        steps.render_gate(
            "Select a dataset and upgrade scenarios first.",
            steps.STEP_DATASET,
            key="gate_filters_dataset",
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
            if (app_state.dataset_type or "").lower() == "comstock":
                # ComStock has no national metadata file — per-county only.
                st.info(
                    "ComStock metadata is only available per county. Select "
                    "states or counties on the Geography step first (whole-state "
                    "selections download every county in the state)."
                )
                if st.button("← Go to Geography", key="gate_filters_geo_comstock"):
                    steps.goto_step(steps.STEP_GEOGRAPHY)
                return
            st.info(
                "No geographic selections have been made yet. Select geographies "
                "on the Geography step, or load metadata for the entire dataset below."
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

            # ComStock sqft spans a huge range; step by 1000 ft² so the slider
            # isn't absurdly precise. Streamlit defaults the step otherwise.
            slider_step = None
            if (
                (app_state.dataset_type or "").lower() == "comstock"
                and col == mgr.config.floor_area_column
            ):
                slider_step = 1000.0

            lo, hi = st.slider(
                label,
                min_value=col_min,
                max_value=col_max,
                value=(default_lo, default_hi),
                step=slider_step,
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
            # RECS income bins: order low→high and display with $ and commas.
            ms_format = None
            if col == _INCOME_RECS_COL:
                options = sorted(options, key=_income_bin_sort_key)
                ms_format = _format_income_bin
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
                format_func=ms_format if ms_format else str,
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

    # ── Additional characteristics (pass-through into results) ───────────────
    st.divider()
    st.subheader("Additional Characteristics")
    st.caption(
        "Optional extra ``in.*`` columns to fetch for every selected building. "
        "They are passed through to the results table so you can break results "
        "down by attributes that aren't part of the default filter set."
    )

    try:
        all_in_cols = mgr.get_input_characteristic_columns()
    except Exception as exc:
        st.warning(f"Could not enumerate dataset characteristics: {exc}")
        all_in_cols = []

    # Exclude columns already covered by the default base set (filter cols,
    # state, geo) — selecting them as "additional" would be a no-op.
    base_set = set(mgr.get_app_metadata_columns())
    candidate_cols = [c for c in all_in_cols if c not in base_set]

    # Keep any already-selected additional columns visible even if they aren't
    # in the live schema (defensive — e.g. project loaded from disk).
    prior_additional = list(app_state.additional_columns or [])
    option_pool = sorted(set(candidate_cols) | set(prior_additional))

    label_lookup = {col: _filter_label(col) for col in option_pool}

    def _fmt(col: str) -> str:
        return f"{label_lookup.get(col, col)}  ({col})"

    selected_additional = st.multiselect(
        "Additional characteristic columns",
        options=option_pool,
        default=[c for c in prior_additional if c in option_pool],
        format_func=_fmt,
        key=f"bf_additional_{app_state.dataset_type}",
        help=(
            "Extra metadata columns to include with every building row. "
            "Changing the selection triggers a reload of metadata so the new "
            "columns are joined onto the cached base set."
        ),
        placeholder="None — use defaults only",
    )

    # If the selection changed, persist and invalidate the session metadata so
    # the page reloads with the new column set on the next render.
    if sorted(selected_additional) != sorted(prior_additional):
        app_state.additional_columns = list(selected_additional)
        # Drop the cached session metadata so the geo cache key changes pick
        # the new columns up on the next access.
        st.session_state.pop("session_metadata", None)
        st.rerun()

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
    geo_id_count = len(sel["states"]) + len(sel["county_ids"]) + len(sel["puma_ids"])

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Matching Buildings", f"{app_state.building_count:,}")
        from buildstock_rate_impact_calculator.billing.results import (
            MIN_SAMPLE_MODELS,
        )

        if 0 < app_state.building_count < MIN_SAMPLE_MODELS:
            st.warning(
                f"⚠ Only {app_state.building_count} building models match — "
                "weighted statistics may be unreliable."
            )
    with col2:
        st.metric(
            "Est. Timeseries Download",
            format_bytes(app_state.estimated_download_bytes),
            help=(
                "Estimated download for the per-building load profiles at run "
                "time. Metadata for the geographic selection has already been "
                "downloaded and cached."
            ),
        )

    # ── Selection breakdown (upgrades / geographies / characteristic filters) ─
    fcol1, fcol2, fcol3 = st.columns(3)
    with fcol1:
        st.metric(
            "Upgrade Scenarios",
            len(app_state.selected_upgrades or []),
            help="Number of scenarios (baseline + upgrades) selected on the Dataset step.",
        )
    with fcol2:
        st.metric(
            "Geography IDs",
            geo_id_count,
            help="Total states, counties, and PUMAs selected on the Geography step.",
        )
    with fcol3:
        st.metric(
            "Characteristic Filters",
            len(app_state.active_filters),
            help="Number of building-characteristic filters applied on this step.",
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
