"""SI.3 — the HUMAN-MINTED write approval (0.0.13 seatbelt cluster, stage 3).

P2 is THE principle: *every durable write is human-gated*. On the MCP path it was a polite fiction.
The gate was `_approved(approve, confirm)` — and `approve` is **a parameter the model types**. The
model decided to say it was approved, and mokata believed it, then handed `assume_yes=True` to the
WriteGate. Prompt injection, a confused model, or plain eagerness minted consent. Doc 74 filed it
C-class; the SS.S5 audit flagged the `assume_yes=True` sites again.

SI.1 closed exactly this class of hole for the gate OVERRIDE, by giving the override no MCP surface
at all: "a model-invocable override would let the model clear its own gate". This module applies the
same logic to the approval itself. The rule it enforces:

    **The model may REFERENCE an approval. It may never MINT one.**

The flow — three acts, two processes
------------------------------------
    1. the model calls a write tool          -> status "proposed" + a proposal_id. NOTHING commits.
    2. the HUMAN runs `mokata approve <id>`  -> a SEPARATE process, a TTY re-confirm, ledgered.
    3. the model re-calls with proposal_id   -> verified, committed, and the approval is BURNED.

Act 2 is the whole point: it happens in a process the model is not driving, through a terminal the
model does not own, and it leaves a record on disk that act 3 must find. `approve=true` on its own
now yields a proposal and an honest note — never a commit.

Why a separate process is the ONLY sound channel: the MCP server is stdio-bound to the harness, so
it owns no TTY and *cannot* prompt. There is no in-server question mokata could ask that the model
would not be the one answering. The consent therefore has to be minted somewhere the model's tool
call cannot reach — which is exactly the shape SI.1 proved out (CLI command · re-confirmed ·
session-scoped · ledgered) and what doc 74 called the "out-of-band approval token".

The honest boundary (P22 — say what is NOT true)
------------------------------------------------
This makes the human gate STRUCTURAL against the MCP surface: no combination of tool parameters
reaches a commit. It does NOT make mokata proof against an agent with arbitrary shell access, which
could run the approve command itself — but such an agent could equally well write the file directly,
so no in-process design could hold there either. The claim is precise and bounded: **mokata's own
write tools no longer accept the model's word for the human's decision.**

The proposal id is DERIVED, not random
--------------------------------------
    proposal_id = "p-" + sha256(run_id \\0 content_hash)[:12]        (content_hash over tool + args)

Deterministic, which buys three properties for free:
  * **idempotent** — an identical re-call resolves to the SAME proposal, so a retrying model cannot
    spam the store with duplicates.
  * **content-bound** — change one argument and the hash changes, so the id changes. "Get X approved,
    then commit Y" is not discouraged, it is *arithmetically impossible*: Y's call cannot produce
    X's id, and X's record cannot match Y's hash.
  * **session-scoped** — `run_id` is in the pre-image, and the record carries it, so an approval
    minted in window A is refused in window B (`run_id == session_id`; see `session.py`).

Storage — MS.S1 primitives, transient by nature
-----------------------------------------------
One JSON file per proposal, `write_proposal__<id>.json`, in the SI.1/SI.2 state dir
(`.mokata/temp_local/state/`). A pending consent token is transient state, not a durable artifact —
it belongs exactly where the TDD phase and the gate override already live. Every write goes through
`StateStore` (atomic temp → fsync → `os.replace`, under the MS.S6 cross-process lock), so a `kill -9`
mid-approve leaves the whole old record or the whole new one, never a fragment.

Single-use is enforced by the LOCK, not by hope: the verify-and-burn is one locked read-modify-write
(`StateStore.update`), so two concurrent redemptions of one approval cannot both win — the second
sees `used` and is refused. The burn happens at REDEMPTION, before the commit: an approval is a
claim on one write, and if that write is then hard-blocked (a secret), the fix changes the content,
which changes the hash, which is a different proposal needing its own approval anyway.

Bounds: proposals expire after `DEFAULT_TTL_SECONDS` and the store keeps at most `MAX_PROPOSALS`
(expired first, then oldest) — a stale approval cannot be redeemed hours later in a different
context, and the state dir cannot grow without limit.

Stdlib-only; no daemon; clean-room. Copyright 2026 MoStack. Licensed under the Apache License,
Version 2.0.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

from .state import StateStore
from .tdd_state import state_dir

# The state key prefix. A sibling of SI.1's `gate_override__<run_id>` and SI.2's `tdd_phase__<run_id>`
# — but keyed by PROPOSAL id, not run id, because the `mokata approve` process looks a proposal up by
# the id the model showed the human. The run is carried INSIDE the record and checked at redemption,
# which is what makes the approval session-scoped.
PROPOSAL_PREFIX = "write_proposal__"

# How long a proposal (and the approval minted on it) stays redeemable. 15 minutes: long enough for a
# human to read the preview, switch to a terminal and type the command; short enough that an approval
# cannot be banked and redeemed against a much later, differently-framed conversation.
DEFAULT_TTL_SECONDS = 900

# The most pending proposals kept per repo. Expired records are pruned first, then the oldest. With
# a content-derived id only DISTINCT pending writes consume a slot, so this is generous.
MAX_PROPOSALS = 32

# The audit-ledger kind for the whole chain (MS.S3): the human's `approved`, and the `redeemed` entry
# that links the approval to the `write_gate` entry of the commit it licensed.
LEDGER_KIND = "write_approval"

# A proposal's lifecycle. There is no path back out of `used`.
STATUS_PROPOSED = "proposed"
STATUS_APPROVED = "approved"
STATUS_USED = "used"

# Consent states, as seen by a write tool.
GRANTED = "granted"          # a human-minted approval was found, verified, and burned
NEEDED = "needed"            # no approval referenced -> propose, commit nothing
REFUSED = "refused"          # an approval WAS referenced, and it does not license this write

# The named refusal reasons. Every one of these is a way a model could try to bend an approval into
# licensing something the human did not see, so each is answered by name rather than a generic "no".
REFUSED_UNKNOWN = "unknown-proposal"
REFUSED_CONTENT_CHANGED = "content-changed"
REFUSED_EXPIRED = "expired"
REFUSED_USED = "already-used"
REFUSED_OTHER_SESSION = "other-session"
REFUSED_NOT_APPROVED = "not-approved"

_REFUSAL_TEXT = {
    REFUSED_UNKNOWN: "no such proposal — it never existed, expired, or was pruned",
    REFUSED_CONTENT_CHANGED: ("this is NOT the write that was approved — the arguments changed since "
                              "the human saw them (the approval is bound to its content hash)"),
    REFUSED_EXPIRED: "the approval has expired",
    REFUSED_USED: "that approval was already used — an approval licenses exactly one write",
    REFUSED_OTHER_SESSION: "that approval was minted in a different session",
    REFUSED_NOT_APPROVED: "no human has approved this proposal yet",
}


# ======================================================================================
# the record
# ======================================================================================

@dataclass(frozen=True)
class Proposal:
    """One staged durable write, and the human decision (or lack of one) attached to it."""

    proposal_id: str
    run_id: str
    tool: str
    target: str
    summary: str
    preview: str
    content_hash: str
    status: str
    created_at: float
    expires_at: float
    approved_by: str = ""
    approved_at: float = 0.0
    used_at: float = 0.0

    @property
    def approved(self) -> bool:
        return self.status == STATUS_APPROVED

    def expired(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at

    def expires_in(self, now: Optional[float] = None) -> int:
        left = self.expires_at - (now if now is not None else time.time())
        return max(0, int(left))

    def to_state(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id, "run_id": self.run_id, "tool": self.tool,
            "target": self.target, "summary": self.summary, "preview": self.preview,
            "content_hash": self.content_hash, "status": self.status,
            "created_at": self.created_at, "expires_at": self.expires_at,
            "approved_by": self.approved_by, "approved_at": self.approved_at,
            "used_at": self.used_at,
        }


def from_state(data: Any) -> Optional[Proposal]:
    """A `Proposal` out of a persisted value, or None when the value is absent / corrupt / the wrong
    shape. Degrade-clean and fail-CLOSED: an unreadable record is not an approval, so it grants
    nothing (the opposite of SI.1's fail-open hook, and deliberately so — that hook decides whether
    to BLOCK, this one decides whether to WRITE)."""
    if not isinstance(data, dict):
        return None
    try:
        return Proposal(
            proposal_id=str(data["proposal_id"]), run_id=str(data["run_id"]),
            tool=str(data["tool"]), target=str(data.get("target", "")),
            summary=str(data.get("summary", "")), preview=str(data.get("preview", "")),
            content_hash=str(data["content_hash"]), status=str(data["status"]),
            created_at=float(data["created_at"]), expires_at=float(data["expires_at"]),
            approved_by=str(data.get("approved_by", "")),
            approved_at=float(data.get("approved_at", 0.0) or 0.0),
            used_at=float(data.get("used_at", 0.0) or 0.0),
        )
    except (KeyError, TypeError, ValueError):
        return None


# ======================================================================================
# identity — the content hash and the derived id
# ======================================================================================

def content_hash(tool: str, args: Dict[str, Any]) -> str:
    """The hash binding an approval to EXACTLY the write the human saw.

    Canonical JSON (sorted keys, no whitespace drift) over the tool name and its meaningful
    arguments — the consent parameters themselves (`approve` / `confirm` / `proposal_id`) are NOT in
    the pre-image, so referencing an approval cannot change the hash it is being checked against."""
    blob = json.dumps({"tool": tool, "args": args}, sort_keys=True, separators=(",", ":"),
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def proposal_id_for(run_id: str, chash: str) -> str:
    """The proposal id for a (run, content) pair — short enough for a human to retype."""
    digest = hashlib.sha256(f"{run_id}\0{chash}".encode("utf-8")).hexdigest()
    return "p-" + digest[:12]


def state_key(proposal_id: str) -> str:
    return PROPOSAL_PREFIX + proposal_id


def _store(root: str) -> StateStore:
    return StateStore(state_dir(root))


# ======================================================================================
# the store
# ======================================================================================

def load(root: str, proposal_id: str) -> Optional[Proposal]:
    """The proposal `proposal_id`, or None (absent / corrupt / unreadable)."""
    if not proposal_id:
        return None
    try:
        return from_state(_store(root).read(state_key(proposal_id)))
    except OSError:
        return None


def pending(root: str, now: Optional[float] = None) -> List[Proposal]:
    """Every live (unexpired, unused) proposal in this repo, newest first — what `mokata approve`
    lists when the human asks what is waiting on them."""
    now = time.time() if now is None else now
    out: List[Proposal] = []
    try:
        with os.scandir(state_dir(root)) as entries:
            names = [e.name for e in entries
                     if e.name.startswith(PROPOSAL_PREFIX) and e.name.endswith(".json")]
    except OSError:
        return []
    for name in names:
        p = load(root, name[len(PROPOSAL_PREFIX):-len(".json")])
        if p is None or p.status == STATUS_USED or p.expired(now):
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.created_at, reverse=True)


def prune(root: str, now: Optional[float] = None, keep: int = MAX_PROPOSALS) -> int:
    """Drop expired, used, and unreadable records, then trim the oldest down to `keep`.
    Returns how many were removed. Never raises — pruning is housekeeping, not a gate.

    `keep` is a parameter because `propose` must leave ROOM for the record it is about to write:
    pruning to the cap and then adding one would land at cap+1."""
    now = time.time() if now is None else now
    store = _store(root)
    try:
        with os.scandir(state_dir(root)) as entries:
            names = [e.name for e in entries
                     if e.name.startswith(PROPOSAL_PREFIX) and e.name.endswith(".json")]
    except OSError:
        return 0

    live: List[Tuple[float, str]] = []
    removed = 0
    for name in names:
        key = name[:-len(".json")]
        p = from_state(store.read(key)) if _safe(store, key) else None
        if p is None or p.expired(now) or p.status == STATUS_USED:
            removed += 1 if store.delete(key) else 0
            continue
        live.append((p.created_at, key))

    keep = max(0, keep)
    if len(live) > keep:
        for _created, key in sorted(live)[:len(live) - keep]:
            removed += 1 if store.delete(key) else 0
    return removed


def _safe(store: StateStore, key: str) -> bool:
    try:
        return store.exists(key)
    except OSError:
        return False


def propose(root: str, *, tool: str, args: Dict[str, Any], run_id: str, target: str = "",
            summary: str = "", preview: str = "", ttl: int = DEFAULT_TTL_SECONDS,
            now: Optional[float] = None) -> Proposal:
    """Stage a durable write for a human to look at, and return its `Proposal`. Writes NOTHING but
    the proposal record itself.

    Idempotent on content: an identical re-call resolves to the same id, and if a LIVE proposal is
    already there it is returned untouched — re-proposing must never quietly reset an approval the
    human has already given (nor extend the clock on one)."""
    now = time.time() if now is None else now
    chash = content_hash(tool, args)
    pid = proposal_id_for(run_id, chash)

    existing = load(root, pid)
    if (existing is not None and existing.status in (STATUS_PROPOSED, STATUS_APPROVED)
            and not existing.expired(now)):
        return existing

    prune(root, now, keep=MAX_PROPOSALS - 1)     # leave room for the one we are about to write
    proposal = Proposal(
        proposal_id=pid, run_id=run_id, tool=tool, target=target, summary=summary,
        preview=preview, content_hash=chash, status=STATUS_PROPOSED,
        created_at=now, expires_at=now + ttl,
    )
    _store(root).write(state_key(pid), proposal.to_state())
    return proposal


# ======================================================================================
# act 2 — the human's own process
# ======================================================================================

@dataclass(frozen=True)
class ApprovalResult:
    """What `mokata approve <id>` did (doc 85 §3: a `*Result` is the produced value)."""

    ok: bool
    code: str
    message: str
    proposal: Optional[Proposal] = None


def approve(root: str, proposal_id: str, *, actor: str = "human", ledger: Any = None,
            ttl: int = DEFAULT_TTL_SECONDS, now: Optional[float] = None) -> ApprovalResult:
    """Mint the human's approval on `proposal_id` — the act the MODEL CANNOT PERFORM.

    Called only from the `mokata approve` CLI, which re-confirms at a TTY first. The clock is reset
    from the moment of approval (the human just looked; give them a fresh window to hand back), and
    the decision is appended to the hash-chained audit ledger."""
    now = time.time() if now is None else now
    p = load(root, proposal_id)
    if p is None:
        return ApprovalResult(False, REFUSED_UNKNOWN, _REFUSAL_TEXT[REFUSED_UNKNOWN])
    if p.status == STATUS_USED:
        return ApprovalResult(False, REFUSED_USED, _REFUSAL_TEXT[REFUSED_USED], p)
    if p.expired(now):
        return ApprovalResult(False, REFUSED_EXPIRED, _REFUSAL_TEXT[REFUSED_EXPIRED], p)
    if p.status == STATUS_APPROVED:
        return ApprovalResult(True, "already-approved",
                              "already approved — the model can redeem it", p)

    approved = replace(p, status=STATUS_APPROVED, approved_by=actor, approved_at=now,
                       expires_at=now + ttl)
    _store(root).write(state_key(proposal_id), approved.to_state())
    if ledger is not None:
        ledger.record(LEDGER_KIND, decision="approved", proposal=proposal_id, tool=p.tool,
                      target=p.target, content_hash=p.content_hash, actor=actor,
                      run=p.run_id, scope="session")
    return ApprovalResult(True, "approved", f"approved — {p.tool} may now commit once", approved)


# ======================================================================================
# act 3 — redemption, inside the write tool
# ======================================================================================

@dataclass(frozen=True)
class Consent:
    """Whether THIS call may commit (doc 85 §3: an `*Outcome`-shaped gate verdict; named `Consent`
    because that is the noun the whole stage is about)."""

    state: str                              # GRANTED | NEEDED | REFUSED
    proposal: Optional[Proposal] = None
    code: str = ""                          # the named refusal reason, when REFUSED
    reason: str = ""

    @property
    def granted(self) -> bool:
        return self.state == GRANTED

    @property
    def refused(self) -> bool:
        return self.state == REFUSED


def _verify(p: Optional[Proposal], *, chash: str, run_id: str, now: float) -> Optional[str]:
    """The refusal code for redeeming `p` against this call, or None when it genuinely licenses it.

    Order matters, and is chosen so the answer names the MOST specific thing that is wrong: a
    stranger's approval is reported as another session's, not as 'content changed'."""
    if p is None:
        return REFUSED_UNKNOWN
    if p.run_id != run_id:
        return REFUSED_OTHER_SESSION
    if p.content_hash != chash:
        return REFUSED_CONTENT_CHANGED
    if p.status == STATUS_USED:
        return REFUSED_USED
    if p.expired(now):
        return REFUSED_EXPIRED
    if p.status != STATUS_APPROVED:
        return REFUSED_NOT_APPROVED
    return None


def redeem(root: str, proposal_id: str, *, tool: str, args: Dict[str, Any], run_id: str,
           now: Optional[float] = None) -> Consent:
    """Verify a human-minted approval against THIS exact call, and burn it. The only path to GRANTED.

    Verify-and-burn is ONE locked read-modify-write, so two concurrent redemptions of one approval
    cannot both win: the loser re-reads `used` inside the same lock and is refused."""
    now = time.time() if now is None else now
    chash = content_hash(tool, args)
    store = _store(root)
    key = state_key(proposal_id)

    # Screen the absent/corrupt case on a plain read, so a refusal never writes anything.
    if from_state(store.read(key)) is None:
        return _refuse(REFUSED_UNKNOWN)

    box: Dict[str, Any] = {}

    def _burn(current: Any) -> Dict[str, Any]:
        p = from_state(current)
        code = _verify(p, chash=chash, run_id=run_id, now=now)
        box["code"], box["proposal"] = code, p
        if code is not None or p is None:
            # Refusing: hand back exactly what was there (an atomic rewrite of identical bytes).
            return current if isinstance(current, dict) else {}
        return replace(p, status=STATUS_USED, used_at=now).to_state()

    try:
        store.update(key, _burn, default=None)
    except OSError:
        return _refuse(REFUSED_UNKNOWN)

    code = box.get("code")
    if code is not None:
        return _refuse(code, box.get("proposal"))
    return Consent(GRANTED, box.get("proposal"))


def _refuse(code: str, p: Optional[Proposal] = None) -> Consent:
    return Consent(REFUSED, p, code=code, reason=_REFUSAL_TEXT.get(code, "refused"))


def consent(root: str, *, tool: str, args: Dict[str, Any], run_id: str,
            proposal_id: str = "") -> Consent:
    """THE entry point every MCP write tool calls. No `proposal_id` referenced -> NEEDED (propose,
    commit nothing). One referenced -> it is verified against this exact call, or refused by name.

    There is deliberately no parameter here that a model could pass to reach GRANTED. That is the
    whole stage."""
    if not proposal_id:
        return Consent(NEEDED)
    return redeem(root, proposal_id, tool=tool, args=args, run_id=run_id)


def record_redemption(ledger: Any, p: Optional[Proposal], *, write_seq: Any = None,
                      committed: bool = True) -> None:
    """Close the chain: link the human's approval to the `write_gate` entry of the commit it
    licensed, so `mokata audit` shows proposal-hash → who approved → which write, forever."""
    if ledger is None or p is None:
        return
    ledger.record(LEDGER_KIND, decision="redeemed", proposal=p.proposal_id, tool=p.tool,
                  target=p.target, content_hash=p.content_hash, approved_by=p.approved_by,
                  approved_at=p.approved_at, run=p.run_id, committed=committed,
                  write_seq=write_seq)


def render(p: Proposal, now: Optional[float] = None) -> str:
    """What the human sees before they say yes — the write, in full, in their own terminal."""
    lines = [
        f"  proposal : {p.proposal_id}",
        f"  tool     : {p.tool}",
        f"  target   : {p.target or '(n/a)'}",
        f"  summary  : {p.summary or '(none)'}",
        f"  expires  : in {p.expires_in(now) // 60}m {p.expires_in(now) % 60}s",
    ]
    if p.preview:
        lines.append("  would write:")
        lines.extend(f"    {ln}" for ln in p.preview.splitlines()[:20])
    return "\n".join(lines)


__all__ = [
    "PROPOSAL_PREFIX", "DEFAULT_TTL_SECONDS", "MAX_PROPOSALS", "LEDGER_KIND",
    "STATUS_PROPOSED", "STATUS_APPROVED", "STATUS_USED",
    "GRANTED", "NEEDED", "REFUSED",
    "REFUSED_UNKNOWN", "REFUSED_CONTENT_CHANGED", "REFUSED_EXPIRED", "REFUSED_USED",
    "REFUSED_OTHER_SESSION", "REFUSED_NOT_APPROVED",
    "Proposal", "ApprovalResult", "Consent",
    "approve", "consent", "content_hash", "from_state", "load", "pending", "propose",
    "proposal_id_for", "prune", "record_redemption", "redeem", "render", "state_key",
]
