"""Regression tests for the release version check in CI."""

import os
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest


@pytest.mark.skipif(shutil.which("bash") is None, reason="Release check uses Bash")
@pytest.mark.parametrize("version", ["0.9.0", "v0.9.0"])
@pytest.mark.parametrize("tag", ["v0.9.0", "v0.9.1"])
def test_release_version_check(tmp_path: Path, version: str, tag: str) -> None:
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    step = workflow.split("- name: Check tag matches pyproject version\n", 1)[1]
    script = textwrap.dedent(step.split("run: |\n", 1)[1].split("\n\n", 1)[0])
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nversion = "{version}"\n', encoding="utf-8"
    )

    result = subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "GITHUB_REF_NAME": tag},
        capture_output=True,
        text=True,
    )

    if tag == "v0.9.0":
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode == 1
        assert f"Tag {tag} does not match pyproject version {version}" in result.stdout
