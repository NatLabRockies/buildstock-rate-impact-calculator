"""
Results summarization and export utilities for BuildStock TOU bill calculations.

Functions:
  bill_source_options  — {display label: bill column} for the energy sources
                          present on a results frame
  with_selected_bill   — adds a per-row sum of the selected bill columns
  add_cost_per_sqft    — adds the per-row $/ft² column (ComStock-style frames)
  summarize_bills      — dataset-aware aggregation by upgrade and optional group
  compute_bill_savings — per-building (baseline − upgrade) savings; ComStock
                          also gets a per-sqft variant
  format_for_export    — serialize results DataFrame to CSV bytes
"""

from __future__ import annotations
import polars as pl
from typing import Optional, Sequence

# Columns that represent per-building bill amounts (used for aggregation).
_BILL_COLUMNS = (
    "annual_bill",
    "natural_gas_bill",
    "propane_bill",
    "fuel_oil_bill",
    "total_energy_bill",
)

# Energy-source bill columns selectable in the results UI, in display order.
# ``annual_bill`` is the PySAM electricity bill; the fuel columns come from
# ``_calculate_fuel_bills`` in page_05_run.
BILL_SOURCE_LABELS: dict[str, str] = {
    "annual_bill": "Electricity",
    "natural_gas_bill": "Natural Gas",
    "propane_bill": "Propane",
    "fuel_oil_bill": "Fuel Oil",
}

# Non-grouping, non-bill metadata columns excluded from the group-by selector
_META_COLUMNS = frozenset(
    {
        "bldg_id",
        "upgrade",
        "scenario",
        "annual_energy_kwh",
        "average_rate_ct_kwh",
        "monthly_bills",
        "error",
        "baseline_bill",
        "bill_diff",
        "bill_savings",
        "bill_savings_per_sqft",
        "baseline_cost_per_sqft",
        "natural_gas_rate_name",
        "propane_rate_name",
        "fuel_oil_rate_name",
        "applicable",
        # Per-row weighting plumbing (Phase 2: weight-aware billing pipeline)
        "weight",
        "sqft",
        "cost_per_sqft",
        "_row_uid",
    }
    | set(_BILL_COLUMNS)
)


def bill_source_options(results_df: pl.DataFrame) -> dict[str, str]:
    """Return ``{display label: bill column}`` for the energy sources present
    on ``results_df``, in canonical display order (Electricity first)."""
    return {
        label: col
        for col, label in BILL_SOURCE_LABELS.items()
        if col in results_df.columns
    }


def with_selected_bill(
    results_df: pl.DataFrame,
    bill_columns: Sequence[str],
    alias: str = "selected_bill",
) -> pl.DataFrame:
    """Add a per-row sum of the selected bill columns as ``alias``.

    Fuel-bill columns may be null for buildings without that fuel; nulls are
    treated as $0 in the sum. Columns absent from the frame are skipped.

    Raises:
        ValueError: If none of ``bill_columns`` exist on the frame.
    """
    present = [c for c in bill_columns if c in results_df.columns]
    if not present:
        raise ValueError(
            f"None of the selected bill columns {list(bill_columns)} are present "
            f"in the results DataFrame."
        )
    expr = pl.col(present[0]).cast(pl.Float64).fill_null(0.0)
    for col in present[1:]:
        expr = expr + pl.col(col).cast(pl.Float64).fill_null(0.0)
    return results_df.with_columns(expr.alias(alias))


def _per_sqft_expr(bill_col: str) -> pl.Expr:
    """Per-row ``bill / (weight × sqft)`` expression, null-safe (no inf/NaN)."""
    denom = pl.col("weight") * pl.col("sqft")
    return (
        pl.when(
            pl.col(bill_col).is_not_null()
            & pl.col("weight").is_not_null()
            & pl.col("sqft").is_not_null()
            & (denom > 0)
        )
        .then(pl.col(bill_col) / denom)
        .otherwise(None)
    )


def add_cost_per_sqft(
    results_df: pl.DataFrame,
    bill_col: str = "annual_bill",
    out_col: str = "cost_per_sqft",
) -> pl.DataFrame:
    """Add a per-row ``cost_per_sqft`` column.

    ``cost_per_sqft = bill / (weight × sqft)`` — ComStock bills already bake
    in the row weight, so dividing by ``weight × sqft`` strips it back to
    $/ft² of a single building. Rows missing any of ``bill_col``, ``weight``,
    ``sqft``, or with a non-positive denominator yield ``None`` rather than
    ``inf``/``NaN``.

    If ``bill_col``, ``weight``, or ``sqft`` are absent from the schema
    (ResStock results or any older frame), the column is still added but is
    uniformly null — callers can rely on its presence after this call.
    """
    cols = set(results_df.columns)
    if not {bill_col, "weight", "sqft"}.issubset(cols):
        return results_df.with_columns(pl.lit(None, dtype=pl.Float64).alias(out_col))
    return results_df.with_columns(_per_sqft_expr(bill_col).alias(out_col))


def _summarize_bills_resstock(
    df: pl.DataFrame, group_keys: list[str], bill_expr: pl.Expr
) -> pl.DataFrame:
    """ResStock branch of :func:`summarize_bills`.

    ``bill_expr`` is the per-building (single dwelling) dollar amount for the
    selected energy sources; ``weight`` is the scalar dataset weight repeated
    on every row. Means/medians/distributions are per-building (unweighted);
    totals are ``sum(weight × bill)``; ``units = sum(weight)`` (dwelling units).
    """
    agg_exprs = [
        bill_expr.mean().alias("mean_annual_bill"),
        bill_expr.median().alias("median_annual_bill"),
        (bill_expr * pl.col("weight")).sum().alias("total_annual_bill"),
        pl.col("weight").sum().alias("units"),
        pl.col("bldg_id").count().alias("count"),
    ]
    if "annual_energy_kwh" in df.columns:
        agg_exprs.append(
            pl.col("annual_energy_kwh").mean().alias("mean_annual_energy_kwh")
        )
    # Per-source means/totals for the "By fuel" view. Electricity gets a
    # distinct alias because ``mean_annual_bill`` above reflects the selected
    # sources, not necessarily electricity alone.
    agg_exprs.append(pl.col("annual_bill").mean().alias("mean_electricity_bill"))
    agg_exprs.append(
        (pl.col("annual_bill") * pl.col("weight")).sum().alias("total_electricity_bill")
    )
    for fuel_col in (
        "natural_gas_bill",
        "propane_bill",
        "fuel_oil_bill",
        "total_energy_bill",
    ):
        if fuel_col in df.columns:
            agg_exprs.append(pl.col(fuel_col).mean().alias(f"mean_{fuel_col}"))
            agg_exprs.append(
                (pl.col(fuel_col) * pl.col("weight")).sum().alias(f"total_{fuel_col}")
            )
    return df.group_by(group_keys).agg(agg_exprs).sort(group_keys)


def _summarize_bills_comstock(
    df: pl.DataFrame, group_keys: list[str], bill_expr: pl.Expr
) -> pl.DataFrame:
    """ComStock branch of :func:`summarize_bills`.

    Bills are already weighted real-world dollars (``load × weight`` was
    applied before PySAM ran; fuel bills were weighted in
    ``_calculate_fuel_bills``). The per-building
    ``cost_per_sqft = bill / (weight × sqft)`` of the selected sources is
    computed first and is what means/medians/distributions are reported over.
    Totals are direct sums (bills already weighted;
    ``total_sqft = sum(weight × sqft)``). Per-source $/ft² means
    (``mean_{col}_per_sqft``) are emitted for the "By fuel" stacked-bar view.
    """
    df = df.with_columns(bill_expr.alias("_sel_bill"))
    df = add_cost_per_sqft(df, bill_col="_sel_bill")

    agg_exprs = [
        pl.col("cost_per_sqft").mean().alias("mean_per_sqft"),
        pl.col("cost_per_sqft").median().alias("median_per_sqft"),
        pl.col("_sel_bill").sum().alias("total_annual_bill"),
        (pl.col("weight") * pl.col("sqft")).sum().alias("total_sqft"),
        pl.col("weight").sum().alias("units"),
        pl.col("bldg_id").count().alias("count"),
    ]
    if "annual_energy_kwh" in df.columns:
        agg_exprs.append(
            pl.col("annual_energy_kwh").mean().alias("mean_annual_energy_kwh")
        )
    for bill_col in _BILL_COLUMNS:
        if bill_col in df.columns:
            # Weighted-dollar means are not meaningful per building, so
            # per-source stats are reported as $/ft²; totals are direct sums.
            # Electricity (annual_bill) gets a distinct alias so it can't
            # collide with the selected-sources headline columns.
            src = "electricity_bill" if bill_col == "annual_bill" else bill_col
            agg_exprs.append(
                _per_sqft_expr(bill_col).mean().alias(f"mean_{src}_per_sqft")
            )
            agg_exprs.append(pl.col(bill_col).sum().alias(f"total_{src}"))
    return df.group_by(group_keys).agg(agg_exprs).sort(group_keys)


def summarize_bills(
    results_df: pl.DataFrame,
    dataset_type: str,
    group_by: Optional[str] = None,
    bill_columns: Optional[Sequence[str]] = None,
) -> pl.DataFrame:
    """Aggregate bill statistics by upgrade scenario, dispatched on dataset.

    Args:
        results_df: DataFrame from :func:`calculate_batch_bills` joined with
            fuel/metadata columns. Must contain ``bldg_id``, ``upgrade``,
            ``annual_bill``, and ``weight``.
        dataset_type: ``"resstock"`` or ``"comstock"``. Controls whether
            statistics are reported on per-building dollars (ResStock) or
            on per-building $/ft² (ComStock).
        group_by: Optional metadata column name to break the summary out by
            (e.g. ``"in.vintage"``, ``"in.state"``).
        bill_columns: Energy-source bill columns whose per-row sum drives the
            headline statistics (see :data:`BILL_SOURCE_LABELS`). Defaults to
            ``["annual_bill"]`` (electricity only). Columns absent from the
            frame are skipped; fuel nulls are treated as $0.

    Returns:
        Polars DataFrame keyed by ``(group_by?, upgrade)`` with columns
        appropriate to the dataset:

        * **ResStock**: ``mean_annual_bill``, ``median_annual_bill``,
          ``total_annual_bill`` (weighted), ``units`` (= sum-of-weights) —
          all over the selected sources — plus per-source means
          (``mean_electricity_bill``, ``mean_<fuel>_bill``) and weighted
          totals when present.
        * **ComStock**: ``mean_per_sqft``, ``median_per_sqft``,
          ``total_annual_bill``, ``total_sqft``, ``units`` over the selected
          sources, plus per-source ``mean_<col>_per_sqft`` and ``total_<col>``
          when present.

    Raises:
        ValueError: If a required column or the ``group_by`` column is missing.
    """
    if "upgrade" not in results_df.columns:
        raise ValueError("results_df must contain an 'upgrade' column.")
    if "annual_bill" not in results_df.columns:
        raise ValueError("results_df must contain an 'annual_bill' column.")
    if "weight" not in results_df.columns:
        raise ValueError("results_df must contain a 'weight' column.")

    # Error rows (failed PySAM calc) carry a null electricity bill; drop them
    # from statistics regardless of which sources are selected.
    df = results_df.filter(pl.col("annual_bill").is_not_null())

    selected = [c for c in (bill_columns or ["annual_bill"]) if c in results_df.columns]
    if not selected:
        raise ValueError(
            f"None of the selected bill columns {list(bill_columns or [])} are "
            f"present in the results DataFrame."
        )
    bill_expr = pl.col(selected[0]).cast(pl.Float64).fill_null(0.0)
    for col in selected[1:]:
        bill_expr = bill_expr + pl.col(col).cast(pl.Float64).fill_null(0.0)

    group_keys = ["upgrade"]
    if group_by is not None:
        if group_by not in df.columns:
            raise ValueError(f"group_by column '{group_by}' not found in DataFrame.")
        group_keys = [group_by, "upgrade"]

    ds = (dataset_type or "").lower()
    if ds == "comstock":
        return _summarize_bills_comstock(df, group_keys, bill_expr)
    return _summarize_bills_resstock(df, group_keys, bill_expr)


def compute_bill_savings(
    results_df: pl.DataFrame,
    baseline_upgrade: int = 0,
    bill_col: str = "annual_bill",
) -> pl.DataFrame:
    """Compute per-building bill savings vs the baseline upgrade.

    ``bill_savings = baseline_bill - bill`` — positive means the
    upgrade is cheaper than the baseline (savings). This is the opposite
    sign convention from the legacy ``compute_bill_diff``.

    For ComStock frames that already carry ``cost_per_sqft`` (run through
    :func:`add_cost_per_sqft` beforehand), a parallel
    ``bill_savings_per_sqft = baseline_cost_per_sqft - cost_per_sqft`` column
    is emitted.

    Args:
        results_df: Must contain ``bldg_id``, ``upgrade``, and ``bill_col``.
            For per-sqft savings, must also contain ``cost_per_sqft``. When
            a ``_row_uid`` column is present the baseline join also includes
            it so ComStock per-row geographic aggregates line up.
        baseline_upgrade: The upgrade ID to treat as baseline (default 0).
        bill_col: The per-row bill column to difference (default
            ``annual_bill``; pass ``selected_bill`` after
            :func:`with_selected_bill` to cover multiple energy sources).

    Returns:
        Non-baseline rows joined with ``baseline_bill`` and ``bill_savings``
        (and ``baseline_cost_per_sqft`` / ``bill_savings_per_sqft`` when
        ``cost_per_sqft`` is present on the input).

    Raises:
        ValueError: If ``baseline_upgrade`` is not present in the DataFrame.
    """
    if baseline_upgrade not in results_df["upgrade"].unique().to_list():
        raise ValueError(
            f"baseline upgrade {baseline_upgrade} not found in results. "
            f"Available upgrades: {sorted(results_df['upgrade'].unique().to_list())}"
        )

    has_per_sqft = "cost_per_sqft" in results_df.columns
    # ComStock per-row uid lets a single bldg_id span multiple geographic
    # aggregates with distinct (weight, sqft); join on it when available.
    join_keys = ["bldg_id"]
    if "_row_uid" in results_df.columns:
        join_keys.append("_row_uid")

    baseline_sel: list = [pl.col(bill_col).alias("baseline_bill")]
    if has_per_sqft:
        baseline_sel.append(pl.col("cost_per_sqft").alias("baseline_cost_per_sqft"))
    baseline = results_df.filter(pl.col("upgrade") == baseline_upgrade).select(
        join_keys + baseline_sel
    )

    non_baseline = results_df.filter(pl.col("upgrade") != baseline_upgrade)
    joined = non_baseline.join(baseline, on=join_keys, how="left")

    new_cols = [(pl.col("baseline_bill") - pl.col(bill_col)).alias("bill_savings")]
    if has_per_sqft:
        new_cols.append(
            (pl.col("baseline_cost_per_sqft") - pl.col("cost_per_sqft")).alias(
                "bill_savings_per_sqft"
            )
        )
    return joined.with_columns(new_cols)


def format_for_export(results_df: pl.DataFrame) -> bytes:
    """
    Serialize a results DataFrame to CSV bytes for download.

    Args:
        results_df: Any Polars DataFrame (typically from calculate_batch_bills
                    or summarize_bills).

    Returns:
        UTF-8 encoded CSV bytes.
    """
    return results_df.write_csv().encode("utf-8")
