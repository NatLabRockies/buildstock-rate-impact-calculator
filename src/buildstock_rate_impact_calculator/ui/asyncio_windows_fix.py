"""Workaround for a Windows asyncio bug that hangs Streamlit on Ctrl+C.

When a browser closes its tab with a TCP reset, asyncio's proactor transport
raises ``ConnectionResetError`` from ``sock.shutdown()`` inside
``_call_connection_lost``'s ``finally`` block. That skips the rest of the
cleanup, including ``server._detach()``, so the asyncio ``Server`` never sees
the connection go away. On Ctrl+C, uvicorn's shutdown awaits
``Server.wait_closed()``, which then waits forever ("Stopping..." never exits).

The patch below performs the same cleanup but tolerates the peer having
already reset the socket. It is a no-op on non-Windows platforms.
"""

from __future__ import annotations

import socket
import sys

_PATCHED_ATTR = "_bric_reset_tolerant"


def apply() -> None:
    """Patch ``_ProactorBasePipeTransport._call_connection_lost`` (idempotent)."""
    if sys.platform != "win32":
        return

    from asyncio import proactor_events

    transport_cls = proactor_events._ProactorBasePipeTransport
    if getattr(transport_cls._call_connection_lost, _PATCHED_ATTR, False):
        return

    def _call_connection_lost(self, exc):  # mirrors CPython 3.11–3.13
        if self._called_connection_lost:
            return
        try:
            self._protocol.connection_lost(exc)
        finally:
            if hasattr(self._sock, "shutdown") and self._sock.fileno() != -1:
                try:
                    self._sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # peer already reset the connection
            self._sock.close()
            self._sock = None
            server = self._server
            if server is not None:
                server._detach(self)
                self._server = None
            self._called_connection_lost = True

    setattr(_call_connection_lost, _PATCHED_ATTR, True)
    transport_cls._call_connection_lost = _call_connection_lost
