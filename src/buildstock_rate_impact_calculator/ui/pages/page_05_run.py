"""
Page 5: Run — Download & Calculate

Shows a configuration summary, pre-download size estimate, and runs the
download + bill calculation pipeline in a background thread with live
progress feedback. Supports cancellation.
"""

from __future__ import annotations
import os
import threading
from typing import Any

import streamlit as st
import polars as pl

from buildstock_rate_impact_calculator.ui.state import AppState
from buildstock_rate_impact_calculator.ui import auto_exit, steps
from buildstock_rate_impact_calculator.ui.project_io import (
    save_project as _save_project,
)
from buildstock_rate_impact_calculator.ui.components.progress_tracker import (
    ProgressTracker,
)
from buildstock_rate_impact_calculator.ui.applicability import (
    render_applicability_table as _render_applicability_table,
)
from buildstock_rate_impact_calculator.billing.results import (
    MIN_SAMPLE_MODELS as _MIN_SAMPLE_MODELS,
)


def _urdb_key_required(
    rate_assignments: dict,
    rate_library: dict,
    comparison_rates: list[str] | None = None,
) -> bool:
    rate_names = list(rate_assignments.values()) + list(comparison_rates or [])
    for rate_name in rate_names:
        if not rate_name:
            continue
        rate_config = rate_library.get(rate_name, {})
        if "label" in rate_config and "energyratestructure" not in rate_config:
            return True
    return False


def _scenario_key(upgrade_id: int) -> str:
    return "baseline" if upgrade_id == 0 else f"upgrade_{upgrade_id}"


# ---------------------------------------------------------------------------
# Per-row metadata helpers (factored out for unit testing)
# ---------------------------------------------------------------------------


def _assign_row_uid(
    metadata: pl.DataFrame, dataset_type: str, bldg_col: str
) -> pl.DataFrame:
    """Add a ``_row_uid`` column that uniquely identifies each metadata row.

    For ComStock the same ``bldg_id`` can appear in multiple geographic
    aggregates (county / PUMA) with different ``weight`` values, and the same
    ``(bldg_id, county, puma, weight)`` combination can recur across rows.
    The uid therefore concatenates ``bldg_id``, ``in.county``, ``in.puma``,
    ``weight`` AND the original row index so it is guaranteed unique per
    metadata row.

    For ResStock each row corresponds to a unique ``bldg_id``, so the uid is
    just the ``bldg_id`` as a string.
    """
    bldg_expr = pl.col(bldg_col).cast(pl.Utf8)
    if dataset_type == "comstock":
        county_expr = (
            pl.col("in.county").cast(pl.Utf8).fill_null("")
            if "in.county" in metadata.columns
            else pl.lit("")
        )
        puma_expr = (
            pl.col("in.puma").cast(pl.Utf8).fill_null("")
            if "in.puma" in metadata.columns
            else pl.lit("")
        )
        weight_expr = (
            pl.col("weight").cast(pl.Utf8).fill_null("")
            if "weight" in metadata.columns
            else pl.lit("")
        )
        metadata = metadata.with_row_index("__row_idx")
        uid_expr = pl.concat_str(
            [
                bldg_expr,
                county_expr,
                puma_expr,
                weight_expr,
                pl.col("__row_idx").cast(pl.Utf8),
            ],
            separator="|",
        )
        return metadata.with_columns(uid_expr.alias("_row_uid")).drop("__row_idx")
    return metadata.with_columns(bldg_expr.alias("_row_uid"))


def _build_load_records(
    metadata: pl.DataFrame,
    ts_data: dict[str, pl.DataFrame],
    dataset_type: str,
    load_column: str,
    scalar_weight: float | None,
    upgrade_id: int,
    applicable_set: set,
    bldg_col: str,
) -> list[dict]:
    """Build per-row records for ``UtilityBillCalculator.calculate_batch_bills``.

    For **ComStock**: one record per metadata row. The 8760 load array is
    multiplied by the row's ``weight`` so the resulting bill is real-world
    weighted dollars. ``sqft`` and ``_row_uid`` are carried through.

    For **ResStock**: one record per unique ``bldg_id``. Load arrays are NOT
    scaled — the scalar dataset weight is applied at aggregation time. The
    ``weight`` column on the record is set to ``scalar_weight`` so per-row
    aggregations stay consistent across datasets.
    """
    from buildstock_rate_impact_calculator.billing.load_profile_utils import (
        HOURS_PER_YEAR,
    )

    if dataset_type == "comstock":
        rows = metadata.iter_rows(named=True)
    else:
        # Dedup by bldg_id for ResStock so PySAM runs once per dwelling type.
        rows = metadata.unique(subset=[bldg_col], keep="first").iter_rows(named=True)

    records: list[dict] = []
    for row in rows:
        bldg_id = row[bldg_col]
        is_applicable = (upgrade_id == 0) or (bldg_id in applicable_set)
        ts_upgrade = upgrade_id if is_applicable else 0
        df = ts_data.get(f"{bldg_id}_{ts_upgrade}")
        if df is None or load_column not in df.columns:
            continue
        values = df[load_column].to_numpy()
        if len(values) == HOURS_PER_YEAR * 4:
            values = values.reshape(-1, 4).mean(axis=1)
        elif len(values) != HOURS_PER_YEAR:
            continue

        if dataset_type == "comstock":
            weight = float(row.get("weight") or 0.0)
            sqft = row.get("in.sqft..ft2")
            sqft_val = float(sqft) if sqft is not None else None
            load_array = values * weight
        else:
            weight = float(scalar_weight) if scalar_weight is not None else 1.0
            # Preview sampling re-scales the scalar weight per row so
            # weighted totals stay unbiased (1.0 outside preview runs).
            weight *= float(row.get("_sample_scale") or 1.0)
            sqft_val = None
            load_array = values

        records.append(
            {
                "bldg_id": bldg_id,
                "upgrade": upgrade_id,
                "load_array": load_array,
                "weight": weight,
                "sqft": sqft_val,
                "_row_uid": row.get("_row_uid"),
            }
        )
    return records


def _bill_upgrade_under_rates(
    load_records: list,
    variants: list[tuple[str, dict, bool]],
    tracker: ProgressTracker | None = None,
    is_comstock: bool = False,
) -> pl.DataFrame:
    """Bill one upgrade's load records under each rate variant.

    Args:
        load_records: Records for ``UtilityBillCalculator.calculate_batch_bills``.
        variants: ``(rate_name, rate_config, is_primary)`` tuples — the primary
            assigned rate first, then any comparison (rate-sweep) rates.
        tracker: Optional progress tracker; advanced once per (record, variant)
            so the active phase's total should be
            ``len(load_records) × len(variants)``.
        is_comstock: Whether rows carry weighted quantities (used to normalize
            per-building values for the rate-eligibility screen).

    Returns:
        Concatenated results with ``rate_name``, ``is_primary``, and
        ``rate_eligible`` columns stamped on every row (single-variant runs
        included, so the schema is stable whether or not a sweep is
        configured).
    """
    from buildstock_rate_impact_calculator.billing.calculator import (
        UtilityBillCalculator,
    )
    from buildstock_rate_impact_calculator.rates.eligibility import (
        flag_rate_eligibility,
        rate_eligibility_bounds,
    )

    progress_cb = None
    if tracker is not None:

        def progress_cb(done: int, total: int) -> None:
            tracker.advance(1, message=f"Calculated {done:,}/{total:,} bills")

    frames: list[pl.DataFrame] = []
    for rate_name, rate_config, is_primary in variants:
        calc = UtilityBillCalculator()
        result_df = calc.calculate_batch_bills(
            load_records, rate_config, progress_cb=progress_cb
        )
        result_df = flag_rate_eligibility(
            result_df, rate_eligibility_bounds(rate_config), is_comstock
        )
        frames.append(
            result_df.with_columns(
                pl.lit(rate_name).alias("rate_name"),
                pl.lit(is_primary).alias("is_primary"),
            )
        )
    return pl.concat(frames)


def _calculate_fuel_bills(
    mgr,
    building_ids: list,
    upgrade_id: int,
    fuel_rate_assignments_for_scenario: dict,
    tracker: ProgressTracker,
    monthly_fuel_data: dict[str, pl.DataFrame] | None = None,
    baseline_metadata: pl.DataFrame | None = None,
) -> pl.DataFrame | None:
    """Load upgrade metadata and calculate fuel bills for all buildings.

    The calculation is fully vectorized via Polars expressions; no per-row
    Python iteration is performed.

    For **ComStock**, each metadata row is processed independently (no dedup)
    so per-row weights are preserved. Energy consumption (both annual and
    monthly) is multiplied by the row ``weight`` *before* the rate expression
    is applied, so block-rate tier boundaries see real-world weighted
    consumption. Default-mode bills are passed through the per-release
    storage policy (see [billing/conversions.py]).

    For **ResStock**, metadata is deduped to one row per ``bldg_id`` and
    bills are stored per-building (the scalar dataset weight is applied at
    aggregation time, not here).

    Args:
        mgr: Active ``BuildStockDataManager``.
        building_ids: Buildings to include.
        upgrade_id: Upgrade scenario being billed.
        fuel_rate_assignments_for_scenario: ``{fuel_type: rate_dict_or_None}``.
            A value of ``None`` means use the dataset's default fuel-bill
            column.
        tracker: Progress tracker for status messages.
        monthly_fuel_data: Optional ``{"{bldg_id}_{upgrade}": DataFrame}``
            mapping where each DataFrame holds monthly-summed fuel kWh (12
            rows × N fuel-timeseries columns). Required only when at least
            one assigned fuel rate has ``is_block_rate=True``.
        baseline_metadata: The already-loaded, filtered, ``_row_uid``-tagged
            metadata frame from the pipeline. Used directly for upgrade 0
            (same rows as the electricity records) instead of re-loading the
            baseline file from cache/S3.

    Returns:
        Polars DataFrame with columns
        ``bldg_id, upgrade, _row_uid,
        <fuel>_bill, <fuel>_rate_name`` (one pair per assigned fuel),
        or ``None`` if no fuel data was available.
    """
    from buildstock_rate_impact_calculator.rates.fuel_rate import (
        FUEL_TYPES,
        fuel_bill_expr,
    )

    dataset_type = mgr.config.dataset_type or ""
    bldg_col = mgr.config.building_id_column

    # Per-fuel plan: (fuel_type, mode, metadata_col, rate_dict_or_None,
    # ts_col_or_None) where mode is "rate" or "default".
    fuel_plan: list[tuple[str, str, str, dict | None, str | None]] = []
    block_rate_fuels: list[tuple[str, str]] = []  # (fuel_type, ts_col)
    for ft in FUEL_TYPES:
        energy_col, bill_col = mgr.get_fuel_columns_for_type(ft)
        rate_dict = fuel_rate_assignments_for_scenario.get(ft)  # None = default
        if rate_dict is not None and energy_col:
            ts_col = (
                mgr.fuel_timeseries_column(ft)
                if rate_dict.get("is_block_rate", False)
                else None
            )
            fuel_plan.append((ft, "rate", energy_col, rate_dict, ts_col))
            if ts_col is not None:
                block_rate_fuels.append((ft, ts_col))
        elif rate_dict is None and bill_col:
            fuel_plan.append((ft, "default", bill_col, None, None))
        # If neither column exists for this fuel type, skip silently

    if not fuel_plan:
        return None

    # ── load metadata (geo-aware, column-projected) ────────────────────────
    # Upgrade 0 reuses the pipeline's in-memory metadata frame: it already
    # carries the fuel columns (part of the app column set), the user filters,
    # and the same _row_uid values as the electricity records. Upgrades load
    # their metadata file with the standard app column set so the projected
    # cache file is shared with other consumers (e.g. applicability checks).
    if upgrade_id == 0 and baseline_metadata is not None:
        upgrade_meta = baseline_metadata
        if "_row_uid" not in upgrade_meta.columns:
            upgrade_meta = _assign_row_uid(upgrade_meta, dataset_type, bldg_col)
    else:
        try:
            tracker.update(
                0,
                message=f"Loading fuel/default bill data for upgrade {upgrade_id}…",
            )
            upgrade_meta = mgr.load_upgrade_metadata(upgrade_id)
            upgrade_meta = upgrade_meta.filter(pl.col(bldg_col).is_in(building_ids))
            if dataset_type != "comstock":
                upgrade_meta = upgrade_meta.unique(subset=[bldg_col])
            upgrade_meta = _assign_row_uid(upgrade_meta, dataset_type, bldg_col)
        except Exception as exc:
            tracker.update(
                0,
                message=(
                    f"Warning: Could not load fuel metadata for upgrade "
                    f"{upgrade_id}: {exc}"
                ),
            )
            return None

    if upgrade_meta.height == 0:
        return None

    # Drop any fuels whose metadata column is missing in the loaded frame
    # (e.g. requested column was projected out by a per-geo aggregate file).
    fuel_plan = [p for p in fuel_plan if p[2] in upgrade_meta.columns]
    if not fuel_plan:
        return None
    block_rate_fuels = [
        (ft, ts_col)
        for ft, _mode, _col, _rate, ts_col in fuel_plan
        if ts_col is not None
    ]

    # ── join monthly fuel totals for block-rate fuels ──────────────────────
    monthly_col_names: dict[tuple[str, int], str] = {}
    if block_rate_fuels:
        if monthly_fuel_data is None:
            monthly_fuel_data = {}
        # Build a per-fuel monthly-totals DataFrame keyed by bldg_id, then
        # join onto upgrade_meta. For non-applicable buildings the caller
        # passes the baseline monthly_fuel_data entry.
        per_bldg_rows: dict[Any, dict[str, float]] = {}
        for bldg_id in upgrade_meta[bldg_col].to_list():
            key = f"{bldg_id}_{upgrade_id}"
            if key not in monthly_fuel_data:
                # Fall back to baseline timeseries (mirrors electricity path).
                key = f"{bldg_id}_0"
            df = monthly_fuel_data.get(key)
            if df is None or df.height == 0:
                continue
            row_dict: dict[str, float] = {}
            for _, ts_col in block_rate_fuels:
                if ts_col not in df.columns:
                    continue
                values = df[ts_col].to_list()
                # Pad / truncate to exactly 12 monthly values.
                values = (list(values) + [0.0] * 12)[:12]
                for month_idx, val in enumerate(values, start=1):
                    col_name = f"_monthly_kwh__{ts_col}__{month_idx:02d}"
                    row_dict[col_name] = float(val) if val is not None else 0.0
                    monthly_col_names[(ts_col, month_idx)] = col_name
            if row_dict:
                per_bldg_rows[bldg_id] = row_dict

        if per_bldg_rows:
            all_cols = sorted({c for d in per_bldg_rows.values() for c in d})
            ids_in_order = list(per_bldg_rows.keys())
            data: dict[str, list] = {bldg_col: ids_in_order}
            for col in all_cols:
                data[col] = [per_bldg_rows[bid].get(col, 0.0) for bid in ids_in_order]
            monthly_df = pl.DataFrame(data)
            upgrade_meta = upgrade_meta.join(monthly_df, on=bldg_col, how="left")
            # Fill nulls (buildings without monthly data) with 0.
            upgrade_meta = upgrade_meta.with_columns(
                [pl.col(c).fill_null(0.0) for c in all_cols]
            )
        else:
            # No monthly data available; ensure expected columns exist as 0.
            for _, ts_col in block_rate_fuels:
                for month_idx in range(1, 13):
                    col_name = f"_monthly_kwh__{ts_col}__{month_idx:02d}"
                    monthly_col_names[(ts_col, month_idx)] = col_name
                    upgrade_meta = upgrade_meta.with_columns(
                        pl.lit(0.0).alias(col_name)
                    )

    # ── build per-fuel result expressions ──────────────────────────────────
    is_comstock = dataset_type == "comstock"
    weight_expr = pl.col("weight").cast(pl.Float64).fill_null(0.0)

    result_exprs: list[pl.Expr] = [
        pl.col(bldg_col).alias("bldg_id"),
        pl.lit(upgrade_id).alias("upgrade"),
        pl.col("_row_uid"),
    ]

    for ft, mode, col_name, rate_dict, ts_col in fuel_plan:
        bill_col = f"{ft}_bill"
        rate_name_col = f"{ft}_rate_name"

        if mode == "default":
            value_expr = pl.col(col_name).cast(pl.Float64).fill_null(0.0)
            release = mgr.config.release
            if dataset_type == "resstock":
                bill_expr = value_expr
            elif dataset_type == "comstock" and release == "2024_release_2":
                bill_expr = value_expr * weight_expr
            elif dataset_type == "comstock" and release == "2025_release_3":
                bill_expr = value_expr * pl.lit(1.0e9)
            else:
                raise ValueError(
                    f"Cannot calculate default-mode fuel bill for unsupported "
                    f"dataset ({dataset_type!r}, {release!r})."
                )
            result_exprs.append(bill_expr.alias(bill_col))
            result_exprs.append(pl.lit("dataset default").alias(rate_name_col))
            continue

        # Rate mode — flat or block
        rate_name = rate_dict.get("name", "Custom Fuel Rate") if rate_dict else ""
        is_block = bool(rate_dict.get("is_block_rate", False)) if rate_dict else False

        if is_block and ts_col is not None:
            monthly_exprs: list[pl.Expr] = []
            for month_idx in range(1, 13):
                mcol = monthly_col_names.get((ts_col, month_idx))
                if mcol is None or mcol not in upgrade_meta.columns:
                    monthly_exprs.append(pl.lit(0.0))
                else:
                    raw = pl.col(mcol).cast(pl.Float64).fill_null(0.0)
                    monthly_exprs.append(raw * weight_expr if is_comstock else raw)
            bill_expr = fuel_bill_expr(rate_dict, monthly_kwh_exprs=monthly_exprs)
        else:
            annual_kwh = pl.col(col_name).cast(pl.Float64).fill_null(0.0)
            if is_comstock:
                annual_kwh = annual_kwh * weight_expr
            bill_expr = fuel_bill_expr(rate_dict, annual_kwh_expr=annual_kwh)

        result_exprs.append(bill_expr.alias(bill_col))
        result_exprs.append(pl.lit(rate_name).alias(rate_name_col))

    out = upgrade_meta.select(result_exprs)
    return out if out.height > 0 else None


def _run_pipeline(
    app_state: AppState,
    tracker: ProgressTracker,
    result_store: dict,
    resolved_rate_assignments: dict,
    resolved_fuel_rate_assignments: dict,
    preloaded_metadata: pl.DataFrame | None = None,
    resolved_scenario_comparisons: dict[str, list[tuple[str, dict]]] | None = None,
) -> None:
    """
    Background thread: download timeseries then calculate bills.
    Writes results into result_store["df"] on completion.

    ``resolved_rate_assignments`` maps scenario key → ``(rate_name,
    rate_dict)`` (the primary rate). ``resolved_scenario_comparisons`` maps
    scenario key → list of extra ``(name, dict)`` electricity rates that
    scenario is additionally billed under; load records are reused across
    variants.

    ``preloaded_metadata`` is the session-cached metadata frame resolved on
    the main thread; passing it avoids re-reading the projected metadata cache
    from disk (or S3) inside the worker thread.
    """
    try:
        from buildstock_rate_impact_calculator.data.buildstock_manager import (
            BuildStockDataManager,
        )

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
        if preloaded_metadata is not None:
            metadata = preloaded_metadata
            mgr._metadata = metadata
            mgr._update_state_mapping(metadata)
        else:
            metadata = mgr.load_metadata(
                additional_columns=list(app_state.additional_columns or []) or None,
            )
        metadata = app_state.apply_geo_filter(metadata)
        if app_state.active_filters:
            _filters = {
                k: v
                for k, v in app_state.active_filters.items()
                if k in metadata.columns
            }
            metadata = mgr.filter_metadata(metadata, _filters)

        # Drop rows whose weighted real-world floor area is unrealistically
        # small (tiny sample weights → inflated cost-per-ft²). No-op for
        # ResStock. See BuildStockDataManager.MIN_WEIGHTED_AREA_SQFT.
        _rows_before = metadata.height
        metadata = mgr.filter_min_weighted_area(metadata)
        _rows_dropped = _rows_before - metadata.height
        if _rows_dropped > 0:
            from buildstock_rate_impact_calculator.data.buildstock_manager import (
                MIN_WEIGHTED_AREA_SQFT,
            )

            tracker.log(
                f"Excluded {_rows_dropped:,} building(s) with weighted area "
                f"≤ {MIN_WEIGHTED_AREA_SQFT:g} ft²."
            )

        # ── fast preview sampling (Phase 9) ──────────────────────────────────
        preview_on = bool(getattr(app_state, "preview_mode", False))
        result_store["preview"] = preview_on
        if preview_on:
            _n_per = int(getattr(app_state, "preview_n_per_stratum", 20) or 20)
            _rows_before_preview = metadata.height
            metadata = mgr.sample_metadata_stratified(metadata, _n_per)
            tracker.log(
                f"⚡ Preview mode: sampled {metadata.height:,} of "
                f"{_rows_before_preview:,} rows ({_n_per}/stratum); weights "
                "re-scaled so totals stay unbiased."
            )
            if mgr.config.dataset_type == "comstock" and "weight" in metadata.columns:
                # Fold the stratum scale into the row weight before load
                # records are built — every weighted quantity (bills, $/ft²,
                # totals) flows from this one column.
                metadata = metadata.with_columns(
                    (pl.col("weight") * pl.col("_sample_scale")).alias("weight")
                ).drop("_sample_scale")
            # ResStock keeps _sample_scale on the frame: _build_load_records
            # folds it into the per-record scalar weight.

        # Stable per-row identifier (ComStock: one bldg_id may span multiple
        # geographic aggregates, each with a different weight). Used as a join
        # key between electricity and fuel-bill DataFrames.
        metadata = _assign_row_uid(
            metadata, mgr.config.dataset_type, mgr.config.building_id_column
        )

        building_ids = list(dict.fromkeys(mgr.get_building_ids(metadata)))
        tracker.log(
            f"Metadata loaded — {len(building_ids):,} buildings match the current filters."
        )
        if len(building_ids) < _MIN_SAMPLE_MODELS:
            tracker.log(
                f"⚠ Only {len(building_ids):,} building models match — "
                "weighted statistics may be unreliable."
            )

        if tracker.is_cancelled():
            tracker.done(message="Cancelled before download.")
            return

        # ── fetch upgrade applicability ───────────────────────────────────────
        _non_baseline = [u for u in app_state.selected_upgrades if u != 0]
        upgrade_applicable_ids: dict[int, list] = {0: list(building_ids)}
        if _non_baseline:
            tracker.start_phase(
                "Checking upgrade applicability", total=len(_non_baseline)
            )
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

        # Determine which fuel timeseries columns are needed for block-rate
        # billing across all scenarios. These are fetched in the same Parquet
        # scan as the electricity load column and aggregated to monthly totals.
        block_fuel_ts_cols: list[str] = []
        for _scen_rates in resolved_fuel_rate_assignments.values():
            for _ft, _rate_dict in (_scen_rates or {}).items():
                if _rate_dict and _rate_dict.get("is_block_rate"):
                    _ts_col = mgr.fuel_timeseries_column(_ft)
                    if _ts_col and _ts_col not in block_fuel_ts_cols:
                        block_fuel_ts_cols.append(_ts_col)

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

        def _is_cached(bldg: Any, upg: int) -> bool:
            hourly_path = ts_dir / f"{bldg}_{upg}.parquet"
            if not hourly_path.exists():
                return False
            if not block_fuel_ts_cols:
                return True
            monthly_path = ts_dir / f"{bldg}_{upg}_monthly_fuel.parquet"
            return monthly_path.exists()

        cached_pairs = {pair for pair in all_pairs if _is_cached(*pair)}
        missing_pairs = all_pairs - cached_pairs

        if cached_pairs:
            tracker.log(
                f"{len(cached_pairs):,} timeseries files already cached on disk."
            )

        # Download only the missing files. Downloads are written to disk and
        # re-read per scenario below, so only one scenario's load profiles are
        # held in memory at a time.
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
                mgr.download_timeseries_with_progress(
                    building_ids=bldgs,
                    upgrades=[upg],
                    output_dir=ts_dir,
                    progress_cb=dl_progress,
                    cancel_event=tracker.cancel_event,
                    monthly_fuel_columns=block_fuel_ts_cols or None,
                )
            if not tracker.is_cancelled():
                tracker.log(f"Downloaded {n_missing:,} timeseries files.")

        if tracker.is_cancelled():
            tracker.done(message="Download cancelled.")
            return

        available_pairs = {pair for pair in all_pairs if _is_cached(*pair)}
        if not available_pairs:
            tracker.error("No timeseries data was downloaded.")
            return

        # Baseline profiles serve every scenario (the baseline scenario itself
        # plus the fall-back for unapplicable buildings), so they are loaded
        # once and kept; upgrade-specific profiles are loaded per scenario and
        # released afterwards.
        baseline_bldgs = sorted({b for b, u in available_pairs if u == 0})
        baseline_ts: dict[str, pl.DataFrame] = {}
        if baseline_bldgs:
            tracker.start_phase(
                f"Loading {len(baseline_bldgs):,} baseline timeseries files from disk"
            )
            baseline_ts = mgr.load_cached_timeseries(
                building_ids=baseline_bldgs,
                upgrades=[0],
                output_dir=ts_dir,
            )
            tracker.log(
                f"Loaded {len(baseline_ts):,} baseline timeseries files from disk."
            )

        # Load monthly-fuel companion files for all (bldg, upgrade) pairs in
        # play. Empty when no block-rate fuel rates are configured.
        monthly_fuel_data: dict[str, pl.DataFrame] = {}
        if block_fuel_ts_cols:
            pairs_by_upg: dict[int, list] = {}
            for bldg, upg in all_pairs:
                pairs_by_upg.setdefault(upg, []).append(bldg)
            for upg, bldgs in pairs_by_upg.items():
                monthly_fuel_data.update(
                    mgr.load_cached_monthly_fuel(
                        building_ids=bldgs,
                        upgrades=[upg],
                        output_dir=ts_dir,
                    )
                )
        # ── calculate electricity bills ───────────────────────────────────────
        load_column = mgr.config.default_load_column
        scalar_weight = app_state.resstock_weight  # None for ComStock
        scenario_comparisons = resolved_scenario_comparisons or {}
        all_results = []

        for upgrade_id in app_state.selected_upgrades:
            scenario_key = _scenario_key(upgrade_id)
            rate_entry = resolved_rate_assignments.get(scenario_key)
            if rate_entry is None:
                tracker.log(
                    f"No electricity rate assigned for {scenario_key}, skipping."
                )
                continue
            primary_name, rate_config = rate_entry

            applicable_set = set(upgrade_applicable_ids.get(upgrade_id, building_ids))

            # Load this scenario's upgrade-specific profiles from disk; merged
            # with the persistent baseline dict for fall-back lookups.
            if upgrade_id != 0:
                upgrade_bldgs = sorted(
                    {b for b, u in available_pairs if u == upgrade_id}
                )
                upgrade_ts = mgr.load_cached_timeseries(
                    building_ids=upgrade_bldgs,
                    upgrades=[upgrade_id],
                    output_dir=ts_dir,
                )
                ts_lookup = {**baseline_ts, **upgrade_ts}
            else:
                ts_lookup = baseline_ts

            # ComStock: one record per metadata row (load × weight applied here
            # so PySAM bills are real-world weighted dollars).
            # ResStock: one record per unique bldg_id; loads passed unscaled,
            # the scalar dataset weight is applied at aggregation time.
            load_records = _build_load_records(
                metadata=metadata,
                ts_data=ts_lookup,
                dataset_type=mgr.config.dataset_type,
                load_column=load_column,
                scalar_weight=scalar_weight,
                upgrade_id=upgrade_id,
                applicable_set=applicable_set,
                bldg_col=mgr.config.building_id_column,
            )
            # Upgrade-specific frames are no longer needed once the records
            # hold their load arrays — release before the PySAM phase.
            ts_lookup = None
            if upgrade_id != 0:
                upgrade_ts = None

            if not load_records:
                tracker.log(
                    f"No load profiles available for upgrade {upgrade_id}, skipping."
                )
                continue

            # Primary assigned rate first, then this scenario's additional
            # (sweep) rates — the same load records are reused for every variant.
            comparison = scenario_comparisons.get(scenario_key, [])
            variants = [(primary_name, rate_config, True)] + [
                (n, rc, False) for n, rc in comparison if n != primary_name
            ]
            tracker.start_phase(
                f"Calculating electricity bills — upgrade {upgrade_id}",
                total=len(load_records) * len(variants),
            )
            try:
                result_df = _bill_upgrade_under_rates(
                    load_records,
                    variants,
                    tracker,
                    is_comstock=mgr.config.dataset_type == "comstock",
                )
                # Tag each building with its applicability status for this upgrade
                result_df = result_df.with_columns(
                    pl.col("bldg_id").is_in(list(applicable_set)).alias("applicable")
                )
                # Surface rate-eligibility screening in the activity log.
                if "rate_eligible" in result_df.columns:
                    _elig_counts = (
                        result_df.group_by("rate_name")
                        .agg(
                            (~pl.col("rate_eligible")).sum().alias("n_ineligible"),
                            pl.len().alias("n_total"),
                        )
                        .sort("rate_name")
                    )
                    for _erow in _elig_counts.iter_rows(named=True):
                        if _erow["n_ineligible"]:
                            tracker.log(
                                f"Upgrade {upgrade_id}: {_erow['n_ineligible']:,} of "
                                f"{_erow['n_total']:,} buildings fall outside "
                                f"'{_erow['rate_name']}' eligibility (kW/kWh window)."
                            )
                all_results.append(result_df)
            except Exception as exc:
                tracker.log(
                    f"Electricity billing error for upgrade {upgrade_id}: {exc}"
                )

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
            fuel_rates_for_scenario = resolved_fuel_rate_assignments.get(
                scenario_key, {}
            )

            fuel_df = _calculate_fuel_bills(
                mgr=mgr,
                building_ids=building_ids,
                upgrade_id=upgrade_id,
                fuel_rate_assignments_for_scenario=fuel_rates_for_scenario,
                tracker=tracker,
                monthly_fuel_data=monthly_fuel_data,
                baseline_metadata=metadata,
            )
            if fuel_df is not None:
                fuel_frames.append(fuel_df)
            tracker.advance(1)

        if fuel_frames:
            all_fuel = pl.concat(fuel_frames)
            # Join on (bldg_id, upgrade, _row_uid) so ComStock per-row bills
            # line up with the matching electricity-bill row instead of
            # cartesian-joining across all geo aggregates for that bldg_id.
            combined = combined.join(
                all_fuel, on=["bldg_id", "upgrade", "_row_uid"], how="left"
            )

        # ── compute total energy bill ─────────────────────────────────────────
        fuel_bill_cols = [
            c for c in combined.columns if c.endswith("_bill") and c != "electricity_annual_bill"
        ]
        if fuel_bill_cols:
            total_expr = pl.col("electricity_annual_bill").fill_null(0.0)
            for fc in fuel_bill_cols:
                total_expr = total_expr + pl.col(fc).fill_null(0.0)
            combined = combined.with_columns(total_expr.alias("total_energy_bill"))

        # ── attach characteristic columns to the results frame ───────────────
        # Union of the user's additional columns and the dataset's configured
        # filter columns (always joined so segment breakout / energy burden
        # work without user action), deduplicated, order preserved.
        extras = list(
            dict.fromkeys(
                [
                    *(app_state.additional_columns or []),
                    *(mgr.config.filter_columns or []),
                ]
            )
        )
        extras_present = [c for c in extras if c in metadata.columns]
        if extras_present:
            bldg_col = mgr.config.building_id_column
            # Join on (bldg_id, _row_uid) so ComStock per-row attributes (one
            # row per geo aggregate) line up correctly. For ResStock _row_uid
            # collapses to bldg_id so the join still matches one-to-one.
            join_keys = ["bldg_id", "_row_uid"]
            extras_df = (
                metadata.select([bldg_col, "_row_uid", *extras_present])
                .unique(subset=[bldg_col, "_row_uid"], keep="first")
                .rename({bldg_col: "bldg_id"})
            )
            # Drop any extras columns that happen to be already present on
            # ``combined`` to avoid ``_right`` suffix collisions.
            extras_df = extras_df.select(
                [
                    *join_keys,
                    *[c for c in extras_present if c not in combined.columns],
                ]
            )
            combined = combined.join(extras_df, on=join_keys, how="left")

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
        st.caption("Check rate assignments and configuration, then re-run.")
    elif snap.is_done:
        st.success(
            f"{snap.message}  (total time {_fmt_duration(snap.elapsed_seconds)})"
        )
    elif snap.phases:
        # Thread ended without done()/error() — should not happen, but surface it
        st.error(
            "The background run stopped unexpectedly. Check the activity log below."
        )

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

    if not steps.dataset_complete(app_state):
        steps.render_gate(
            "Select a dataset and upgrade scenarios first.",
            steps.STEP_DATASET,
            key="gate_run_dataset",
        )
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
                st.markdown(
                    f"**States:** {', '.join(sorted(app_state.selected_states))}"
                )
            if app_state.selected_county_ids:
                st.markdown(
                    f"**Counties:** {len(app_state.selected_county_ids)} selected"
                )
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
            "Assign rates on the Energy Rates step before running."
        )
        if st.button("← Assign rates", key="run_goto_rates"):
            steps.goto_step(steps.STEP_RATES)

    st.divider()

    # ── Runtime state in session ─────────────────────────────────────────────
    st.session_state.setdefault("run_tracker", None)
    st.session_state.setdefault("run_thread", None)
    st.session_state.setdefault("run_results", {})

    thread: threading.Thread | None = st.session_state["run_thread"]
    result_store: dict = st.session_state["run_results"]

    is_running = thread is not None and thread.is_alive()

    # ── API key pre-flight check ──────────────────────────────────────────────
    # Union of every scenario's additional (sweep) rates, plus any legacy global
    # comparison rates, so the URDB-key check covers all rates that will bill.
    _all_extra_rates = list(app_state.comparison_rates or [])
    for _names in (app_state.scenario_extra_rates or {}).values():
        _all_extra_rates.extend(_names)
    needs_urdb = _urdb_key_required(
        app_state.rate_assignments,
        app_state.rate_library,
        comparison_rates=_all_extra_rates,
    )
    api_key_available = bool(app_state.urdb_api_key or os.getenv("OPENEI_API_KEY"))
    if needs_urdb and not api_key_available:
        st.error(
            "An OpenEI API key is required: one or more rate assignments need to "
            "fetch rate data from URDB during the run. Enter your API key in the "
            "sidebar before starting."
        )

    # ── Fast preview sampling (Phase 9) ───────────────────────────────────────
    preview_on = st.toggle(
        "⚡ Fast preview (stratified sample)",
        value=app_state.preview_mode,
        key="preview_mode_toggle",
        help=(
            "Bill a stratified sample of the filtered buildings instead of "
            "all of them — iterate on rate designs in seconds."
        ),
    )
    app_state.preview_mode = bool(preview_on)
    if preview_on:
        app_state.preview_n_per_stratum = int(
            st.number_input(
                "Buildings per stratum",
                min_value=1,
                max_value=500,
                value=int(app_state.preview_n_per_stratum or 20),
                key="preview_n_per_stratum_input",
            )
        )
        st.caption(
            "Weighted totals are re-scaled to stay unbiased; per-building "
            "means and medians are stratified-sample estimates."
        )

    # ── Start button ──────────────────────────────────────────────────────────
    start_blocked = assigned == 0 or (needs_urdb and not api_key_available)
    if not is_running:
        if st.button(
            "▶ Start Download & Calculate", type="primary", disabled=start_blocked
        ):
            # Resolve (or reuse) the session-cached metadata on the main
            # thread so the worker doesn't re-read it from disk/S3.
            from buildstock_rate_impact_calculator.ui.metadata_session import (
                load_session_metadata,
            )

            try:
                with st.spinner("Preparing building metadata…"):
                    _, _preloaded_metadata = load_session_metadata(app_state)
            except Exception as exc:
                st.error(f"Cannot start run — failed to load metadata: {exc}")
                return

            new_tracker = ProgressTracker()
            new_results: dict = {}
            # Resolve rate names → full dicts before handing off to background thread
            _rate_lib = app_state.rate_library
            _fuel_lib = app_state.fuel_rate_library
            # Scenario key → (rate name, rate dict); the name is stamped onto
            # results as ``rate_name`` for the rate sweep.
            _resolved_rates = {
                sk: (name, _rate_lib[name])
                for sk, name in app_state.rate_assignments.items()
                if name and name in _rate_lib
            }
            # Per-scenario additional (sweep) rates → {sk: [(name, dict), …]}.
            # Legacy global comparison_rates are folded into every scenario so
            # older projects that predate per-scenario assignment still sweep.
            _legacy_comparison = list(app_state.comparison_rates or [])
            _resolved_scenario_comparisons: dict[str, list[tuple[str, dict]]] = {}
            for sk in app_state.rate_assignments:
                _names = list(app_state.scenario_extra_rates.get(sk, []))
                for _n in _legacy_comparison:
                    if _n not in _names:
                        _names.append(_n)
                _resolved_scenario_comparisons[sk] = [
                    (name, _rate_lib[name]) for name in _names if name in _rate_lib
                ]
            _resolved_fuel = {
                sk: {
                    ft: _fuel_lib.get(name) if name else None
                    for ft, name in ft_map.items()
                }
                for sk, ft_map in app_state.fuel_rate_assignments.items()
            }
            new_thread = threading.Thread(
                target=_run_pipeline,
                args=(
                    app_state,
                    new_tracker,
                    new_results,
                    _resolved_rates,
                    _resolved_fuel,
                    _preloaded_metadata,
                    _resolved_scenario_comparisons,
                ),
                daemon=True,
                name=auto_exit.PIPELINE_THREAD_NAME,
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
    # running, only this panel re-renders (not the whole app). Each tick is a
    # fragment rerun, so Streamlit briefly shows the Running/Stop toolbar and
    # dims the panel while it re-renders — expected polling behavior. The
    # interval is 2s: during the PySAM billing phase the worker threads hold
    # the GIL for long stretches, so more frequent ticks just queue up and
    # make the dimming more visible without adding information.
    if is_running:
        st.session_state["_run_finish_pending"] = True

    @st.fragment(run_every=2.0 if is_running else None)
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
        # A fresh run supersedes any project-loaded results frame; drop the
        # stale copy so only one results DataFrame is held in session memory.
        st.session_state.pop("results_df", None)
        df = result_store["df"]
        st.divider()
        st.success(f"Calculation complete — {len(df):,} building-scenario results.")
        st.dataframe(df.head(20).to_pandas(), width="stretch")
        if st.button("View Results →", type="primary", key="run_view_results"):
            steps.goto_step(steps.STEP_RESULTS)
