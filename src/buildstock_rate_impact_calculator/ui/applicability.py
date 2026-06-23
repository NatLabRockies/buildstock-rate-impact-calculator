"""
Shared helpers for computing and rendering per-upgrade applicability summaries.

Used by the Geography page, the Building Filters page, and the Run page so the
summary of applicable buildings (for the selected upgrades, geographies, and
building filters) stays consistent everywhere.

The expensive part — which buildings each upgrade applies to — depends only on
the dataset, geographic selection, and upgrades, NOT on building characteristic
filters. Those per-upgrade applicable-id sets are cached separately
(get_applicable_id_sets) so changing a filter updates the summary instantly
without re-reading upgrade metadata from OEDI.
"""

from __future__ import annotations
import hashlib
import json
from typing import Any, Optional

from buildstock_rate_impact_calculator.ui.state import AppState

#: Empirical average size of one hourly timeseries parquet download
BYTES_PER_BUILDING = 90_000


def format_bytes(n: float) -> str:
    """Format a byte count into a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024
    return f"{n:.1f} PB"


def _geo_parts(app_state: AppState) -> list[str]:
    sel = app_state.effective_geo_selection()
    return [
        app_state.dataset_type or "",
        app_state.release or "",
        str(sorted(app_state.selected_upgrades or [])),
        str(sorted(sel["states"])),
        str(sorted(sel["county_ids"])),
        str(sorted(sel["puma_ids"])),
    ]


def compute_summary_cache_key(app_state: AppState) -> str:
    """Session-state key for the applicability summary (includes filters)."""
    parts = "|".join(
        _geo_parts(app_state)
        + [json.dumps(app_state.active_filters, sort_keys=True, default=str)]
    )
    digest = hashlib.md5(parts.encode()).hexdigest()[:12]
    return f"applicability_summary_{digest}"


def applicable_sets_cache_key(app_state: AppState) -> str:
    """Session-state key for per-upgrade applicable-id sets (no filters)."""
    digest = hashlib.md5("|".join(_geo_parts(app_state)).encode()).hexdigest()[:12]
    return f"applicable_sets_{digest}"


def get_applicable_id_sets(
    app_state: AppState,
    manager: Any,
    building_ids: list,
    cache: Optional[dict] = None,
) -> dict[int, set]:
    """
    Per-upgrade sets of applicable building ids among *building_ids*.

    Parameters
    ----------
    app_state : AppState
        Source of the selected upgrades.
    manager : BuildStockDataManager
        Manager with the active geographic selection applied.
    building_ids : list
        Geo-filtered (but not characteristic-filtered) building ids.
    cache : dict, optional
        Mapping (e.g. st.session_state) used to cache the sets across reruns.

    Returns
    -------
    dict[int, set]
        Mapping upgrade_id → set of applicable building ids.
    """
    key = applicable_sets_cache_key(app_state)
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached

    sets: dict[int, set] = {}
    for upgrade_id in sorted(app_state.selected_upgrades or []):
        if upgrade_id == 0:
            sets[0] = set(building_ids)
        else:
            applicability = manager.get_upgrade_applicability(upgrade_id, building_ids)
            sets[upgrade_id] = {bid for bid, ok in applicability.items() if ok}

    if cache is not None:
        cache[key] = sets
    return sets


def load_applicability_summary(
    app_state: AppState,
    metadata: Optional[Any] = None,
    manager: Optional[Any] = None,
    applicable_sets_cache: Optional[dict] = None,
) -> dict:
    """
    Compute the per-upgrade applicability summary for the buildings matching
    the current geographic selection and building filters.

    Parameters
    ----------
    app_state : AppState
        Source of dataset, release, upgrades, geo, and building filters.
    metadata : polars.DataFrame, optional
        Pre-loaded geo-filtered (but NOT characteristic-filtered) metadata.
        When omitted, metadata is loaded from OEDI at the appropriate
        geographic resolution.
    manager : BuildStockDataManager, optional
        Re-use an existing manager instance instead of constructing one.
    applicable_sets_cache : dict, optional
        Cache (e.g. st.session_state) for the per-upgrade applicable-id sets.

    Returns
    -------
    dict
        ``{"rows": [...], "total_buildings": int}`` on success, or
        ``{"error": str}`` on failure.
    """
    from buildstock_rate_impact_calculator.data.buildstock_manager import (
        BuildStockDataManager,
    )

    try:
        mgr = manager
        if mgr is None:
            mgr = BuildStockDataManager(
                dataset_type=app_state.dataset_type,
                release=app_state.release,
                cache_enabled=True,
                cache_dir=app_state.output_dir,
            )
        sel = app_state.effective_geo_selection()
        mgr.set_geo_selection(
            states=sel["states"],
            county_ids=sel["county_ids"],
            puma_ids=sel["puma_ids"],
        )

        if metadata is None:
            metadata = mgr.load_metadata()
            metadata = app_state.apply_geo_filter(metadata)

        bldg_col = mgr.config.building_id_column
        geo_ids = metadata[bldg_col].unique().to_list()

        filters = {
            k: v
            for k, v in (app_state.active_filters or {}).items()
            if k in metadata.columns
        }
        filtered = mgr.filter_metadata(metadata, filters) if filters else metadata
        filtered_ids = set(filtered[bldg_col].to_list())
        n_total = len(filtered_ids)

        sets = get_applicable_id_sets(
            app_state, mgr, geo_ids, cache=applicable_sets_cache
        )

        rows = []
        for upgrade_id in sorted(app_state.selected_upgrades or []):
            if upgrade_id == 0:
                n_applicable = n_total
            else:
                n_applicable = len(filtered_ids & sets.get(upgrade_id, filtered_ids))
            rows.append(
                {
                    "upgrade_id": upgrade_id,
                    "n_applicable": n_applicable,
                    "n_total": n_total,
                    "est_bytes": n_applicable * BYTES_PER_BUILDING,
                }
            )
        return {"rows": rows, "total_buildings": n_total}
    except Exception as exc:  # pragma: no cover — surfaced in UI
        return {"error": str(exc)}


def render_applicability_table(
    app_state: AppState,
    manager: Optional[Any] = None,
    metadata: Optional[Any] = None,
    *,
    title: str = "Upgrade Applicability Summary",
    show_refresh: bool = False,
    key_prefix: str = "",
) -> None:
    """
    Render the applicability summary table for the current configuration.

    Computes (and session-caches) the summary when metadata for the active
    geographic selection is available; otherwise prompts the user to make/save
    geographic selections first so no metadata is downloaded prematurely.
    """
    import streamlit as st
    import polars as pl

    from buildstock_rate_impact_calculator.data.buildstock_manager import (
        BuildStockDataManager,
    )
    from buildstock_rate_impact_calculator.data.upgrade_labels import get_upgrade_label
    from buildstock_rate_impact_calculator.ui.metadata_session import (
        get_manager,
        get_session_metadata,
    )

    if not (
        app_state.dataset_type and app_state.release and app_state.selected_upgrades
    ):
        return

    sum_key = compute_summary_cache_key(app_state)

    if show_refresh:
        if st.button(
            "↺ Refresh",
            key=f"{key_prefix}refresh_applicability",
            help="Re-fetch applicability data from OEDI",
        ):
            st.session_state.pop(sum_key, None)
            st.session_state.pop(applicable_sets_cache_key(app_state), None)
            st.rerun()

    # Resolve metadata from the session cache when not handed in directly
    if metadata is None:
        raw = get_session_metadata(app_state)
        if raw is not None:
            metadata = app_state.apply_geo_filter(raw)
            if manager is None:
                manager = get_manager(app_state)

    summary = st.session_state.get(sum_key)
    if summary is None:
        if metadata is None:
            st.markdown(f"**{title}**")
            st.caption(
                "Building metadata has not been loaded yet — make geographic "
                "selections on the Geography tab (or load metadata on the "
                "Building Filters tab) to see the applicability summary."
            )
            return
        with st.spinner("Computing upgrade applicability…"):
            summary = load_applicability_summary(
                app_state,
                metadata=metadata,
                manager=manager,
                applicable_sets_cache=st.session_state,
            )
            st.session_state[sum_key] = summary

    st.markdown(f"**{title}**")
    if "error" in summary:
        st.warning(f"Could not load applicability data: {summary['error']}")
        return

    cfg_key = f"{app_state.dataset_type}_{app_state.release}"
    base_uri = BuildStockDataManager.DATASET_CONFIGS[cfg_key].base_uri

    tbl_rows = []
    for row in summary.get("rows", []):
        uid = row["upgrade_id"]
        try:
            label = get_upgrade_label(base_uri, uid)
        except Exception:
            label = f"Upgrade {uid}"
        scenario_label = label if uid == 0 else f"Upgrade {uid}: {label}"
        pct = row["n_applicable"] / row["n_total"] * 100 if row["n_total"] > 0 else 0.0
        tbl_rows.append(
            {
                "Scenario": scenario_label,
                "Applicable": f"{row['n_applicable']:,} / {row['n_total']:,} ({pct:.0f}%)",
                "Est. Download": format_bytes(row["est_bytes"]),
            }
        )

    if tbl_rows:
        st.dataframe(
            pl.DataFrame(tbl_rows).to_pandas(),
            hide_index=True,
            width="stretch",
        )
        total_est = sum(r["est_bytes"] for r in summary.get("rows", []))
        st.caption(f"Total estimated download: {format_bytes(total_est)}")
