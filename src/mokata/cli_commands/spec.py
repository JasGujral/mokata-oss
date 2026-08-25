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


# HANDOFF.G1 — the two legible recovery lines `mokata spec show` prints when there is nothing to
# show. Lifted to module constants (VERBATIM, no text change) so the MCP `spec_show` read tool
# returns the SAME sentence the CLI prints instead of paraphrasing it into a second, drifting copy.
# Both are pure guidance: neither carries a byte of spec content, which is what makes them safe to
# hand back on the degrade path (a no-spec answer must never leak the project's words).
NO_TRACKED_RUN_RECOVERY = (
    "no tracked run in this repo — mokata has nothing to attach a spec to yet. Start a "
    "tracked run with /mokata:brainstorm (it registers the run) or resume one with "
    "/mokata:resume, then emit the spec (/mokata:spec).")
NO_SPEC_RECOVERY = (
    "no spec is emitted for this run — draft one and emit it (/mokata:spec, or "
    "`mokata spec emit --file <spec.json>`).")

# A3 (0.0.19) — the exit code for a STAGED, REDEEMABLE WAIT, chosen and not inherited.
#
# NOT 1. `1` is this repo's "the operation failed" code and it is what a genuine refusal still
# returns — a human answered No, or a gate blocked, and the road ended. A wait is a different
# outcome with a different next move, and "nothing was written" must not be indistinguishable to a
# SCRIPT from "nothing was written and here is exactly how to write it". That indistinguishability
# is the whole shape of this row (doc 85 §7g), and returning 1 for both would fix the human-facing
# half while leaving the machine-facing half broken.
#
# NOT 0. Nothing was written. A caller that reads 0 as "the spec is emitted" would be wrong, and a
# CI step that proceeds on it would build against a spec that does not exist.
#
# NOT 2 — argparse owns 2 for usage errors (the same reasoning `RECORD_REVIEW_FAILED_EXIT` gives).
#
# So 3, deliberately non-zero, on the precedent `cmd_branch_protection_check` set at 0.0.18: only a
# caller taught what it means may proceed on it, and every caller that has not been taught reads it
# as a refusal — which is the safe direction for a gate.
EMIT_AWAITING_EXIT = 3


def _run_scoped_store(surface, run_id: "str | None" = None):
    """The state store scoped to the run the HOOK would enforce — not to this shell's own session.

    `mokata spec emit` runs in a SEPARATE process from the agent's session (a human at a terminal,
    exactly like `mokata approve` and `mokata gate override`). Its own `current_run_id()` would be a
    fresh uuid4, so a spec written through `surface.state` would land at `emitted_spec__<this
    shell>` — a run the `spec-persisted` gate is not looking at — and would ALSO leave a second run's
    state in the repo, which makes the hook's run resolution AMBIGUOUS and silently turns every gate
    OFF. Emitting a spec would then unblock nothing and disable everything.

    So the run is resolved by THE resolver (`run_resolver.resolve_run` — the same one the gate hook
    enforces with, and the discipline `cli_commands/gate.py` already uses for the override): the
    spec lands on the run being gated. `(store, run_id, error)` — `error` is a message when the run
    cannot be resolved WITHOUT guessing, and the caller must refuse rather than pick a window.

    HANDOFF.G1 — `run_id` names the run EXPLICITLY (the MCP `spec_show` tool's optional `run`), and
    is threaded straight into the SAME resolver rather than resolved a second way: `resolve_run`
    already short-circuits on an explicit id, so naming a run and letting it be inferred travel one
    code path. `None` (every CLI caller, unchanged) keeps the infer-from-the-repo behaviour byte for
    byte — no CLI surface passes this argument.

    RE-ENTRY — the EVIDENCE rung: when several runs have state but exactly one holds pipeline
    EVIDENCE (an approved approach / an emitted spec), that run is the answer. Without it, going
    back to `/brainstorm` broke this command outright — re-entry REGISTERS a bare
    `pipeline_run__<new>` checkpoint (RUN-REG), which counts as a second "run with state", so
    `mokata spec amend --abort` — the escape hatch the regressed-run answer TELLS the user to run —
    died on "2 mokata runs have state in this repo and none is pinned". Naming a road out that
    errors is the exact P16 failure the awaiting head exists to prevent.

    RUN-ID-DRIFT — that rung is now inside THE resolver rather than in a spec-specific wrapper
    around it, so this command reads the single `run_resolver.resolve_run` every other surface
    reads. One call, one ladder: the EXPLICIT id and the inferred run travel the same code path
    (HANDOFF.G1's property, now structural rather than arranged), and genuine ambiguity — two runs
    that BOTH hold evidence — still REFUSES below. The evidence rung de-ambiguates the re-entry
    shape; it never guesses between two pipelines."""
    from ..run_resolver import resolve_run
    from ..session_state import scoped_store
    from ..state import StateStore
    from ..tdd_state import state_dir

    run = resolve_run(surface.root, run_id=run_id)
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


def _emit_knowledge_layer(surface):
    """The adopted code graph, or None — built the SAME way `MemoryStore.from_surface` builds it
    (`memory/store.py:267`) and the same way the MCP twin does. P4 is an EQUALITY: the emit refusal
    must see exactly the evidence H-6's proposal arm sees, so a gate that quietly passed None would
    decline symbol anchors the arm fires on."""
    try:
        from ..knowledge.layer import KnowledgeLayer
        return KnowledgeLayer.from_surface(surface)
    except Exception:                       # noqa: BLE001 — no graph is a valid answer, not a fault
        return None


def _await_a_human(surface, spec, store, run_id: "str | None", emit_args: dict) -> int:
    """A3 — the TTY-less decline, turned from a DEAD END into a redeemable WAIT.

    The refusal itself was always correct and is unchanged: a non-interactive shell must never emit
    by accident, and a piped `y` is not consent (P2). What was wrong was the SHAPE OF THE WAY OUT.
    Nothing was staged, so there was no `proposal_id`, so `mokata approve <id>` had nothing to
    redeem and `mokata approve --list` showed nothing — and every subsequent implementation write
    then hit `spec-persisted`, whose exits were *emit again* (needing the TTY that was absent) or
    `mokata gate override spec-persisted`. **The only exit reachable without a TTY was the one that
    turns the gate off**, which does not fail closed in practice: it trains users to override.

    So the never-asked path now stages through `approval.propose` — the SAME call
    `mcp/consent.py:_propose` makes, not a second staging path — and the MCP twin's behaviour
    becomes the CLI's. The gate is redeemed, not disabled.

    NO RUN, NO PROPOSAL, AND IT SAYS SO. An approval is bound to one run; with no pipeline run
    resolved the spec is going to this shell's OWN session's key, and a proposal keyed to that
    could never be redeemed by the next process — the human would be walked to a terminal to
    approve something structurally unredeemable, which is worse than the honest refusal. Nothing is
    stranded there either: with no run holding state, `spec-persisted` is not blocking anything
    (`gate_hook.check_write` allows outright). Distinct message, distinct code — §7g holds on this
    branch too."""
    from .. import approval
    from ..awaiting import awaiting_cli_lines
    from ..engine.emit import EMIT_TOOL, preview_content

    if not run_id:
        print("emit declined at the write gate — nothing was written (no human was asked: stdin "
              "is not a TTY).")
        print("  There is no tracked pipeline run here to attach a proposal to, so there would be "
              "nothing for `mokata approve` to redeem.")
        print("  Emit through the harness (/mokata:spec), pin the run with MOKATA_SESSION_ID, or "
              "pass --yes if this is your own non-interactive flow.")
        return 1

    p = approval.propose(
        surface.root, tool=EMIT_TOOL, args=emit_args, run_id=run_id,
        target="state/emitted_spec.json",
        summary=f"emit the spec '{spec.title}' ({len(spec.criteria)} AC(s), all mapped)",
        preview=preview_content(store, spec))
    # The wording is `awaiting.py`'s, not this command's — see `awaiting_cli_lines`. The summary
    # and the preview above are stored for `mokata approve <id>` to show at the human's own
    # terminal; NEITHER is printed here.
    for line in awaiting_cli_lines(p.proposal_id, tool=EMIT_TOOL):
        print(line)
    return EMIT_AWAITING_EXIT


def cmd_spec_emit(args: argparse.Namespace) -> int:
    """Emit the spec through the real gates — completeness, then the human write gate."""
    from .. import approval
    from ..engine.emit import EMIT_TOOL, emit_spec, spec_from_payload
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
        # H-6 S4 — code-anchor freshness, the MCP twin's Gate 1c-ii. A NEW way this command can
        # refuse (contract change, 0.0.16): the approved approach's prior-art CITATIONS must not be
        # anchored to code that has moved since those decisions were recorded. Same Handoff, a
        # different question — 1c asks whether the step ran, this asks whether what it found is
        # still true of the code. Fail-OPEN on absent evidence: no baseline is no opinion.
        from ..govern.code_anchor_gate import handoff_code_anchor_gate
        ca_gate = handoff_code_anchor_gate(handoff, root=surface.root,
                                           layer=_emit_knowledge_layer(surface))
        if ca_gate.refused:
            print(f"[BLOCK] code-anchor — {ca_gate.render()}")
            print("\nRe-read the changed code, decide whether those decisions still hold, then "
                  "re-approve and emit again. Nothing was written.")
            return 1

    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)

    # A3 — the CONSENT IDENTITY of this emit: the same tool name and the same argument shape the
    # MCP `spec_emit` proposes under (`mcp/tools_spec.py`), so one spec on one run is one write
    # whichever surface asked. `approval.propose` hashes it and `approval.redeem` re-derives that
    # hash from these same bytes, which is what makes the approval content-bound — "approve X, then
    # commit Y" stays arithmetically impossible across the CLI too.
    #
    # The proposal id is DERIVED from (run, content), so the re-run that redeems needs no new flag
    # and no id to retype: running the same command against the same spec resolves to the same id.
    emit_args = {"path": surface.root, "title": spec.title,
                 "criteria": [{"id": c.id, "text": c.text} for c in spec.criteria],
                 "tests": [{"name": t.name, "ac_ids": list(t.ac_ids)} for t in tests],
                 "approach": spec.approach, "domains": list(spec.domains),
                 "scope": payload.get("scope") or {}}
    proposal_id = (approval.proposal_id_for(run_id, approval.content_hash(EMIT_TOOL, emit_args))
                   if run_id else "")
    decisions: list = []
    redeemed: list = []

    def _confirm(text: str) -> bool:
        """The human gate for this emit — and the ONE place a human-minted approval is redeemed.

        REDEEM FIRST, and WHERE this runs is the entire safety argument. `WriteGate.submit` calls
        this only after self-protect (layer 0), the trust dial, the secret scan and governance
        enforcement have all passed, and only immediately before the commit — so an approval is
        BURNED at the moment the write actually lands, never by a run that a later layer then
        refuses. It is the CLI's spelling of what `mcp/consent.py:_gated_write` states as
        `human_approved=True`: not a bypass, a statement of fact about a decision a human already
        made, out-of-band, in a process the model was not driving.

        No approval on disk → the ordinary terminal question, byte-identical to before."""
        if proposal_id:
            granted = approval.redeem(surface.root, proposal_id, tool=EMIT_TOOL, args=emit_args,
                                      run_id=run_id)
            if granted.granted:
                redeemed.append(granted.proposal)
                return True
        decision = read_yes_no(text, f"Emit the spec '{spec.title}'?")
        decisions.append(decision)
        return bool(decision)

    # The human sees the whole spec — every AC and the test that covers it — before the question.
    # Off a TTY `read_yes_no` fails CLOSED (P2): a non-interactive shell cannot emit by accident;
    # a genuinely non-interactive HUMAN flow passes `--yes`.
    out = emit_spec(surface, spec, tests, ledger=ledger, store=store, run_id=run_id or "",
                    assume_yes=args.yes, confirm=None if args.yes else _confirm)

    if out.blocked_by_completeness:
        print(f"[BLOCK] completeness — {out.reason}")
        for ac in out.unmapped:
            print(f"  unmapped: {ac} — no test covers it")
        print("\nMap every acceptance criterion to a test, then emit again. Nothing was written.")
        return 1
    # SPEC-REEMIT-CLOBBER — a re-emit onto a run with work in flight is refused and routed to
    # `mokata spec amend`. Its own branch, not the write-gate one below: "declined at the write
    # gate" would tell a human their approval was the problem, when the truth is that no approval
    # could make this the right tool.
    if out.blocked_by_reemit:
        print(f"[BLOCK] re-emit — {out.reason}")
        print("\nUse `mokata spec amend --file <spec.json> --reason \"...\"` to take the same "
              "change through the gates a live spec is owed. Nothing was written.")
        return 1
    if not out.committed:
        # A3 — WHY the gate answered no now decides what happens next, and until this row nothing
        # downstream could tell. A human who said No has answered: that is settled, and converting
        # it into a pending proposal would re-ask a question already decided. A human who was never
        # ASKED has settled nothing, and leaving them with no proposal is the strand this row
        # closes. A refusal from any OTHER layer (a secret, a read-only dial, self-protect) never
        # reaches the reader at all, so `decisions` is empty and it falls through to the message
        # below — a blocked write is not a write awaiting permission (§7e).
        decision = decisions[-1] if decisions else None
        if decision is not None and not decision.answered_by_human:
            return _await_a_human(surface, spec, store, run_id, emit_args)
        print(f"emit declined at the write gate — nothing was written ({out.reason}).")
        return 1

    if redeemed:
        # Close the audit chain the same way `mcp/consent.py:_record` does: proposal-hash → who
        # approved → what landed. Without it the approval and the `write_gate` entry it licensed
        # sit on the ledger unlinked, and `mokata audit` cannot walk from one to the other.
        approval.record_redemption(ledger, redeemed[-1], committed=True)

    where = f" (run {run_id[:8]})" if run_id else ""
    print(f"spec emitted: '{spec.title}' — {out.ac_count} acceptance criteria, all mapped to "
          f"tests.")
    # SPEC-STANDALONE-SILENT — a spec emitted with NO approved brainstorm/refine behind it says so.
    # The identical sentence the MCP `spec_emit` returns, from the one shared builder
    # (`engine.completeness.STANDALONE_NOTE`) — the CLI does not word this for itself. Printed only
    # when it applies, so a brainstorm-attached emit's output is byte-identical to 0.0.15.
    if out.standalone:
        print(f"  {out.standalone_note}")
    if out.superseded:
        print(f"  this is v{out.version}: v{out.version - 1} was superseded, not overwritten — "
              f"it is kept at {out.superseded} and named on the audit ledger.")
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
        print(NO_TRACKED_RUN_RECOVERY)
        return 0
    spec = load_emitted_spec(store)
    if spec is None:
        print(NO_SPEC_RECOVERY)
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
