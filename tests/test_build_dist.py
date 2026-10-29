"""Tests for the stand-alone build helpers (standalone/build_dist.py).

Packing itself (pixi-pack downloads) is exercised by the release workflow;
these cover the pieces that run locally and quickly.
"""

from __future__ import annotations

import importlib.util
import stat
import sys
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "standalone" / "build_dist.py"
_spec = importlib.util.spec_from_file_location("build_dist", _SCRIPT)
build_dist = importlib.util.module_from_spec(_spec)
# Registered before executing: @dataclass looks its module up in sys.modules.
sys.modules["build_dist"] = build_dist
_spec.loader.exec_module(build_dist)


def test_project_version_reads_pyproject() -> None:
    assert build_dist.project_version()


def test_render_template_replaces_placeholders() -> None:
    out = build_dist.render_template(
        "@echo off\nv=@BRIC_VERSION@", bric_version="1.2.3"
    )
    # Batch "@echo" is not a placeholder and is left alone.
    assert out == "@echo off\nv=1.2.3"


def test_render_template_rejects_leftover_placeholders() -> None:
    with pytest.raises(ValueError, match="START_FILE"):
        build_dist.render_template("@BRIC_VERSION@ @START_FILE@", bric_version="1")


@pytest.mark.parametrize("platform", sorted(build_dist.TARGETS))
def test_templates_render_for_every_platform(platform: str) -> None:
    target = build_dist.TARGETS[platform]
    templates = build_dist.TEMPLATES
    build_dist.render_template(
        (templates / target.template).read_text(encoding="utf-8"),
        bric_version="1",
        bric_build="abc123",
    )
    build_dist.render_template(
        (templates / "README.txt").read_text(encoding="utf-8"),
        bric_version="1",
        bric_platform=platform,
        start_file=target.start_file,
        install_dir=target.install_dir,
        log_dir=target.log_dir,
        security_note=target.security_note,
    )


def test_build_id_tracks_content(tmp_path: Path) -> None:
    tar = tmp_path / "environment.tar"
    tar.write_bytes(b"one")
    first = build_dist.build_id(tar)
    assert len(first) == 12
    assert build_dist.build_id(tar) == first
    tar.write_bytes(b"two")
    assert build_dist.build_id(tar) != first


def test_write_text_line_endings(tmp_path: Path) -> None:
    build_dist.write_text(tmp_path / "a.cmd", "a\nb\n", windows=True)
    build_dist.write_text(tmp_path / "a.sh", "a\r\nb\r\n", windows=False)
    assert (tmp_path / "a.cmd").read_bytes() == b"a\r\nb\r\n"
    assert (tmp_path / "a.sh").read_bytes() == b"a\nb\n"


@pytest.mark.parametrize("windows", [True, False])
def test_write_license_copies_notice(tmp_path: Path, windows: bool) -> None:
    build_dist.write_license(tmp_path, windows)
    data = (tmp_path / "LICENSE.txt").read_bytes()
    assert b"Alliance for Energy Innovation" in data
    assert (b"\r\n" in data) == windows


def test_zip_folder_keeps_exec_bits_and_stores_tar(tmp_path: Path) -> None:
    folder = tmp_path / "BRIC-1-osx-arm64"
    folder.mkdir()
    for name in ("Start BRIC.command", "pixi-unpack", "README.txt", "environment.tar"):
        (folder / name).write_text(name)
    zip_path = tmp_path / "out.zip"

    build_dist.zip_folder(folder, zip_path, {"Start BRIC.command", "pixi-unpack"})

    with zipfile.ZipFile(zip_path) as zf:
        infos = {i.filename: i for i in zf.infolist()}
    prefix = "BRIC-1-osx-arm64/"
    assert set(infos) == {
        prefix + n
        for n in ("Start BRIC.command", "pixi-unpack", "README.txt", "environment.tar")
    }

    def mode(name: str) -> int:
        return stat.S_IMODE(infos[prefix + name].external_attr >> 16)

    assert mode("Start BRIC.command") == 0o755
    assert mode("pixi-unpack") == 0o755
    assert mode("README.txt") == 0o644
    assert infos[prefix + "environment.tar"].compress_type == zipfile.ZIP_STORED


def test_wheel_contains_app_launcher_and_data(tmp_path: Path) -> None:
    wheel = build_dist.build_wheel(tmp_path / "wheel")
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        entry_points = next(
            zf.read(n).decode() for n in names if n.endswith("entry_points.txt")
        )
    pkg = "buildstock_rate_impact_calculator/"
    for rel in (
        "app.py",
        "launcher.py",
        "data/cb_2019_us_county_500k.geojson",
        "data/cb_2019_us_puma10_500k.geojson",
        "data/us_states.geojson",
        "data/eia_to_county.csv",
        "data/utility_companies.csv",
    ):
        assert pkg + rel in names
    assert "bric = buildstock_rate_impact_calculator.launcher:main" in entry_points
    assert any(n.endswith(".dist-info/licenses/LICENSE.md") for n in names)
