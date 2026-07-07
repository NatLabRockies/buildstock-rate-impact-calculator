"""
Tests for geo-aware metadata loading in BuildStockDataManager:
URI construction for sub-state (county/PUMA) metadata files, app column
assembly, column-projected multi-file loading with renames, and local caching.

All tests run against local fixture files — no network access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

from buildstock_rate_impact_calculator.data.buildstock_manager import (
    BuildStockDataManager,
)


def _make_mgr(
    tmp_path: Path,
    dataset_type: str = "comstock",
    release: str = "2025_release_3",
    **kwargs: Any,
) -> BuildStockDataManager:
    return BuildStockDataManager(
        dataset_type=dataset_type,
        release=release,
        cache_enabled=kwargs.pop("cache_enabled", False),
        cache_dir=tmp_path / "cache",
        **kwargs,
    )


# ===========================================================================
# state_for_gisjoin
# ===========================================================================


class TestStateForGisjoin:
    def test_county_gisjoin(self) -> None:
        assert BuildStockDataManager.state_for_gisjoin("G0200130") == "AK"
        assert BuildStockDataManager.state_for_gisjoin("G0800690") == "CO"

    def test_puma_gisjoin(self) -> None:
        assert BuildStockDataManager.state_for_gisjoin("G02000101") == "AK"

    def test_invalid_returns_none(self) -> None:
        assert BuildStockDataManager.state_for_gisjoin("08069") is None
        assert BuildStockDataManager.state_for_gisjoin("G99") is None
        assert BuildStockDataManager.state_for_gisjoin("") is None


# ===========================================================================
# counties_for_states (geo_utils)
# ===========================================================================


class TestCountiesForStates:
    def test_returns_gisjoins_for_state(self) -> None:
        from buildstock_rate_impact_calculator.data.geo_utils import (
            counties_for_states,
        )

        counties = counties_for_states(["AK"])
        assert len(counties) > 0
        assert all(c.startswith("G02") for c in counties)
        assert counties == sorted(set(counties))

    def test_multiple_states_union(self) -> None:
        from buildstock_rate_impact_calculator.data.geo_utils import (
            counties_for_states,
        )

        ak = counties_for_states(["AK"])
        co = counties_for_states(["co"])  # case-insensitive
        both = counties_for_states(["AK", "CO"])
        assert set(both) == set(ak) | set(co)

    def test_unknown_state_skipped(self) -> None:
        from buildstock_rate_impact_calculator.data.geo_utils import (
            counties_for_states,
        )

        assert counties_for_states(["ZZ"]) == []


class TestSupportedGeoLevels:
    def test_puma_not_supported_for_comstock(self) -> None:
        from buildstock_rate_impact_calculator.data.geo_utils import validate_geo_ids

        with pytest.raises(ValueError, match="not supported"):
            validate_geo_ids(["0800803"], geo_level="puma", dataset_type="comstock")

    def test_puma_still_supported_for_resstock(self) -> None:
        from buildstock_rate_impact_calculator.data.geo_utils import validate_geo_ids

        valid, invalid = validate_geo_ids(
            ["0800803"], geo_level="puma", dataset_type="resstock"
        )
        assert valid == ["0800803"]
        assert invalid == []


# ===========================================================================
# set_geo_selection / uses_geo_metadata / metadata_uris_for_upgrade
# ===========================================================================


class TestGeoUriConstruction:
    def test_resstock_never_uses_geo_metadata(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path, dataset_type="resstock", release="2025_release_1")
        mgr.set_geo_selection(county_ids=["G0800690"], puma_ids=["G08000803"])
        assert mgr.uses_geo_metadata() is False
        uris = mgr.metadata_uris_for_upgrade(0)
        assert uris == [mgr.metadata_uri]

    def test_comstock_without_geo_selection_raises(self, tmp_path: Path) -> None:
        """ComStock has no national metadata file — aggregate weights are not
        valid for bill calculations, so a geographic selection is required."""
        mgr = _make_mgr(tmp_path)
        with pytest.raises(RuntimeError, match="county"):
            mgr.metadata_uris_for_upgrade(0)

    def test_comstock_states_expand_to_counties(self, tmp_path: Path) -> None:
        """A states-only selection expands to every county in those states."""
        from buildstock_rate_impact_calculator.data.geo_utils import (
            counties_for_states,
        )

        mgr = _make_mgr(tmp_path)
        mgr.set_geo_selection(states=["AK"])
        assert mgr.uses_geo_metadata() is True
        uris = mgr.metadata_uris_for_upgrade(0)
        expected_counties = counties_for_states(["AK"])
        assert len(uris) == len(expected_counties) > 0
        assert all("state=AK/county=G02" in u for u in uris)
        assert all("metadata_and_annual_results/by_state_and_county" in u for u in uris)

    def test_comstock_county_uris_2025(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.set_geo_selection(county_ids=["G0200130", "G0800690"])
        assert mgr.uses_geo_metadata() is True
        uris = mgr.metadata_uris_for_upgrade(0)
        assert len(uris) == 2
        # County metadata comes from the non-aggregate full tree (per-row weights)
        assert any(
            u.endswith(
                "metadata_and_annual_results/by_state_and_county/full/parquet/"
                "state=AK/county=G0200130/AK_G0200130_upgrade0.parquet"
            )
            for u in uris
        )
        assert all(u.startswith("s3://oedi-data-lake/") for u in uris)

    def test_comstock_puma_selection_ignored(self, tmp_path: Path) -> None:
        """PUMA is unsupported for ComStock — no puma path is configured, so a
        PUMA-only selection resolves to zero metadata URIs."""
        mgr = _make_mgr(tmp_path)
        mgr.set_geo_selection(puma_ids=["G02000101"])
        uris = mgr.metadata_uris_for_upgrade(3)
        assert uris == []

    def test_comstock_mixed_county_and_puma_keeps_county_only(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.set_geo_selection(county_ids=["G0200130"], puma_ids=["G02000101"])
        uris = mgr.metadata_uris_for_upgrade(0)
        assert len(uris) == 1
        assert "by_state_and_county" in uris[0]

    def test_comstock_2024_tokens(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path, release="2024_release_2")
        mgr.set_geo_selection(county_ids=["G0200130"])
        baseline = mgr.metadata_uris_for_upgrade(0)
        upgrade = mgr.metadata_uris_for_upgrade(3)
        assert baseline[0].endswith("AK_G0200130_baseline.parquet")
        assert upgrade[0].endswith("AK_G0200130_upgrade03.parquet")

    def test_unparseable_geo_id_skipped(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.set_geo_selection(county_ids=["G0200130", "BOGUS"])
        uris = mgr.metadata_uris_for_upgrade(0)
        assert len(uris) == 1

    def test_estimate_metadata_download_size_counties(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.set_geo_selection(county_ids=["G0200130", "G0800690"])
        # 2 county files × 2 scenarios (baseline always included alongside 3)
        est = mgr.estimate_metadata_download_size(upgrades=[0, 3])
        assert est == 2 * 2 * BuildStockDataManager._METADATA_BYTES_PER_GEO_FILE

    def test_estimate_metadata_download_size_no_selection_is_zero(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path)
        assert mgr.estimate_metadata_download_size(upgrades=[0, 3]) == 0

    def test_estimate_metadata_download_size_resstock_national(
        self, tmp_path: Path
    ) -> None:
        mgr = _make_mgr(tmp_path, dataset_type="resstock", release="2025_release_1")
        est = mgr.estimate_metadata_download_size(upgrades=[3])
        # 1 national file × 2 scenarios (baseline + upgrade 3)
        assert est == 1 * 2 * BuildStockDataManager._METADATA_BYTES_PER_NATIONAL_FILE

    def test_changing_selection_clears_loaded_metadata(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr._metadata = pl.DataFrame({"bldg_id": [1]})
        mgr.set_geo_selection(county_ids=["G0200130"])
        assert mgr._metadata is None


# ===========================================================================
# get_app_metadata_columns
# ===========================================================================


class TestAppMetadataColumns:
    def test_includes_required_and_filter_columns(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        cols = mgr.get_app_metadata_columns()
        assert mgr.config.building_id_column in cols
        assert mgr.config.state_column in cols
        assert "in.county" in cols
        assert "in.puma" in cols
        for c in mgr.config.filter_columns:
            assert c in cols

    def test_includes_fuel_and_bill_columns(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path, dataset_type="resstock", release="2025_release_1")
        cols = mgr.get_app_metadata_columns()
        assert "out.natural_gas.total.energy_consumption..kwh" in cols
        assert "out.utility_bills.natural_gas_bill..usd" in cols
        assert "out.utility_bills.electricity_bill..usd" in cols

    def test_no_duplicates(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        cols = mgr.get_app_metadata_columns()
        assert len(cols) == len(set(cols))


# ===========================================================================
# load_upgrade_metadata — local fixture files, no network
# ===========================================================================


@pytest.fixture()
def county_file(tmp_path: Path) -> Path:
    """County-style metadata fixture with ComStock geo column naming."""
    path = tmp_path / "county_AK.parquet"
    pl.DataFrame(
        {
            "bldg_id": [1, 2],
            "in.state": ["AK", "AK"],
            "in.nhgis_county_gisjoin": ["G0200130", "G0200130"],
            "in.comstock_building_type": ["SmallOffice", "Warehouse"],
            "out.natural_gas.total.energy_consumption..kwh": [100.0, 200.0],
            "out.unneeded_giant_column": [9.9, 9.9],
        }
    ).write_parquet(path)
    return path


@pytest.fixture()
def puma_file(tmp_path: Path) -> Path:
    """PUMA-style metadata fixture."""
    path = tmp_path / "puma_AK.parquet"
    pl.DataFrame(
        {
            "bldg_id": [3],
            "in.state": ["AK"],
            "in.nhgis_puma_gisjoin": ["G02000101"],
            "in.comstock_building_type": ["RetailStandalone"],
            "out.natural_gas.total.energy_consumption..kwh": [300.0],
        }
    ).write_parquet(path)
    return path


class TestLoadUpgradeMetadata:
    def test_column_projection_and_renames(
        self, tmp_path: Path, county_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _make_mgr(tmp_path)
        monkeypatch.setattr(
            mgr, "metadata_uris_for_upgrade", lambda upgrade_id: [str(county_file)]
        )
        df = mgr.load_upgrade_metadata(0)
        # geo column renamed to canonical name
        assert "in.county" in df.columns
        assert "in.nhgis_county_gisjoin" not in df.columns
        # unrequested columns are not loaded
        assert "out.unneeded_giant_column" not in df.columns
        # requested columns that exist are present
        assert "in.comstock_building_type" in df.columns
        assert "out.natural_gas.total.energy_consumption..kwh" in df.columns

    def test_multi_file_concat_county_and_puma(
        self,
        tmp_path: Path,
        county_file: Path,
        puma_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mgr = _make_mgr(tmp_path)
        monkeypatch.setattr(
            mgr,
            "metadata_uris_for_upgrade",
            lambda upgrade_id: [str(county_file), str(puma_file)],
        )
        df = mgr.load_upgrade_metadata(0)
        assert len(df) == 3
        assert set(df["bldg_id"].to_list()) == {1, 2, 3}
        # diagonal concat: county rows have null in.puma and vice versa
        assert "in.county" in df.columns and "in.puma" in df.columns
        assert df.filter(pl.col("bldg_id") == 3)["in.puma"][0] == "G02000101"
        assert df.filter(pl.col("bldg_id") == 3)["in.county"][0] is None

    def test_load_metadata_populates_state_mapping(
        self, tmp_path: Path, county_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _make_mgr(tmp_path)
        monkeypatch.setattr(
            mgr, "metadata_uris_for_upgrade", lambda upgrade_id: [str(county_file)]
        )
        mgr.load_metadata()
        assert mgr.get_building_state(1) == "AK"

    def test_local_cache_round_trip(
        self, tmp_path: Path, county_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _make_mgr(tmp_path, cache_enabled=True)
        monkeypatch.setattr(
            mgr, "metadata_uris_for_upgrade", lambda upgrade_id: [str(county_file)]
        )
        df1 = mgr.load_upgrade_metadata(0)
        # Overwrite the source: a cached load must not see the change
        pl.DataFrame(
            {
                "bldg_id": [99],
                "in.state": ["AK"],
                "in.nhgis_county_gisjoin": ["G0200130"],
                "in.comstock_building_type": ["Hospital"],
                "out.natural_gas.total.energy_consumption..kwh": [1.0],
            }
        ).write_parquet(county_file)
        df2 = mgr.load_upgrade_metadata(0)
        assert df2["bldg_id"].to_list() == df1["bldg_id"].to_list()
        # force_reload bypasses the cache
        df3 = mgr.load_upgrade_metadata(0, force_reload=True)
        assert df3["bldg_id"].to_list() == [99]

    def test_failed_file_does_not_sink_the_load(
        self, tmp_path: Path, county_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _make_mgr(tmp_path)
        missing = tmp_path / "does_not_exist.parquet"
        monkeypatch.setattr(
            mgr,
            "metadata_uris_for_upgrade",
            lambda upgrade_id: [str(county_file), str(missing)],
        )
        df = mgr.load_upgrade_metadata(0)
        assert set(df["bldg_id"].to_list()) == {1, 2}

    def test_all_files_failed_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _make_mgr(tmp_path)
        monkeypatch.setattr(
            mgr,
            "metadata_uris_for_upgrade",
            lambda upgrade_id: [
                str(tmp_path / "a.parquet"),
                str(tmp_path / "b.parquet"),
            ],
        )
        with pytest.raises(RuntimeError, match="None of the"):
            mgr.load_upgrade_metadata(0)


# ===========================================================================
# get_upgrade_applicability — geo-aware multi-file behavior
# ===========================================================================


@pytest.mark.integration
class TestIntegrationGeoMetadata:
    """Live OEDI checks for the ComStock sub-state metadata path."""

    def test_comstock_multi_county_load(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path, cache_enabled=True)
        # Denver county (CO) + Aleutians East (AK)
        mgr.set_geo_selection(county_ids=["G0800310", "G0200130"])
        md = mgr.load_metadata()
        assert len(md) > 0
        assert "in.county" in md.columns
        assert set(md["in.state"].unique().to_list()) == {"AK", "CO"}
        # Only the possibly-necessary columns are downloaded (full file has 1300+)
        assert len(md.columns) < 30
        assert "out.natural_gas.total.energy_consumption..kwh" in md.columns

    def test_comstock_geo_applicability(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path, cache_enabled=True)
        mgr.set_geo_selection(county_ids=["G0800310"])
        md = mgr.load_metadata()
        bids = md["bldg_id"].unique().to_list()[:100]
        result = mgr.get_upgrade_applicability(3, bids)
        assert set(result.keys()) == set(bids)
        assert any(result.values())


class TestGeoApplicability:
    def test_boolean_applicability_column_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _make_mgr(tmp_path)
        # ComStock requires a geo selection for URI construction
        mgr.set_geo_selection(county_ids=["G0200130"])
        fake_df = pl.DataFrame(
            {"bldg_id": [1, 2, 3], "applicability": [True, False, True]}
        )
        monkeypatch.setattr(pl, "scan_parquet", lambda *a, **k: fake_df.lazy())
        result = mgr.get_upgrade_applicability(5, [1, 2, 3, 4])
        assert result == {1: True, 2: False, 3: True, 4: False}

    def test_union_across_geo_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _make_mgr(tmp_path)
        frames = {
            "uri_a": pl.DataFrame({"bldg_id": [1], "applicability": [True]}),
            "uri_b": pl.DataFrame({"bldg_id": [2], "applicability": [True]}),
        }
        monkeypatch.setattr(
            mgr, "metadata_uris_for_upgrade", lambda upgrade_id: ["uri_a", "uri_b"]
        )
        monkeypatch.setattr(pl, "scan_parquet", lambda uri, **k: frames[uri].lazy())
        result = mgr.get_upgrade_applicability(5, [1, 2, 3])
        assert result == {1: True, 2: True, 3: False}
