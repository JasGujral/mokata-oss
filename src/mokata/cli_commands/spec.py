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
         "criteria": [{"id": …, "text": …}], "tests": [{"name": …, "ac_ids": [...]}],
         "tasks": [{"id", "title", "ac_ids", "scope"}], "clarifications"?: [...] (<= 5),
         "assumptions"?: [...]}"""
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


def _consent_criteria(spec: "object", payload: "object") -> list:
    """The criteria as the CONSENT identity hashes them — `{id, text}`, plus the author's `anchors`
    EXACTLY as sent when the payload carried any (SPEC-ANCHOR, 0.0.23 D4). The MCP `spec_emit` hashes
    its raw `criteria` argument, so carrying the raw anchors here keeps one spec one write across
    both surfaces; an anchorless spec hashes byte-identically to before."""
    raw = payload.get("criteria") if isinstance(payload, dict) else None
    raw = raw if isinstance(raw, list) else []
    out = []
    for i, c in enumerate(spec.criteria):
        d = {"id": c.id, "text": c.text}
        src = raw[i] if i < len(raw) and isinstance(raw[i], dict) else {}
        if src.get("anchors"):
            d["anchors"] = src["anchors"]
        # 0.0.25 Lane D — a UI criterion's form is part of what the human approves (raw, as sent:
        # the MCP surface hashes its raw `criteria`, so both surfaces stay one identity).
        for key in ("kind", "states"):
            if src.get(key):
                d[key] = src[key]
        out.append(d)
    return out


def cmd_spec_emit(args: argparse.Namespace) -> int:
    """Emit the spec through the real gates — completeness, then the human write gate."""
    from .. import approval
    from ..engine.emit import EMIT_TOOL, emit_spec, spec_for_emit
    from ..prompt import read_yes_no

    surface = _load_surface(args.path)
    try:
        payload = _read_payload(args)
        spec, tests = spec_for_emit(payload)
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
                 "criteria": _consent_criteria(spec, payload),
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
    # L7b — with `settings.specs.commit` on, approving ALSO writes the tracked
    # `.mokata/specs/<run>.json`; say so before the question, so it is approved knowingly.
    from ..committed_specs import committed_path_for
    from ..engine.emit import _reemit_run
    committed = committed_path_for(surface.root, _reemit_run(store, run_id or ""))
    if committed:
        print(f"approving also commits the spec to {committed} (settings.specs.commit — tracked "
              f"in git).")
    out = emit_spec(surface, spec, tests, ledger=ledger, store=store, run_id=run_id or "",
                    assume_yes=args.yes, confirm=None if args.yes else _confirm)

    if out.blocked_by_completeness:
        # SPEC-STANDALONE residual (2) (0.0.23 D6) — the gate's OWN rendering (`GateResult.render`:
        # verdict, coverage, unmapped ids, advisories, the approved direction or the standalone
        # note, and the one action that unblocks it), not a second hand-worded copy of it.
        print(out.completeness.render())
        print("\nMap every acceptance criterion to a test, then emit again. Nothing was written.")
        return 1
    # SQ.S1 + SQ-REGRESS (0.0.23 D5) — the spec-quality battery refused: its findings, and the road.
    if out.blocked_by_quality:
        print(out.quality.render())
        if out.quality.status == "regress":
            print("\nThe run is back at BRAINSTORM: re-open the approach, update the plan, "
                  "re-approve it, then emit the spec again. No spec was written.")
        elif out.quality.status == "escalate":
            print("\nThe revise loop is exhausted — these findings need a HUMAN decision before "
                  "the spec can be emitted. No spec was written.")
        else:
            print("\nRevise the spec to answer each finding, then emit again. No spec was written.")
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
    # SPEC-ANCHOR S3 — the gate's advisories (never a block), printed only when there are any.
    for w in out.warnings:
        print(f"  ⚠ {w}")
    if out.superseded:
        print(f"  this is v{out.version}: v{out.version - 1} was superseded, not overwritten — "
              f"it is kept at {out.superseded} and named on the audit ledger.")
    print(f"  saved as this run's spec{where}, and recorded in the shared spec corpus "
          f"({out.corpus_size} spec(s)).")
    if committed:
        print(f"  committed copy: {committed} — commit it with your change so CI checks it.")
    print("  implementation is unblocked once a failing test is on record (/mokata:test).")
    return 0


def _version_line(version: int, current: int) -> str:
    """SPEC-HISTORY rider — WHICH spec this is. Shared with the MCP `spec_show` block."""
    if version and version != current:
        return f"version: v{version} (superseded — current is v{current})"
    return f"version: v{version or 1} (current)"


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
    # SPEC-HISTORY (0.0.23 D6) — `--versions` lists the run's spec history; `--version N` reads a
    # superseded spec back from its archive. Same reader as the MCP `spec_show` (engine/spec_history).
    from ..engine import spec_history as SH
    if getattr(args, "versions", False):
        rows = SH.spec_versions(store, run_id)
        if not rows:
            print(NO_SPEC_RECOVERY)
            return 0
        print(f"spec history (run {run_id[:8]}):")
        for line in SH.render_versions(rows):
            print(line)
        return 0
    want = getattr(args, "version", None)
    version = SH.current_version(store)
    if want:
        row = SH.spec_at_version(store, run_id, int(want))
        if row["malformed"]:
            print(f"error: v{want} of this run's spec is present but unreadable at {row['key']} — "
                  f"it is kept, not lost; inspect or restore it by hand.", file=sys.stderr)
            return 1
        if row["spec"] is None:
            print(f"error: this run has no spec v{want} (current is v{version}; "
                  f"`mokata spec show --versions` lists what exists).", file=sys.stderr)
            return 1
        spec, version = row["spec"], int(want)
    else:
        spec = load_emitted_spec(store)
    if spec is None:
        print(NO_SPEC_RECOVERY)
        return 0
    print(f"spec: {spec.title}")
    print(_version_line(version, SH.current_version(store)))
    if spec.approach:
        print(f"approach: {spec.approach}")
    if spec.domains:
        print(f"domains: {', '.join(spec.domains)}")
        from ..domains import domain_routing_line
        routing = domain_routing_line(spec.domains)
        if routing:
            print(routing)
    for c in spec.criteria:
        print(f"  {c.id}: {c.text}")
    # SPEC-ANCHOR S2 — where each AC lives, so a test is placed without exploring for it.
    from ..engine.spec_anchor import ac_anchor_lines
    for line in ac_anchor_lines(spec):
        print(f"  → {line}")
    # R2 + R3 — the approved plan (hash + tasks) and the capped clarify record, from the CURRENT
    # spec's persisted dict (a superseded version shows its ACs only).
    if not want:
        from ..engine.spec_gate import SPEC_STATE_KEY
        data = store.read(SPEC_STATE_KEY)
        if isinstance(data, dict):
            for line in _plan_lines(data):
                print(line)
    return 0


# R2 (0.0.26) — the exit code when a stage refuses because the plan on record is not the approved
# one (hash mismatch, or a spec approved before tasks[] + the hash existed). 3, not 1: nothing
# failed to run — the remedy is re-approval, and a script must be able to tell the two apart.
PLAN_REFUSED_EXIT = 3


def _plan_lines(data: dict) -> list:
    """`spec show`'s plan block: the plan hash, each task (with the cursor's marks), the capped
    clarify record and the stated assumptions."""
    from ..plan_tasks import (ASSUMPTIONS_KEY, CLARIFICATIONS_KEY, CLARIFY_CAP, PLAN_HASH_KEY,
                              TASKS_KEY, clarify_from_state, tasks_from_state)
    lines = []
    if data.get(PLAN_HASH_KEY):
        lines.append(f"plan: {data[PLAN_HASH_KEY]}")
    for t in tasks_from_state(data.get(TASKS_KEY)):
        deps = f" after {', '.join(t.depends_on)}" if t.depends_on else ""
        lines.append(f"  task {t.id}: {t.title} [{', '.join(t.ac_ids)}] scope "
                     f"{', '.join(t.scope)}{deps}")
    qs, asm = clarify_from_state(data.get(CLARIFICATIONS_KEY), data.get(ASSUMPTIONS_KEY))
    if qs:
        lines.append(f"clarifications ({len(qs)}/{CLARIFY_CAP}):")
        for q in qs:
            lines.append(f"  {q.id}: {q.question} -> {q.answer or '(unanswered)'} "
                         f"(recommended: {q.recommended})")
    for a in asm:
        lines.append(f"  assumption {a.id}: {a.text}" + (f" (for {a.for_question})"
                                                          if a.for_question else ""))
    return lines


def cmd_spec_task(args: argparse.Namespace) -> int:
    """R2 — develop works TASK BY TASK: `status` shows the plan and the active task; `start <id>`
    makes a task active (its dependencies must be done); `done <id>` closes it. The cursor is
    run-state (UNGATED, the tier of the TDD record); every move ATTESTS the plan hash first and
    refuses (exit 3) on a plan that is not the approved one."""
    from ..engine.spec_gate import SPEC_STATE_KEY
    from ..plan_tasks import (PLAN_HASH_KEY, TASKS_KEY, active_task, attest, move_cursor,
                              read_cursor, tasks_from_state)

    surface = _load_surface(args.path)
    store, run_id, err = _run_scoped_store(surface, getattr(args, "run", None) or None)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    if run_id is None:
        print(NO_TRACKED_RUN_RECOVERY)
        return 1
    data = store.read(SPEC_STATE_KEY)
    if not isinstance(data, dict):
        print(NO_SPEC_RECOVERY)
        return 1
    attestation = attest(data)
    if not attestation.ok:
        print(f"mokata spec task: REFUSED — {attestation.reason}.", file=sys.stderr)
        return PLAN_REFUSED_EXIT
    tasks = tasks_from_state(data.get(TASKS_KEY))
    action = args.task_action
    if action in ("start", "done"):
        if not args.task_id:
            print(f"error: name the task: mokata spec task {action} <id>", file=sys.stderr)
            return 1
        try:
            move_cursor(surface.root, run_id, tasks, args.task_id, done=(action == "done"),
                        plan=data[PLAN_HASH_KEY])
        except ValueError as exc:
            print(f"mokata spec task: {exc}", file=sys.stderr)
            return 1
    cursor = read_cursor(surface.root, run_id)
    active = active_task(tasks, cursor)
    print(f"plan {data[PLAN_HASH_KEY][:19]}… (attested) — run {run_id[:8]}")
    for t in tasks:
        mark = "x" if t.id in cursor.done else (">" if active and t.id == active.id else " ")
        print(f"  [{mark}] {t.id}: {t.title} — scope {', '.join(t.scope)}")
    print(f"active task: {active.id} — implementation writes are held to its scope"
          if active else "every task is done — writes stay inside the plan's surface")
    return 0


#: L7b — the consent identity of a plan migration (the `mokata approve` proposal's tool name).
MIGRATE_TOOL = "spec_migrate"


def migration_plan(data: dict) -> list:
    """The one-task plan `spec migrate` proposes for a spec approved before tasks[] existed: ONE
    task covering every acceptance criterion, scoped to the spec's own `scope.authorized` (or the
    whole repo when it drew none — the same surface the spec already allowed)."""
    from ..plan_tasks import Task
    from ..spec_scope import SCOPE_KEY, scope_from_dict
    ac_ids = tuple(str(c["id"]) for c in (data.get("criteria") or [])
                   if isinstance(c, dict) and c.get("id"))
    scope = scope_from_dict(data.get(SCOPE_KEY))
    authorized = tuple(scope.authorized) if scope is not None else ()
    return [Task(id="T1", title=str(data.get("title") or "the approved spec"), ac_ids=ac_ids,
                 scope=authorized or ("*",))]


def cmd_spec_migrate(args: argparse.Namespace) -> int:
    """L7b/R2 — re-approve a spec approved BEFORE 0.0.26's task plan, without hand-building JSON.

    Proposes a one-task plan (`migration_plan`), shows it, and commits it through the ONE spec
    committer (`engine/emit.commit_spec`, WriteGate + ledger), which seals the plan hash — the run's
    writes are then allowed again. The approval is the usual one: a human answering at a TTY, a
    human's own `--yes`, or (off a TTY) a proposal approved with `mokata approve <id>` and this
    command re-run. Refuses loudly when the run already has a task plan (that is `spec amend`)."""
    from .. import approval
    from ..engine.emit import commit_spec, note_plan_approved
    from ..engine.spec import Spec
    from ..engine.spec_gate import SPEC_STATE_KEY
    from ..govern import WriteGate
    from ..plan_tasks import TASKS_KEY
    from ..prompt import read_yes_no
    from ..session_state import scoped_store
    from ..state import StateStore
    from ..tdd_state import state_dir

    surface = _load_surface(args.path)
    run_id = (args.run_id or "").strip()
    store = scoped_store(StateStore(state_dir(surface.root)), run_id)
    data = store.read(SPEC_STATE_KEY)
    if not isinstance(data, dict):
        print(f"error: run {run_id} has no readable spec — nothing to migrate "
              f"(`mokata sessions` lists runs).", file=sys.stderr)
        return 1
    if isinstance(data.get(TASKS_KEY), list) and data[TASKS_KEY]:
        print(f"error: run {run_id}'s spec already has a task plan — `spec migrate` only adds one "
              f"to a spec approved before 0.0.26. To change the plan, use `mokata spec amend`.",
              file=sys.stderr)
        return 1
    tasks = migration_plan(data)
    spec = Spec.from_dict(data)
    spec.tasks = tasks
    t = tasks[0]
    preview = (f"migrate run {run_id}: '{spec.title}' gets a task plan (0.0.26) — the criteria and "
               f"scope are unchanged.\n  task {t.id}: {t.title} [{', '.join(t.ac_ids)}] scope "
               f"{', '.join(t.scope)}\n  approving seals the plan hash; this run's implementation "
               f"writes are then allowed again.")
    print(preview)
    margs = {"path": surface.root, "run": run_id, "tasks": [x.to_dict() for x in tasks]}
    pid = approval.proposal_id_for(run_id, approval.content_hash(MIGRATE_TOOL, margs))
    asked: list = []

    def _confirm(text: str) -> bool:
        granted = approval.redeem(surface.root, pid, tool=MIGRATE_TOOL, args=margs, run_id=run_id)
        if granted.granted:
            return True
        decision = read_yes_no(text, f"Add this task plan to run {run_id}?")
        asked.append(decision)
        return bool(decision)

    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    committed, reason, _size = commit_spec(store, spec, gate=WriteGate(ledger=ledger),
                                           assume_yes=args.yes,
                                           confirm=None if args.yes else _confirm, run_id=run_id)
    if committed:
        note_plan_approved(ledger, run_id, store)
        print(f"migrated: run {run_id} now carries task plan T1 and its plan hash.")
        return 0
    if asked and not asked[-1].answered_by_human:
        p = approval.propose(surface.root, tool=MIGRATE_TOOL, args=margs, run_id=run_id,
                             target="state/emitted_spec.json",
                             summary=f"add a task plan to run {run_id}", preview=preview)
        print(f"waiting for you: approve it with `mokata approve {p.proposal_id}`, then re-run "
              f"`mokata spec migrate {run_id}`. Nothing was written.")
        return EMIT_AWAITING_EXIT
    print(f"migration declined — nothing was written ({reason}).")
    return 1


def cmd_spec_amend(args: argparse.Namespace) -> int:
    """SI-DEV — the FORCED PHASE REGRESSION. Not a text edit to the spec: the run leaves `develop`,
    the new scope re-earns every gate, and the spec lands as vN+1 with vN superseded."""
    from ..engine.amend import abort_amend, begin_amend, finish_amend
    from ..engine.emit import spec_for_emit
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
        spec, tests = spec_for_emit(_read_payload(args))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    from ..knowledge import KnowledgeLayer
    layer = KnowledgeLayer.from_surface(surface)

    # THE REGRESSION happens here — before a single gate is judged. From this moment development
    # writes are blocked, and they stay blocked if the amendment turns out to be incomplete.
    # O14b — gate 2 has no clock, so long work is SAID before it starts rather than looking hung.
    print("mokata · spec amend — running the gates (a scope-WIDENING amendment computes its blast "
          "radius first; on a large repo without a code graph that can take minutes)…", flush=True)
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
            if plan.lens_basis:
                print(f"    basis: {plan.lens_basis}")
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
        # OSS #66 — "MOkata amend spec getting stuck". This said "amend declined" for a run in which
        # NOBODY WAS ASKED, and it leaves the run REGRESSED with development writes blocked, so an
        # unactionable message here is not a cosmetic problem: it is a wedged pipeline whose one
        # sentence names no way out. `outcome.reason` now carries the flag when there was no
        # terminal; the two escape hatches are printed either way.
        print(f"nothing was written — {outcome.reason}.")
        print("The run stays REGRESSED (development writes stay blocked) until a correct amendment "
              "lands. Either:")
        print(f"  re-run this command with --yes   (records the amendment to v{plan.to_version})")
        print("  mokata spec amend --abort        (abandon it; the run returns to its existing "
              "spec)")
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
    p_show.add_argument("--version", type=int, default=None, metavar="N",
                        help="show version N of this run's spec (a superseded one is read back "
                             "from its archive)")
    p_show.add_argument("--versions", action="store_true",
                        help="list every version of this run's spec with its archive key")
    p_show.set_defaults(func=cmd_spec_show)

    p_mig = ssub.add_parser(
        "migrate", parents=[common],
        help="give a spec approved before 0.0.26 its task plan (one task over all its criteria), "
             "human-gated; seals the plan hash so the run's writes are allowed again")
    p_mig.add_argument("run_id", help="the run whose spec to migrate (`mokata doctor` names it)")
    p_mig.add_argument("--yes", action="store_true",
                       help="approve non-interactively (a HUMAN's own flow)")
    p_mig.set_defaults(func=cmd_spec_migrate)

    p_task = ssub.add_parser(
        "task", parents=[common],
        help="the approved plan's tasks: status, start <id>, done <id> — develop works task by "
             "task and writes outside the active task's scope are blocked")
    p_task.add_argument("task_action", choices=("status", "start", "done"))
    p_task.add_argument("task_id", nargs="?", default="",
                        help="the task id (start / done)")
    p_task.add_argument("--run", default="", help="the run (default: the run being gated)")
    p_task.set_defaults(func=cmd_spec_task)


__all__ = ["MIGRATE_TOOL", "PLAN_REFUSED_EXIT", "cmd_spec_amend", "cmd_spec_emit",
           "cmd_spec_migrate", "cmd_spec_show", "cmd_spec_task", "migration_plan", "register"]
