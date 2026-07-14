"""docsync — keep the docs TRUE to the code (`mokata docsync [target]`).

Two targeting modes: `mokata docsync <path>` audits (or, with `--reconcile`, reconciles) exactly
that doc; `mokata docsync` with no target sweeps + drift-detects the whole public doc tree. Two
output modes: AUDIT is read-only (default) — it reports doc↔code discrepancies with a severity and
highlights stale sections, exiting non-zero on a Blocking finding; RECONCILE (`--reconcile`)
previews the fix as a diff and writes ONLY through the human gate (`--yes` to approve
non-interactively). The engine lives in :mod:`mokata.docsync`; this is the thin CLI over it.
"""
from __future__ import annotations

import argparse
import sys


def cmd_docsync(args: argparse.Namespace) -> int:
    from ..docsync import (
        AuditDegradation, audit_doc, docsync_active_line, gather_facts, has_blocking,
        reconcile_doc, render_findings, render_sweep, sweep,
    )

    # Announce the ⛭ activation line so the user always knows docsync is running (SK.S1 surface).
    print(docsync_active_line())
    facts = gather_facts()
    # D5 — carry what the fact-gather could NOT arm into the render, so an audit that checked
    # nothing cannot print "OK". Empty (the normal case) ⇒ the output is unchanged.
    degradation = AuditDegradation.from_facts(facts)
    target = getattr(args, "target", None)

    if getattr(args, "reconcile", False):
        if not target:
            print("error: `docsync --reconcile` needs a target doc "
                  "(`mokata docsync <path> --reconcile`)", file=sys.stderr)
            return 2
        from ..govern import AuditLedger
        from ..cli_commands._common import MOKATA_DIR
        import os
        ledger = AuditLedger.from_mokata_dir(os.path.join(args.path, MOKATA_DIR))
        try:
            result = reconcile_doc(
                target, facts=facts, ledger=ledger,
                confirm=(None if args.yes else read_yes_no_prompt), assume_yes=args.yes)
        except OSError as exc:
            print(f"error: cannot read {target}: {exc}", file=sys.stderr)
            return 1
        if result.written:
            print(f"docsync: reconciled {result.edits} discrepancy(ies) in {target} (approved).")
            return 0
        # Not written: a decline, or nothing reconcilable — say which; write nothing either way.
        print(f"docsync: {result.reason} — {target} left unchanged.")
        # Still surface the audit so the user sees what stands.
        if result.findings:
            print(render_findings(str(target), result.findings, degradation))
        return 1 if has_blocking(result.findings) else 0

    if target:
        try:
            findings = audit_doc(target, facts=facts, degradation=degradation)
        except OSError as exc:
            print(f"error: cannot read {target}: {exc}", file=sys.stderr)
            return 1
        print(render_findings(str(target), findings, degradation))
        return 1 if has_blocking(findings) else 0

    # No target — sweep the doc tree (targeting mode ii).
    results = sweep(root=args.path, facts=facts, degradation=degradation)
    print(render_sweep(results, degradation))
    return 1 if any(has_blocking(v) for v in results.values()) else 0


def read_yes_no_prompt(text: str) -> bool:
    from ..prompt import read_yes_no
    return read_yes_no(text, "Apply these doc edits?")


def register(sub, common):
    p = sub.add_parser(
        "docsync", parents=[common],
        help="audit (or reconcile) docs against the code — read-only audit by default",
    )
    p.add_argument(
        "target", nargs="?", default=None,
        help="a doc to audit/reconcile; omit to sweep + drift-detect the whole doc tree",
    )
    p.add_argument(
        "--reconcile", action="store_true",
        help="propose fixes, preview the diff, and write on approval (human-gated); default is "
             "a read-only audit",
    )
    p.add_argument(
        "--yes", action="store_true",
        help="approve the human-gated reconcile write non-interactively",
    )
    p.set_defaults(func=cmd_docsync)


__all__ = ["cmd_docsync", "register"]
