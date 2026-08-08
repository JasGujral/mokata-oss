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
        rl = build_run_lanes(surface.state, ledger=ledger, run_id=args.run, root=surface.root)
        print(render_lanes(rl, ascii_only=args.ascii))
        return 0
    from ..progress import build_progress, render_progress
    progress = build_progress(surface.state, run_id=args.run, root=surface.root)
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
    from ..progress_events import ProgressLog, STAGE_ENTER
    from ..run_resolver import resolve_run, unresolved_reason
    try:
        surface = _load_surface(args.path)
        # RUN-ID-DRIFT — THE resolver, not a scan. This is the surface OSS #44 was reported on: the
        # scan stamped a stage into whichever run sorted first, so `progress` read `[2/7]` on one run
        # while the marks landed on a different, unstarted one.
        #
        # The TWO unresolved cases are handled differently, and that difference is the whole Class-1
        # point rather than an inconsistency:
        #
        #   AMBIGUOUS — several runs, none identifiably the caller's. REFUSE. A stage mark in the
        #     wrong run is worse than an absent one: it is a false green a later reader trusts, and
        #     it is exactly what #44 reported. Say which runs were seen and name `--run`.
        #   NO RUN AT ALL — nothing exists to mis-attribute to, so recording a RUN-LESS stage_enter
        #     is honest observability, not a guess (it is what the badge's "no checkpoint but a log
        #     recorded a stage" path reads, and it is byte-for-byte the pre-#44 behaviour here).
        res = resolve_run(surface.root, run_id=getattr(args, "run", None))
        run_id = res.run_id
        if res.ambiguous:
            print(f"mokata progress: not recording '{args.stage}' — " + unresolved_reason(res))
            return 0
        ProgressLog.from_surface(surface).append_event(
            STAGE_ENTER, args.stage, run_id=run_id)
        # B-LIFE — entering `ship` is the run's terminal END-OF-RUN signal: stamp the checkpoint's
        # `completed_at` (once, additive, run-state class) so a finished run is reported as
        # finished-THEN, not current-NOW. Best-effort: a stamp failure never blocks recording the
        # transition. Keyed on SHIP (not the emit checkpoint) — a spec-emitted run is AT develop.
        if args.stage == "ship" and run_id:
            try:
                from ..govern.resume import PipelineCheckpoint
                PipelineCheckpoint(surface.state, run_id).mark_completed()
            except Exception:
                pass
        # MS.S2 — a stage transition is a natural touchpoint to refresh this window's registry
        # entry (its current phase). Degrade-clean: registry upkeep never breaks recording a stage.
        try:
            from .. import session_registry as _SR
            _SR.touch(surface, phase=args.stage)
        except Exception:
            pass
        where = f" (run {run_id})" if run_id else ""
        print(f"mokata progress: entered '{args.stage}'{where}.")
        return 0
    except Exception as exc:
        # never fail the caller — the log is best-effort observability.
        #
        # REVIEW-FIX.R4 deliberately LEFT this posture alone while making the sibling
        # `record-review` failure exit non-zero, and the asymmetry is the point, not an oversight:
        # a `stage_enter` is OBSERVABILITY (it moves a badge), while a `review_verdict` is GATE
        # EVIDENCE (`ship_review_gate` refuses to ship without it). Losing the first costs a
        # cosmetic; losing the second means a review that happened cannot be proven, which is the
        # one failure the 6r loop exists to make impossible to miss. Do not "fix" this by symmetry.
        print(f"mokata progress: could not record '{args.stage}' ({exc}); continuing.")
        return 0


def review_recorded_line(verdict: str, kind: str, run_id: str) -> str:
    """The one line a recorded verdict reports — lifted VERBATIM out of
    `cmd_progress_record_review` (no text change) so the MCP `review_record` tool says the SAME
    thing (REVIEW-FIX.R3). One truth source deserves one wording; a second copy is a drift class.
    `test_review_fix_r3` reconstructs the pre-R3 f-string and pins byte-identity."""
    return f"mokata review: recorded verdict {verdict} ({kind}) for run {run_id}."


def review_runless_line(verdict: str, kind: str) -> str:
    """The same, for a verdict that landed with NO run — which satisfies NO gate (R1). Shared by
    the CLI and the MCP `review_record` tool so both name the same remedy (REVIEW-FIX.R3)."""
    return (f"mokata review: recorded verdict {verdict} ({kind}) WITHOUT a run — no run could "
            f"be resolved here, and ship will NOT accept a run-less verdict. Re-record it "
            f"naming the run: `mokata progress record-review --{verdict}"
            f" --run <run id>` (`mokata sessions` lists them).")


def review_record_failed_line(exc: BaseException) -> str:
    """The one line a FAILED recording reports (REVIEW-FIX.R4) — shared by the CLI and the MCP
    `review_record` tool, the R3 way, so the failure reads the same on both surfaces.

    It states the CONSEQUENCE before the remedy, because the consequence is what the caller gets
    wrong: nothing was written, so ship's gate will block as though review never ran. The remedy is
    the MCP tool's pre-R4 hint, promoted to shared text.

    `{exc}` IS echoed here, unlike the read path's sentences. The asymmetry is grounded, not
    inconsistent: this is a WRITE fault (resolving the run, then appending to the log), so the
    exception carries filesystem/permission detail and never log CONTENT — whereas a read fault can
    quote the offending line, and a `review_verdict` line carries findings text."""
    return (f"mokata review: FAILED to record the verdict ({exc}) — nothing was written, so ship's "
            f"review gate will BLOCK as if review never ran. Retry, or record it from the terminal: "
            f"`mokata progress record-review --passed|--failed --run <run id>` "
            f"(`mokata sessions` lists them).")


# REVIEW-FIX.R4 — the exit code a FAILED recording returns, chosen and not inherited.
#
# 1, not 2. In this cluster 2 means the review gate BLOCKS — a verdict about the CODE, which
# `review-status` returns and which routes the human to /mokata:review. A record failure is not a
# verdict about anything; it is this command failing to do its job, and giving it 2 would tell a
# caller that reads the code (a script, a skill) to send someone to re-review when the actual fault
# is a log that could not be written. That mis-routing is exactly the defect R4 fixes on the READ
# side, and minting it on the write side would be perverse. 1 is already the repo's "the operation
# failed" code (`vault pull`/`push` on a VaultError, `worktree create` when nothing was created),
# and argparse owns 2 for usage errors besides.
RECORD_REVIEW_FAILED_EXIT = 1


def cmd_progress_record_review(args: argparse.Namespace) -> int:
    # Stage 6r — persist the closing review's verdict as a `review_verdict` event in the SAME
    # append-only progress-event log (one persistence layer, not a second store). Observability
    # tier: append-only + UNGATED (like `mark`). `--independent` records that the review ran as
    # a fresh-context subagent; omit it when it degraded to the inline two-pass. Degrade-clean:
    # any failure is reported and non-fatal (the review skill must never break on recording).
    # REVIEW-FIX.R1 — `--run` names the run the verdict belongs to (the record-key ship reads back
    # with); omitted, it resolves session-awarely. A verdict that lands run-less satisfies NO gate,
    # so say that here rather than let ship discover it later.
    from ..progress_events import _CURRENT_RUN, record_review_verdict
    passed = args.passed and not args.failed        # --failed wins if somehow both slip through
    try:
        surface = _load_surface(args.path)
        event = record_review_verdict(surface, passed=passed, independent=args.independent,
                                      findings=args.findings,
                                      run_id=args.run if args.run else _CURRENT_RUN)
        kind = "independent" if args.independent else "inline"
        verdict = "passed" if passed else "failed"
        rid = event.get("run_id")
        if rid:
            print(review_recorded_line(verdict, kind, rid))
            return 0
        print(review_runless_line(verdict, kind))
        return 0
    except Exception as exc:
        # REVIEW-FIX.R4 — this used to print "…; continuing." and return 0: a review whose verdict
        # could NOT be written exited GREEN, so nothing checking the exit code could tell a recorded
        # verdict from a lost one. That is the single failure this whole cluster exists to prevent
        # (evidence over claims) and it was the one made invisible.
        #
        # The review skill's "degrade-clean: never break on recording" contract SURVIVES, relocated:
        # nothing raises, no traceback reaches the user, and the review's own findings are still
        # theirs to act on. What moves is the honesty of the signal — the failure is LOUD in the
        # message and TRUE in the exit code, instead of silent in both.
        print(review_record_failed_line(exc))
        return RECORD_REVIEW_FAILED_EXIT


def cmd_progress_review_status(args: argparse.Namespace) -> int:
    # Stage 6r — the read side of the review gate, surfaced so `/mokata:ship` VERIFY checks the
    # RECORD, not conversation vibes. Prints the one-line gate verdict and returns non-zero when
    # ship must BLOCK (no verdict recorded, or the review failed) — fail-closed, evidence over
    # claims. An inline (non-independent) PASS returns 0 (ships) but says so honestly.
    # REVIEW-FIX.R1 — `--run` reads THAT run's verdict; omitted, the run is resolved session-awarely
    # and an unresolvable run BLOCKS with the remedy (never a global scan of every run's verdicts).
    # REVIEW-FIX.R4 — a read ERROR and an ABSENT verdict are now DIFFERENT answers. Both still
    # BLOCK (fail-closed is unchanged and is the whole reason this gate is trustworthy); what
    # changes is that the remedy names the real fault. The gate itself distinguishes them (so the
    # MCP twin gets the same two answers from the same place); this handler covers only the case
    # where the read raised before any gate could be built.
    from ..progress_events import (FAULT_UNKNOWN, review_log_path, review_read_error_message,
                                   review_read_error_unblock, ship_review_gate)
    surface = None
    try:
        surface = _load_surface(args.path)
        gate = ship_review_gate(surface, run_id=args.run or None)
    except Exception:
        # SECRET-SAFETY (R3's bar, binding on the CLI since R4): `{exc}` is NOT echoed. This line
        # used to print it, and a parse fault can quote the offending log line — a `review_verdict`
        # line carries FINDINGS text, which may quote project content. The fault is named by KIND
        # and located by PATH instead; neither is derived from the log's bytes.
        path = review_log_path(surface)
        print(review_read_error_message(FAULT_UNKNOWN, path)
              + f"  → to unblock: {review_read_error_unblock(path)}")
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
    sessions = list_sessions(surface.state, root=surface.root)
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


def cmd_windows(args: argparse.Namespace) -> int:
    # MS.S2 — list the LIVE Claude Code windows on this repo (each window is its own MCP process),
    # so two windows are no longer invisible to each other. Read-only: the caller's own window
    # self-registers (transient registry upkeep, ungated — never routes through the WriteGate), and
    # stale (dead-pid) windows are shown once then pruned. Degrade-clean: any registry problem lists
    # nothing rather than tracebacking. Distinct from `sessions` (pipeline RUNS).
    from ..session import short_id
    from .. import session_registry as SR
    from ..repo_identity import worktree_label
    surface = _load_surface(args.path)
    try:
        SR.touch(surface)                    # register self so `windows` always shows this window
    except Exception:
        pass
    rows = SR.list_sessions(surface)
    if not rows:
        print("mokata windows: no live sessions on record yet.")
        return 0
    live = sum(1 for r in rows if r.alive)
    stale = len(rows) - live
    tail = f", {stale} stale" if stale else ""
    print(f"mokata windows — {live} live{tail}:")
    for r in rows:
        status = "live " if r.alive else "stale"
        phase = f"phase: {r.phase}" if r.phase else "phase: —"
        started = f"started {r.started_at}" if r.started_at else "started —"
        dead = " (dead pid)" if not r.alive else ""
        wt = worktree_label(r.repo_root) if r.repo_root else "main"    # WT.S1: main | rel worktree
        branch = f"  branch: {r.branch}" if r.branch else ""           # WT.S4: the run's binding
        scope = f"  scope: {r.scope}" if r.scope else ""
        print(f"  {short_id(r.session_id):10} {status}  {started}  {phase}  wt: {wt}"
              f"{branch}{scope}{dead}")
    # WT.S1 — a ONE-TIME human-gated worktree offer when a live sibling is on this repo (never
    # creates anything). Reuses the rows just listed; degrade-clean.
    try:
        from .. import session_worktree as SW
        SW.emit_offer_once(surface, rows=rows)
    except Exception:
        pass
    return 0


def cmd_worktree_create(args: argparse.Namespace) -> int:
    # WT.S1 — create a git worktree to isolate THIS session's working tree. The ONLY durable action
    # in WT and explicitly HUMAN-GATED (P2/P14): it recommends a topic-aware branch/dir name and
    # confirms via the standard read_yes_no path (fail-closed non-TTY) unless `--yes`. Refuses
    # politely outside a git repo. Returns 0 when created, 1 when nothing was created.
    from .. import session_worktree as SW
    surface = _load_surface(args.path)
    res = SW.create_worktree(surface, topic=args.topic, assume_yes=args.yes, out=print)
    return 0 if res.created else 1


def cmd_worktree_list(args: argparse.Namespace) -> int:
    # WT-LIST (FR-WT-1) — READ-ONLY: every git worktree of this repo joined to its owning session,
    # with a staleness verdict. It creates nothing, removes nothing, and deliberately does NOT
    # `git worktree prune` (that is FR-WT-2/3, 0.0.17) — a lister must not mutate what it lists.
    # It also does not `SR.touch()`: unlike `windows`, this surface registers no session.
    # Renders the SAME report the `worktree_list` MCP tool returns, so the two cannot disagree.
    # Degrade-clean: not a git repo / git absent / an unreadable registry ⇒ one honest line and a
    # CLEAN exit (0) — the answer is a legitimate read-only answer, not a command failure.
    from ..worktree_list import build_worktree_report
    surface = _load_surface(args.path)
    report = build_worktree_report(surface)
    print(report.render(ascii_only=getattr(args, "ascii", False)))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    # Stage 50 — PREVIEW where a run resumes (read-only): the phase + the gate that still
    # applies. mokata never auto-runs the pipeline; the gates hold on resume.
    from ..pipeline import PHASE_GATES
    from ..progress import build_progress
    from ..run_resolver import resolve_run, unresolved_reason
    surface = _load_surface(args.path)
    # RUN-ID-DRIFT — resume reads THE resolver. An ambiguous repo is told to name a run rather than
    # handed an arbitrary one to resume, which is how a resume landed in someone else's pipeline.
    res = resolve_run(surface.root, run_id=args.id)
    rid = res.run_id
    if rid is None:
        print("mokata resume: no run to resume — " + unresolved_reason(res, flag="--id"))
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
    # RUN-ID-DRIFT — the same `--run` spelling as `record-review` / `review-status` / `progress`.
    # A mark is a WRITE into a run's history, so when resolution is ambiguous this is the remedy
    # the refusal names; without it the user would be told to name a run they could not name.
    p_mark.add_argument("--run", default=None,
                        help="the run this stage mark belongs to (default: the run resolved for "
                             "this session)")
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
    # REVIEW-FIX.R1 — the record-key, mirrored on `review-status` (the read-key). Same spelling as
    # `progress --run` / `watch --run`.
    p_rec.add_argument("--run", default=None,
                       help="the run this verdict belongs to (default: the run resolved for this "
                            "session; ship reads back the SAME run)")
    p_rec.set_defaults(func=cmd_progress_record_review)

    p_rs = prog_sub.add_parser(
        "review-status", parents=[common],
        help="print ship's review gate from the persisted verdict; exits non-zero when ship "
             "must BLOCK (no verdict, or review failed)",
    )
    p_rs.add_argument("--run", default=None,
                      help="read THIS run's verdict (default: the run resolved for this session; "
                           "an unresolvable run BLOCKS rather than reading another run's verdict)")
    p_rs.set_defaults(func=cmd_progress_review_status)

    p_sessions = sub.add_parser(
        "sessions", parents=[common],
        help="list past + active runs (id, phases passed, resume point); read-only",
    )
    p_sessions.set_defaults(func=cmd_sessions)

    p_windows = sub.add_parser(
        "windows", parents=[common],
        help="list the live Claude Code windows on this repo (id, started, alive/stale, phase); "
             "read-only. Distinct from `sessions`, which lists pipeline runs.",
    )
    p_windows.set_defaults(func=cmd_windows)

    p_worktree = sub.add_parser(
        "worktree", parents=[common],
        help="isolate a session's working tree with a git worktree (human-gated; never automatic)",
    )
    wt_sub = p_worktree.add_subparsers(dest="worktree_command", required=True)
    p_wt_create = wt_sub.add_parser(
        "create", parents=[common],
        help="create a git worktree for this session (asks the scope, recommends a name, confirms)",
    )
    p_wt_create.add_argument("topic", nargs="?", default=None,
                             help="what this session is working on (the scope; asked if omitted)")
    p_wt_create.add_argument("--yes", action="store_true",
                             help="approve non-interactively (the durable git worktree add)")
    p_wt_create.set_defaults(func=cmd_worktree_create)
    p_wt_list = wt_sub.add_parser(
        "list", parents=[common],
        help="list this repo's git worktrees joined to their sessions, with a staleness "
             "verdict (read-only; creates, prunes and removes nothing)",
    )
    p_wt_list.add_argument("--ascii", action="store_true",
                           help="ASCII-only output (no box-drawing/arrow glyphs)")
    p_wt_list.set_defaults(func=cmd_worktree_list)

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
    "RECORD_REVIEW_FAILED_EXIT",
    "review_recorded_line",
    "review_runless_line",
    "review_record_failed_line",
    "cmd_progress",
    "cmd_progress_mark",
    "cmd_progress_record_review",
    "cmd_progress_review_status",
    "cmd_sessions",
    "cmd_windows",
    "cmd_worktree_create",
    "cmd_worktree_list",
    "cmd_resume",
    "cmd_watch",
    "cmd_govern",
]
