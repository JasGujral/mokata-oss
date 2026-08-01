"""I2 — universal human-gated writes.

Every durable write — code, memory, or config — goes through one gate: scan for secrets
(I1), then require explicit human approval, then commit, recording the decision in the
audit ledger (I3). A secret is a security block that approval cannot override; a write is
never committed silently.
"""

from __future__ import annotations

from ..prompt import read_yes_no

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, List, Optional, Sequence

from .secrets import Finding, scan
from .trust import TrustPolicy

WRITE_KINDS = ("code", "memory", "config", "send")


@dataclass
class WriteRequest:
    kind: str            # one of WRITE_KINDS
    target: str          # path / destination
    content: str = ""
    actor: str = "agent"
    tool: Optional[str] = None      # WHICH tool/command is writing (K3 trust keys on this)
    surface: Optional[str] = None   # WHERE it came from: "mcp" | "cli" (the trust surface default)
    # UX-CONFIRM (D7a) — the run this write belongs to. Recorded on the ledger entry, and the
    # BOUND on carry-forward: an approval may only be carried within the run it was given in.
    # None (every pre-0.0.16 caller) means the entry records no run AND carry-forward can never
    # fire, so those callers are byte-identical — including in how many times they ask.
    run: Optional[str] = None


@dataclass
class WriteOutcome:
    committed: bool
    aborted: bool
    reason: str
    findings: List[Finding] = field(default_factory=list)
    # TM.S8 — the enforcement verdict when in-scope governance rules were evaluated (None when no
    # rules/action were supplied, so every pre-S8 caller is byte-identical). `overridden` is True
    # when a soft-rule block was cleared by an explicit, ledgered override.
    enforcement: Any = None
    overridden: bool = False
    # MS.S3/B2 — the REAL seq of the gate's `approved` ledger entry (None when declined/blocked or
    # no ledger). Callers record THIS as the approval id instead of predicting `len(ledger)`, so a
    # racing gated write can never be misattributed to another's approval.
    approval_seq: Optional[int] = None


def content_hash_for(req: "WriteRequest") -> str:
    """UX-CONFIRM (D7a) — the hash binding a gate decision to EXACTLY the write the human saw.

    Covers kind + target + content, and nothing else. The identity fields (`actor`, `tool`,
    `surface`, `run`) are deliberately OUT of the pre-image: they say who is asking, not what is
    being written, and folding them in would mean the same artifact re-proposed through a different
    surface no longer matched the approval the human just gave — re-asking for exactly the reason
    D7 exists to remove. `run` bounds the carry separately, in the WHERE rather than in the hash.

    Same construction as `approval.content_hash` (canonical JSON, sorted keys, sha256) so the two
    consent surfaces hash the same way; not the same FUNCTION, because they bind different things —
    that one binds a tool call's arguments, this one binds a durable write's bytes.
    """
    import hashlib
    import json
    blob = json.dumps({"kind": req.kind, "target": req.target, "content": req.content},
                      sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _default_confirm(text: str) -> bool:
    return read_yes_no(text, "Approve this write?")


class WriteGate:
    def __init__(self, ledger: Any = None,
                 trust: Optional[TrustPolicy] = None) -> None:
        self.ledger = ledger
        self.trust = trust            # K3 — per-adapter trust dial (optional)

    def _log(self, req: WriteRequest, decision: str, reason: str) -> Any:
        """Record the gate decision and RETURN the ledger entry (or None with no ledger). The
        returned `seq` is the approval id the caller propagates (B2).

        UX-CONFIRM (D7a) — the entry carries `content_hash` + `run`. Until now it carried neither,
        so nothing downstream could even ASK "has this exact artifact already been approved in this
        run" — the re-ask problem was not just unfixed, it was unmeasurable. These two fields are
        what the carry-forward below joins on, and what an audit of confirmation economics reads."""
        if self.ledger is not None:
            return self.ledger.record("write_gate", write_kind=req.kind, target=req.target,
                                      actor=req.actor, decision=decision, reason=reason,
                                      content_hash=content_hash_for(req), run=req.run)
        return None

    def _carried_forward_seq(self, req: WriteRequest, chash: str) -> Optional[int]:
        """UX-CONFIRM (D7b/c) — the seq of an approval ALREADY on the ledger that licenses exactly
        this write, or None. Mirrors `approval.propose`'s live-proposal short-circuit: a human who
        has already said yes to this exact content, in this run, is not asked again.

        The bound is `content_hash` AND `run`, and both halves are load-bearing:

          * CONTENT — a different hash is a different artifact. The hash covers kind, target and
            content, so an approval cannot be stretched to license a write the human did not read.
          * RUN — the SI.3 threat model, applied here. An approval that carried across runs could
            be banked and redeemed "against a much later, differently-framed conversation"
            (`approval.DEFAULT_TTL_SECONDS`'s own reasoning). A run is the scope in which the human
            still has the context they approved with.

        FAIL-CLOSED on absence: no ledger, no run, or an unreadable ledger → None, i.e. ASK. That
        makes every caller who threads no run byte-identical to before, and makes the safe answer
        the default one rather than something a caller must opt into.

        Read-only. Nothing here can approve anything — it only reports that a human already did.
        """
        if self.ledger is None or not req.run:
            return None
        try:
            entries = self.ledger.entries()
        except Exception:
            return None                      # an unreadable ledger means ASK, never assume
        for entry in reversed(entries):
            if (entry.get("kind") == "write_gate" and entry.get("decision") == "approved"
                    and entry.get("run") == req.run
                    and entry.get("content_hash") == chash):
                seq = entry.get("seq")
                return seq if isinstance(seq, int) and not isinstance(seq, bool) else None
        return None

    @contextmanager
    def _ledger_hold(self) -> Iterator[None]:
        """Hold the ledger's cross-process append lock across commit() + the approved record, so the
        store's `len(ledger)+1` approval-id prediction (computed inside commit) is provably exact —
        no other writer can append between the prediction and the approved entry it predicts (B2).
        A no-op when there is no ledger, or a ledger without the lock (a test stub)."""
        if self.ledger is not None and hasattr(self.ledger, "hold"):
            with self.ledger.hold():
                yield
        else:
            with nullcontext():
                yield

    def submit(self, req: WriteRequest, commit: Optional[Callable[[], None]] = None,
               confirm: Optional[Callable[[str], bool]] = None,
               assume_yes: bool = False, prompt: Optional[str] = None,
               rules: Optional[Sequence[Any]] = None, action: Any = None,
               scope_context: Any = None,
               override: Optional[Callable[[Any], bool]] = None,
               matcher: Optional[Callable[[Any, Any], bool]] = None,
               actor: Optional[str] = None,
               human_approved: bool = False,
               workspace_root: Optional[str] = None) -> WriteOutcome:
        """`human_approved` says an explicit human decision on THIS EXACT write has already been
        obtained and verified out-of-band — today, an SI.3 approval a human minted with
        `mokata approve <id>`, bound to the write's content hash, single-use and session-scoped.

        It is deliberately NOT `assume_yes`. `assume_yes` is a STAND-IN approval ("nobody is here to
        ask; proceed"), which is exactly what propose-only exists to refuse. `human_approved` is the
        human's real decision, already made. Conflating them is what made propose-only unusable on
        MCP (see below), so they stay two flags.

        `workspace_root` supplies the containment root for SELF-PROTECT rule (c). Omitted (every
        pre-0.0.16 caller) it enforces rules (a) and (b) only — which need no root — so those
        callers are byte-identical unless they were writing into an installed tree."""
        # Layer 0 (SELF-PROTECT, 0.0.16 stage 3): installed / out-of-workspace code is NEVER
        # writable, and this fires FIRST — ahead of the trust dial, the secret scan and the human
        # gate. Ahead of the trust dial because a trust level says WHO may write, not WHERE; ahead
        # of the human gate for exactly the reason the secret block is ("a secret is a security
        # block that approval cannot override" — this module's own docstring): an approval must not
        # be able to whitelist a blocked tree. Until now `target` was an OPAQUE LABEL to this gate —
        # no realpath existed anywhere in `govern/` — which is half of how the live incident (an
        # agent editing mokata's own code in site-packages) was possible at all.
        from ..selfprotect import check_target, render_refusal
        verdict = check_target(req.target, workspace_root=workspace_root)
        if verdict.blocked:
            self._log(req, "blocked", f"self-protect: {verdict.rule}")
            return WriteOutcome(False, True, render_refusal(verdict), [])

        # K3 trust dial (SI.4). The dial keys on WHO is writing: the tool's own level, else the
        # surface's (`mcp`/`cli`), else gated-write. An untagged request resolves to the default, so
        # a caller that threads no identity is byte-identical to before.
        if self.trust is not None and (req.tool or req.surface):
            if not self.trust.can_write(req.tool, req.surface):
                self._log(req, "blocked", "read-only trust")
                return WriteOutcome(
                    False, True,
                    f"blocked: '{req.tool or req.surface}' is read-only — writes not permitted",
                    [])
            if not self.trust.allows_auto_approve(req.tool, req.surface) and not human_approved:
                # propose-only: no AUTO-approval. An explicit human decision is required — and none
                # has been verified yet, so fall through to the human gate below and ASK.
                #
                # This is the whole of the SI.4 semantic fix. The rule used to be a bare
                # `assume_yes = False`, i.e. "always force the interactive prompt". That is wrong in
                # a way that only bites off a TTY: `prompt.read_yes_no` fails CLOSED when stdin is
                # not a terminal, and the MCP server is stdio-bound to the harness and HAS no
                # terminal. So a propose-only tool on MCP had every write DECLINED — including one
                # carrying a genuine, human-minted approval. The rung was a dead end on the surface
                # it most needed to govern.
                #
                # `propose-only` means "a human must decide", not "a human must be prompted". A
                # prompt is one way to obtain the decision; SI.3's out-of-band approval is another,
                # and a stronger one. So a verified approval SATISFIES this rung and skips the ask.
                assume_yes = False

        # Layer 1 (security): secrets are a hard block, regardless of human approval.
        #
        # SECRET-IGNORE — with a `workspace_root` the repo's RECORDED false positives are honoured,
        # at the ENTROPY layer only (`scan` enforces that; the store cannot reach the signature
        # layer or the known-shape floor). Without one, nothing is suppressed — a caller that
        # threads no root is byte-identical to before, and that default is the fail-closed one.
        from .secret_ignore import load_for_scan, render_block
        store = None
        if workspace_root:
            store, _notice = load_for_scan(workspace_root)
        findings = scan(text=req.content, path=req.target,
                        for_send=(req.kind == "send"), ignores=store)
        if findings:
            self._log(req, "blocked", "secret detected")
            # The SAME builder the `secret-guard` hook uses, so the two surfaces cannot drift —
            # the wording lives in one module, never inside a tool (R3/R4).
            return WriteOutcome(False, True,
                                render_block(findings, path=req.target, root=workspace_root),
                                findings)

        # Layer 2 (governance, TM.S8 / P14): in-scope hard rules block with no runtime override;
        # a soft rule blocks unless an explicit override clears it (ledgered); advisory only flags.
        # No rules/action supplied → this whole layer is skipped (local/zero-config unaffected).
        enf = None
        if action is not None and rules:
            from .enforce import EnforcementGate
            enf = EnforcementGate(ledger=self.ledger).check(
                action, rules, context=scope_context, matcher=matcher,
                override=override, actor=(actor or req.actor))
            if not enf.allowed:
                self._log(req, "blocked", "governance rule fired")
                return WriteOutcome(False, True, enf.message, [], enforcement=enf.verdict)

        # Layer 2: human gate. A caller may supply a richer `prompt` surface (e.g. memory's
        # render_write / old→new healing diff) so unifying on this gate doesn't flatten it.
        #
        # `human_approved` clears it because the human ALREADY decided, out-of-band, on this exact
        # content — re-asking would be asking a terminal that isn't there. `assume_yes` clears it as
        # the stand-in for a non-interactive run. Everything else asks.
        # UX-CONFIRM (D7b/c) — carry-forward, and WHERE it sits is the entire safety argument.
        #
        # It is HERE: after self-protect (layer 0), after the trust dial, after the secret scan
        # (layer 1) and after governance enforcement (layer 2). Every one of those runs on every
        # submit, unchanged, whether or not an approval is carried. That ordering is not incidental
        # — it is the difference between de-duplicating an ASK and removing a GATE. A secret
        # introduced into content that hashes the same as something approved earlier still blocks;
        # a target that became unwritable still blocks; a rule that now fires still blocks. The
        # human's earlier "yes" licenses the CONTENT, and it was never able to license anything
        # else.
        #
        # What it removes is exactly one thing: asking a human a question they have already
        # answered, about the same artifact, in the same run. Doc 84's principle, unchanged —
        # "every human DECISION is asked exactly once, with full content; no gate is removed".
        #
        # The write is still gated, still scanned, and still gets its OWN `approved` ledger entry
        # below (naming the approval it was carried from), so the audit trail keeps one record per
        # durable write. Nothing about the count of gate decisions changes.
        carried = None
        if not assume_yes and not human_approved:
            carried = self._carried_forward_seq(req, content_hash_for(req))

        if not assume_yes and not human_approved and carried is None:
            gate = confirm or _default_confirm
            shown = prompt or (f"mokata · approve {req.kind} write to {req.target} "
                               f"({len(req.content)} chars)?")
            if not gate(shown):
                self._log(req, "declined", "human declined")
                return WriteOutcome(False, True, "declined at the human gate", [])

        # Commit. The commit and the `approved` ledger record are held under ONE ledger-lock window
        # so a caller predicting the approval seq inside commit (the store's `len+1`) gets exactly
        # the seq the approved entry lands at — no interleave, no misattribution (B2). The human gate
        # above is deliberately OUTSIDE the hold (never hold a cross-process lock across a prompt).
        with self._ledger_hold():
            if commit is not None:
                commit()
            # A carried approval still records its OWN entry, and says what it was carried from —
            # so the ledger keeps one decision per durable write (no count drops), and an auditor
            # can walk from this write to the ask the human actually answered.
            reason = ("committed" if carried is None
                      else f"committed (approval carried forward from #{carried}, same run + "
                           f"same content)")
            approval = self._log(req, "approved", reason)
        approval_seq = approval.get("seq") if isinstance(approval, dict) else None
        return WriteOutcome(True, False, "committed", [],
                            enforcement=(enf.verdict if enf is not None else None),
                            overridden=bool(enf is not None and enf.overridden),
                            approval_seq=approval_seq)
