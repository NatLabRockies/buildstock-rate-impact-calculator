"""
Energy-burden calculations for ResStock results.

Energy burden = annual energy bill / annual household income. Income comes
from the dataset's binned income characteristic (e.g. ``in.income_recs_2020``,
whose enumerations are ``<20000``, ``20000-39999`` … ``150000+``,
``Not Available``); each bin is mapped to a point estimate:

* ``"lo-hi"``  → midpoint ``(lo + hi) / 2``
* ``"<hi"``    → ``hi / 2``
* ``"lo+"``    → ``lo × 1.25``

Thresholds follow DOE LEAD conventions: 6 % of income is a *high* energy
burden, 10 % is *severe*.
"""

from __future__ import annotations

import re

import polars as pl

# DOE LEAD energy-burden thresholds (fraction of household income).
HIGH_BURDEN = 0.06
SEVERE_BURDEN = 0.10

_RANGE_RE = re.compile(r"^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$")
_LESS_THAN_RE = re.compile(r"^<(\d+(?:\.\d+)?)$")
_PLUS_RE = re.compile(r"^(\d+(?:\.\d+)?)\+$")


def income_bin_midpoint(bin_label: str | None) -> float | None:
    """Point estimate of household income for a binned income label.

    Handles ``"20000-39999"`` (midpoint), ``"<20000"`` (half the bound), and
    ``"150000+"`` (bound × 1.25); ``$``, commas, and surrounding whitespace
    are stripped first. Returns ``None`` for anything unparseable (e.g.
    ``"Not Available"``).
    """
    if bin_label is None:
        return None
    cleaned = str(bin_label).strip().replace("$", "").replace(",", "")
    if not cleaned:
        return None
    if m := _RANGE_RE.match(cleaned):
        return (float(m.group(1)) + float(m.group(2))) / 2.0
    if m := _LESS_THAN_RE.match(cleaned):
        return float(m.group(1)) / 2.0
    if m := _PLUS_RE.match(cleaned):
        return float(m.group(1)) * 1.25
    return None


def add_energy_burden(
    results_df: pl.DataFrame,
    income_col: str,
    bill_col: str = "total_energy_bill",
) -> pl.DataFrame:
    """Add ``income_midpoint`` and ``energy_burden`` columns.

    ``energy_burden = bill / income_midpoint`` — null (never inf/NaN) when
    the bill is null, the income bin is unparseable, or the midpoint is
    non-positive.

    Args:
        results_df: Results frame carrying ``income_col``.
        income_col: Binned income column (e.g. ``in.income_recs_2020``).
        bill_col: Bill column to use; falls back to ``electricity_annual_bill`` when
            absent from the frame.

    Raises:
        ValueError: If ``income_col`` (or both bill candidates) are missing.
    """
    if income_col not in results_df.columns:
        raise ValueError(f"income column '{income_col}' not found in DataFrame.")
    if bill_col not in results_df.columns:
        if "electricity_annual_bill" not in results_df.columns:
            raise ValueError(
                f"Neither '{bill_col}' nor 'electricity_annual_bill' present in DataFrame."
            )
        bill_col = "electricity_annual_bill"

    # Map through the frame's unique labels so the parser runs once per bin.
    unique_labels = results_df[income_col].unique().to_list()
    midpoint_map = {lbl: income_bin_midpoint(lbl) for lbl in unique_labels}

    df = results_df.with_columns(
        pl.col(income_col)
        .replace_strict(midpoint_map, default=None, return_dtype=pl.Float64)
        .alias("income_midpoint")
    )
    return df.with_columns(
        pl.when(
            pl.col(bill_col).is_not_null()
            & pl.col("income_midpoint").is_not_null()
            & (pl.col("income_midpoint") > 0)
        )
        .then(pl.col(bill_col) / pl.col("income_midpoint"))
        .otherwise(None)
        .alias("energy_burden")
    )


def summarize_energy_burden(results_df: pl.DataFrame) -> pl.DataFrame:
    """Per-upgrade energy-burden statistics.

    Args:
        results_df: Frame from :func:`add_energy_burden` (must carry
            ``energy_burden``; ``weight`` defaults to 1.0 when absent;
            ``scenario`` is carried through when present).

    Returns:
        One row per upgrade with ``mean_burden``, ``median_burden``, and the
        weighted shares ``pct_high`` (burden ≥ 6 %) and ``pct_severe``
        (burden ≥ 10 %). Rows with a null burden are excluded.
    """
    if "energy_burden" not in results_df.columns:
        raise ValueError(
            "results_df must contain 'energy_burden' — run add_energy_burden first."
        )
    df = results_df.filter(pl.col("energy_burden").is_not_null())
    if "weight" not in df.columns:
        df = df.with_columns(pl.lit(1.0).alias("weight"))

    group_keys = ["upgrade"] + (["scenario"] if "scenario" in df.columns else [])
    w = pl.col("weight")
    grouped = df.group_by(group_keys).agg(
        [
            pl.col("energy_burden").mean().alias("mean_burden"),
            pl.col("energy_burden").median().alias("median_burden"),
            pl.when(pl.col("energy_burden") >= HIGH_BURDEN)
            .then(w)
            .otherwise(0.0)
            .sum()
            .alias("_w_high"),
            pl.when(pl.col("energy_burden") >= SEVERE_BURDEN)
            .then(w)
            .otherwise(0.0)
            .sum()
            .alias("_w_severe"),
            w.sum().alias("_w_total"),
            pl.col("bldg_id").count().alias("count"),
        ]
    )
    total_ok = pl.col("_w_total") > 0
    return (
        grouped.with_columns(
            [
                pl.when(total_ok)
                .then(pl.col("_w_high") / pl.col("_w_total"))
                .otherwise(None)
                .alias("pct_high"),
                pl.when(total_ok)
                .then(pl.col("_w_severe") / pl.col("_w_total"))
                .otherwise(None)
                .alias("pct_severe"),
            ]
        )
        .drop(["_w_high", "_w_severe", "_w_total"])
        .sort("upgrade")
    )
