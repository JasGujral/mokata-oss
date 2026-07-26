"""spec — emit the spec (SI-DEV.0): the CLI half of the only path a spec reaches disk.

`emitted_spec` is the key the `spec-persisted` run-state gate reads before it will let
implementation proceed, and `spec_corpus` is the corpus `spec-check`'s regression guard reads.
Until SI-DEV.0 NEITHER had a writer any human or model could reach: the gate blocked forever, and
the guard always found nothing. This command (and its `spec_emit` MCP twin) is that writer.

Both surfaces route through `engine/emit.py` — the completeness gate first (an acceptance criterion
with no test refuses the emit and writes nothing), then the human WriteGate. Same gates the engine
pipeline runs; re-invoked, never reimplemented.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import argparse
import json
import sys

from ._common import AuditLedger, _load_surface


def _read_payload(args: argparse.Namespace) -> dict:
    """The spec, as JSON, from `--file` or stdin (`-`). One shape, shared with the MCP tool:

        {"title": …, "approach": …?, "domains": [...]?,
         "criteria": [{"id": …, "text": …}], "tests": [{"name": …, "ac_ids": [...]}]}"""
    if args.file == "-":
        raw = sys.stdin.read()
    else:
        with open(args.file, "r", encoding="utf-8") as fh:
            raw = fh.read()
    return json.loads(raw)


def _run_scoped_store(surface):
    """The state store scoped to the run the HOOK would enforce — not to this shell's own session.

    `mokata spec emit` runs in a SEPARATE process from the agent's session (a human at a terminal,
    exactly like `mokata approve` and `mokata gate override`). Its own `current_run_id()` would be a
    fresh uuid4, so a spec written through `surface.state` would land at `emitted_spec__<this
    shell>` — a run the `spec-persisted` gate is not looking at — and would ALSO leave a second run's
    state in the repo, which makes the hook's run resolution AMBIGUOUS and silently turns every gate
    OFF. Emitting a spec would then unblock nothing and disable everything.

    So the run is resolved by the hook's OWN resolver (`gate_hook.resolve_run`, the discipline
    `cli_commands/gate.py` already uses for the override): the spec lands on the run being gated.
    `(store, run_id, error)` — `error` is a message when the run cannot be resolved WITHOUT
    guessing, and the caller must refuse rather than pick a window."""
    from ..gate_hook import resolve_run
    from ..session_state import scoped_store
    from ..state import StateStore
    from ..tdd_state import state_dir

    run = resolve_run(surface.root)
    if run.ambiguous:
        return None, None, (
            f"{len(run.candidates)} mokata runs have state in this repo and none is pinned, so "
            f"mokata cannot tell which run this spec belongs to and will not guess (emitting it "
            f"onto the wrong run would leave the right one still blocked).\n"
            f"       Pin the run with MOKATA_SESSION_ID, or emit through the harness "
            f"(/mokata:spec), which always knows its own run.")
    if run.run_id is None:
        # No run has pipeline state — a standalone spec (no brainstorm ran). This shell's own
        # session IS the run; `surface.state` is already scoped to it.
        return surface.state, None, None
    return scoped_store(StateStore(state_dir(surface.root)), run.run_id), run.run_id, None


def cmd_spec_emit(args: argparse.Namespace) -> int:
    """Emit the spec through the real gates — completeness, then the human write gate."""
    from ..engine.emit import emit_spec, spec_from_payload
    from ..prompt import read_yes_no

    surface = _load_surface(args.path)
    try:
        payload = _read_payload(args)
        spec, tests = spec_from_payload(payload)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    store, run_id, err = _run_scoped_store(surface)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    # GR-PA-WIRE — the prior-art step-ran gate at the CLI emit seam (mirrors the gate-computation
    # placement `knowledge.py` uses for spec-check). Reads the CHOSEN approach's step-ran evidence
    # from the DURABLE `approved_approach` Handoff on the run-scoped store — the SAME key and SAME
    # `check_prior_art_ran` verdict the MCP `spec_emit` reads (run_id == session_id, so the run-scoped
    # store resolves the exact file the agent wrote). Degrade-clean: no persisted approach (a
    # standalone spec) → no gate. Fail-CLOSED: a legacy/missing `handoff.prior_art` refuses, naming
    # the road back. NOTE the deliberate seam difference from GR.S3, whose emit refusal reads
    # `brainstorm_progress`; the Handoff is the durable approval record (see govern/prior_art_gate).
    from ..brainstorm import load_approved_approach
    from ..govern.prior_art_gate import handoff_prior_art_gate
    handoff = load_approved_approach(store)
    if handoff is not None:
        pa_gate = handoff_prior_art_gate(handoff)
        if pa_gate.refused:
            print(f"[BLOCK] prior-art — {pa_gate.render()}")
            print("\nRe-run the prior-art pass for the chosen approach and re-approve, then emit "
                  "again. Nothing was written.")
            return 1

    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    # The human sees the whole spec — every AC and the test that covers it — before the question.
    # Off a TTY `read_yes_no` fails CLOSED (P2): a non-interactive shell cannot emit by accident;
    # a genuinely non-interactive HUMAN flow passes `--yes`.
    out = emit_spec(surface, spec, tests, ledger=ledger, store=store, run_id=run_id or "",
                    assume_yes=args.yes,
                    confirm=None if args.yes
                    else (lambda text: read_yes_no(text, f"Emit the spec '{spec.title}'?")))

    if out.blocked_by_completeness:
        print(f"[BLOCK] completeness — {out.reason}")
        for ac in out.unmapped:
            print(f"  unmapped: {ac} — no test covers it")
        print("\nMap every acceptance criterion to a test, then emit again. Nothing was written.")
        return 1
    if not out.committed:
        print(f"emit declined at the write gate — nothing was written ({out.reason}).")
        return 1

    where = f" (run {run_id[:8]})" if run_id else ""
    print(f"spec emitted: '{spec.title}' — {out.ac_count} acceptance criteria, all mapped to "
          f"tests.")
    print(f"  saved as this run's spec{where}, and recorded in the shared spec corpus "
          f"({out.corpus_size} spec(s)).")
    print("  implementation is unblocked once a failing test is on record (/mokata:test).")
    return 0


def cmd_spec_show(args: argparse.Namespace) -> int:
    """The persisted spec for the run being gated (read-only) — resolved the same way `emit` does,
    so `show` never reports on a different run than the one `emit` wrote to."""
    from ..engine.spec_gate import load_emitted_spec

    surface = _load_surface(args.path)
    store, run_id, err = _run_scoped_store(surface)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    # RUN-REG — legible no-run recovery: distinguish "no tracked run at all" (nothing to attach a
    # spec to — the conversational-brainstorm repro) from "a run is tracked but no spec is emitted
    # yet". The first names how to START/attach a tracked run; the second, how to emit.
    if run_id is None:
        print("no tracked run in this repo — mokata has nothing to attach a spec to yet. Start a "
              "tracked run with /mokata:brainstorm (it registers the run) or resume one with "
              "/mokata:resume, then emit the spec (/mokata:spec).")
        return 0
    spec = load_emitted_spec(store)
    if spec is None:
        print("no spec is emitted for this run — draft one and emit it (/mokata:spec, or "
              "`mokata spec emit --file <spec.json>`).")
        return 0
    print(f"spec: {spec.title}")
    if spec.approach:
        print(f"approach: {spec.approach}")
    if spec.domains:
        print(f"domains: {', '.join(spec.domains)}")
    for c in spec.criteria:
        print(f"  {c.id}: {c.text}")
    return 0


def cmd_spec_amend(args: argparse.Namespace) -> int:
    """SI-DEV — the FORCED PHASE REGRESSION. Not a text edit to the spec: the run leaves `develop`,
    the new scope re-earns every gate, and the spec lands as vN+1 with vN superseded."""
    from ..engine.amend import abort_amend, begin_amend, finish_amend
    from ..engine.emit import spec_from_payload
    from ..prompt import read_yes_no

    surface = _load_surface(args.path)
    store, run_id, err = _run_scoped_store(surface)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    if run_id is None:
        print("error: no mokata run has state in this repo — there is no spec to amend.",
              file=sys.stderr)
        return 1

    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)

    if args.abort:
        if abort_amend(store, run_id, ledger=ledger):
            print("amend abandoned — the run returns to its existing spec, and development "
                  "writes are unblocked. Nothing was changed.")
            return 0
        print("no amendment is in progress — nothing to abort.")
        return 0

    if not args.file:
        print("error: --file is required (the WHOLE amended spec as JSON, not a patch); "
              "or --abort to abandon an amendment in progress.", file=sys.stderr)
        return 1

    try:
        spec, tests = spec_from_payload(_read_payload(args))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    from ..knowledge import KnowledgeLayer
    layer = KnowledgeLayer.from_surface(surface)

    # THE REGRESSION happens here — before a single gate is judged. From this moment development
    # writes are blocked, and they stay blocked if the amendment turns out to be incomplete.
    plan = begin_amend(surface, spec, tests, run_id=run_id, store=store, reason=args.reason,
                       item=args.item, ledger=ledger, layer=layer)
    if not plan.ok:
        print(f"[BLOCK] {plan.gate} — {plan.reason_text}")
        for ac in plan.unmapped:
            print(f"  unmapped: {ac} — no test covers it")
        print("\nThe run is REGRESSED to the SPEC phase: development writes stay blocked until a "
              "correct amendment lands (or `mokata spec amend --abort`).")
        return 1

    print(f"mokata · spec amend  v{plan.from_version} -> v{plan.to_version}")
    print(plan.diff.render())
    if plan.scope_widened:
        print("  ! this WIDENS the scope — the blast radius was re-computed (Lens-1):")
        if plan.impact is not None:
            from ..brainstorm_impact import render_impacts
            print("    " + render_impacts([plan.impact]).replace("\n", "\n    "))
        else:
            print("    (impact could not be computed — saying so rather than assuming it is small)")
    if plan.red_owed:
        print(f"  new criteria will OWE a failing test: {', '.join(plan.red_owed)}")
    print()

    outcome = finish_amend(
        plan, store=store, ledger=ledger, assume_yes=args.yes,
        confirm=None if args.yes else (
            lambda text: read_yes_no(text, f"Amend the spec to v{plan.to_version}?")))

    if not outcome.committed:
        print(f"amend declined — nothing was written ({outcome.reason}).")
        print("The run stays regressed. Abandon it with: mokata spec amend --abort")
        return 1

    print(f"spec amended to v{outcome.version} — v{plan.from_version} superseded (kept), the diff "
          f"is on the audit ledger.")
    if outcome.red_owed:
        print(f"  develop resumes, but implementation of the new criteria is BLOCKED until "
              f"{', '.join(outcome.red_owed)} "
              f"{'is' if len(outcome.red_owed) == 1 else 'are'} RED (/mokata:test).")
    else:
        print("  develop resumes from the last passed gate.")
    return 0


def register(sub, common):
    p_spec = sub.add_parser(
        "spec", parents=[common],
        help="emit / amend the spec (human-gated) — the persisted spec implementation is gated on",
    )
    ssub = p_spec.add_subparsers(dest="spec_command", required=True)

    p_emit = ssub.add_parser(
        "emit", parents=[common],
        help="emit a spec: completeness gate (every AC maps to a test), then the human write gate")
    p_emit.add_argument("--file", required=True,
                        help="the spec as JSON (title + criteria + tests); '-' reads stdin")
    p_emit.add_argument("--yes", action="store_true",
                        help="skip the interactive write-gate confirmation (a HUMAN's own "
                             "non-interactive flow: CI, a script)")
    p_emit.set_defaults(func=cmd_spec_emit)

    p_amend = ssub.add_parser(
        "amend", parents=[common],
        help="amend the spec — a FORCED PHASE REGRESSION (develop → SPEC): the new scope re-earns "
             "completeness + blast-radius + human approval, lands as vN+1, and owes RED")
    p_amend.add_argument("--file", default="",
                         help="the WHOLE amended spec as JSON (not a patch); '-' reads stdin")
    p_amend.add_argument("--reason", default="",
                         help="why the approved scope must change (recorded to the audit ledger)")
    p_amend.add_argument("--item", default="",
                         help="the id of the deferred item being released, if any (e.g. D1)")
    p_amend.add_argument("--abort", action="store_true",
                         help="abandon an amendment in progress and unblock development writes "
                              "(changes no spec; ledgered)")
    p_amend.add_argument("--yes", action="store_true",
                         help="skip the interactive re-confirmation (a HUMAN's own "
                              "non-interactive flow)")
    p_amend.set_defaults(func=cmd_spec_amend)

    p_show = ssub.add_parser("show", parents=[common],
                             help="show this run's persisted spec (read-only)")
    p_show.set_defaults(func=cmd_spec_show)


__all__ = ["cmd_spec_amend", "cmd_spec_emit", "cmd_spec_show", "register"]
