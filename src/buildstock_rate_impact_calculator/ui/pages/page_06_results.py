"""
Page 6: Results Visualization & Export

Displays:
  - Summary table: mean/median/total bill per scenario (with optional grouping)
  - Bill Summary view toggle: total-bill table vs by-fuel stacked bar
  - Grouped bar chart: mean annual bill by scenario and group
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

    # ── Navigate-to-results banner ────────────────────────────────────────────
    if st.session_state.pop("_navigate_to_results", False):
        st.info("📂 Results loaded from project — displaying below.")

    # ── Group-by selector ─────────────────────────────────────────────────────
    st.subheader("Grouping")
    from buildstock_rate_impact_calculator.billing.results import _META_COLUMNS

    available_groups = [c for c in df.columns if c not in _META_COLUMNS]
    group_options = ["None"] + available_groups
    chosen_group = st.selectbox(
        "Group results by",
        options=group_options,
        index=0,
        help="Choose a column to break down results (e.g. building_type, state).",
    )
    group_col = None if chosen_group == "None" else chosen_group

    from buildstock_rate_impact_calculator.billing.results import (
        summarize_bills,
        compute_bill_savings,
        format_for_export,
    )
    from buildstock_rate_impact_calculator.ui.components.charts import (
        render_bill_comparison_bar,
        render_bill_distribution,
        render_bill_stack_bar,
        melt_fuels_to_long,
    )

    # Dataset type drives which aggregation columns the summary frame
    # carries (ResStock: mean/median/total_annual_bill + units; ComStock:
    # mean/median_per_sqft + total_annual_bill + total_sqft + units).
    ds_type = (app_state.dataset_type or "").lower()
    is_comstock = ds_type == "comstock"

    # Whole-building dollars use $.0f; ComStock $/ft² uses $.2f.
    dollar_fmt = "$.0f"
    per_sqft_fmt = "$.2f"

    st.divider()

    # ── Summary table ─────────────────────────────────────────────────────────
    st.subheader("Bill Summary")
    bill_summary_view = st.radio(
        "Bill Summary view",
        ["Total bill", "By fuel"],
        horizontal=True,
        key="bill_summary_view",
    )
    summary = None
    try:
        summary = summarize_bills(df, dataset_type=ds_type, group_by=group_col)
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
            display_cols = ["scenario", "upgrade"]
            if group_col:
                display_cols = [group_col] + display_cols
            if is_comstock:
                display_cols += [
                    "mean_per_sqft",
                    "median_per_sqft",
                    "total_annual_bill",
                    "total_sqft",
                    "units",
                ]
            else:
                display_cols += [
                    "mean_annual_bill",
                    "median_annual_bill",
                    "total_annual_bill",
                    "units",
                ]
            # Include fuel/total bill summaries if present
            for extra in (
                "mean_natural_gas_bill",
                "mean_propane_bill",
                "mean_fuel_oil_bill",
                "mean_total_energy_bill",
                "total_total_energy_bill",
            ):
                if extra in summary.columns:
                    display_cols.append(extra)
            display_cols = [c for c in display_cols if c in summary.columns]

            # Per-column number format: $/ft² gets 2 decimals; everything else
            # in whole-building dollars gets no decimals.
            per_sqft_cols = {"mean_per_sqft", "median_per_sqft"}
            non_dollar_cols = {"scenario", "upgrade", "units", "total_sqft"}
            if group_col:
                non_dollar_cols.add(group_col)
            column_config: dict[str, st.column_config.Column] = {}
            for col in display_cols:
                if col in per_sqft_cols:
                    column_config[col] = st.column_config.NumberColumn(
                        format=per_sqft_fmt
                    )
                elif col in non_dollar_cols:
                    continue
                else:
                    column_config[col] = st.column_config.NumberColumn(
                        format=dollar_fmt
                    )
            st.dataframe(
                summary.select(display_cols).to_pandas(),
                width="stretch",
                hide_index=True,
                column_config=column_config,
            )
        else:
            # "By fuel": stacked bar of mean per-fuel bills by scenario.
            fuel_label_map = {
                "mean_annual_bill": "Electricity",
                "mean_natural_gas_bill": "Natural Gas",
                "mean_propane_bill": "Propane",
                "mean_fuel_oil_bill": "Fuel Oil",
            }
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
                    group_col=group_col,
                    scenario_col="scenario",
                    fuel_col="fuel_type",
                    value_format=dollar_fmt,
                    chart_title="Mean Annual Bill by Fuel",
                )
                st.altair_chart(stack_chart, width="stretch")
    except Exception as exc:
        st.error(f"Failed to summarize bills: {exc}")

    # ── Fuel cost breakdown ───────────────────────────────────────────────────
    fuel_bill_cols = [
        c
        for c in df.columns
        if c
        in ("natural_gas_bill", "propane_bill", "fuel_oil_bill", "total_energy_bill")
    ]
    if fuel_bill_cols:
        st.divider()
        st.subheader("Energy Cost Breakdown")
        fuel_labels = {
            "annual_bill": "Electricity",
            "natural_gas_bill": "Natural Gas",
            "propane_bill": "Propane",
            "fuel_oil_bill": "Fuel Oil",
            "total_energy_bill": "Total Energy",
        }
        breakdown_cols = ["annual_bill"] + fuel_bill_cols
        breakdown_present = [c for c in breakdown_cols if c in df.columns]
        try:
            # Per-scenario mean breakdown table
            grp_keys = (
                ["upgrade", "scenario"] if "scenario" in df.columns else ["upgrade"]
            )
            agg = (
                df.group_by(grp_keys)
                .agg(
                    [pl.col(c).mean().alias(fuel_labels[c]) for c in breakdown_present]
                )
                .sort("upgrade")
            )
            st.dataframe(agg.to_pandas(), width="stretch", hide_index=True)

            # Rate names applied (if available)
            rate_name_cols = [c for c in df.columns if c.endswith("_rate_name")]
            if rate_name_cols:
                st.caption("Rates applied to fuel bills:")
                sample = (
                    df.select(["upgrade"] + rate_name_cols)
                    .unique(subset=["upgrade"])
                    .sort("upgrade")
                )
                st.dataframe(sample.to_pandas(), hide_index=True)
        except Exception as exc:
            st.error(f"Fuel breakdown error: {exc}")

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
                group_col=group_col,
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
        dist_chart = render_bill_distribution(df, group_col=group_col)
        st.altair_chart(dist_chart, width="stretch")
    except Exception as exc:
        st.error(f"Distribution chart error: {exc}")

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
            diff_df = compute_bill_savings(df, baseline_upgrade=0)
            grp_keys = (
                ["upgrade", "scenario"]
                if "scenario" in diff_df.columns
                else ["upgrade"]
            )

            # Pick the right savings column based on dataset + view.
            #   ResStock: bill_savings (whole-building $)
            #   ComStock: bill_savings_per_sqft when displaying $/ft²; otherwise
            #            bill_savings (whole-building $) for totals.
            use_per_sqft = (
                is_comstock
                and savings_view == "Average"
                and "bill_savings_per_sqft" in diff_df.columns
            )
            savings_col = "bill_savings_per_sqft" if use_per_sqft else "bill_savings"
            display_fmt = per_sqft_fmt if use_per_sqft else dollar_fmt

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
                            pl.col("bldg_id").count().alias("count"),
                        ]
                    )
                    .sort("upgrade")
                )
            else:
                # Total view: weighted total annual savings (sum across
                # buildings). ComStock annual_bill is already weighted, so a
                # direct sum is the dataset total; ResStock requires the
                # row weight (already baked into bill_savings? No — savings
                # is baseline-upgrade in per-building dollars for ResStock).
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
                value_label: st.column_config.NumberColumn(format=display_fmt),
            }
            if savings_view == "Average":
                med_label = (
                    "median_bill_savings_per_sqft"
                    if use_per_sqft
                    else "median_bill_savings"
                )
                sav_column_config[med_label] = st.column_config.NumberColumn(
                    format=display_fmt
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
        except Exception as exc:
            st.error(f"Bill savings error: {exc}")
    # ── Upgrade vs Baseline Comparison ────────────────────────────────────────
    if "applicable" in df.columns and len(upgrades) > 1 and 0 in upgrades:
        st.divider()
        st.subheader("Upgrade vs Baseline Comparison")
        st.caption(
            "Compares each upgrade against the baseline. "
            "Unapplicable buildings use their baseline load profile with the upgrade rate."
        )
        import altair as alt

        baseline_bills = df.filter(pl.col("upgrade") == 0).select(
            ["bldg_id", pl.col("annual_bill").alias("baseline_bill")]
        )
        non_baseline_upgrades = [u for u in upgrades if u != 0]
        for _uid in non_baseline_upgrades:
            _label = _scenario_display(_base_uri, _uid)
            with st.expander(_label, expanded=(_uid == non_baseline_upgrades[0])):
                _upg_df = df.filter(pl.col("upgrade") == _uid)
                _show = st.radio(
                    "Show buildings:",
                    ["All filtered buildings", "Applicable buildings only"],
                    key=f"comp_mode_{_uid}",
                    horizontal=True,
                )
                if _show == "Applicable buildings only":
                    _upg_df = _upg_df.filter(pl.col("applicable"))
                _merged = _upg_df.join(
                    baseline_bills, on="bldg_id", how="left"
                ).with_columns(
                    (pl.col("baseline_bill") - pl.col("annual_bill")).alias(
                        "annual_savings"
                    )
                )
                _n_total = len(_upg_df)
                _n_app = (
                    int(_upg_df["applicable"].sum())
                    if "applicable" in _upg_df.columns
                    else _n_total
                )
                _mean_sav = _merged["annual_savings"].mean()
                _m1, _m2, _m3 = st.columns(3)
                with _m1:
                    st.metric("Buildings", f"{_n_total:,}")
                with _m2:
                    st.metric("Applicable", f"{_n_app:,}")
                with _m3:
                    if _mean_sav is not None:
                        _sign = "+" if _mean_sav > 0 else ""
                        st.metric("Mean Savings", f"{_sign}${_mean_sav:,.0f}")

                # Summary statistics table
                try:
                    _stats = _merged.select(
                        [
                            pl.col("annual_savings").mean().alias("Mean ($)"),
                            pl.col("annual_savings").median().alias("Median ($)"),
                            pl.col("annual_savings").std().alias("Std Dev ($)"),
                            pl.col("annual_savings").min().alias("Min ($)"),
                            pl.col("annual_savings").max().alias("Max ($)"),
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
                    _plot = _merged.filter(pl.col("annual_savings").is_not_null())
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
                            y=alt.Y("annual_savings:Q", title="Annual Savings ($)"),
                            color=_color,
                            tooltip=[
                                alt.Tooltip(
                                    "annual_savings:Q",
                                    title="Savings ($)",
                                    format="$.0f",
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
        st.metric("Total Buildings", df["bldg_id"].n_unique())
    with col2:
        st.metric("Scenarios", df["upgrade"].n_unique())
    with col3:
        error_count = df["error"].is_not_null().sum() if "error" in df.columns else 0
        st.metric("Calculation Errors", error_count)
