"""
Shared helpers for computing per-upgrade applicability summaries.

Used by the Geography page (to display the effect of geographic selections)
and the Run page (to show the final pre-download summary).
"""
from __future__ import annotations
import hashlib
import json
from typing import Any, Optional

from buildstock_rate_impact_calculator.ui.state import AppState


def compute_summary_cache_key(app_state: AppState) -> str:
    """Return a stable session-state key for the applicability summary."""
    parts = "|".join([
        app_state.dataset_type or "",
        app_state.release or "",
        str(sorted(app_state.selected_upgrades or [])),
        json.dumps(app_state.active_filters, sort_keys=True, default=str),
        str(sorted(app_state.selected_states or [])),
        str(sorted(app_state.selected_county_ids or [])),
        str(sorted(app_state.selected_puma_ids or [])),
        str(sorted(app_state.selected_geo_ids or [])),
    ])
    digest = hashlib.md5(parts.encode()).hexdigest()[:12]
    return f"applicability_summary_{digest}"


def load_applicability_summary(
    app_state: AppState,
    metadata: Optional[Any] = None,
    manager: Optional[Any] = None,
) -> dict:
    """
    Fetch per-upgrade applicability for the buildings matching current filters.

    Parameters
    ----------
    app_state : AppState
        Source of dataset, release, upgrades, geo, and building filters.
    metadata : polars.DataFrame, optional
        Pre-loaded metadata that has already had geo + building filters applied.
        When provided, skips re-loading from OEDI.
    manager : BuildStockDataManager, optional
        Re-use an existing manager instance instead of constructing one.

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
                cache_dir=app_state.output_dir / "cache",
            )

        if metadata is None:
            metadata = mgr.load_metadata()
            metadata = app_state.apply_geo_filter(metadata)
            if app_state.active_filters:
                metadata = mgr.filter_metadata(metadata, app_state.active_filters)

        building_ids = mgr.get_building_ids(metadata)
        n_total = len(building_ids)

        rows = []
        for upgrade_id in sorted(app_state.selected_upgrades or []):
            if upgrade_id == 0:
                rows.append({
                    "upgrade_id": 0,
                    "n_applicable": n_total,
                    "n_total": n_total,
                    "est_bytes": n_total * 90_000,
                })
            else:
                applicability = mgr.get_upgrade_applicability(upgrade_id, building_ids)
                n_applicable = sum(1 for v in applicability.values() if v)
                rows.append({
                    "upgrade_id": upgrade_id,
                    "n_applicable": n_applicable,
                    "n_total": n_total,
                    "est_bytes": n_applicable * 90_000,
                })
        return {"rows": rows, "total_buildings": n_total}
    except Exception as exc:  # pragma: no cover — surfaced in UI
        return {"error": str(exc)}
