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
    """
    Return True if any assigned rate config is a URDB stub (has a 'label' but
    lacks 'energyratestructure'), meaning the full rate data would need to be
    fetched from URDB during the calculation pipeline.
    """
    for rate_config in rate_assignments.values():
        if not isinstance(rate_config, dict):
            continue
        if "label" in rate_config and "energyratestructure" not in rate_config:
            return True
    return False


def _scenario_key(upgrade_id: int) -> str:
    return "baseline" if upgrade_id == 0 else f"upgrade_{upgrade_id}"


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

        # Check how many files are already on disk
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
            tracker._total = total_downloads  # Update total now that we know it

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

        # ── calculate bills ───────────────────────────────────────────────────
        from buildstock_rate_impact_calculator.billing.load_profile_utils import HOURS_PER_YEAR

        load_column = mgr.config.default_load_column
        all_results = []

        for upgrade_id in app_state.selected_upgrades:
            scenario_key = _scenario_key(upgrade_id)
            rate_config = app_state.rate_assignments.get(scenario_key)
            if rate_config is None:
                tracker.update(0, message=f"No rate assigned for {scenario_key}, skipping.")
                continue

            tracker.update(0, message=f"Calculating bills for upgrade {upgrade_id}…")
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
                # Convert 15-min to hourly if needed
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
                tracker.update(0, message=f"Billing error for upgrade {upgrade_id}: {exc}")

        if all_results:
            combined = pl.concat(all_results)
            result_store["df"] = combined
            # Save to disk
            out_csv = output_dir / "bill_results.csv"
            combined.write_csv(out_csv)
            tracker.done(message=f"Done! Results saved to {out_csv}")
        else:
            tracker.error("No bill results were calculated.")

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
