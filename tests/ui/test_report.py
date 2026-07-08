"""Phase 10: shareable HTML report builder."""

from __future__ import annotations

import altair as alt
import polars as pl

from buildstock_rate_impact_calculator.ui.report import build_html_report


def _chart() -> alt.Chart:
    return (
        alt.Chart(pl.DataFrame({"x": [1, 2], "y": [3.0, 4.0]}).to_pandas())
        .mark_bar()
        .encode(x="x:O", y="y:Q")
    )


def _table() -> pl.DataFrame:
    return pl.DataFrame(
        {"scenario": ["Baseline", "Upgrade 1"], "mean_annual_bill": [1200.0, 950.0]}
    )


class TestBuildHtmlReport:
    def test_contains_title_tables_charts_and_config(self) -> None:
        html_doc = build_html_report(
            title="Test Report",
            config_summary={"Dataset": "resstock 2024_release_2", "Preview run": "No"},
            tables=[("Bill Summary", _table())],
            charts=[("Mean Bill", _chart()), ("Distribution", _chart())],
            footnotes=["Electricity only."],
        )
        assert "Test Report" in html_doc
        # Table section heading + column header
        assert "Bill Summary" in html_doc
        assert "mean_annual_bill" in html_doc
        # Config values
        assert "resstock 2024_release_2" in html_doc
        assert "Preview run" in html_doc
        # One vegaEmbed call per chart
        assert html_doc.count("vegaEmbed(") == 2
        assert '"#c0"' in html_doc and '"#c1"' in html_doc
        # Footnote present
        assert "Electricity only." in html_doc
        # Self-contained document skeleton + pinned CDN assets
        assert html_doc.startswith("<!DOCTYPE html>")
        assert "cdn.jsdelivr.net/npm/vega-lite@" in html_doc

    def test_zero_charts_ok(self) -> None:
        html_doc = build_html_report(
            title="No Charts",
            config_summary={},
            tables=[("Only Table", _table())],
            charts=[],
            footnotes=[],
        )
        assert "vegaEmbed(" not in html_doc
        assert "Only Table" in html_doc

    def test_failing_chart_degrades_to_note(self) -> None:
        class BrokenChart:
            def to_json(self) -> str:
                raise RuntimeError("boom")

        html_doc = build_html_report(
            title="Broken",
            config_summary={},
            tables=[],
            charts=[("Bad Chart", BrokenChart())],  # type: ignore[list-item]
            footnotes=[],
        )
        assert "Chart could not be rendered" in html_doc
        assert "vegaEmbed(" not in html_doc

    def test_html_escapes_headings(self) -> None:
        html_doc = build_html_report(
            title="<script>alert(1)</script>",
            config_summary={"<key>": "<value>"},
            tables=[],
            charts=[],
            footnotes=["<note>"],
        )
        assert "<script>alert(1)</script>" not in html_doc
        assert "&lt;script&gt;" in html_doc
