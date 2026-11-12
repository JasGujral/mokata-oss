"""Workspace trust (0.0.27 P24 `ID-LSP-READONLY-EARLIER`, research ruling R3) — the human decision
that lets the IDE run a language server on THIS repo's code.

WHY A GATE. A language server executes project code: `rust-analyzer` runs `build.rs` and proc
macros, `gopls` resolves Go modules (`go` tooling runs), `typescript-language-server` reads
`tsconfig` paths and plugins, `pyright` follows `pyrightconfig`/`pyproject` to the venv. px0 starts
them on an untrusted PR checkout with no prompt (doc 109 §6). Here nothing executes repo code until
a human marked the workspace TRUSTED — once, per workspace, as a P2-gated and LEDGERED decision.

THE RECORD. Trust lives in the USER's own profile, never inside the repo:
`~/.mokata/ide-repos/<sha256(realpath)[:16]>/trust.json` (the same key `ide/statedir.py` uses). A
repo cannot ship its own trust; a clone at another path is another workspace; deleting the file
revokes. The decision is appended to the repo's audit ledger when mokata governs the repo (kind
`workspace_trust`, actor `ide-tty` / `cli`), and to a user-level ledger beside the record otherwise
(an uninitialised repo gets nothing written into it — P29).

THE GATE. In the window the decision needs the ONE-TIME CODE printed to the server's own terminal
(`ide/approve_codes.py`, the P38 bar: the pairing token alone never trusts — an agent can read it).
The CLI twin is `mokata ide --trust` / `--untrust`, TTY-only and never inside a Claude Code session.
REVOKING is the safe direction: it needs no code, and it stops every language server.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

from . import statedir
from ..errors import MokataError

LEDGER_KIND = "workspace_trust"
RECORD = "trust.json"
USER_LEDGER = "ledger.jsonl"
TRUST_CMD = "mokata ide --trust"
UNTRUST_CMD = "mokata ide --untrust"
WHY = ("a language server runs this repo's own code (build scripts, plugins, tsconfig paths, Go "
       "modules) — trust it only if you would run its build")
#: Said plainly wherever trust is shown (`mokata status`, `mokata doctor`, `mokata ide --trust`, the
#: window): trust is a decision about the WORKSPACE, not a seal on the commands shown with it.
#: 0.0.27 SMALL-FIX (P24 L5, Jas: option B) - not hashing the argv is deliberate: trust already
#: means "this repo's code may run", and a hash would re-prompt on every `npm i` or venv rebuild.
NOTE = ("trust covers this workspace, not the exact commands: the language servers listed are what "
        "would run TODAY, and mokata does not record or re-check them. If a venv, node_modules or "
        "PATH entry changes what a server resolves to, it runs under this same trust. Revoke with "
        "`mokata ide --untrust`")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def record_path(root: str) -> str:
    return os.path.join(statedir.user_repo_dir(root), RECORD)


def _real(root: str) -> str:
    return os.path.realpath(os.path.abspath(root))


def status(root: str) -> Dict[str, Any]:
    """`{trusted, at, by, path, root, why, command, revoke_command}` — read from the user's record;
    a record for ANOTHER path (the directory moved) is not trust for this one."""
    out: Dict[str, Any] = {"trusted": False, "at": None, "by": None, "root": _real(root),
                           "why": WHY, "note": NOTE, "command": TRUST_CMD,
                           "revoke_command": UNTRUST_CMD}
    from ..winacl import read_private
    raw = read_private(record_path(root))          # 0.0.28 C1: ours and private, or not trust
    if raw is None:
        return out
    try:
        rec = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return out
    if not isinstance(rec, dict) or rec.get("root") != out["root"] or not rec.get("trusted"):
        return out
    out.update({"trusted": True, "at": rec.get("at"), "by": rec.get("by")})
    return out


def status_line(root: str) -> Optional[str]:
    """The one line `mokata status` and `mokata doctor` print about IDE workspace trust, or None
    when this workspace was never trusted (nothing to say). Read-only; never raises."""
    try:
        st = status(root)
    except (OSError, ValueError):                        # a status line never breaks the command
        return None
    if not st["trusted"]:
        return None
    return f"IDE workspace trust: trusted {st['at'] or ''} by {st['by'] or '?'} - {NOTE}."


def _ledger(root: str):
    from ..govern.ledger import AuditLedger
    if statedir.governed(root):
        from .. import MOKATA_DIR
        return AuditLedger.from_mokata_dir(os.path.join(root, MOKATA_DIR)), "repo"
    return AuditLedger(os.path.join(statedir.user_repo_dir(root), USER_LEDGER)), "user"


def _write(root: str, rec: Dict[str, Any]) -> None:
    """The record, through the shared atomic primitive (`atomicfile.atomic_write_text`: temp →
    fsync → replace) — the one sanctioned file writer, so no second mechanism grows here — and its
    PRIVATE door (0.0.28 C1): the decision is the user's own, readable by them alone."""
    from ..atomicfile import atomic_write_text
    path = record_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_text(path, json.dumps(rec, indent=1, sort_keys=True) + "\n", private=True)
    statedir.note_repo(root)


def decide(root: str, trusted: bool, actor: str) -> Dict[str, Any]:
    """Grant or revoke, ledgered. Returns the new status plus the ledger row written.

    GRANT: the ledger row first, then the record — a grant that cannot be ledgered does not
    happen (the exception propagates; the workspace stays untrusted).
    REVOKE is the fail-safe direction and is never blocked by the ledger (verify2 N3: in a
    governed repo the ledger is the REPO's own file, so the repo could make it unwritable — a
    directory at its path — and keep itself trusted). The record is flipped FIRST; the ledger row
    is written after it, and if that fails the answer carries `ledger_error` (the CLI prints it,
    the window shows it) — said loudly, never silent, and the workspace is untrusted either way."""
    real = _real(root)
    if not trusted:
        _write(root, {"trusted": False, "root": real, "at": _now_iso(), "by": actor,
                      "ledger": None, "ledger_seq": None})
        ledger_error = None
        row: Dict[str, Any] = {}
        where = None
        try:
            led, where = _ledger(root)
            row = led.record(LEDGER_KIND, decision="revoked", actor=actor, target=real,
                             reason="revoked by the human")
            _write(root, {"trusted": False, "root": real, "at": row.get("at") or _now_iso(),
                          "by": actor, "ledger": where, "ledger_seq": row.get("seq")})
        except (OSError, ValueError, MokataError) as exc:
            ledger_error = (f"trust WAS revoked, but the audit ledger row could not be written "
                            f"({type(exc).__name__}: {exc})")[:500]
        out = status(root)
        out["ledger_row"] = {k: row.get(k) for k in ("seq", "at", "decision", "actor", "entry_hash")}
        out["ledger"] = where
        out["ledger_error"] = ledger_error
        return out
    led, where = _ledger(root)
    row = led.record(LEDGER_KIND, decision="trusted", actor=actor, target=real, reason=WHY)
    _write(root, {"trusted": True, "root": real, "at": row.get("at") or _now_iso(),
                  "by": actor, "ledger": where, "ledger_seq": row.get("seq")})
    out = status(root)
    out["ledger_row"] = {k: row.get(k) for k in ("seq", "at", "decision", "actor", "entry_hash")}
    out["ledger"] = where
    out["ledger_error"] = None
    return out


def cli_decide(root: str, trusted: bool, stdin: Any = None, stdout: Any = None,
               environ: Optional[Dict[str, str]] = None, assume_yes: bool = False) -> int:
    """`mokata ide --trust` / `--untrust`: the same bar as `mokata approve` — a human at a TTY, not
    inside a Claude Code session — then a y/N question. Returns the exit code."""
    import sys
    from .approve_codes import human_terminal
    out = sys.stdout if stdout is None else stdout
    inp = sys.stdin if stdin is None else stdin
    if not trusted:
        res = decide(root, False, "cli")
        if res["ledger_error"]:
            out.write(f"mokata ide: workspace trust REVOKED for {res['root']}; language servers "
                      f"will not start here.\n  WARNING: {res['ledger_error']}\n")
            return 3
        out.write(f"mokata ide: workspace trust REVOKED for {res['root']} (ledger {res['ledger']} "
                  f"#{res['ledger_row']['seq']}); language servers will not start here.\n")
        return 0
    ok, why = human_terminal(environ, inp, out)
    if not ok:
        out.write("mokata ide --trust: refused — " + why.split(" — ")[0]
                  + " (a terminal of your own is needed; never from an agent's session)\n")
        return 2
    from .lsp import will_run
    runs = will_run(root)
    lines = "".join(f"    {r['language']}: {' '.join(r['argv'])}\n" for r in runs) or \
        "    (no language server found yet — trusting allows the built-in ones once installed)\n"
    out.write(f"Trust {_real(root)}?\n  {WHY}.\n  These commands would run its code:\n{lines}"
              f"  Note: {NOTE}.\n  Trust it? [y/N] ")
    out.flush()
    if not assume_yes:
        answer = (inp.readline() or "").strip().lower()
        if answer not in ("y", "yes"):
            out.write("not trusted — nothing changed.\n")
            return 1
    res = decide(root, True, "cli")
    out.write(f"trusted (ledger {res['ledger']} #{res['ledger_row']['seq']}); `{UNTRUST_CMD}` "
              "revokes it.\n")
    return 0


__all__ = ["NOTE", "status_line", "LEDGER_KIND", "TRUST_CMD", "UNTRUST_CMD", "WHY", "status", "decide", "cli_decide",
           "record_path"]
