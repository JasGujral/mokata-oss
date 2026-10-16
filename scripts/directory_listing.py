#!/usr/bin/env python3
"""Single source of truth for the plugin MARKETPLACE-LISTING notice.

The notice appears in several user docs (README + the docs site). Rather than hand-edit each one
when the listing lands, THIS file owns it:

  * ``LISTED`` — the one flag the user flips to ``True`` once the community marketplace lists mokata;
  * the PENDING / LIVE notice texts (the only two states);
  * ``apply()`` rewrites the marker-delimited region in every target file to match ``LISTED``.

**Which channel (0.0.23 B4 — `PLUGIN-DIRECTORY-WRONG-CHANNEL`).** The notice used to wait on
*"Claude plugin-directory approval"* — but the official marketplace (``claude-plugins-official``)
is partner-contact only. Individual authors submit to the **community** marketplace
(platform.claude.com/plugins/submit). Meanwhile the plugin installs TODAY from mokata's own
self-hosted marketplace (``.claude-plugin/marketplace.json``, name ``mostack``), which needs no
approval — so both states lead with those two commands, and only the community listing is pending.

**The listing itself is external** (a credentialed submission the user performs — mokata cannot
list itself). So we never *claim* a listing while ``LISTED`` is ``False``. To go live AFTER the
community marketplace lists mokata:

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
# Flip to True ONLY after the Claude community plugin marketplace lists mokata, then run
# `python3 scripts/directory_listing.py --apply`. While False, the docs say the listing is pending
# submission — we never claim a listing that does not exist.
LISTED = False

START = "<!-- mokata:directory-listing:start -->"
END = "<!-- mokata:directory-listing:end -->"

#: The two in-app commands — the self-hosted marketplace (`.claude-plugin/marketplace.json`).
INSTALL_COMMANDS = ("/plugin marketplace add JasGujral/mokata-oss", "/plugin install mokata@mostack")

PENDING = (
    "> **Installs today as a Claude Code plugin** from mokata's own marketplace — inside Claude\n"
    "> Code run `/plugin marketplace add JasGujral/mokata-oss`, then `/plugin install mokata@mostack`\n"
    "> (the plugin starts its MCP server through `uvx`, so [`uv`](https://docs.astral.sh/uv/) must be\n"
    "> on PATH).\n"
    "> ⏳ **Pending: the community-marketplace listing** (pending submission) — mokata is not yet\n"
    "> listed in Claude's community plugin marketplace; until it is, use the two commands above.\n"
    "> _(This notice auto-flips once the listing exists — single source:\n"
    "> `scripts/directory_listing.py`.)_"
)

LIVE = (
    "> ✅ **mokata is listed in the Claude community plugin marketplace.** Install it from Claude\n"
    "> Code's in-app plugin browser — or from mokata's own marketplace with\n"
    "> `/plugin marketplace add JasGujral/mokata-oss` then `/plugin install mokata@mostack`\n"
    "> (either way the MCP server starts through `uvx`, so `uv` must be on PATH)."
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
