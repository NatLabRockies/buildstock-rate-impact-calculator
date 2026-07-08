"""
Rate applicability screening against URDB capacity/usage windows.

URDB rates may declare the demand and consumption windows they apply to:

* ``peakkwcapacitymin`` / ``peakkwcapacitymax`` — peak demand window (kW)
* ``peakkwhusagemin`` / ``peakkwhusagemax`` — monthly consumption window (kWh)

Buildings outside the assigned rate's window are flagged (not excluded) so
users can see how much of the stock the rate does not actually apply to.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import polars as pl


def rate_eligibility_bounds(rate_dict: Dict[str, Any]) -> Optional[dict]:
    """Extract the eligibility window from a URDB rate dict.

    Missing, non-numeric, or non-positive values are treated as unbounded
    (``UrdbRateBuilder`` writes 0.0 for "no bound", matching URDB usage).

    Returns:
        ``{"kw_min", "kw_max", "kwh_min", "kwh_max"}`` with ``None`` for
        unbounded edges, or ``None`` when the rate declares no bounds at all.
    """

    def _positive(key: str) -> Optional[float]:
        value = rate_dict.get(key)
        try:
            value = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    bounds = {
        "kw_min": _positive("peakkwcapacitymin"),
        "kw_max": _positive("peakkwcapacitymax"),
        "kwh_min": _positive("peakkwhusagemin"),
        "kwh_max": _positive("peakkwhusagemax"),
    }
    if all(v is None for v in bounds.values()):
        return None
    return bounds


def flag_rate_eligibility(
    results_df: pl.DataFrame,
    bounds: Optional[dict],
    is_comstock: bool,
) -> pl.DataFrame:
    """Add a ``rate_eligible`` bool column against a rate's window.

    Per-building quantities are compared: ComStock rows carry *weighted*
    ``annual_energy_kwh`` / ``peak_demand_kw``, so both are divided by
    ``weight`` first; ResStock rows are already per-building. The URDB kWh
    window is monthly, so annual consumption is compared as ``annual / 12``
    (mean monthly kWh). Rows with null consumption/peak (e.g. error rows) are
    flagged eligible — missing data is not grounds for exclusion.

    Args:
        results_df: Frame from ``calculate_batch_bills``.
        bounds: Window from :func:`rate_eligibility_bounds`; ``None`` marks
            every row eligible.
        is_comstock: Whether rows carry weighted quantities.
    """
    if bounds is None:
        return results_df.with_columns(pl.lit(True).alias("rate_eligible"))

    def _per_building(col: str) -> Optional[pl.Expr]:
        if col not in results_df.columns:
            return None
        expr = pl.col(col).cast(pl.Float64)
        if is_comstock:
            if "weight" not in results_df.columns:
                return None
            weight = pl.col("weight").cast(pl.Float64)
            return (
                pl.when(weight.is_not_null() & (weight > 0))
                .then(expr / weight)
                .otherwise(None)
            )
        return expr

    kw = _per_building("peak_demand_kw")
    monthly_kwh_expr = _per_building("annual_energy_kwh")
    monthly_kwh = monthly_kwh_expr / 12.0 if monthly_kwh_expr is not None else None

    checks: list[pl.Expr] = []
    for expr, lo_key, hi_key in (
        (kw, "kw_min", "kw_max"),
        (monthly_kwh, "kwh_min", "kwh_max"),
    ):
        if expr is None:
            continue
        if bounds.get(lo_key) is not None:
            checks.append(expr.is_null() | (expr >= bounds[lo_key]))
        if bounds.get(hi_key) is not None:
            checks.append(expr.is_null() | (expr <= bounds[hi_key]))

    if not checks:
        return results_df.with_columns(pl.lit(True).alias("rate_eligible"))

    eligible = checks[0]
    for check in checks[1:]:
        eligible = eligible & check
    return results_df.with_columns(eligible.alias("rate_eligible"))
