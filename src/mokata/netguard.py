"""K4 — local-first / no-telemetry: enforcement helpers.

mokata phones home nothing by default; nothing leaves the machine unless a human
explicitly wires an external service. Two surfaces make that *checkable* rather than
merely asserted:

  - `no_network()` — a context manager that turns every outbound socket connection
    into a loud `NetworkEgressBlocked` error. Wrap any code path in it to *prove* the
    path makes zero connections (the minimal profile runs its whole path inside it).
  - `network_capable_tools()` — lists the enabled tools in a manifest that *could*
    egress (kinds `mcp` / `external`). Local-only kinds (cli / library / builtin)
    never appear; minimal and standard wire none.

This is a guard for tests and opt-in strict runs — mokata does not silently install a
global socket block on import (that would itself be surprising, non-local behavior).
"""

from __future__ import annotations

import socket
from contextlib import contextmanager
from typing import Iterator, List

from .manifest import Manifest

# Tool kinds that can reach off-machine. Everything else (cli, library, builtin) is
# local-only by construction.
NETWORK_CAPABLE_KINDS = ("mcp", "external")

# Stated plainly so it can be surfaced in UIs / docs / the constitution.
LOCAL_FIRST_STATEMENT = (
    "mokata is local-first: nothing leaves this machine unless you explicitly wire an "
    "external service. No telemetry."
)


class NetworkEgressBlocked(RuntimeError):
    """Raised when code attempts network egress inside a `no_network()` guard."""


@contextmanager
def no_network() -> Iterator[None]:
    """Block all outbound socket connections for the duration of the context.

    Patches the connection-initiating entry points (`socket.create_connection` and
    `socket.socket.connect` / `connect_ex`); any attempt to use them raises
    `NetworkEgressBlocked`. Originals are always restored on exit.
    """
    orig_create_connection = socket.create_connection
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex

    def _blocked(*_args, **_kwargs):
        raise NetworkEgressBlocked(
            "network egress attempted under no_network() — " + LOCAL_FIRST_STATEMENT
        )

    socket.create_connection = _blocked  # type: ignore[assignment]
    socket.socket.connect = _blocked  # type: ignore[assignment]
    socket.socket.connect_ex = _blocked  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.create_connection = orig_create_connection  # type: ignore[assignment]
        socket.socket.connect = orig_connect  # type: ignore[assignment]
        socket.socket.connect_ex = orig_connect_ex  # type: ignore[assignment]


def network_capable_tools(manifest: Manifest) -> List[str]:
    """Enabled tools in this manifest that could egress (sorted, deterministic).

    A disabled tool (per-tool toggle, K1) cannot egress, so it is excluded.
    """
    return sorted(
        tool_id
        for tool_id, tool in manifest.tools.items()
        if isinstance(tool, dict)
        and tool.get("kind") in NETWORK_CAPABLE_KINDS
        and manifest.tool_enabled(tool_id)
    )
