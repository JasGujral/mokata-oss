"""A3 — `mokata config wizard`: an interactive, GATED walk through mokata's settings.

A FRONT-END, never a second write authority (P2). For each user-facing setting it shows the
current value, a one-line description, the allowed values, and the documented default; when the
user changes one, the write is routed through the ONE gated path — `config_cmd.config_set` →
`WriteGate` (secret-scan hard-block + schema-validate + human gate + ledger). The wizard never
writes the manifest itself, so a change can't skip the gate or the ledger.

Fail-closed (P2): on a non-TTY / unreadable stdin it makes NO change and says so — a wizard must
never hang or silently write (it reuses `prompt._stdin_is_tty` + `read_approve_edit_reject`, both
of which default to "no change"). Each proposed change is previewed and gated; reject leaves the
manifest byte-unchanged; the ledger records each committed change.

The setting list is NOT invented: `SETTINGS` mirrors the documented settings reference
(`docs/reference/manifest.md`'s "Settings" table — the single source). A drift-guard test asserts
the two stay in lockstep, so a newly documented scalar setting can't be silently missed. (The docs
live at the repo root and aren't shipped in the wheel, so the registry is carried in-package and
kept honest by that test rather than parsed at runtime.)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

from . import config_cmd
from .legibility import _color_enabled, box, table
from .prompt import _stdin_is_tty, read_approve_edit_reject


@dataclass(frozen=True)
class Setting:
    key: str            # full dotted manifest key, e.g. "settings.ux.progress"
    rel: str            # the settings-relative key, e.g. "ux.progress" (matches manifest.md)
    allowed: str        # allowed-values hint, e.g. 'terminal | dashboard | both'
    default: str        # the documented default, e.g. "terminal"
    description: str     # the one-line "Feature" description


# The curated user-facing settings — MIRRORS docs/reference/manifest.md's Settings table (single
# source; a drift-guard test keeps them in lockstep). Only concrete SCALAR toggles are walked: the
# object row (`memory`) and the parameterized rows (`governance.karpathy.<id>`, `trust.<tool>`)
# need a sub-key/parameter and are edited with `mokata config set <key> <value>` directly.
SETTINGS: Tuple[Setting, ...] = (
    Setting("settings.ux.progress", "ux.progress",
            "terminal | dashboard | both", "terminal",
            "run-observability tier (Stage 40)"),
    Setting("settings.ux.statusline", "ux.statusline", "true | false", "true",
            "the always-on pipeline-stage badge (Stage 54b) — opt-out"),
    Setting("settings.ux.badge_verbosity", "ux.badge_verbosity",
            "full | minimal", "full",
            "badge detail: full (everything on) or minimal (just the current stage) — opt-DOWN"),
    Setting("settings.review.independent", "review.independent",
            "on | off", "on",
            "closing review as a fresh-context subagent (on) or the inline two-pass (off)"),
    Setting("settings.brainstorm.auto", "brainstorm.auto",
            "on | off | ask", "on",
            "auto-engage brainstorm when exploring: on (dive in), ask (offer first), off (never)"),
    Setting("settings.governance.output_density", "governance.output_density",
            "true | false", "false",
            "output-density compression (F4)"),
)


@dataclass
class WizardResult:
    ran: bool                                    # False when fail-closed (non-TTY / not initialized)
    changed: List[str] = field(default_factory=list)
    message: str = ""


def _current(root: str, key: str) -> Tuple[bool, Any]:
    """(found, value) for a setting, tolerant of an uninitialized/broken manifest (→ not found)."""
    try:
        return config_cmd.config_get(root, key)
    except config_cmd.ConfigCommandError:
        return (False, None)


def _shown(found: bool, value: Any) -> str:
    return json.dumps(value) if found else "(unset)"


def render_overview(root: str, settings: Tuple[Setting, ...], *, ascii_only: bool = False) -> str:
    """A one-screen table of the settings the wizard walks — current vs default — through the A4
    helpers. Colour-gated; zero escapes when piped / NO_COLOR / ascii_only."""
    header = box(
        ["mokata · config wizard — walk your settings (every change is gated + ledgered)",
         "for each:  [e]dit a new value  ·  [a]pprove keep  ·  [r]eject skip (default: skip)"],
        ascii_only=ascii_only,
    )
    rows = []
    for s in settings:
        found, val = _current(root, s.key)
        rows.append((s.rel, _shown(found, val), s.default, s.description))
    grid = table(("setting", "current", "default", "description"), rows, ascii_only=ascii_only)
    return "\n".join([header, "", grid])


def run_wizard(
    root: str,
    *,
    settings: Tuple[Setting, ...] = SETTINGS,
    reader: Optional[Callable[[str], str]] = None,
    confirm: Optional[Callable[[str], bool]] = None,
    ledger: Any = None,
    out: Optional[Callable[[str], None]] = None,
    is_tty: Optional[bool] = None,
    ascii_only: Optional[bool] = None,
) -> WizardResult:
    """Walk `settings` interactively; route every change through `config_cmd.config_set` (the one
    gated write path). Returns a WizardResult with the committed keys. `reader`/`confirm`/`is_tty`
    are injectable for tests; by default `reader` is `input`, `confirm` is the WriteGate's own
    y/N gate, and TTY is auto-detected."""
    emit = out or print

    # Fail-closed (P2): never prompt (and never hang) on a non-interactive stdin.
    tty = _stdin_is_tty() if is_tty is None else is_tty
    if not tty:
        emit("mokata config wizard: stdin is not a TTY — no changes made. Edit non-interactively "
             "with `mokata config set <key> <value> --yes`.")
        return WizardResult(ran=False, message="non-interactive: no changes made")

    # The wizard needs an initialized manifest to read/gate against.
    try:
        config_cmd.config_get(root, "profile")
    except config_cmd.ConfigCommandError as exc:
        emit(f"mokata config wizard: {exc}")
        return WizardResult(ran=False, message=str(exc))

    if ascii_only is None:
        ascii_only = not _color_enabled()
    emit(render_overview(root, settings, ascii_only=ascii_only))

    changed: List[str] = []
    for s in settings:
        found, val = _current(root, s.key)
        prompt = (f"\n{s.rel}  [{s.allowed}]"
                  f"\n  {s.description}"
                  f"\n  current: {_shown(found, val)}   default: {s.default}")
        # The gate: edit → a new value; approve → keep current; reject/blank/EOF → skip (safe).
        resp = read_approve_edit_reject(prompt, _shown(found, val), reader=reader)
        if resp.action != "edit":
            continue                                  # keep / skip → no change
        new_raw = (resp.value or "").strip()
        if new_raw == "":
            continue                                  # empty edit → no change

        # THE ONE WRITE PATH: secret-scan + schema-validate + WriteGate human-gate + ledger.
        result = config_cmd.config_set(root, s.key, new_raw,
                                       confirm=confirm, ledger=ledger, out=emit)
        if result.committed:
            changed.append(s.key)
        elif result.findings:
            emit(f"  (skipped {s.rel} — the change was blocked: secret detected)")

    if changed:
        emit(f"\nmokata config wizard: {len(changed)} change(s) committed: " + ", ".join(changed))
    else:
        emit("\nmokata config wizard: no changes committed.")
    return WizardResult(ran=True, changed=changed, message="ok")


__all__ = ["Setting", "SETTINGS", "WizardResult", "render_overview", "run_wizard"]
