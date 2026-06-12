"""
Page 5: Run — Download & Calculate

Shows a configuration summary, pre-download size estimate, and runs the
download + bill calculation pipeline in a background thread with live
progress feedback. Supports cancellation.
"""
from __future__ import annotations
import os
import threading

import streamlit as st
import polars as pl

from buildstock_rate_impact_calculator.ui.state import AppState
from buildstock_rate_impact_calculator.ui.project_io import save_project as _save_project
from buildstock_rate_impact_calculator.ui.components.progress_tracker import ProgressTracker
from buildstock_rate_impact_calculator.ui.applicability import (
    render_applicability_table as _render_applicability_table,
)


def _urdb_key_required(rate_assignments: dict, rate_library: dict) -> bool:
    for rate_name in rate_assignments.values():
        if not rate_name:
            continue
        rate_config = rate_library.get(rate_name, {})
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
        tracker.update(0, message=f"Loading fuel/default bill data for upgrade {upgrade_id}…")
        # Geo-resolution-aware load with column projection: only the needed
        # columns are downloaded, and ComStock county/PUMA selections read the
        # per-geometry aggregate files. Missing columns are skipped per file.
        upgrade_meta = mgr.load_upgrade_metadata(upgrade_id, columns=needed_cols)

        # Filter to selected buildings
        bldg_col = mgr.config.building_id_column
        upgrade_meta = upgrade_meta.filter(pl.col(bldg_col).is_in(building_ids)).unique(
            subset=[bldg_col]
        )

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
    resolved_rate_assignments: dict,
    resolved_fuel_rate_assignments: dict,
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

        # ── build manager with the active geographic selection ───────────────
        mgr = BuildStockDataManager(
            dataset_type=app_state.dataset_type,
            release=app_state.release,
            cache_enabled=True,
            cache_dir=output_dir,
        )
        _sel = app_state.effective_geo_selection()
        mgr.set_geo_selection(
            states=_sel["states"],
            county_ids=_sel["county_ids"],
            puma_ids=_sel["puma_ids"],
        )

        # ── load metadata (geo-appropriate files, needed columns only) ───────
        tracker.start_phase("Loading building metadata")
        metadata = mgr.load_metadata()
        metadata = app_state.apply_geo_filter(metadata)
        if app_state.active_filters:
            _filters = {
                k: v for k, v in app_state.active_filters.items()
                if k in metadata.columns
            }
            metadata = mgr.filter_metadata(metadata, _filters)

        building_ids = list(dict.fromkeys(mgr.get_building_ids(metadata)))
        tracker.log(f"Metadata loaded — {len(building_ids):,} buildings match the current filters.")

        if tracker.is_cancelled():
            tracker.done(message="Cancelled before download.")
            return

        # ── fetch upgrade applicability ───────────────────────────────────────
        _non_baseline = [u for u in app_state.selected_upgrades if u != 0]
        upgrade_applicable_ids: dict[int, list] = {0: list(building_ids)}
        if _non_baseline:
            tracker.start_phase("Checking upgrade applicability", total=len(_non_baseline))
            for _upg_id in _non_baseline:
                _applicability = mgr.get_upgrade_applicability(_upg_id, building_ids)
                upgrade_applicable_ids[_upg_id] = [
                    bid for bid, v in _applicability.items() if v
                ]
                tracker.advance(1)
                tracker.log(
                    f"Upgrade {_upg_id}: "
                    f"{len(upgrade_applicable_ids[_upg_id]):,}/{len(building_ids):,} applicable."
                )

        # ── download timeseries (or reload from cache) ───────────────────────
        ts_dir = output_dir / "timeseries" / app_state.release

        # Build (building_id, upgrade) pairs to download.
        # Each upgrade only downloads timeseries for applicable buildings.
        # Unapplicable buildings fall back to baseline files during bill calculation,
        # so baseline files are included for those buildings even if upgrade 0
        # is not among the selected scenarios.
        all_pairs: set[tuple] = set()
        for _upg in app_state.selected_upgrades:
            _applicable_bids = set(upgrade_applicable_ids.get(_upg, building_ids))
            if _upg == 0:
                all_pairs |= {(bldg, 0) for bldg in building_ids}
            else:
                all_pairs |= {(bldg, _upg) for bldg in _applicable_bids}
                # Unapplicable buildings need the baseline file for fall-back
                _unapplicable_bids = set(building_ids) - _applicable_bids
                all_pairs |= {(bldg, 0) for bldg in _unapplicable_bids}
        cached_pairs = {
            pair for pair in all_pairs
            if (ts_dir / f"{pair[0]}_{pair[1]}.parquet").exists()
        }
        missing_pairs = all_pairs - cached_pairs

        ts_data: dict[str, pl.DataFrame] = {}

        # Load already-downloaded files from disk
        if cached_pairs:
            n_cached = len(cached_pairs)
            tracker.start_phase(
                f"Loading {n_cached:,} cached timeseries files from disk", total=n_cached
            )
            cached_bldgs_by_upg: dict[int, list] = {}
            for bldg, upg in cached_pairs:
                cached_bldgs_by_upg.setdefault(upg, []).append(bldg)
            for upg, bldgs in cached_bldgs_by_upg.items():
                ts_data.update(
                    mgr.load_cached_timeseries(
                        building_ids=bldgs,
                        upgrades=[upg],
                        output_dir=ts_dir,
                    )
                )
                tracker.advance(len(bldgs), message=f"Loaded cached files for upgrade {upg}")
            tracker.log(f"Loaded {n_cached:,} cached timeseries files from disk.")

        # Download only the missing files
        if missing_pairs:
            n_missing = len(missing_pairs)
            tracker.start_phase(
                f"Downloading {n_missing:,} timeseries files from OEDI", total=n_missing
            )
            missing_bldgs_by_upg: dict[int, list] = {}
            for bldg, upg in missing_pairs:
                missing_bldgs_by_upg.setdefault(upg, []).append(bldg)

            def dl_progress(done, total, msg=""):
                tracker.advance(1, message=msg)

            for upg, bldgs in missing_bldgs_by_upg.items():
                if tracker.is_cancelled():
                    break
                ts_data.update(
                    mgr.download_timeseries_with_progress(
                        building_ids=bldgs,
                        upgrades=[upg],
                        output_dir=ts_dir,
                        progress_cb=dl_progress,
                        cancel_event=tracker.cancel_event,
                    )
                )
            if not tracker.is_cancelled():
                tracker.log(f"Downloaded {n_missing:,} timeseries files.")

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
            rate_config = resolved_rate_assignments.get(scenario_key)
            if rate_config is None:
                tracker.log(f"No electricity rate assigned for {scenario_key}, skipping.")
                continue

            applicable_set = set(upgrade_applicable_ids.get(upgrade_id, building_ids))
            load_records = []
            for bldg_id in building_ids:
                # Applicable buildings use their upgrade timeseries;
                # unapplicable buildings fall back to baseline timeseries.
                is_applicable = (upgrade_id == 0) or (bldg_id in applicable_set)
                ts_key = f"{bldg_id}_{upgrade_id}" if is_applicable else f"{bldg_id}_0"
                df = ts_data.get(ts_key)
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
                tracker.log(f"No load profiles available for upgrade {upgrade_id}, skipping.")
                continue

            tracker.start_phase(
                f"Calculating electricity bills — upgrade {upgrade_id}",
                total=len(load_records),
            )
            try:
                calc = UtilityBillCalculator()
                result_df = calc.calculate_batch_bills(
                    load_records,
                    rate_config,
                    progress_cb=lambda done, total: tracker.advance(
                        1, message=f"Calculated {done:,}/{total:,} bills"
                    ),
                )
                # Tag each building with its applicability status for this upgrade
                result_df = result_df.with_columns(
                    pl.col("bldg_id").is_in(list(applicable_set)).alias("applicable")
                )
                all_results.append(result_df)
            except Exception as exc:
                tracker.log(f"Electricity billing error for upgrade {upgrade_id}: {exc}")

        if not all_results:
            tracker.error("No electricity bill results were calculated.")
            return

        combined = pl.concat(all_results)

        # ── calculate fuel bills ──────────────────────────────────────────────
        fuel_frames: list[pl.DataFrame] = []

        tracker.start_phase(
            "Calculating fuel bills", total=len(app_state.selected_upgrades)
        )
        for upgrade_id in app_state.selected_upgrades:
            scenario_key = _scenario_key(upgrade_id)
            fuel_rates_for_scenario = resolved_fuel_rate_assignments.get(scenario_key, {})

            fuel_df = _calculate_fuel_bills(
                mgr=mgr,
                building_ids=building_ids,
                upgrade_id=upgrade_id,
                fuel_rate_assignments_for_scenario=fuel_rates_for_scenario,
                tracker=tracker,
            )
            if fuel_df is not None:
                fuel_frames.append(fuel_df)
            tracker.advance(1)

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

        tracker.start_phase("Saving results")
        result_store["df"] = combined
        out_csv = app_state.project_dir / "results" / "bill_results.csv"
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        combined.write_csv(out_csv)
        tracker.done(message=f"Done! Results saved to {out_csv}")

    except Exception as exc:
        tracker.error(f"Pipeline failed: {exc}")


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _render_progress_panel() -> None:
    """Render the live progress panel from the tracker snapshot.

    Reads runtime state from st.session_state (not closures) so it stays
    correct across fragment auto-reruns.
    """
    tracker: ProgressTracker | None = st.session_state.get("run_tracker")
    thread: threading.Thread | None = st.session_state.get("run_thread")
    if tracker is None:
        return

    snap = tracker.snapshot()
    running = thread is not None and thread.is_alive()

    # ── Status banner + cancel control ────────────────────────────────────────
    if running:
        if snap.cancel_requested:
            st.warning("Cancelling — waiting for in-flight downloads to stop…")
        else:
            banner_col, cancel_col = st.columns([5, 1])
            with banner_col:
                st.info(f"Running — elapsed {_fmt_duration(snap.elapsed_seconds)}")
            with cancel_col:
                if st.button("⏹ Cancel", type="secondary", key="run_cancel_btn"):
                    tracker.cancel()
    elif snap.is_error:
        st.error(snap.error_message)
    elif snap.is_done:
        st.success(f"{snap.message}  (total time {_fmt_duration(snap.elapsed_seconds)})")
    elif snap.phases:
        # Thread ended without done()/error() — should not happen, but surface it
        st.error("The background run stopped unexpectedly. Check the activity log below.")

    # ── Phase list ────────────────────────────────────────────────────────────
    for phase in snap.phases:
        if phase.done:
            count = f" — {phase.completed:,}" if phase.completed else ""
            if phase.total and phase.completed:
                count = f" — {phase.completed:,}/{phase.total:,}"
            st.markdown(f"✅ {phase.name}{count}")
        else:
            label = phase.name
            if phase.total:
                label += f" — {phase.completed:,}/{phase.total:,}"
                eta = phase.eta_seconds
                if eta is not None and eta > 1:
                    label += f" · ~{_fmt_duration(eta)} remaining"
                st.progress(phase.fraction or 0.0, text=f"▶ {label}")
            else:
                st.markdown(f"▶ {label} …")
            if snap.message and snap.message != phase.name:
                st.caption(snap.message)

    # ── Activity log ──────────────────────────────────────────────────────────
    if snap.log:
        with st.expander("Activity log", expanded=False):
            st.code("\n".join(snap.log), language=None)


def render_run_page(app_state: AppState) -> None:
    """Render the Run page with configuration summary, progress, and cancel."""
    st.header("Run")

    if not app_state.dataset_type or not app_state.release:
        st.warning("Please select a dataset on the Dataset tab first.")
        return

    if not app_state.selected_upgrades:
        st.warning("Please select one or more upgrade scenarios on the Dataset tab first.")
        return

    # ── Configuration summary ─────────────────────────────────────────────────
    st.subheader("Configuration Summary")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Dataset", f"{app_state.dataset_type} {app_state.release}")
    with col2:
        st.metric("Scenarios", len(app_state.selected_upgrades))

    # Geo and filter detail expanders
    col_geo, col_flt = st.columns(2)
    with col_geo:
        with st.expander("Geographic Selections", expanded=False):
            if app_state.selected_states:
                st.markdown(f"**States:** {', '.join(sorted(app_state.selected_states))}")
            if app_state.selected_county_ids:
                st.markdown(f"**Counties:** {len(app_state.selected_county_ids)} selected")
            if app_state.selected_puma_ids:
                st.markdown(f"**PUMAs:** {len(app_state.selected_puma_ids)} selected")
            if not (
                app_state.selected_states
                or app_state.selected_county_ids
                or app_state.selected_puma_ids
                or app_state.selected_geo_ids
            ):
                st.caption("No geographic filter — all states included.")
    with col_flt:
        with st.expander("Building Filters", expanded=False):
            if app_state.active_filters:
                for _fcol, _fval in app_state.active_filters.items():
                    _fdisp = (
                        ", ".join(str(v) for v in _fval)
                        if isinstance(_fval, list)
                        else str(_fval)
                    )
                    st.markdown(f"**{_fcol}:** {_fdisp}")
            else:
                st.caption("No building attribute filters applied.")

    # ── Upgrade applicability summary (cached; reflects geo + filters) ───────
    _render_applicability_table(
        app_state,
        title="Upgrade Applicability Summary",
        show_refresh=True,
        key_prefix="run_",
    )

    assigned = len(app_state.rate_assignments)
    total_scenarios = len(app_state.selected_upgrades)
    if assigned < total_scenarios:
        st.warning(
            f"Only {assigned}/{total_scenarios} scenarios have rates assigned. "
            "Go to the Rates tab to assign rates before running."
        )

    st.divider()

    # ── Runtime state in session ─────────────────────────────────────────────
    st.session_state.setdefault("run_tracker", None)
    st.session_state.setdefault("run_thread", None)
    st.session_state.setdefault("run_results", {})

    thread: threading.Thread | None = st.session_state["run_thread"]
    result_store: dict = st.session_state["run_results"]

    is_running = thread is not None and thread.is_alive()

    # ── API key pre-flight check ──────────────────────────────────────────────
    needs_urdb = _urdb_key_required(app_state.rate_assignments, app_state.rate_library)
    api_key_available = bool(
        app_state.urdb_api_key or os.getenv("OPENEI_API_KEY")
    )
    if needs_urdb and not api_key_available:
        st.error(
            "An OpenEI API key is required: one or more rate assignments need to "
            "fetch rate data from URDB during the run. Enter your API key in the "
            "sidebar before starting."
        )

    # ── Start button ──────────────────────────────────────────────────────────
    start_blocked = assigned == 0 or (needs_urdb and not api_key_available)
    if not is_running:
        if st.button("▶ Start Download & Calculate", type="primary", disabled=start_blocked):
            new_tracker = ProgressTracker()
            new_results: dict = {}
            # Resolve rate names → full dicts before handing off to background thread
            _rate_lib = app_state.rate_library
            _fuel_lib = app_state.fuel_rate_library
            _resolved_rates = {
                sk: _rate_lib[name]
                for sk, name in app_state.rate_assignments.items()
                if name and name in _rate_lib
            }
            _resolved_fuel = {
                sk: {
                    ft: _fuel_lib.get(name) if name else None
                    for ft, name in ft_map.items()
                }
                for sk, ft_map in app_state.fuel_rate_assignments.items()
            }
            new_thread = threading.Thread(
                target=_run_pipeline,
                args=(app_state, new_tracker, new_results, _resolved_rates, _resolved_fuel),
                daemon=True,
            )
            st.session_state["run_tracker"] = new_tracker
            st.session_state["run_thread"] = new_thread
            st.session_state["run_results"] = new_results
            if app_state.project_name:
                _save_project(app_state)
            new_thread.start()
            st.rerun()

    # ── Live progress display ─────────────────────────────────────────────────
    # The panel runs as an auto-refreshing fragment: while the pipeline is
    # running, only this panel re-renders (every second) instead of the whole
    # app, which keeps the page responsive during downloads/calculations.
    if is_running:
        st.session_state["_run_finish_pending"] = True

    @st.fragment(run_every=1.0 if is_running else None)
    def _progress_fragment() -> None:
        t = st.session_state.get("run_thread")
        still_running = t is not None and t.is_alive()
        _render_progress_panel()
        # One full-app refresh when the run transitions to finished, so the
        # results section renders and the fragment stops polling.
        if not still_running and st.session_state.get("_run_finish_pending"):
            st.session_state["_run_finish_pending"] = False
            st.rerun(scope="app")

    _progress_fragment()

    # ── Results available ─────────────────────────────────────────────────────
    if not is_running and result_store.get("df") is not None:
        app_state.results_available = True
        df = result_store["df"]
        st.divider()
        st.success(f"Calculation complete — {len(df):,} building-scenario results.")
        st.dataframe(df.head(20).to_pandas(), width='stretch')
        st.info("Go to the **Results** tab for full visualization and export.")
