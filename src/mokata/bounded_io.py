"""A1 — THE ONE BOUNDED READ, for a pipe that can go quiet.

A stdio server that starts and then says nothing is not a server that failed: `poll()` reports it
alive, the pipe is open, and `readline()` simply never returns. Every one of mokata's stdio clients
therefore needs a read that is bounded by a clock rather than by the peer's goodwill — and until
0.0.19 the tree had **two** hand-rolled copies of that bound (`mcp_admin.handshake`,
`tests/windows_e2e_check.py`) and one transport with **none** at all
(`knowledge/crg_client._McpStdioSession`, the defect behind #45/#46/#53).

This module is that bound, written once. `mcp_admin.handshake` and `_McpStdioSession` both use it.
(`tests/windows_e2e_check.py` keeps its own copy on purpose: it is a BLACK-BOX probe of the
INSTALLED package and imports nothing from `mokata`, so borrowing this helper would make the probe
depend on the very tree it exists to check from the outside.)

⭐ A THREAD, NOT `select`. `select`/`poll` on a pipe does not work on Windows, and every current
mokata user is on Windows. The daemon-reader-thread shape is the one `mcp_admin` and the Windows
E2E probe already proved on that platform; this is that shape, extracted.

⭐ FOUR OUTCOMES, FOUR REPRESENTATIONS (doc 85 §7g). A read that answered, a read that timed out, a
stream that closed, and a read that raised are four different facts, and the whole reason this
module exists is that the first three used to arrive as the same empty string — or, in the hanging
case, as nothing at all. `BoundedReadResult` gives each its own name so no caller can collapse two
of them by accident.

Stdlib-only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Optional

#: The read returned text inside the bound.
READ_ANSWERED = "answered"
#: The bound expired with the reader still parked. The peer is ALIVE and MUTE — the fact that has
#: no other representation here, and the one this module was written for.
READ_TIMEOUT = "timeout"
#: The stream reached EOF: the peer closed its end (it died, or it hung up). Distinct from
#: `READ_TIMEOUT` — "it stopped" and "it stopped answering" send a reader to different remedies.
READ_CLOSED = "closed"
#: The read itself raised (the stream was torn down mid-read, the descriptor went away).
READ_ERROR = "error"

#: How much of a drained stderr the tail keeps. Both caps are deliberate: the LINE cap bounds
#: memory for a chatty server, the CHAR cap bounds what can ever reach a user-facing notice.
DEFAULT_TAIL_LINES = 40
DEFAULT_TAIL_CHARS = 600


@dataclass
class BoundedReadResult:
    """One bounded read's outcome, carrying its own provenance so it cannot be mistaken for a
    different one (doc 85 §7g's `RunResolution` model)."""

    outcome: str
    text: str = ""
    error: Optional[BaseException] = None

    @property
    def answered(self) -> bool:
        return self.outcome == READ_ANSWERED

    @property
    def timed_out(self) -> bool:
        return self.outcome == READ_TIMEOUT


def read_bounded(reader: Callable[[], str], timeout: float) -> BoundedReadResult:
    """Run `reader()` on a daemon thread and give it `timeout` seconds to come back.

    On expiry the thread is LEFT PARKED — there is no way to interrupt a blocking read, and that
    is precisely why the caller must then kill the peer rather than reuse it. The thread is a
    daemon, so a parked reader never delays interpreter shutdown, and it unwinds by itself the
    moment the stream is closed underneath it.
    """
    holder: dict = {}

    def _pump() -> None:
        try:
            holder["text"] = reader()
        except Exception as exc:            # carried, never swallowed — see READ_ERROR below
            holder["error"] = exc

    thread = threading.Thread(target=_pump, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return BoundedReadResult(READ_TIMEOUT)
    error = holder.get("error")
    if error is not None:
        return BoundedReadResult(READ_ERROR, error=error)
    text = holder.get("text") or ""
    if not text:
        return BoundedReadResult(READ_CLOSED)
    return BoundedReadResult(READ_ANSWERED, text=text)


def read_line_bounded(stream: Any, timeout: float) -> BoundedReadResult:
    """`stream.readline()`, bounded. The one call site shape both stdio clients need."""
    return read_bounded(stream.readline, timeout)


class BoundedStderrTail:
    """Drain a subprocess's stderr continuously into a bounded ring, so the tail is available the
    instant something goes wrong.

    ⚠ THIS IS WHY IT DRAINS RATHER THAN READING ON DEMAND. Capturing stderr with
    `subprocess.PIPE` and never reading it fills the OS pipe buffer (~64 KiB), at which point the
    subprocess BLOCKS on its next write — a brand-new hang, in the same release whose whole subject
    is a hang. A continuous drain has no such ceiling, and it also means the account of what went
    wrong is already in hand when a read times out, instead of needing a second bounded read from
    a pipe we just proved can block.
    """

    def __init__(self, stream: Any, max_lines: int = DEFAULT_TAIL_LINES,
                 max_chars: int = DEFAULT_TAIL_CHARS) -> None:
        self._lines: Deque[str] = deque(maxlen=max_lines)
        self._max_chars = max_chars
        self._stream = stream
        self._thread: Optional[threading.Thread] = None
        if stream is not None:
            self._thread = threading.Thread(target=self._drain, daemon=True)
            self._thread.start()

    def _drain(self) -> None:
        try:
            for line in self._stream:        # ends at EOF, i.e. when the peer dies or is killed
                self._lines.append(line.rstrip("\r\n"))
        except Exception:                    # the stream was torn down under us; see the docstring
            pass

    def tail(self) -> str:
        """The last lines the peer wrote, joined and capped. Never blocks: it reads the ring the
        drain thread has already filled."""
        text = " | ".join(line for line in list(self._lines) if line.strip())
        return text[-self._max_chars:]
