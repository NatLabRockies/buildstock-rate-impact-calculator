"""Pure helper functions for the app sidebar — no Streamlit imports."""
from __future__ import annotations
from pathlib import Path


def list_projects(output_dir: Path) -> list[str]:
    """Return sorted list of project subdirectory names under output_dir/projects/.

    Returns [] if the directory doesn't exist or has no subdirs.
    """
    projects_dir = output_dir / "projects"
    if not projects_dir.is_dir():
        return []
    return sorted(
        p.name for p in projects_dir.iterdir() if p.is_dir()
    )


def default_project_name(output_dir: Path) -> str:
    """Return the lowest 'Project N' name (N >= 1) not in existing project names.

    Existing custom names (non-'Project N' format) do not consume slots.
    Fills gaps: if ["Project 1", "Project 3"] exist, returns "Project 2".
    """
    existing = set(list_projects(output_dir))
    n = 1
    while f"Project {n}" in existing:
        n += 1
    return f"Project {n}"


def browse_folder() -> str | None:
    """Open a folder picker dialog via tkinter. Returns selected path or None.

    Returns None on ImportError or if the user cancels.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", True)
        folder = filedialog.askdirectory()
        root.destroy()
        return folder if folder else None
    except Exception:
        return None
