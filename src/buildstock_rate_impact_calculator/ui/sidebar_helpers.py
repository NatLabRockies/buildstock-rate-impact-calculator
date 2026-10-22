"""Pure helper functions for the app sidebar — no Streamlit imports."""

from __future__ import annotations
import subprocess
import sys
from pathlib import Path


def list_projects(output_dir: Path) -> list[str]:
    """Return sorted list of project subdirectory names under output_dir/projects/.

    Returns [] if the directory doesn't exist or has no subdirs.
    """
    projects_dir = output_dir / "projects"
    if not projects_dir.is_dir():
        return []
    return sorted(p.name for p in projects_dir.iterdir() if p.is_dir())


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


# Run in a child interpreter so Tk owns that process's main thread.
_TK_PICKER_SCRIPT = """
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.wm_attributes("-topmost", True)
print(filedialog.askdirectory(), end="")
root.destroy()
"""

_MAC_PICKER_SCRIPT = 'POSIX path of (choose folder with prompt "Select Data Directory")'


def browse_folder() -> str | None:
    """Open a native folder picker dialog. Returns selected path or None.

    The dialog is never created in the Streamlit process: Streamlit runs
    scripts on a worker thread, and on macOS creating a GUI window off the
    main thread aborts the whole process. macOS uses the native AppleScript
    ``choose folder`` dialog; other platforms run tkinter in a subprocess.

    Returns None if the dialog is unavailable or the user cancels.
    """
    if sys.platform == "darwin":
        cmd = ["osascript", "-e", _MAC_PICKER_SCRIPT]
    else:
        cmd = [sys.executable, "-c", _TK_PICKER_SCRIPT]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return None
    # osascript exits non-zero when the user cancels (error -128).
    folder = result.stdout.strip() if result.returncode == 0 else ""
    # Normalise (osascript adds a trailing slash; tkinter uses "/" on Windows).
    return str(Path(folder)) if folder else None
