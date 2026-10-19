"""Tests for the Windows asyncio connection-reset workaround."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from buildstock_rate_impact_calculator.ui import asyncio_windows_fix

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only patch")


def _fake_transport(shutdown_error: Exception | None):
    sock = MagicMock()
    sock.fileno.return_value = 5
    if shutdown_error is not None:
        sock.shutdown.side_effect = shutdown_error
    return SimpleNamespace(
        _called_connection_lost=False,
        _protocol=MagicMock(),
        _sock=sock,
        _server=MagicMock(),
    )


def test_reset_connection_still_detaches_from_server():
    """A peer reset must not skip server._detach (which hangs shutdown)."""
    from asyncio import proactor_events

    asyncio_windows_fix.apply()
    transport = _fake_transport(ConnectionResetError(10054, "reset"))
    sock, server = transport._sock, transport._server

    proactor_events._ProactorBasePipeTransport._call_connection_lost(transport, None)

    transport_protocol = transport._protocol
    transport_protocol.connection_lost.assert_called_once_with(None)
    sock.close.assert_called_once()
    server._detach.assert_called_once_with(transport)
    assert transport._server is None
    assert transport._called_connection_lost is True


def test_apply_is_idempotent():
    from asyncio import proactor_events

    asyncio_windows_fix.apply()
    first = proactor_events._ProactorBasePipeTransport._call_connection_lost
    asyncio_windows_fix.apply()
    assert proactor_events._ProactorBasePipeTransport._call_connection_lost is first
