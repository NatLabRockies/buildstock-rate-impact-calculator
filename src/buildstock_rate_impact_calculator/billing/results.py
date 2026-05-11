"""
Results summarization and export utilities for BuildStock TOU bill calculations.

Functions:
  summarize_bills   — aggregate mean/median bill stats by upgrade and optional group
  compute_bill_diff — compute signed bill difference vs baseline for each building
  format_for_export — serialize results DataFrame to CSV bytes
"""
from __future__ import annotations
import polars as pl
from typing import Optional


def summarize_bills(
    results_df: pl.DataFrame,
    group_by: Optional[str] = None,
) -> pl.DataFrame:
    """
    Aggregate bill statistics by upgrade scenario and an optional grouping column.

    Args:
        results_df: DataFrame from calculate_batch_bills, must contain columns
                    ``bldg_id``, ``upgrade``, ``annual_bill``.
        group_by: Optional column name to group by (e.g. 'building_type', 'state').
                  If None, aggregates across all buildings per upgrade.

    Returns:
        Polars DataFrame with columns:
          - upgrade
          - group_by column (if provided)
          - mean_annual_bill, median_annual_bill, total_annual_bill
          - mean_annual_energy_kwh (if column present)
          - count
    """
    if "upgrade" not in results_df.columns:
        raise ValueError("results_df must contain an 'upgrade' column.")
    if "annual_bill" not in results_df.columns:
        raise ValueError("results_df must contain an 'annual_bill' column.")

    # Only aggregate non-null rows
    df = results_df.filter(pl.col("annual_bill").is_not_null())

    group_keys = ["upgrade"]
    if group_by is not None:
        if group_by not in df.columns:
            raise ValueError(f"group_by column '{group_by}' not found in DataFrame.")
        group_keys = [group_by, "upgrade"]

    agg_exprs = [
        pl.col("annual_bill").mean().alias("mean_annual_bill"),
        pl.col("annual_bill").median().alias("median_annual_bill"),
        pl.col("annual_bill").sum().alias("total_annual_bill"),
        pl.col("bldg_id").count().alias("count"),
    ]
    if "annual_energy_kwh" in df.columns:
        agg_exprs.append(pl.col("annual_energy_kwh").mean().alias("mean_annual_energy_kwh"))

    return df.group_by(group_keys).agg(agg_exprs).sort(group_keys)


def compute_bill_diff(
    results_df: pl.DataFrame,
    baseline_upgrade: int = 0,
) -> pl.DataFrame:
    """
    Compute the signed bill difference (upgrade - baseline) per building.

    Args:
        results_df: DataFrame from calculate_batch_bills. Must have columns
                    ``bldg_id``, ``upgrade``, ``annual_bill``.
        baseline_upgrade: The upgrade ID to treat as baseline (default 0).

    Returns:
        DataFrame with columns: bldg_id, upgrade, annual_bill, baseline_bill, bill_diff.
        Excludes the baseline upgrade rows.

    Raises:
        ValueError: If baseline_upgrade is not present in the DataFrame.
    """
    if baseline_upgrade not in results_df["upgrade"].unique().to_list():
        raise ValueError(
            f"baseline upgrade {baseline_upgrade} not found in results. "
            f"Available upgrades: {sorted(results_df['upgrade'].unique().to_list())}"
        )

    baseline = (
        results_df
        .filter(pl.col("upgrade") == baseline_upgrade)
        .select(["bldg_id", pl.col("annual_bill").alias("baseline_bill")])
    )

    non_baseline = results_df.filter(pl.col("upgrade") != baseline_upgrade)

    joined = non_baseline.join(baseline, on="bldg_id", how="left")
    joined = joined.with_columns(
        (pl.col("annual_bill") - pl.col("baseline_bill")).alias("bill_diff")
    )
    return joined


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
