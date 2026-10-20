"""ID.S3 — OSC-8 deep links from the CLI into the running IDE (0.0.25 Lane ID2; doc 109 §4.2 item 4).

Doc 109 §4.2: "Every `file:line` mokata prints (query results, review findings, brainstorm anchors,
spec ACs) becomes an OSC-8 hyperlink to `http://127.0.0.1:PORT/open?...`. Clicking a reference in
the CLI opens it in the IDE."

A link is emitted ONLY when all three hold — otherwise the exact plain text is printed, byte for
byte what the command printed before:

  1. the stream is a TTY (a pipe, a file, `--json`, a CI log never see an escape sequence);
  2. the terminal is one that renders OSC 8 (`hyperlinks_supported` — an allow-list of terminals
     known to render it, because an unknown terminal may print the escape as garbage; the
     `MOKATA_HYPERLINKS=0|1` override wins either way, `TERM=dumb` and `CI` never link);
  3. an IDE is running for this repo (`launch.running`, which PINGS it — a stale record from a
     crashed server is not "running").

The link carries NO token: `/open` is served to the paired browser by its cookie. A link copied
out of the scrollback opens nothing for anyone who has not paired.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional
from urllib.parse import quote

OSC = "\x1b]8;;"
ST = "\x1b\\"
OPEN_PATH = "/open"

# Terminals that render OSC 8 (each checked against its own documentation / changelog).
_TERM_PROGRAMS = frozenset({"iTerm.app", "WezTerm", "vscode", "ghostty", "Hyper", "Tabby",
                            "rio", "mokata-ide"})
_TERM_SUBSTR = ("kitty", "foot", "wezterm", "alacritty", "ghostty", "contour")
_ENV_MARKERS = ("WT_SESSION", "KITTY_WINDOW_ID", "KONSOLE_VERSION", "DOMTERM",
                "ALACRITTY_WINDOW_ID", "WEZTERM_EXECUTABLE")


def hyperlinks_supported(stream: Any, env: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    try:
        tty = bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        tty = False
    if not tty:
        return False
    force = env.get("MOKATA_HYPERLINKS", "")
    if force in ("0", "1"):
        return force == "1"
    term = env.get("TERM", "")
    if term == "dumb" or env.get("CI"):
        return False
    if env.get("TERM_PROGRAM", "") in _TERM_PROGRAMS:
        return True
    if any(env.get(k) for k in _ENV_MARKERS):
        return True
    if any(s in term for s in _TERM_SUBSTR):
        return True
    try:
        return int(env.get("VTE_VERSION", "0")) >= 5000       # GNOME Terminal & other VTE
    except ValueError:
        return False


def osc8(url: str, text: str) -> str:
    return f"{OSC}{url}{ST}{text}{OSC}{ST}"


def open_url(port: int, path: str, line: Optional[int] = None) -> str:
    url = f"http://127.0.0.1:{int(port)}{OPEN_PATH}?path={quote(path, safe='/')}"
    return url + (f"&line={int(line)}" if line else "")


class Linker:
    """Decides once per command, then wraps references. `ref(path, line, text)` returns `text`
    unchanged unless the three conditions above hold."""

    def __init__(self, root: str, stream: Any, env: Optional[Mapping[str, str]] = None,
                 port: Optional[int] = None):
        self.root = os.path.realpath(root)
        self.port: Optional[int] = None
        if hyperlinks_supported(stream, env):
            self.port = port if port else _running_port(self.root)

    @property
    def active(self) -> bool:
        return bool(self.port)

    def ref(self, path: str, line: Optional[int], text: str) -> str:
        if not self.port or not isinstance(path, str) or not path:
            return text
        rel = self._rel(path)
        if rel is None:
            return text
        try:
            n = int(line) if line else None
        except (TypeError, ValueError):
            n = None
        return osc8(open_url(self.port, rel, n), text)

    def _rel(self, path: str) -> Optional[str]:
        from .snapshots import rel_path
        return rel_path(self.root, path)


def _running_port(root: str) -> Optional[int]:
    try:
        from .launch import running
    except ImportError:
        return None
    rec = running(root)
    try:
        return int(rec["port"]) if rec else None
    except (KeyError, TypeError, ValueError):
        return None


__all__ = ["Linker", "hyperlinks_supported", "osc8", "open_url", "OPEN_PATH"]
