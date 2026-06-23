"""
Rate editor UI components for Phase 4.

Three modes:
  1. URDB Search — search OpenEI for real utility rates
  2. TOU Builder — build a custom TOU rate interactively
  3. Flat Rate   — simple $/kWh entry
"""

from __future__ import annotations
import datetime
import pandas as pd
import streamlit as st
from typing import Optional

from buildstock_rate_impact_calculator.rates.rate_builder import (
    UrdbRateBuilder,
    FixedChargeUnits,
)

_MONTH_NAMES = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]
_ALL_MONTHS = list(range(1, 13))
_ALL_HOURS = list(range(24))
_DAY_OPTIONS = ["All days", "Weekdays only", "Weekends only"]


def build_tou_schedules(
    periods: list[dict],
) -> tuple[list[list[int]], list[list[int]]]:
    """
    Build URDB 12x24 weekday/weekend schedule matrices from TOU period rows.

    Each row is a dict with keys ``months`` (1-12), ``hours`` (0-23) and
    ``days`` (one of _DAY_OPTIONS). Period indices in the matrices are
    1-based by row order; uncovered cells stay at period 0 (the base rate).
    Where rows overlap, later rows overwrite earlier ones.

    Returns:
        (weekday_schedule, weekend_schedule) 12x24 matrices of period indices.
    """
    weekday = [[0] * 24 for _ in range(12)]
    weekend = [[0] * 24 for _ in range(12)]
    for idx, period in enumerate(periods, start=1):
        days = period.get("days", "All days")
        on_weekday = days in ("All days", "Weekdays only")
        on_weekend = days in ("All days", "Weekends only")
        for month in period["months"]:
            for hour in period["hours"]:
                if on_weekday:
                    weekday[month - 1][hour] = idx
                if on_weekend:
                    weekend[month - 1][hour] = idx
    return weekday, weekend


def build_flat_demand(
    rows: list[dict],
) -> tuple[list[list[dict]], list[int]]:
    """
    Build URDB flatdemandstructure / flatdemandmonths from monthly demand rows.

    Each row is a dict with keys ``rate`` ($/kW) and ``months`` (1-12).
    Months not covered by any row get a $0/kW period. Where rows overlap,
    later rows overwrite earlier ones.

    Returns:
        (flatdemandstructure, flatdemandmonths) where flatdemandmonths is a
        list of 12 zero-based period indices (Jan-Dec).
    """
    months: list = [None] * 12
    structure: list[list[dict]] = []
    for row in rows:
        idx = len(structure)
        structure.append([{"rate": float(row["rate"]), "adj": 0.0}])
        for month in row["months"]:
            months[month - 1] = idx
    if any(m is None for m in months):
        zero_idx = len(structure)
        structure.append([{"rate": 0.0, "adj": 0.0}])
        months = [zero_idx if m is None else m for m in months]
    return structure, months


def _fmt_ts(ts: object) -> str:
    """Convert a Unix timestamp (int or None) to a YYYY-MM-DD string."""
    if not ts:
        return "—"
    try:
        return datetime.datetime.fromtimestamp(
            int(ts), tz=datetime.timezone.utc
        ).strftime("%Y-%m-%d")
    except Exception:
        return "—"


def _get_urdb_client(api_key: str):
    """Return a URDBClient for *api_key*."""
    from buildstock_rate_impact_calculator.rates.urdb_client import URDBClient

    return URDBClient(api_key=api_key)


def render_urdb_search(api_key: str, dataset_type: str = "resstock") -> Optional[dict]:
    """
    Render a URDB rate search UI.

    Step 1 — the user types a query; the full utility-company list is
    filtered by substring match to populate a selectbox.

    Step 2 — "Search Rates" calls the URDB rates API with the selected utility's
    label as the ``ratesforutility`` parameter.

    Step 3 — the user picks a rate from the results table and clicks
    "Load selected rate" to return the full rate dict.

    Args:
        api_key: OpenEI API key (from sidebar).
        dataset_type: 'resstock' or 'comstock', used to default the sector filter.

    Returns:
        Selected rate dict or None.
    """

    st.markdown(
        "Search for an existing rate in the "
        "[Utility Rate Database](https://openei.org/wiki/Utility_Rate_Database)."
    )

    # ── Step 1: fuzzy-filter the utility company list ─────────────────────────
    search_query: str = st.text_input(
        "Search utility name or alias",
        placeholder="e.g. Xcel Energy, Rocky Mountain Power",
        key="urdb_search_query",
    )

    # Lazy-load utility company list on first non-empty query
    if search_query and "urdb_utility_companies" not in st.session_state:
        if not api_key:
            st.error("Enter your OpenEI API key in the sidebar before searching.")
            return None
        try:
            client = _get_urdb_client(api_key)
            with st.spinner("Loading utility list…"):
                companies = client.get_utility_companies()
            st.session_state["urdb_utility_companies"] = companies
        except Exception as exc:
            st.error(f"Failed to load utility list: {exc}")
            return None

    if not search_query:
        return None

    companies: list = st.session_state.get("urdb_utility_companies", [])
    if not companies:
        return None

    # Substring match on label (case-insensitive); word-by-word fallback
    q = search_query.lower()
    matches = [c for c in companies if q in c.get("label", "").lower()]
    if not matches:
        words = q.split()
        matches = [
            c for c in companies if all(w in c.get("label", "").lower() for w in words)
        ]

    if not matches:
        st.caption("No matching utilities found. Try a different search term.")
        return None

    # Cap dropdown at 50 entries for performance
    match_labels = [c["label"] for c in matches[:50]]

    selected_label: str = st.selectbox(
        "Select utility",
        options=match_labels,
        key="urdb_utility_selectbox",
    )

    # ── Search filters ────────────────────────────────────────────────────────
    st.markdown("**Search Filters**")
    filter_col1, filter_col2 = st.columns(2)

    # Sector — default based on dataset type
    _sector_default = (
        "Commercial" if dataset_type.lower() == "comstock" else "Residential"
    )
    with filter_col1:
        sector_choice: str = st.selectbox(
            "Sector",
            options=["Residential", "Commercial"],
            index=0 if _sector_default == "Residential" else 1,
            key="urdb_filter_sector",
        )

        service_type_options = [
            "Any",
            "Bundled",
            "Energy",
            "Delivery",
            "Delivery with Standard Offer",
        ]
        service_type_choice: str = st.selectbox(
            "Service Type",
            options=service_type_options,
            index=0,
            key="urdb_filter_servicetype",
        )

    with filter_col2:
        effective_date: datetime.date = st.date_input(
            "Effective As Of",
            value=datetime.date.today(),
            key="urdb_filter_effectivedate",
        )

        default_only: bool = st.checkbox(
            "Default rates only",
            value=False,
            key="urdb_filter_approved",
        )

    # ── Step 2: search rates for the selected utility ─────────────────────────
    if st.button("Search Rates", key="urdb_search_btn"):
        if not api_key:
            st.error("Enter your OpenEI API key in the sidebar before searching.")
            return None
        st.session_state.pop("urdb_search_results", None)
        st.session_state.pop("urdb_selected_idx", None)
        try:
            import datetime as _dt

            client = _get_urdb_client(api_key)

            # Convert effective_as_of date to Unix timestamp (start of day UTC)
            _eff_dt = _dt.datetime(
                effective_date.year,
                effective_date.month,
                effective_date.day,
                tzinfo=_dt.timezone.utc,
            )
            effective_ts = int(_eff_dt.timestamp())

            servicetype_param = (
                None if service_type_choice == "Any" else service_type_choice
            )

            with st.spinner(f"Searching rates for {selected_label}…"):
                results = client.search_rates_by_utility_raw(
                    utility_name=selected_label,
                    sector=sector_choice,
                    servicetype=servicetype_param,
                    effective_as_of=effective_ts,
                    limit=50,
                )
            # Strip to display-only fields — full rate data is fetched on explicit load
            _DISPLAY_KEYS = {
                "name",
                "utility",
                "sector",
                "approved",
                "startdate",
                "enddate",
                "label",
                "is_default",
            }
            st.session_state["urdb_search_results"] = [
                {k: v for k, v in r.items() if k in _DISPLAY_KEYS} for r in results
            ]
            st.rerun()
        except Exception as exc:
            st.error(f"URDB search failed: {exc}")
            return None

    # ── Step 3: display results and let user load a rate ──────────────────────
    results = st.session_state.get("urdb_search_results", [])
    if not results:
        if "urdb_search_results" in st.session_state:
            st.info("No rates found for this utility. Try selecting a different entry.")
        return None

    # Apply client-side default-only filter based on the is_default field in results
    if default_only:
        results = [r for r in results if r.get("is_default")]
        if not results:
            st.info(
                "No default rates found for this utility. Uncheck 'Default rates only' to see all rates."
            )
            return None

    display_rows = [
        {
            "Name": r.get("name", "—"),
            "Utility": r.get("utility", "—"),
            "Sector": r.get("sector", "—"),
            "Approved": "✓" if r.get("approved") else "—",
            "Start Date": _fmt_ts(r.get("startdate")),
            "End Date": _fmt_ts(r.get("enddate")),
            "Label": r.get("label", "—"),
        }
        for r in results
    ]
    df = pd.DataFrame(display_rows)
    st.dataframe(df, width="stretch", hide_index=True)

    label_options = [r.get("label", "") for r in results if r.get("label")]
    name_options = [
        f"{r.get('name', '?')} ({r.get('utility', '?')})"
        for r in results
        if r.get("label")
    ]
    if not label_options:
        return None

    chosen_idx = st.selectbox(
        "Select rate",
        options=range(len(name_options)),
        format_func=lambda i: name_options[i],
        key="urdb_selected_idx",
    )

    if st.button("Load selected rate", key="urdb_load_btn"):
        chosen_label = label_options[chosen_idx]
        try:
            client = _get_urdb_client(api_key)
            with st.spinner("Fetching full rate data…"):
                full_rate = client.get_rate_by_label(chosen_label)
            if full_rate:
                st.success(f"Loaded: {full_rate.get('name', chosen_label)}")
                return full_rate
            else:
                st.error("Could not fetch full rate data.")
        except Exception as exc:
            st.error(f"Failed to load rate: {exc}")

    return None


def _row_ids(prefix: str) -> list[int]:
    """Session-state list of row ids for a dynamic row group."""
    return st.session_state.setdefault(f"{prefix}_row_ids", [])


def _add_row(prefix: str) -> None:
    """Append a new row id to a dynamic row group."""
    next_id = st.session_state.get(f"{prefix}_next_id", 0)
    _row_ids(prefix).append(next_id)
    st.session_state[f"{prefix}_next_id"] = next_id + 1


def _render_tou_period_row(
    prefix: str,
    rid: int,
    position: int,
    rate_label: str,
    default_rate: float,
    rate_format: str,
    rate_step: float,
) -> tuple[dict, bool]:
    """
    Render one TOU period row (name, rate, months, hours, day type).

    Returns:
        (row_dict, removed) where removed indicates the delete button was clicked.
    """
    with st.container(border=True):
        c1, c2, c3 = st.columns([3, 2, 1], vertical_alignment="bottom")
        name = c1.text_input(
            "Period name", value=f"Period {position}", key=f"{prefix}_name_{rid}"
        )
        rate = c2.number_input(
            rate_label,
            min_value=0.0,
            value=default_rate,
            step=rate_step,
            format=rate_format,
            key=f"{prefix}_rate_{rid}",
        )
        removed = c3.button("✕", key=f"{prefix}_del_{rid}", help="Remove this period")

        m1, m2 = st.columns(2)
        months = m1.multiselect(
            "Months",
            options=_ALL_MONTHS,
            default=_ALL_MONTHS,
            format_func=lambda m: _MONTH_NAMES[m - 1],
            key=f"{prefix}_months_{rid}",
        )
        hours = m2.multiselect(
            "Hours (24-hr clock)",
            options=_ALL_HOURS,
            default=[16, 17, 18, 19, 20],
            format_func=lambda h: f"{h:02d}:00",
            key=f"{prefix}_hours_{rid}",
        )
        days = st.selectbox(
            "Applies on", options=_DAY_OPTIONS, key=f"{prefix}_days_{rid}"
        )

    return {
        "name": name,
        "rate": rate,
        "months": months,
        "hours": hours,
        "days": days,
    }, removed


def _schedule_preview_df(schedule: list[list[int]]) -> pd.DataFrame:
    """12x24 schedule matrix as a months-by-hours DataFrame for display."""
    return pd.DataFrame(
        schedule, index=_MONTH_NAMES, columns=[f"{h:02d}" for h in range(24)]
    )


def render_tou_builder() -> Optional[dict]:
    """
    Render the interactive TOU rate builder.

    Supports a fixed meter charge ($/month or $/day), a base energy rate plus
    any number of TOU energy periods (by month, hour, and weekday/weekend),
    and optional demand charges: monthly (flat) demand charges by month and
    TOU demand charges by month and hour.

    Returns the built URDB v8 rate dict when the user clicks 'Build Rate',
    or None otherwise.
    """
    st.markdown(
        "Define a custom electricity rate with time-of-use energy rates and "
        "optional demand charges."
    )

    # ── Basic info ─────────────────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        rate_name = st.text_input(
            "Rate Name", value="Custom TOU Rate", key="tou_rate_name"
        )
        sector = st.selectbox(
            "Sector",
            options=["Residential", "Commercial", "Industrial"],
            key="tou_sector",
        )
    with col2:
        utility_name = st.text_input(
            "Utility Name", value="Custom Utility", key="tou_utility_name"
        )

    # ── Fixed meter charge ─────────────────────────────────────────────────────
    st.subheader("Fixed Meter Charge")
    fc1, fc2 = st.columns([3, 2])
    with fc1:
        fixed_charge = st.number_input(
            "Fixed Charge",
            min_value=0.0,
            value=0.0,
            step=0.50,
            format="%.2f",
            key="tou_fixed_charge",
        )
    with fc2:
        fixed_units = st.selectbox(
            "Units", options=["$/month", "$/day"], key="tou_fixed_units"
        )

    # ── Energy rates ───────────────────────────────────────────────────────────
    st.subheader("Energy Rates")
    base_rate = st.number_input(
        "Base (off-peak) Energy Rate ($/kWh)",
        min_value=0.0,
        value=0.10,
        step=0.001,
        format="%.4f",
        key="tou_base_rate",
        help="Applies to all hours not covered by a TOU energy period below.",
    )
    st.caption(
        "Add TOU energy periods for hours billed at a different rate. "
        "Where periods overlap, the one lower in the list takes precedence."
    )

    energy_periods: list[dict] = []
    for position, rid in enumerate(list(_row_ids("tou_ep")), start=1):
        row, removed = _render_tou_period_row(
            "tou_ep", rid, position, "Rate ($/kWh)", 0.20, "%.4f", 0.001
        )
        if removed:
            _row_ids("tou_ep").remove(rid)
            st.rerun()
        energy_periods.append(row)

    if st.button("＋ Add TOU Energy Period", key="tou_ep_add"):
        _add_row("tou_ep")
        st.rerun()

    # ── Demand charges ─────────────────────────────────────────────────────────
    st.subheader("Demand Charges (optional)")

    st.markdown(
        "**Monthly demand charges** — applied to each month's peak demand, "
        "whenever it occurs."
    )
    monthly_demand_rows: list[dict] = []
    for rid in list(_row_ids("tou_fd")):
        with st.container(border=True):
            c1, c2, c3 = st.columns([2, 3, 1], vertical_alignment="bottom")
            fd_rate = c1.number_input(
                "Rate ($/kW)",
                min_value=0.0,
                value=10.0,
                step=0.50,
                format="%.2f",
                key=f"tou_fd_rate_{rid}",
            )
            fd_months = c2.multiselect(
                "Months",
                options=_ALL_MONTHS,
                default=_ALL_MONTHS,
                format_func=lambda m: _MONTH_NAMES[m - 1],
                key=f"tou_fd_months_{rid}",
            )
            if c3.button("✕", key=f"tou_fd_del_{rid}", help="Remove this charge"):
                _row_ids("tou_fd").remove(rid)
                st.rerun()
        monthly_demand_rows.append({"rate": fd_rate, "months": fd_months})

    if st.button("＋ Add Monthly Demand Charge", key="tou_fd_add"):
        _add_row("tou_fd")
        st.rerun()

    st.markdown(
        "**TOU demand charges** — applied to the peak demand occurring within "
        "the selected months and hours."
    )
    demand_periods: list[dict] = []
    for position, rid in enumerate(list(_row_ids("tou_dp")), start=1):
        row, removed = _render_tou_period_row(
            "tou_dp", rid, position, "Rate ($/kW)", 10.0, "%.2f", 0.50
        )
        if removed:
            _row_ids("tou_dp").remove(rid)
            st.rerun()
        demand_periods.append(row)

    if st.button("＋ Add TOU Demand Period", key="tou_dp_add"):
        _add_row("tou_dp")
        st.rerun()

    # ── Schedule preview ───────────────────────────────────────────────────────
    if energy_periods or demand_periods:
        with st.expander("Preview TOU schedules"):
            if energy_periods:
                weekday, weekend = build_tou_schedules(energy_periods)
                legend = "; ".join(
                    [f"0 = base (${base_rate:.4f}/kWh)"]
                    + [
                        f"{i} = {p['name']} (${p['rate']:.4f}/kWh)"
                        for i, p in enumerate(energy_periods, start=1)
                    ]
                )
                st.markdown(f"**Energy periods:** {legend}")
                st.caption("Weekdays")
                st.dataframe(_schedule_preview_df(weekday))
                st.caption("Weekends")
                st.dataframe(_schedule_preview_df(weekend))
            if demand_periods:
                d_weekday, d_weekend = build_tou_schedules(demand_periods)
                legend = "; ".join(
                    ["0 = no TOU demand charge"]
                    + [
                        f"{i} = {p['name']} (${p['rate']:.2f}/kW)"
                        for i, p in enumerate(demand_periods, start=1)
                    ]
                )
                st.markdown(f"**Demand periods:** {legend}")
                st.caption("Weekdays")
                st.dataframe(_schedule_preview_df(d_weekday))
                st.caption("Weekends")
                st.dataframe(_schedule_preview_df(d_weekend))

    # ── Build ──────────────────────────────────────────────────────────────────
    if not st.button("Build Rate", type="primary", key="tou_build_btn"):
        return None

    errors: list[str] = []
    if not rate_name.strip():
        errors.append("Rate name is required.")
    for period in energy_periods:
        if not period["months"] or not period["hours"]:
            errors.append(
                f"Energy period '{period['name']}' needs at least one month and one hour."
            )
    for i, row in enumerate(monthly_demand_rows, start=1):
        if not row["months"]:
            errors.append(f"Monthly demand charge {i} needs at least one month.")
    for period in demand_periods:
        if not period["months"] or not period["hours"]:
            errors.append(
                f"Demand period '{period['name']}' needs at least one month and one hour."
            )
    if errors:
        for err in errors:
            st.error(err)
        return None

    try:
        builder = UrdbRateBuilder(validate_pysam=False)
        builder.basic_info(
            rate_name.strip(), utility_name.strip() or "Custom Utility", sector
        )
        builder.fixed_charge(fixed_charge, FixedChargeUnits(fixed_units))

        # Energy: period 0 is the base rate; TOU periods follow in row order
        builder.add_energy_tier(0, None, base_rate)
        for i, period in enumerate(energy_periods, start=1):
            builder.add_energy_tier(i, None, period["rate"])
        weekday, weekend = build_tou_schedules(energy_periods)
        builder.set_tou_schedule(weekday, weekend)

        if demand_periods:
            builder.enable_demand_charges()
            builder.add_demand_tier(
                0, None, 0.0
            )  # hours outside all TOU demand periods
            for i, period in enumerate(demand_periods, start=1):
                builder.add_demand_tier(i, None, period["rate"])
            d_weekday, d_weekend = build_tou_schedules(demand_periods)
            builder.set_demand_schedule(d_weekday, d_weekend)

        if monthly_demand_rows:
            structure, flat_months = build_flat_demand(monthly_demand_rows)
            for period_idx, tiers in enumerate(structure):
                for tier in tiers:
                    builder.add_flat_demand_tier(period_idx, None, tier["rate"])
            builder.set_flat_demand_months(flat_months)

        validation_errors = builder.validate()
        if validation_errors:
            for err in validation_errors:
                st.error(err)
            return None

        rate_dict = builder.to_pysam_dict()
        st.success(f"Rate '{rate_name}' built successfully.")
        return rate_dict
    except Exception as exc:
        st.error(f"Failed to build rate: {exc}")
        return None


def render_flat_rate_form() -> Optional[dict]:
    """
    Render a simple flat-rate entry form.

    Returns the URDB-compatible dict when submitted, or None.
    """
    st.markdown("Enter a simple flat rate with an optional fixed meter charge.")

    with st.form("flat_rate_form"):
        col1, col2 = st.columns(2)
        with col1:
            rate_name = st.text_input("Rate Name", value="Custom Flat Rate")
            energy_rate = st.number_input(
                "Energy Rate ($/kWh)",
                min_value=0.0,
                value=0.12,
                step=0.001,
                format="%.4f",
            )
        with col2:
            utility_name = st.text_input("Utility Name", value="Custom Utility")
            fc1, fc2 = st.columns([3, 2])
            fixed_charge = fc1.number_input(
                "Fixed Charge", min_value=0.0, value=0.0, step=0.50
            )
            fixed_units = fc2.selectbox("Units", options=["$/month", "$/day"])
        sector = st.selectbox("Sector", options=["Residential", "Commercial"])
        submitted = st.form_submit_button("Create Flat Rate")

    if not submitted:
        return None

    try:
        builder = (
            UrdbRateBuilder(validate_pysam=False)
            .basic_info(
                rate_name.strip() or "Custom Flat Rate",
                utility_name.strip() or "Custom Utility",
                sector,
            )
            .fixed_charge(fixed_charge, FixedChargeUnits(fixed_units))
            .simple_flat_rate(energy_rate)
        )
        rate_dict = builder.to_pysam_dict()
        st.success(
            f"Flat rate '{rate_dict.get('name', rate_name)}' created: "
            f"${energy_rate:.4f}/kWh + ${fixed_charge:.2f}{fixed_units[1:]}"
        )
        return rate_dict
    except ValueError as exc:
        st.error(str(exc))
        return None
