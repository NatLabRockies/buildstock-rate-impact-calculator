"""
Page 6: Results Visualization & Export

Displays:
  - Energy source pills: which bill sources feed the statistics
  - Summary table: mean/median/total bill per scenario
  - Bill Summary view toggle: total-bill table vs by-fuel stacked bar
  - Bar chart: mean annual bill ($ for ResStock, $/ft² for ComStock)
  - Box-plot distribution: bill spread per scenario
  - Bill Savings vs baseline (Average / Total toggle)
  - Download CSV button (st.download_button)
  - Save to output directory button
"""

from __future__ import annotations

import polars as pl
import streamlit as st

from buildstock_rate_impact_calculator.ui.state import AppState
from buildstock_rate_impact_calculator.data.buildstock_manager import (
    BuildStockDataManager,
)
from buildstock_rate_impact_calculator.data.upgrade_labels import get_upgrade_label


def _scenario_display(base_uri: str, upgrade_id: int) -> str:
    try:
        label = get_upgrade_label(base_uri, upgrade_id)
    except Exception:
        label = f"Upgrade {upgrade_id}"
    return label if upgrade_id == 0 else f"Upgrade {upgrade_id}: {label}"


# ---------------------------------------------------------------------------
# Configured-rates summary helpers (pure functions, unit-testable)
# ---------------------------------------------------------------------------


def _fmt_rate_range(lo: float, hi: float, unit: str, decimals: int) -> str:
    """'$0.0800–$0.1200/kWh' or '$0.1200/kWh' when lo == hi."""
    if abs(lo - hi) < 0.5 * 10**-decimals:
        return f"${lo:,.{decimals}f}{unit}"
    return f"${lo:,.{decimals}f}–${hi:,.{decimals}f}{unit}"


def _tier_rates(structure: list) -> list[float]:
    """Flatten a URDB period/tier structure into effective rates (rate + adj)."""
    return [
        float(tier.get("rate", 0.0)) + float(tier.get("adj", 0.0) or 0.0)
        for period in (structure or [])
        for tier in period
    ]


def _electricity_charge_summary(rate: dict) -> dict[str, str]:
    """Display strings for a URDB-format electricity rate's charge components.

    Returns a dict with ``fixed``, ``energy``, and ``demand`` keys. URDB rates
    that have not been fetched yet (label-only) are flagged as such.
    """
    if "energyratestructure" not in rate:
        if "label" in rate:
            note = f"URDB rate {rate['label']} — details fetched at run time"
        else:
            note = "—"
        return {"fixed": "—", "energy": note, "demand": "—"}

    fixed_val = rate.get("fixedchargefirstmeter")
    if fixed_val is not None:
        fixed_units = str(rate.get("fixedchargeunits") or "$/month")
        unit_suffix = fixed_units[1:] if fixed_units.startswith("$") else fixed_units
        fixed = f"${float(fixed_val):,.2f}{unit_suffix}"
    else:
        fixed = "—"

    energy_rates = _tier_rates(rate.get("energyratestructure"))
    if energy_rates:
        periods = rate.get("energyratestructure") or []
        n_periods = len(periods)
        n_tiers = sum(len(p) for p in periods)
        energy = _fmt_rate_range(min(energy_rates), max(energy_rates), "/kWh", 4)
        quals = []
        if n_periods > 1:
            quals.append(f"{n_periods} TOU periods")
        if n_tiers > n_periods:
            quals.append("tiered")
        if quals:
            energy += f" ({', '.join(quals)})"
    else:
        energy = "—"

    demand_parts = []
    tou_demand = _tier_rates(rate.get("demandratestructure"))
    if tou_demand:
        demand_parts.append(
            _fmt_rate_range(min(tou_demand), max(tou_demand), "/kW", 2) + " (TOU)"
        )
    flat_demand = _tier_rates(rate.get("flatdemandstructure"))
    if flat_demand:
        demand_parts.append(
            _fmt_rate_range(min(flat_demand), max(flat_demand), "/kW", 2) + " (monthly)"
        )
    demand = "; ".join(demand_parts) if demand_parts else "—"

    return {"fixed": fixed, "energy": energy, "demand": demand}


def _fuel_charge_summary(rate: dict) -> dict[str, str]:
    """Display strings for a custom fuel rate's charge components."""
    fixed = f"${float(rate.get('fixed_monthly_charge', 0.0) or 0.0):,.2f}/month"
    if rate.get("is_block_rate"):
        blocks = rate.get("blocks") or []
        unit = str(rate.get("block_unit", "$/Therm"))
        unit_suffix = unit[1:] if unit.startswith("$") else unit
        block_rates = [float(b.get("rate", 0.0)) for b in blocks]
        if block_rates:
            energy = (
                _fmt_rate_range(min(block_rates), max(block_rates), unit_suffix, 2)
                + f" ({len(blocks)} blocks)"
            )
        else:
            energy = "—"
    else:
        unit = str(rate.get("rate_unit", "$/Therm"))
        unit_suffix = unit[1:] if unit.startswith("$") else unit
        energy = f"${float(rate.get('flat_rate', 0.0)):,.2f}{unit_suffix} flat"
    return {"fixed": fixed, "energy": energy, "demand": "—"}


def _build_rates_summary_rows(
    app_state: AppState,
    upgrade_ids: list[int],
    base_uri: str,
    result_columns: set[str],
) -> list[dict[str, str]]:
    """Rows for the Configured Rates table.

    One row per (rate, energy source) with its charge components and the
    scenarios it applies to. Fuels present in the results without an assigned
    rate get a 'Dataset default' row.
    """
    from buildstock_rate_impact_calculator.rates.fuel_rate import (
        FUEL_TYPE_LABELS,
        FUEL_TYPES,
    )

    def _scenario_key(u: int) -> str:
        return "baseline" if u == 0 else f"upgrade_{u}"

    sk_labels = {
        _scenario_key(u): _scenario_display(base_uri, u) for u in sorted(upgrade_ids)
    }

    rows: list[dict[str, str]] = []

    # Electricity — group scenarios by assigned rate name.
    elec_applies: dict[str, list[str]] = {}
    for sk, label in sk_labels.items():
        name = (app_state.rate_assignments or {}).get(sk)
        if name:
            elec_applies.setdefault(name, []).append(label)
    for name, labels in elec_applies.items():
        parts = _electricity_charge_summary(app_state.rate_library.get(name, {}))
        rows.append(
            {
                "Rate": name,
                "Energy Source": "Electricity",
                "Fixed Charge": parts["fixed"],
                "Energy Charges": parts["energy"],
                "Demand Charges": parts["demand"],
                "Applies To": ", ".join(labels),
            }
        )

    # Fuels — only those actually present in the results frame.
    for ft in FUEL_TYPES:
        if f"{ft}_bill" not in result_columns:
            continue
        ft_applies: dict[str | None, list[str]] = {}
        for sk, label in sk_labels.items():
            name = ((app_state.fuel_rate_assignments or {}).get(sk) or {}).get(ft)
            ft_applies.setdefault(name, []).append(label)
        for name, labels in ft_applies.items():
            if name is None:
                rows.append(
                    {
                        "Rate": "Dataset default",
                        "Energy Source": FUEL_TYPE_LABELS[ft],
                        "Fixed Charge": "—",
                        "Energy Charges": "Annual fuel bill from dataset metadata",
                        "Demand Charges": "—",
                        "Applies To": ", ".join(labels),
                    }
                )
            else:
                parts = _fuel_charge_summary(app_state.fuel_rate_library.get(name, {}))
                rows.append(
                    {
                        "Rate": name,
                        "Energy Source": FUEL_TYPE_LABELS[ft],
                        "Fixed Charge": parts["fixed"],
                        "Energy Charges": parts["energy"],
                        "Demand Charges": parts["demand"],
                        "Applies To": ", ".join(labels),
                    }
                )
    return rows


def render_results_page(app_state: AppState) -> None:
    """Render the results visualization and export page."""
    st.header("Results")

    # ── Guard: need results ───────────────────────────────────────────────────
    results_key = "run_results"
    result_store = st.session_state.get(results_key, {})
    df: pl.DataFrame | None = result_store.get("df")

    # Also check for results loaded via load_project
    if df is None:
        df = st.session_state.get("results_df")

    if df is None or len(df) == 0:
        if not app_state.results_available:
            st.info(
                "No results yet. Complete the Run step (tab 5) to generate bill calculations."
            )
        else:
            st.warning(
                "Results were marked available but the DataFrame is missing. Please re-run."
            )
        return

    # ── Add human-readable scenario labels ───────────────────────────────────
    _config_key = f"{app_state.dataset_type}_{app_state.release}"
    _base_uri = BuildStockDataManager.DATASET_CONFIGS[_config_key].base_uri
    df = df.with_columns(
        pl.col("upgrade")
        .map_elements(
            lambda u: _scenario_display(_base_uri, u),
            return_dtype=pl.Utf8,
        )
        .alias("scenario")
    )

    # Rate-sweep frames carry one row per rate variant. Every pre-existing
    # section reports the primary assigned rate only; ``df_all`` keeps all
    # variants for the Rate Comparison section.
    df_all = df
    if "is_primary" in df.columns:
        df = df.filter(pl.col("is_primary"))

    # ── Rate eligibility screening (Phase 8) ──────────────────────────────────
    if "rate_eligible" in df.columns and not df["rate_eligible"].fill_null(True).all():
        _inelig = df.filter(~pl.col("rate_eligible").fill_null(True))
        if "weight" in df.columns:
            _w_inelig = _inelig["weight"].sum() or 0.0
            _w_total = df["weight"].sum() or 0.0
            _share = (_w_inelig / _w_total) if _w_total else 0.0
            st.warning(
                f"⚠ {_w_inelig:,.0f} of {_w_total:,.0f} weighted buildings "
                f"({_share:.1%}) fall outside the assigned rate's eligibility "
                "window (peak-kW / monthly-kWh limits declared by the rate)."
            )
        else:
            st.warning(
                f"⚠ {_inelig.height:,} of {df.height:,} building rows fall "
                "outside the assigned rate's eligibility window."
            )
        if st.toggle(
            "Exclude ineligible buildings from statistics",
            key="exclude_rate_ineligible",
        ):
            df = df.filter(pl.col("rate_eligible").fill_null(True))
            df_all = df_all.filter(pl.col("rate_eligible").fill_null(True))

    # ── Navigate-to-results banner ────────────────────────────────────────────
    if st.session_state.pop("_navigate_to_results", False):
        st.info("📂 Results loaded from project — displaying below.")

    from buildstock_rate_impact_calculator.billing.results import (
        MIN_SAMPLE_MODELS,
        add_cost_per_sqft,
        bill_source_options,
        compute_bill_savings,
        format_for_export,
        monthly_bills_long,
        savings_shares,
        segment_columns,
        summarize_bills,
        with_selected_bill,
    )
    from buildstock_rate_impact_calculator.ui.components.charts import (
        render_bill_comparison_bar,
        render_bill_distribution,
        render_bill_stack_bar,
        render_monthly_profile,
        render_savings_histogram,
        melt_fuels_to_long,
    )

    # Dataset type drives which aggregation columns the summary frame
    # carries (ResStock: mean/median/total_annual_bill + units; ComStock:
    # mean/median_per_sqft + total_annual_bill + total_sqft).
    ds_type = (app_state.dataset_type or "").lower()
    is_comstock = ds_type == "comstock"

    # Whole-building dollars get no decimals; ComStock $/ft² gets two.
    # Altair charts take D3 format strings; st.column_config.NumberColumn
    # takes printf-style strings (a D3 string would render literally).
    dollar_fmt = "$.0f"  # charts
    per_sqft_fmt = "$.2f"  # charts
    table_dollar_fmt = "$%.0f"  # tables
    table_per_sqft_fmt = "$%.2f"  # tables

    # ── Energy source selection ───────────────────────────────────────────────
    # The selected sources drive every statistic on the page: summary
    # mean/median/totals, the distribution, and bill savings.
    sources = bill_source_options(df)  # {display label: bill column}
    source_labels = list(sources)
    if len(source_labels) > 1:
        st.subheader("Energy Sources")
        chosen = st.pills(
            "Energy sources included in the bill statistics below",
            options=source_labels,
            selection_mode="multi",
            default=source_labels,
            key="bill_sources",
        )
        selected_labels = list(chosen) if chosen else source_labels
        if not chosen:
            st.caption("No sources selected — showing the total across all sources.")
        st.divider()
    else:
        selected_labels = source_labels
    bill_cols = [sources[label] for label in selected_labels]

    # Per-row bill for the selected sources (+ $/ft² for ComStock).
    df_sel = with_selected_bill(df, bill_cols)
    if is_comstock:
        df_sel = add_cost_per_sqft(df_sel, bill_col="selected_bill")

    # ── Summary table ─────────────────────────────────────────────────────────
    st.subheader("Bill Summary")
    bill_summary_view = st.radio(
        "Bill Summary view",
        ["Total bill", "By fuel", "By charge component"],
        horizontal=True,
        key="bill_summary_view",
    )
    summary = None
    try:
        summary = summarize_bills(df, dataset_type=ds_type, bill_columns=bill_cols)
        # Add scenario labels
        summary = summary.with_columns(
            pl.col("upgrade")
            .map_elements(
                lambda u: _scenario_display(_base_uri, u),
                return_dtype=pl.Utf8,
            )
            .alias("scenario")
        )

        if bill_summary_view == "Total bill":
            st.caption(f"Sources included: {', '.join(selected_labels)}")
            if is_comstock:
                # Weighted floor area is reported instead of building counts.
                display_cols = [
                    "scenario",
                    "upgrade",
                    "mean_per_sqft",
                    "median_per_sqft",
                    "total_annual_bill",
                    "total_sqft",
                ]
            else:
                display_cols = [
                    "scenario",
                    "upgrade",
                    "mean_annual_bill",
                    "median_annual_bill",
                    "total_annual_bill",
                    "units",
                ]
            display_cols = [c for c in display_cols if c in summary.columns]

            # Per-column number format: $/ft² gets 2 decimals; everything else
            # in whole-building dollars gets no decimals.
            per_sqft_cols = {"mean_per_sqft", "median_per_sqft"}
            non_dollar_cols = {"scenario", "upgrade", "units", "total_sqft"}
            column_config: dict[str, st.column_config.Column] = {
                "total_sqft": st.column_config.NumberColumn(
                    "total_sqft (ft²)", format="%d"
                ),
            }
            for col in display_cols:
                if col in per_sqft_cols:
                    column_config[col] = st.column_config.NumberColumn(
                        format=table_per_sqft_fmt
                    )
                elif col in non_dollar_cols:
                    continue
                else:
                    column_config[col] = st.column_config.NumberColumn(
                        format=table_dollar_fmt
                    )
            st.dataframe(
                summary.select(display_cols).to_pandas(),
                width="stretch",
                hide_index=True,
                column_config=column_config,
            )
        elif bill_summary_view == "By charge component":
            # Stacked bar of the PySAM electricity-bill decomposition
            # ($/ft² for ComStock, whole-building $ for ResStock).
            comp_label_map: dict[str, str] = {}
            for comp, comp_label in (
                ("annual_fixed_charge", "Fixed"),
                ("annual_energy_charge", "Energy"),
                ("annual_demand_charge", "Demand"),
                ("annual_min_charge", "Minimum"),
            ):
                summary_col = f"mean_{comp}_per_sqft" if is_comstock else f"mean_{comp}"
                comp_label_map[summary_col] = comp_label
            comp_fmt = per_sqft_fmt if is_comstock else dollar_fmt
            long_components = melt_fuels_to_long(summary, comp_label_map)
            if long_components.height == 0:
                st.info(
                    "These results were generated before charge components "
                    "were tracked — re-run the calculation to see the bill "
                    "composition."
                )
            else:
                comp_chart = render_bill_stack_bar(
                    long_components,
                    value_col="value",
                    scenario_col="scenario",
                    fuel_col="fuel_type",
                    legend_title="Charge Component",
                    value_format=comp_fmt,
                    y_axis_title=(
                        "Mean Electricity Bill ($/ft²)"
                        if is_comstock
                        else "Mean Electricity Bill ($)"
                    ),
                    chart_title="Mean Electricity Bill by Charge Component",
                )
                st.altair_chart(comp_chart, width="stretch")
            st.caption("Electricity bill only — fuel bills are not decomposed.")
        else:
            # "By fuel": stacked bar of per-source mean bills by scenario
            # ($/ft² for ComStock, whole-building $ for ResStock).
            fuel_label_map: dict[str, str] = {}
            for label in selected_labels:
                col = sources[label]
                src = "electricity_bill" if col == "annual_bill" else col
                summary_col = f"mean_{src}_per_sqft" if is_comstock else f"mean_{src}"
                fuel_label_map[summary_col] = label
            stack_fmt = per_sqft_fmt if is_comstock else dollar_fmt
            long_summary = melt_fuels_to_long(summary, fuel_label_map)
            if long_summary.height == 0:
                st.info(
                    "No per-fuel bill columns available — switch to "
                    "'Total bill' for the summary table."
                )
            else:
                stack_chart = render_bill_stack_bar(
                    long_summary,
                    value_col="value",
                    scenario_col="scenario",
                    fuel_col="fuel_type",
                    value_format=stack_fmt,
                    y_axis_title=(
                        "Mean Bill ($/ft²)" if is_comstock else "Mean Bill ($)"
                    ),
                    chart_title="Mean Annual Bill by Fuel",
                )
                st.altair_chart(stack_chart, width="stretch")
    except Exception as exc:
        st.error(f"Failed to summarize bills: {exc}")

    # ── Energy cost breakdown ─────────────────────────────────────────────────
    # Per-scenario mean bill for every available source (not just the selected
    # ones): $/ft² for ComStock, whole-building $ for ResStock.
    if summary is not None and len(source_labels) > 1:
        st.divider()
        st.subheader("Energy Cost Breakdown")
        try:
            breakdown_map: dict[str, str] = {}
            for label, col in sources.items():
                src = "electricity_bill" if col == "annual_bill" else col
                summary_col = f"mean_{src}_per_sqft" if is_comstock else f"mean_{src}"
                if summary_col in summary.columns:
                    breakdown_map[summary_col] = label
            total_col = (
                "mean_total_energy_bill_per_sqft"
                if is_comstock
                else "mean_total_energy_bill"
            )
            if total_col in summary.columns:
                breakdown_map[total_col] = "Total Energy"
            breakdown_fmt = table_per_sqft_fmt if is_comstock else table_dollar_fmt
            unit_note = "$/ft²" if is_comstock else "$ per building"
            st.caption(f"Mean annual bill by energy source ({unit_note}).")
            breakdown = summary.select(["scenario"] + list(breakdown_map)).rename(
                breakdown_map
            )
            st.dataframe(
                breakdown.to_pandas(),
                width="stretch",
                hide_index=True,
                column_config={
                    label: st.column_config.NumberColumn(format=breakdown_fmt)
                    for label in breakdown_map.values()
                },
            )
        except Exception as exc:
            st.error(f"Fuel breakdown error: {exc}")

    # ── Configured rates ──────────────────────────────────────────────────────
    st.divider()
    st.subheader("Configured Rates")
    try:
        _upgrade_ids = sorted(df["upgrade"].unique().to_list())
        rate_rows = _build_rates_summary_rows(
            app_state,
            upgrade_ids=_upgrade_ids,
            base_uri=_base_uri,
            result_columns=set(df.columns),
        )
        if rate_rows:
            st.caption(
                "Rates assigned on the Rates tab, with their charge components "
                "and the scenarios they apply to."
            )
            st.dataframe(
                pl.DataFrame(rate_rows).to_pandas(),
                width="stretch",
                hide_index=True,
            )
        else:
            st.caption("No rates are assigned to the scenarios in these results.")
    except Exception as exc:
        st.error(f"Rate summary error: {exc}")

    # ── Rate comparison (rate sweep) ──────────────────────────────────────────
    if "rate_name" in df_all.columns and df_all["rate_name"].n_unique() > 1:
        st.divider()
        st.subheader("Rate Comparison")
        rate_comp_view = st.radio(
            "Rate comparison view",
            ["Mean bill", "Mean savings"],
            horizontal=True,
            key="rate_comparison_view",
        )
        try:
            matrix_fmt = table_per_sqft_fmt if is_comstock else table_dollar_fmt
            if rate_comp_view == "Mean bill":
                comp_summary = summarize_bills(
                    df_all,
                    dataset_type=ds_type,
                    group_by=["rate_name"],
                    bill_columns=bill_cols,
                )
                comp_value = "mean_per_sqft" if is_comstock else "mean_annual_bill"
                comp_summary = comp_summary.with_columns(
                    pl.col("upgrade")
                    .map_elements(
                        lambda u: _scenario_display(_base_uri, u),
                        return_dtype=pl.Utf8,
                    )
                    .alias("scenario")
                )
                matrix = comp_summary.pivot(
                    values=comp_value, index="scenario", on="rate_name"
                )
                st.caption(
                    "Mean electricity bill per scenario under each rate "
                    + ("($/ft²)." if is_comstock else "($ per building).")
                )
            else:
                # Same-rate-baseline savings: baseline under rate R minus
                # upgrade under rate R (compute_bill_savings joins on
                # rate_name automatically).
                df_all_sel = with_selected_bill(df_all, bill_cols)
                if is_comstock:
                    df_all_sel = add_cost_per_sqft(df_all_sel, bill_col="selected_bill")
                diff_all = compute_bill_savings(
                    df_all_sel, baseline_upgrade=0, bill_col="selected_bill"
                )
                sweep_savings_col = (
                    "bill_savings_per_sqft"
                    if is_comstock and "bill_savings_per_sqft" in diff_all.columns
                    else "bill_savings"
                )
                comp_savings = (
                    diff_all.group_by(["rate_name", "upgrade"])
                    .agg(pl.col(sweep_savings_col).mean().alias("mean_savings"))
                    .with_columns(
                        pl.col("upgrade")
                        .map_elements(
                            lambda u: _scenario_display(_base_uri, u),
                            return_dtype=pl.Utf8,
                        )
                        .alias("scenario")
                    )
                )
                matrix = comp_savings.pivot(
                    values="mean_savings", index="scenario", on="rate_name"
                )
                st.caption(
                    "Mean savings vs the same-rate baseline — what each "
                    "measure saves under each rate design "
                    + ("($/ft²)." if is_comstock else "($ per building).")
                )
            rate_cols = [c for c in matrix.columns if c != "scenario"]
            st.dataframe(
                matrix.to_pandas(),
                width="stretch",
                hide_index=True,
                column_config={
                    c: st.column_config.NumberColumn(format=matrix_fmt)
                    for c in rate_cols
                },
            )
        except Exception as exc:
            st.error(f"Rate comparison error: {exc}")

    st.divider()

    # ── Bar chart ─────────────────────────────────────────────────────────────
    st.subheader("Mean Annual Bill by Scenario")
    if summary is not None:
        try:
            # ComStock means are reported on $/ft², ResStock on whole-building $.
            bill_col = "mean_per_sqft" if is_comstock else "mean_annual_bill"
            chart_fmt = per_sqft_fmt if is_comstock else dollar_fmt
            chart = render_bill_comparison_bar(
                summary,
                bill_col=bill_col,
                value_format=chart_fmt,
            )
            st.altair_chart(chart, width="stretch")
        except Exception as exc:
            st.error(f"Chart error: {exc}")

    st.divider()

    # ── Distribution chart ────────────────────────────────────────────────────
    st.subheader("Bill Distribution")
    try:
        if is_comstock:
            dist_chart = render_bill_distribution(
                df_sel,
                bill_col="cost_per_sqft",
                value_format=per_sqft_fmt,
                y_axis_title="Annual Bill ($/ft²)",
            )
        else:
            dist_chart = render_bill_distribution(
                df_sel,
                bill_col="selected_bill",
                y_axis_title="Annual Bill ($)",
            )
        st.altair_chart(dist_chart, width="stretch")
    except Exception as exc:
        st.error(f"Distribution chart error: {exc}")

    st.divider()

    # ── Monthly bill profile ──────────────────────────────────────────────────
    if "monthly_bills" in df.columns:
        st.subheader("Monthly Bill Profile")
        try:
            profile = monthly_bills_long(df, per_sqft=is_comstock)
            if profile.height == 0:
                st.caption("No monthly bill data available for these results.")
            else:
                profile_chart = render_monthly_profile(
                    profile,
                    value_col="value",
                    value_format=per_sqft_fmt if is_comstock else dollar_fmt,
                    y_axis_title=(
                        "Mean Monthly Bill ($/ft²)"
                        if is_comstock
                        else "Mean Monthly Bill ($)"
                    ),
                )
                st.altair_chart(profile_chart, width="stretch")
                st.caption(
                    "Electricity bill only — fuel bills are annual and are "
                    "not decomposed by month."
                )
        except Exception as exc:
            st.error(f"Monthly profile error: {exc}")
        st.divider()

    # ── Bill savings vs baseline ──────────────────────────────────────────────
    upgrades = sorted(df["upgrade"].unique().to_list())
    if len(upgrades) > 1 and 0 in upgrades:
        st.subheader("Bill Savings")
        savings_view = st.radio(
            "Show",
            ["Average", "Total"],
            horizontal=True,
            key="bill_savings_view",
        )
        try:
            diff_df = compute_bill_savings(
                df_sel, baseline_upgrade=0, bill_col="selected_bill"
            )
            grp_keys = (
                ["upgrade", "scenario"]
                if "scenario" in diff_df.columns
                else ["upgrade"]
            )

            # Pick the right savings column based on dataset + view.
            #   ResStock: bill_savings (whole-building $)
            #   ComStock: bill_savings_per_sqft when displaying $/ft²; otherwise
            #            bill_savings (weighted $) for totals.
            use_per_sqft = (
                is_comstock
                and savings_view == "Average"
                and "bill_savings_per_sqft" in diff_df.columns
            )
            savings_col = "bill_savings_per_sqft" if use_per_sqft else "bill_savings"
            display_fmt = per_sqft_fmt if use_per_sqft else dollar_fmt  # charts
            table_display_fmt = table_per_sqft_fmt if use_per_sqft else table_dollar_fmt

            # Winners/losers metrics row: weighted share of buildings whose
            # dollar bill goes down / up by more than $1 under each upgrade.
            try:
                shares = savings_shares(diff_df, savings_col="bill_savings")
                share_lines = []
                for share_row in shares.sort("upgrade").iter_rows(named=True):
                    label = _scenario_display(_base_uri, share_row["upgrade"])
                    if share_row["pct_saving"] is None:
                        continue
                    share_lines.append(
                        f"**{label}**: {share_row['pct_saving']:.0%} of buildings "
                        f"save · {share_row['pct_increasing']:.0%} increase · "
                        f"{share_row['pct_neutral']:.0%} within ±$1"
                    )
                if share_lines:
                    st.markdown("  \n".join(share_lines))
            except Exception as exc:
                st.warning(f"Winners/losers summary error: {exc}")

            if savings_view == "Average":
                value_label = (
                    "mean_bill_savings_per_sqft"
                    if use_per_sqft
                    else "mean_bill_savings"
                )
                diff_summary = (
                    diff_df.group_by(grp_keys)
                    .agg(
                        [
                            pl.col(savings_col).mean().alias(value_label),
                            pl.col(savings_col)
                            .median()
                            .alias(
                                "median_bill_savings_per_sqft"
                                if use_per_sqft
                                else "median_bill_savings"
                            ),
                            # Percent-of-baseline savings, scaled to 0–100 for
                            # the printf "%.1f%%" column format.
                            (pl.col("bill_savings_pct").mean() * 100).alias(
                                "mean_bill_savings_pct"
                            ),
                            (pl.col("bill_savings_pct").median() * 100).alias(
                                "median_bill_savings_pct"
                            ),
                            pl.col("bldg_id").count().alias("count"),
                        ]
                    )
                    .sort("upgrade")
                )
            else:
                # Total view: weighted total annual savings (sum across
                # buildings). ComStock bills are already weighted, so a direct
                # sum is the dataset total; ResStock savings are per-building
                # dollars, so multiply by the scalar row weight.
                if not is_comstock and "weight" in diff_df.columns:
                    total_expr = (
                        (pl.col(savings_col) * pl.col("weight"))
                        .sum()
                        .alias("total_bill_savings")
                    )
                else:
                    total_expr = pl.col(savings_col).sum().alias("total_bill_savings")
                value_label = "total_bill_savings"
                diff_summary = (
                    diff_df.group_by(grp_keys)
                    .agg([total_expr, pl.col("bldg_id").count().alias("count")])
                    .sort("upgrade")
                )

            # Render with $ formatting on the savings column.
            sav_column_config: dict[str, st.column_config.Column] = {
                value_label: st.column_config.NumberColumn(format=table_display_fmt),
            }
            if savings_view == "Average":
                med_label = (
                    "median_bill_savings_per_sqft"
                    if use_per_sqft
                    else "median_bill_savings"
                )
                sav_column_config[med_label] = st.column_config.NumberColumn(
                    format=table_display_fmt
                )
                for pct_label in ("mean_bill_savings_pct", "median_bill_savings_pct"):
                    sav_column_config[pct_label] = st.column_config.NumberColumn(
                        format="%.1f%%"
                    )
            st.dataframe(
                diff_summary.to_pandas(),
                width="stretch",
                hide_index=True,
                column_config=sav_column_config,
            )

            # Savings bar chart
            diff_pandas = diff_summary.to_pandas()
            diff_pandas["upgrade"] = diff_pandas["upgrade"].astype(str)
            import altair as alt

            y_title = (
                "Mean Bill Savings ($/ft²)"
                if use_per_sqft
                else (
                    "Total Bill Savings ($)"
                    if savings_view == "Total"
                    else "Mean Bill Savings ($)"
                )
            )
            diff_chart = (
                alt.Chart(diff_pandas)
                .mark_bar()
                .encode(
                    x=alt.X("upgrade:N", title="Upgrade Scenario"),
                    y=alt.Y(
                        f"{value_label}:Q",
                        title=y_title,
                        axis=alt.Axis(format=display_fmt),
                    ),
                    # Positive savings (baseline > upgrade) → green.
                    color=alt.condition(
                        alt.datum[value_label] > 0,
                        alt.value("#4CAF50"),
                        alt.value("#F44336"),
                    ),
                    tooltip=[
                        alt.Tooltip("upgrade:N", title="Upgrade"),
                        alt.Tooltip(
                            f"{value_label}:Q",
                            title=y_title,
                            format=display_fmt,
                        ),
                        alt.Tooltip("count:Q", title="Buildings"),
                    ],
                )
                .properties(
                    title="Annual Bill Savings vs Baseline",
                    width=500,
                    height=300,
                )
                .interactive()
            )
            st.altair_chart(diff_chart, width="stretch")

            # Savings distribution histogram — the spread behind the means.
            hist_use_per_sqft = (
                is_comstock and "bill_savings_per_sqft" in diff_df.columns
            )
            hist_col = "bill_savings_per_sqft" if hist_use_per_sqft else "bill_savings"
            hist_chart = render_savings_histogram(
                diff_df,
                savings_col=hist_col,
                value_format=per_sqft_fmt if hist_use_per_sqft else dollar_fmt,
                x_axis_title=(
                    "Annual Bill Savings ($/ft²)"
                    if hist_use_per_sqft
                    else "Annual Bill Savings ($)"
                ),
            )
            st.altair_chart(hist_chart, width="stretch")

            # Segment breakout: savings grouped by a categorical building
            # characteristic (Phase 5). Uses the same view (Average/Total)
            # expressions as the headline table above.
            segments = segment_columns(diff_df)
            if segments:
                seg_choice = st.selectbox(
                    "Break out savings by",
                    ["None", *segments],
                    key="savings_segment",
                )
                if seg_choice != "None":
                    seg_keys = [seg_choice, *grp_keys]
                    if savings_view == "Average":
                        seg_aggs = [
                            pl.col(savings_col).mean().alias(value_label),
                            pl.col("bldg_id").count().alias("count"),
                        ]
                    else:
                        seg_aggs = [
                            total_expr,
                            pl.col("bldg_id").count().alias("count"),
                        ]
                    seg_summary = (
                        diff_df.group_by(seg_keys)
                        .agg(seg_aggs)
                        .sort([seg_choice, "upgrade"])
                    )
                    min_count = seg_summary["count"].min()
                    if min_count is not None and min_count < MIN_SAMPLE_MODELS:
                        st.caption(
                            f"⚠ Some segments have fewer than "
                            f"{MIN_SAMPLE_MODELS} building models — their "
                            "statistics may be unreliable."
                        )
                    st.dataframe(
                        seg_summary.to_pandas(),
                        width="stretch",
                        hide_index=True,
                        column_config={
                            value_label: st.column_config.NumberColumn(
                                format=table_display_fmt
                            ),
                        },
                    )
                    seg_chart = render_bill_comparison_bar(
                        seg_summary,
                        group_col=seg_choice,
                        bill_col=value_label,
                        value_format=display_fmt,
                        y_axis_title=y_title,
                        chart_title="Bill Savings by Segment",
                    )
                    st.altair_chart(seg_chart, width="stretch")
        except Exception as exc:
            st.error(f"Bill savings error: {exc}")

    # ── Peak demand ───────────────────────────────────────────────────────────
    # Rendered only when the run produced peak columns (skipped silently for
    # older result frames).
    peak_col = "mean_peak_w_per_sqft" if is_comstock else "mean_peak_demand_kw"
    if summary is not None and peak_col in summary.columns:
        st.divider()
        st.subheader("Peak Demand")
        try:
            peak_label = (
                "Mean Peak Demand (W/ft²)" if is_comstock else "Mean Peak Demand (kW)"
            )
            peak_df = summary.select(["scenario", "upgrade", peak_col]).sort("upgrade")
            if 0 in upgrades:
                _base_peak = peak_df.filter(pl.col("upgrade") == 0)[peak_col][0]
                if _base_peak is not None:
                    peak_df = peak_df.with_columns(
                        (pl.col(peak_col) - _base_peak).alias("delta_vs_baseline")
                    )
            st.caption(
                "Electricity peak demand from PySAM monthly peaks — "
                + ("per-building W/ft²." if is_comstock else "kW per dwelling.")
            )
            st.dataframe(
                peak_df.to_pandas(),
                width="stretch",
                hide_index=True,
                column_config={
                    peak_col: st.column_config.NumberColumn(peak_label, format="%.2f"),
                    "delta_vs_baseline": st.column_config.NumberColumn(
                        "Δ vs Baseline", format="%.2f"
                    ),
                },
            )
        except Exception as exc:
            st.error(f"Peak demand error: {exc}")

    # ── Energy burden (ResStock only) ─────────────────────────────────────────
    _income_col = BuildStockDataManager.DATASET_CONFIGS[_config_key].income_column
    if not is_comstock and _income_col and _income_col in df.columns:
        st.divider()
        st.subheader("Energy Burden")
        try:
            from buildstock_rate_impact_calculator.billing.energy_burden import (
                HIGH_BURDEN,
                SEVERE_BURDEN,
                add_energy_burden,
                summarize_energy_burden,
            )

            burden_df = add_energy_burden(df_sel, _income_col, bill_col="selected_bill")
            burden_summary = summarize_energy_burden(burden_df)
            pct_cols = ["mean_burden", "median_burden", "pct_high", "pct_severe"]
            # Scale to 0–100 for the printf "%.1f%%" column format.
            burden_display = burden_summary.with_columns(
                [(pl.col(c) * 100).alias(c) for c in pct_cols]
            )
            if 0 in upgrades:
                _base_rows = burden_display.filter(pl.col("upgrade") == 0)
                if _base_rows.height > 0:
                    _base_burden = _base_rows["mean_burden"][0]
                    if _base_burden is not None:
                        burden_display = burden_display.with_columns(
                            (pl.col("mean_burden") - _base_burden).alias(
                                "delta_mean_burden"
                            )
                        )
            display_cols = [
                c
                for c in (
                    "scenario",
                    "upgrade",
                    "mean_burden",
                    "median_burden",
                    "pct_high",
                    "pct_severe",
                    "delta_mean_burden",
                    "count",
                )
                if c in burden_display.columns
            ]
            burden_column_config = {
                "mean_burden": st.column_config.NumberColumn(
                    "Mean Burden", format="%.1f%%"
                ),
                "median_burden": st.column_config.NumberColumn(
                    "Median Burden", format="%.1f%%"
                ),
                "pct_high": st.column_config.NumberColumn(
                    f"High (≥{HIGH_BURDEN:.0%})", format="%.1f%%"
                ),
                "pct_severe": st.column_config.NumberColumn(
                    f"Severe (≥{SEVERE_BURDEN:.0%})", format="%.1f%%"
                ),
                "delta_mean_burden": st.column_config.NumberColumn(
                    "Δ Mean vs Baseline", format="%.1f%%"
                ),
            }
            st.dataframe(
                burden_display.select(display_cols).to_pandas(),
                width="stretch",
                hide_index=True,
                column_config=burden_column_config,
            )
            st.caption(
                "Energy burden = selected-source bills ÷ household income, "
                "with income estimated as the midpoint of each "
                f"`{_income_col}` bin (open-ended bins use half the bound "
                "or 1.25× the bound). High/severe thresholds of "
                f"{HIGH_BURDEN:.0%} / {SEVERE_BURDEN:.0%} follow DOE LEAD "
                "conventions."
            )
        except Exception as exc:
            st.error(f"Energy burden error: {exc}")

    # ── Upgrade vs Baseline Comparison ────────────────────────────────────────
    if "applicable" in df.columns and len(upgrades) > 1 and 0 in upgrades:
        st.divider()
        st.subheader("Upgrade vs Baseline Comparison")
        st.caption(
            "Compares each upgrade against the baseline. "
            "Unapplicable buildings use their baseline load profile with the upgrade rate."
        )
        import altair as alt

        # Join on _row_uid too when present: ComStock full metadata can carry
        # the same bldg_id on multiple rows (one per geographic instance).
        comp_join_keys = ["bldg_id"]
        if "_row_uid" in df_sel.columns:
            comp_join_keys.append("_row_uid")
        baseline_sel = [pl.col("selected_bill").alias("baseline_bill")]
        if is_comstock and "cost_per_sqft" in df_sel.columns:
            baseline_sel.append(pl.col("cost_per_sqft").alias("baseline_cost_per_sqft"))
        baseline_bills = df_sel.filter(pl.col("upgrade") == 0).select(
            comp_join_keys + baseline_sel
        )

        # ComStock savings are reported per weighted ft²; ResStock in $.
        comp_per_sqft = is_comstock and "cost_per_sqft" in df_sel.columns
        savings_value_col = (
            "annual_savings_per_sqft" if comp_per_sqft else "annual_savings"
        )
        savings_unit = "$/ft²" if comp_per_sqft else "$"
        comp_fmt = per_sqft_fmt if comp_per_sqft else dollar_fmt

        non_baseline_upgrades = [u for u in upgrades if u != 0]
        for _uid in non_baseline_upgrades:
            _label = _scenario_display(_base_uri, _uid)
            with st.expander(_label, expanded=(_uid == non_baseline_upgrades[0])):
                _upg_df = df_sel.filter(pl.col("upgrade") == _uid)
                _show = st.radio(
                    "Show buildings:",
                    ["All filtered buildings", "Applicable buildings only"],
                    key=f"comp_mode_{_uid}",
                    horizontal=True,
                )
                if _show == "Applicable buildings only":
                    _upg_df = _upg_df.filter(pl.col("applicable"))
                _savings_exprs = [
                    (pl.col("baseline_bill") - pl.col("selected_bill")).alias(
                        "annual_savings"
                    )
                ]
                if comp_per_sqft:
                    _savings_exprs.append(
                        (
                            pl.col("baseline_cost_per_sqft") - pl.col("cost_per_sqft")
                        ).alias("annual_savings_per_sqft")
                    )
                _merged = _upg_df.join(
                    baseline_bills, on=comp_join_keys, how="left"
                ).with_columns(_savings_exprs)
                _n_total = len(_upg_df)
                _n_app = (
                    int(_upg_df["applicable"].sum())
                    if "applicable" in _upg_df.columns
                    else _n_total
                )
                _mean_sav = _merged[savings_value_col].mean()
                _m1, _m2, _m3 = st.columns(3)
                with _m1:
                    st.metric("Buildings", f"{_n_total:,}")
                with _m2:
                    st.metric("Applicable", f"{_n_app:,}")
                with _m3:
                    if _mean_sav is not None:
                        _sign = "+" if _mean_sav > 0 else ""
                        _val = (
                            f"{_sign}${_mean_sav:,.2f}/ft²"
                            if comp_per_sqft
                            else f"{_sign}${_mean_sav:,.0f}"
                        )
                        st.metric("Mean Savings", _val)

                # Summary statistics table
                try:
                    _stats = _merged.select(
                        [
                            pl.col(savings_value_col)
                            .mean()
                            .alias(f"Mean ({savings_unit})"),
                            pl.col(savings_value_col)
                            .median()
                            .alias(f"Median ({savings_unit})"),
                            pl.col(savings_value_col)
                            .std()
                            .alias(f"Std Dev ({savings_unit})"),
                            pl.col(savings_value_col)
                            .min()
                            .alias(f"Min ({savings_unit})"),
                            pl.col(savings_value_col)
                            .max()
                            .alias(f"Max ({savings_unit})"),
                        ]
                    )
                    st.dataframe(
                        _stats.to_pandas().style.format("${:,.2f}"),
                        hide_index=True,
                        width="stretch",
                    )
                except Exception as exc:
                    st.warning(f"Stats error: {exc}")

                # Box/whisker savings chart, split by applicability
                try:
                    _plot = _merged.filter(pl.col(savings_value_col).is_not_null())
                    if "applicable" in _plot.columns:
                        _plot = _plot.with_columns(
                            pl.when(pl.col("applicable"))
                            .then(pl.lit("Applicable"))
                            .otherwise(pl.lit("Not Applicable"))
                            .alias("Group")
                        )
                        _color = alt.Color(
                            "Group:N",
                            scale=alt.Scale(
                                domain=["Applicable", "Not Applicable"],
                                range=["#2196F3", "#FF9800"],
                            ),
                        )
                        _x_enc = alt.X("Group:N", title=None)
                    else:
                        _plot = _plot.with_columns(pl.lit("All").alias("Group"))
                        _color = alt.value("#2196F3")
                        _x_enc = alt.X("Group:N", title=None)
                    _chart = (
                        alt.Chart(_plot.to_pandas())
                        .mark_boxplot(extent="min-max", size=60)
                        .encode(
                            x=_x_enc,
                            y=alt.Y(
                                f"{savings_value_col}:Q",
                                title=f"Annual Savings ({savings_unit})",
                                axis=alt.Axis(format=comp_fmt),
                            ),
                            color=_color,
                            tooltip=[
                                alt.Tooltip(
                                    f"{savings_value_col}:Q",
                                    title=f"Savings ({savings_unit})",
                                    format=comp_fmt,
                                )
                            ],
                        )
                        .properties(
                            title="Annual Bill Savings vs Baseline",
                            width=350,
                            height=320,
                        )
                        .interactive()
                    )
                    st.altair_chart(_chart, width="stretch")
                except Exception as exc:
                    st.warning(f"Chart error: {exc}")
    st.divider()

    # ── Export ────────────────────────────────────────────────────────────────
    st.subheader("Export")
    col1, col2 = st.columns(2)

    with col1:
        try:
            csv_bytes = format_for_export(df)
            st.download_button(
                label="⬇ Download Results CSV",
                data=csv_bytes,
                file_name="buildstock_bill_results.csv",
                mime="text/csv",
            )
        except Exception as exc:
            st.error(f"Export error: {exc}")

    with col2:
        if st.button("💾 Save to Output Directory"):
            try:
                out_path = app_state.output_dir / "bill_results.csv"
                df.write_csv(out_path)

                # Also save rate JSONs used
                rates_dir = app_state.output_dir / "rates"
                rates_dir.mkdir(parents=True, exist_ok=True)
                import json

                for scenario_key, rate_dict in app_state.rate_assignments.items():
                    rate_file = rates_dir / f"{scenario_key}_rate.json"
                    with open(rate_file, "w") as f:
                        json.dump(rate_dict, f, indent=2, default=str)

                st.success(
                    f"Saved to {app_state.output_dir}:\n"
                    f"- bill_results.csv ({len(df):,} rows)\n"
                    f"- rates/ ({len(app_state.rate_assignments)} rate JSON files)"
                )
            except Exception as exc:
                st.error(f"Save failed: {exc}")

    # ── Metrics ───────────────────────────────────────────────────────────────
    st.divider()
    col1, col2, col3 = st.columns(3)
    with col1:
        if is_comstock and {"weight", "sqft"}.issubset(df.columns):
            # Weighted floor area represented, reported instead of a building
            # count (one scenario's rows; all scenarios cover the same stock).
            _ref_upgrade = 0 if 0 in upgrades else upgrades[0]
            _floor_area = (
                df.filter(pl.col("upgrade") == _ref_upgrade)
                .select((pl.col("weight") * pl.col("sqft")).sum())
                .item()
            )
            st.metric("Total Floor Area", f"{(_floor_area or 0):,.0f} ft²")
        else:
            st.metric("Total Buildings", df["bldg_id"].n_unique())
    with col2:
        st.metric("Scenarios", df["upgrade"].n_unique())
    with col3:
        error_count = df["error"].is_not_null().sum() if "error" in df.columns else 0
        st.metric("Calculation Errors", error_count)
