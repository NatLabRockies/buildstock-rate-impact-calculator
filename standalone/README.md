# Stand-alone downloads

Builds the zips that let users run BRIC without installing pixi or Python:
unzip, double-click `Start BRIC`, and the app opens in the browser. User-facing
instructions are in
[docs/bric-installation-instructions.md](../docs/bric-installation-instructions.md#option-1--download-the-app-recommended).

## How it works

Each zip contains the exact environment from `pixi.lock`, packed by
[pixi-pack](https://github.com/Quantco/pixi-pack), plus this project as a
wheel:

```
BRIC-<version>-<platform>/
├── Start BRIC.cmd | Start BRIC.command | start-bric.sh   ← from templates/
├── environment.tar     packed conda + PyPI packages, incl. this project's wheel
├── pixi-unpack[.exe]   unpacker for the platform (Quantco/pixi-pack release)
└── README.txt          from templates/README.txt
```

When the user double-clicks the start script:

1. **First run of a build only:** `pixi-unpack` installs the environment into
   a per-user folder, `%LOCALAPPDATA%\BRIC\<version>` (Windows),
   `~/Library/Application Support/BRIC/<version>` (macOS) or
   `~/.local/share/BRIC/<version>` (Linux), then writes a `.bric-unpacked`
   marker holding the build ID (a hash of `environment.tar`, baked into the
   start script by `build_dist.py`). A missing marker (interrupted setup) or a
   different build ID (a rebuilt zip of the same version) is unpacked again.
   Other versions' folders are then deleted. About 1–2 minutes and 2.3 GB
   (Windows). The environment is not unpacked beside the script:
   Desktop/Documents are often OneDrive-synced, and an unpacked environment
   can't be moved.
2. The script activates the environment and runs `bric`, the console script
   for [`launcher.py`](../src/buildstock_rate_impact_calculator/launcher.py).
   It starts Streamlit headless on a free `127.0.0.1` port and opens the
   browser when the server's health check answers. Arguments to the start
   script are passed to `bric` (`--no-browser`, `--port N`).
3. The app stops on **⏻ Quit app** or about 2 minutes after the last browser
   tab closes (`ui/auto_exit.py`), and the start script exits.
4. **If it stops with any other exit code** (including negative Windows crash
   codes such as `0xC0000005`), the script shows the code and the end of the
   run's log, then waits for a key press.

### Logs

`bric` copies its console output to a new log file per run and enables
`faulthandler`, which writes every thread's Python stack there on a native
crash. The last 10 logs are kept in `%LOCALAPPDATA%\BRIC\logs` (Windows),
`~/Library/Logs/BRIC` (macOS) or `~/.local/state/BRIC` (Linux); set
`BRIC_LOG_DIR` to use another folder. Windows also records native crashes in
the Application event log (Event Viewer → Windows Logs → Application,
"Application Error" for `python.exe`), with the faulting module and offset.

User data (cache, projects, OpenEI key) stays in the app's Data Directory
(`~/Documents/buildstock_results` by default), so updates and uninstalls never
touch it.

## Building locally

```bash
pixi run pack                         # all platforms -> dist/BRIC-<version>-<platform>.zip
pixi run pack --platform win-64       # one platform (repeatable)
```

Any platform can be packed from any OS. Downloads are cached in `dist/.cache/`.
Each zip is about 400 MB.

To try a build without touching your real install folder, extract the zip and
run the start script with `LOCALAPPDATA` (Windows) or `HOME` (macOS/Linux)
pointed at a scratch folder, for example:

```bash
LOCALAPPDATA=C:/temp/bric-test cmd //c "dist\\BRIC-0.1.0-win-64\\Start BRIC.cmd" --no-browser --port 8599
```

## CI

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on every pull
request, every push to `main`, and by hand (**Actions → CI → Run workflow**):

1. **test**: the test suite on Linux and Windows.
2. **pack**: `pixi run --locked pack` builds all four zips.
3. **smoke**: each zip is unzipped on its own OS (Windows, macOS arm64, macOS
   Intel, Linux) and started through the real start script with
   `--no-browser --port 8599`; the job passes once the app's health check
   answers.

The zips are kept as the run's `bric-zips` artifact (3 days; 14 for releases),
so any green run's downloads can be tried before releasing.

## Releasing

1. Bump `version` in `pyproject.toml` and commit (with the updated `pixi.lock`,
   which records the package hash).
2. Tag and push: `git tag v<version> && git push origin v<version>`.
3. CI runs as above, also checking the tag matches the version, then the
   **release** job attaches the zips to a **draft** GitHub Release.
4. Review the draft release notes and publish. The README's download link
   points at the latest published release.

## Notes and limitations

- **polars is pinned to 1.41.2.** polars 1.42 and later (including 2.x)
  bundle mimalloc 3.3.2, which crashes Windows processes with an access
  violation in `_polars_runtime.pyd` when a thread that used polars exits
  ([pola-rs/polars#29883](https://github.com/pola-rs/polars/issues/29883)).
  BRIC starts and ends such threads on every Streamlit rerun and download, so
  the stand-alone app crashed after a few minutes of use. Lift the pin once a
  polars release bundles mimalloc 3.4.1 or later (check `libmimalloc-sys` in
  polars' `Cargo.lock`: it must be newer than 0.1.49).

- **Unsigned.** Windows SmartScreen and macOS Gatekeeper warn on first start;
  the install docs and `README.txt` explain how to proceed. Code signing
  (Authenticode, Apple Developer ID + notarization) would remove the warnings.
- **pixi-pack and this project.** `pixi.lock` lists this project as an
  editable path install, which pixi-pack can't pack, so `build_dist.py` passes
  `--ignore-pypi-non-wheel` and injects a freshly built wheel instead. That flag
  would also silently skip any *other* PyPI dependency that only has a source
  distribution; the CI smoke test catches a missing package only if it breaks
  startup.
- **pixi-pack version.** `PIXI_PACK_VERSION` in `build_dist.py` pins both
  pixi-pack and the bundled pixi-unpack; keep them on the same release.
- **Size.** About 500 MB of the unpacked Windows environment is Intel MKL
  (numpy's BLAS), and pyarrow brings gRPC/protobuf libraries. Pinning BLAS to
  OpenBLAS and moving dev tools (pytest, ruff) to a separate pixi feature would
  shrink it.
- **The app script lives in the package** (`src/buildstock_rate_impact_calculator/app.py`)
  so it ships in the wheel. Streamlit re-executes it on every interaction, so
  don't add a root-level `app.py` that merely imports it.
