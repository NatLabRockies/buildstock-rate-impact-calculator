"""Unit tests for Building Filters display helpers: acronym-aware labels,
RECS income-bin formatting/sorting, and tenure help text."""

from __future__ import annotations

from buildstock_rate_impact_calculator.ui.pages import page_03_building_filter as bf


class TestFilterLabel:
    def test_acronyms_uppercased(self) -> None:
        assert bf._filter_label("in.hvac_heating_type") == "HVAC Heating Type"
        assert bf._filter_label("in.ashrae_iecc_climate_zone_2004") == (
            "ASHRAE IECC Climate Zone 2004"
        )

    def test_recs_token_uppercased(self) -> None:
        assert bf._filter_label("in.geometry_building_type_recs") == (
            "Geometry Building Type RECS"
        )

    def test_income_override(self) -> None:
        assert bf._filter_label("in.income_recs_2020") == (
            "RECS 2020 Standard Income Bins"
        )

    def test_unit_suffix_preserved(self) -> None:
        assert bf._filter_label("in.sqft..ft2") == "Sqft (ft2)"

    def test_plain_column_title_cased(self) -> None:
        assert bf._filter_label("in.heating_fuel") == "Heating Fuel"


class TestIncomeBinFormatting:
    def test_sort_low_to_high_with_na_last(self) -> None:
        bins = ["150000+", "<20000", "20000-39999", "Not Available", "60000-79999"]
        assert sorted(bins, key=bf._income_bin_sort_key) == [
            "<20000",
            "20000-39999",
            "60000-79999",
            "150000+",
            "Not Available",
        ]

    def test_format_range(self) -> None:
        assert bf._format_income_bin("20000-39999") == "$20,000 – $39,999"

    def test_format_less_than(self) -> None:
        assert bf._format_income_bin("<20000") == "< $20,000"

    def test_format_plus(self) -> None:
        assert bf._format_income_bin("150000+") == "$150,000+"

    def test_format_not_available_passthrough(self) -> None:
        assert bf._format_income_bin("Not Available") == "Not Available"


class TestTenureHelp:
    def test_tenure_help_mentions_vacant(self) -> None:
        text = bf._help_text({}, "in.tenure")
        assert "vacant" in text.lower()

    def test_non_tenure_help_has_no_vacant_note(self) -> None:
        text = bf._help_text({}, "in.heating_fuel")
        assert "vacant" not in text.lower()
