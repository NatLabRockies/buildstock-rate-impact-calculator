"""Tests for the desktop launcher (the ``bric`` console script)."""

from __future__ import annotations

import socket
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
    opened: list = []
    monkeypatch.setattr(launcher, "_open_browser_when_ready", opened.append)

    with pytest.raises(SystemExit):
        launcher.main(["--no-browser", "--port", "8124"])

    assert _option(calls["args"], "--server.port") == "8124"
    assert calls["args"][1] == str(launcher.APP_PATH)
    assert opened == []
