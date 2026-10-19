"""
Tests for the pure schedule-construction helpers used by the TOU rate builder UI.
"""

from __future__ import annotations

from buildstock_rate_impact_calculator.ui.components.rate_editor import (
    add_urdb_rate,
    build_flat_demand,
    build_tou_schedules,
)


# ── build_tou_schedules ───────────────────────────────────────────────────────


def test_empty_periods_give_all_base():
    weekday, weekend = build_tou_schedules([])
    assert weekday == [[0] * 24 for _ in range(12)]
    assert weekend == [[0] * 24 for _ in range(12)]


def test_weekdays_only_period():
    periods = [{"months": [6, 7], "hours": [16, 17], "days": "Weekdays only"}]
    weekday, weekend = build_tou_schedules(periods)
    assert weekday[5][16] == 1
    assert weekday[6][17] == 1
    assert weekend[5][16] == 0
    assert weekday[5][15] == 0
    assert weekday[4][16] == 0


def test_weekends_only_period():
    periods = [{"months": [1], "hours": [0], "days": "Weekends only"}]
    weekday, weekend = build_tou_schedules(periods)
    assert weekday[0][0] == 0
    assert weekend[0][0] == 1


def test_all_days_default():
    periods = [{"months": [12], "hours": [23]}]
    weekday, weekend = build_tou_schedules(periods)
    assert weekday[11][23] == 1
    assert weekend[11][23] == 1


def test_later_periods_overwrite_earlier():
    periods = [
        {"months": [6], "hours": [12, 13, 14], "days": "All days"},
        {"months": [6], "hours": [14, 15], "days": "All days"},
    ]
    weekday, _ = build_tou_schedules(periods)
    assert weekday[5][12] == 1
    assert weekday[5][13] == 1
    assert weekday[5][14] == 2
    assert weekday[5][15] == 2


# ── build_flat_demand ─────────────────────────────────────────────────────────


def test_flat_demand_full_coverage_has_no_zero_period():
    rows = [
        {"rate": 15.0, "months": [6, 7, 8, 9]},
        {"rate": 8.0, "months": [1, 2, 3, 4, 5, 10, 11, 12]},
    ]
    structure, months = build_flat_demand(rows)
    assert len(structure) == 2
    assert structure[0][0]["rate"] == 15.0
    assert structure[1][0]["rate"] == 8.0
    assert months == [1, 1, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1]


def test_flat_demand_partial_coverage_appends_zero_period():
    rows = [{"rate": 12.0, "months": [6, 7, 8]}]
    structure, months = build_flat_demand(rows)
    assert len(structure) == 2
    assert structure[1][0]["rate"] == 0.0
    assert months == [1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1]


def test_flat_demand_later_rows_overwrite():
    rows = [
        {"rate": 5.0, "months": [6, 7]},
        {"rate": 9.0, "months": [7, 8]},
    ]
    structure, months = build_flat_demand(rows)
    assert months[5] == 0  # June from first row
    assert months[6] == 1  # July overwritten by second row
    assert months[7] == 1  # August from second row


# ── add_urdb_rate ─────────────────────────────────────────────────────────────


def test_add_urdb_rate_keys_by_name():
    library: dict[str, dict] = {}
    key = add_urdb_rate(library, {"name": "Residential", "label": "aaa"})
    assert key == "Residential"
    assert library["Residential"]["label"] == "aaa"


def test_add_urdb_rate_same_name_different_label_is_suffixed():
    """Two URDB versions sharing a name must not overwrite each other."""
    library: dict[str, dict] = {}
    add_urdb_rate(library, {"name": "Residential", "label": "aaa"})
    key = add_urdb_rate(library, {"name": "Residential", "label": "bbb"})
    assert key == "Residential (bbb)"
    assert set(library) == {"Residential", "Residential (bbb)"}
    # Library key and the rate's own name stay in sync (projects reload by name).
    assert library[key]["name"] == key


def test_add_urdb_rate_same_label_replaces():
    library: dict[str, dict] = {}
    add_urdb_rate(library, {"name": "Residential", "label": "aaa", "v": 1})
    add_urdb_rate(library, {"name": "Residential", "label": "aaa", "v": 2})
    assert list(library) == ["Residential"]
    assert library["Residential"]["v"] == 2
