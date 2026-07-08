"""Smoke tests for the Results page (page_06_results).

Renders the full page with synthetic ComStock / ResStock results frames via
``streamlit.testing.v1.AppTest`` and asserts the page builds without errors,
the energy-source pills render, and the Grouping section is gone.
"""

from __future__ import annotations

import io

import polars as pl
import pytest
from streamlit.testing.v1 import AppTest


def _comstock_results_df() -> pl.DataFrame:
    """Two buildings × two upgrades, ComStock-shaped (weighted bills).

    Includes a two-rate sweep (Phase 7): the primary "Test Elec" rows plus a
    non-primary "Rate B" variant with 20% higher electricity bills. Sections
    other than Rate Comparison must report primary-only values.
    """
    base = pl.DataFrame(
        {
            "bldg_id": [1, 2, 1, 2],
            "upgrade": [0, 0, 1, 1],
            "annual_bill": [50_000.0, 160_000.0, 40_000.0, 120_000.0],
            "natural_gas_bill": [10_000.0, 40_000.0, 10_000.0, 40_000.0],
            "weight": [10.0, 20.0, 10.0, 20.0],
            "sqft": [1000.0, 2000.0, 1000.0, 2000.0],
            "_row_uid": ["1_a", "2_a", "1_a", "2_a"],
            "applicable": [True, True, True, False],
            "error": [None, None, None, None],
            "monthly_bills": [
                str([50_000.0 / 12] * 12),
                str([160_000.0 / 12] * 12),
                str([40_000.0 / 12] * 12),
                str([120_000.0 / 12] * 12),
            ],
            "annual_fixed_charge": [600.0, 1_200.0, 600.0, 1_200.0],
            "annual_energy_charge": [39_400.0, 118_800.0, 31_400.0, 88_800.0],
            "annual_demand_charge": [10_000.0, 40_000.0, 8_000.0, 30_000.0],
            "annual_min_charge": [0.0, 0.0, 0.0, 0.0],
            "peak_demand_kw": [50.0, 160.0, 40.0, 120.0],
            "rate_name": ["Test Elec"] * 4,
            "is_primary": [True] * 4,
        }
    )
    sweep_variant = base.with_columns(
        pl.lit("Rate B").alias("rate_name"),
        pl.lit(False).alias("is_primary"),
        (pl.col("annual_bill") * 1.2).alias("annual_bill"),
    )
    return pl.concat([base, sweep_variant])


def _resstock_results_df() -> pl.DataFrame:
    """Two buildings × two upgrades, ResStock-shaped (per-building bills)."""
    return pl.DataFrame(
        {
            "bldg_id": [1, 2, 1, 2],
            "upgrade": [0, 0, 1, 1],
            "annual_bill": [1000.0, 2000.0, 800.0, 1500.0],
            "natural_gas_bill": [100.0, 200.0, 50.0, 100.0],
            "weight": [100.0, 100.0, 100.0, 100.0],
            "sqft": [None, None, None, None],
            "_row_uid": ["1", "2", "1", "2"],
            "applicable": [True, True, True, True],
            "error": [None, None, None, None],
            "monthly_bills": [
                str([1000.0 / 12] * 12),
                str([2000.0 / 12] * 12),
                str([800.0 / 12] * 12),
                str([1500.0 / 12] * 12),
            ],
            "annual_fixed_charge": [60.0, 60.0, 60.0, 60.0],
            "annual_energy_charge": [900.0, 1900.0, 700.0, 1400.0],
            "annual_demand_charge": [40.0, 40.0, 40.0, 40.0],
            "annual_min_charge": [0.0, 0.0, 0.0, 0.0],
            "peak_demand_kw": [3.0, 5.0, 2.0, 4.0],
            "in.vintage": ["1980s", "1990s", "1980s", "1990s"],
            "in.income_recs_2020": ["<20000", "150000+", "<20000", "150000+"],
            # Phase 8: bldg 2 falls outside the assigned rate's window.
            "rate_eligible": [True, False, True, False],
        }
    )


def _render_page(df_bytes: bytes, dataset_type: str, release: str) -> None:
    """AppTest script: seed session state and render the results page."""
    import io as _io

    import polars as _pl
    import streamlit as st

    from buildstock_rate_impact_calculator.data import upgrade_labels
    from buildstock_rate_impact_calculator.data.buildstock_manager import (
        BuildStockDataManager,
    )
    from buildstock_rate_impact_calculator.ui.pages.page_06_results import (
        render_results_page,
    )
    from buildstock_rate_impact_calculator.ui.state import AppState

    df = _pl.read_parquet(_io.BytesIO(df_bytes))
    config_key = f"{dataset_type}_{release}"
    base_uri = BuildStockDataManager.DATASET_CONFIGS[config_key].base_uri
    # Pre-seed the label cache so the page never fetches over the network.
    upgrade_labels._label_cache[base_uri] = {0: "Baseline", 1: "Test Upgrade"}

    st.session_state["run_results"] = {"df": df}
    app_state = AppState()
    app_state.dataset_type = dataset_type
    app_state.release = release
    app_state.results_available = True
    # Rate config exercised by the Configured Rates section.
    app_state.rate_library = {
        "Test Elec": {
            "energyratestructure": [[{"rate": 0.12}]],
            "fixedchargefirstmeter": 10.0,
        }
    }
    app_state.rate_assignments = {"baseline": "Test Elec", "upgrade_1": "Test Elec"}
    render_results_page(app_state)


def _run_apptest(df: pl.DataFrame, dataset_type: str, release: str) -> AppTest:
    buf = io.BytesIO()
    df.write_parquet(buf)
    at = AppTest.from_function(
        _render_page,
        args=(buf.getvalue(), dataset_type, release),
        default_timeout=15,
    )
    at.run()
    return at


@pytest.fixture()
def comstock_page() -> AppTest:
    return _run_apptest(_comstock_results_df(), "comstock", "2025_release_3")


@pytest.fixture()
def resstock_page() -> AppTest:
    return _run_apptest(_resstock_results_df(), "resstock", "2024_release_2")


class TestComStockResultsPage:
    def test_renders_without_errors(self, comstock_page: AppTest) -> None:
        assert not comstock_page.exception
        assert len(comstock_page.error) == 0

    def test_grouping_section_removed(self, comstock_page: AppTest) -> None:
        subheaders = [s.value for s in comstock_page.subheader]
        assert "Grouping" not in subheaders
        assert "Bill Summary" in subheaders

    def test_energy_source_pills_present(self, comstock_page: AppTest) -> None:
        subheaders = [s.value for s in comstock_page.subheader]
        assert "Energy Sources" in subheaders

    def test_configured_rates_section_present(self, comstock_page: AppTest) -> None:
        subheaders = [s.value for s in comstock_page.subheader]
        assert "Configured Rates" in subheaders

    def test_by_fuel_view_renders(self, comstock_page: AppTest) -> None:
        at = comstock_page
        at.radio(key="bill_summary_view").set_value("By fuel")
        at.run()
        assert not at.exception
        assert len(at.error) == 0

    def test_bill_summary_radio_has_component_option(
        self, comstock_page: AppTest
    ) -> None:
        options = comstock_page.radio(key="bill_summary_view").options
        assert "By charge component" in options

    def test_by_charge_component_view_renders(self, comstock_page: AppTest) -> None:
        at = comstock_page
        at.radio(key="bill_summary_view").set_value("By charge component")
        at.run()
        assert not at.exception
        assert len(at.error) == 0

    def test_monthly_profile_section_present(self, comstock_page: AppTest) -> None:
        subheaders = [s.value for s in comstock_page.subheader]
        assert "Monthly Electricity Bill Profile" in subheaders

    def test_energy_burden_section_absent(self, comstock_page: AppTest) -> None:
        """Energy burden is a ResStock-only view (no income data in ComStock)."""
        subheaders = [s.value for s in comstock_page.subheader]
        assert "Energy Burden" not in subheaders

    def test_rate_comparison_section_present(self, comstock_page: AppTest) -> None:
        """Phase 7: two rates in the frame → Rate Comparison matrix renders."""
        subheaders = [s.value for s in comstock_page.subheader]
        assert "Rate Comparison" in subheaders

    def test_monthly_profile_series_multiselect(self, comstock_page: AppTest) -> None:
        """Rate-sweep frames expose scenario–rate combos as a multiselect."""
        at = comstock_page
        selector = at.multiselect(key="monthly_profile_series")
        # 2 scenarios × 2 rates = 4 combos, all shown by default.
        assert len(selector.options) == 4
        assert selector.value == selector.options
        # Deselect down to a single combo — the page must rerun cleanly.
        selector.set_value([selector.options[0]])
        at.run()
        assert not at.exception
        assert len(at.error) == 0

    def test_electricity_bill_sections_renamed(self, comstock_page: AppTest) -> None:
        subheaders = [s.value for s in comstock_page.subheader]
        assert "Mean Annual Electricity Bill" in subheaders
        assert "Electricity Bill Distribution" in subheaders
        assert "Monthly Electricity Bill Profile" in subheaders

    def test_rate_comparison_savings_view_renders(self, comstock_page: AppTest) -> None:
        at = comstock_page
        at.radio(key="rate_comparison_view").set_value("Mean savings")
        at.run()
        assert not at.exception
        assert len(at.error) == 0

    def test_peak_demand_section_present(self, comstock_page: AppTest) -> None:
        subheaders = [s.value for s in comstock_page.subheader]
        assert "Peak Demand" in subheaders

    def test_total_savings_view_renders(self, comstock_page: AppTest) -> None:
        at = comstock_page
        at.radio(key="bill_savings_view").set_value("Total")
        at.run()
        assert not at.exception
        assert len(at.error) == 0

    def test_floor_area_metric_replaces_building_count(
        self, comstock_page: AppTest
    ) -> None:
        metric_labels = [m.label for m in comstock_page.metric]
        assert "Total Floor Area" in metric_labels
        assert "Total Buildings" not in metric_labels
        # sum(weight × sqft) for the baseline scenario = 10×1000 + 20×2000
        floor_metric = next(
            m for m in comstock_page.metric if m.label == "Total Floor Area"
        )
        assert floor_metric.value == "50,000 ft²"


class TestResStockResultsPage:
    def test_renders_without_errors(self, resstock_page: AppTest) -> None:
        assert not resstock_page.exception
        assert len(resstock_page.error) == 0

    def test_building_count_metric_kept(self, resstock_page: AppTest) -> None:
        metric_labels = [m.label for m in resstock_page.metric]
        assert "Total Buildings" in metric_labels

    def test_by_fuel_view_renders(self, resstock_page: AppTest) -> None:
        at = resstock_page
        at.radio(key="bill_summary_view").set_value("By fuel")
        at.run()
        assert not at.exception
        assert len(at.error) == 0

    def test_by_charge_component_view_renders(self, resstock_page: AppTest) -> None:
        at = resstock_page
        at.radio(key="bill_summary_view").set_value("By charge component")
        at.run()
        assert not at.exception
        assert len(at.error) == 0

    def test_monthly_profile_and_peak_sections_present(
        self, resstock_page: AppTest
    ) -> None:
        subheaders = [s.value for s in resstock_page.subheader]
        assert "Monthly Electricity Bill Profile" in subheaders
        assert "Peak Demand" in subheaders

    def test_energy_burden_section_present(self, resstock_page: AppTest) -> None:
        """Phase 6: income column present → Energy Burden section renders."""
        subheaders = [s.value for s in resstock_page.subheader]
        assert "Energy Burden" in subheaders

    def test_segment_breakout_renders(self, resstock_page: AppTest) -> None:
        """Phase 5: selecting a segment breaks savings out without errors."""
        at = resstock_page
        seg_box = at.selectbox(key="savings_segment")
        assert "in.vintage" in seg_box.options
        seg_box.set_value("in.vintage")
        at.run()
        assert not at.exception
        assert len(at.error) == 0

    def test_rate_eligibility_banner_and_toggle(self, resstock_page: AppTest) -> None:
        """Phase 8: ineligible rows raise a warning; the toggle excludes them."""
        at = resstock_page
        warnings = " ".join(w.value for w in at.warning)
        assert "eligibility window" in warnings

        def _buildings_metric(app_test: AppTest) -> str:
            return next(
                m.value for m in app_test.metric if m.label == "Total Buildings"
            )

        assert _buildings_metric(at) == "2"
        at.toggle(key="exclude_rate_ineligible").set_value(True)
        at.run()
        assert not at.exception
        assert len(at.error) == 0
        assert _buildings_metric(at) == "1"

    def test_html_report_download_button(self, resstock_page: AppTest) -> None:
        """Phase 10: the Export section offers an HTML report download."""
        labels = [b.label for b in resstock_page.get("download_button")]
        assert any("HTML Report" in label for label in labels)

    def test_small_sample_guardrail_banner(self, resstock_page: AppTest) -> None:
        """Phase 9: fewer than MIN_SAMPLE_MODELS models → warning banner."""
        warnings = " ".join(w.value for w in resstock_page.warning)
        assert "building models" in warnings
        assert "unreliable" in warnings

    def test_winners_losers_metrics_shown(self, resstock_page: AppTest) -> None:
        """Phase 4: the savings section reports weighted saver/loser shares."""
        md_values = " ".join(m.value for m in resstock_page.markdown)
        assert "of buildings save" in md_values
        # Both fixture buildings save (>$1) under upgrade 1.
        assert "100% of buildings save" in md_values
