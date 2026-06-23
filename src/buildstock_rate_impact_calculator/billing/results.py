"""
Results summarization and export utilities for BuildStock TOU bill calculations.

Functions:
  add_cost_per_sqft           — adds the per-row $/ft² column (ComStock frames)
  summarize_bills             — dataset-aware aggregation by upgrade + group
  compute_bill_savings        — per-building (baseline − upgrade) savings;
                                 ComStock also gets a per-sqft variant
  groupable_columns           — list of columns eligible for the results page
                                 grouping selector (filter ∪ additional cols
                                 present in the results df)
  filter_df_by_group_values   — apply a "pills" selection to the results df
                                 (empty selection → no-op)
  format_for_export           — serialize results DataFrame to CSV bytes
"""

from __future__ import annotations
import polars as pl
from typing import Iterable, Optional, Sequence

# Columns that represent per-building bill amounts (used for aggregation).
_BILL_COLUMNS = (
    "annual_bill",
    "natural_gas_bill",
    "propane_bill",
    "fuel_oil_bill",
    "total_energy_bill",
)

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


def add_cost_per_sqft(results_df: pl.DataFrame) -> pl.DataFrame:
    """Add a per-row ``cost_per_sqft`` column.

    ``cost_per_sqft = annual_bill / (weight × sqft)`` — ComStock
    ``annual_bill`` already bakes in the row weight, so dividing by
    ``weight × sqft`` strips it back to $/ft² of a single building.
    Rows missing any of ``annual_bill``, ``weight``, ``sqft``, or with a
    non-positive denominator yield ``None`` rather than ``inf``/``NaN``.

    If ``weight`` or ``sqft`` are absent from the schema (ResStock results or
    any older frame), the column is still added but is uniformly null —
    callers can rely on its presence after this call.
    """
    cols = set(results_df.columns)
    if not {"annual_bill", "weight", "sqft"}.issubset(cols):
        return results_df.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("cost_per_sqft")
        )

    denom = pl.col("weight") * pl.col("sqft")
    return results_df.with_columns(
        pl.when(
            pl.col("annual_bill").is_not_null()
            & pl.col("weight").is_not_null()
            & pl.col("sqft").is_not_null()
            & (denom > 0)
        )
        .then(pl.col("annual_bill") / denom)
        .otherwise(None)
        .alias("cost_per_sqft")
    )


def _summarize_bills_resstock(df: pl.DataFrame, group_keys: list[str]) -> pl.DataFrame:
    """ResStock branch of :func:`summarize_bills`.

    ``annual_bill`` is the per-building (single dwelling) dollar amount;
    ``weight`` is the scalar dataset weight repeated on every row.
    Means/medians/distributions are per-building (unweighted); totals are
    ``sum(weight × bill)``; ``units = sum(weight)`` (dwelling units).
    """
    agg_exprs = [
        pl.col("annual_bill").mean().alias("mean_annual_bill"),
        pl.col("annual_bill").median().alias("median_annual_bill"),
        (pl.col("annual_bill") * pl.col("weight")).sum().alias("total_annual_bill"),
        pl.col("weight").sum().alias("units"),
        pl.col("bldg_id").count().alias("count"),
    ]
    if "annual_energy_kwh" in df.columns:
        agg_exprs.append(
            pl.col("annual_energy_kwh").mean().alias("mean_annual_energy_kwh")
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


def _summarize_bills_comstock(df: pl.DataFrame, group_keys: list[str]) -> pl.DataFrame:
    """ComStock branch of :func:`summarize_bills`.

    ``annual_bill`` is already weighted real-world dollars (``load × weight``
    was applied before PySAM ran). The per-building
    ``cost_per_sqft = annual_bill / (weight × sqft)`` is computed first and
    is what means/medians/distributions are reported over. Totals are direct
    sums (``annual_bill`` already weighted; ``total_sqft = sum(weight × sqft)``).
    """
    df = add_cost_per_sqft(df)

    agg_exprs = [
        pl.col("cost_per_sqft").mean().alias("mean_per_sqft"),
        pl.col("cost_per_sqft").median().alias("median_per_sqft"),
        pl.col("annual_bill").sum().alias("total_annual_bill"),
        (pl.col("weight") * pl.col("sqft")).sum().alias("total_sqft"),
        pl.col("weight").sum().alias("units"),
        pl.col("bldg_id").count().alias("count"),
    ]
    if "annual_energy_kwh" in df.columns:
        agg_exprs.append(
            pl.col("annual_energy_kwh").mean().alias("mean_annual_energy_kwh")
        )
    for fuel_col in (
        "natural_gas_bill",
        "propane_bill",
        "fuel_oil_bill",
        "total_energy_bill",
    ):
        if fuel_col in df.columns:
            # ComStock fuel bills are already weighted (see _calculate_fuel_bills
            # in page_05_run). Means are reported on weighted dollars, totals
            # are direct sums.
            agg_exprs.append(pl.col(fuel_col).mean().alias(f"mean_{fuel_col}"))
            agg_exprs.append(pl.col(fuel_col).sum().alias(f"total_{fuel_col}"))
    return df.group_by(group_keys).agg(agg_exprs).sort(group_keys)


def summarize_bills(
    results_df: pl.DataFrame,
    dataset_type: str,
    group_by: Optional[str] = None,
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

    Returns:
        Polars DataFrame keyed by ``(group_by?, upgrade)`` with columns
        appropriate to the dataset:

        * **ResStock**: ``mean_annual_bill``, ``median_annual_bill``,
          ``total_annual_bill`` (weighted), ``units`` (= sum-of-weights),
          plus fuel/total bill means and weighted totals when present.
        * **ComStock**: ``mean_per_sqft``, ``median_per_sqft``,
          ``total_annual_bill``, ``total_sqft``, ``units``, plus fuel-bill
          means and totals when present.

    Raises:
        ValueError: If a required column or the ``group_by`` column is missing.
    """
    if "upgrade" not in results_df.columns:
        raise ValueError("results_df must contain an 'upgrade' column.")
    if "annual_bill" not in results_df.columns:
        raise ValueError("results_df must contain an 'annual_bill' column.")
    if "weight" not in results_df.columns:
        raise ValueError("results_df must contain a 'weight' column.")

    df = results_df.filter(pl.col("annual_bill").is_not_null())

    group_keys = ["upgrade"]
    if group_by is not None:
        if group_by not in df.columns:
            raise ValueError(f"group_by column '{group_by}' not found in DataFrame.")
        group_keys = [group_by, "upgrade"]

    ds = (dataset_type or "").lower()
    if ds == "comstock":
        return _summarize_bills_comstock(df, group_keys)
    return _summarize_bills_resstock(df, group_keys)


def compute_bill_savings(
    results_df: pl.DataFrame,
    baseline_upgrade: int = 0,
) -> pl.DataFrame:
    """Compute per-building bill savings vs the baseline upgrade.

    ``bill_savings = baseline_bill - annual_bill`` — positive means the
    upgrade is cheaper than the baseline (savings). This is the opposite
    sign convention from the legacy ``compute_bill_diff``.

    For ComStock frames that already carry ``cost_per_sqft`` (run through
    :func:`add_cost_per_sqft` beforehand), a parallel
    ``bill_savings_per_sqft = baseline_cost_per_sqft - cost_per_sqft`` column
    is emitted.

    Args:
        results_df: Must contain ``bldg_id``, ``upgrade``, ``annual_bill``.
            For per-sqft savings, must also contain ``cost_per_sqft``. When
            a ``_row_uid`` column is present the baseline join also includes
            it so ComStock per-row geographic aggregates line up.
        baseline_upgrade: The upgrade ID to treat as baseline (default 0).

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

    baseline_sel: list = [pl.col("annual_bill").alias("baseline_bill")]
    if has_per_sqft:
        baseline_sel.append(pl.col("cost_per_sqft").alias("baseline_cost_per_sqft"))
    baseline = results_df.filter(pl.col("upgrade") == baseline_upgrade).select(
        join_keys + baseline_sel
    )

    non_baseline = results_df.filter(pl.col("upgrade") != baseline_upgrade)
    joined = non_baseline.join(baseline, on=join_keys, how="left")

    new_cols = [(pl.col("baseline_bill") - pl.col("annual_bill")).alias("bill_savings")]
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


def groupable_columns(
    df: pl.DataFrame,
    filter_columns: Optional[Iterable[str]] = None,
    additional_columns: Optional[Iterable[str]] = None,
) -> list[str]:
    """Return the columns eligible for the results-page grouping selector.

    Combines the dataset's configured ``filter_columns`` with the user's
    ``additional_columns`` selection, keeps only those actually present in
    ``df``, dedupes, and preserves order (filter columns first, then
    additional columns). Either argument may be ``None``.

    Args:
        df: The results DataFrame whose columns gate the result.
        filter_columns: Columns the dataset configures as filterable
            (``DatasetConfig.filter_columns``).
        additional_columns: User-selected extra metadata columns
            (``AppState.additional_columns``).

    Returns:
        Ordered list of column names suitable for ``st.selectbox`` options.
    """
    available = set(df.columns)
    ordered: list[str] = []
    seen: set[str] = set()
    for source in (filter_columns or (), additional_columns or ()):
        for col in source:
            if col in available and col not in seen:
                ordered.append(col)
                seen.add(col)
    return ordered


def filter_df_by_group_values(
    df: pl.DataFrame,
    group_col: Optional[str],
    selected: Sequence,
) -> pl.DataFrame:
    """Apply a results-page pill selection to the results DataFrame.

    Args:
        df: Results DataFrame.
        group_col: The grouping column the user picked, or ``None`` if no
            grouping is active.
        selected: Values toggled in ``st.pills``; an empty list means "no
            filter — show everything".

    Returns:
        Filtered DataFrame, or ``df`` unchanged when no filter applies
        (no group column, no selected values, or group column not present
        in ``df``).
    """
    if not group_col or not selected or group_col not in df.columns:
        return df
    return df.filter(pl.col(group_col).is_in(list(selected)))
