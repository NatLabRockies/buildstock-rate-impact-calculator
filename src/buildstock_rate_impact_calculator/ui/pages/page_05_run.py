"""
Page 5: Run — Download & Calculate

Shows a configuration summary, pre-download size estimate, and runs the
download + bill calculation pipeline in a background thread with live
progress feedback. Supports cancellation.
"""
from __future__ import annotations
import os
import threading
import time
import json
from pathlib import Path

import streamlit as st
import polars as pl

from buildstock_rate_impact_calculator.ui.state import AppState
from buildstock_rate_impact_calculator.ui.components.progress_tracker import ProgressTracker
from buildstock_rate_impact_calculator.data.upgrade_labels import get_upgrade_label


def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024
    return f"{n:.1f} PB"


def _urdb_key_required(rate_assignments: dict) -> bool:
    for rate_config in rate_assignments.values():
        if not isinstance(rate_config, dict):
            continue
        if "label" in rate_config and "energyratestructure" not in rate_config:
            return True
    return False


def _scenario_key(upgrade_id: int) -> str:
    return "baseline" if upgrade_id == 0 else f"upgrade_{upgrade_id}"


def _calculate_fuel_bills(
    mgr,
    building_ids: list,
    upgrade_id: int,
    fuel_rate_assignments_for_scenario: dict,
    tracker: ProgressTracker,
) -> pl.DataFrame | None:
    """Load upgrade metadata and calculate fuel bills for all buildings.

    Returns a Polars DataFrame with columns:
        bldg_id, upgrade,
        natural_gas_bill, natural_gas_rate_name,
        propane_bill,      propane_rate_name,
        fuel_oil_bill,     fuel_oil_rate_name
    or None if no fuel data was available.
    """
    from buildstock_rate_impact_calculator.rates.fuel_rate import (
        FUEL_TYPES, calculate_fuel_bill,
    )

    fuel_type_columns: list[tuple[str, str, str | None, str | None]] = []
    # (fuel_type, mode, column_name, rate_dict_or_None)
    for ft in FUEL_TYPES:
        energy_col, bill_col = mgr.get_fuel_columns_for_type(ft)
        rate_dict = fuel_rate_assignments_for_scenario.get(ft)  # None = dataset default
        if rate_dict is not None and energy_col:
            fuel_type_columns.append((ft, "rate", energy_col, rate_dict))
        elif rate_dict is None and bill_col:
            fuel_type_columns.append((ft, "default", bill_col, None))
        # If neither column exists for this fuel type, skip silently

    if not fuel_type_columns:
        return None

    needed_cols = [mgr.config.building_id_column] + [col for _, _, col, _ in fuel_type_columns]

    try:
        upgrade_meta_uri = mgr.get_metadata_uri_for_upgrade(upgrade_id)
        tracker.update(0, message=f"Loading fuel/default bill data for upgrade {upgrade_id}…")

        if mgr.config.metadata_structure == "parquet":
            # Load only needed columns if possible
            import polars as pl
            try:
                available = list(pl.scan_parquet(upgrade_meta_uri).collect_schema().keys())
                cols_to_load = [c for c in needed_cols if c in available]
                upgrade_meta = pl.scan_parquet(upgrade_meta_uri).select(cols_to_load).collect()
            except Exception:
                upgrade_meta = pl.read_parquet(upgrade_meta_uri)
        else:
            # For non-parquet (e.g. ComStock csv.gz), load full metadata — expensive but necessary
            upgrade_meta = mgr.load_metadata(force_reload=False)  # use cached baseline as fallback

        # Filter to selected buildings
        bldg_col = mgr.config.building_id_column
        upgrade_meta = upgrade_meta.filter(pl.col(bldg_col).is_in(building_ids))

    except Exception as exc:
        tracker.update(0, message=f"Warning: Could not load fuel metadata for upgrade {upgrade_id}: {exc}")
        return None

    # Build result rows
    rows: list[dict] = []
    for row in upgrade_meta.iter_rows(named=True):
        bldg_id = row[mgr.config.building_id_column]
        result_row: dict = {"bldg_id": bldg_id, "upgrade": upgrade_id}

        for ft, mode, col_name, rate_dict in fuel_type_columns:
            if col_name not in row:
                result_row[f"{ft}_bill"] = None
                result_row[f"{ft}_rate_name"] = None
                continue

            raw_val = row.get(col_name)
            value = float(raw_val) if raw_val is not None else 0.0

            if mode == "rate":
                bill_result = calculate_fuel_bill(value, rate_dict)
                result_row[f"{ft}_bill"] = bill_result["annual_fuel_bill"]
                result_row[f"{ft}_rate_name"] = bill_result["rate_name"]
            else:
                # Dataset default — raw value is already an annual bill in USD
                result_row[f"{ft}_bill"] = value
                result_row[f"{ft}_rate_name"] = "dataset default"

        rows.append(result_row)

    if not rows:
        return None

    return pl.DataFrame(rows)


def _run_pipeline(
    app_state: AppState,
    tracker: ProgressTracker,
    result_store: dict,
) -> None:
    """
    Background thread: download timeseries then calculate bills.
    Writes results into result_store["df"] on completion.
    """
    try:
        from buildstock_rate_impact_calculator.data.buildstock_manager import BuildStockDataManager
        from buildstock_rate_impact_calculator.billing.calculator import UtilityBillCalculator
        import numpy as np

        output_dir = app_state.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        # ── build manager ────────────────────────────────────────────────────
        mgr = BuildStockDataManager(
            dataset_type=app_state.dataset_type,
            release=app_state.release,
            cache_enabled=True,
            cache_dir=output_dir / "cache",
        )

        # ── load metadata to get state mapping ───────────────────────────────
        tracker.update(0, message="Loading building metadata…")
        metadata = mgr.load_metadata()
        metadata = app_state.apply_geo_filter(metadata)
        if app_state.active_filters:
            metadata = mgr.filter_metadata(metadata, app_state.active_filters)

        building_ids = mgr.get_building_ids(metadata)

        if tracker.is_cancelled():
            tracker.done(message="Cancelled before download.")
            return

        # ── download timeseries (or reload from cache) ───────────────────────
        total_downloads = len(building_ids) * len(app_state.selected_upgrades)
        ts_dir = output_dir / "timeseries"

        cached_keys = {
            f"{bldg}_{upg}"
            for bldg in building_ids
            for upg in app_state.selected_upgrades
            if (ts_dir / f"{bldg}_{upg}.parquet").exists()
        }
        all_cached = len(cached_keys) == total_downloads and total_downloads > 0

        if all_cached:
            tracker.update(0, message="All timeseries files already downloaded — loading from disk…")
            ts_data = mgr.load_cached_timeseries(
                building_ids=building_ids,
                upgrades=app_state.selected_upgrades,
                output_dir=ts_dir,
            )
            tracker.update(total_downloads, message=f"Loaded {len(ts_data)} cached files.")
        else:
            tracker._total = total_downloads

            def dl_progress(done, total, msg=""):
                tracker.update(completed=1, message=msg)

            ts_data = mgr.download_timeseries_with_progress(
                building_ids=building_ids,
                upgrades=app_state.selected_upgrades,
                output_dir=ts_dir,
                progress_cb=dl_progress,
                cancel_event=tracker.cancel_event,
            )

        if tracker.is_cancelled():
            tracker.done(message="Download cancelled.")
            return

        if not ts_data:
            tracker.error("No timeseries data was downloaded.")
            return

        # ── calculate electricity bills ───────────────────────────────────────
        from buildstock_rate_impact_calculator.billing.load_profile_utils import HOURS_PER_YEAR

        load_column = mgr.config.default_load_column
        all_results = []

        for upgrade_id in app_state.selected_upgrades:
            scenario_key = _scenario_key(upgrade_id)
            rate_config = app_state.rate_assignments.get(scenario_key)
            if rate_config is None:
                tracker.update(0, message=f"No electricity rate assigned for {scenario_key}, skipping.")
                continue

            tracker.update(0, message=f"Calculating electricity bills for upgrade {upgrade_id}…")
            tracker._total += len(building_ids)

            load_records = []
            for bldg_id in building_ids:
                key = f"{bldg_id}_{upgrade_id}"
                df = ts_data.get(key)
                if df is None:
                    continue
                if load_column not in df.columns:
                    continue
                values = df[load_column].to_numpy()
                if len(values) == HOURS_PER_YEAR * 4:
                    values = values.reshape(-1, 4).mean(axis=1)
                elif len(values) != HOURS_PER_YEAR:
                    continue
                load_records.append((bldg_id, upgrade_id, values))

            if not load_records:
                continue

            try:
                calc = UtilityBillCalculator()
                result_df = calc.calculate_batch_bills(
                    load_records,
                    rate_config,
                    progress_cb=lambda done, total: tracker.update(
                        completed=1, message=f"Calculated {done}/{total}"
                    ),
                )
                all_results.append(result_df)
            except Exception as exc:
                tracker.update(0, message=f"Electricity billing error for upgrade {upgrade_id}: {exc}")

        if not all_results:
            tracker.error("No electricity bill results were calculated.")
            return

        combined = pl.concat(all_results)

        # ── calculate fuel bills ──────────────────────────────────────────────
        fuel_rate_assignments = getattr(app_state, "fuel_rate_assignments", {})
        fuel_frames: list[pl.DataFrame] = []

        for upgrade_id in app_state.selected_upgrades:
            scenario_key = _scenario_key(upgrade_id)
            fuel_rates_for_scenario = fuel_rate_assignments.get(scenario_key, {})

            fuel_df = _calculate_fuel_bills(
                mgr=mgr,
                building_ids=building_ids,
                upgrade_id=upgrade_id,
                fuel_rate_assignments_for_scenario=fuel_rates_for_scenario,
                tracker=tracker,
            )
            if fuel_df is not None:
                fuel_frames.append(fuel_df)

        if fuel_frames:
            all_fuel = pl.concat(fuel_frames)
            # Left-join fuel data onto electricity results
            combined = combined.join(all_fuel, on=["bldg_id", "upgrade"], how="left")

        # ── compute total energy bill ─────────────────────────────────────────
        fuel_bill_cols = [c for c in combined.columns if c.endswith("_bill") and c != "annual_bill"]
        if fuel_bill_cols:
            total_expr = pl.col("annual_bill").fill_null(0.0)
            for fc in fuel_bill_cols:
                total_expr = total_expr + pl.col(fc).fill_null(0.0)
            combined = combined.with_columns(total_expr.alias("total_energy_bill"))

        result_store["df"] = combined
        out_csv = output_dir / "bill_results.csv"
        combined.write_csv(out_csv)
        tracker.done(message=f"Done! Results saved to {out_csv}")

    except Exception as exc:
        tracker.error(f"Pipeline failed: {exc}")


def render_run_page(app_state: AppState) -> None:
    """Render the Run page with configuration summary, progress, and cancel."""
    st.header("Run")

    # ── Configuration summary ─────────────────────────────────────────────────
    st.subheader("Configuration Summary")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Dataset", f"{app_state.dataset_type} {app_state.release}")
    with col2:
        st.metric("Scenarios", len(app_state.selected_upgrades))
    with col3:
        st.metric("Est. Download", _format_bytes(app_state.estimated_download_bytes))

    assigned = len(app_state.rate_assignments)
    total_scenarios = len(app_state.selected_upgrades)
    if assigned < total_scenarios:
        st.warning(
            f"Only {assigned}/{total_scenarios} scenarios have rates assigned. "
            "Go to the Rates tab to assign rates before running."
        )

    has_geo = bool(
        app_state.selected_states
        or app_state.selected_county_ids
        or app_state.selected_puma_ids
        or app_state.selected_geo_ids
    )
    if not has_geo and not app_state.active_filters:
        st.info("No geographic or building filters set — all buildings in dataset will be included.")

    st.divider()

    # ── Runtime state in session ─────────────────────────────────────────────
    if "run_tracker" not in st.session_state:
        st.session_state["run_tracker"] = None
    if "run_thread" not in st.session_state:
        st.session_state["run_thread"] = None
    if "run_results" not in st.session_state:
        st.session_state["run_results"] = {}

    tracker: ProgressTracker | None = st.session_state["run_tracker"]
    thread: threading.Thread | None = st.session_state["run_thread"]
    result_store: dict = st.session_state["run_results"]

    is_running = thread is not None and thread.is_alive()
    is_done = tracker is not None and tracker.is_done()

    # ── API key pre-flight check ──────────────────────────────────────────────
    needs_urdb = _urdb_key_required(app_state.rate_assignments)
    api_key_available = bool(
        app_state.urdb_api_key or os.getenv("OPENEI_API_KEY")
    )
    if needs_urdb and not api_key_available:
        st.error(
            "An OpenEI API key is required: one or more rate assignments need to "
            "fetch rate data from URDB during the run. Enter your API key in the "
            "sidebar before starting."
        )

    # ── Action buttons ────────────────────────────────────────────────────────
    start_blocked = assigned == 0 or (needs_urdb and not api_key_available)
    if not is_running:
        if st.button("▶ Start Download & Calculate", type="primary", disabled=start_blocked):
            new_tracker = ProgressTracker(total=1)
            new_results: dict = {}
            new_thread = threading.Thread(
                target=_run_pipeline,
                args=(app_state, new_tracker, new_results),
                daemon=True,
            )
            st.session_state["run_tracker"] = new_tracker
            st.session_state["run_thread"] = new_thread
            st.session_state["run_results"] = new_results
            new_thread.start()
            st.rerun()
    else:
        if st.button("⏹ Cancel", type="secondary"):
            tracker.cancel()

    # ── Live progress display ─────────────────────────────────────────────────
    if tracker is not None:
        updates = tracker.drain()
        latest = updates[-1] if updates else None

        frac = min(tracker.completed / max(tracker.total, 1), 1.0)
        st.progress(frac)

        if latest:
            if latest.is_error:
                st.error(latest.error_message)
            elif latest.is_done:
                st.success(latest.message)
            else:
                st.info(f"{latest.message} ({latest.completed}/{latest.total})")

        if is_running:
            time.sleep(0.5)
            st.rerun()

    # ── Results available ─────────────────────────────────────────────────────
    if "df" in result_store and result_store["df"] is not None:
        app_state.results_available = True
        df = result_store["df"]
        st.success(f"Calculation complete — {len(df):,} building-scenario results.")
        st.dataframe(df.head(20).to_pandas(), width='stretch')
        st.info("Go to the **Results** tab for full visualization and export.")
