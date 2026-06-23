"""
Tests for sidebar helper functions in sidebar_helpers.py.
"""

from __future__ import annotations

from buildstock_rate_impact_calculator.ui.sidebar_helpers import (
    list_projects,
    default_project_name,
)


# ── list_projects ─────────────────────────────────────────────────────────────


def test_list_projects_empty_dir(tmp_path):
    """No projects/ subdir → empty list."""
    # tmp_path has no projects/ subdirectory
    assert list_projects(tmp_path) == []


def test_list_projects_sorted(tmp_path):
    """Three project subdirs created → sorted list."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "Charlie").mkdir()
    (projects_dir / "Alpha").mkdir()
    (projects_dir / "Bravo").mkdir()
    assert list_projects(tmp_path) == ["Alpha", "Bravo", "Charlie"]


def test_list_projects_ignores_files(tmp_path):
    """Files (not dirs) in projects/ are excluded."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "Real Project").mkdir()
    (projects_dir / "some_file.json").write_text("{}")
    (projects_dir / "README.md").write_text("notes")
    assert list_projects(tmp_path) == ["Real Project"]


# ── default_project_name ──────────────────────────────────────────────────────


def test_default_project_name_empty_dir(tmp_path):
    """No projects/ dir → 'Project 1'."""
    assert default_project_name(tmp_path) == "Project 1"


def test_default_project_name_sequential(tmp_path):
    """['Project 1', 'Project 2'] exist → 'Project 3'."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "Project 1").mkdir()
    (projects_dir / "Project 2").mkdir()
    assert default_project_name(tmp_path) == "Project 3"


def test_default_project_name_gap(tmp_path):
    """['Project 1', 'Project 3'] exist → 'Project 2'."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "Project 1").mkdir()
    (projects_dir / "Project 3").mkdir()
    assert default_project_name(tmp_path) == "Project 2"


def test_default_project_name_collision_with_custom(tmp_path):
    """['Project 1', 'my project'] exist → 'Project 2'.

    Custom names don't consume 'Project N' slots; fills the first available N >= 1.
    """
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "Project 1").mkdir()
    (projects_dir / "my project").mkdir()
    assert default_project_name(tmp_path) == "Project 2"
