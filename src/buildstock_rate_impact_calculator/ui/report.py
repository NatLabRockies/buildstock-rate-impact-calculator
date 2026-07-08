"""
Self-contained HTML report builder for run results.

Pure functions (no Streamlit): the Results page collects the tables and
Altair charts it has already computed and calls :func:`build_html_report`
to produce a single HTML document for ``st.download_button``. Charts are
embedded as Vega-Lite specs rendered client-side via vega-embed from the
jsDelivr CDN (pinned to the installed Altair's schema version), so the file
needs no local assets — just an internet connection when opened.
"""

from __future__ import annotations

import html
from typing import Any

import altair as alt
import polars as pl

_CSS = """
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial,
       sans-serif; margin: 2rem auto; max-width: 60rem; padding: 0 1rem;
       color: #1a1a1a; }
h1 { border-bottom: 2px solid #2196F3; padding-bottom: 0.3rem; }
h2 { margin-top: 2rem; color: #235a97; }
table { border-collapse: collapse; margin: 0.75rem 0; font-size: 0.9rem; }
th, td { padding: 0.35rem 0.7rem; text-align: left;
         border-bottom: 1px solid #ddd; }
th { background: #f0f4f8; }
dl { display: grid; grid-template-columns: max-content auto;
     gap: 0.25rem 1rem; font-size: 0.9rem; }
dt { font-weight: 600; }
dd { margin: 0; }
.footnote { font-size: 0.8rem; color: #555; margin-top: 2rem;
            border-top: 1px solid #ddd; padding-top: 0.5rem; }
.chart-error { color: #b00020; font-size: 0.85rem; }
"""


def _cdn_script_tags() -> str:
    """Script tags for vega / vega-lite / vega-embed pinned to the installed
    Altair's Vega-Lite schema version."""
    vl_version = alt.SCHEMA_VERSION.lstrip("v")
    vl_major = int(vl_version.split(".")[0])
    vega_major = 6 if vl_major >= 6 else 5
    embed_major = 7 if vl_major >= 6 else 6
    return (
        f'<script src="https://cdn.jsdelivr.net/npm/vega@{vega_major}"></script>\n'
        f'<script src="https://cdn.jsdelivr.net/npm/vega-lite@{vl_version}"></script>\n'
        f'<script src="https://cdn.jsdelivr.net/npm/vega-embed@{embed_major}"></script>'
    )


def build_html_report(
    *,
    title: str,
    config_summary: dict[str, Any],
    tables: list[tuple[str, pl.DataFrame]],
    charts: list[tuple[str, alt.Chart]],
    footnotes: list[str],
) -> str:
    """Build a self-contained HTML report.

    Args:
        title: Report heading (also the document title).
        config_summary: Run-configuration facts rendered as a definition
            list (dataset, geography, filters, timestamp, …).
        tables: ``(section heading, DataFrame)`` pairs rendered as HTML
            tables.
        charts: ``(section heading, Altair chart)`` pairs rendered via
            vega-embed. A chart that fails to serialize degrades to an
            inline note instead of aborting the report.
        footnotes: Caveat lines appended at the bottom.

    Returns:
        Complete HTML document as a string.
    """
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f"<title>{html.escape(title)}</title>",
        _cdn_script_tags(),
        f"<style>{_CSS}</style>",
        "</head><body>",
        f"<h1>{html.escape(title)}</h1>",
    ]

    if config_summary:
        parts.append("<h2>Run Configuration</h2><dl>")
        for key, value in config_summary.items():
            parts.append(
                f"<dt>{html.escape(str(key))}</dt><dd>{html.escape(str(value))}</dd>"
            )
        parts.append("</dl>")

    for heading, df in tables:
        parts.append(f"<h2>{html.escape(heading)}</h2>")
        try:
            parts.append(df.to_pandas().to_html(index=False, border=0))
        except Exception as exc:
            parts.append(
                f'<p class="chart-error">Table could not be rendered: '
                f"{html.escape(str(exc))}</p>"
            )

    for i, (heading, chart) in enumerate(charts):
        parts.append(f"<h2>{html.escape(heading)}</h2>")
        try:
            spec_json = chart.to_json()
        except Exception as exc:
            parts.append(
                f'<p class="chart-error">Chart could not be rendered: '
                f"{html.escape(str(exc))}</p>"
            )
            continue
        parts.append(
            f'<div id="c{i}"></div>\n'
            f'<script>vegaEmbed("#c{i}", {spec_json}, {{actions: false}});</script>'
        )

    if footnotes:
        parts.append('<div class="footnote"><ul>')
        for note in footnotes:
            parts.append(f"<li>{html.escape(note)}</li>")
        parts.append("</ul></div>")

    parts.append("</body></html>")
    return "\n".join(parts)
