"""Stage 35d — the team design & spec VAULT.

Shared memory carries the distilled *decisions*; the vault carries the *artifacts* — the
brainstorm-plan (the *why*: approach + rationale + options weighed) and the spec (the *what*:
ACs ↔ tests) — as human-readable markdown a teammate can find, pull, and review.

The vault is a COMMITTABLE/synced artifact store at `.mokata/vault/` (the repo root, NOT under
`temp_local/` — so it travels with the repo like `memory-share.json`), no service required
(P8 local-first). Each entry is `<name>.md` plus a record in `index.json` carrying provenance
(author, source path, kind, timestamps) and a content hash.

Flow: **push** (gated, never a silent clobber — a changed re-push needs `--force` and is
versioned, keeping prior-version metadata) → **list / search** (cheap, read-only) → **pull**
(read-only; round-trips the exact content + provenance to a teammate's repo).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from . import MOKATA_DIR
from .atomicfile import atomic_write_text, lock_path_for
from .oslock import file_lock
from .errors import MokataError

VAULT_DIRNAME = "vault"
VAULT_INDEX_FILENAME = "index.json"
VAULT_KIND = "mokata-design-vault"
VAULT_SCHEMA_VERSION = 1
ARTIFACT_KINDS = ("brainstorm", "spec")

# DB.S9 — the ledger kind for an artifact that failed its content-hash check at pull.
VAULT_INTEGRITY_KIND = "vault_integrity"
# Hashes are named by PREFIX in the ledger, never echoed in full: enough to correlate a mismatch,
# not enough to be mistaken for the artifact's identity.
_HASH_PREFIX_LEN = 19          # "sha256:" + 12 hex


class VaultError(MokataError):
    """Raised on a malformed vault index or an invalid push (e.g. a clobber without --force)."""


# ----------------------------------------------------------------------------- paths
def vault_dir(root: str) -> str:
    return os.path.join(root, MOKATA_DIR, VAULT_DIRNAME)


def _index_path(root: str) -> str:
    return os.path.join(vault_dir(root), VAULT_INDEX_FILENAME)


def _artifact_path(root: str, name: str) -> str:
    return os.path.join(vault_dir(root), f"{name}.md")


# ----------------------------------------------------------------------------- helpers
def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_name(name: str) -> str:
    """A vault name is a single path-free slug — reject anything that could escape the dir."""
    if not name or name != os.path.basename(name) or name in (".", ".."):
        raise VaultError(f"invalid vault name '{name}' (use a simple name, no path separators)")
    if any(c in name for c in '/\\:'):
        raise VaultError(f"invalid vault name '{name}'")
    return name


def _extract_title(text: str, fallback: str) -> str:
    """First markdown H1, else the first non-empty line, else the name."""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return fallback


def _infer_kind(name: str, src: str) -> str:
    hay = f"{name} {src}".lower()
    return "spec" if "spec" in hay else "brainstorm"


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())}


# ----------------------------------------------------------------------------- entry model
@dataclass
class VaultEntry:
    name: str
    kind: str
    title: str
    author: str
    source: str
    content_hash: str
    created_at: str
    updated_at: str
    version: int = 1
    history: List[Dict[str, Any]] = field(default_factory=list)   # prior-version metadata

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "kind": self.kind, "title": self.title,
            "author": self.author, "source": self.source,
            "content_hash": self.content_hash, "created_at": self.created_at,
            "updated_at": self.updated_at, "version": self.version,
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "VaultEntry":
        return cls(
            name=d["name"], kind=d.get("kind", "brainstorm"),
            title=d.get("title", d["name"]), author=d.get("author", ""),
            source=d.get("source", ""), content_hash=d.get("content_hash", ""),
            created_at=d.get("created_at", ""), updated_at=d.get("updated_at", ""),
            version=int(d.get("version", 1)), history=list(d.get("history", [])),
        )

    def summary(self) -> str:
        return (f"{self.name}  [{self.kind} v{self.version}]  {self.title}  "
                f"— {self.author or 'unknown'} · {self.updated_at[:10]}")


# ----------------------------------------------------------------------------- index io
def load_index(root: str) -> Dict[str, Any]:
    path = _index_path(root)
    if not os.path.exists(path):
        return {"schema_version": VAULT_SCHEMA_VERSION, "kind": VAULT_KIND, "entries": {}}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or data.get("kind") != VAULT_KIND:
        raise VaultError(f"not a mokata design vault (kind != '{VAULT_KIND}')")
    data.setdefault("entries", {})
    return data


def _save_index(root: str, data: Dict[str, Any]) -> None:
    """Replace the index ATOMICALLY (MS.S6 — same bytes as the plain write it replaces). Callers
    that MUTATE the index must do so inside :func:`index_lock` / :func:`update_index`; this function
    is the write half only."""
    os.makedirs(vault_dir(root), exist_ok=True)
    atomic_write_text(_index_path(root), json.dumps(data, indent=2, sort_keys=True) + "\n")


@contextmanager
def index_lock(root: str) -> Iterator[str]:
    """Hold the cross-process lock on the vault index for the whole block (`oslock`, the shared
    MS.S1 primitive). EVERY read-modify-write of the index — and every check-then-write that decides
    whether a name is free — must run inside this, or two windows can interleave between the check
    and the write."""
    with file_lock(lock_path_for(_index_path(root))) as held:
        yield held


def update_index(root: str, mutator: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Dict[str, Any]:
    """Locked read-modify-write of the vault index (MS.S6 / M-6): load → `mutator` → atomic replace,
    with the lock held across all three. `mutator` receives the CURRENT index and returns the one to
    persist. Two windows adding different entries now both survive; before, each wrote back a whole
    index built from its own stale read and the loser's entries vanished."""
    with index_lock(root):
        data = load_index(root)
        new = mutator(data)
        _save_index(root, new)
        return new


def _entries(root: str) -> List[VaultEntry]:
    data = load_index(root)
    return [VaultEntry.from_dict(d) for d in data["entries"].values()]


# ----------------------------------------------------------------------------- list / search
def vault_list(root: str) -> List[VaultEntry]:
    """All entries, name-sorted. Cheap, read-only."""
    return sorted(_entries(root), key=lambda e: e.name)


@dataclass
class VaultHit:
    entry: VaultEntry
    score: float

    def render(self) -> str:
        return f"[{self.score:.2f}] {self.entry.summary()}"


def vault_search(root: str, query: str) -> List[VaultHit]:
    """Lexical search over name + title + body (Jaccard token overlap), ranked. Read-only.
    Deterministic order: score DESC, then name ASC. Only entries that match at all are returned."""
    q = _tokens(query)
    hits: List[VaultHit] = []
    if not q:
        return hits
    for entry in _entries(root):
        body = ""
        ap = _artifact_path(root, entry.name)
        if os.path.exists(ap):
            with open(ap, encoding="utf-8") as fh:
                body = fh.read()
        text = _tokens(f"{entry.name} {entry.title} {body}")
        if not text:
            continue
        overlap = len(q & text)
        if overlap == 0:
            continue
        hits.append(VaultHit(entry=entry, score=overlap / len(q | text)))
    hits.sort(key=lambda h: (-h.score, h.entry.name))
    return hits


# ----------------------------------------------------------------------------- pull (read-only)
def _record_integrity_failure(root: str, name: str, expected: str, actual: str) -> None:
    """DB.S9 — an integrity failure is an AUDITABLE event, not just a raised error.

    Recorded at the ONE seam every caller already funnels through (`vault_pull`), so the CLI, the
    MCP read tool, `team join`'s untrusted-vault read, and SIMP.S2's deprecation shim all inherit it
    without threading a ledger through. Hashes go in by PREFIX only — enough to correlate the
    mismatch, never a full echo.

    Degrade-clean (P8/D5): the ledger is the audit trail, NOT the guard. If it cannot be written (a
    read-only shared vault, no `.mokata/` at the source) the REFUSAL still stands — a failure to
    record must never become a failure to refuse. But it does not get to be a SECRET either: the
    swallow carries a `note_degraded` notice, so "the vault served corrupt bytes and we could not
    even write that down" is said out loud rather than inferred from an empty ledger."""
    try:
        from .govern.ledger import AuditLedger
        AuditLedger.from_mokata_dir(os.path.join(root, MOKATA_DIR)).record(
            VAULT_INTEGRITY_KIND, name=name, outcome="refused", check="content_hash",
            expected=expected[:_HASH_PREFIX_LEN], actual=actual[:_HASH_PREFIX_LEN])
    except Exception as exc:                # noqa: BLE001 — D5 DEGRADES_LOUD; never mask the refusal
        from .degrade import note_degraded
        note_degraded(
            "vault", exc.__class__.__name__,
            detail=f"integrity failure on '{name}' could NOT be recorded to the audit ledger",
            fallback="the pull is still refused and nothing was copied — only the audit record "
                     "is missing",
            fix="check .mokata/temp_local/audit/ is writable, then re-run the pull to re-record")


def vault_pull(root: str, name: str, dest: Optional[str] = None) -> Tuple[str, VaultEntry]:
    """Return (content, entry) for a named artifact; optionally write it to `dest`. READ-ONLY
    on the vault. Verifies the stored content hash so a corrupted artifact is caught, not served —
    and (DB.S9) RECORDS that catch to the audit ledger, because a vault serving bytes that are not
    the bytes that were gated in is an auditable event, not a private disappointment."""
    name = _safe_name(name)
    data = load_index(root)
    rec = data["entries"].get(name)
    if rec is None:
        raise VaultError(f"no vault entry named '{name}' (try `mokata vault list`)")
    entry = VaultEntry.from_dict(rec)
    ap = _artifact_path(root, name)
    if not os.path.exists(ap):
        raise VaultError(f"vault entry '{name}' is missing its artifact file")
    with open(ap, encoding="utf-8") as fh:
        content = fh.read()
    if entry.content_hash:
        actual = content_hash(content)
        if actual != entry.content_hash:
            # Ledger BEFORE the raise — the copy has not happened and never will, but the fact that
            # this vault served corrupt bytes must survive the exception.
            _record_integrity_failure(root, name, entry.content_hash, actual)
            raise VaultError(f"vault entry '{name}' failed its content-hash check (corrupted)")
    if dest is not None:
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(content)
    return content, entry


# ----------------------------------------------------------------------------- push (gated)
@dataclass
class PushPlan:
    """The computed effect of a push, BEFORE any write — so the caller can gate it."""
    name: str
    kind: str
    title: str
    content: str
    new_hash: str
    status: str                 # "new" | "unchanged" | "version" | "conflict"
    source: str
    prior: Optional[VaultEntry] = None
    next_version: int = 1

    @property
    def blocked(self) -> bool:
        return self.status == "conflict"

    def reason(self) -> str:
        if self.status == "new":
            return f"new entry '{self.name}' [{self.kind}]"
        if self.status == "unchanged":
            return f"'{self.name}' is already in the vault, unchanged (no-op)"
        if self.status == "version":
            return (f"'{self.name}' changes v{self.prior.version} → v{self.next_version} "
                    f"(prior version metadata kept)")
        return (f"'{self.name}' already exists with different content — re-push with --force "
                f"to version it (v{self.prior.version} → v{self.next_version}); nothing clobbered")


def plan_push(root: str, name: str, src_file: str,
              kind: Optional[str] = None, force: bool = False) -> PushPlan:
    """Compute what a push WOULD do without writing. Idempotent (identical content = no-op);
    a changed re-push is a CONFLICT unless `force` (then it versions, keeping prior metadata)."""
    name = _safe_name(name)
    if not os.path.exists(src_file):
        raise VaultError(f"source file not found: {src_file}")
    with open(src_file, encoding="utf-8") as fh:
        content = fh.read()
    kind = kind or _infer_kind(name, src_file)
    if kind not in ARTIFACT_KINDS:
        raise VaultError(f"unknown artifact kind '{kind}'; one of {ARTIFACT_KINDS}")
    new_hash = content_hash(content)
    title = _extract_title(content, name)
    source = os.path.abspath(src_file)

    data = load_index(root)
    rec = data["entries"].get(name)
    if rec is None:
        return PushPlan(name=name, kind=kind, title=title, content=content,
                        new_hash=new_hash, status="new", source=source, next_version=1)
    prior = VaultEntry.from_dict(rec)
    if prior.content_hash == new_hash:
        return PushPlan(name=name, kind=kind, title=title, content=content,
                        new_hash=new_hash, status="unchanged", source=source,
                        prior=prior, next_version=prior.version)
    status = "version" if force else "conflict"
    return PushPlan(name=name, kind=kind, title=title, content=content, new_hash=new_hash,
                    status=status, source=source, prior=prior,
                    next_version=prior.version + 1)


def _claim(plan: PushPlan, prior: Optional[VaultEntry], author: str,
           ts: str) -> Optional[VaultEntry]:
    """Reconcile the plan against the index as it stands RIGHT NOW, under the lock (MS.S6).

    `plan_push` read the index, then the human gate ran — an arbitrarily long window in which
    another Claude Code window could have claimed the same name. Re-deciding here against `prior`
    (the CURRENT entry, or None) is what makes the push a CLAIM rather than a blind overwrite. It
    returns the entry to write, or None when there is nothing to do; it raises rather than clobber
    content this plan never saw.

    Single-process, `prior` is exactly what `plan_push` saw, so every branch reproduces the previous
    behaviour byte-for-byte."""
    if prior is None:                       # the name is free right now → a clean create
        return VaultEntry(name=plan.name, kind=plan.kind, title=plan.title, author=author,
                          source=plan.source, content_hash=plan.new_hash,
                          created_at=ts, updated_at=ts, version=1, history=[])

    if prior.content_hash == plan.new_hash and plan.status == "new":
        # A sibling pushed the IDENTICAL bytes while we waited at the gate. Nothing of ours is lost
        # and nothing of theirs is overwritten — the vault's own idempotency contract (identical
        # content = no-op) already says this is a success, so honour it rather than invent a clash.
        return None

    if plan.status != "version" and prior.content_hash != plan.new_hash:
        # The name now holds content this plan never saw, and this push was not authorised to
        # overwrite anything (only `--force` produces `version`). Fail HONESTLY — a silent clobber
        # here would destroy a teammate's artifact that the plan/gate never showed the human.
        raise VaultError(
            f"'{plan.name}' was claimed by another window while this push was being approved and "
            f"now holds different content — nothing clobbered; re-run the push (with --force to "
            f"version it)")

    # `version` (forced) or an unchanged re-push: build off the CURRENT prior, not the planned one,
    # so a sibling's version bump stays in the trail instead of being erased.
    entry = VaultEntry(name=plan.name, kind=plan.kind, title=plan.title, author=author,
                       source=plan.source, content_hash=plan.new_hash,
                       created_at=prior.created_at, updated_at=ts,
                       version=prior.version + 1 if plan.status == "version" else prior.version,
                       history=list(prior.history))
    if plan.status == "version":
        # keep the prior version's metadata so a clobber is never silent (auditable trail)
        entry.history.append({"version": prior.version,
                              "content_hash": prior.content_hash,
                              "updated_at": prior.updated_at, "author": prior.author})
    return entry


def commit_push(root: str, plan: PushPlan, author: str = "", now: str = "") -> VaultEntry:
    """Apply a non-conflicting plan: write the artifact + update the index. Never call this on a
    `conflict` plan (the gate/caller must refuse first). `unchanged` is a metadata-only no-op.

    MS.S6 — the whole commit (re-read index → decide the claim → write artifact → save index) runs
    under :func:`index_lock`, and the claim is re-checked against the index as it stands NOW. Two
    windows racing the same name therefore end with exactly one winner and an honest "claimed by
    another window" error for the loser — never a merged index or a silently overwritten artifact."""
    if plan.blocked:
        raise VaultError("refusing to clobber an existing entry without --force")
    from .memory.item import now_iso
    ts = now or now_iso()

    with index_lock(root):
        data = load_index(root)
        rec = data["entries"].get(plan.name)
        prior = VaultEntry.from_dict(rec) if rec else None
        entry = _claim(plan, prior, author, ts)
        if entry is None:                   # an identical sibling push already landed → no-op
            return prior
        os.makedirs(vault_dir(root), exist_ok=True)
        atomic_write_text(_artifact_path(root, plan.name), plan.content)
        data["entries"][plan.name] = entry.to_dict()
        _save_index(root, data)
    return entry
