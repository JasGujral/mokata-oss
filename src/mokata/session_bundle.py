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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import MOKATA_DIR
from .brainstorm import APPROACH_STATE_KEY, BRAINSTORM_PROGRESS_KEY, PIPELINE_PHASES
from .engine.spec_gate import SPEC_STATE_KEY
from .govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint

BUNDLE_DIRNAME = "session-bundles"
BUNDLE_KIND = "mokata-session-bundle"
BUNDLE_SCHEMA_VERSION = 1

# The session-scoped (NOT per-run) state keys a bundle carries alongside the run checkpoint(s).
_SESSION_KEYS = (APPROACH_STATE_KEY, SPEC_STATE_KEY, BRAINSTORM_PROGRESS_KEY)

# The fields the content-hash covers — the SUBSTANTIVE payload only, so a re-push of the same
# session at a later time (different provenance timestamp/author) stays idempotent (like vault).
_HASHED_FIELDS = ("schema_version", "kind", "repo_fingerprint", "run_id", "state")


class SessionBundleError(Exception):
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
                         author: str = "", now: str = "") -> Dict[str, Any]:
    """Package the current session into a versioned, MACHINE-PATH-FREE bundle: the run
    checkpoint(s) (the named run, else every recorded run) + the session-scoped keys
    (`emitted_spec`, `approved_approach`, `brainstorm_progress`) + provenance + a content-hash +
    a repo fingerprint. Deterministic given the same `(surface, run_id, author, now)`."""
    from .memory.item import now_iso
    from .progress import list_runs
    store = surface.state

    state: Dict[str, Any] = {}
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

    state = _machine_path_free(state)        # strip machine paths so the bundle travels
    core = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "kind": BUNDLE_KIND,
        "repo_fingerprint": repo_fingerprint(surface.root),
        "run_id": run_id,
        "state": state,
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
    payload = {k: bundle.get(k) for k in _HASHED_FIELDS}
    return content_hash(json.dumps(payload, sort_keys=True))


def reseal_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute the content-hash after an intentional edit (so an edited bundle is internally
    consistent — used to verify the pull-side secret scan catches NASTY-but-not-corrupt content)."""
    out = dict(bundle)
    out["content_hash"] = _hash_core(out)
    return out


def is_empty_bundle(bundle: Dict[str, Any]) -> bool:
    """True when there is no session to package (degrade-clean → a friendly message upstream)."""
    return not bundle.get("state")


# ----------------------------------------------------------------------------- verify / load
def verify_bundle(bundle: Any) -> None:
    """Raise SessionBundleError unless `bundle` is a well-formed session bundle whose stored
    content-hash matches its payload (corruption caught, not served)."""
    if not isinstance(bundle, dict) or bundle.get("kind") != BUNDLE_KIND:
        raise SessionBundleError(f"not a mokata session bundle (kind != '{BUNDLE_KIND}')")
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
                      transport: Any = None) -> SessionPushPlan:
    """Compute what a push WOULD do without writing. Idempotent (an identical session = a no-op);
    a changed re-push is a CONFLICT unless `force` (then it overwrites). `transport` selects the
    byte store (default: 55a's LOCAL file store); the gates are identical on every transport."""
    t = _transport(transport, root)
    path = t.location(tag)
    bundle = build_session_bundle(surface, run_id=run_id, author=author, now=now)
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


def commit_session_push(plan: SessionPushPlan) -> str:
    """Write the bundle through the plan's transport (caller must have gated). Never call on a
    `conflict`/`empty` plan. An `unchanged` plan is a no-op. Returns the stored location."""
    if plan.blocked:
        raise SessionBundleError("refusing to overwrite a different session without --force")
    if plan.status == "empty":
        raise SessionBundleError("nothing to push (no session in progress)")
    if plan.status == "unchanged":
        return plan.path
    t = _transport(plan.transport, "")
    return t.write_bundle(plan.tag, serialize_bundle(plan.bundle))


@dataclass
class GateResult:
    committed: bool
    reason: str
    findings: List[Any] = field(default_factory=list)


def commit_session_push_gated(plan: SessionPushPlan, *, ledger: Any = None,
                              confirm=None, assume_yes: bool = False) -> GateResult:
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
    gate = WriteGate(ledger=ledger)
    box: Dict[str, Any] = {}
    outcome = gate.submit(
        WriteRequest("config", plan.path, content=serialize_bundle(plan.bundle), actor="cli"),
        commit=lambda: box.update(path=commit_session_push(plan)),
        confirm=confirm, assume_yes=assume_yes)
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

    def reason(self) -> str:
        if self.status == "missing":
            return f"no session bundle tagged '{self.tag}' (try `mokata session list`)"
        if self.status == "mismatch":
            return (f"'{self.tag}' was captured on a DIFFERENT codebase "
                    f"(repo fingerprints differ) — re-pull with --force to apply it here anyway, "
                    f"or pull into the matching repo. Nothing was hydrated.")
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
    status = "ok"
    if bundle_fp != target_fp and not force:
        status = "mismatch"
    return SessionPullPlan(tag=_safe_tag(tag), status=status, bundle=bundle,
                           bundle_fingerprint=bundle_fp, target_fingerprint=target_fp,
                           path=path, transport=t)


def hydrate_bundle(target_surface: Any, bundle: Dict[str, Any], *, ledger: Any = None,
                   confirm=None, assume_yes: bool = False) -> GateResult:
    """Re-hydrate a bundle into the TARGET repo's StateStore so `mokata resume` continues — but
    the bundle is UNTRUSTED, so this goes through the universal WriteGate: SECRET-SCAN (a secret
    in the bundle is a hard block approval cannot override) + human gate + audit. Only on approval
    are the state keys written; a declined / blocked gate hydrates NOTHING.

    The HARD-GATE survives the round-trip: this writes exactly the recorded state keys, so a
    not-yet-approved `brainstorm_progress` (approved=False) restores as NOT approved — no approval
    is ever conjured on the far side."""
    from .govern import WriteGate, WriteRequest
    verify_bundle(bundle)
    state = bundle.get("state", {}) or {}
    store = target_surface.state
    blob = json.dumps(state, sort_keys=True)
    gate = WriteGate(ledger=ledger)

    def _write_all() -> None:
        for key, data in state.items():
            store.write(key, data)

    outcome = gate.submit(
        WriteRequest("config", store.root, content=blob, actor="cli"),
        commit=_write_all, confirm=confirm, assume_yes=assume_yes)
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
                             new_path=t.location(new))


def commit_session_rename(plan: SessionRenamePlan) -> str:
    """Apply a rename: write the bundle under the new tag, then drop the old (caller must have
    gated). Never call on a `collision`/`missing`/`noop` plan. Returns the new location."""
    if plan.status != "ok":
        raise SessionBundleError(f"cannot rename ({plan.status}): {plan.reason()}")
    t = plan.transport
    loc = t.write_bundle(plan.new, serialize_bundle(plan.bundle))
    t.delete_bundle(plan.old)                # move, not copy — the old name is gone
    return loc


def commit_session_rename_gated(plan: SessionRenamePlan, *, ledger: Any = None,
                                confirm=None, assume_yes: bool = False) -> GateResult:
    """Rename through the universal WriteGate (secret-scan + human gate + audit) where it writes
    durable. A `noop`/`missing`/`collision` plan writes nothing (a clear reason). A declined gate
    leaves both names untouched."""
    from .govern import WriteGate, WriteRequest
    if plan.status != "ok":
        return GateResult(False, plan.reason())
    gate = WriteGate(ledger=ledger)
    box: Dict[str, Any] = {}
    outcome = gate.submit(
        WriteRequest("config", plan.new_path, content=serialize_bundle(plan.bundle),
                     actor="cli"),
        commit=lambda: box.update(path=commit_session_rename(plan)),
        confirm=confirm, assume_yes=assume_yes)
    return GateResult(outcome.committed, outcome.reason, list(outcome.findings))
