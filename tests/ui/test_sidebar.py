"""
Tests for sidebar helper functions in sidebar_helpers.py.
"""

from __future__ import annotations
import subprocess
from pathlib import Path

import pytest

from buildstock_rate_impact_calculator.ui import sidebar_helpers

from buildstock_rate_impact_calculator.ui.sidebar_helpers import (
    list_projects,
    default_project_name,
    browse_folder,
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


# ── browse_folder ─────────────────────────────────────────────────────────────


@pytest.fixture
def fake_run(monkeypatch):
    """Replace subprocess.run; records the command and returns a canned result."""
    calls = {}

    def install(platform, stdout="", returncode=0, exc=None):
        monkeypatch.setattr(sidebar_helpers.sys, "platform", platform)

        def run(cmd, **kwargs):
            calls["cmd"] = cmd
            if exc:
                raise exc
            return subprocess.CompletedProcess(cmd, returncode, stdout, "")

        monkeypatch.setattr(sidebar_helpers.subprocess, "run", run)
        return calls

    return install


def test_browse_folder_macos_uses_osascript(fake_run):
    """macOS never creates a Tk window; it shells out to the native dialog."""
    calls = fake_run("darwin", stdout="/Users/me/Documents/data/\n")
    assert browse_folder() == str(Path("/Users/me/Documents/data"))
    assert calls["cmd"][0] == "osascript"


def test_browse_folder_macos_cancel(fake_run):
    """osascript exits 1 (error -128) on cancel → None."""
    fake_run("darwin", returncode=1)
    assert browse_folder() is None


def test_browse_folder_other_platforms_use_tk_subprocess(fake_run):
    """Non-mac platforms run tkinter in a child interpreter, not in-process."""
    calls = fake_run("win32", stdout="C:/data/results")
    assert browse_folder() == str(Path("C:/data/results"))
    assert calls["cmd"][0] == sidebar_helpers.sys.executable
    assert calls["cmd"][1] == "-c"


def test_browse_folder_cancel_empty_output(fake_run):
    """tkinter prints an empty string on cancel → None."""
    fake_run("linux", stdout="")
    assert browse_folder() is None


def test_browse_folder_missing_executable(fake_run):
    """A missing picker executable → None rather than an exception."""
    fake_run("darwin", exc=FileNotFoundError("osascript"))
    assert browse_folder() is None
