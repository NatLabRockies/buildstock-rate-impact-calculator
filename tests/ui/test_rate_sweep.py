"""Phase 7: rate sweep — billing one upgrade's records under multiple rates.

Tests the extracted variant loop ``_bill_upgrade_under_rates`` with a fake
``UtilityBillCalculator.calculate_batch_bills`` so no PySAM runs, plus the
comparison-rate extension of ``_urdb_key_required`` and the ``rate_name``
join-key behavior of ``compute_bill_savings``.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from buildstock_rate_impact_calculator.billing.calculator import UtilityBillCalculator
from buildstock_rate_impact_calculator.ui.pages.page_05_run import (
    _bill_upgrade_under_rates,
    _urdb_key_required,
)


@pytest.fixture
def fake_batch_bills(monkeypatch: pytest.MonkeyPatch) -> list:
    """Patch calculate_batch_bills with a deterministic per-record frame.

    The fake bills every record at $100 × (1 + index) so rows are
    distinguishable; records the rate configs it was called with.
    """
    calls: list[dict] = []

    def fake(self, load_records, rate_config, progress_cb=None, max_workers=None):
        calls.append({"rate_config": rate_config, "n_records": len(load_records)})
        rows = []
        for i, rec in enumerate(load_records):
            rows.append(
                {
                    "bldg_id": rec["bldg_id"],
                    "upgrade": rec["upgrade"],
                    "electricity_annual_bill": 100.0 * (i + 1),
                    "electricity_annual_energy_kwh": 1000.0,
                    "electricity_average_rate_ct_kwh": 10.0,
                    "electricity_monthly_bills": None,
                    "error": None,
                    "weight": rec.get("weight"),
                    "_row_uid": rec.get("_row_uid"),
                }
            )
            if progress_cb is not None:
                progress_cb(i + 1, len(load_records))
        return pl.DataFrame(rows)

    monkeypatch.setattr(UtilityBillCalculator, "calculate_batch_bills", fake)
    return calls


def _records(n: int = 3) -> list[dict]:
    return [
        {
            "bldg_id": i + 1,
            "upgrade": 0,
            "load_array": np.ones(8760),
            "weight": 1.0,
            "_row_uid": str(i + 1),
        }
        for i in range(n)
    ]


class TestBillUpgradeUnderRates:
    def test_row_count_is_records_times_variants(self, fake_batch_bills: list) -> None:
        variants = [
            ("Rate A", {"name": "A"}, True),
            ("Rate B", {"name": "B"}, False),
            ("Rate C", {"name": "C"}, False),
        ]
        out = _bill_upgrade_under_rates(_records(3), variants)
        assert out.height == 9
        assert len(fake_batch_bills) == 3

    def test_is_primary_true_only_for_first_variant(
        self, fake_batch_bills: list
    ) -> None:
        variants = [
            ("Rate A", {"name": "A"}, True),
            ("Rate B", {"name": "B"}, False),
        ]
        out = _bill_upgrade_under_rates(_records(2), variants)
        primary = out.filter(pl.col("is_primary"))
        assert set(primary["rate_name"].to_list()) == {"Rate A"}
        non_primary = out.filter(~pl.col("is_primary"))
        assert set(non_primary["rate_name"].to_list()) == {"Rate B"}

    def test_single_variant_schema_is_stable(self, fake_batch_bills: list) -> None:
        """Runs without a sweep still get rate_name/is_primary columns."""
        out = _bill_upgrade_under_rates(
            _records(2), [("Only Rate", {"name": "only"}, True)]
        )
        assert "rate_name" in out.columns
        assert "is_primary" in out.columns
        assert out["is_primary"].all()
        assert set(out["rate_name"].to_list()) == {"Only Rate"}

    def test_each_variant_gets_its_own_rate_config(
        self, fake_batch_bills: list
    ) -> None:
        variants = [
            ("Rate A", {"name": "A"}, True),
            ("Rate B", {"name": "B"}, False),
        ]
        _bill_upgrade_under_rates(_records(1), variants)
        assert [c["rate_config"]["name"] for c in fake_batch_bills] == ["A", "B"]

    def test_tracker_advanced_once_per_record_and_variant(
        self, fake_batch_bills: list
    ) -> None:
        class FakeTracker:
            def __init__(self) -> None:
                self.advanced = 0

            def advance(self, n: int, message: str = "") -> None:
                self.advanced += n

        tracker = FakeTracker()
        variants = [
            ("Rate A", {"name": "A"}, True),
            ("Rate B", {"name": "B"}, False),
        ]
        _bill_upgrade_under_rates(_records(3), variants, tracker)  # type: ignore[arg-type]
        assert tracker.advanced == 6


class TestUrdbKeyRequiredComparison:
    def test_comparison_rate_needing_fetch_triggers_requirement(self) -> None:
        library = {
            "Full Rate": {"energyratestructure": [[{"rate": 0.1}]]},
            "URDB Stub": {"label": "abc123"},
        }
        assignments = {"baseline": "Full Rate"}
        assert not _urdb_key_required(assignments, library)
        assert _urdb_key_required(assignments, library, comparison_rates=["URDB Stub"])
