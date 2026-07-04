"""progress / sessions / resume / watch / govern — read-only run views and local HTML dashboards."""
from __future__ import annotations

import argparse
import os

from ._common import (
    AuditLedger,
    _load_surface,
)


def cmd_progress(args: argparse.Namespace) -> int:
    # Stage 27 — read-only run-progress tracker. Degrades cleanly with no active run.
    # Stage 40 — `--lanes` renders the parallel-aware multi-lane view (read-only).
    surface = _load_surface(args.path)
    if getattr(args, "lanes", False):
        from ..progress import build_run_lanes, render_lanes
        ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
        rl = build_run_lanes(surface.state, ledger=ledger, run_id=args.run)
        print(render_lanes(rl, ascii_only=args.ascii))
        return 0
    from ..progress import build_progress, render_progress
    progress = build_progress(surface.state, run_id=args.run)
    # Stage 6d — pass the surface so /progress renders the MAX-detail view: the 7-phase tracker
    # PLUS the 5 user-stage arc + 6c develop sub-counter + what's pending this session.
    print(render_progress(progress, ascii_only=args.ascii, surface=surface))
    return 0


def cmd_progress_mark(args: argparse.Namespace) -> int:
    # Stage 6b — append a `stage_enter` to the append-only progress-event log so the
    # always-on badge can tell develop/review/ship apart. OBSERVABILITY tier: append-only
    # + UNGATED (same trust tier as the audit ledger — NOT a P2 durable write, so it never
    # routes through the WriteGate/human-gate). Degrade-clean: any failure is reported and
    # non-fatal (a skills-template call must never break the phase it's recording).
    from ..progress import find_active_run
    from ..progress_events import ProgressLog, STAGE_ENTER
    try:
        surface = _load_surface(args.path)
        run_id = find_active_run(surface.state)     # reuse the existing run identity
        ProgressLog.from_surface(surface).append_event(
            STAGE_ENTER, args.stage, run_id=run_id)
        where = f" (run {run_id})" if run_id else ""
        print(f"mokata progress: entered '{args.stage}'{where}.")
        return 0
    except Exception as exc:
        # never fail the caller — the log is best-effort observability.
        print(f"mokata progress: could not record '{args.stage}' ({exc}); continuing.")
        return 0


def cmd_progress_record_review(args: argparse.Namespace) -> int:
    # Stage 6r — persist the closing review's verdict as a `review_verdict` event in the SAME
    # append-only progress-event log (one persistence layer, not a second store). Observability
    # tier: append-only + UNGATED (like `mark`). `--independent` records that the review ran as
    # a fresh-context subagent; omit it when it degraded to the inline two-pass. Degrade-clean:
    # any failure is reported and non-fatal (the review skill must never break on recording).
    from ..progress_events import record_review_verdict
    passed = args.passed and not args.failed        # --failed wins if somehow both slip through
    try:
        surface = _load_surface(args.path)
        record_review_verdict(surface, passed=passed, independent=args.independent,
                              findings=args.findings)
        kind = "independent" if args.independent else "inline"
        verdict = "passed" if passed else "failed"
        print(f"mokata review: recorded verdict {verdict} ({kind}).")
        return 0
    except Exception as exc:
        print(f"mokata review: could not record the verdict ({exc}); continuing.")
        return 0


def cmd_progress_review_status(args: argparse.Namespace) -> int:
    # Stage 6r — the read side of the review gate, surfaced so `/mokata:ship` VERIFY checks the
    # RECORD, not conversation vibes. Prints the one-line gate verdict and returns non-zero when
    # ship must BLOCK (no verdict recorded, or the review failed) — fail-closed, evidence over
    # claims. An inline (non-independent) PASS returns 0 (ships) but says so honestly.
    from ..progress_events import ship_review_gate
    try:
        surface = _load_surface(args.path)
        gate = ship_review_gate(surface)
    except Exception as exc:
        # degrade-clean: treat an unreadable gate as "no verdict" -> block, never a traceback.
        print(f"review hasn't run — run /mokata:review first ({exc})")
        return 2
    if gate.blocks:
        line = gate.message + (f"  → to unblock: {gate.unblock}" if gate.unblock else "")
        print(line)
        return 2
    print(gate.message)
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    # Stage 50 — list past + active runs (read-only; bounded; friendly empty state).
    from ..progress import list_sessions
    surface = _load_surface(args.path)
    sessions = list_sessions(surface.state)
    if not sessions:
        print("mokata sessions: no runs on record yet. Start one with /mokata:brainstorm "
              "or /mokata:refine.")
        return 0
    print(f"mokata sessions — {len(sessions)} run(s):")
    for s in sessions:
        status = ("complete ✓" if s.complete
                  else f"resume at '{s.resume_phase}'") + (" · active" if s.active else "")
        last = f" · last passed '{s.last_passed}'" if s.last_passed else " · not started"
        print(f"  {s.run_id:24} [{s.done}/{s.total}]{last} — {status}")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    # Stage 50 — PREVIEW where a run resumes (read-only): the phase + the gate that still
    # applies. mokata never auto-runs the pipeline; the gates hold on resume.
    from ..pipeline import PHASE_GATES
    from ..progress import build_progress, find_active_run
    surface = _load_surface(args.path)
    rid = args.id or find_active_run(surface.state)
    if rid is None:
        print("mokata resume: no run to resume. Start one with /mokata:brainstorm.")
        return 0
    progress = build_progress(surface.state, run_id=rid)
    if not progress.active:
        print(f"mokata resume: {progress.message}")
        return 0
    if progress.complete:
        print(f"mokata resume: run '{rid}' is complete — nothing to resume.")
        return 0
    phase = progress.current
    gate = PHASE_GATES.get(phase)
    print(f"mokata resume: run '{rid}' — [{progress.done}/{progress.total}] phases passed.")
    print(f"  resume at: '{phase}'"
          + (f" (the '{gate.id}' gate still applies — {gate.kind})" if gate else ""))
    print(f"  continue with: mokata enter {phase}   (or /mokata:{phase}) — gates hold.")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    # Stage 40 — write the self-contained local HTML dashboard (read-only; never mutates a run).
    # Respects settings.ux.progress: `terminal` writes NO HTML (the terminal tier is the floor).
    from ..dashboard import dashboard_enabled, ux_progress_setting, write_dashboard
    surface = _load_surface(args.path)
    if not dashboard_enabled(surface):
        print(f"mokata watch: the dashboard is off (settings.ux.progress="
              f"{ux_progress_setting(surface)}). Enable it with "
              f"`mokata config set settings.ux.progress dashboard` (or `both`).")
        return 0
    refresh = None if args.once else 2
    path = write_dashboard(surface, run_id=args.run, refresh_secs=refresh)
    print(f"mokata watch: wrote {path}")
    if args.open:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(path))
    if args.once:
        return 0
    # Live mode: rewrite the file on an interval; the page meta-refreshes itself. Read-only.
    print("mokata watch: live — refreshing every 2s (Ctrl-C to stop).")
    try:
        import time
        while True:
            time.sleep(2)
            write_dashboard(surface, run_id=args.run, refresh_secs=refresh)
    except KeyboardInterrupt:
        print("\nmokata watch: stopped.")
    return 0


def cmd_govern(args: argparse.Namespace) -> int:
    # Stage 48 — write the self-contained governance dashboard (rules + memory-by-kind +
    # read/write ratio + pending proposals + Stage 60 "since last session" diff). Read-only;
    # never mutates state. The manage commands are surfaced, not run.
    # Stage 60 — optional live self-meta-refresh (mirrors `mokata watch`), honouring
    # settings.ux.progress: the live loop runs only on the dashboard tier; the static snapshot
    # always works (the govern view is valuable even on the terminal tier).
    from ..dashboard import dashboard_enabled, ux_progress_setting, write_governance_dashboard
    surface = _load_surface(args.path)
    live = getattr(args, "live", False) and not getattr(args, "once", False)
    if live and not dashboard_enabled(surface):
        print(f"mokata govern: live refresh needs the dashboard tier (settings.ux.progress="
              f"{ux_progress_setting(surface)}). Writing a static snapshot. Enable live with "
              f"`mokata config set settings.ux.progress dashboard` (or `both`).")
        live = False
    refresh = 2 if live else None
    path = write_governance_dashboard(surface, refresh_secs=refresh)
    print(f"mokata govern: wrote {path}")
    print("  read-only view of the governed state — manage via the surfaced "
          "`mokata memory edit` commands (human-gated).")
    if args.open:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(path))
    if not live:
        return 0
    # Live mode: rewrite the file on an interval; the page meta-refreshes itself. Read-only.
    print("mokata govern: live — refreshing every 2s (Ctrl-C to stop).")
    try:
        import time
        while True:
            time.sleep(2)
            write_governance_dashboard(surface, refresh_secs=refresh)
    except KeyboardInterrupt:
        print("\nmokata govern: stopped.")
    return 0


def register(sub, common):
    p_prog = sub.add_parser(
        "progress", parents=[common],
        help="show the run-progress tracker (done/current/pending); read-only",
    )
    p_prog.add_argument("--run", default=None,
                        help="a specific run id (default: the active/most-recent run)")
    p_prog.add_argument("--ascii", action="store_true",
                        help="ASCII glyphs ([x]/[>]/[ ]) instead of unicode")
    p_prog.add_argument("--lanes", action="store_true",
                        help="parallel-aware multi-lane view (one line per concurrent lane)")
    p_prog.set_defaults(func=cmd_progress)

    # Stage 6b — `mokata progress mark <stage>`: append a user-stage transition to the
    # append-only progress-event log (observability, UNGATED). A nested sub-action, so the
    # top-level command set (and the Stage-54e parity matrix) still sees one `progress`
    # command; bare `mokata progress` keeps its default (the tracker) since this sub-action
    # is optional, not required.
    prog_sub = p_prog.add_subparsers(dest="progress_action")
    from ..progress import STAGE_BADGE_STAGES
    p_mark = prog_sub.add_parser(
        "mark", parents=[common],
        help="record entering a user-stage (brainstorm/spec/develop/review/ship) in the "
             "append-only progress log; observability, never gated",
    )
    p_mark.add_argument("stage", choices=list(STAGE_BADGE_STAGES),
                        help="the user-stage being entered")
    p_mark.set_defaults(func=cmd_progress_mark)

    # Stage 6r — `mokata progress record-review` / `review-status`: the closing review's
    # verdict is persisted as a `review_verdict` event in the SAME log (one persistence layer),
    # and ship reads it back. Both are nested sub-actions (like `mark`), so the top-level
    # command set + the Stage-54e parity matrix still see one `progress` command.
    p_rec = prog_sub.add_parser(
        "record-review", parents=[common],
        help="persist the closing review's verdict (passed/failed, independent?) in the "
             "append-only progress log; observability, never gated",
    )
    outcome = p_rec.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--passed", action="store_true", help="the review passed")
    outcome.add_argument("--failed", action="store_true", help="the review failed (findings)")
    p_rec.add_argument("--independent", action="store_true",
                       help="the review ran as a fresh-context subagent (vs the inline "
                            "two-pass)")
    p_rec.add_argument("--findings", default=None,
                       help="optional finding count / summary to record with the verdict")
    p_rec.set_defaults(func=cmd_progress_record_review)

    p_rs = prog_sub.add_parser(
        "review-status", parents=[common],
        help="print ship's review gate from the persisted verdict; exits non-zero when ship "
             "must BLOCK (no verdict, or review failed)",
    )
    p_rs.set_defaults(func=cmd_progress_review_status)

    p_sessions = sub.add_parser(
        "sessions", parents=[common],
        help="list past + active runs (id, phases passed, resume point); read-only",
    )
    p_sessions.set_defaults(func=cmd_sessions)

    p_resume = sub.add_parser(
        "resume", parents=[common],
        help="preview where a run resumes — the phase + the gate that still applies",
    )
    p_resume.add_argument("id", nargs="?", default=None,
                          help="run id to resume (default: the active/most-recent run)")
    p_resume.set_defaults(func=cmd_resume)

    p_watch = sub.add_parser(
        "watch", parents=[common],
        help="write a self-contained local HTML dashboard of the active run (read-only)",
    )
    p_watch.add_argument("--once", action="store_true",
                         help="write a single snapshot and exit (no live refresh loop)")
    p_watch.add_argument("--open", action="store_true",
                         help="open the written HTML file in your browser")
    p_watch.add_argument("--run", default=None,
                         help="a specific run id (default: the active/most-recent run)")
    p_watch.set_defaults(func=cmd_watch)

    p_govern = sub.add_parser(
        "govern", parents=[common],
        help="write a clickable local HTML view of the governed state "
             "(rules + memory by kind + pending proposals; read-only)",
    )
    p_govern.add_argument("--open", action="store_true",
                          help="open the dashboard in your browser after writing it")
    p_govern.add_argument("--live", action="store_true",
                          help="auto-refresh: re-write on an interval + self meta-refresh "
                               "(dashboard tier only; Ctrl-C to stop)")
    p_govern.add_argument("--once", action="store_true",
                          help="write a single static snapshot and exit (no live loop)")
    p_govern.set_defaults(func=cmd_govern)


__all__ = [
    "cmd_progress",
    "cmd_progress_mark",
    "cmd_progress_record_review",
    "cmd_progress_review_status",
    "cmd_sessions",
    "cmd_resume",
    "cmd_watch",
    "cmd_govern",
]
