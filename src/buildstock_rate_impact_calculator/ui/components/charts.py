"""
Altair chart components for BuildStock TOU Calculator results visualization.
"""

from __future__ import annotations
import textwrap
from typing import Mapping, Optional

import altair as alt
import pandas as pd
import polars as pl

# Long labels (scenario names, rate names) are pre-wrapped in the data by
# joining lines with this separator; axis/legend/header labelExpr split it
# back so Vega renders multi-line labels instead of truncating.
_LABEL_LINE_SEP = "|"
_LABEL_WRAP_EXPR = f"split(datum.label, '{_LABEL_LINE_SEP}')"


def _wrap_labels(values: "pd.Series", width: int = 20) -> "pd.Series":
    """Wrap each label at ~``width`` chars, joining lines with the separator
    consumed by :data:`_LABEL_WRAP_EXPR`."""
    return values.map(
        lambda v: _LABEL_LINE_SEP.join(textwrap.wrap(str(v), width=width)) or str(v)
    )


def _escape_field(name: str) -> str:
    """Escape dots in a column name for Vega-Lite field shorthands.

    Vega-Lite treats unescaped ``.`` as nested-field access, so a column like
    ``in.vintage`` silently resolves to ``datum.in.vintage`` (undefined) and
    every mark collapses onto a single axis position.
    """
    return name.replace(".", "\\.")


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

    tooltips = [
        alt.Tooltip(f"{upgrade_col}:N", title="Upgrade"),
        alt.Tooltip(f"{bill_col}:Q", title=y_axis_title, format=value_format),
    ]
    if group_col and group_col in pandas_df.columns:
        group_field = _escape_field(group_col)
        x = alt.X(f"{group_field}:N", title=group_col.replace("_", " ").title())
        color = alt.Color(f"{upgrade_col}:N", title="Upgrade")
        x_offset = alt.XOffset(f"{upgrade_col}:N")
        tooltips.insert(0, alt.Tooltip(f"{group_field}:N", title=group_col))
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
            tooltip=tooltips,
        )
    )

    if x_offset is not None:
        bar = bar.encode(xOffset=x_offset)

    return bar.properties(
        title=chart_title,
        width=600,
        height=350,
    ).interactive()


def render_bill_rate_bar(
    summary_df: pl.DataFrame,
    scenario_col: str = "scenario",
    rate_col: str = "rate_name",
    upgrade_col: str = "upgrade",
    bill_col: str = "mean_annual_bill",
    value_format: str = "$.0f",
    y_axis_title: Optional[str] = None,
    chart_title: str = "Mean Annual Electricity Bill by Scenario and Rate",
) -> alt.Chart:
    """Grouped bar chart of mean electricity bills by scenario × rate.

    One bar group per upgrade scenario (full scenario names on the x-axis,
    horizontal and wrapped onto multiple lines rather than truncated), with
    a bar per rate the scenario was billed under, colored by rate. Legend
    labels also wrap instead of truncating.

    Args:
        summary_df: Frame from ``summarize_bills(..., group_by=["rate_name"])``
            with a scenario label column.
        scenario_col: Scenario label column.
        rate_col: Rate name column.
        upgrade_col: Upgrade ID column (drives scenario ordering).
        bill_col: Value column to plot.
        value_format: D3 numeric format for tooltips / axis labels.
        y_axis_title: Optional y-axis label override.
        chart_title: Chart title text.

    Returns:
        Altair Chart object.
    """
    if y_axis_title is None:
        y_axis_title = (
            "Mean Electricity Bill ($/ft²)"
            if "per_sqft" in bill_col
            else "Mean Annual Electricity Bill ($)"
        )

    pandas_df = summary_df.to_pandas().sort_values([upgrade_col, rate_col])
    pandas_df["_scenario_wrapped"] = _wrap_labels(pandas_df[scenario_col])
    pandas_df["_rate_wrapped"] = _wrap_labels(pandas_df[rate_col])
    scenario_order = list(dict.fromkeys(pandas_df["_scenario_wrapped"]))
    rate_order = sorted(set(pandas_df["_rate_wrapped"]))

    bar = (
        alt.Chart(pandas_df)
        .mark_bar()
        .encode(
            x=alt.X(
                "_scenario_wrapped:N",
                title="Upgrade Scenario",
                sort=scenario_order,
                axis=alt.Axis(labelAngle=0, labelLimit=0, labelExpr=_LABEL_WRAP_EXPR),
            ),
            xOffset=alt.XOffset("_rate_wrapped:N", sort=rate_order),
            y=alt.Y(
                f"{bill_col}:Q",
                title=y_axis_title,
                axis=alt.Axis(format=value_format),
            ),
            color=alt.Color(
                "_rate_wrapped:N",
                title="Rate",
                sort=rate_order,
                legend=alt.Legend(labelLimit=0, labelExpr=_LABEL_WRAP_EXPR),
            ),
            tooltip=[
                alt.Tooltip(f"{scenario_col}:N", title="Scenario"),
                alt.Tooltip(f"{rate_col}:N", title="Rate"),
                alt.Tooltip(f"{bill_col}:Q", title=y_axis_title, format=value_format),
            ],
        )
    )

    return bar.properties(title=chart_title, width=600, height=350).interactive()


def render_bill_distribution(
    results_df: pl.DataFrame,
    group_col: Optional[str] = None,
    upgrade_col: str = "upgrade",
    bill_col: str = "annual_bill",
    value_format: str = "$.0f",
    y_axis_title: str = "Annual Bill ($)",
    scenario_col: Optional[str] = None,
    rate_col: Optional[str] = None,
    chart_title: str = "Electricity Bill Distribution by Scenario",
) -> alt.Chart:
    """
    Box-and-whisker distribution of per-building bills by upgrade scenario.

    Whiskers follow the Tukey convention (Q1 − 1.5·IQR / Q3 + 1.5·IQR) with
    outliers drawn as points. When ``rate_col`` is given (rate-sweep frames)
    one box is drawn per rate, faceted by scenario, so every rate's
    distribution is visible; scenario and rate labels wrap onto multiple
    lines instead of truncating.

    Args:
        results_df: Raw results DataFrame from ``calculate_batch_bills``.
        group_col: Optional column to facet by (e.g. 'building_type');
            ignored when ``rate_col`` is used.
        upgrade_col: Column with upgrade IDs.
        bill_col: Column with per-building annual bill values.
        value_format: D3 numeric format for tooltips / axis labels.
        y_axis_title: Y-axis label.
        scenario_col: Scenario label column (used for facet headers when
            ``rate_col`` is given).
        rate_col: Rate name column; enables the per-rate view.
        chart_title: Chart title text.

    Returns:
        Altair Chart object.
    """
    df = results_df.filter(pl.col(bill_col).is_not_null())

    if rate_col and rate_col in df.columns and scenario_col in df.columns:
        keep = [scenario_col, rate_col, upgrade_col, bill_col]
        pandas_df = df.select(keep).to_pandas()
        pandas_df = pandas_df.sort_values([upgrade_col, rate_col])
        pandas_df["_scenario_wrapped"] = _wrap_labels(pandas_df[scenario_col])
        pandas_df["_rate_wrapped"] = _wrap_labels(pandas_df[rate_col])
        scenario_order = list(dict.fromkeys(pandas_df["_scenario_wrapped"]))
        rate_order = sorted(set(pandas_df["_rate_wrapped"]))

        box = (
            alt.Chart(pandas_df)
            .mark_boxplot(extent=1.5, outliers=True)
            .encode(
                # Rate identity is carried by the color legend; per-box x
                # labels are hidden because wrapped rate names collide in
                # the narrow facet bands.
                x=alt.X(
                    "_rate_wrapped:N",
                    title="Rate",
                    sort=rate_order,
                    axis=alt.Axis(labels=False, ticks=False),
                ),
                y=alt.Y(
                    f"{bill_col}:Q",
                    title=y_axis_title,
                    axis=alt.Axis(format=value_format),
                ),
                color=alt.Color(
                    "_rate_wrapped:N",
                    title="Rate",
                    sort=rate_order,
                    legend=alt.Legend(labelLimit=0, labelExpr=_LABEL_WRAP_EXPR),
                ),
                tooltip=[
                    alt.Tooltip(f"{scenario_col}:N", title="Scenario"),
                    alt.Tooltip(f"{rate_col}:N", title="Rate"),
                    alt.Tooltip(
                        f"{bill_col}:Q", title=y_axis_title, format=value_format
                    ),
                ],
            )
            .properties(width=180, height=350)
        )
        return box.facet(
            column=alt.Column(
                "_scenario_wrapped:N",
                title=None,
                sort=scenario_order,
                header=alt.Header(
                    labelAngle=0, labelLimit=0, labelExpr=_LABEL_WRAP_EXPR
                ),
            ),
        ).properties(title=chart_title)

    pandas_df = df.to_pandas()
    pandas_df[upgrade_col] = pandas_df[upgrade_col].astype(str)

    box = (
        alt.Chart(pandas_df)
        .mark_boxplot(extent=1.5, outliers=True)
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
        ).properties(title=chart_title)
    else:
        box = box.properties(
            title=chart_title,
            width=600,
            height=350,
        ).interactive()

    return box


def render_savings_histogram(
    diff_df: pl.DataFrame,
    savings_col: str = "bill_savings",
    value_format: str = "$.0f",
    weight_col: str = "weight",
    scenario_col: str = "scenario",
    x_axis_title: str = "Annual Bill Savings ($)",
) -> alt.Chart:
    """Weighted histogram of per-building bill savings.

    Args:
        diff_df: Frame from ``compute_bill_savings`` (savings + weight rows).
        savings_col: Numeric savings column to bin on the x-axis.
        value_format: D3 numeric format for the x-axis labels.
        weight_col: Weight column summed on the y-axis ("buildings
            represented"); a constant 1.0 is used when absent.
        scenario_col: Scenario label column; the chart is faceted by it when
            more than one scenario is present.
        x_axis_title: X-axis label.

    Returns:
        Altair Chart object.
    """
    df = diff_df.filter(pl.col(savings_col).is_not_null())
    if weight_col not in df.columns:
        df = df.with_columns(pl.lit(1.0).alias(weight_col))
    keep = [savings_col, weight_col] + (
        [scenario_col] if scenario_col in df.columns else []
    )
    pandas_df = df.select(keep).to_pandas()

    hist = (
        alt.Chart(pandas_df)
        .mark_bar()
        .encode(
            x=alt.X(
                f"{savings_col}:Q",
                bin=alt.Bin(maxbins=40),
                title=x_axis_title,
                axis=alt.Axis(format=value_format),
            ),
            y=alt.Y(f"sum({weight_col}):Q", title="Buildings represented"),
            tooltip=[
                alt.Tooltip(
                    f"{savings_col}:Q",
                    bin=alt.Bin(maxbins=40),
                    title=x_axis_title,
                    format=value_format,
                ),
                alt.Tooltip(f"sum({weight_col}):Q", title="Buildings represented"),
            ],
        )
    )

    n_scenarios = (
        pandas_df[scenario_col].nunique() if scenario_col in pandas_df.columns else 1
    )
    if n_scenarios > 1:
        return hist.facet(
            facet=alt.Facet(f"{scenario_col}:N", title="Scenario"),
            columns=3,
        ).properties(title="Savings Distribution")
    return hist.properties(
        title="Savings Distribution", width=600, height=300
    ).interactive()


def render_monthly_profile(
    long_df: pl.DataFrame,
    value_col: str = "value",
    value_format: str = "$.0f",
    y_axis_title: str = "Mean Monthly Electricity Bill ($)",
    scenario_col: str = "scenario",
    chart_title: str = "Mean Monthly Electricity Bill by Scenario",
    legend_title: str = "Scenario",
) -> alt.Chart:
    """Render a per-series line chart of mean monthly electricity bills.

    Args:
        long_df: Long-form frame from ``monthly_bills_long`` with ``month``
            (1–12), the value column, and a series label column.
        value_col: Numeric column plotted on the y-axis.
        value_format: D3 numeric format for tooltips / axis labels.
        y_axis_title: Y-axis label.
        scenario_col: Column holding the series label (used for color; e.g.
            a scenario name, or a "scenario — rate" combination). Legend
            labels wrap onto multiple lines rather than truncating.
        chart_title: Chart title text.
        legend_title: Legend title for the series color scale.

    Returns:
        Altair Chart object.
    """
    pandas_df = long_df.to_pandas()
    pandas_df["_series_wrapped"] = _wrap_labels(pandas_df[scenario_col], width=28)
    series_order = list(dict.fromkeys(pandas_df["_series_wrapped"]))

    line = (
        alt.Chart(pandas_df)
        .mark_line(point=True)
        .encode(
            x=alt.X("month:O", title="Month"),
            y=alt.Y(
                f"{value_col}:Q",
                title=y_axis_title,
                axis=alt.Axis(format=value_format),
            ),
            color=alt.Color(
                "_series_wrapped:N",
                title=legend_title,
                sort=series_order,
                legend=alt.Legend(labelLimit=0, labelExpr=_LABEL_WRAP_EXPR),
            ),
            tooltip=[
                alt.Tooltip(f"{scenario_col}:N", title=legend_title),
                alt.Tooltip("month:O", title="Month"),
                alt.Tooltip(f"{value_col}:Q", title=y_axis_title, format=value_format),
            ],
        )
    )

    return line.properties(title=chart_title, width=600, height=350).interactive()


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
    legend_title: str = "Fuel",
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
        "color": alt.Color(f"{fuel_col}:N", title=legend_title),
        "tooltip": [
            alt.Tooltip(f"{scenario_col}:N", title="Scenario"),
            alt.Tooltip(f"{fuel_col}:N", title=legend_title),
            alt.Tooltip(f"{value_col}:Q", title=y_axis_title, format=value_format),
        ],
    }

    if group_col and group_col in pandas_df.columns:
        encodings["column"] = alt.Column(
            f"{group_col}:N", title=group_col.replace("_", " ").title()
        )

    bar = alt.Chart(pandas_df).mark_bar().encode(**encodings)

    return bar.properties(title=chart_title, width=600, height=350)
