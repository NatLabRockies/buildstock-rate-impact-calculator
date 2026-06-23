"""
Altair chart components for BuildStock TOU Calculator results visualization.
"""

from __future__ import annotations
from typing import Mapping, Optional

import altair as alt
import polars as pl


def render_bill_comparison_bar(
    summary_df: pl.DataFrame,
    group_col: Optional[str] = None,
    upgrade_col: str = "upgrade",
    bill_col: str = "mean_annual_bill",
    value_format: str = "$.0f",
    y_axis_title: Optional[str] = None,
    chart_title: str = "Mean Annual Bill by Scenario",
) -> alt.Chart:
    """
    Create a grouped bar chart comparing mean bills across scenarios.

    Args:
        summary_df: Summarized results from ``summarize_bills``.
        group_col: Column to use for x-axis grouping (e.g. 'building_type',
            'state'). If None, uses upgrade as the x-axis.
        upgrade_col: Column with upgrade IDs.
        bill_col: Column with the bill value to plot.
        value_format: D3 numeric format for tooltips / axis labels. Default
            ``"$.0f"`` for whole-dollar amounts; pass ``"$.2f"`` for $/ft².
        y_axis_title: Optional override for the y-axis label.
        chart_title: Chart title text.

    Returns:
        Altair Chart object.
    """
    pandas_df = summary_df.to_pandas()
    pandas_df[upgrade_col] = pandas_df[upgrade_col].astype(str)

    if y_axis_title is None:
        y_axis_title = (
            "Mean Bill ($/ft²)" if "per_sqft" in bill_col else "Mean Annual Bill ($)"
        )

    if group_col and group_col in pandas_df.columns:
        x = alt.X(f"{group_col}:N", title=group_col.replace("_", " ").title())
        color = alt.Color(f"{upgrade_col}:N", title="Upgrade")
        x_offset = alt.XOffset(f"{upgrade_col}:N")
    else:
        x = alt.X(f"{upgrade_col}:N", title="Upgrade Scenario")
        color = alt.Color(f"{upgrade_col}:N", title="Upgrade")
        x_offset = None

    bar = (
        alt.Chart(pandas_df)
        .mark_bar()
        .encode(
            x=x,
            y=alt.Y(
                f"{bill_col}:Q",
                title=y_axis_title,
                axis=alt.Axis(format=value_format),
            ),
            color=color,
            tooltip=[
                alt.Tooltip(f"{upgrade_col}:N", title="Upgrade"),
                alt.Tooltip(f"{bill_col}:Q", title=y_axis_title, format=value_format),
            ],
        )
    )

    if x_offset is not None:
        bar = bar.encode(xOffset=x_offset)

    return bar.properties(
        title=chart_title,
        width=600,
        height=350,
    ).interactive()


def render_bill_distribution(
    results_df: pl.DataFrame,
    group_col: Optional[str] = None,
    upgrade_col: str = "upgrade",
    bill_col: str = "annual_bill",
    value_format: str = "$.0f",
    y_axis_title: str = "Annual Bill ($)",
) -> alt.Chart:
    """
    Create a box plot showing the distribution of annual bills by upgrade scenario.

    Args:
        results_df: Raw results DataFrame from ``calculate_batch_bills``.
        group_col: Optional column to facet by (e.g. 'building_type').
        upgrade_col: Column with upgrade IDs.
        bill_col: Column with per-building annual bill values.
        value_format: D3 numeric format for tooltips / axis labels.
        y_axis_title: Y-axis label.

    Returns:
        Altair Chart object.
    """
    df = results_df.filter(pl.col(bill_col).is_not_null())
    pandas_df = df.to_pandas()
    pandas_df[upgrade_col] = pandas_df[upgrade_col].astype(str)

    box = (
        alt.Chart(pandas_df)
        .mark_boxplot(extent="min-max")
        .encode(
            x=alt.X(f"{upgrade_col}:N", title="Upgrade Scenario"),
            y=alt.Y(
                f"{bill_col}:Q",
                title=y_axis_title,
                axis=alt.Axis(format=value_format),
            ),
            color=alt.Color(f"{upgrade_col}:N", title="Upgrade"),
            tooltip=[
                alt.Tooltip(f"{upgrade_col}:N", title="Upgrade"),
                alt.Tooltip(f"{bill_col}:Q", title=y_axis_title, format=value_format),
            ],
        )
    )

    if group_col and group_col in pandas_df.columns:
        box = box.facet(
            facet=alt.Facet(
                f"{group_col}:N", title=group_col.replace("_", " ").title()
            ),
            columns=3,
        ).properties(title="Annual Bill Distribution by Scenario")
    else:
        box = box.properties(
            title="Annual Bill Distribution by Scenario",
            width=600,
            height=350,
        )

    return box.interactive()


def melt_fuels_to_long(
    summary_df: pl.DataFrame,
    fuel_columns: Mapping[str, str],
) -> pl.DataFrame:
    """Pivot wide per-fuel summary columns into long form for stacked bars.

    Args:
        summary_df: Wide-form summary DataFrame from ``summarize_bills``,
            typically containing columns such as ``scenario``, ``upgrade``,
            ``mean_annual_bill`` (electricity), ``mean_natural_gas_bill``,
            ``mean_propane_bill``, and ``mean_fuel_oil_bill``.
        fuel_columns: Mapping from wide column name to display fuel label
            (e.g. ``{"mean_annual_bill": "Electricity", ...}``). Columns
            missing from ``summary_df`` are silently skipped.

    Returns:
        Long-form DataFrame with id columns preserved plus ``fuel_type``
        and ``value`` columns.
    """
    present_cols = [c for c in fuel_columns if c in summary_df.columns]
    if not present_cols:
        return pl.DataFrame(schema={"fuel_type": pl.Utf8, "value": pl.Float64})

    id_vars = [c for c in summary_df.columns if c not in present_cols]

    long = summary_df.unpivot(
        on=present_cols,
        index=id_vars,
        variable_name="fuel_column",
        value_name="value",
    )

    label_map = {c: fuel_columns[c] for c in present_cols}
    return long.with_columns(
        pl.col("fuel_column").replace_strict(label_map).alias("fuel_type")
    ).drop("fuel_column")


def render_bill_stack_bar(
    summary_long_df: pl.DataFrame,
    value_col: str = "value",
    group_col: Optional[str] = None,
    scenario_col: str = "scenario",
    fuel_col: str = "fuel_type",
    value_format: str = "$.0f",
    y_axis_title: Optional[str] = None,
    chart_title: str = "Bill Composition by Fuel",
) -> alt.Chart:
    """Render a stacked bar chart of bill amounts by scenario × fuel type.

    Args:
        summary_long_df: Long-form DataFrame from :func:`melt_fuels_to_long`
            with at least ``scenario``, ``fuel_type``, and the value column.
        value_col: Numeric column to stack on the y-axis.
        group_col: Optional column to facet by (one stacked bar group per
            value). If None, a single set of bars is drawn with scenario on
            the x-axis.
        scenario_col: Column holding the scenario label.
        fuel_col: Column holding the fuel type label.
        value_format: D3 numeric format for tooltips / axis labels.
        y_axis_title: Optional y-axis label override.
        chart_title: Chart title text.

    Returns:
        Altair Chart object.
    """
    if y_axis_title is None:
        y_axis_title = "Bill ($/ft²)" if "per_sqft" in value_col else "Bill ($)"

    pandas_df = summary_long_df.to_pandas()

    encodings: dict[str, object] = {
        "x": alt.X(f"{scenario_col}:N", title="Scenario"),
        "y": alt.Y(
            f"{value_col}:Q",
            stack="zero",
            title=y_axis_title,
            axis=alt.Axis(format=value_format),
        ),
        "color": alt.Color(f"{fuel_col}:N", title="Fuel"),
        "tooltip": [
            alt.Tooltip(f"{scenario_col}:N", title="Scenario"),
            alt.Tooltip(f"{fuel_col}:N", title="Fuel"),
            alt.Tooltip(f"{value_col}:Q", title=y_axis_title, format=value_format),
        ],
    }

    if group_col and group_col in pandas_df.columns:
        encodings["column"] = alt.Column(
            f"{group_col}:N", title=group_col.replace("_", " ").title()
        )

    bar = alt.Chart(pandas_df).mark_bar().encode(**encodings)

    return bar.properties(title=chart_title, width=600, height=350)
