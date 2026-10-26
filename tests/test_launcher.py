"""Tests for the desktop launcher (the ``bric`` console script)."""

from __future__ import annotations

import io
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from buildstock_rate_impact_calculator import launcher


def _option(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def test_app_path_points_at_packaged_app() -> None:
    assert launcher.APP_PATH.name == "app.py"
    assert launcher.APP_PATH.is_file()


def test_streamlit_args_run_local_headless_app() -> None:
    args = launcher.streamlit_args(Path("x/app.py"), 8123)
    assert args[:2] == ["run", str(Path("x/app.py"))]
    assert _option(args, "--server.address") == "127.0.0.1"
    assert _option(args, "--server.port") == "8123"
    # Headless skips Streamlit's first-run email prompt, which would block a
    # double-click launch; the launcher opens the browser itself.
    assert _option(args, "--server.headless") == "true"
    assert _option(args, "--browser.gatherUsageStats") == "false"


def test_find_free_port_is_bindable() -> None:
    port = launcher.find_free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((launcher.HOST, port))


def test_wait_until_healthy_returns_once_ok() -> None:
    answers = iter([False, False, True])
    sleeps: list[float] = []
    ok = launcher.wait_until_healthy(
        "http://x", timeout=10, is_ok=lambda _url: next(answers), sleep=sleeps.append
    )
    assert ok
    assert len(sleeps) == 2


def test_wait_until_healthy_gives_up_after_timeout() -> None:
    calls: list[str] = []

    def never_ok(url: str) -> bool:
        calls.append(url)
        return False

    ok = launcher.wait_until_healthy(
        "http://x", timeout=2, is_ok=never_ok, sleep=lambda _s: None, poll_seconds=0.5
    )
    assert not ok
    assert len(calls) == 4


def test_parse_args_defaults() -> None:
    args = launcher.parse_args([])
    assert args.port is None
    assert args.no_browser is False


def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        launcher.parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == launcher.app_version()


def test_main_runs_streamlit_without_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict = {}

    def fake_main(args, prog_name):
        calls["args"] = args
        return 0

    import streamlit.web.cli as stcli

    monkeypatch.setattr(stcli, "main", fake_main)
    monkeypatch.setattr(launcher, "start_log_file", lambda _dir: Path("x.log"))
    opened: list = []
    monkeypatch.setattr(launcher, "_open_browser_when_ready", opened.append)

    with pytest.raises(SystemExit):
        launcher.main(["--no-browser", "--port", "8124"])

    assert _option(calls["args"], "--server.port") == "8124"
    assert calls["args"][1] == str(launcher.APP_PATH)
    assert opened == []


def test_log_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(launcher.LOG_DIR_ENV, str(tmp_path))
    assert launcher.default_log_dir() == tmp_path


def test_windows_log_dir_is_beside_installs(monkeypatch: pytest.MonkeyPatch) -> None:
    # The start script deletes every other folder in %LOCALAPPDATA%\BRIC
    # except "logs", so the log folder must be exactly that name.
    monkeypatch.delenv(launcher.LOG_DIR_ENV, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", "C:/la")
    assert launcher.default_log_dir() == Path("C:/la/BRIC/logs")


def test_prune_logs_keeps_newest(tmp_path: Path) -> None:
    names = [f"bric-2026010{i}-000000-1.log" for i in range(1, 6)]
    for name in names:
        (tmp_path / name).write_text("x")
    (tmp_path / "other.txt").write_text("x")
    launcher.prune_logs(tmp_path, keep=2)
    assert sorted(p.name for p in tmp_path.iterdir()) == names[-2:] + ["other.txt"]


def test_tee_writes_both_and_survives_closed_console() -> None:
    console, log = io.StringIO(), io.StringIO()
    tee = launcher._Tee(console, log)
    tee.write("a\n")
    console.close()
    tee.write("b\n")  # console gone: still logged, no error
    tee.flush()
    assert log.getvalue() == "a\nb\n"


# A genuine invalid read in a worker thread, like the polars allocator crash
# (pola-rs/polars#29883). faulthandler._sigsegv() is no substitute on Windows:
# it raises a signal that is not caught off the main thread.
_CRASH_SCRIPT = """
import ctypes, sys, threading
from pathlib import Path
if sys.platform == "win32":
    ctypes.windll.kernel32.SetErrorMode(0x0002)  # no Windows crash dialog
from buildstock_rate_impact_calculator import launcher
launcher.start_log_file(Path(sys.argv[1]))
print("before crash", flush=True)
worker = threading.Thread(target=lambda: ctypes.c_char.from_address(0x18).value)
worker.start()
worker.join()
"""


def test_native_crash_stack_lands_in_log(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _CRASH_SCRIPT, str(tmp_path)],
        check=False,  # the child is meant to crash
        capture_output=True,
        timeout=120,
    )
    assert proc.returncode != 0
    (log,) = tmp_path.glob("bric-*.log")
    text = log.read_text(encoding="utf-8", errors="replace")
    assert "before crash" in text
    assert "fatal" in text.lower()
    assert "most recent call first" in text
