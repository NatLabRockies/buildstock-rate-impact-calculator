"""Build the stand-alone BRIC zips (one per platform) into ``dist/``.

Run with ``pixi run pack`` (all platforms) or
``pixi run pack --platform win-64`` (one). Every platform can be packed from
any OS: pixi-pack downloads the packages listed in ``pixi.lock`` rather than
copying an installed environment.

Each zip holds a top-level ``BRIC-<version>-<platform>/`` folder with:

- ``environment.tar``: the locked environment plus this project's wheel
  (pixi-pack)
- ``pixi-unpack[.exe]``: the unpacker for that platform
- ``Start BRIC.cmd`` / ``Start BRIC.command`` / ``start-bric.sh``: what the
  user double-clicks (from ``templates/``)
- ``README.txt``

See ``standalone/README.md`` for the release process.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Path(__file__).resolve().parent / "templates"
LICENSE = REPO_ROOT / "LICENSE.md"

# Keep the packer and unpacker on the same release.
PIXI_PACK_VERSION = "0.7.11"
PIXI_UNPACK_URL = (
    "https://github.com/Quantco/pixi-pack/releases/download/"
    f"v{PIXI_PACK_VERSION}/pixi-unpack-{{target}}"
)


@dataclass(frozen=True)
class Target:
    unpack_target: str  # pixi-unpack release asset suffix
    start_file: str  # name of the double-click script in the zip
    template: str  # file in templates/
    install_dir: str  # shown in README.txt; must match the start script
    log_dir: str  # shown in README.txt; must match launcher.default_log_dir()
    security_note: str

    @property
    def is_windows(self) -> bool:
        return self.start_file.endswith(".cmd")

    @property
    def unpack_name(self) -> str:
        return "pixi-unpack.exe" if self.is_windows else "pixi-unpack"


_MAC_NOTE = (
    'If macOS says the file "cannot be opened because it is from an\n'
    'unidentified developer", right-click (or Control-click) it, choose Open,\n'
    "then Open again. You only need to do this once."
)
_MAC_DIR = "~/Library/Application Support/BRIC"
_MAC_LOG_DIR = "~/Library/Logs/BRIC"

TARGETS: dict[str, Target] = {
    "win-64": Target(
        unpack_target="x86_64-pc-windows-msvc.exe",
        start_file="Start BRIC.cmd",
        template="start-bric.cmd",
        install_dir=r"%LOCALAPPDATA%\BRIC",
        log_dir=r"%LOCALAPPDATA%\BRIC\logs",
        security_note=(
            'If Windows shows "Windows protected your PC", click "More info" and\n'
            'then "Run anyway". If it shows an "Open File - Security Warning",\n'
            'click "Run". To avoid this, right-click the downloaded zip, choose\n'
            'Properties, tick "Unblock" and click OK before extracting it.'
        ),
    ),
    "osx-arm64": Target(
        unpack_target="aarch64-apple-darwin",
        start_file="Start BRIC.command",
        template="start-bric.sh",
        install_dir=_MAC_DIR,
        log_dir=_MAC_LOG_DIR,
        security_note=_MAC_NOTE,
    ),
    "osx-64": Target(
        unpack_target="x86_64-apple-darwin",
        start_file="Start BRIC.command",
        template="start-bric.sh",
        install_dir=_MAC_DIR,
        log_dir=_MAC_LOG_DIR,
        security_note=_MAC_NOTE,
    ),
    "linux-64": Target(
        # Statically linked, so it runs regardless of the system's glibc.
        unpack_target="x86_64-unknown-linux-musl",
        start_file="start-bric.sh",
        template="start-bric.sh",
        install_dir="~/.local/share/BRIC",
        log_dir="~/.local/state/BRIC",
        security_note=(
            "Start it from a terminal with ./start-bric.sh if your file manager\n"
            "opens it in a text editor instead of running it."
        ),
    ),
}

# Files in the zip that must keep their executable bit.
_EXEC_MODE = stat.S_IFREG | 0o755
_FILE_MODE = stat.S_IFREG | 0o644


def project_version(pyproject: Path = REPO_ROOT / "pyproject.toml") -> str:
    with pyproject.open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def render_template(text: str, **values: str) -> str:
    """Replace ``@KEY@`` placeholders; fail loudly on any left over."""
    for key, value in values.items():
        text = text.replace(f"@{key.upper()}@", value)
    leftover = re.findall(r"@[A-Z][A-Z_]*@", text)
    if leftover:
        raise ValueError(f"Unreplaced placeholder(s) in template: {leftover}")
    return text


def write_text(path: Path, text: str, windows: bool) -> None:
    # Batch files misbehave (labels, goto) with LF line endings.
    newline = "\r\n" if windows else "\n"
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", newline).encode("utf-8"))


def write_license(stage: Path, windows: bool) -> None:
    # The license's clause 2 requires binary redistributions to carry the
    # notice. As .txt it opens in Notepad/TextEdit with a double-click.
    write_text(stage / "LICENSE.txt", LICENSE.read_text(encoding="utf-8"), windows)


def zip_folder(folder: Path, zip_path: Path, executables: set[str]) -> None:
    """Zip *folder* (as the archive's top-level directory).

    Unix permissions go into each entry so macOS/Linux unzip tools restore the
    executable bit on *executables* (names relative to *folder*). The already
    compressed environment.tar is stored rather than deflated.
    """
    with zipfile.ZipFile(zip_path, "w") as zf:
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(folder).as_posix()
            info = zipfile.ZipInfo.from_file(path, f"{folder.name}/{rel}")
            info.external_attr = (
                _EXEC_MODE if rel in executables else _FILE_MODE
            ) << 16
            info.compress_type = (
                zipfile.ZIP_STORED if rel.endswith(".tar") else zipfile.ZIP_DEFLATED
            )
            with path.open("rb") as src, zf.open(info, "w") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)


def build_id(environment_tar: Path) -> str:
    """Short content hash of the packed environment.

    The start scripts store it in the install marker and re-unpack when it
    changes, so a rebuilt zip of the same version (e.g. a dependency fix)
    replaces the old environment.
    """
    digest = hashlib.sha256()
    with environment_tar.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def build_wheel(out_dir: Path) -> Path:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(REPO_ROOT),
            "--no-deps",
            "--no-cache-dir",
            "-q",
            "-w",
            str(out_dir),
        ],
        check=True,
    )
    (wheel,) = out_dir.glob("*.whl")
    return wheel


def fetch_pixi_unpack(target: Target, cache_dir: Path) -> Path:
    cached = cache_dir / f"pixi-unpack-{PIXI_PACK_VERSION}-{target.unpack_target}"
    if not cached.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        url = PIXI_UNPACK_URL.format(target=target.unpack_target)
        print(f"Downloading {url}", flush=True)
        tmp = cached.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(cached)
    return cached


def pack_environment(
    platform: str, wheel: Path, out_file: Path, cache_dir: Path
) -> None:
    # --ignore-pypi-non-wheel skips this project's own editable install from
    # pixi.lock (not a wheel); the built wheel is injected in its place.
    subprocess.run(
        [
            "pixi",
            "exec",
            "--spec",
            f"pixi-pack=={PIXI_PACK_VERSION}",
            "pixi-pack",
            str(REPO_ROOT / "pyproject.toml"),
            "--platform",
            platform,
            "--ignore-pypi-non-wheel",
            "--inject",
            str(wheel),
            "--use-cache",
            str(cache_dir / "packages"),
            "--output-file",
            str(out_file),
        ],
        check=True,
    )


def build_platform(
    platform: str, version: str, wheel: Path, dist: Path, cache_dir: Path
) -> Path:
    target = TARGETS[platform]
    name = f"BRIC-{version}-{platform}"
    stage = dist / name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    pack_environment(platform, wheel, stage / "environment.tar", cache_dir)
    shutil.copyfile(fetch_pixi_unpack(target, cache_dir), stage / target.unpack_name)

    start = render_template(
        (TEMPLATES / target.template).read_text(encoding="utf-8"),
        bric_version=version,
        bric_build=build_id(stage / "environment.tar"),
    )
    write_text(stage / target.start_file, start, target.is_windows)
    readme = render_template(
        (TEMPLATES / "README.txt").read_text(encoding="utf-8"),
        bric_version=version,
        bric_platform=platform,
        start_file=target.start_file,
        install_dir=target.install_dir,
        log_dir=target.log_dir,
        security_note=target.security_note,
    )
    write_text(stage / "README.txt", readme, target.is_windows)
    write_license(stage, target.is_windows)

    zip_path = dist / f"{name}.zip"
    zip_folder(stage, zip_path, executables={target.start_file, target.unpack_name})
    shutil.rmtree(stage)
    print(f"Built {zip_path} ({zip_path.stat().st_size / 2**20:,.0f} MiB)", flush=True)
    return zip_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--platform",
        action="append",
        choices=sorted(TARGETS),
        help="Platform to build (repeatable; default: all).",
    )
    parser.add_argument(
        "--dist", type=Path, default=REPO_ROOT / "dist", help="Output directory."
    )
    args = parser.parse_args(argv)

    version = project_version()
    dist = args.dist.resolve()
    dist.mkdir(parents=True, exist_ok=True)
    cache_dir = dist / ".cache"
    wheel = build_wheel(dist / "wheel")
    print(f"Built {wheel.name}", flush=True)
    for platform in args.platform or sorted(TARGETS):
        build_platform(platform, version, wheel, dist, cache_dir)


if __name__ == "__main__":
    main()
