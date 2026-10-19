"""
Shared pytest configuration, fixtures, and markers.

Integration tests (marked ``@pytest.mark.integration``) are skipped by default.
Run them with::

    pixi run pytest tests/ -v --integration

They require a working internet connection and will hit the OEDI S3 bucket.
"""

from __future__ import annotations

import os

import pytest

# AppTest smoke tests execute app.py, which would otherwise start the
# auto-exit watchdog (and could SIGINT the test process) — opt out.
os.environ.setdefault("BRIC_NO_AUTO_EXIT", "1")


# ---------------------------------------------------------------------------
# CLI option
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="Run integration tests that require live network access to OEDI S3.",
    )


# ---------------------------------------------------------------------------
# Auto-skip integration tests unless --integration is passed
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if not config.getoption("--integration"):
        skip = pytest.mark.skip(reason="Pass --integration to run network tests")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)


# ---------------------------------------------------------------------------
# Marker documentation
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: marks tests that require live network access (deselected by default)",
    )
