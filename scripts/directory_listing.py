#!/usr/bin/env python3
"""Single source of truth for the Claude plugin-DIRECTORY listing notice.

The "pending directory approval" notice appears in several user docs (README + the docs site).
Rather than hand-edit each one when the listing is approved, THIS file owns it:

  * ``LISTED`` — the one flag the user flips to ``True`` after the directory approves the listing;
  * the PENDING / LIVE notice texts (the only two states);
  * ``apply()`` rewrites the marker-delimited region in every target file to match ``LISTED``.

**The directory APPROVAL itself is external** (a Claude/GitHub plugin-directory action the user
performs — mokata cannot self-approve). So we never *claim* the listing is live while ``LISTED``
is ``False``. To go live AFTER approval:

  1. set ``LISTED = True`` below;
  2. run ``python3 scripts/directory_listing.py --apply``;
  3. commit the doc changes.

``--check`` (used by the Stage-57 test) fails if any target drifts from the flag, so the notice
can never go stale or contradict itself. Pure stdlib; clean-room; Apache-2.0.
"""

from __future__ import annotations

import os
import re
import sys

# ─── the single flag the user controls ──────────────────────────────────────────────────────
# Flip to True ONLY after Claude's plugin directory approves the mokata listing, then run
# `python3 scripts/directory_listing.py --apply`. While False, the docs say "pending" — we never
# claim a listing that isn't approved.
LISTED = False

START = "<!-- mokata:directory-listing:start -->"
END = "<!-- mokata:directory-listing:end -->"

PENDING = (
    "> ⏳ **Pending Claude plugin-directory approval.** mokata isn't in Claude's in-app\n"
    "> \"Browse plugins\" directory **yet** — install it via `/plugin marketplace add` (you get\n"
    "> the same in-Claude-Code experience). _(This notice auto-flips once the listing is\n"
    "> approved — single source: `scripts/directory_listing.py`.)_"
)

LIVE = (
    "> ✅ **mokata is in the Claude plugin directory.** Install it straight from Claude Code's\n"
    "> in-app **\"Browse plugins\"**, or use the `/plugin marketplace add` command below."
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every file that carries the notice (relative to the repo root).
TARGETS = (
    "README.md",
    "docs/quickstart.md",
    "docs/how-to/install-plugin.md",
    "docs/how-to/use-the-plugin.md",
    "docs/tutorials/mokata-complete-guide.md",
)


def notice(listed: bool | None = None) -> str:
    """The full marker-wrapped notice block for the given state (defaults to ``LISTED``)."""
    listed = LISTED if listed is None else listed
    body = LIVE if listed else PENDING
    return f"{START}\n{body}\n{END}"


def _region_re() -> "re.Pattern[str]":
    return re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)


def apply(listed: bool | None = None, *, root: str = REPO_ROOT) -> list:
    """Rewrite the notice region in every target to match ``listed``. Returns the files changed."""
    listed = LISTED if listed is None else listed
    want = notice(listed)
    changed = []
    for rel in TARGETS:
        path = os.path.join(root, rel)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        new = _region_re().sub(lambda _m: want, text)
        if new != text:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            changed.append(rel)
    return changed


def check(listed: bool | None = None, *, root: str = REPO_ROOT) -> list:
    """Targets whose notice region is missing or out of sync with ``listed`` (empty = all good)."""
    listed = LISTED if listed is None else listed
    want = notice(listed)
    bad = []
    for rel in TARGETS:
        path = os.path.join(root, rel)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        m = _region_re().search(text)
        if m is None or m.group(0) != want:
            bad.append(rel)
    return bad


def _main(argv) -> int:
    arg = argv[0] if argv else "--check"
    if arg == "--apply":
        ch = apply()
        print(f"directory-listing: applied (LISTED={LISTED}); updated {ch or 'nothing'}")
        return 0
    if arg == "--check":
        bad = check()
        if bad:
            print(f"directory-listing OUT OF SYNC with LISTED={LISTED}: {bad}", file=sys.stderr)
            return 1
        print(f"directory-listing in sync (LISTED={LISTED}).")
        return 0
    print("usage: directory_listing.py [--check|--apply]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
