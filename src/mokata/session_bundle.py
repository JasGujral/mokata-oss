"""Stage 55a — portable / shareable tagged sessions (bundle + LOCAL file share).

Today a session's resumable state lives under `.mokata/temp_local/state/` — local + gitignored,
so it does not travel. This module packages the CURRENT session into a self-contained,
MACHINE-PATH-FREE, versioned bundle and shares it as a LOCAL file, so another machine (or a
teammate) can pull + re-hydrate it and `mokata resume` continues the work.

It composes the existing primitives — it does NOT rebuild session/resume/vault:
  * the resumable run state is the `pipeline_run__<id>` checkpoint(s) (govern.resume) +
    the session-scoped keys `emitted_spec`, `approved_approach`, `brainstorm_progress`
    (brainstorm), read back through the same `StateStore` (state.py);
  * push follows the vault's compute-then-commit GATED pattern (vault.py) — a content-hash,
    provenance, idempotent, never a silent clobber (a changed re-push is a conflict unless
    forced);
  * BOTH push and pull are human-gated (P2) and SECRET-SCANNED through the universal
    `WriteGate` (a secret is a hard block approval cannot override) and audit-logged;
  * pull VERIFIES the content-hash (corruption is caught, not served), re-scans + re-gates
    the UNTRUSTED bundle, and SURFACES a cross-codebase repo-fingerprint mismatch (never a
    silent mis-apply) — the Stage 50/54g HARD-GATE invariant survives the round-trip (a
    not-yet-approved brainstorm stays NOT approved after pull).

Local-first (P8): the bundle is a file under `.mokata/session-bundles/` (NOT temp_local/, so it
travels like the vault / memory-share). Dependency-free core; clean-room.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import MOKATA_DIR
from .govern.trust import (CLI_SURFACE, policy_approved, policy_surface,
                           policy_tool, policy_trust)
from .brainstorm import APPROACH_STATE_KEY, BRAINSTORM_PROGRESS_KEY, PIPELINE_PHASES
from .engine.spec_gate import SPEC_STATE_KEY
from .govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint
from .errors import MokataError

BUNDLE_DIRNAME = "session-bundles"
BUNDLE_KIND = "mokata-session-bundle"
# SS.S4 — the bundle wire format's OWN version marker (independent of the memory-doc schema).
# v1 = the SS.S3 shape (state + hash + provenance + resume). v2 adds the explicit `transcript` /
# `meta` / `cross_repo` / `origin_repo` sections. This is the HIGHEST version this reader emits
# and can honor — a bundle whose version EXCEEDS it is refused loudly (see `verify_bundle`), never
# silently stripped of its newer sections (D6-class).
BUNDLE_SCHEMA_VERSION = 2

# SS.S4 — a requirements-only cross-repo bundle stores its distilled requirements under this ONE
# state key (no brainstorm_progress / approval / checkpoints), so the receiver hydrates the
# distilled ask on a DIFFERENT codebase without any approach/approval crossing.
SESSION_REQUIREMENTS_KEY = "session_requirements"

# SS.S4 — the bounded transcript cap (P15/P23: the transcript is the highest-risk payload). A
# healthy Socratic brainstorm (one question at a time) converges well under this many answered
# turns; beyond it the bundle keeps the OPENING context (head) and the LATEST state (tail) and
# DECLARES how many middle turns were dropped — the thread is preserved, the payload stays bounded,
# and no adversarial/oversized progress blob can turn the transcript into an unbounded vault.
TRANSCRIPT_MAX_TURNS = 40
TRANSCRIPT_HEAD_TURNS = 20
TRANSCRIPT_TAIL_TURNS = 20

# The session-scoped (NOT per-run) state keys a bundle carries alongside the run checkpoint(s).
_SESSION_KEYS = (APPROACH_STATE_KEY, SPEC_STATE_KEY, BRAINSTORM_PROGRESS_KEY)

# The fields the content-hash covers — the SUBSTANTIVE payload only, so a re-push of the same
# session at a later time (different provenance timestamp/author) stays idempotent (like vault).
# v1 hashed exactly these five; v2 EXTENDS the cover to the new sections so a tampered transcript
# or a flipped cross-repo marker is caught by the hash (the fingerprint exception cannot be forged).
_HASHED_FIELDS_V1 = ("schema_version", "kind", "repo_fingerprint", "run_id", "state")
_HASHED_FIELDS_V2 = _HASHED_FIELDS_V1 + ("transcript", "meta", "cross_repo", "origin_repo")


class SessionBundleError(MokataError):
    """Raised on a bad tag, a corrupt/missing bundle, or a malformed bundle file."""


# ----------------------------------------------------------------------------- paths
def bundle_dir(root: str) -> str:
    return os.path.join(root, MOKATA_DIR, BUNDLE_DIRNAME)


def _safe_tag(tag: str) -> str:
    """A tag is a single path-free slug — reject anything that could escape the store dir."""
    if not tag or tag != os.path.basename(tag) or tag in (".", ".."):
        raise SessionBundleError(
            f"invalid session tag '{tag}' (use a simple name, no path separators)")
    if any(c in tag for c in '/\\:'):
        raise SessionBundleError(f"invalid session tag '{tag}'")
    return tag


def bundle_path(root: str, tag: str) -> str:
    return os.path.join(bundle_dir(root), f"{_safe_tag(tag)}.json")


# ----------------------------------------------------------------------------- helpers
def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _default_author() -> str:
    # cross-platform: getpass.getuser() also reads %USERNAME% on Windows ($USER is POSIX-only).
    from .crossplat import current_user
    return current_user()


# A wholly-absolute path (posix `/…`, windows `C:\…` / `C:/…`, or a UNC `\\host`). We only
# neutralise VALUES that are entirely an absolute path — session state carries machine paths in
# `source`/`root`/`dest` fields, never paths embedded mid-prose — so the bundle stays portable
# without mangling legitimate content.
_ABS_RE = re.compile(r"^(?:/|[A-Za-z]:[\\/]|\\\\)")


def _is_abs_path(s: str) -> bool:
    return bool(_ABS_RE.match(s)) and "\n" not in s


def _machine_path_free(obj: Any) -> Any:
    """Recursively strip absolute paths so the bundle can travel. A string that is wholly an
    absolute path collapses to its basename (keeps a hint, drops the machine-specific prefix);
    containers recurse. Deterministic."""
    if isinstance(obj, dict):
        return {k: _machine_path_free(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_machine_path_free(v) for v in obj]
    if isinstance(obj, str) and _is_abs_path(obj):
        # separator-agnostic basename so a WINDOWS abs path scrubbed on a POSIX host (CI) also
        # collapses — os.path.basename only splits on the host separator (Stage 66).
        from .crossplat import basename_any
        return basename_any(obj) or "<path>"
    return obj


def find_abs_paths(obj: Any) -> List[str]:
    """Every absolute-path VALUE still present in `obj` (for the machine-path-free invariant).
    Empty when the structure is portable."""
    out: List[str] = []
    if isinstance(obj, dict):
        for v in obj.values():
            out += find_abs_paths(v)
    elif isinstance(obj, list):
        for v in obj:
            out += find_abs_paths(v)
    elif isinstance(obj, str) and _is_abs_path(obj):
        out.append(obj)
    return out


def serialize_bundle(bundle: Dict[str, Any]) -> str:
    """Canonical, deterministic JSON for a bundle (sorted keys) + trailing newline."""
    return json.dumps(bundle, indent=2, sort_keys=True) + "\n"


# ----------------------------------------------------------------------------- repo fingerprint
def repo_fingerprint(root: str) -> str:
    """A deterministic, machine-path-FREE signature of the CODEBASE (not the session), used to
    catch a cross-codebase pull. Derived from the repo's top-level layout — the sorted
    non-hidden top-level entry names (dirs flagged) — which two clones of the same repo share
    and an unrelated repo does not. Frugal (names only, never file contents); never raises."""
    names: List[str] = []
    try:
        for name in sorted(os.listdir(root)):
            if name.startswith("."):
                continue                      # skip .mokata / .git / dotfiles (machine noise)
            tag = name + ("/" if os.path.isdir(os.path.join(root, name)) else "")
            names.append(tag)
    except OSError:
        names = []
    return content_hash("\n".join(names))


# ----------------------------------------------------------------------------- build (the bundle)
def _resume_summary(store: Any, run_id: Optional[str],
                    phases=PIPELINE_PHASES) -> Dict[str, Any]:
    """A small, read-only resume descriptor for `list` — the run id, its resume phase, and
    done/total — derived from the run checkpoint (the first run if none is named). Bounded."""
    from .progress import find_active_run
    rid = run_id or find_active_run(store, phases)
    if not rid or store.read(CHECKPOINT_PREFIX + rid) is None:
        return {"run_id": rid, "resume_phase": None, "done": 0, "total": len(phases)}
    cp = PipelineCheckpoint(store, rid)
    passed = [p for p in cp.passed if p in phases]
    return {"run_id": rid, "resume_phase": cp.resume_phase(phases),
            "done": len(passed), "total": len(phases)}


def build_session_bundle(surface: Any, run_id: Optional[str] = None,
                         author: str = "", now: str = "",
                         requirements_only: bool = False) -> Dict[str, Any]:
    """Package the current session into a versioned (v2), MACHINE-PATH-FREE bundle.

    Normal push: `state` (the session_state section — the run checkpoint(s) + the session-scoped
    keys `emitted_spec` / `approved_approach` / `brainstorm_progress`, exactly as SS.S3) + a bounded,
    per-turn-secret-scanned `transcript` of the brainstorm Q&A + a counts-only `meta` (the SS.S3
    in-progress label + counts).

    `requirements_only`: the CROSS-REPO handoff — `state` carries ONLY the distilled requirements
    (anchor + synthesis + requirement lines; NO approaches/approval/transcript/checkpoints), the
    bundle is flagged `cross_repo` and stamped with an `origin_repo` LABEL the receiver sees at the
    gate (the fingerprint match is skipped for it — see `plan_session_pull`).

    Deterministic given the same `(surface, run_id, author, now, requirements_only)`."""
    from .memory.item import now_iso
    from .progress import list_runs
    store = surface.state

    transcript: Optional[Dict[str, Any]] = None
    if requirements_only:
        # ONLY the distilled requirements — no approach/approval/checkpoint ever crosses.
        bs_raw = store.read(BRAINSTORM_PROGRESS_KEY)
        state: Dict[str, Any] = {}
        if isinstance(bs_raw, dict):
            state[SESSION_REQUIREMENTS_KEY] = distill_requirements(bs_raw)
    else:
        state = {}
        runs = [run_id] if run_id else list_runs(store)
        for rid in runs:
            key = CHECKPOINT_PREFIX + rid
            data = store.read(key)
            if data is not None:
                state[key] = data
        for key in _SESSION_KEYS:
            data = store.read(key)
            if data is not None:
                state[key] = data
        transcript = build_transcript(state)   # the bounded Q&A record (None when no brainstorm)

    state = _machine_path_free(state)        # strip machine paths so the bundle travels
    core = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "kind": BUNDLE_KIND,
        "repo_fingerprint": repo_fingerprint(surface.root),
        "run_id": run_id,
        "state": state,
        "transcript": transcript,
        "meta": _build_meta(state, requirements_only, transcript),
        "cross_repo": bool(requirements_only),
        # a LABEL (basename), never a machine path — the receiver sees WHERE the requirements came
        # from when the fingerprint check is deliberately skipped.
        "origin_repo": (os.path.basename(os.path.abspath(surface.root))
                        if requirements_only else ""),
    }
    bundle = dict(core)
    bundle["content_hash"] = _hash_core(core)
    bundle["provenance"] = {
        "author": author or _default_author(),
        "source": os.path.basename(os.path.abspath(surface.root)),   # a label, NOT a machine path
        "created": now or now_iso(),
    }
    bundle["resume"] = _resume_summary(store, run_id)
    return bundle


def _hash_core(bundle: Dict[str, Any]) -> str:
    """Content-hash over the substantive payload — VERSION-AWARE so an old v1 bundle keeps its
    original hash (the five v1 fields only) while a v2 bundle also binds its new sections."""
    version = bundle.get("schema_version")
    fields = _HASHED_FIELDS_V2 if (isinstance(version, int) and version >= 2) else _HASHED_FIELDS_V1
    payload = {k: bundle.get(k) for k in fields}
    return content_hash(json.dumps(payload, sort_keys=True))


# ------------------------------------------------------------- SS.S4: bounded Q&A transcript
def _bound_turns(turns: List[Dict[str, Any]]) -> tuple:
    """Head+tail bound a turn list. Returns `(kept, dropped)` where `kept` is the head then the
    tail (original order preserved) and `dropped` is the count of dropped middle turns. Under the
    cap: everything kept, nothing dropped."""
    total = len(turns)
    if total <= TRANSCRIPT_MAX_TURNS:
        return list(turns), 0
    head = turns[:TRANSCRIPT_HEAD_TURNS]
    tail = turns[total - TRANSCRIPT_TAIL_TURNS:]
    return head + tail, total - TRANSCRIPT_HEAD_TURNS - TRANSCRIPT_TAIL_TURNS


def build_transcript(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The brainstorm Q&A record for the bundle — the ANSWERED turns (question + answer + the
    original turn index), BOUNDED head+tail with the dropped count DECLARED (P15/P23). Derived from
    `brainstorm_progress`; None when there is no answered Q&A to record. The turn text is the same
    content already carried in `state` (so it adds no new secret surface) — it is scanned per turn
    on push (`scan_transcript_turns`) and the whole bundle is scanned at the gate."""
    bs = (state or {}).get(BRAINSTORM_PROGRESS_KEY)
    if not isinstance(bs, dict):
        return None
    answered = [(i, q) for i, q in enumerate(bs.get("questions", []) or [])
                if isinstance(q, dict) and q.get("answer") is not None]
    if not answered:
        return None
    kept, dropped = _bound_turns(answered)
    turns = [{"index": i, "question": q.get("text", ""), "answer": q.get("answer")}
             for i, q in kept]
    return {"turns": turns, "total": len(answered), "dropped": dropped,
            "truncated": dropped > 0}


def scan_transcript_turns(bundle: Dict[str, Any]) -> Dict[int, List[Any]]:
    """Secret-scan EVERY transcript turn, keyed by its TURN INDEX — so a refusal can NAME the
    offending turn (never its content). Empty when the transcript is clean / absent."""
    from .govern import has_secrets, scan
    hits: Dict[int, List[Any]] = {}
    tr = (bundle or {}).get("transcript")
    if not isinstance(tr, dict):
        return hits
    for turn in tr.get("turns", []) or []:
        findings = scan(json.dumps({"question": turn.get("question"),
                                    "answer": turn.get("answer")}, sort_keys=True))
        if has_secrets(findings):
            hits[int(turn.get("index", -1))] = findings
    return hits


def transcript_summary(bundle: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A COUNTS-ONLY view of the transcript (turns kept / total / dropped / truncated) for a
    result surface — never any turn text (secret-safe by construction)."""
    tr = (bundle or {}).get("transcript")
    if not isinstance(tr, dict):
        return None
    return {"turns": len(tr.get("turns", [])), "total": tr.get("total", 0),
            "dropped": tr.get("dropped", 0), "truncated": bool(tr.get("truncated"))}


# ------------------------------------------------------------- SS.S4: distilled requirements
def distill_requirements(bs_raw: Dict[str, Any]) -> Dict[str, Any]:
    """Distil a brainstorm record down to its REQUIREMENTS only — the anchor (original ask), the
    running synthesis (goal / constraints / open question), and one requirement line per answered
    turn. NO approaches, NO approval, NO checkpoints, NO raw transcript. This is the cross-repo
    handoff payload. Deterministic; bounded (requirement lines follow the same turn cap)."""
    syn = bs_raw.get("synthesis") or {}
    answered = [q for q in (bs_raw.get("questions", []) or [])
                if isinstance(q, dict) and q.get("answer") is not None]
    kept, dropped = _bound_turns(answered)
    requirements = [f"{q.get('text', '')} → {q.get('answer')}" for q in kept]
    return {
        "anchor": bs_raw.get("anchor") or bs_raw.get("topic") or "",
        "goal": syn.get("goal", ""),
        "constraints": list(syn.get("constraints", []) or []),
        "requirements": requirements,
        "requirements_dropped": dropped,
        "open_question": syn.get("open_question", ""),
    }


def _build_meta(state: Dict[str, Any], requirements_only: bool,
                transcript: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The bundle's counts-only `meta` — the SS.S3 in-progress label + section counts. Integers /
    booleans / a fixed label only; never a topic/answer/approach text."""
    label = in_progress_summary({"state": state})
    checkpoints = sum(1 for k in state if k.startswith(CHECKPOINT_PREFIX))
    turns = transcript["total"] if isinstance(transcript, dict) else 0
    approaches = 0
    bs = state.get(BRAINSTORM_PROGRESS_KEY)
    if isinstance(bs, dict):
        approaches = len(bs.get("approaches", []) or [])
    return {
        "in_progress": label,
        "counts": {"turns": turns, "approaches": approaches, "checkpoints": checkpoints},
        "requirements_only": bool(requirements_only),
    }


def reseal_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute the content-hash after an intentional edit (so an edited bundle is internally
    consistent — used to verify the pull-side secret scan catches NASTY-but-not-corrupt content)."""
    out = dict(bundle)
    out["content_hash"] = _hash_core(out)
    return out


def is_empty_bundle(bundle: Dict[str, Any]) -> bool:
    """True when there is no session to package (degrade-clean → a friendly message upstream)."""
    return not bundle.get("state")


# ------------------------------------------------------------- SS.S3: honest in-progress label
def in_progress_summary(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """A COUNTS-ONLY, secret-free label of what a share carries — for the human gate prompt so an
    IN-PROGRESS session is approved knowing what leaves (P2). `answered` is the number of answered
    brainstorm turns; `approved` is whether an approach is approved; `label` is the one-line summary
    ("in-progress: N answered turns, no approved approach" | "complete: approach approved").
    Derived from the bundle state through the REAL brainstorm reader — never a topic/answer/approach
    text, only integers/booleans."""
    state = bundle.get("state", {}) or {}
    bs_raw = state.get(BRAINSTORM_PROGRESS_KEY)
    answered = 0
    approved = APPROACH_STATE_KEY in state
    if isinstance(bs_raw, dict):
        from .brainstorm import BrainstormSession
        sess = BrainstormSession.from_dict(bs_raw)
        answered = len(sess.answered_questions)
        approved = approved or bool(sess.approved)
    label = ("complete: approach approved" if approved
             else f"in-progress: {answered} answered turns, no approved approach")
    return {"answered": answered, "approved": approved, "label": label}


def shares_unfinished_thinking(bundle: Dict[str, Any]) -> bool:
    """SS.S4 — True when the bundle carries UNFINISHED THINKING: a not-yet-approved brainstorm.
    That is the payload whose sharing needs explicit consent (`--allow-in-progress`). A
    checkpoint-only / spec-only / approved session is NOT unfinished thinking (it is past the
    brainstorm, or complete), so it shares without the flag. Used identically by CLI + MCP."""
    state = bundle.get("state", {}) or {}
    if BRAINSTORM_PROGRESS_KEY not in state:
        return False
    return not in_progress_summary(bundle)["approved"]


# ------------------------------------------------------------- SS.S3: per-value secret scan
def scan_bundle_values(state: Dict[str, Any]) -> Dict[str, List[Any]]:
    """Secret-scan EVERY bundled value, keyed by its state key (P15/P23). Returns `{key: findings}`
    for each key whose serialized value trips the scan — so a refusal can NAME the offending KEY
    without ever echoing the secret VALUE. Empty when the whole session is clean."""
    from .govern import has_secrets, scan
    hits: Dict[str, List[Any]] = {}
    for key, value in (state or {}).items():
        findings = scan(json.dumps(value, sort_keys=True))
        if has_secrets(findings):
            hits[key] = findings
    return hits


# ------------------------------------------------------------- SS.S3: receiver-side de-approval
def strip_imported_approval(state: Dict[str, Any]) -> Dict[str, Any]:
    """Remove approval AUTHORITY from an untrusted bundle's state before it is hydrated on the
    RECEIVER — the HARD-GATE-survives invariant made real cross-machine. An imported approval is
    still THEIR approval to make: the `approved_approach` handoff is DROPPED and the brainstorm's
    approval flags are cleared, so the receiver holds all the PROGRESS (questions, approaches,
    answered turns, checkpoints, spec) but must re-earn approval through their OWN gate. A
    not-yet-approved session is unchanged (nothing to strip). Pure; never mutates its input."""
    out = dict(state or {})
    out.pop(APPROACH_STATE_KEY, None)                 # the approval slot never crosses the boundary
    bs = out.get(BRAINSTORM_PROGRESS_KEY)
    if isinstance(bs, dict) and bs.get("approved"):
        bs = dict(bs)
        bs["approved"] = False
        bs["approver"] = None
        bs["approved_at"] = None
        events = list(bs.get("events", []))
        events.append(
            "imported: approval not transferred — re-approve on this machine (HARD-GATE)")
        bs["events"] = events
        out[BRAINSTORM_PROGRESS_KEY] = bs
    return out


# ----------------------------------------------------------------------------- verify / load
def verify_bundle(bundle: Any, max_version: int = BUNDLE_SCHEMA_VERSION) -> None:
    """Raise SessionBundleError unless `bundle` is a well-formed session bundle whose stored
    content-hash matches its payload (corruption caught, not served).

    `max_version` is the highest bundle version THIS reader can honor. A bundle whose version
    EXCEEDS it is REFUSED LOUDLY (D6-class) rather than read as if its newer sections did not
    exist — a v1-only reader must never silently strip a v2 bundle's transcript/meta. Defaults to
    the current max; a caller can pass a lower ceiling to prove the refusal (a degraded reader)."""
    if not isinstance(bundle, dict) or bundle.get("kind") != BUNDLE_KIND:
        raise SessionBundleError(f"not a mokata session bundle (kind != '{BUNDLE_KIND}')")
    version = bundle.get("schema_version")
    if isinstance(version, int) and version > max_version:
        raise SessionBundleError(
            f"session bundle is schema v{version} but this mokata reads up to v{max_version} — "
            f"upgrade to pull it (refusing to silently drop its newer sections)")
    if not isinstance(bundle.get("state"), dict):
        raise SessionBundleError("session bundle has no 'state' object")
    stored = bundle.get("content_hash")
    if stored and _hash_core(bundle) != stored:
        raise SessionBundleError("session bundle failed its content-hash check (corrupted)")


def load_bundle(path: str) -> Dict[str, Any]:
    """Read + verify a bundle file. A missing/malformed/corrupt file is a clean error, never a
    crash."""
    if not os.path.exists(path):
        raise SessionBundleError(f"no session bundle at {path}")
    try:
        with open(path, encoding="utf-8") as fh:
            bundle = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SessionBundleError(f"cannot read session bundle {path}: {exc}") from exc
    verify_bundle(bundle)
    return bundle


def parse_bundle(blob: str) -> Dict[str, Any]:
    """Parse + VERIFY a serialized bundle (the bytes a transport returns). The content-hash check
    runs here, so a corrupted REMOTE bundle is caught on read exactly like a corrupt local file —
    the gates live in this module, never in the transport."""
    try:
        bundle = json.loads(blob)
    except ValueError as exc:
        raise SessionBundleError(f"cannot parse session bundle: {exc}") from exc
    verify_bundle(bundle)
    return bundle


def _transport(transport: Any, root: str) -> Any:
    """The given transport, or 55a's LOCAL file store under `root` (the default). Lazy import
    keeps the transport layer optional and breaks the import cycle."""
    if transport is not None:
        return transport
    from .session_transport import LocalTransport
    return LocalTransport(root)


# ----------------------------------------------------------------------------- list
@dataclass
class BundleInfo:
    tag: str
    author: str
    created: str
    source: str
    run_id: Optional[str]
    resume_phase: Optional[str]
    done: int
    total: int
    fingerprint: str
    transport: str = "local"             # which transport this bundle was read from

    def summary(self) -> str:
        where = self.resume_phase or "complete"
        where_from = f" @{self.transport}" if self.transport != "local" else ""
        return (f"{self.tag}{where_from}  [{self.done}/{self.total}]  resume → {where}  "
                f"— {self.author or 'unknown'} · {self.created[:10]}")


def _info_from_bundle(tag: str, bundle: Dict[str, Any], transport: str) -> BundleInfo:
    prov = bundle.get("provenance", {}) or {}
    res = bundle.get("resume", {}) or {}
    return BundleInfo(
        tag=tag, author=prov.get("author", ""), created=prov.get("created", ""),
        source=prov.get("source", ""), run_id=res.get("run_id"),
        resume_phase=res.get("resume_phase"), done=int(res.get("done", 0)),
        total=int(res.get("total", len(PIPELINE_PHASES))),
        fingerprint=bundle.get("repo_fingerprint", ""), transport=transport)


def list_session_bundles(root: str, transport: Any = None) -> List[BundleInfo]:
    """Enumerate the tagged bundles on a transport (tag, provenance, resume point), tag-sorted.
    Default = 55a's LOCAL file store. Read-only; a corrupt bundle is skipped rather than crashing
    the listing (degrade-clean)."""
    t = _transport(transport, root)
    out: List[BundleInfo] = []
    for tag in t.list_tags():
        blob = t.read_bundle(tag)
        if blob is None:
            continue
        try:
            bundle = parse_bundle(blob)
        except SessionBundleError:
            continue
        out.append(_info_from_bundle(tag, bundle, t.name))
    return sorted(out, key=lambda i: i.tag)


def list_session_bundles_across(root: str, transports: List[Any]) -> List[BundleInfo]:
    """List bundles across several transports (local + the configured remote(s)), each row tagged
    with its source. An unavailable remote is skipped CLEAN (no crash) so `list` always answers."""
    from .session_transport import SessionTransportUnavailable
    out: List[BundleInfo] = []
    for t in transports:
        try:
            out += list_session_bundles(root, transport=t)
        except SessionTransportUnavailable:
            continue
    return sorted(out, key=lambda i: (i.tag, i.transport))


# ----------------------------------------------------------------------------- push (plan/commit)
@dataclass
class SessionPushPlan:
    """The computed effect of a push BEFORE any write — so the caller can gate it (vault pattern)."""
    tag: str
    status: str                 # "new" | "unchanged" | "version" | "conflict" | "empty"
    bundle: Dict[str, Any]
    path: str
    prior_hash: Optional[str] = None
    transport: Any = None       # the byte store this push writes through (default: LOCAL)

    @property
    def blocked(self) -> bool:
        return self.status == "conflict"

    def reason(self) -> str:
        if self.status == "empty":
            return ("no session to package — nothing is in progress. Start with "
                    "/mokata:brainstorm (a new problem) or /mokata:refine (existing code).")
        if self.status == "new":
            return f"new session bundle '{self.tag}'"
        if self.status == "unchanged":
            return f"'{self.tag}' already holds this exact session (no-op)"
        if self.status == "version":
            return f"'{self.tag}' is updated to the current session (prior bundle replaced)"
        return (f"'{self.tag}' already exists with a DIFFERENT session — re-push with --force "
                f"to overwrite it; nothing clobbered")


def plan_session_push(root: str, surface: Any, tag: str, run_id: Optional[str] = None,
                      force: bool = False, author: str = "", now: str = "",
                      transport: Any = None, save_first: bool = False,
                      requirements_only: bool = False) -> SessionPushPlan:
    """Compute what a push WOULD do without writing. Idempotent (an identical session = a no-op);
    a changed re-push is a CONFLICT unless `force` (then it overwrites). `transport` selects the
    byte store (default: 55a's LOCAL file store); the gates are identical on every transport.

    `save_first` (SS.S4 --save-first): snapshot the CURRENT session through the SS.S0/SS.S2 UNGATED
    save path FIRST, then bundle that affirmed snapshot — one atomic user action, no gap between
    "what I see" and "what I share". The save is the user's own transient state (P2-exempt); the
    human gate stays at the SHARE boundary below. `requirements_only` (SS.S4 --requirements-only):
    build the cross-repo requirements-only bundle instead of the full session bundle."""
    if save_first:
        from .session_save import save_session
        save_session(surface)                    # UNGATED — re-affirm on-disk state atomically
    t = _transport(transport, root)
    path = t.location(tag)
    bundle = build_session_bundle(surface, run_id=run_id, author=author, now=now,
                                  requirements_only=requirements_only)
    if is_empty_bundle(bundle):
        return SessionPushPlan(tag=_safe_tag(tag), status="empty", bundle=bundle, path=path,
                               transport=t)

    prior_hash: Optional[str] = None
    prior_blob = t.read_bundle(tag)
    if prior_blob is not None:
        try:
            prior_hash = parse_bundle(prior_blob).get("content_hash")
        except SessionBundleError:
            prior_hash = None                # a corrupt prior bundle → treat as overwritable
    if prior_hash is None:
        status = "new"
    elif prior_hash == bundle["content_hash"]:
        status = "unchanged"
    else:
        status = "version" if force else "conflict"
    return SessionPushPlan(tag=_safe_tag(tag), status=status, bundle=bundle, path=path,
                           prior_hash=prior_hash, transport=t)


def _tag_lock(transport: Any, tag: str):
    """The transport's per-tag claim lock (MS.S6). A transport without one (an injected fake, or
    Postgres — where the DB is the serialization point) contributes no lock; the claim RE-CHECK
    below still runs, so a clobber is refused either way."""
    lock = getattr(transport, "lock", None)
    return lock(tag) if callable(lock) else nullcontext(tag)


def _prior_hash(transport: Any, tag: str) -> Optional[str]:
    """The content-hash of whatever occupies `tag` right now — None if free, and None for a corrupt
    prior bundle (which `plan_session_push` also treats as overwritable, so the two agree)."""
    blob = transport.read_bundle(tag)
    if blob is None:
        return None
    try:
        return parse_bundle(blob).get("content_hash")
    except SessionBundleError:
        return None


def commit_session_push(plan: SessionPushPlan) -> str:
    """Write the bundle through the plan's transport (caller must have gated). Never call on a
    `conflict`/`empty` plan. An `unchanged` plan is a no-op. Returns the stored location.

    MS.S6 — the tag is CLAIMED under its lock: the store is re-read and the plan re-checked against
    what is there NOW, because `plan_session_push` read it before the human gate and a sibling window
    could have taken the tag in that window. A lost race surfaces as an honest "claimed by another
    window", never a silently overwritten session. `--force` (`version`) still overwrites, as asked."""
    if plan.blocked:
        raise SessionBundleError("refusing to overwrite a different session without --force")
    if plan.status == "empty":
        raise SessionBundleError("nothing to push (no session in progress)")
    if plan.status == "unchanged":
        return plan.path
    t = _transport(plan.transport, "")
    ours = plan.bundle.get("content_hash")
    with _tag_lock(t, plan.tag):
        prior = _prior_hash(t, plan.tag)
        if prior is not None and prior == ours:
            return plan.path                 # an identical sibling push landed first → idempotent
        if prior is not None and plan.status != "version":
            raise SessionBundleError(
                f"'{plan.tag}' was claimed by another window while this push was being approved and "
                f"now holds a DIFFERENT session — nothing clobbered; re-run the push (with --force "
                f"to overwrite)")
        return t.write_bundle(plan.tag, serialize_bundle(plan.bundle))


@dataclass
class GateResult:
    committed: bool
    reason: str
    findings: List[Any] = field(default_factory=list)


def commit_session_push_gated(plan: SessionPushPlan, *, ledger: Any = None,
                              confirm=None, assume_yes: bool = False,
                              policy: Any = None) -> GateResult:
    """Push through the universal WriteGate: SECRET-SCAN (hard block) + human gate + audit, then
    write the bundle. A secret anywhere in the session content blocks the push even when approved;
    a declined gate writes nothing."""
    from .govern import WriteGate, WriteRequest
    if plan.status == "empty":
        return GateResult(False, plan.reason())
    if plan.blocked:
        return GateResult(False, plan.reason())
    if plan.status == "unchanged":
        return GateResult(False, plan.reason())
    gate = WriteGate(ledger=ledger, trust=policy_trust(policy))
    box: Dict[str, Any] = {}
    outcome = gate.submit(
        WriteRequest("config", plan.path, content=serialize_bundle(plan.bundle), actor="cli",
                     tool=policy_tool(policy, "session_push"),
                     surface=policy_surface(policy, CLI_SURFACE)),
        commit=lambda: box.update(path=commit_session_push(plan)),
        confirm=confirm, assume_yes=assume_yes,
        human_approved=policy_approved(policy))
    return GateResult(outcome.committed, outcome.reason, list(outcome.findings))


# ----------------------------------------------------------------------------- pull (plan/hydrate)
@dataclass
class SessionPullPlan:
    """The computed effect of a pull BEFORE any hydrate. `status` is `missing` (no bundle),
    `mismatch` (a cross-codebase fingerprint difference — surfaced, NOT applied unless forced),
    or `ok`. The content-hash is already verified when the plan is built (corruption caught)."""
    tag: str
    status: str                 # "ok" | "mismatch" | "missing"
    bundle: Optional[Dict[str, Any]] = None
    bundle_fingerprint: str = ""
    target_fingerprint: str = ""
    path: str = ""
    transport: Any = None       # the byte store this pull read from (default: LOCAL)
    cross_repo: bool = False    # SS.S4 — a requirements-only handoff (fingerprint check skipped)
    origin_repo: str = ""       # SS.S4 — the origin-repo LABEL surfaced at the gate for cross-repo

    def reason(self) -> str:
        if self.status == "missing":
            return f"no session bundle tagged '{self.tag}' (try `mokata session list`)"
        if self.status == "mismatch":
            return (f"'{self.tag}' was captured on a DIFFERENT codebase "
                    f"(repo fingerprints differ) — re-pull with --force to apply it here anyway, "
                    f"or pull into the matching repo. Nothing was hydrated.")
        if self.cross_repo:
            return (f"'{self.tag}' carries only DISTILLED REQUIREMENTS captured on "
                    f"'{self.origin_repo or 'another repo'}' — cross-repo by design "
                    f"(fingerprint match skipped); ready to hydrate here.")
        return f"'{self.tag}' is ready to hydrate"


def plan_session_pull(store_root: str, tag: str, target_root: str,
                      force: bool = False, transport: Any = None) -> SessionPullPlan:
    """Read the tagged bundle from a transport (default: `store_root`'s LOCAL store), VERIFY its
    content-hash, and check the bundle's repo fingerprint against the TARGET repo's. A mismatch is
    SURFACED (never silently mis-applied) unless `force`. Degrade-clean: a missing/corrupt bundle
    is a clean status/error, never a crash — on EVERY transport."""
    t = _transport(transport, store_root)
    path = t.location(tag)
    blob = t.read_bundle(tag)
    if blob is None:
        return SessionPullPlan(tag=_safe_tag(tag), status="missing", path=path, transport=t)
    bundle = parse_bundle(blob)              # raises on corruption (caught, not served)
    bundle_fp = bundle.get("repo_fingerprint", "")
    target_fp = repo_fingerprint(target_root)
    # SS.S4 — a requirements-only bundle is CROSS-REPO by design: the fingerprint match is
    # deliberately skipped and the origin-repo LABEL is surfaced at the gate instead (never a
    # silent mis-apply). `cross_repo` is under the content-hash, so it cannot be forged onto a
    # same-repo bundle to bypass the check — same-repo bundles keep the strict fingerprint gate.
    cross_repo = bool(bundle.get("cross_repo"))
    status = "ok"
    if not cross_repo and bundle_fp != target_fp and not force:
        status = "mismatch"
    return SessionPullPlan(tag=_safe_tag(tag), status=status, bundle=bundle,
                           bundle_fingerprint=bundle_fp, target_fingerprint=target_fp,
                           path=path, transport=t, cross_repo=cross_repo,
                           origin_repo=bundle.get("origin_repo", ""))


def hydrate_bundle(target_surface: Any, bundle: Dict[str, Any], *, ledger: Any = None,
                   confirm=None, assume_yes: bool = False,
                   policy: Any = None) -> GateResult:
    """Re-hydrate a bundle into the TARGET repo's StateStore so `mokata resume` continues — but
    the bundle is UNTRUSTED, so this goes through the universal WriteGate: SECRET-SCAN (a secret
    in the bundle is a hard block approval cannot override) + human gate + audit. Only on approval
    are the state keys written; a declined / blocked gate hydrates NOTHING.

    The HARD-GATE survives the round-trip cross-machine: approval AUTHORITY never crosses the
    bundle (SS.S3). Every bundled value is still SECRET-SCANNED (the scan covers the FULL recorded
    state), but what gets WRITTEN is `strip_imported_approval(state)` — the `approved_approach`
    handoff is dropped and the brainstorm's approval flags cleared, so a not-yet-approved AND a
    once-approved session both restore as NOT approved. The receiver re-earns approval through
    their own gate; only PROGRESS is rehydrated."""
    from .govern import WriteGate, WriteRequest
    verify_bundle(bundle)
    full_state = bundle.get("state", {}) or {}
    write_state = strip_imported_approval(full_state)   # approval authority is receiver-owned
    store = target_surface.state
    blob = json.dumps(full_state, sort_keys=True)       # scan EVERY bundled value, approval included
    gate = WriteGate(ledger=ledger, trust=policy_trust(policy))

    def _write_all() -> None:
        for key, data in write_state.items():
            store.write(key, data)

    outcome = gate.submit(
        WriteRequest("config", store.root, content=blob, actor="cli",
                     tool=policy_tool(policy, "session_pull"),
                     surface=policy_surface(policy, CLI_SURFACE)),
        commit=_write_all, confirm=confirm, assume_yes=assume_yes,
        human_approved=policy_approved(policy))
    return GateResult(outcome.committed, outcome.reason, list(outcome.findings))


# ----------------------------------------------------------------------------- rename (gated)
@dataclass
class SessionRenamePlan:
    """The computed effect of a rename BEFORE any write. `status`:
      * `missing`   — no bundle under the source tag;
      * `noop`      — the source already has this name (idempotent — nothing to do);
      * `collision` — a DIFFERENT bundle already holds the new name (refused unless `force` —
                      never a silent clobber);
      * `ok`        — ready to move (provenance preserved; the content-hash is untouched).
    """
    old: str
    new: str
    status: str
    bundle: Optional[Dict[str, Any]] = None
    transport: Any = None
    new_path: str = ""
    # Was this rename authorised to OVERWRITE an existing name? `plan_session_rename(force=True)`
    # keeps `status == "ok"` even over a collision, so the commit (which re-checks the name under
    # the lock, MS.S6) needs to know whether an occupied name is licensed or a race it must refuse.
    forced: bool = False

    def reason(self) -> str:
        if self.status == "missing":
            return f"no session bundle tagged '{self.old}' to rename"
        if self.status == "noop":
            return f"'{self.old}' is already named that (no-op)"
        if self.status == "collision":
            return (f"the name '{self.new}' already holds a DIFFERENT session — rename with "
                    f"--force to overwrite it; nothing clobbered")
        return f"rename '{self.old}' → '{self.new}' (provenance preserved)"


def _record_rename(bundle: Dict[str, Any], old: str, new: str) -> Dict[str, Any]:
    """A copy of `bundle` with the rename recorded in provenance — a `prior_names` trail + the
    current `name`. Provenance is NOT part of the content-hash, so the session payload (and its
    hash) is untouched: a rename never changes what the bundle says, only what we call it."""
    out = dict(bundle)
    prov = dict(out.get("provenance") or {})
    trail = list(prov.get("prior_names") or [])
    trail.append(old)
    prov["prior_names"] = trail
    prov["name"] = new
    out["provenance"] = prov
    return out


def plan_session_rename(root: str, old: str, new: str, transport: Any = None,
                        force: bool = False) -> SessionRenamePlan:
    """Compute what a rename WOULD do without writing. Idempotent (renaming to the current name is
    a no-op); a name collision is REFUSED unless `force` (never a silent clobber). Works on every
    transport. Degrade-clean: a missing source is a clean status, never a crash."""
    t = _transport(transport, root)
    old = _safe_tag(old)
    new = _safe_tag(new)
    if old == new:
        return SessionRenamePlan(old=old, new=new, status="noop", transport=t,
                                 new_path=t.location(new))
    blob = t.read_bundle(old)
    if blob is None:
        return SessionRenamePlan(old=old, new=new, status="missing", transport=t)
    bundle = parse_bundle(blob)              # raises on corruption (caught, not served)
    if t.read_bundle(new) is not None and not force:
        return SessionRenamePlan(old=old, new=new, status="collision", bundle=bundle,
                                 transport=t, new_path=t.location(new))
    renamed = _record_rename(bundle, old, new)
    return SessionRenamePlan(old=old, new=new, status="ok", bundle=renamed, transport=t,
                             new_path=t.location(new), forced=force)


def commit_session_rename(plan: SessionRenamePlan) -> str:
    """Apply a rename: write the bundle under the new tag, then drop the old (caller must have
    gated). Never call on a `collision`/`missing`/`noop` plan. Returns the new location.

    MS.S6 — the target name is re-checked and claimed under ITS lock. `plan_session_rename` saw the
    name free, but the human gate ran in between; without the re-check, a sibling window that took
    the name in that window would be silently overwritten — the exact clobber the `collision` status
    exists to prevent, just moved into the gap."""
    if plan.status != "ok":
        raise SessionBundleError(f"cannot rename ({plan.status}): {plan.reason()}")
    t = plan.transport
    with _tag_lock(t, plan.new):
        if not plan.forced and t.read_bundle(plan.new) is not None:
            raise SessionBundleError(
                f"the name '{plan.new}' was claimed by another window while this rename was being "
                f"approved — nothing clobbered; re-run the rename (with --force to overwrite)")
        loc = t.write_bundle(plan.new, serialize_bundle(plan.bundle))
    t.delete_bundle(plan.old)                # move, not copy — the old name is gone
    return loc


def commit_session_rename_gated(plan: SessionRenamePlan, *, ledger: Any = None,
                                confirm=None, assume_yes: bool = False,
                                policy: Any = None) -> GateResult:
    """Rename through the universal WriteGate (secret-scan + human gate + audit) where it writes
    durable. A `noop`/`missing`/`collision` plan writes nothing (a clear reason). A declined gate
    leaves both names untouched."""
    from .govern import WriteGate, WriteRequest
    if plan.status != "ok":
        return GateResult(False, plan.reason())
    gate = WriteGate(ledger=ledger, trust=policy_trust(policy))
    box: Dict[str, Any] = {}
    outcome = gate.submit(
        WriteRequest("config", plan.new_path, content=serialize_bundle(plan.bundle),
                     tool=policy_tool(policy, "session_name"),
                     surface=policy_surface(policy, CLI_SURFACE),
                     actor="cli"),
        commit=lambda: box.update(path=commit_session_rename(plan)),
        confirm=confirm, assume_yes=assume_yes,
        human_approved=policy_approved(policy))
    return GateResult(outcome.committed, outcome.reason, list(outcome.findings))
