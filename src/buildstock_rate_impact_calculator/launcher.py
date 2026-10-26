"""Desktop launcher: start the app on localhost and open it in the browser.

Installed as the ``bric`` console script. The stand-alone packages' "Start
BRIC" scripts run it after unpacking the bundled environment (see
``standalone/``); developers can use it too (``pixi run bric``).

Streamlit runs headless — otherwise a first run stops at a console prompt
asking for an email address, which hangs a double-click launch — and a
background thread opens the browser once the server answers its health check.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

HOST = "127.0.0.1"
HEALTH_PATH = "/_stcore/health"
STARTUP_TIMEOUT_SECONDS = 120
APP_PATH = Path(__file__).with_name("app.py")


def app_version() -> str:
    try:
        return version("buildstock-rate-impact-calculator")
    except PackageNotFoundError:
        return "unknown"


def find_free_port(host: str = HOST) -> int:
    """Ask the OS for an unused TCP port on *host*."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def streamlit_args(app_path: Path, port: int, host: str = HOST) -> list[str]:
    """Arguments for ``streamlit`` that run the app as a local desktop tool."""
    return [
        "run",
        str(app_path),
        # Local only: not reachable from other machines on the network.
        "--server.address",
        host,
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--server.fileWatcherType",
        "none",
        "--browser.gatherUsageStats",
        "false",
        # Hides Streamlit's developer menu items such as "Deploy".
        "--client.toolbarMode",
        "minimal",
        "--global.developmentMode",
        "false",
    ]


def _http_ok(url: str) -> bool:
    # Bypass any system proxy: a corporate proxy must never see localhost calls.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=2) as resp:
            return resp.status == 200
    except OSError:
        return False


def wait_until_healthy(
    url: str,
    timeout: float = STARTUP_TIMEOUT_SECONDS,
    is_ok: Callable[[str], bool] = _http_ok,
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = 0.5,
) -> bool:
    """Poll *url* until it answers 200 or *timeout* seconds of polling pass."""
    for _ in range(max(1, round(timeout / poll_seconds))):
        if is_ok(url):
            return True
        sleep(poll_seconds)
    return False


def _open_browser_when_ready(base_url: str) -> None:
    import webbrowser

    if wait_until_healthy(base_url + HEALTH_PATH):
        webbrowser.open(base_url)
    else:
        print(
            f"The app is taking a long time to start. Open {base_url} in your "
            "browser once it is ready.",
            flush=True,
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bric", description="Start the BuildStock Rate Impact Calculator."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to serve on (default: any free port).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser tab (for automated checks).",
    )
    parser.add_argument("--version", action="version", version=app_version())
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    port = args.port or find_free_port()
    base_url = f"http://{HOST}:{port}"

    print(
        f"BuildStock Rate Impact Calculator {app_version()}\n"
        f"Running at {base_url}\n"
        "Closing the browser tab (or this window) stops the app.\n",
        flush=True,
    )
    if not args.no_browser:
        threading.Thread(
            target=_open_browser_when_ready,
            args=(base_url,),
            daemon=True,
            name="bric-open-browser",
        ).start()

    # Imported late so `bric --version` / `--help` stay fast.
    from streamlit.web import cli as stcli

    sys.exit(stcli.main(args=streamlit_args(APP_PATH, port), prog_name="streamlit"))


if __name__ == "__main__":
    main()
