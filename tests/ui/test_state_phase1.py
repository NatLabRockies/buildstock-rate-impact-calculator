"""
Phase 1 tests for AppState directory structure and config key changes.
"""
from __future__ import annotations
from pathlib import Path
import pytest

from buildstock_rate_impact_calculator.ui.state import AppState


def test_project_dir_property():
    """project_dir returns output_dir / 'projects' / project_name."""
    state = AppState()
    state.output_dir = Path("/some/base")
    state.project_name = "My Project"
    assert state.project_dir == Path("/some/base/projects/My Project")


def test_project_dir_property_default():
    """project_dir works with the default empty project_name."""
    state = AppState()
    state.output_dir = Path("/base")
    state.project_name = ""
    assert state.project_dir == Path("/base/projects/")


def test_to_config_dict_excludes_rate_library():
    """rate_library and fuel_rate_library must NOT appear in to_config_dict output."""
    state = AppState()
    state.rate_library = {"My Rate": {"name": "My Rate"}}
    state.fuel_rate_library = {"Gas Rate": {"name": "Gas Rate", "fuel_type": "natural_gas"}}
    config = state.to_config_dict()
    assert "rate_library" not in config
    assert "fuel_rate_library" not in config


def test_to_config_dict_includes_project_name():
    """project_name IS included in to_config_dict output."""
    state = AppState()
    state.project_name = "Test Project"
    config = state.to_config_dict()
    assert "project_name" in config
    assert config["project_name"] == "Test Project"


def test_project_name_round_trip():
    """project_name is saved and restored via to_config_dict / apply_config_dict."""
    state = AppState()
    state.project_name = "Round Trip Project"
    config = state.to_config_dict()

    restored = AppState()
    restored.apply_config_dict(config)
    assert restored.project_name == "Round Trip Project"


def test_project_name_default_empty():
    """project_name defaults to empty string."""
    state = AppState()
    assert state.project_name == ""


def test_rate_library_not_serialized_but_still_functional():
    """rate_library field still exists on the dataclass even though not in _CONFIG_KEYS."""
    state = AppState()
    state.rate_library["TestRate"] = {"name": "TestRate", "label": "abc123"}
    assert "TestRate" in state.rate_library
    # Not in config dict
    assert "rate_library" not in state.to_config_dict()


# ───────────────────────────────────────────────────────────────────────────
# resstock_weight (Phase 2)
# ───────────────────────────────────────────────────────────────────────────


def test_resstock_weight_field_default_is_none():
    """resstock_weight defaults to None on a fresh AppState."""
    state = AppState()
    assert state.resstock_weight is None


def test_resstock_weight_field_not_persisted_in_config_round_trip():
    """resstock_weight is runtime-only — not saved to config, not restored from it."""
    state = AppState()
    state.resstock_weight = 188.0
    config = state.to_config_dict()
    assert "resstock_weight" not in config

    restored = AppState()
    restored.apply_config_dict(config)
    assert restored.resstock_weight is None

