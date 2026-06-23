"""
Tests for BuildStockDataManager.

Unit tests (no network)
-----------------------
Run with the default ``pixi run pytest tests/ -v``.

Integration tests (live OEDI S3)
---------------------------------
Run with::

    pixi run pytest tests/ -v --integration -s

The ``-s`` flag lets timing output reach the terminal.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from buildstock_rate_impact_calculator.data.buildstock_manager import (
    BuildStockDataManager,
    _OEDI_HTTPS_BASE,
    _OEDI_S3_BASE,
)


# ===========================================================================
# Helpers
# ===========================================================================

_RS25_KEY = "resstock_2025_release_1"
_RS25_CONFIG = BuildStockDataManager.DATASET_CONFIGS[_RS25_KEY]

_EXPECTED_S3_METADATA_URI = (
    "s3://oedi-data-lake/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock"
    "/2025/resstock_amy2018_release_1"
    "/metadata_and_annual_results/national/full/parquet/upgrade0.parquet"
)

# ResStock 2025 r1 upgrade files are NOT zero-padded on OEDI (upgrade1.parquet)
_EXPECTED_S3_UPGRADE1_URI = (
    "s3://oedi-data-lake/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock"
    "/2025/resstock_amy2018_release_1"
    "/metadata_and_annual_results/national/full/parquet/upgrade1.parquet"
)


def _make_mgr(tmp_path: Path, **kwargs: Any) -> BuildStockDataManager:
    """Create a BuildStockDataManager with cache stored under tmp_path."""
    return BuildStockDataManager(
        dataset_type=kwargs.pop("dataset_type", "resstock"),
        release=kwargs.pop("release", "2025_release_1"),
        cache_enabled=kwargs.pop("cache_enabled", False),
        cache_dir=tmp_path / "cache",
        **kwargs,
    )


# ===========================================================================
# Unit tests — DatasetConfig
# ===========================================================================


class TestDatasetConfig:
    def test_all_configs_present(self) -> None:
        expected = {
            "resstock_2024_release_2",
            "resstock_2025_release_1",
            "comstock_2024_release_2",
            "comstock_2025_release_3",
        }
        assert expected.issubset(BuildStockDataManager.DATASET_CONFIGS.keys())

    def test_resstock_2025_required_fields(self) -> None:
        cfg = _RS25_CONFIG
        assert cfg.base_uri.startswith("https://")
        assert cfg.metadata_structure == "parquet"
        assert cfg.timeseries_structure == "by_state"
        assert cfg.building_id_column == "bldg_id"
        assert cfg.state_column == "in.state"
        assert cfg.expected_resolution_minutes == 15
        assert 0 in cfg.supported_upgrades
        assert 16 in cfg.supported_upgrades
        assert 17 not in cfg.supported_upgrades

    def test_resstock_2025_metadata_path_fields(self) -> None:
        cfg = _RS25_CONFIG
        assert cfg.metadata_path == "metadata_and_annual_results/national/full/parquet"
        assert cfg.baseline_filename == "upgrade0.parquet"
        assert "{upgrade_id" in cfg.upgrade_filename

    def test_resstock_2025_fuel_columns(self) -> None:
        cfg = _RS25_CONFIG
        assert cfg.natural_gas_energy_column is not None
        assert cfg.natural_gas_bill_column is not None
        assert "kwh" in cfg.natural_gas_energy_column.lower()

    def test_comstock_configs_have_40_upgrades(self) -> None:
        for key in ("comstock_2024_release_2", "comstock_2025_release_3"):
            cfg = BuildStockDataManager.DATASET_CONFIGS[key]
            assert len(cfg.supported_upgrades) == 40
            assert cfg.supported_upgrades == list(range(40))


# ===========================================================================
# Unit tests — _to_s3_uri
# ===========================================================================


class TestToS3Uri:
    def test_converts_oedi_https_to_s3(self) -> None:
        https = f"{_OEDI_HTTPS_BASE}some/path/file.parquet"
        s3 = BuildStockDataManager._to_s3_uri(https)
        assert s3 == f"{_OEDI_S3_BASE}some/path/file.parquet"

    def test_non_oedi_url_unchanged(self) -> None:
        url = "https://example.com/data.parquet"
        assert BuildStockDataManager._to_s3_uri(url) == url

    def test_already_s3_uri_unchanged(self) -> None:
        uri = "s3://oedi-data-lake/some/path.parquet"
        assert BuildStockDataManager._to_s3_uri(uri) == uri


# ===========================================================================
# Unit tests — __init__ / config validation
# ===========================================================================


class TestInit:
    def test_valid_resstock_2025(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        assert mgr.dataset_type == "resstock"
        assert mgr.release == "2025_release_1"
        assert mgr.config is _RS25_CONFIG

    def test_valid_comstock(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path, dataset_type="comstock", release="2024_release_2")
        assert mgr.dataset_type == "comstock"

    def test_invalid_config_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Unsupported dataset configuration"):
            _make_mgr(tmp_path, dataset_type="resstock", release="9999_release_99")

    def test_metadata_uri_is_s3(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        assert mgr.metadata_uri.startswith("s3://")

    def test_custom_metadata_uri_respected(self, tmp_path: Path) -> None:
        custom = "s3://my-bucket/custom.parquet"
        mgr = _make_mgr(tmp_path, metadata_uri=custom)
        assert mgr.metadata_uri == custom

    def test_cache_disabled_no_cache_dir_created(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path, cache_enabled=False)
        assert not hasattr(mgr, "cache_metadata_dir")

    def test_cache_enabled_creates_dirs(self, tmp_path: Path) -> None:
        mgr = BuildStockDataManager(
            dataset_type="resstock",
            release="2025_release_1",
            cache_enabled=True,
            cache_dir=tmp_path / "cache",
        )
        assert mgr.cache_metadata_dir.exists()
        assert mgr.cache_timeseries_dir.exists()


# ===========================================================================
# Unit tests — URI construction
# ===========================================================================


class TestMetadataUriConstruction:
    def test_default_metadata_uri_resstock_2025(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        assert mgr.metadata_uri == _EXPECTED_S3_METADATA_URI

    def test_default_metadata_uri_resstock_2024(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path, release="2024_release_2")
        assert "baseline_metadata_and_annual_results.parquet" in mgr.metadata_uri
        assert mgr.metadata_uri.startswith("s3://")

    def test_upgrade_zero_returns_metadata_uri(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        assert mgr.get_metadata_uri_for_upgrade(0) == mgr.metadata_uri

    def test_upgrade_one_uri(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        uri = mgr.get_metadata_uri_for_upgrade(1)
        assert uri == _EXPECTED_S3_UPGRADE1_URI

    def test_upgrade_uri_not_zero_padded_resstock_2025(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        uri = mgr.get_metadata_uri_for_upgrade(9)
        assert uri.endswith("upgrade9.parquet")

    def test_upgrade_uri_double_digit(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        uri = mgr.get_metadata_uri_for_upgrade(16)
        assert "upgrade16" in uri


class TestLoadProfileUriConstruction:
    def test_valid_uri_structure(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        uri = mgr._get_load_profile_uri(building_id=100, upgrade=0, state="CO")
        assert uri.startswith("s3://oedi-data-lake/")
        assert "upgrade=0" in uri
        assert "state=CO" in uri
        assert uri.endswith("100-0.parquet")

    def test_uri_contains_correct_base_path(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        uri = mgr._get_load_profile_uri(building_id=42, upgrade=0, state="AZ")
        assert "resstock_amy2018_release_1" in uri
        assert "timeseries_individual_buildings/by_state" in uri

    def test_invalid_upgrade_raises(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        with pytest.raises(ValueError, match="not supported"):
            mgr._get_load_profile_uri(building_id=1, upgrade=99, state="CO")

    def test_state_lookup_from_mapping(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr._state_mapping[999] = "TX"
        uri = mgr._get_load_profile_uri(building_id=999, upgrade=0)
        assert "state=TX" in uri

    def test_missing_state_raises(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        with pytest.raises(ValueError, match="Could not determine state"):
            mgr._get_load_profile_uri(building_id=999_999, upgrade=0)


# ===========================================================================
# Unit tests — column helpers
# ===========================================================================


class TestDeduplicateColumns:
    def test_adds_required_columns(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        result = mgr._deduplicate_columns(["some.col"])
        assert "bldg_id" in result
        assert "in.state" in result

    def test_preserves_order(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        result = mgr._deduplicate_columns(["c", "a", "b"])
        assert result.index("c") < result.index("a") < result.index("b")

    def test_removes_duplicates(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        result = mgr._deduplicate_columns(["x", "x", "bldg_id"])
        assert result.count("x") == 1
        assert result.count("bldg_id") == 1


class TestEstimateDownloadSize:
    def test_single_building_single_upgrade(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        size = mgr.estimate_download_size([1], [0])
        assert size == 90_000

    def test_scales_with_buildings_and_upgrades(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        assert mgr.estimate_download_size(list(range(10)), [0, 1]) == 10 * 2 * 90_000

    def test_default_upgrades_is_baseline_only(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        assert mgr.estimate_download_size([1, 2, 3]) == 3 * 90_000


class TestGetFuelColumns:
    def test_natural_gas_columns_resstock_2025(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        energy_col, bill_col = mgr.get_fuel_columns_for_type("natural_gas")
        assert energy_col is not None
        assert bill_col is not None

    def test_unknown_fuel_type_raises(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        with pytest.raises(ValueError, match="Unknown fuel type"):
            mgr.get_fuel_columns_for_type("coal")


# ===========================================================================
# Unit tests — DataFrame helpers (no network)
# ===========================================================================


@pytest.fixture()
def sample_metadata() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "bldg_id": [1, 2, 3, 4, 5],
            "in.state": ["CO", "CO", "AZ", "AZ", "TX"],
            "in.geometry_building_type_acs": ["Single-Family Detached"] * 3
            + ["Mobile Home"] * 2,
            "out.some_col": [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    )


class TestGetAvailableFilterColumns:
    def test_returns_only_in_columns(
        self, tmp_path: Path, sample_metadata: pl.DataFrame
    ) -> None:
        mgr = _make_mgr(tmp_path)
        cols = mgr.get_available_filter_columns(sample_metadata)
        assert all(c.startswith("in.") for c in cols)
        assert "out.some_col" not in cols
        assert "bldg_id" not in cols

    def test_excludes_state_column(
        self, tmp_path: Path, sample_metadata: pl.DataFrame
    ) -> None:
        mgr = _make_mgr(tmp_path)
        cols = mgr.get_available_filter_columns(sample_metadata)
        assert "in.state" not in cols

    def test_result_is_sorted(
        self, tmp_path: Path, sample_metadata: pl.DataFrame
    ) -> None:
        mgr = _make_mgr(tmp_path)
        cols = mgr.get_available_filter_columns(sample_metadata)
        assert cols == sorted(cols)


class TestGetColumnUniqueValues:
    def test_returns_sorted_unique(
        self, tmp_path: Path, sample_metadata: pl.DataFrame
    ) -> None:
        mgr = _make_mgr(tmp_path)
        vals = mgr.get_column_unique_values(sample_metadata, "in.state")
        assert vals == ["AZ", "CO", "TX"]

    def test_missing_column_raises(
        self, tmp_path: Path, sample_metadata: pl.DataFrame
    ) -> None:
        mgr = _make_mgr(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            mgr.get_column_unique_values(sample_metadata, "nonexistent")


class TestFilterMetadata:
    def test_scalar_filter(self, tmp_path: Path, sample_metadata: pl.DataFrame) -> None:
        mgr = _make_mgr(tmp_path)
        result = mgr.filter_metadata(sample_metadata, {"in.state": "CO"})
        assert len(result) == 2
        assert result["in.state"].unique().to_list() == ["CO"]

    def test_list_filter(self, tmp_path: Path, sample_metadata: pl.DataFrame) -> None:
        mgr = _make_mgr(tmp_path)
        result = mgr.filter_metadata(sample_metadata, {"in.state": ["CO", "AZ"]})
        assert len(result) == 4

    def test_missing_column_raises(
        self, tmp_path: Path, sample_metadata: pl.DataFrame
    ) -> None:
        mgr = _make_mgr(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            mgr.filter_metadata(sample_metadata, {"nonexistent": "x"})

    def test_multiple_filters_are_and(
        self, tmp_path: Path, sample_metadata: pl.DataFrame
    ) -> None:
        mgr = _make_mgr(tmp_path)
        result = mgr.filter_metadata(
            sample_metadata,
            {
                "in.state": "CO",
                "in.geometry_building_type_acs": "Single-Family Detached",
            },
        )
        assert len(result) == 2


class TestStateMappingAndBuildingState:
    def test_update_state_mapping(
        self, tmp_path: Path, sample_metadata: pl.DataFrame
    ) -> None:
        mgr = _make_mgr(tmp_path)
        mgr._update_state_mapping(sample_metadata)
        assert mgr.get_building_state(1) == "CO"
        assert mgr.get_building_state(3) == "AZ"
        assert mgr.get_building_state(5) == "TX"

    def test_missing_building_returns_none(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        assert mgr.get_building_state(999_999) is None

    def test_state_mapping_clears_on_update(
        self, tmp_path: Path, sample_metadata: pl.DataFrame
    ) -> None:
        mgr = _make_mgr(tmp_path)
        mgr._state_mapping[999] = "OLD"
        mgr._update_state_mapping(sample_metadata)
        assert mgr.get_building_state(999) is None


# ===========================================================================
# Unit tests — get_upgrade_applicability
# ===========================================================================


class TestGetUpgradeApplicability:
    """Tests for the get_upgrade_applicability method (no network)."""

    def test_baseline_returns_all_true(self, tmp_path: Path) -> None:
        """Upgrade 0 is always 100% applicable."""
        mgr = _make_mgr(tmp_path)
        building_ids = [1, 2, 3, 4, 5]
        result = mgr.get_upgrade_applicability(0, building_ids)
        assert result == {bid: True for bid in building_ids}

    def test_baseline_empty_list(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        assert mgr.get_upgrade_applicability(0, []) == {}

    def test_result_covers_all_building_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every building_id in the input appears in the output dict."""
        mgr = _make_mgr(tmp_path)
        building_ids = [10, 20, 30]

        # Simulate upgrade metadata returning applicability for only 2 of 3 buildings
        fake_df = pl.DataFrame({"bldg_id": [10, 20], "applicability": [True, False]})

        def _fake_scan(*args: Any, **kwargs: Any):
            return fake_df.lazy()

        monkeypatch.setattr(pl, "scan_parquet", _fake_scan)
        result = mgr.get_upgrade_applicability(1, building_ids)
        assert set(result.keys()) == set(building_ids)

    def test_absent_buildings_default_to_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Buildings missing from upgrade metadata are marked not applicable."""
        mgr = _make_mgr(tmp_path)
        building_ids = [1, 2, 99]  # 99 not in upgrade metadata

        fake_df = pl.DataFrame({"bldg_id": [1, 2], "applicability": [True, True]})

        def _fake_scan(*args: Any, **kwargs: Any):
            return fake_df.lazy()

        monkeypatch.setattr(pl, "scan_parquet", _fake_scan)
        result = mgr.get_upgrade_applicability(1, building_ids)
        assert result[99] is False

    def test_applicability_values_are_bool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Values in the returned dict are plain Python bools."""
        mgr = _make_mgr(tmp_path)
        building_ids = [1, 2]

        # Use Int32 0/1 to verify casting to bool
        fake_df = pl.DataFrame(
            {"bldg_id": [1, 2], "upgrade.duct_leakage_and_insulation": [1, None]},
            schema={
                "bldg_id": pl.Int32,
                "upgrade.duct_leakage_and_insulation": pl.Int32,
            },
        )

        def _fake_scan(*args: Any, **kwargs: Any):
            return fake_df.lazy()

        monkeypatch.setattr(pl, "scan_parquet", _fake_scan)
        result = mgr.get_upgrade_applicability(1, building_ids)
        assert isinstance(result[1], bool)
        assert isinstance(result[2], bool)
        assert result[1] is True
        assert result[2] is False

    # def test_partial_applicability(
    #     self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    # ) -> None:
    #     """Mix of True/False/absent buildings is handled correctly."""
    #     mgr = _make_mgr(tmp_path)
    #     building_ids = [10, 20, 30, 40]  # 40 absent

    #     fake_df = pl.DataFrame(
    #         {
    #             "bldg_id": [10, 20, 30],
    #             "upgrade.duct_leakage_and_insulation": [True, False, True],
    #         }
    #     )

    #     def _fake_scan(*args: Any, **kwargs: Any):
    #         return fake_df.lazy()

    #     monkeypatch.setattr(pl, "scan_parquet", _fake_scan)
    #     result = mgr.get_upgrade_applicability(1, building_ids)
    #     assert result[10] is True
    #     assert result[20] is False
    #     assert result[30] is True
    #     assert result[40] is False  # absent → not applicable

    def test_scan_failure_falls_back_to_all_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the S3 read fails, all buildings are treated as applicable."""
        mgr = _make_mgr(tmp_path)
        building_ids = [1, 2, 3]

        def _explode(*args: Any, **kwargs: Any):
            raise OSError("network error")

        monkeypatch.setattr(pl, "scan_parquet", _explode)
        result = mgr.get_upgrade_applicability(1, building_ids)
        assert result == {1: True, 2: True, 3: True}

    def test_upgrade_uri_used_for_non_baseline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The URI passed to scan_parquet is the upgrade (not baseline) metadata URI."""
        mgr = _make_mgr(tmp_path)
        captured_uris: list[str] = []
        expected_uri = mgr.get_metadata_uri_for_upgrade(3)

        fake_df = pl.DataFrame(
            {
                "bldg_id": pl.Series([], dtype=pl.Int32),
                "applicability": pl.Series([], dtype=pl.Boolean),
            }
        )

        def _fake_scan(uri: str, **kwargs: Any):
            captured_uris.append(uri)
            return fake_df.lazy()

        monkeypatch.setattr(pl, "scan_parquet", _fake_scan)
        mgr.get_upgrade_applicability(3, [1, 2])
        assert captured_uris[0] == expected_uri


# ===========================================================================
# Unit tests — get_dataset_weight
# ===========================================================================


class TestGetDatasetWeight:
    """Unit tests for BuildStockDataManager.get_dataset_weight (no network)."""

    def test_get_dataset_weight_resstock_returns_scalar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _make_mgr(tmp_path)
        fake_lf = pl.DataFrame({"weight": [188.0]}).lazy()

        def _fake_scan(*args: Any, **kwargs: Any):
            return fake_lf

        monkeypatch.setattr(pl, "scan_parquet", _fake_scan)
        weight = mgr.get_dataset_weight()
        assert weight == 188.0
        assert isinstance(weight, float)

    def test_get_dataset_weight_comstock_returns_none(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path, dataset_type="comstock", release="2024_release_2")
        assert mgr.get_dataset_weight() is None

    def test_get_dataset_weight_cached_after_first_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _make_mgr(tmp_path)
        call_count = {"n": 0}
        fake_lf = pl.DataFrame({"weight": [42.5]}).lazy()

        def _fake_scan(*args: Any, **kwargs: Any):
            call_count["n"] += 1
            return fake_lf

        monkeypatch.setattr(pl, "scan_parquet", _fake_scan)
        first = mgr.get_dataset_weight()
        second = mgr.get_dataset_weight()
        assert first == 42.5
        assert second == 42.5
        assert call_count["n"] == 1


# ===========================================================================
# Integration tests — live OEDI S3 access
# ===========================================================================

_N_BUILDINGS = 10
_UPGRADE = 0
_HOURS_PER_YEAR = 8760


@pytest.fixture(scope="module")
def integration_mgr(tmp_path_factory: pytest.TempPathFactory) -> BuildStockDataManager:
    """Session-scoped BuildStockDataManager for integration tests."""
    cache_dir = tmp_path_factory.mktemp("integration_cache")
    return BuildStockDataManager(
        dataset_type="resstock",
        release="2025_release_1",
        cache_enabled=True,
        cache_dir=cache_dir,
        max_workers=_N_BUILDINGS,
    )


@pytest.mark.integration
class TestIntegrationApplicability:
    @pytest.fixture(autouse=True)
    def _ensure_metadata_loaded(self, integration_mgr: BuildStockDataManager) -> None:
        """Load metadata so state_mapping and building_ids are available."""
        if not integration_mgr._state_mapping:
            integration_mgr.load_metadata(
                columns=[
                    integration_mgr.config.building_id_column,
                    integration_mgr.config.state_column,
                ]
            )

    def test_applicability_returns_correct_keys(
        self, integration_mgr: BuildStockDataManager
    ) -> None:
        """Result dict contains an entry for every queried building."""
        sample_ids = integration_mgr.get_building_ids()[:50]
        result = integration_mgr.get_upgrade_applicability(1, sample_ids)
        assert set(result.keys()) == set(sample_ids)

    def test_applicability_values_are_bool(
        self, integration_mgr: BuildStockDataManager
    ) -> None:
        """All values are plain Python bools."""
        sample_ids = integration_mgr.get_building_ids()[:50]
        result = integration_mgr.get_upgrade_applicability(13, sample_ids)
        assert all(isinstance(v, bool) for v in result.values())

    def test_some_buildings_applicable_some_not(
        self, integration_mgr: BuildStockDataManager
    ) -> None:
        """
        For a real upgrade, at least some buildings should be applicable
        and at least some should not (i.e. not all True and not all False
        across a reasonably large sample).
        """
        sample_ids = integration_mgr.get_building_ids()[500:1000]
        result = integration_mgr.get_upgrade_applicability(13, sample_ids)
        n_applicable = sum(1 for v in result.values() if v)
        assert 0 < n_applicable < len(sample_ids), (
            f"Expected mixed applicability, got {n_applicable}/{len(sample_ids)} applicable"
        )

    def test_baseline_always_applicable(
        self, integration_mgr: BuildStockDataManager
    ) -> None:
        """Upgrade 0 (baseline) must return True for every building."""
        sample_ids = integration_mgr.get_building_ids()[:100]
        result = integration_mgr.get_upgrade_applicability(0, sample_ids)
        assert all(result.values())

    def test_applicability_fetch_timing(
        self, integration_mgr: BuildStockDataManager
    ) -> None:
        """Fetch applicability for a large sample and report timing."""
        sample_ids = integration_mgr.get_building_ids()[:2_000]
        t0 = time.perf_counter()
        result = integration_mgr.get_upgrade_applicability(13, sample_ids)
        elapsed = time.perf_counter() - t0
        n_app = sum(1 for v in result.values() if v)
        print(
            f"\n[Applicability] upgrade=1  sample={len(sample_ids):,}  "
            f"applicable={n_app:,} ({n_app / len(sample_ids) * 100:.1f}%)  "
            f"elapsed={elapsed:.2f}s"
        )
        assert elapsed < 30, "Applicability fetch took unexpectedly long"


@pytest.mark.integration
class TestIntegrationMetadata:
    def test_load_metadata_shape_and_columns(
        self, integration_mgr: BuildStockDataManager
    ) -> None:
        """Load the ResStock 2025 r1 baseline metadata and validate basic structure."""
        t0 = time.perf_counter()
        metadata = integration_mgr.load_metadata(
            columns=[
                integration_mgr.config.building_id_column,
                integration_mgr.config.state_column,
            ]
        )
        elapsed = time.perf_counter() - t0

        print(
            f"\n[Metadata] rows={len(metadata):,}  "
            f"cols={len(metadata.columns)}  "
            f"elapsed={elapsed:.2f}s"
        )

        assert isinstance(metadata, pl.DataFrame)
        assert len(metadata) > 100_000, "Expected at least 100k buildings"
        assert integration_mgr.config.building_id_column in metadata.columns
        assert integration_mgr.config.state_column in metadata.columns

    def test_state_mapping_populated_after_load(
        self, integration_mgr: BuildStockDataManager
    ) -> None:
        """State mapping should be populated once metadata is loaded."""
        # Ensure metadata is loaded (module fixture is shared; may already be loaded)
        if integration_mgr._metadata is None:
            integration_mgr.load_metadata(
                columns=[
                    integration_mgr.config.building_id_column,
                    integration_mgr.config.state_column,
                ]
            )
        assert len(integration_mgr._state_mapping) > 0

    def test_metadata_building_ids_are_integers(
        self, integration_mgr: BuildStockDataManager
    ) -> None:
        meta = integration_mgr._metadata
        assert meta is not None
        dtype = meta[integration_mgr.config.building_id_column].dtype
        assert dtype in (pl.Int32, pl.Int64, pl.UInt32, pl.UInt64)


@pytest.mark.integration
class TestIntegrationTimeseriesDownload:
    @pytest.fixture(autouse=True)
    def _ensure_metadata_loaded(self, integration_mgr: BuildStockDataManager) -> None:
        """Load metadata (fast if cached) so state_mapping is available."""
        if not integration_mgr._state_mapping:
            integration_mgr.load_metadata(
                columns=[
                    integration_mgr.config.building_id_column,
                    integration_mgr.config.state_column,
                ]
            )

    def test_download_ten_buildings_baseline(
        self,
        integration_mgr: BuildStockDataManager,
        tmp_path: Path,
    ) -> None:
        """Download 10 building timeseries, validate structure, report per-file timing."""
        building_ids = integration_mgr.get_building_ids()[:_N_BUILDINGS]
        assert len(building_ids) == _N_BUILDINGS, (
            "Need at least 10 buildings in metadata"
        )

        ts_dir = tmp_path / "timeseries"
        progress_log: list[tuple[int, float]] = []
        t_start = time.perf_counter()
        last_t = t_start

        def _cb(done: int, total: int, msg: str) -> None:
            nonlocal last_t
            now = time.perf_counter()
            progress_log.append((done, now - last_t))
            last_t = now

        ts_data = integration_mgr.download_timeseries_with_progress(
            building_ids=building_ids,
            upgrades=[_UPGRADE],
            output_dir=ts_dir,
            progress_cb=_cb,
        )
        total_elapsed = time.perf_counter() - t_start

        # ── Print timing summary ──────────────────────────────────────────────
        per_file_times = [t for _, t in progress_log]
        if per_file_times:
            avg = sum(per_file_times) / len(per_file_times)
            minimum = min(per_file_times)
            maximum = max(per_file_times)
        else:
            avg = minimum = maximum = 0.0

        print(
            f"\n[Timeseries] buildings={_N_BUILDINGS}  "
            f"total={total_elapsed:.2f}s  "
            f"per-file avg={avg:.2f}s  min={minimum:.2f}s  max={maximum:.2f}s"
        )

        # ── Structural assertions ─────────────────────────────────────────────
        assert len(ts_data) == _N_BUILDINGS, (
            f"Expected {_N_BUILDINGS} results, got {len(ts_data)}"
        )

        load_col = integration_mgr.config.default_load_column
        for key, df in ts_data.items():
            assert isinstance(df, pl.DataFrame), f"{key}: expected DataFrame"
            assert "timestamp" in df.columns, f"{key}: missing timestamp column"
            assert load_col in df.columns, f"{key}: missing load column {load_col!r}"
            assert len(df) == _HOURS_PER_YEAR, (
                f"{key}: expected {_HOURS_PER_YEAR} hourly rows, got {len(df)}"
            )
            assert df[load_col].is_nan().sum() == 0, f"{key}: NaN values in load column"
            assert (df[load_col] >= 0).all(), f"{key}: negative load values"

    def test_output_files_written_to_disk(
        self,
        integration_mgr: BuildStockDataManager,
        tmp_path: Path,
    ) -> None:
        """Verify parquet files are written alongside the in-memory results."""
        building_ids = integration_mgr.get_building_ids()[:_N_BUILDINGS]
        ts_dir = tmp_path / "ts_files"

        integration_mgr.download_timeseries_with_progress(
            building_ids=building_ids,
            upgrades=[_UPGRADE],
            output_dir=ts_dir,
            progress_cb=None,
        )

        for bid in building_ids:
            expected = ts_dir / f"{bid}_{_UPGRADE}.parquet"
            assert expected.exists(), f"Missing output file: {expected}"

    def test_load_cached_timeseries_matches_download(
        self,
        integration_mgr: BuildStockDataManager,
        tmp_path: Path,
    ) -> None:
        """load_cached_timeseries should return the same rows as the download."""
        building_ids = integration_mgr.get_building_ids()[:_N_BUILDINGS]
        ts_dir = tmp_path / "cache_test"

        downloaded = integration_mgr.download_timeseries_with_progress(
            building_ids=building_ids,
            upgrades=[_UPGRADE],
            output_dir=ts_dir,
            progress_cb=None,
        )
        cached = integration_mgr.load_cached_timeseries(
            building_ids=building_ids,
            upgrades=[_UPGRADE],
            output_dir=ts_dir,
        )

        assert set(downloaded.keys()) == set(cached.keys())
        for key in downloaded:
            assert len(downloaded[key]) == len(cached[key])
