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

from buildstock_rate_impact_calculator.rates.rate_builder import UrdbRateBuilder


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
    from buildstock_rate_impact_calculator.rates.urdb_client import URDBClient

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
            c for c in companies
            if all(w in c.get("label", "").lower() for w in words)
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
    _sector_default = "Commercial" if dataset_type.lower() == "comstock" else "Residential"
    with filter_col1:
        sector_choice: str = st.selectbox(
            "Sector",
            options=["Residential", "Commercial"],
            index=0 if _sector_default == "Residential" else 1,
            key="urdb_filter_sector",
        )

        service_type_options = ["Any", "Bundled", "Energy", "Delivery", "Delivery with Standard Offer"]
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
                effective_date.year, effective_date.month, effective_date.day,
                tzinfo=_dt.timezone.utc,
            )
            effective_ts = int(_eff_dt.timestamp())

            servicetype_param = None if service_type_choice == "Any" else service_type_choice

            with st.spinner(f"Searching rates for {selected_label}…"):
                results = client.search_rates_by_utility_raw(
                    utility_name=selected_label,
                    sector=sector_choice,
                    servicetype=servicetype_param,
                    effective_as_of=effective_ts,
                    limit=50,
                )
            # Strip to display-only fields — full rate data is fetched on explicit load
            _DISPLAY_KEYS = {"name", "utility", "sector", "approved", "startdate", "enddate", "label", "is_default"}
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
            st.info("No default rates found for this utility. Uncheck 'Default rates only' to see all rates.")
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
    st.dataframe(df, width='stretch', hide_index=True)

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


def render_tou_builder() -> Optional[dict]:
    """
    Render an interactive TOU rate builder form.

    Returns the built URDB v8 rate dict when the user clicks 'Build Rate',
    or None if the form hasn't been submitted yet.
    """
    st.markdown("Define a custom Time-of-Use rate structure.")

    with st.form("tou_builder_form"):
        col1, col2 = st.columns(2)
        with col1:
            rate_name = st.text_input("Rate Name", value="Custom TOU Rate")
            utility_name = st.text_input("Utility Name", value="Custom Utility")
        with col2:
            sector = st.selectbox("Sector", options=["Residential", "Commercial", "Industrial"])
            fixed_charge = st.number_input("Monthly Fixed Charge ($)", min_value=0.0, value=0.0, step=0.50)

        st.subheader("Energy Rates")
        col3, col4 = st.columns(2)
        with col3:
            off_peak_rate = st.number_input("Off-Peak Rate ($/kWh)", min_value=0.0, value=0.10, step=0.001, format="%.4f")
        with col4:
            peak_rate = st.number_input("Peak Rate ($/kWh)", min_value=0.0, value=0.20, step=0.001, format="%.4f")

        st.subheader("Peak Hours")
        st.caption("Select which hours of the day are 'peak' (0 = midnight, 17 = 5pm).")
        peak_hour_options = list(range(24))
        peak_hours_default = [16, 17, 18, 19, 20]  # 4pm–8pm
        peak_hours = st.multiselect(
            "Peak hours (24-hr clock)",
            options=peak_hour_options,
            default=peak_hours_default,
            format_func=lambda h: f"{h:02d}:00",
            key="tou_peak_hours",
        )

        st.subheader("Peak Season (months)")
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        all_months = list(range(1, 13))
        peak_months = st.multiselect(
            "Peak months",
            options=all_months,
            default=all_months,
            format_func=lambda m: month_names[m - 1],
            key="tou_peak_months",
        )

        weekend_peak = st.checkbox("Apply peak rate on weekends too", value=False)

        demand_enabled = st.checkbox("Add demand charge", value=False)
        demand_rate = 0.0
        if demand_enabled:
            demand_rate = st.number_input("Demand Rate ($/kW/month)", min_value=0.0, value=10.0, step=0.50)

        submitted = st.form_submit_button("Build Rate")

    if not submitted:
        return None

    # Validate inputs
    if not rate_name.strip():
        st.error("Rate name is required.")
        return None
    if not peak_hours:
        st.error("At least one peak hour must be selected.")
        return None
    if not peak_months:
        st.error("At least one peak month must be selected.")
        return None

    try:
        builder = UrdbRateBuilder(validate_pysam=False)
        (builder
            .basic_info(rate_name.strip(), utility_name.strip(), sector)
            .monthly_fixed_charge(fixed_charge)
            .add_energy_tier(0, None, off_peak_rate)   # Period 0 = off-peak
            .add_energy_tier(1, None, peak_rate)        # Period 1 = peak
            .simple_tou_schedule(
                peak_hours=peak_hours,
                peak_months=peak_months,
                weekend_peak=weekend_peak,
            )
        )
        if demand_enabled and demand_rate > 0:
            builder.enable_demand_charges().add_demand_tier(0, None, demand_rate)

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
    st.markdown("Enter a simple flat rate with an optional fixed monthly charge.")

    with st.form("flat_rate_form"):
        col1, col2 = st.columns(2)
        with col1:
            rate_name = st.text_input("Rate Name", value="Custom Flat Rate")
            energy_rate = st.number_input(
                "Energy Rate ($/kWh)", min_value=0.0, value=0.12, step=0.001, format="%.4f"
            )
        with col2:
            utility_name = st.text_input("Utility Name", value="Custom Utility")
            fixed_charge = st.number_input(
                "Monthly Fixed Charge ($)", min_value=0.0, value=0.0, step=0.50
            )
        sector = st.selectbox("Sector", options=["Residential", "Commercial"])
        submitted = st.form_submit_button("Create Flat Rate")

    if not submitted:
        return None

    try:
        builder = UrdbRateBuilder.simple_flat_rate_builder(
            utility_name=utility_name.strip() or "Custom Utility",
            rate_name=rate_name.strip() or "Custom Flat Rate",
            sector=sector,
            energy_rate=energy_rate,
            monthly_fixed=fixed_charge,
            validate_pysam=False,
        )
        rate_dict = builder.to_pysam_dict()
        st.success(f"Flat rate '{rate_dict.get('name', rate_name)}' created: ${energy_rate:.4f}/kWh + ${fixed_charge:.2f}/month")
        return rate_dict
    except ValueError as exc:
        st.error(str(exc))
        return None
