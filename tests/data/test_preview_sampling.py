"""Phase 9: stratified fast-preview sampling.

Covers ``BuildStockDataManager.sample_metadata_stratified`` (per-stratum
cap, weight-sum preservation, determinism, small-stratum passthrough) and
the ResStock ``_sample_scale`` weight fold in ``_build_load_records``.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from buildstock_rate_impact_calculator.data.buildstock_manager import (
    BuildStockDataManager,
)


def _mgr(tmp_path: Path, dataset_type: str = "comstock") -> BuildStockDataManager:
    release = "2025_release_3" if dataset_type == "comstock" else "2024_release_2"
    return BuildStockDataManager(
        dataset_type=dataset_type,
        release=release,
        cache_enabled=True,
        cache_dir=tmp_path / "cache",
    )


def _metadata(n_office: int = 50, n_retail: int = 8) -> pl.DataFrame:
    """ComStock-shaped metadata: a big Office stratum and a small Retail one."""
    return pl.DataFrame(
        {
            "bldg_id": list(range(1, n_office + n_retail + 1)),
            "weight": [2.0] * n_office + [5.0] * n_retail,
            "in.comstock_building_type": ["Office"] * n_office + ["Retail"] * n_retail,
            "in.state": ["CO"] * (n_office + n_retail),
        }
    )


class TestSampleMetadataStratified:
    def test_per_stratum_cap(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        out = mgr.sample_metadata_stratified(_metadata(), n_per_stratum=10)
        counts = (
            out.group_by("in.comstock_building_type")
            .len()
            .sort("in.comstock_building_type")
        )
        by_type = dict(zip(counts["in.comstock_building_type"], counts["len"]))
        assert by_type["Office"] == 10  # capped
        assert by_type["Retail"] == 8  # smaller than cap → fully kept

    def test_weight_sum_preserved_per_stratum(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        meta = _metadata()
        out = mgr.sample_metadata_stratified(meta, n_per_stratum=10)
        for stratum in ("Office", "Retail"):
            original = meta.filter(pl.col("in.comstock_building_type") == stratum)[
                "weight"
            ].sum()
            sampled = out.filter(pl.col("in.comstock_building_type") == stratum)
            rescaled = (sampled["weight"] * sampled["_sample_scale"]).sum()
            assert rescaled == pytest.approx(original)

    def test_small_stratum_scale_is_one(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        out = mgr.sample_metadata_stratified(_metadata(), n_per_stratum=10)
        retail = out.filter(pl.col("in.comstock_building_type") == "Retail")
        assert all(s == pytest.approx(1.0) for s in retail["_sample_scale"])

    def test_deterministic_with_fixed_seed(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        meta = _metadata()
        first = mgr.sample_metadata_stratified(meta, n_per_stratum=10, seed=42)
        second = mgr.sample_metadata_stratified(meta, n_per_stratum=10, seed=42)
        assert sorted(first["bldg_id"].to_list()) == sorted(second["bldg_id"].to_list())

    def test_fallback_to_state_column(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        meta = _metadata().drop("in.comstock_building_type")
        out = mgr.sample_metadata_stratified(meta, n_per_stratum=10)
        # Single 'CO' state stratum → 10 rows kept
        assert out.height == 10
        assert "_sample_scale" in out.columns

    def test_no_weight_column_uses_row_ratio(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        meta = _metadata().drop("weight")
        out = mgr.sample_metadata_stratified(meta, n_per_stratum=10)
        office = out.filter(pl.col("in.comstock_building_type") == "Office")
        assert all(s == pytest.approx(5.0) for s in office["_sample_scale"])


class TestResStockSampleScaleInLoadRecords:
    def test_record_weight_folds_sample_scale(self) -> None:
        from buildstock_rate_impact_calculator.ui.pages.page_05_run import (
            _assign_row_uid,
            _build_load_records,
        )

        meta = pl.DataFrame(
            {
                "bldg_id": [1, 2],
                "weight": [188.0, 188.0],
                "_sample_scale": [2.5, 1.0],
            }
        )
        meta = _assign_row_uid(meta, "resstock", "bldg_id")
        ts = {
            "1_0": pl.DataFrame({"load": [1.0] * 8760}),
            "2_0": pl.DataFrame({"load": [1.0] * 8760}),
        }
        records = _build_load_records(
            metadata=meta,
            ts_data=ts,
            dataset_type="resstock",
            load_column="load",
            scalar_weight=188.0,
            upgrade_id=0,
            applicable_set={1, 2},
            bldg_col="bldg_id",
        )
        by_bldg = {rec["bldg_id"]: rec for rec in records}
        assert by_bldg[1]["weight"] == pytest.approx(188.0 * 2.5)
        assert by_bldg[2]["weight"] == pytest.approx(188.0)

    def test_missing_scale_defaults_to_one(self) -> None:
        from buildstock_rate_impact_calculator.ui.pages.page_05_run import (
            _assign_row_uid,
            _build_load_records,
        )

        meta = _assign_row_uid(
            pl.DataFrame({"bldg_id": [1], "weight": [188.0]}), "resstock", "bldg_id"
        )
        records = _build_load_records(
            metadata=meta,
            ts_data={"1_0": pl.DataFrame({"load": [1.0] * 8760})},
            dataset_type="resstock",
            load_column="load",
            scalar_weight=188.0,
            upgrade_id=0,
            applicable_set={1},
            bldg_col="bldg_id",
        )
        assert records[0]["weight"] == pytest.approx(188.0)
