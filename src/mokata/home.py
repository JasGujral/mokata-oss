"""MOK-HOME (0.0.23 E2) — bare `mokata` shows LIVE state and the next step, not an argparse error.

AXI principle 8, content-first: the most common thing a person (or an agent) types first is the
bare command, and it used to answer `error: the following arguments are required: command`. It now
answers what is true of THIS repo — version · mode · profile, the active run and its phase, how
many writes are waiting on a human, whether the gate hooks will actually fire — followed by the
top next-step commands. Help stays at `mokata --help`.

READ-ONLY and cheap: no git, no network, no doctor run (the hook check is a local resolution of the
wired command). Every line degrades to an honest "could not read …" rather than failing the screen.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import List, Tuple

from .plugin_runtime import slash_command


def _line(fn, fallback: str) -> str:
    try:
        return fn()
    except Exception:  # noqa: BLE001 — one unreadable fact must not take the home screen down
        return fallback


def build_home(root: str = ".") -> Tuple[str, List[str]]:
    """(text, next_commands) for bare `mokata` in `root`."""
    from . import __version__
    from .config import Surface, find_project_root
    root = find_project_root(root)
    if not Surface.is_initialized(root):
        nxt = ["mokata init", "mokata tour", "mokata --help"]
        text = (f"mokata {__version__} — not set up in {root}.\n"
                f"next:\n"
                f"  mokata init       set this repo up (detects your tools; asks before writing)\n"
                f"  mokata tour       a two-minute walk through what mokata does\n"
                f"  mokata --help     every command")
        return text, nxt
    try:
        surface = Surface.load(root)
    except Exception as exc:  # noqa: BLE001 — a broken config is reported, with the fix
        text = (f"mokata {__version__} — this repo's mokata config could not be read ({exc}).\n"
                f"next:\n"
                f"  mokata doctor     names what is broken and how to fix it\n"
                f"  mokata --help     every command")
        return text, ["mokata doctor", "mokata --help"]

    lines = [_line(lambda: _identity(surface), f"mokata {__version__} · {root}")]
    run_line, run_next = _run(surface)
    lines.append(run_line)
    pending = _line(lambda: _pending(root), "  approvals: could not be read")
    lines.append(pending)
    lines.append(_line(lambda: _hooks(surface), "  gates: could not check the hook wiring "
                                                "(`mokata doctor`)"))
    nxt: List[Tuple[str, str]] = []
    if run_next:
        nxt.append(run_next)
    if pending.startswith("  approvals waiting on you:") and not pending.endswith(": 0"):
        nxt.append(("mokata approve --list", "the writes waiting on you"))
    nxt += [("mokata doctor", "full health check"), ("mokata --help", "every command")]
    width = max(len(c) for c, _ in nxt)
    lines.append("next:")
    lines += [f"  {c:<{width}}  {why}" for c, why in nxt]
    return "\n".join(lines), [c for c, _ in nxt]


def _identity(surface) -> str:
    from .run_mode import read_mode
    m = surface.manifest
    return (f"mokata {m.mokata_version} · mode: {read_mode(surface)} · profile: {m.profile} · "
            f"{surface.root}")


def _run(surface) -> Tuple[str, Tuple[str, str]]:
    """The run line (its words unchanged) and the next pair — 0.0.27 P14: the next pair is the
    run's PRIMARY next-step hint (`hints.derive`), so bare `mokata`, `mokata suggest`, the MCP tool
    and the IDE say the same next step. A run whose recorded state cannot be read says so, naming
    the store, and points at `doctor` — never a guessed "resume"."""
    try:
        from .progress import build_progress
        from .run_resolver import resolve_run
        res = resolve_run(surface.root)
        if res.run_id is None:
            if getattr(res, "ambiguous", False):
                return (f"  run: {len(res.candidates)} runs, none identifiably yours",
                        ("mokata sessions", "list them, then `--run <id>`"))
            return ("  run: none active", (slash_command("brainstorm", surface.root), "start a tracked run"))
        p = build_progress(surface.state, run_id=res.run_id)
        if not p.active:
            line = f"  run: {res.run_id[:8]} — no progress recorded yet"
        elif p.complete:
            line = f"  run: {res.run_id[:8]} — complete [{p.done}/{p.total}]"
        else:
            line = f"  run: {res.run_id[:8]} — phase '{p.current}' [{p.done}/{p.total}]"
    except Exception:  # noqa: BLE001
        return ("  run: could not be read", ("mokata doctor", "see what is damaged"))
    return _next_from_hint(surface.root, res.run_id, line)


def _next_from_hint(root: str, run_id: str, line: str) -> Tuple[str, Tuple[str, str]]:
    from .hints import STATUS_NONE, STATUS_UNKNOWN, derive, primary
    ans = derive(root, run=run_id)
    if ans["status"] == STATUS_UNKNOWN:
        return (f"  run: {run_id[:8]} — could not be read ({ans['reason']})",
                ("mokata doctor", "see what is damaged"))
    if ans["status"] == STATUS_NONE:
        return line, (slash_command("brainstorm", root), "start the next run")
    h = primary(ans)
    return line, (h["command"], h["text"])


def _pending(root: str) -> str:
    from . import approval
    waiting = [p for p in approval.pending(root) if not p.approved]
    return f"  approvals waiting on you: {len(waiting)}"


def _hooks(surface) -> str:
    from .govern.doctor import hook_resolution_findings
    bad = [f for f in hook_resolution_findings(surface) if f.severity == "error"]
    if bad:
        return f"  gates: {len(bad)} hook problem(s) — gates may NOT fire (`mokata doctor`)"
    return "  gates: no unresolvable hook found"
