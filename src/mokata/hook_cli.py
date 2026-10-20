"""`mokata-hook` — the console entry point for mokata's Claude Code hooks (Stage 53b).

mokata's hooks used to be launched by a static plugin ``hooks.json`` that ran
``sh launch.sh`` to resolve *a* ``python3`` / ``python`` / ``py`` at run time. That chain
is fragile: Windows has no ``sh``, a GUI-launched macOS Claude Code runs with a minimal
PATH, and any stale config that baked a bare ``python3`` never re-resolves — so test users
hit ``python3: command not found`` on the PreToolUse hook.

The bundled MCP server never had this problem: it's launched as the ``mokata-mcp`` **console
entry point**, resolved exactly like any other installed script. This module gives the hooks
the *same* mechanism: a single ``mokata-hook`` entry point with its subcommands —

    mokata-hook session-start [--plugin-root <dir>]
    mokata-hook secret-guard  [--text … --path … --send]
    mokata-hook gate-guard    [--path … --cwd …]          (SI.1 — the run-state gates)
    mokata-hook statusline    [--wrap <command>]

No bare ``python3``, no ``sh``, no PATH guessing. If ``mokata-mcp`` resolves for a user (it
must, for the MCP server to work), ``mokata-hook`` resolves identically.

Two of these are PreToolUse hooks and BOTH may block with exit 2, for the two sanctioned reasons:
``secret-guard`` is the SECURITY block (G4/I1 — never overridable), and ``gate-guard`` is the
run-state GATE block (SI.1 — a methodology gate, overridable under P14's discipline). They are
independent entries on the same event; neither knows about the other.

This module is the **single source of truth** for the hook runtime. The standalone
``hooks/session_start.py`` / ``hooks/secret_guard.py`` / ``hooks/gate_guard.py`` scripts are thin
shims that import from here, so the legacy ``launch.sh`` fallback (and any direct invocation) still
works.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re          # free: `json.decoder` imports it anyway
import sys

# ⚡ HOOK-BUDGET (0.0.25 Lane HB, doc 109 §17.2: an observability hook ≤ 50 ms p95). Python runs
# this module's top level for EVERY `mokata-hook` call, so it imports only json/os/re/sys. `typing` is
# for readers (the annotations are strings under `from __future__ import annotations`), and
# `threading` and `degrade` (→ dataclasses → inspect) are imported where they are used. The
# observability hooks that have nothing to do outside a mokata repo answer from `main` on stdlib
# alone (`_surely_not_mokata`); measured before/after in the HB report.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Any, Callable, List, Optional

# `mcp_admin` (≈20 ms: dataclasses, inspect, subprocess, urllib) is imported INSIDE
# `_mcp_reachability_warning`, its only user. Not a cycle workaround — Stage 3d.1 broke that cycle via
# `harness_paths` — but HOOK-BUDGET (0.0.25 Lane HB): every `mokata-hook` subcommand imports this
# module, and only session-start needs the MCP registration check.

# D5 — the degrade notice is imported at the TOP of each of its two users (`secret_guard_main`,
# `gate_guard_main`), never inside their `except`: an import that lives in the handler for "the
# engine could not be imported" is an import that can fail exactly when it is needed, turning a
# swallow into a traceback. It used to be module-level; HOOK-BUDGET moved it to the function top,
# which keeps that guarantee (if `degrade` cannot import, the gate hook fails loud before its try,
# exactly as a failed module import did) while the observability hooks stop paying for it.

# Security-block exit code (PreToolUse): a non-zero exit blocks the tool call.
BLOCK_EXIT = 2

# The two events mokata injects context on, and the names Claude Code keys the payload by. A
# payload stamped with the wrong `hookEventName` is dropped silently, so these are named once.
SESSION_START_EVENT = "SessionStart"
USER_PROMPT_SUBMIT_EVENT = "UserPromptSubmit"

# Keys inside tool_input that name the target path (for path-based detection, e.g. .env).
_PATH_KEYS = ("file_path", "path", "notebook_path", "target_file")

# How long a hook waits for stdin before giving up and using its default. Claude Code's real
# invocation writes its JSON payload and closes stdin, so the read returns instantly at EOF —
# this is pure headroom, not truncation. It only bites when stdin is an OPEN non-TTY pipe with
# no writer, where a bare read() would block forever and hang the session.
_STDIN_READ_TIMEOUT_SECS = 2.0

# How long the UserPromptSubmit hook may spend on its OWN WORK — the H-1a recall pack — before it
# gives up and injects nothing (OSS issue #43: "UserPromptSubmit hook timed out after 30s").
#
# WHY IT EXISTS. `_STDIN_READ_TIMEOUT_SECS` above bounds READING the payload; nothing bounded what
# the hook then did with it. The hook is documented FAIL-OPEN, but that is fail-open on EXIT CODE,
# not on TIME: it never blocks a tool call, and it still stalls the human for however long its
# slowest phase takes. Claude Code kills a hook at 30 s, discards its output, and shows the user a
# warning on an ordinary turn — so an unbounded body converts a slow disk, a cold cache or a
# filtered network into a scary message about a hook the user never asked about.
#
# WHY 5 s. Measured, not guessed. End-to-end in a cold subprocess the whole hook is ~90-100 ms, of
# which ~85 ms is interpreter start plus imports; the recall itself ran 0.5-1.1 ms at 0 / 50 / 500
# memory items and `always_on_lines` peaked at 3.3 ms. 5 s is ~1000x the measured body cost and
# ~50x the entire process, so it cannot trip a healthy turn on a far slower machine — and it is a
# sixth of the harness's kill, so mokata's own degrade always fires FIRST and the user gets a
# normal turn instead of a stall plus a warning. It is a runaway catch, not a performance target.
#
# WHAT IT CATCHES. `build_injection` opens the store on every prompt, and `MemoryStore.from_surface`
# resolves the embedder; with `memory.embedder: auto|model2vec` that reaches `_load_model2vec`,
# whose only bound is `MODEL_FETCH_TIMEOUT_S = 30.0` for the model fetch. A sub-budget equal to the
# harness's WHOLE budget can never fire in time to help — this one can.
#
# THE DEGRADE IS SILENCE, never a partial pack: half a recall pack is context the model will trust,
# cut wherever the clock stopped. A turn with no pack is a correct turn.
_HOOK_WORK_BUDGET_SECS = 5.0

# The raw stdin `main` read for its fast path (`_surely_not_mokata`), handed to the handler's own
# `_read_stdin_bounded` call — stdin can be consumed only once. Set and cleared by `main` around ONE
# invocation, so nothing leaks between in-process calls.
_STDIN_MEMO: list = []

# U8 (0.0.25 Lane HB) — the I/O seam that lets `pretooluse_verdict` run the SAME gate mains in a
# long-lived process (the IDE's `/api/gate`): stdin text in, stderr text out, per call. A
# `contextvars.ContextVar` (per thread / per task, so concurrent requests never share a buffer),
# created on first use so a command hook never imports `contextvars`. None ⇒ real stdin/stderr.
_HOOK_IO = None


def _injected() -> "Optional[dict]":
    return _HOOK_IO.get() if _HOOK_IO is not None else None


def _err(text: str) -> None:
    """The gate hooks' stderr — the message Claude Code shows on a block (exit 2)."""
    io = _injected()
    if io is not None:
        io["err"].append(text)
    else:
        sys.stderr.write(text)


def _read_stdin_bounded(timeout: float = _STDIN_READ_TIMEOUT_SECS) -> Optional[str]:
    """Read all of stdin, but never block a session longer than ``timeout`` seconds.

    A bare ``sys.stdin.read()`` blocks FOREVER when stdin is an OPEN non-TTY pipe with no
    writer (a mis-wired launcher, a shell that leaves the write end open) — violating the
    "hooks never block a session" contract. So we read on a daemon thread and give up after
    ``timeout``, returning None (the caller falls back to its own default). Reuses the
    ``mcp_admin.handshake`` bounded-reader pattern: cross-platform (no SIGALRM), Windows-safe.
    Never raises. The normal path (payload + EOF) returns instantly, well inside ``timeout``."""
    if _STDIN_MEMO:
        return _STDIN_MEMO[0]          # `main` already read it once for its fast path
    io = _injected()
    if io is not None:
        return io["stdin"]
    stream = sys.stdin
    if stream is None:
        return None
    import threading
    holder: dict = {}

    def _reader() -> None:
        try:
            holder["raw"] = stream.read()
        except Exception:            # torn-down/errored stream → treat as no input
            holder["raw"] = None

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None                  # timed out: an open pipe with no writer — don't block
    return holder.get("raw")


def _bounded(work: "Callable[[], Any]", timeout: float) -> Any:
    """Run `work` on a daemon thread and return its result, or None if it outlives `timeout`.
    Never raises: an exception inside `work` also comes back as None, which every caller already
    treats as "nothing to say" — so there is exactly ONE no-result answer and no caller has to
    tell two flavours of nothing apart.

    The SAME pattern `_read_stdin_bounded` uses, and for the same reason — cross-platform, no
    SIGALRM, Windows-safe (the platform issue #43 was reported on). Deliberately RESULT-RETURNING
    rather than side-effecting: the caller acts on a result only after the join, so a LATE worker
    cannot emit, cannot record, and cannot be observed at all.

    `is_alive()` IS the load-bearing line, not a formality. A worker can populate its result in
    the instant between the join expiring and this check — a COMPLETE pack that arrived LATE, past
    the budget. Reading the holder unconditionally would hand that back and emit it, which is
    precisely what the budget exists to prevent, so a still-running worker is refused on the fact
    that it is still running rather than on whether it happens to have left something behind.

    The abandoned thread is left running as a daemon and dies with the process. Safe here because
    the work is READ-ONLY (`build_injection` runs the ranked recall with `stamp=False`, precisely
    so a per-prompt hook moves no durable state) — the worst a late worker can do is finish a
    computation nobody reads."""
    import threading
    holder: dict = {}

    def _run() -> None:
        try:
            holder["value"] = work()
        except Exception:            # noqa: BLE001 — the caller's own fail-open floor applies
            holder["value"] = None

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None                  # over budget — refuse it for RUNNING, not for being empty
    return holder.get("value")


def _parse_flags(argv: Optional[List[str]], description: str, flags: "List[tuple]",
                 known: bool = True) -> Any:
    """The hook's flags, parsed — with `argparse` imported only when there IS a flag to parse.

    HOOK-BUDGET (0.0.25 Lane HB): Claude Code launches every hook with NO flags (the payload
    arrives on stdin), and `argparse` costs ~5 ms of a 50 ms observability budget to import and
    build. So an EMPTY argv answers with each flag's default directly; anything else — including
    `argv=None`, which means "read `sys.argv`" — goes through the real parser, unchanged. `flags`
    is the ONE declaration both branches read: `(option, add_argument kwargs)`. `known` picks
    `parse_known_args` (unknown flags ignored) over `parse_args`."""
    if argv is not None and not argv:
        from types import SimpleNamespace
        values = {}
        for option, kw in flags:
            dest = kw.get("dest") or option.lstrip("-").replace("-", "_")
            values[dest] = kw.get("default", False if kw.get("action") == "store_true" else None)
        return SimpleNamespace(**values)
    import argparse
    parser = argparse.ArgumentParser(description=description)
    for option, kw in flags:
        parser.add_argument(option, **kw)
    if known:
        return parser.parse_known_args(argv)[0]
    return parser.parse_args(argv)


def _mokata_root(start: str) -> Optional[str]:
    """`gate_hook.find_mokata_root`, for the hooks that only need the ROOT — without importing
    `gate_hook`, whose run-state machinery (run_resolver, spec_scope, awaiting) costs an
    observability hook ~10 ms it never uses (HOOK-BUDGET, 0.0.25 Lane HB). Both are the same one
    resolver, `repo_identity.resolve_mokata_root`, so they cannot disagree."""
    from .repo_identity import resolve_mokata_root
    return resolve_mokata_root(start).root


def _surely_not_mokata(start: str) -> bool:
    """True only when `repo_identity.resolve_mokata_root(start).root` is PROVABLY None — answered on
    `os.path` alone, with no mokata or `dataclasses` import (HOOK-BUDGET, 0.0.25 Lane HB).

    It is the resolver's own walk for the ONLY case it can prove cheaply, and it says False
    ("ask the real resolver") for everything else:
      1. no ancestor of `start` holds `.mokata/manifest.json` — the resolver's first walk;
      2. the nearest `.git` above `start` is absent or a real DIRECTORY (not a file, not a symlink),
         so `canonical_repo_root` is that directory's realpath and the resolver's second walk, from
         there, finds nothing either. A `.git` FILE (a linked worktree or a submodule, whose main
         checkout the resolver consults) or a symlinked `.git` is never decided here.
    False is always safe — it only means the hook takes its normal path.
    `tests/test_hb_hook_budget.py` pins it to the resolver: True ⇒ the resolver says None, across
    plain dirs, repos, worktrees (manifest committed or not), submodules and symlinked paths."""
    try:
        cur = os.path.abspath(start)
        git_dir = None
        while True:
            if os.path.exists(os.path.join(cur, ".mokata", "manifest.json")):
                return False
            if git_dir is None:
                gp = os.path.join(cur, ".git")
                if os.path.lexists(gp):
                    if os.path.islink(gp) or not os.path.isdir(gp):
                        return False
                    git_dir = gp
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        if git_dir is None:
            return True                  # no git: the resolver's canon is `start` itself
        cur = os.path.dirname(os.path.realpath(git_dir))
        while True:                       # the resolver's second walk, from canonical_repo_root
            if os.path.exists(os.path.join(cur, ".mokata", "manifest.json")):
                return False
            parent = os.path.dirname(cur)
            if parent == cur:
                return True
            cur = parent
    except (OSError, ValueError, TypeError):
        return False


# ======================================================================================
# secret-guard (PreToolUse, sync SECURITY hook — G4 + I1)
# ======================================================================================

def _iter_strings(obj):
    """Yield every string value nested anywhere inside obj (dict/list/scalar)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def _find_path(obj):
    """First path-like string found in a tool_input mapping (for path-based detection)."""
    if isinstance(obj, dict):
        for k in _PATH_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and v:
                return v
        for v in obj.values():
            found = _find_path(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_path(v)
            if found:
                return found
    return None


def _from_envelope(raw):
    """If raw stdin is a PreToolUse JSON envelope, return (content_to_scan, target_path)
    drawn ONLY from tool_input. Return None if it isn't such an envelope (so the caller
    falls back to raw-text scanning). The envelope metadata is never scanned."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or "tool_input" not in data:
        return None
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, (dict, list)):
        return None
    content = "\n".join(_iter_strings(tool_input))   # command / content / edit strings only
    path = _find_path(tool_input)
    return content, path


def secret_guard_main(argv: Optional[List[str]] = None) -> int:
    """Scan the tool's CONTENT (and its target path) for secrets; BLOCK (exit 2) if any
    are found. Sync hooks block only for security; this is the canonical example.

    Input resolution (priority): ``--text`` (+ optional ``--path``); else stdin as a
    PreToolUse JSON envelope (scan only ``tool_input``); else stdin as raw text."""
    args = _parse_flags(argv, "mokata secret guard (sync security hook)", [
        ("--text", dict(default=None, help="content to scan")),
        ("--path", dict(default=None, help="target path being written/sent")),
        ("--send", dict(action="store_true", help="content is leaving the machine")),
    ], known=False)
    from .degrade import FAILURE_ENGINE, note_degraded     # D5: at the top, never in the handler

    # Imported lazily so a half-installed environment degrades to a clean no-op (exit 0)
    # rather than crashing a hook — a missing engine must never block a session/tool call.
    #
    # D5 — but NEVER a SILENT no-op. This is a SECURITY control: if `govern` cannot be imported,
    # every Write/Edit/Bash from here on proceeds UNSCANNED FOR SECRETS, forever, and the user has
    # no way to know. A disabled scanner that says nothing is worse than no scanner at all, because
    # the user believes they are protected. Exit 0 stays (a hook must never wedge the session); the
    # silence does not. Narrowed to ImportError — the class a half-installed engine actually raises
    # (the same catch `session_start_main` already uses for this lazy-import pattern) — so a real
    # BUG in `govern` surfaces as a crash instead of hiding inside the security no-op.
    #
    # A hook is a short-lived process handling ONE tool call, so once-per-process == one warning per
    # unscanned write. That is not spam: it is the correct loudness for a security control that is
    # not running.
    try:
        from .govern import scan
    except ImportError as exc:
        note_degraded("secret-guard", FAILURE_ENGINE,
                      fallback="writes are NOT being scanned for secrets",
                      fix="Reinstall mokata (`pip install -U mokata`)", detail=str(exc))
        return 0

    if args.text is not None:
        text, path = args.text, args.path
    else:
        io = _injected()
        raw = io["stdin"] if io is not None else sys.stdin.read()
        envelope = _from_envelope(raw)
        if envelope is not None:
            text, path = envelope
            path = path or args.path        # explicit --path still honored if given
        else:
            text, path = raw, args.path     # raw-text fallback (non-envelope callers)

    # SECRET-IGNORE — the repo's RECORDED false positives (entropy layer only). Resolved from the
    # target path, then the envelope cwd, then this process's cwd: a hook is launched by the
    # harness, so its own cwd is the least reliable of the three. Every failure mode lands on
    # `store=None`, which suppresses NOTHING — fail-closed, exactly as before the feature existed.
    from .govern.secret_ignore import load_for_scan, render_block
    store = None
    ignore_notice = ""
    root = _mokata_root(os.path.dirname(os.path.abspath(path)) if path else os.getcwd())
    if root is not None:
        store, ignore_notice = load_for_scan(root)

    findings = scan(text=text or "", path=path, for_send=args.send, ignores=store)
    if ignore_notice:
        # A suppression list that cannot be trusted is not a quiet condition: the user believes
        # their recorded false positives are honoured, and they are not.
        _err(ignore_notice + "\n")
    if findings:
        # ONE shared builder for the wording (R3/R4) — the MCP WriteGate renders the SAME text.
        _err(render_block(findings, path=path, root=root) + "\n")
        return BLOCK_EXIT
    return 0


# ======================================================================================
# gate-guard (PreToolUse, sync RUN-STATE GATE hook — SI.1 / A1)
# ======================================================================================
# The lock on the door doc 74 named: mokata's gates all fired INSIDE its MCP write tools, so the
# model could bypass every one of them by using its NATIVE Write/Edit. This hook runs on those
# native tools and blocks (exit 2) a write that violates a run-state gate — enforcement stops being
# advice the model may ignore and becomes an exit code it cannot.
#
# It is the I/O only; `gate_hook.check_write` is the decision (and is unit-tested without a
# subprocess). Sync + exit-2 is sanctioned here: this IS the gate class G4 reserves the hard block
# for. The overriding contract is FAIL-OPEN — a hook that wedges the editor would be uninstalled,
# and an uninstalled gate enforces nothing — so EVERY uncertainty (no mokata repo, no run,
# ambiguous window, unreadable state, a bug in our own code) exits 0.


def gate_guard_main(argv: Optional[List[str]] = None) -> int:
    """Block a native file-mutation tool call that violates a persisted run-state gate — and, ahead
    of that, ANY write to installed or out-of-workspace code (SELF-PROTECT, 0.0.16 stage 3).

    Reads the PreToolUse envelope on stdin (`tool_input` for the target path, `cwd` for the repo,
    `session_id` for the once-per-window notice). Exit 2 + a one-line actionable reason on a
    violation; exit 0 otherwise. Never raises: the outermost `except` is the fail-open floor.

    TWO independent decisions live here, in this order and for a reason:
      1. SELF-PROTECT — keys on the TARGET PATH alone (`selfprotect.check_target`), so it runs
         BEFORE `find_mokata_root` and before every run-state gate. Those gates are positively
         triggered and this hook exits 0 on a non-mokata cwd, which is precisely why a
         `site-packages` edit was never examined: it is never inside a mokata repo, and never
         inside a run. Security class, never overridable.
      2. the SI.1 run-state gates — unchanged, in their existing order, on their existing signals."""
    args = _parse_flags(argv, "mokata run-state gate (sync gate hook)", [
        ("--path", dict(default=None, help="target path (bypasses stdin; for testing)")),
        ("--cwd", dict(default=None, help="repo dir (bypasses stdin; for testing)")),
        ("--command", dict(default=None,
                           help="Bash command (bypasses stdin; for testing the self-protect lane)")),
    ])
    from .degrade import FAILURE_ENGINE, note_degraded     # D5: at the top, never in the handler

    try:
        # Imported lazily (like secret-guard's engine import) so a half-installed environment
        # degrades to a clean no-op instead of crashing a hook. The module's own import surface is
        # SI.2-cheap: stdlib + state + tdd_state — no engine, no govern, no config.
        from .gate_hook import (bash_command, check_write, find_mokata_root, notice_once,
                                target_content, target_path)
        from .selfprotect import (bash_write_targets, check_target, render_refusal,
                                  workspace_root_for)

        cwd, session_id, path = args.cwd or os.getcwd(), "", args.path
        command = args.command
        content = ""
        tool_name, agent_id = "", None
        if args.path is None and args.command is None:
            raw = _read_stdin_bounded(_STDIN_READ_TIMEOUT_SECS)
            payload = {}
            if raw and raw.strip():
                try:
                    loaded = json.loads(raw)
                    payload = loaded if isinstance(loaded, dict) else {}
                except (ValueError, TypeError):
                    payload = {}                  # unparseable envelope -> nothing to gate on
            cwd = args.cwd or payload.get("cwd") or os.getcwd()
            sid = payload.get("session_id")
            session_id = sid if isinstance(sid, str) else ""
            tool_input = payload.get("tool_input")
            path = target_path(tool_input)
            # SI-DEV — the envelope carries the TEXT about to be written, and this hook used to
            # throw it away. It is what lets the scope gate see a deferred feature being added to an
            # otherwise-authorized file (the incident's actual shape). Matched against literal
            # markers only; never interpreted, never sent anywhere.
            content = target_content(tool_input)
            command = bash_command(tool_input)
            tool_name = payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else ""
            # D4 — Claude Code puts `agent_id` on a tool call made INSIDE a subagent.
            agent_id = payload.get("agent_id") if isinstance(payload.get("agent_id"), str) else None
            # RUN-REG (0.0.22 C1) — the MODEL invoking the brainstorm skill is the second way a
            # session asks for a pipeline (a typed `/mokata:brainstorm` is the first, recorded by
            # user-prompt-submit). Record the intent and stop: a Skill call writes no file.
            if payload.get("tool_name") == "Skill":
                _record_skill_intent(cwd, session_id, tool_input)
                return 0

        # ---- 1 · SELF-PROTECT. Target path only, BEFORE find_mokata_root and every run-state
        # gate. `_SOURCE_EXTS` is deliberately NOT consulted: that filter is what keeps a README
        # edit from tripping a methodology gate, and inheriting it here would leave
        # `settings.json` / `hooks.json` / `manifest.json` — the files that switch mokata's own
        # enforcement OFF — outside the block entirely.
        protect_root = workspace_root_for(cwd)
        for target in ([path] if path else []) + bash_write_targets(command or ""):
            # `base_dir=cwd`: a RELATIVE target is relative to the cwd the TOOL acts in (the
            # envelope's), never to this hook process's own cwd — see `resolve_target`.
            verdict = check_target(target, workspace_root=protect_root, base_dir=cwd)
            if verdict.blocked:
                _err(render_refusal(verdict) + "\n")
                return BLOCK_EXIT

        # ---- 1b · the SUBAGENT CONTRACT (HP.S3, 0.0.22 D4). Only a call made inside a subagent,
        # or a call that STARTS one, is judged; everything else passes straight through.
        bash = None
        if command:
            from .bash_lane import analyze
            bash = analyze(command, cwd)          # D5 — bare names included, resolved against cwd
        if agent_id is not None or tool_name in _SPAWN_TOOLS:
            refusal = _subagent_contract_refusal(
                cwd, session_id, agent_id, tool_name, path,
                list(bash.targets) if bash else [],
                (bash.unreadable + bash.mutates) if bash else ())
            if refusal:
                _err(refusal + "\n")
                return BLOCK_EXIT

        # ---- 1b' · MOKATA'S OWN RUN RECORD (0.0.22 verification, D4-forge). The state directory
        # holds the evidence the gates trust — which subagent finished, which test failed, which
        # run a session holds. mokata writes it; an agent tool call never does (no skill asks it
        # to). A Write or a Bash redirect into it is refused in every session.
        refusal = _own_record_refusal(cwd, ([path] if path else []) +
                                      (list(bash.targets) if bash else []), command or "", bash)
        if refusal:
            _err(refusal + "\n")
            return BLOCK_EXIT

        # ---- 1c · the BASH WRITE SIDE DOOR (SP-BLIND + HP.S4's Bash half, 0.0.22 D5). Every write a
        # command makes that mokata can SEE meets the same run-state gates as a native Write to that
        # path; one it cannot see is refused, but only in a session that is in a pipeline.
        if bash is not None and (bash.targets or bash.unreadable):
            refusal = _bash_lane_refusal(cwd, session_id, bash)
            if refusal:
                _err(refusal + "\n")
                return BLOCK_EXIT

        if not path:
            return 0                              # no file target (or no envelope) -> nothing to do

        # ---- 2 · the SI.1 run-state gates. Untouched, including this fail-open floor.
        root = find_mokata_root(cwd)
        if root is None:
            return 0                              # not a mokata project -> zero cost, instant allow

        outcome = check_write(root, path, content=content, session_id=session_id or None)

        if outcome.notice:
            once = notice_once(root, session_id, outcome.notice)
            if once:
                _err(once + "\n")

        if outcome.allowed:
            _snapshot_before(root, path, session_id, cwd)
            return 0
        _record_in_loop_catch(root, outcome, path, session_id)
        _err(f"BLOCKED [{outcome.gate}] {outcome.reason}\n")
        return BLOCK_EXIT
    except Exception as exc:
        # FAIL OPEN. A gate that breaks the harness is worse than a gate that misses one write:
        # the user can always re-run the tool, but they cannot edit through a crashed hook. The
        # `return 0` and the BROAD catch are both correct and both stay: this IS the fail-open
        # floor, and a floor that only catches the failures we predicted is not a floor.
        #
        # D5 — what was wrong was the SILENCE. Every EXPECTED case (no file target, not a mokata
        # repo, an unparseable envelope) already returned 0 explicitly ABOVE, so reaching here means
        # something genuinely unexpected: a bug in `check_write`, a corrupt state file, an
        # ImportError from a half-installed engine. A gate that is persistently failing open
        # enforces NOTHING while the statusline badge and the docs tell the user governance is on —
        # a lie in the proof (P16). It still allows the write; it just says so.
        #
        # SUPPRESSION: `note_degraded` (once per PROCESS), deliberately NOT `gate_hook.notice_once`
        # (once per SESSION), even though the latter's cross-process marker is the tighter squelch:
        #   * its marker is keyed on the session_id ALONE (`.gate_notice__<sid>`), not on the
        #     message — so a gate notice already emitted this session would SILENCE this degrade,
        #     and emitting this degrade would silence a later legitimate gate notice. Re-introducing
        #     the D5 bug to save a repeated line is a bad trade;
        #   * it needs `root` and a non-empty `session_id`, and neither is guaranteed HERE — an
        #     ImportError of `gate_hook` leaves both `notice_once` and `root` unbound, and `--path`
        #     mode never parses an envelope, so `session_id` is "".
        # A hook is a one-tool-call process, so once-per-process == one line per write that went
        # ungated. For a gate enforcing nothing, that is the right loudness.
        note_degraded("gate-guard", FAILURE_ENGINE,
                      fallback="the run-state gate is FAILING OPEN and enforcing nothing",
                      fix="Run `mokata doctor`", detail=str(exc))
        return 0


def _record_in_loop_catch(root: str, outcome: Any, path: str, session_id: str) -> None:
    """SL.S3 (0.0.22 C2) — a block keyed to a run is an IN-LOOP catch for that run: mokata caught
    it before review. Recorded so "caught at review vs in the loop" has a denominator. Silent and
    total: a lost record makes the run's figure smaller, never the write allowed.

    Credited ONLY when this session holds the run (its own bound claim) — 0.0.22 verification D-1:
    a second window with no claim is still policed by the repo's only run (so `/clear` is not a
    way around the gates), but its blocks are not that run's catches and must not inflate them."""
    try:
        rid = getattr(outcome, "run_id", None)
        if not rid or not session_id:
            return
        from .run_intent import CLAIM_BOUND, session_claim
        claim, claimed = session_claim(root, session_id)
        if claim != CLAIM_BOUND or claimed != rid:
            return
        from .progress_events import record_gate_block
        from .repo_paths import name_of
        from .tdd_state import state_dir
        try:
            rel = name_of(path if os.path.isabs(path) else os.path.join(root, path), root)
        except (ValueError, OSError):
            rel = os.path.basename(path)
        record_gate_block(state_dir(root), rid, outcome.gate or "", rel)
    except Exception:  # noqa: BLE001 — the block stands whether or not it was counted
        pass


#: The tools that start a subagent (`subagent_contract.SPAWN_TOOLS`, restated so the hot path does
#: not import the contract module for every write; a test pins the two equal).
_SPAWN_TOOLS = ("Agent", "Task")


GATE_OWN_RECORD = "own-record"


def _own_record_refusal(cwd: str, targets: List[str], command: str = "", bash: Any = None) -> str:
    """BLOCKED when an agent tool call would write, delete or move mokata's run record —
    `.mokata/temp_local/state/`, the evidence the gates decide from — in ANY session. Deleting it
    is the worst case: with no state there is no run, and every run-state gate opens (0.0.22
    verification). Fail open on a fault, like every lane here."""
    try:
        from .gate_hook import find_mokata_root
        from .tdd_state import state_dir
        root = find_mokata_root(cwd)
        if root is None:
            return ""
        from .bash_lane import own_record_hits
        hit = own_record_hits(root, cwd, state_dir(root), targets, command, bash)
        if hit:
            return (f"BLOCKED [{GATE_OWN_RECORD}] {hit} — that is mokata's run record, the "
                    f"evidence the gates decide from. mokata writes it; a tool call may not change "
                    f"or remove it. Use the mokata command for what you meant (e.g. `mokata gate "
                    f"override`, `mokata uninstall`).")
        return ""
    except Exception:  # noqa: BLE001
        return ""


def _bash_lane_refusal(cwd: str, session_id: str, bash: Any) -> str:
    """The BLOCKED line when a Bash command's writes break a run-state gate, or when — in a session
    that is in a pipeline — mokata cannot tell what it writes. "" otherwise. Fail open on a fault,
    like every lane here: the floor is the gate's, not this helper's."""
    try:
        from .gate_hook import check_write, find_mokata_root, notice_once
        root = find_mokata_root(cwd)
        if root is None:
            return ""
        if bash.unreadable and session_id:
            from .run_intent import CLAIM_NONE, session_claim
            claim, rid = session_claim(root, session_id)
            if claim != CLAIM_NONE:
                from .bash_lane import GATE_UNREADABLE, refusal
                if rid:
                    try:
                        from .progress_events import record_gate_block
                        from .tdd_state import state_dir
                        record_gate_block(state_dir(root), rid, GATE_UNREADABLE, "(bash)")
                    except Exception:  # noqa: BLE001 — the block stands whether or not counted
                        pass
                return refusal(bash.unreadable)
        for target in bash.targets:
            outcome = check_write(root, target, session_id=session_id or None)
            if outcome.notice:
                once = notice_once(root, session_id, outcome.notice)
                if once:
                    _err(once + "\n")
            if not outcome.allowed:
                _record_in_loop_catch(root, outcome, target, session_id)
                return f"BLOCKED [{outcome.gate}] {outcome.reason}"
        return ""
    except Exception:  # noqa: BLE001
        return ""


def _subagent_contract_refusal(cwd: str, session_id: str, agent_id: Optional[str], tool_name: str,
                               path: Optional[str], bash_targets: List[str],
                               bash_unreadable: Any = ()) -> str:
    """The BLOCKED line when this call breaks its subagent's contract, else "". Ledgers the refusal.
    Fail open on any fault: an unreadable contract is an unpoliced subagent, never a broken tool."""
    try:
        from .gate_hook import find_mokata_root
        from .subagent_contract import check_tool, ledger_unhonored, read_contract
        root = find_mokata_root(cwd)
        if root is None:
            return ""
        verdict = check_tool(root, agent_id, tool_name, path, bash_targets,
                             session_id=session_id or None, bash_unreadable=tuple(bash_unreadable))
        if verdict.allowed:
            return ""
        contract = read_contract(root, agent_id) if agent_id else None
        ledger_unhonored(root, contract, verdict.gate, tool=tool_name,
                         target=path or (bash_targets[0] if bash_targets else ""))
        return f"BLOCKED [{verdict.gate}] {verdict.reason}"
    except Exception:  # noqa: BLE001
        return ""


def _record_skill_intent(cwd: str, session_id: str, tool_input: Any) -> None:
    """A `Skill` call that starts a registering pipeline records this session's intent. Silent and
    total: an unrecorded intent leaves the gate exactly as it was before this stage (fail open)."""
    try:
        from .gate_hook import find_mokata_root
        from .run_intent import pipeline_in_skill_call, record_intent
        pipeline = pipeline_in_skill_call(tool_input)
        root = find_mokata_root(cwd) if pipeline else None
        if root and session_id:
            record_intent(root, session_id, "skill:" + pipeline)
    except Exception:  # noqa: BLE001
        pass


# ======================================================================================
# the PreToolUse verdict as a function (U8 — the http gate's decision IS the command gates')
# ======================================================================================

def pretooluse_verdict(payload: dict) -> "tuple":
    """`(exit code, message)` for ONE Claude Code PreToolUse payload — running `secret_guard_main`
    and `gate_guard_main` exactly as the two command hooks would, in-process, each only when its
    `mokata setup` matcher covers the tool (`harness_setup.HOOK_PRETOOL_MATCHER` /
    `HOOK_GATE_MATCHER`). Both run when both match — Claude Code runs matching hooks in parallel
    and either one's exit 2 blocks — so their side effects (notices, recorded intent, in-loop
    catches) are the command route's too. 2 when either blocked (the message is what each blocking
    hook wrote), else 0. Never touches the process's real stdin/stderr: the payload goes in and the
    messages come out through `_HOOK_IO`, per call."""
    global _HOOK_IO
    import contextvars
    import re
    from .harness_setup import HOOK_GATE_MATCHER, HOOK_PRETOOL_MATCHER
    if _HOOK_IO is None:
        _HOOK_IO = contextvars.ContextVar("mokata_hook_io", default=None)
    tool = payload.get("tool_name")
    tool = tool if isinstance(tool, str) else ""
    raw = json.dumps(payload)
    code, blocks = 0, []
    for matcher, run in ((HOOK_PRETOOL_MATCHER, secret_guard_main),
                         (HOOK_GATE_MATCHER, gate_guard_main)):
        if not re.fullmatch(matcher, tool):
            continue
        io = {"stdin": raw, "err": []}
        token = _HOOK_IO.set(io)
        try:
            rc = run([])
        finally:
            _HOOK_IO.reset(token)
        if rc == BLOCK_EXIT:
            code = BLOCK_EXIT
            blocks.append("".join(io["err"]).strip())
    return code, "\n".join(b for b in blocks if b)


# ======================================================================================
# pre-compact (PreCompact — H-1b, 0.0.22 D1)
# ======================================================================================

def pre_compact_main(argv: Optional[List[str]] = None) -> int:
    """Persist THIS session's run position before Claude Code summarises the conversation, so the
    re-anchor after compaction can say whether anything moved (H-1b). ALWAYS exits 0: a PreCompact
    hook that blocks or crashes would cost the human their compaction."""
    try:
        raw = _read_stdin_bounded(_STDIN_READ_TIMEOUT_SECS)
        payload: dict = {}
        if raw and raw.strip():
            try:
                loaded = json.loads(raw)
                payload = loaded if isinstance(loaded, dict) else {}
            except (ValueError, TypeError):
                payload = {}
        sid = payload.get("session_id")
        if not isinstance(sid, str) or not sid.strip():
            return 0
        root = _mokata_root(payload.get("cwd") or os.getcwd())
        if root:
            from .reanchor import write_anchor
            write_anchor(root, sid.strip())
            # H-1b's episodic half (0.0.22 D6) — draft this session's note before the summary.
            from .harvest import harvest
            harvest(root, sid.strip(), "pre-compact")
        return 0
    except Exception:  # noqa: BLE001 — never cost the human their compaction
        return 0


# ======================================================================================
# stop / session-end (Stop / SessionEnd — H-1c, 0.0.22 D6)
# ======================================================================================

def _harvest_main(event: str) -> int:
    """Rewrite this session's DRAFT note from the record. ALWAYS exits 0 and prints NOTHING: exit 2
    on Stop would send Claude back to work, and stdout on Stop is a decision channel — a harvest
    must never be either. Nothing here writes memory; a person accepts a draft at the CLI."""
    try:
        payload = _hook_payload()
        sid = payload.get("session_id")
        if not isinstance(sid, str) or not sid.strip():
            return 0
        if payload.get("stop_hook_active"):
            return 0                       # another Stop hook sent Claude back; not a real end
        root = _mokata_root(payload.get("cwd") or os.getcwd())
        if root is None:
            return 0
        from .harvest import harvest
        last = payload.get("last_assistant_message")
        harvest(root, sid.strip(), event, last if isinstance(last, str) else None)
        return 0
    except Exception:  # noqa: BLE001
        return 0


def stop_main(argv: Optional[List[str]] = None) -> int:
    return _harvest_main("stop")


def session_end_main(argv: Optional[List[str]] = None) -> int:
    return _harvest_main("session-end")


# ======================================================================================
# subagent-start / subagent-stop (SubagentStart / SubagentStop — HP.S3 + H-3, 0.0.22 D4)
# ======================================================================================

def _hook_payload() -> dict:
    raw = _read_stdin_bounded(_STDIN_READ_TIMEOUT_SECS)
    if not raw or not raw.strip():
        return {}
    try:
        loaded = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def subagent_start_main(argv: Optional[List[str]] = None) -> int:
    """Record this subagent's contract and tell the subagent what it is. Context only — SubagentStart
    cannot block — and ALWAYS exits 0: a session in no pipeline sees nothing at all."""
    try:
        payload = _hook_payload()
        sid = payload.get("session_id")
        if not isinstance(sid, str) or not sid.strip():
            return 0
        root = _mokata_root(payload.get("cwd") or os.getcwd())
        if root is None:
            return 0
        from .subagent_contract import contract_context, start_contract
        contract = start_contract(root, sid.strip(), payload.get("agent_id"),
                                  payload.get("agent_type"))
        if contract:
            sys.stdout.write(json.dumps({"hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": contract_context(contract)}}) + "\n")
        return 0
    except Exception:  # noqa: BLE001 — never cost the human their subagent
        return 0


def subagent_stop_main(argv: Optional[List[str]] = None) -> int:
    """Read the subagent's HandbackReport; send it back ONCE when it has none (`decision: block`),
    and record how it finished. ALWAYS exits 0 — the JSON decision is the only way it blocks, and a
    crash here must never hold a subagent open."""
    try:
        payload = _hook_payload()
        root = _mokata_root(payload.get("cwd") or os.getcwd())
        if root is None:
            return 0
        from .subagent_contract import finish_contract
        verdict = finish_contract(root, payload)
        if verdict.block:
            sys.stdout.write(json.dumps({"decision": "block", "reason": verdict.reason}) + "\n")
        return 0
    except Exception:  # noqa: BLE001
        return 0


# ======================================================================================
# run-bind (PostToolUse on mokata's `session_save` — RUN-REG structural, 0.0.22 C1)
# ======================================================================================

def run_bind_main(argv: Optional[List[str]] = None) -> int:
    """Bind this Claude Code session to the run ITS OWN `session_save(register=true)` registered.

    The MCP process cannot see Claude Code's `session_id` (harness gap #25642); this hook sees both
    the session id and the tool's response, so it is the one place the two identities meet without
    a guess — which is what lets parallel runs each be policed by their own run. ALWAYS exits 0:
    the tool already ran, and a missed binding only leaves the gate where it would have been."""
    try:
        raw = _read_stdin_bounded(_STDIN_READ_TIMEOUT_SECS)
        payload: dict = {}
        if raw and raw.strip():
            try:
                loaded = json.loads(raw)
                payload = loaded if isinstance(loaded, dict) else {}
            except (ValueError, TypeError):
                payload = {}
        sid = payload.get("session_id")
        if not isinstance(sid, str) or not sid.strip():
            return 0
        from .run_intent import bind_registered, registered_run
        rid = registered_run(payload.get("tool_name"), payload.get("tool_response"))
        if not rid:
            return 0
        root = _mokata_root(payload.get("cwd") or os.getcwd())
        if root:
            bind_registered(root, sid.strip(), rid)
        return 0
    except Exception:  # noqa: BLE001 — a PostToolUse hook never fails the call it follows
        return 0


# ======================================================================================
# dirty-track (PostToolUse, ASYNC OBSERVABILITY hook — GR.S4)
# ======================================================================================

def _calibrate_from_transcript(root: str, payload: dict, session_id: Optional[str]) -> None:
    """H-2 — fold the transcript's new assistant messages into `memory_stats.calibration` and
    log ONE ledger record per model with a REAL `actual`, so `doctor`'s `calibration-drift`
    finding can fire outside a test for the first time since R11 shipped it.

    Guarded end to end and silent: this is the async observability lane, and `observe` already
    returns a reason rather than raising. ⚠ It REFUSES on a repo that is not a mokata repo —
    writing `memory_stats` would otherwise create `.mokata/` in a tree the user never
    initialized, which is the one thing a hook on an uninitialized repo must not do (see
    `session_start_main`'s WT-ROOT comment for the same rule arriving from the other side)."""
    try:
        transcript_path = payload.get("transcript_path")
        if not isinstance(transcript_path, str) or not transcript_path.strip():
            return
        from .repo_identity import is_initialized   # not `config`: HOOK-BUDGET
        if not is_initialized(root):
            return
        from .govern.calibration import observe
        observe(root, transcript_path=transcript_path.strip(), session_id=session_id or "")
    except Exception:  # noqa: BLE001 — the async lane NEVER fails a tool call
        pass


def dirty_track_main(argv: Optional[List[str]] = None) -> int:
    """Append the just-written path to the session graph dirty-set (GR.S4).

    This is the ASYNC OBSERVABILITY lane (doc 85): the sync PreToolUse hooks are SECURITY ONLY
    and are the only hooks that block (exit 2); this one NEVER blocks and NEVER fails an action —
    it always exits 0, and every failure is swallowed. It records which files changed so the
    read-time freshness contract can reconcile the code graph BEFORE the next query answers,
    with no watcher and no daemon.

    Input: the PostToolUse JSON envelope on stdin (`tool_input` + `cwd` + `session_id`), or the
    `--path`/`--cwd`/`--session-id` argv escape (for tests / manual wiring)."""
    try:
        args = _parse_flags(argv, "mokata graph dirty-track (async observability hook)", [
            ("--path", dict(default=None)),
            ("--cwd", dict(default=None)),
            ("--session-id", dict(default=None)),
        ])

        payload: dict = {}
        if args.path is None:
            raw = _read_stdin_bounded(_STDIN_READ_TIMEOUT_SECS)
            if raw and raw.strip():
                try:
                    loaded = json.loads(raw)
                    payload = loaded if isinstance(loaded, dict) else {}
                except (ValueError, TypeError):
                    payload = {}

        cwd = args.cwd or payload.get("cwd") or os.getcwd()
        sid = args.session_id or payload.get("session_id")
        session_id = sid if isinstance(sid, str) and sid.strip() else None

        paths: List[str] = []
        if args.path:
            paths = [args.path]
        else:
            tool_input = payload.get("tool_input")
            from .gate_hook import target_path
            p = target_path(tool_input)
            if p:
                paths = [p]

        from .repo_identity import find_project_root   # not `config`: HOOK-BUDGET
        root = find_project_root(cwd)

        # H-2 — close R11's calibration loop while we are already in this envelope. EVERY hook
        # payload carries `transcript_path`, and that file is the only place a text mokata can
        # read and a real token count the harness reports line up; `govern.calibration` reads it
        # incrementally behind `adapters.transcript` (doc 85 §7l: read-only, adapter-wrapped,
        # hooks primary) and never raises. It runs BEFORE the dirty-set block and outside its
        # `not paths` exit on purpose — a PostToolUse call with nothing to mark is still a turn
        # whose output is measurable, and tying the measurement to the dirty-set's own early
        # return would have made the sample set a side effect of someone else's guard.
        _calibrate_from_transcript(root, payload, session_id)

        if not paths:
            return 0                       # nothing to record — still a clean success

        from .knowledge.freshness import mark_dirty
        from .repo_paths import name_of
        # The dirty set is a serialized list of NAMES: `freshness`'s cold walk fills the same
        # channel with `name_of(...)` and the two are matched against each other and against the
        # knowledge index's keys. This loop built them with a bare `relpath` until the invariant
        # landed, so on Windows the hook wrote `src\a.py` into a set the walker wrote `src/a.py`
        # into — the same half-moved pair as the eleven, in a lane too quiet to fail loudly.
        rels = []
        for p in paths:
            ab = p if os.path.isabs(p) else os.path.join(cwd, p)
            try:
                rels.append(name_of(ab, root))
            except (ValueError, OSError):
                # DROPPED, not passed through raw. A path that cannot be expressed relative to
                # the root (a different drive on Windows) has no NAME, and the old fallback put
                # the caller's own string — an absolute native path — into a set of names, where
                # it could never match anything and would be re-serialized as one. Nothing is
                # lost that could have been used; the async lane stays silent either way.
                continue
        mark_dirty(root, rels, session_id=session_id)
        _record_working_set(root, session_id, rels)
        _snapshot_after(root, session_id, rels)
        return 0
    except Exception:  # noqa: BLE001 — the async lane NEVER fails a tool call
        return 0


def _session_run(root: str, session_id: Optional[str]) -> Optional[str]:
    from .run_intent import CLAIM_BOUND, session_claim
    claim, rid = session_claim(root, session_id)
    return rid if claim == CLAIM_BOUND else None


def _snapshot_before(root: str, path: str, session_id: str, cwd: str) -> None:
    """E2 — the content BEFORE this session's first write to `path`, for the review window's diff at
    any point in time. Only for an ALLOWED write (a blocked one changes nothing) and only with a
    session id. Silent and total: a snapshot that cannot be taken never touches the write."""
    try:
        if not session_id:
            return
        from .ide.snapshots import capture_before_first_write
        capture_before_first_write(root, path, session_id, _session_run(root, session_id), cwd)
    except Exception:  # noqa: BLE001
        pass


def _snapshot_after(root: Optional[str], session_id: Optional[str], rels: List[str]) -> None:
    """E2 — the content AFTER each native write. The async observability lane: never fails."""
    try:
        if not root or not rels:
            return
        from .ide.snapshots import AFTER, capture
        rid = _session_run(root, session_id) if session_id else None
        for rel in rels:
            capture(root, rel, AFTER, session_id, rid)
    except Exception:  # noqa: BLE001
        pass


def _record_working_set(root: Optional[str], session_id: Optional[str], rels: List[str]) -> None:
    """HP.S2 (0.0.22 D2) — the written paths join the WorkingSet of the run THIS session is bound
    to (C1's claim — never the repo's "active" run, so parallel runs keep their own files). A
    session with no claim records nothing. Silent: this is the async observability lane."""
    try:
        if not root or not session_id or not rels:
            return
        from .run_intent import CLAIM_BOUND, session_claim
        claim, rid = session_claim(root, session_id)
        if claim != CLAIM_BOUND or not rid:
            return
        from .working_set import record_touched
        for rel in rels:
            record_touched(root, rid, rel)
    except Exception:  # noqa: BLE001 — never fails the tool call it follows
        pass


# ======================================================================================
# session-start (SessionStart, async/observability hook — A4 bootstrap + Stage 23 offer)
# ======================================================================================

def _read_cwd_from_stdin() -> str:
    """Claude Code passes a JSON payload on stdin; honour its cwd if present."""
    if sys.stdin is None or sys.stdin.isatty():
        return os.getcwd()
    raw = _read_stdin_bounded(_STDIN_READ_TIMEOUT_SECS)
    if not raw or not raw.strip():
        return os.getcwd()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return os.getcwd()
    return payload.get("cwd") or os.getcwd()


def _payload_from_stdin() -> dict:
    """The SessionStart JSON payload as a dict, or `{}` (TTY / EOF / unparseable / not a mapping).
    Reads stdin exactly ONCE — SessionStart needs `cwd` AND (B-BADGE) `session_id` + `source`, and
    stdin can only be consumed once. Same bounded, never-blocks guards as `_read_cwd_from_stdin`."""
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    raw = _read_stdin_bounded(_STDIN_READ_TIMEOUT_SECS)
    if not raw or not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _emit(context: str, event: str = SESSION_START_EVENT) -> None:
    """Emit ONE `additionalContext` payload for `event`.

    The event name was hard-coded to `SessionStart` while there was exactly one context-injecting
    hook. H-1a adds the second (`UserPromptSubmit`), and Claude Code keys the payload on
    `hookEventName` — an emit stamped with the wrong event is silently ignored, so the parameter is
    load-bearing, not cosmetic. It stays a DEFAULT rather than a required argument so every
    existing SessionStart call site is byte-identical.

    ONE channel, deliberately (doc 84's "no second competing channel"): both injection surfaces go
    through this function, so the budget accounting has a single place to live."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        }
    }))


def _record_plugin_root(plugin_root: Optional[str]) -> None:
    """Cache the plugin root so the /mokata:init command can find the bundled engine.

    Unlike the old in-tree hook, this module lives inside the installed package, so its
    ``__file__`` is NOT the plugin root — the root is passed explicitly (the plugin
    ``hooks.json`` forwards ``${CLAUDE_PLUGIN_ROOT}``; ``mokata setup`` forwards the clone
    root) or read from the ``CLAUDE_PLUGIN_ROOT`` env. Never raises."""
    try:
        from .plugin_cache import record_plugin_root
        root = plugin_root or os.environ.get("CLAUDE_PLUGIN_ROOT")
        if root:
            record_plugin_root(root)
    except Exception:
        # D5 — broad + silent ON PURPOSE: this is a best-effort CACHE of a hint. Nothing degrades
        # when it fails (`/mokata:init` re-derives the root), so there is no fallback to announce
        # and a notice here would be noise on a SessionStart that is working perfectly.
        pass


def _harvest_waiting(root: str) -> Optional[str]:
    """H-1c (0.0.22 D6) — one line when session notes are waiting for review; inside the budget."""
    try:
        from .harvest import briefing_line
        return briefing_line(root)
    except Exception:  # noqa: BLE001 — a briefing line is never worth a failed SessionStart
        return None


def _intentions_due(surface: Any) -> Optional[str]:
    """PM.S1 (0.0.23 D15) — "N intentions due" (and any UNREADABLE ones), inside the budget."""
    try:
        from .memory import MemoryStore
        from .memory.intentions import briefing_line as _line
        return _line(MemoryStore.from_surface(surface), root=getattr(surface, "root", ""))
    except Exception as exc:  # noqa: BLE001 — a briefing line is never worth a failed SessionStart
        # LOUD, not silent: a reminder the user is counting on must not vanish without a word. The
        # briefing still starts; it says the check did not run, and the degrade is classed.
        try:
            from .degrade import FAILURE_LOCAL_IO, note_degraded
            note_degraded("memory-intentions", FAILURE_LOCAL_IO, detail=str(exc),
                          fallback="due intentions were NOT checked this session",
                          fix="run `mokata memory --kind intention` to see them; `mokata doctor`")
        except Exception:  # noqa: BLE001
            pass
        return ("mokata: intentions could NOT be checked this session — run "
                "`mokata memory --kind intention`.")


def _mcp_reachability_warning(root: str) -> Optional[str]:
    """One-line SessionStart warning when mokata's MCP server is registered but its command
    can't be resolved. Fast local lookup (no subprocess); None (silent) on reachable/absent
    or any error — never blocks or breaks the session."""
    try:
        from .mcp_admin import unreachable_registration
        reg = unreachable_registration(root=root)
        if reg is None:
            return None
        return (f"⚠ mokata-mcp: registered but its command isn't reachable ({reg.command}) — "
                "run `mokata mcp install` to repair, then restart Claude Code.")
    except Exception:
        return None


def _mode_dependencies_missing(root: str, via_plugin: bool = False) -> Optional[str]:
    """0.0.25 I-04 — check on miss. Claude Code has no plugin install hook, so the plugin route
    reaches the CLI route's end state by being TOLD, once per session, the one command that gets
    there. Policy lives in `adoption_modes.first_session_check`; this only renders it. Never
    installs (P2 / DB.S4), never raises: a check that cannot run says nothing."""
    try:
        from .adoption_modes import first_session_check
        return first_session_check(root, via_plugin=via_plugin).line
    except (ImportError, OSError, ValueError, KeyError):
        return None


def _new_worktree_notice(surface) -> Optional[str]:
    """WT.S3 FR-WT-4 — the one-line "new git worktree(s) since the last session" notice, or None.
    Zero subprocess (git's admin files only); `new_worktree_notice` never raises."""
    from .worktree_manage import new_worktree_notice
    return new_worktree_notice(surface)


def session_start_main(argv: Optional[List[str]] = None) -> int:
    """Inject mokata's sub-2k-token bootstrap (A4). Async/observability hook: only adds
    context and ALWAYS exits 0 — a broken config degrades to a one-line note, never blocks
    the session."""
    args = _parse_flags(argv, "mokata SessionStart briefing hook", [
        ("--plugin-root", dict(default=None,
                               help="the plugin/clone root holding the bundled engine")),
        (VIA_PLUGIN_FLAG, dict(dest="via_plugin", action="store_true",
                               help="served by the plugin's hooks.json (I-04: the route decides "
                                    "the check-on-miss wording)")),
    ])

    # B-BADGE — one stdin read yields cwd AND the session identity (`session_id` + `source`) the
    # badge binding needs; stdin can only be consumed once.
    payload = _payload_from_stdin()
    cwd = payload.get("cwd") or os.getcwd()
    _sid = payload.get("session_id")
    session_id = _sid if isinstance(_sid, str) and _sid.strip() else None
    _src = payload.get("source")
    source = _src if isinstance(_src, str) and _src.strip() else None
    try:
        from .bootstrap import build_bootstrap, build_setup_offer
        from .config import ConfigError, Surface, find_project_root
    except ImportError:
        # mokata engine not available — say nothing, break nothing.
        return 0

    _record_plugin_root(args.plugin_root)

    # Key off the REAL project root so an already-initialized repo is recognized from a
    # subdirectory and never re-offered init (Stage 23 — the "asks every time" bug).
    root = find_project_root(cwd)

    if not Surface.is_initialized(root):
        # WT-ROOT — the silent no-op goes, and THIS is where it did the most damage. A linked
        # worktree of a repo that has a `.mokata/` but no readable manifest lands here, and the
        # setup offer below is the single worst thing to say to it: `mokata init` run inside a
        # worktree is precisely what forks the memory store and the audit ledger. A broken mokata
        # repo must not be greeted in the voice of a directory that was never one.
        #
        # SessionStart is also the one surface where "say it once" is honestly once — the gate hook
        # is a fresh process per tool call and would repeat this forever, so it stays silent.
        from .repo_identity import resolve_mokata_root
        resolution = resolve_mokata_root(cwd)
        if resolution.unresolved_worktree:
            _emit("mokata: this is a linked git WORKTREE of a repo whose `.mokata/` has no readable "
                  "manifest.json — a BROKEN mokata repo, not an uninitialized one. Fix it in the "
                  "main checkout. Do NOT run `mokata init` here: it would fork this repo's memory "
                  "store and audit ledger into a second, empty copy.")
            return 0
        # Proactive, one-time offer. Disappears for good once .mokata/ exists.
        _emit(build_setup_offer().text)
        return 0

    # SESSION-BIND-BY-ANCESTOR (0.0.23 F1) — tell the MCP server (a sibling descendant of this
    # Claude process, which can never see `session_id` — #25642) which session this process now
    # serves. On `resume` this is what re-points a process whose session `/resume` just switched.
    _record_session_ancestry(root, session_id)

    try:
        surface = Surface.load(root)
    except ConfigError:
        _emit(build_setup_offer().text)
        return 0
    except Exception as exc:  # never let a config problem break a session
        _emit(f"mokata: bootstrap skipped ({exc}).")
        return 0

    # H-8 (0.0.22 D3) — the re-anchor block and the MCP reachability warning are built FIRST and
    # handed to `build_bootstrap`, so they are counted inside the ONE briefing budget instead of
    # being appended after it (a second, unmetered channel).
    reanchor = None
    try:
        from .reanchor import build_reanchor
        reanchor = build_reanchor(root, session_id, source)
    except Exception:  # noqa: BLE001 — the briefing must start even if the re-anchor cannot
        reanchor = None
    footer = [f for f in (_mcp_reachability_warning(root), _mode_dependencies_missing(root, args.via_plugin),
                          _harvest_waiting(root), _intentions_due(surface),
                          _new_worktree_notice(surface)) if f]
    briefing = build_bootstrap(surface, reanchor=reanchor,
                               footer="\n".join(footer) if footer else None)
    context = briefing.text
    # R11 — log the briefing's tokenizer-free chars/4 estimate so its safety margin is MEASURED.
    # Claude Code reports no real token count at inject time, so we log the ESTIMATE alone and
    # wire the comparison point for when an actual arrives (never fabricate one). Fully guarded:
    # observability that never blocks, slows, or raises in the SessionStart path.
    try:
        from .govern import log_bootstrap_calibration
        log_bootstrap_calibration(surface, briefing.token_estimate)
    except Exception:
        pass
    # Stage 3b.3 — minimal MCP reachability warning. If mokata's MCP server is registered but
    # its command can't be resolved (a broken auto-start), append ONE warning line + the fix.
    # A fast LOCAL lookup only (no handshake subprocess): SessionStart is async/observability
    # and must never block or slow the session. Degrade-clean: any doubt → say nothing.
    _emit(context)

    # HP.S5 (0.0.23 E1) — record this session's prefix fingerprint (its baseline for the per-turn
    # comparison, and a data point for PIPE-TOKEN-AUDIT). After the emit: it never touches the
    # briefing. `observe_prefix` never raises.
    from .govern.cache import observe_prefix
    observe_prefix(surface, root, session_id, event="session-start")

    # Stage 60 — advance the "since last session" baseline at the session boundary. The briefing
    # above already DERIVED its diff against the OLD snapshot (read-only); now capture the new
    # baseline so the next session compares against this one. Guarded: a transient write to
    # gitignored temp_local/, read-only on the governed state, never raises, never blocks.
    try:
        from .visibility import capture_session_snapshot
        capture_session_snapshot(surface)
    except Exception:
        pass

    # B-BADGE — bind THIS Claude Code session to its single live run so the statusline badge is
    # session-aware (a cleared session never wears a dead run's strip; a resumed one re-attaches).
    # SessionStart is the writer because the MCP process that REGISTERS the run cannot learn Claude
    # Code's `session_id` (harness gap #25642) — see `run_resolver.maybe_bind_on_session_start`, which
    # binds only on source=startup/resume with exactly one live run (never on `clear`). Run-state
    # class, ungated, degrade-clean: on any failure the badge falls open to live-narrowing.
    try:
        from .run_resolver import maybe_bind_on_session_start
        maybe_bind_on_session_start(root, session_id, source)
    except Exception:
        pass
    return 0


# ======================================================================================
# user-prompt-submit (UserPromptSubmit, async CONTEXT-INJECTION hook — H-1a)
# ======================================================================================
# H-1a: memory stops being a once-per-session briefing and becomes per-turn inbuilt RAG. This
# hook fires on every prompt the human submits, and returns a small `additionalContext` pack of
# the rules + the recalled items relevant to THIS turn.
#
# Its class is the SessionStart class, not the PreToolUse one, and every line of it follows from
# that. It is ASYNC/OBSERVABILITY: it only ADDS context, so it must ALWAYS exit 0.
#
#   * FAIL-OPEN, absolutely. Exit 2 on a UserPromptSubmit hook does not block a tool call — it
#     EATS THE HUMAN'S TURN. There is no failure here worth that, so every arm (stdin parse,
#     Surface load, store construction, recall, budget cap) returns 0 with an empty stdout and an
#     unchanged prompt. `BLOCK_EXIT` is unreachable from this subcommand by construction and is
#     pinned as such.
#   * NON-COUNTING + SCOPED reads only (H-1a C1). It calls `jit_recall` + `always_on_lines`,
#     never `recall_relevant` — which STAMPS the hits it returns (`record_usage`) and would make
#     a per-turn injection a durable write on every turn. `recall_relevant(stamp=False)` is the
#     deliberate future route to the wider tiered retrieval (semantic + graph + edge expansion);
#     v1 is the lexical floor alone, so the injection stays zero-dependency and deterministic.
#   * ANCHOR-FREE (items only). Wiring code anchors into the injection is H-6's job; inheriting
#     that coupling here would create it by accident rather than by decision.


_IDE_PROMPT = re.compile(r"^\s*/(?:mokata:)?review-window(?=\s|$)")


def _ide_for_prompt(root: str, prompt: str, session_id: Optional[str]) -> Optional[str]:
    """E2 — the context line for a `/mokata:review-window` prompt (after opening / re-focusing), else None.
    A failure is SAID (the command template falls back to `mokata ide --no-browser`)."""
    if not _IDE_PROMPT.match(prompt or ""):
        return None
    try:
        from .ide.launch import open_or_focus
        return open_or_focus(root, session_id).line()
    except ImportError:
        from .ide.server import INSTALL_LINE
        return "mokata ide could not start — " + INSTALL_LINE
    except Exception as exc:  # noqa: BLE001 — a window that will not open is said, never raised
        return f"mokata ide could not start ({type(exc).__name__}: {str(exc)[:160]})"


def user_prompt_submit_main(argv: Optional[List[str]] = None) -> int:
    """Inject the per-turn recall pack (H-1a). ALWAYS exits 0; prints nothing when there is
    nothing to say (an uninitialized repo, an empty prompt, no relevant memory).

    NOTHING is the normal quiet outcome: an empty prompt, a repo mokata isn't set up in, or a
    turn with no relevant memory all emit NO `hookSpecificOutput` at all — not an empty
    `additionalContext`, which is still a channel and still says "mokata spoke and had nothing"."""
    args = _parse_flags(argv, "mokata per-turn recall injection hook", [
        ("--prompt", dict(default=None,
                          help="the submitted prompt (bypasses stdin; for testing)")),
        ("--cwd", dict(default=None, help="repo dir (bypasses stdin; for testing)")),
    ])

    try:
        prompt, cwd, session_id = _prompt_envelope(args)
        if not prompt:
            return 0                       # nothing to key a recall on — say nothing

        # Imported lazily (secret-guard's precedent) so a half-installed environment degrades to
        # a clean no-op. `config` alone here — deliberately NOT the memory stack, which is the
        # expensive import and must not be paid by the overwhelmingly common case of a repo
        # mokata is not set up in. `build_injection` is what pulls it, and only past the
        # `is_initialized` gate below.
        from .config import Surface, find_project_root

        root = find_project_root(cwd)
        if not Surface.is_initialized(root):
            return 0                       # not a mokata project — zero cost, total silence
        # RUN-REG (0.0.22 C1) — a typed `/mokata:brainstorm` is this session asking for a pipeline.
        # Recorded BEFORE the recall work and outside its budget: it is one small write, and the
        # gate's verdict for this session depends on it.
        _record_prompt_intent(root, prompt, session_id)
        # SESSION-BIND-BY-ANCESTOR (0.0.23 F1) — refresh this process's session for the MCP side
        # on every prompt (newest write wins), so a `/resume` switch is followed by the next turn.
        _record_session_ancestry(root, session_id)
        # E2 (0.0.22) — `/mokata:review-window`: THIS hook is the one place that knows which session asked, so
        # it opens the review window, or re-focuses the open one on this session, and says where.
        ide_line = _ide_for_prompt(root, prompt, session_id)
        if ide_line:
            _emit(ide_line, event=USER_PROMPT_SUBMIT_EVENT)
            return 0
        surface = Surface.load(root)
        # HP.S5 (0.0.23 E1) — the cache-prefix fingerprint, compared with this session's last
        # record. A change is ONE line, costed against the turn budget; unchanged is silent and
        # writes nothing. Before the bounded work: it is a hash of three short strings.
        notice = _prefix_notice(surface, root, session_id)

        # THE WALL-CLOCK BUDGET (OSS issue #43). Everything expensive lives past this point —
        # opening the store, resolving the embedder, the ranked recall — and until now none of it
        # was bounded by anything. It runs on a bounded worker; if the worker outlives the budget
        # the hook injects NOTHING and returns, rather than handing back a pack the harness has
        # already given up waiting for.
        #
        # The emit and the ledger write stay HERE, on the main thread, past the join. That is the
        # load-bearing half: a worker that finishes late must not be able to emit into a turn that
        # has moved on, and must not be able to mark items "already injected" that the model never
        # saw (see `record_injected` below — the S4 ledger suppresses an item for the rest of the
        # session, so recording a dropped pack loses that memory in the name of not repeating it).
        from .injection_ledger import record_injected
        # ID.S3 (doc 109 §4.2 item 1) — context the HUMAN sent from the IDE rides THIS channel and
        # THIS budget (doc 85 §6: no second injection channel). It is costed first, after the
        # notice: the human's explicit action outranks recall. Drained only after the emit.
        ide_block, ide_ids = _ide_context_for(surface, root, session_id, notice)
        head = "\n".join(x for x in (notice, ide_block) if x)
        budget = _turn_budget_after(surface, head)
        pack = _bounded(lambda: _build_injection_for(surface, prompt, root, session_id, budget),
                        _HOOK_WORK_BUDGET_SECS)
        if pack is None or not pack.text:
            # over budget / unreadable / nothing relevant — emit NO channel at all, unless the
            # prefix moved or the human sent IDE context, which are said on their own.
            if head:
                _emit(head, event=USER_PROMPT_SUBMIT_EVENT)
                _ide_context_delivered(root, ide_ids)
            return 0
        _emit((head + "\n" + pack.text) if head else pack.text,
              event=USER_PROMPT_SUBMIT_EVENT)
        _ide_context_delivered(root, ide_ids)
        # AFTER the emit, and only the ids that actually SURVIVED the budget (`pack.item_ids` is
        # what was rendered, not what was retrieved). Recording a dropped item would suppress it
        # for the rest of the session without the model ever having seen it — losing memory in
        # the name of not repeating it.
        record_injected(root, pack.item_ids, session_id=session_id)
        return 0
    except Exception:  # noqa: BLE001
        # THE FAIL-OPEN FLOOR, and it is deliberately broad. On this event an exit 2 eats the
        # human's turn and even a traceback on stderr is noise in their transcript, so the floor
        # swallows and returns 0. Unlike gate-guard's floor there is no `note_degraded` here: a
        # gate failing open enforces nothing and MUST say so (P16), whereas an injection failing
        # open costs the turn some context it never promised — announcing it every turn would be
        # the louder harm.
        return 0


def _prefix_notice(surface, root: str, session_id: Optional[str]) -> str:
    """HP.S5 — the one-line "cache prefix changed" notice, or "" (unchanged, first sighting, or
    any failure: observability that cannot observe says nothing)."""
    try:
        from .govern.cache import observe_prefix
        obs = observe_prefix(surface, root, session_id, event="turn")
        return obs.notice() if obs is not None else ""
    except Exception:  # noqa: BLE001 — the async lane never fails a turn over a notice
        return ""


def _ide_context_for(surface, root: str, session_id: Optional[str], notice: str):
    """ID.S3 — the bounded IDE-context block for this session's turn and the ids it carries, or
    ("", []). Its budget is the SAME per-turn ceiling as the recall pack, less the notice."""
    try:
        from .bootstrap import estimate_tokens
        from .context_budget import context_budget
        from .ide.context import pending_block
        turn = context_budget(getattr(surface, "manifest", None)).turn_tokens
        return pending_block(root, session_id, max(turn - estimate_tokens(notice), 0))
    except (ImportError, OSError, ValueError):
        return "", []                       # never fail a turn over IDE context; it stays queued


def _ide_context_delivered(root: str, ids) -> None:
    if not ids:
        return
    try:
        from .ide.context import delivered
        delivered(root, ids)
    except (ImportError, OSError, ValueError):
        pass                                # undrained: re-sent next turn rather than lost


def _turn_budget_after(surface, notice: str) -> Optional[int]:
    """The pack's budget with the notice's own cost taken out — ONE budget, never a notice
    appended past it. None (the pack's own default) when there is no notice."""
    if not notice:
        return None
    from .bootstrap import estimate_tokens
    from .context_budget import context_budget
    return max(context_budget(getattr(surface, "manifest", None)).turn_tokens
               - estimate_tokens(notice) - 1, 0)


def _build_injection_for(surface, prompt: str, root: str, session_id: str,
                         budget: Optional[int] = None):
    """The hook's WORK, in one callable so it can be bounded as a unit.

    Extracted for `_bounded` rather than inlined: the budget is on the hook's work, not on one
    call inside it, and the two expensive phases (`already_injected`'s read and `build_injection`'s
    store-open + recall) belong on the same side of the clock. Imported lazily here for the same
    reason the caller imports `config` lazily — a repo mokata is not set up in must never pay for
    the memory stack.

    READ-ONLY by construction (`build_injection` runs the ranked recall with `stamp=False`), which
    is what makes it safe to abandon on a timeout."""
    from .bootstrap import build_injection
    from .injection_ledger import already_injected
    # S4 — what this session has already been handed. Excluded from the ranking, so the budget
    # goes to what is NEW rather than re-handing the model turn 3's answer on turn 20.
    # LIVE-EVIDENCE + DEV-CC (0.0.23 D1) — the develop-time passive findings for the run THIS
    # session is bound to. Bounded on its own clock INSIDE this worker's, so a slow graph costs the
    # turn its evidence lines and never its memory pack.
    evidence = _bounded(lambda: _develop_evidence(surface, root, session_id),
                        _EVIDENCE_BUDGET_SECS) or []
    return build_injection(surface, prompt, budget=budget,
                           exclude_ids=already_injected(root, session_id=session_id),
                           evidence_lines=evidence)


#: D1 — the develop-time evidence's own share of the hook's wall clock (≤3 live graph queries).
_EVIDENCE_BUDGET_SECS = 2.0


def _develop_evidence(surface, root: str, session_id: str) -> List[str]:
    """D1 — `engine/live_evidence.develop_turn_lines` for the run this session is BOUND to (C1's
    claim — never the repo's "active" run). An unbound session, a run outside develop, or any
    failure is `[]`: the per-turn lane only ever adds context."""
    try:
        if not session_id:
            return []
        from .run_intent import CLAIM_BOUND, session_claim
        claim, rid = session_claim(root, session_id)
        if claim != CLAIM_BOUND or not rid:
            return []
        from .domains import domain_turn_lines
        from .engine.live_evidence import develop_turn_lines
        from .engine.spec_anchor import develop_anchor_lines
        from .knowledge import KnowledgeLayer
        # 0.0.25 Lane D — the domain skills the approved spec routes, LOADED in develop AND review
        # (not left to description matching). They lead: a routing instruction the model needs
        # before it reads anything else, and they cost one line per engaged skill.
        routed = domain_turn_lines(root, rid)
        # D4 (SPEC-ANCHOR S2) — the per-AC placement lines and the stale-anchor findings ride the
        # SAME lines, through the `extra` hook: one channel, one budget.
        return routed + develop_turn_lines(
            root, rid, layer_factory=lambda: KnowledgeLayer.from_surface(surface),
            extra=lambda h, s, lay: develop_anchor_lines(h, s, lay, root=root))
    except Exception:  # noqa: BLE001
        return []


def _record_session_ancestry(root: str, session_id: Optional[str]) -> None:
    """SESSION-BIND-BY-ANCESTOR — record `session_id` against this hook's ancestor pids (see
    `session_ancestry`). SessionStart/UserPromptSubmit only, never PreToolUse. Bounded; never
    raises: an unrecorded map only means the MCP side resolves exactly as it did before."""
    try:
        from .session_ancestry import record_session
        record_session(root, session_id)
    except Exception:  # noqa: BLE001 — observability-class write in a hook that must exit 0
        pass


def _record_prompt_intent(root: str, prompt: str, session_id: str) -> None:
    try:
        from .run_intent import pipeline_in_prompt, record_intent
        pipeline = pipeline_in_prompt(prompt)
        if pipeline and session_id:
            record_intent(root, session_id, "prompt:" + pipeline)
    except Exception:  # noqa: BLE001 — an unrecorded intent is the gate as it was (fail open)
        pass


def _prompt_envelope(args) -> "tuple":
    """(prompt, cwd, session_id) from the UserPromptSubmit envelope on stdin, or from the
    `--prompt`/`--cwd` argv escape. Never raises; every unreadable field degrades to empty."""
    if args.prompt is not None:
        return args.prompt, args.cwd or os.getcwd(), ""
    raw = _read_stdin_bounded(_STDIN_READ_TIMEOUT_SECS)
    payload = {}
    if raw and raw.strip():
        try:
            loaded = json.loads(raw)
            payload = loaded if isinstance(loaded, dict) else {}
        except (ValueError, TypeError):
            payload = {}
    prompt = payload.get("prompt")
    sid = payload.get("session_id")
    return (prompt if isinstance(prompt, str) else "",
            args.cwd or payload.get("cwd") or os.getcwd(),
            sid if isinstance(sid, str) else "")


# ======================================================================================
# statusline (Claude Code statusLine command — Stage 54b)
# ======================================================================================
# Claude Code's statusLine is a command that receives the session JSON on stdin and prints
# ONE line to stdout (re-run, debounced, as state changes). This subcommand reads that
# payload, builds mokata's pipeline-stage mode-badge from the run-state, and prints it.
# It is READ-ONLY, ALWAYS exits 0, never blocks, and prints NOTHING when the badge is
# disabled (settings.ux.statusline=false) or mokata isn't initialized here — so it's pure,
# safe surfacing that can never get in the way of a session.

# How long we let a wrapped (pre-existing user) statusline command run before giving up —
# a statusline must never block the harness, so composition degrades to mokata's part alone.
_WRAP_TIMEOUT_SECS = 5


def _read_statusline_stdin() -> str:
    """The raw statusLine JSON on stdin; "" when there's nothing/at a TTY (never blocks)."""
    if sys.stdin is None:
        return ""
    try:
        if sys.stdin.isatty():
            return ""
    except (ValueError, OSError):
        # D5 — the two classes `isatty()` actually raises: ValueError on a CLOSED stream ("I/O
        # operation on closed file") and OSError on a broken/errored one. Either way there is no
        # TTY to detect, so fall through to the bounded read. Nothing else is swallowed.
        pass
    raw = _read_stdin_bounded(_STDIN_READ_TIMEOUT_SECS)
    return raw if raw is not None else ""


def _cwd_and_session_name(raw: str):
    """(cwd, session_name, session_id) from the statusLine payload. Honour `workspace.current_dir`
    (then top-level `cwd`), the optional `session_name` Claude Code passes (the Stage-55 badge
    slot), and the top-level `session_id` (B-BADGE — the session-aware badge resolution key).
    Degrade to the process cwd / no name / no id on any parse problem — a payload with no
    `session_id` (older/non-Claude caller) yields None, which keeps today's badge byte-identical."""
    cwd, name, session_id = os.getcwd(), None, None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return cwd, name, session_id
    if isinstance(payload, dict):
        ws = payload.get("workspace")
        if isinstance(ws, dict) and ws.get("current_dir"):
            cwd = ws["current_dir"]
        elif payload.get("cwd"):
            cwd = payload["cwd"]
        sn = payload.get("session_name")
        if isinstance(sn, str) and sn.strip():
            name = sn
        sid = payload.get("session_id")
        if isinstance(sid, str) and sid.strip():
            session_id = sid
    return cwd, name, session_id


def _mokata_segment(cwd: str, session_name: Optional[str],
                    session_id: Optional[str] = None) -> str:
    """mokata's badge for `cwd` — "" when mokata isn't initialized here, the badge is
    disabled, or the engine is unavailable. Never raises."""
    try:
        # the cheap question first, on the leaf module: a repo mokata is not set up in renders
        # nothing, and must not pay for `config` + `progress` to learn that (HOOK-BUDGET)
        from .repo_identity import find_project_root, is_initialized
        root = find_project_root(cwd)
        if not is_initialized(root):
            return ""
        from .config import Surface
        from .progress import statusline_badge, statusline_enabled
    except Exception:
        return ""
    try:
        surface = Surface.load(root)
        if not statusline_enabled(surface):
            return ""
        # TM.S1 — the statusline segment prefixes the run mode (local|team) so a session is
        # never ambiguous about which mode it's in, then the pipeline-stage badge. B-BADGE — the
        # payload's `session_id` scopes the stage strip to THIS session (see `run_resolver`).
        #
        # MCP-R.D2's wait segment (UX-NOTIFY) used to be appended HERE, one call below. Lane F
        # moved the composition into `statusline_badge` and put the wait FIRST — see that
        # function for why order is the deliverable and why composing it here left it unpinned.
        # Nothing is lost: this is `statusline_badge`'s only production caller, so the shipped
        # line gains the ordering and loses nothing it rendered before.
        return statusline_badge(surface, session_name=session_name, session_id=session_id)
    except Exception:
        return ""


def _run_wrapped(command: str, raw: str) -> str:
    """Run the statusLine command that was already in this settings.json (merge-safe
    composition), feeding it the SAME payload, and return its first stdout line. Best-effort +
    bounded: any failure / timeout degrades to "" so mokata's badge still renders and the
    harness never blocks.

    "already in this settings.json" is deliberate and is NOT "the user's own" — see the
    B602 justification below."""
    if not command:
        return ""
    try:
        import subprocess
        # Justification for the B602 suppression (F10, rewritten — the previous one asserted
        # this string was never under an attacker's control, which is FALSE at the default
        # scope and had been for four releases):
        #
        #   `command` reaches here from ONE writer — `harness_setup._statusline_command`, the
        #   only thing in the package that spells this flag — which composes it from a
        #   `harness_setup.WrapOrigin`: a statusLine command STAMPED WITH THE settings.json IT
        #   WAS READ FROM. That function REFUSES a WrapOrigin whose origin is not the file
        #   being written, so mokata never carries a statusLine command between files or
        #   across scopes. The string that lands here is therefore always one the harness
        #   itself already runs through a shell out of that same file — mokata adds no
        #   execution path, and the human was shown the exact line before approving setup.
        #
        #   NOT "the user's own": at the default `project` scope the file is
        #   `<root>/.claude/settings.json`, Claude Code's checked-in, shareable project
        #   settings — a clone can carry one nobody here wrote. The no-added-privilege
        #   argument above does not depend on who authored it, which is why it is the one
        #   stated. The caller graph it rests on is DERIVED in
        #   `tests/test_stage5_statusline_wrap_origin.py`, so a second writer, a second merge
        #   site, or a `_merge_statusline` that read one file and wrote another goes RED here
        #   rather than quietly turning this comment into a lie.
        #
        # `shell=True` STAYS: it must match how the harness itself runs a statusLine (pipes,
        # $VARS); a shlex.split arg-list would silently break a user's existing line — a
        # behaviour change. Bounded by `timeout`, best-effort (failure → "").
        proc = subprocess.run(command, shell=True, input=raw,  # nosec B602
                              capture_output=True, text=True, timeout=_WRAP_TIMEOUT_SECS)
        first = (proc.stdout or "").splitlines()
        return first[0].strip() if first else ""
    except Exception:
        return ""


def statusline_main(argv: Optional[List[str]] = None) -> int:
    """Print mokata's pipeline-stage badge for the Claude Code statusLine. With ``--wrap``
    it first runs the user's pre-existing statusLine command and prepends its output
    (compose, don't clobber). ALWAYS exits 0 and prints at most ONE line; prints nothing
    when there's nothing to show."""
    args = _parse_flags(argv, "mokata statusLine badge", [
        ("--wrap", dict(default=None,
                        help="a pre-existing statusLine command to run + compose with")),
    ])

    raw = _read_statusline_stdin()
    cwd, session_name, session_id = _cwd_and_session_name(raw)

    their = _run_wrapped(args.wrap, raw) if args.wrap else ""
    mine = _mokata_segment(cwd, session_name, session_id)

    line = "  ".join(part for part in (their, mine) if part)
    if line:
        sys.stdout.write(line + "\n")
    return 0


# ======================================================================================
# dispatcher
# ======================================================================================

_SUBCOMMANDS = {
    "session-start": session_start_main,
    "secret-guard": secret_guard_main,
    "gate-guard": gate_guard_main,
    "dirty-track": dirty_track_main,
    "run-bind": run_bind_main,
    "pre-compact": pre_compact_main,
    "subagent-start": subagent_start_main,
    "subagent-stop": subagent_stop_main,
    "stop": stop_main,
    "session-end": session_end_main,
    "user-prompt-submit": user_prompt_submit_main,
    "statusline": statusline_main,
}

# The one place the subcommand list is written for HUMANS (both error strings below). Derived
# from `_SUBCOMMANDS` so a new hook cannot be added without appearing in the misconfiguration
# message that tells a user what they mistyped.
_SUBCOMMAND_LIST = " | ".join(_SUBCOMMANDS)


#: The observability hooks that do NOTHING outside a mokata repo — each returns 0, silently, with no
#: side effect, once its root resolves to None (and the plugin route's stand-down is the same exit 0).
#: On the PLUGIN route (the one installed globally — `mokata setup` wires the settings route per
#: project), `main` answers them from `_surely_not_mokata` before importing anything, so a user pays a
#: bare interpreter for them in every non-mokata repo (HOOK-BUDGET). The settings route always reaches
#: its handler (test_b3: it never stands down). NOT here:
#: `dirty-track` (it records in any repo), the gate hooks (self-protect and the secret scan run
#: everywhere), and the context hooks (session-start records the plugin root; the others may speak).
QUIET_OUTSIDE_MOKATA = ("stop", "session-end", "pre-compact", "subagent-start", "subagent-stop",
                        "run-bind")


#: Appended by the PLUGIN's `hooks.json` to every command (0.0.23 B3,
#: `HOOKS-FIRE-TWICE-WITH-PLUGIN-AND-PROJECT-WIRING`). Stripped before dispatch — except that
#: `session-start` is handed it back (0.0.25 I-04: the check-on-miss line names the route switch
#: only on the plugin route).
VIA_PLUGIN_FLAG = "--via-plugin"


def _plugin_wiring(sub: str) -> List[tuple]:
    """(event, matcher) for every PLUGIN hook that runs `sub`, read from the packaged hooks.json."""
    import json as _json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks", "hooks.json")
    with open(path, encoding="utf-8") as fh:
        data = _json.load(fh)
    out = []
    for event, blocks in (data.get("hooks") or {}).items():
        for block in blocks or []:
            for h in block.get("hooks") or []:
                words = str(h.get("command", "")).split()
                if len(words) >= 2 and words[1] == sub:
                    out.append((event, block.get("matcher")))
    return out


def _matcher_covers(settings_matcher: Optional[str], plugin_matcher: Optional[str]) -> bool:
    """True when the settings hook sees AT LEAST every tool the plugin hook sees. A plain `A|B|C`
    tool list is compared as a set; anything else (a regex) must be identical."""
    if not plugin_matcher:
        return not settings_matcher
    if not settings_matcher:
        return False
    plain = lambda m: all(part.isidentifier() for part in m.split("|"))   # noqa: E731
    if plain(settings_matcher) and plain(plugin_matcher):
        return set(plugin_matcher.split("|")) <= set(settings_matcher.split("|"))
    return settings_matcher == plugin_matcher


def _settings_route_owns(sub: str) -> bool:
    """True when `mokata setup`/`init` wired this SAME hook in settings.json — same subcommand, same
    EVENT, a matcher that covers at least the plugin's — and it resolves.

    With the plugin installed AND project/user wiring present, Claude Code runs every hook twice —
    two `gate_block` events 20 ms apart, the review window launched then re-focused. The settings
    route is the one the user wired explicitly, at an absolute path, so the PLUGIN invocation
    stands down — but ONLY where the settings hook does everything the plugin hook would. A 0.0.21
    wiring's gate-guard matcher has no `Skill|Agent|Task`; standing down for it would have switched
    the gate off for those tools (0.0.23 verification B1). ⛔ A dead settings hook never silences
    the live plugin one, and any doubt — unreadable settings or hooks.json — answers False, so the
    plugin hook runs: a gate that silently does not run is worse than a duplicate."""
    try:
        from .hook_wiring import _settings_hooks, _wired_subcommand
        root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        mine = _plugin_wiring(sub)
        if not mine:
            return False
        theirs = [h for h in _settings_hooks(root, None)
                  if _wired_subcommand(h) == sub and h.resolves]
        return all(any(h.event == event and _matcher_covers(h.matcher, matcher) for h in theirs)
                   for event, matcher in mine)
    except Exception:
        return False


def main(argv: Optional[List[str]] = None) -> int:
    """`mokata-hook <subcommand> [args…]`. A missing or unknown subcommand is a
    MISCONFIGURATION (a mis-wired hook command), so it fails VISIBLY with exit 1 rather than
    a silent no-op — otherwise a mis-wired hook looks successful to Claude Code and scripts.
    It must NOT use exit 2: exit 2 is mokata's reserved SECURITY-BLOCK code, and only exit 2
    blocks a PreToolUse tool call — so exit 1 still honors "hooks never block a session on a
    routing mistake" (it fails loud without tripping the security-block path)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write(
            f"mokata-hook: missing subcommand ({_SUBCOMMAND_LIST})\n")
        return 1  # misconfiguration → visible exit 1 (NOT 0=silent, NOT 2=security-block)
    sub, rest = argv[0], argv[1:]
    via_plugin = VIA_PLUGIN_FLAG in rest
    rest = [a for a in rest if a != VIA_PLUGIN_FLAG]
    handler = _SUBCOMMANDS.get(sub)
    if handler is None:
        sys.stderr.write(
            f"mokata-hook: unknown subcommand {sub!r} (expected {_SUBCOMMAND_LIST})\n")
        return 1  # misconfiguration → visible exit 1 (NOT 0=silent, NOT 2=security-block)
    if via_plugin and sub in QUIET_OUTSIDE_MOKATA and not rest:
        _STDIN_MEMO[:] = [_read_stdin_bounded(_STDIN_READ_TIMEOUT_SECS)]
        try:
            try:
                loaded = json.loads(_STDIN_MEMO[0] or "")
            except (ValueError, TypeError):
                loaded = {}
            cwd = (loaded.get("cwd") if isinstance(loaded, dict) else None) or os.getcwd()
            if isinstance(cwd, str) and _surely_not_mokata(cwd):
                return 0
            return _dispatch(sub, handler, rest, via_plugin)
        finally:
            _STDIN_MEMO.clear()
    return _dispatch(sub, handler, rest, via_plugin)


def _dispatch(sub: str, handler: "Callable[[List[str]], int]", rest: List[str],
              via_plugin: bool) -> int:
    if via_plugin and _settings_route_owns(sub):
        if sub == "session-start":
            # the plugin root is still recorded — `/mokata:init` finds the bundled engine by it
            root = None
            if "--plugin-root" in rest:
                i = rest.index("--plugin-root")
                root = rest[i + 1] if i + 1 < len(rest) else None
            _record_plugin_root(root)
        return 0
    if via_plugin and sub == "session-start":
        rest = rest + [VIA_PLUGIN_FLAG]
    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main())
