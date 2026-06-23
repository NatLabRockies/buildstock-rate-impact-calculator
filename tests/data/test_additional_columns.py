"""
Phase 4 — Additional characteristic columns: tests for
``get_app_metadata_columns``, ``merge_additional_columns``, and the
delta-fetch path inside ``_cached_metadata_fetch``.

These are colocated in their own file (rather than appended to
test_buildstock_manager.py) to keep Phase-4 surface area discoverable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock

import polars as pl
import pytest

from buildstock_rate_impact_calculator.data.buildstock_manager import (
    BuildStockDataManager,
)


def _make_mgr(tmp_path: Path, **kwargs: Any) -> BuildStockDataManager:
    """Cache-enabled manager rooted at ``tmp_path/cache``."""
    return BuildStockDataManager(
        dataset_type=kwargs.pop("dataset_type", "resstock"),
        release=kwargs.pop("release", "2025_release_1"),
        cache_enabled=kwargs.pop("cache_enabled", True),
        cache_dir=tmp_path / "cache",
        **kwargs,
    )


# ===========================================================================
# get_app_metadata_columns(additional_columns=...)
# ===========================================================================


class TestGetAppMetadataColumnsExtras:
    def test_no_extras_matches_existing_behavior(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        before = mgr.get_app_metadata_columns()
        after = mgr.get_app_metadata_columns(additional_columns=None)
        assert before == after

    def test_extras_appended_to_base_columns(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        base = mgr.get_app_metadata_columns()
        result = mgr.get_app_metadata_columns(
            additional_columns=["in.vintage", "in.heating_setpoint"]
        )
        assert "in.vintage" in result
        assert "in.heating_setpoint" in result
        # All base columns still present
        for col in base:
            assert col in result

    def test_extras_deduplicated_against_base(self, tmp_path: Path) -> None:
        """Extras that duplicate base/filter columns don't appear twice."""
        mgr = _make_mgr(tmp_path)
        base = mgr.get_app_metadata_columns()
        # Pick one column that's already in base
        already_there = base[0]
        result = mgr.get_app_metadata_columns(
            additional_columns=[already_there, "in.vintage"]
        )
        assert result.count(already_there) == 1
        assert "in.vintage" in result

    def test_empty_extras_list_is_noop(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        assert (
            mgr.get_app_metadata_columns(additional_columns=[])
            == mgr.get_app_metadata_columns()
        )


# ===========================================================================
# merge_additional_columns
# ===========================================================================


def _write_parquet(path: Path, df: pl.DataFrame) -> str:
    """Write ``df`` to ``path`` and return a ``file://`` URI."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    return f"file:///{path.as_posix().lstrip('/')}"


class TestMergeAdditionalColumns:
    def test_joins_on_bldg_id(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        base = pl.DataFrame({"bldg_id": [1, 2, 3], "weight": [1.0, 2.0, 3.0]})
        source_path = tmp_path / "source.parquet"
        _write_parquet(
            source_path,
            pl.DataFrame(
                {
                    "bldg_id": [1, 2, 3],
                    "in.vintage": ["1980s", "1990s", "2000s"],
                    "irrelevant_col": [10, 20, 30],
                }
            ),
        )

        merged = mgr.merge_additional_columns(
            existing_df=base,
            uri=f"file:///{source_path.as_posix().lstrip('/')}",
            extras=["in.vintage"],
            bldg_col="bldg_id",
        )
        assert "in.vintage" in merged.columns
        assert "irrelevant_col" not in merged.columns
        assert merged.sort("bldg_id")["in.vintage"].to_list() == [
            "1980s",
            "1990s",
            "2000s",
        ]
        # All base columns preserved
        assert merged.sort("bldg_id")["weight"].to_list() == [1.0, 2.0, 3.0]

    def test_left_join_preserves_existing_rows(self, tmp_path: Path) -> None:
        """Buildings absent from the source parquet still appear, with null extras."""
        mgr = _make_mgr(tmp_path)
        base = pl.DataFrame({"bldg_id": [1, 2, 3], "weight": [1.0, 2.0, 3.0]})
        source_path = tmp_path / "source.parquet"
        _write_parquet(
            source_path,
            pl.DataFrame({"bldg_id": [1, 2], "in.vintage": ["1980s", "1990s"]}),
        )

        merged = mgr.merge_additional_columns(
            base,
            f"file:///{source_path.as_posix().lstrip('/')}",
            ["in.vintage"],
            "bldg_id",
        )
        assert merged.height == 3
        assert merged.sort("bldg_id")["in.vintage"].to_list() == [
            "1980s",
            "1990s",
            None,
        ]

    def test_missing_extras_in_source_silently_dropped(self, tmp_path: Path) -> None:
        """If an extra column doesn't exist in the source file, it's skipped
        (not an error). Caller gets back the existing frame unchanged for
        that column."""
        mgr = _make_mgr(tmp_path)
        base = pl.DataFrame({"bldg_id": [1, 2], "weight": [1.0, 2.0]})
        source_path = tmp_path / "source.parquet"
        _write_parquet(
            source_path,
            pl.DataFrame({"bldg_id": [1, 2], "in.vintage": ["a", "b"]}),
        )

        merged = mgr.merge_additional_columns(
            base,
            f"file:///{source_path.as_posix().lstrip('/')}",
            ["in.vintage", "in.does_not_exist"],
            "bldg_id",
        )
        assert "in.vintage" in merged.columns
        # The missing column is not synthesized; caller can detect its absence.
        assert "in.does_not_exist" not in merged.columns


# ===========================================================================
# _cached_metadata_fetch — delta-fetch path
# ===========================================================================


class TestCachedMetadataFetchDelta:
    """When the base-columns cache exists and the caller requests extras,
    only ``[bldg_col, *extras]`` should be projected from S3."""

    def test_delta_fetch_when_base_cache_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _make_mgr(tmp_path)
        bldg_col = mgr.config.building_id_column

        base_cols = [bldg_col, "weight", "in.state"]
        extras = ["in.vintage"]
        full_cols = base_cols + extras

        # Source parquet on disk — local file:// URI
        source_path = tmp_path / "source.parquet"
        _write_parquet(
            source_path,
            pl.DataFrame(
                {
                    bldg_col: [1, 2],
                    "weight": [188.0, 188.0],
                    "in.state": ["CO", "CO"],
                    "in.vintage": ["1980s", "1990s"],
                }
            ),
        )
        uri = f"file:///{source_path.as_posix().lstrip('/')}"

        # 1) Prime base-only cache by fetching without extras
        mgr._cached_metadata_fetch(uri, base_cols, force_reload=False)

        # 2) Spy on _scan_select_available so we can inspect what gets projected
        real = mgr._scan_select_available
        calls: List[List[str]] = []

        def _spy(uri_arg: str, cols: List[str]) -> pl.DataFrame:
            calls.append(list(cols))
            return real(uri_arg, cols)

        monkeypatch.setattr(mgr, "_scan_select_available", _spy)

        # 3) Request full_cols — should hit the delta-fetch path
        result = mgr._cached_metadata_fetch(
            uri, full_cols, force_reload=False, base_columns=base_cols
        )

        assert "in.vintage" in result.columns
        assert "weight" in result.columns
        assert result.sort(bldg_col)["in.vintage"].to_list() == ["1980s", "1990s"]
        # CRITICAL: only the delta projection was issued — not the full set
        assert len(calls) == 1, f"Expected exactly one delta fetch, got: {calls}"
        delta_cols = set(calls[0])
        assert bldg_col in delta_cols
        assert "in.vintage" in delta_cols
        # Base cols should NOT be re-fetched
        assert "weight" not in delta_cols
        assert "in.state" not in delta_cols

    def test_writes_new_col_hash_file(self, tmp_path: Path) -> None:
        """After a successful delta-merge, the result is written under the
        full-columns col_hash so subsequent identical requests are cache hits."""
        mgr = _make_mgr(tmp_path)
        bldg_col = mgr.config.building_id_column
        base_cols = [bldg_col, "weight"]
        extras = ["in.vintage"]
        full_cols = base_cols + extras

        source_path = tmp_path / "source.parquet"
        _write_parquet(
            source_path,
            pl.DataFrame(
                {
                    bldg_col: [1, 2],
                    "weight": [1.0, 2.0],
                    "in.vintage": ["x", "y"],
                }
            ),
        )
        uri = f"file:///{source_path.as_posix().lstrip('/')}"

        mgr._cached_metadata_fetch(uri, base_cols, force_reload=False)
        cache_files_before = sorted(
            (mgr.cache_metadata_dir / f"{mgr.dataset_type}_{mgr.release}").glob(
                "*.parquet"
            )
        )

        mgr._cached_metadata_fetch(
            uri, full_cols, force_reload=False, base_columns=base_cols
        )
        cache_files_after = sorted(
            (mgr.cache_metadata_dir / f"{mgr.dataset_type}_{mgr.release}").glob(
                "*.parquet"
            )
        )

        # A NEW cache file (different col_hash) should have been written
        new_files = set(cache_files_after) - set(cache_files_before)
        assert len(new_files) == 1, (
            f"Expected one new col-hash cache file; got {new_files}"
        )

    def test_full_cache_hit_skips_delta(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the full-cols cache already exists, no S3 projection is issued."""
        mgr = _make_mgr(tmp_path)
        bldg_col = mgr.config.building_id_column
        base_cols = [bldg_col, "weight"]
        extras = ["in.vintage"]
        full_cols = base_cols + extras

        source_path = tmp_path / "source.parquet"
        _write_parquet(
            source_path,
            pl.DataFrame({bldg_col: [1], "weight": [1.0], "in.vintage": ["x"]}),
        )
        uri = f"file:///{source_path.as_posix().lstrip('/')}"

        # Prime the full cache
        mgr._cached_metadata_fetch(
            uri, full_cols, force_reload=False, base_columns=base_cols
        )

        # Now spy and verify no fetch happens on the second call
        scan_spy = MagicMock(wraps=mgr._scan_select_available)
        monkeypatch.setattr(mgr, "_scan_select_available", scan_spy)
        mgr._cached_metadata_fetch(
            uri, full_cols, force_reload=False, base_columns=base_cols
        )
        scan_spy.assert_not_called()


# ===========================================================================
# load_metadata pass-through of additional_columns
# ===========================================================================


class TestLoadMetadataAdditionalColumns:
    def test_load_metadata_threads_additional_columns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_metadata(additional_columns=[...]) eventually calls
        _cached_metadata_fetch with the extras merged into the column set."""
        mgr = _make_mgr(tmp_path)
        captured: List[dict] = []

        def fake_fetch(uri, cols, force_reload=False, base_columns=None):
            captured.append(
                {"uri": uri, "cols": list(cols), "base_columns": base_columns}
            )
            return pl.DataFrame(
                {
                    mgr.config.building_id_column: [1],
                    "weight": [1.0],
                    "in.state": ["CO"],
                    "in.vintage": ["1980s"],
                }
            )

        monkeypatch.setattr(mgr, "_cached_metadata_fetch", fake_fetch)
        # Use a column NOT present in the default ResStock filter_columns set
        # so we can assert it surfaces only via additional_columns (not base).
        extra_name = "in.test_extra_characteristic_xyz"
        mgr.load_metadata(additional_columns=[extra_name])

        assert captured, "Expected _cached_metadata_fetch to be called"
        cols = captured[0]["cols"]
        assert extra_name in cols
        assert captured[0]["base_columns"] is not None
        assert extra_name not in captured[0]["base_columns"]
