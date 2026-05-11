"""
Altair chart components for BuildStock TOU Calculator results visualization.
"""
from __future__ import annotations
from typing import Optional

import altair as alt
import polars as pl


def render_bill_comparison_bar(
    summary_df: pl.DataFrame,
    group_col: Optional[str] = None,
    upgrade_col: str = "upgrade",
    bill_col: str = "mean_annual_bill",
) -> alt.Chart:
    """
    Create a grouped bar chart comparing mean annual bills across scenarios.

    Args:
        summary_df: Summarized results from summarize_bills().
        group_col: Column to use for x-axis grouping (e.g. 'building_type', 'state').
                   If None, uses upgrade as the x-axis.
        upgrade_col: Column with upgrade IDs.
        bill_col: Column with the bill value to plot.

    Returns:
        Altair Chart object.
    """
    pandas_df = summary_df.to_pandas()
    pandas_df[upgrade_col] = pandas_df[upgrade_col].astype(str)

    if group_col and group_col in pandas_df.columns:
        x = alt.X(f"{group_col}:N", title=group_col.replace("_", " ").title())
        color = alt.Color(f"{upgrade_col}:N", title="Upgrade")
        x_offset = alt.XOffset(f"{upgrade_col}:N")
    else:
        x = alt.X(f"{upgrade_col}:N", title="Upgrade Scenario")
        color = alt.Color(f"{upgrade_col}:N", title="Upgrade")
        x_offset = None

    bar = alt.Chart(pandas_df).mark_bar().encode(
        x=x,
        y=alt.Y(f"{bill_col}:Q", title="Mean Annual Bill ($)"),
        color=color,
        tooltip=[
            alt.Tooltip(f"{upgrade_col}:N", title="Upgrade"),
            alt.Tooltip(f"{bill_col}:Q", title="Mean Bill ($)", format="$.2f"),
        ],
    )

    if x_offset is not None:
        bar = bar.encode(xOffset=x_offset)

    return bar.properties(
        title="Mean Annual Bill by Scenario",
        width=600,
        height=350,
    ).interactive()


def render_bill_distribution(
    results_df: pl.DataFrame,
    group_col: Optional[str] = None,
    upgrade_col: str = "upgrade",
    bill_col: str = "annual_bill",
) -> alt.Chart:
    """
    Create a box plot showing the distribution of annual bills by upgrade scenario.

    Args:
        results_df: Raw results DataFrame from calculate_batch_bills().
        group_col: Optional column to facet by (e.g. 'building_type').
        upgrade_col: Column with upgrade IDs.
        bill_col: Column with per-building annual bill values.

    Returns:
        Altair Chart object.
    """
    df = results_df.filter(pl.col(bill_col).is_not_null())
    pandas_df = df.to_pandas()
    pandas_df[upgrade_col] = pandas_df[upgrade_col].astype(str)

    box = alt.Chart(pandas_df).mark_boxplot(extent="min-max").encode(
        x=alt.X(f"{upgrade_col}:N", title="Upgrade Scenario"),
        y=alt.Y(f"{bill_col}:Q", title="Annual Bill ($)"),
        color=alt.Color(f"{upgrade_col}:N", title="Upgrade"),
        tooltip=[
            alt.Tooltip(f"{upgrade_col}:N", title="Upgrade"),
            alt.Tooltip(f"{bill_col}:Q", title="Annual Bill ($)", format="$.2f"),
        ],
    )

    if group_col and group_col in pandas_df.columns:
        box = box.facet(
            facet=alt.Facet(f"{group_col}:N", title=group_col.replace("_", " ").title()),
            columns=3,
        ).properties(title="Annual Bill Distribution by Scenario")
    else:
        box = box.properties(
            title="Annual Bill Distribution by Scenario",
            width=600,
            height=350,
        )

    return box.interactive()
