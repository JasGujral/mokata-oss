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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import MOKATA_DIR

VAULT_DIRNAME = "vault"
VAULT_INDEX_FILENAME = "index.json"
VAULT_KIND = "mokata-design-vault"
VAULT_SCHEMA_VERSION = 1
ARTIFACT_KINDS = ("brainstorm", "spec")


class VaultError(Exception):
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
    os.makedirs(vault_dir(root), exist_ok=True)
    with open(_index_path(root), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2, sort_keys=True) + "\n")


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
def vault_pull(root: str, name: str, dest: Optional[str] = None) -> Tuple[str, VaultEntry]:
    """Return (content, entry) for a named artifact; optionally write it to `dest`. READ-ONLY
    on the vault. Verifies the stored content hash so a corrupted artifact is caught, not served."""
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
    if entry.content_hash and content_hash(content) != entry.content_hash:
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


def commit_push(root: str, plan: PushPlan, author: str = "", now: str = "") -> VaultEntry:
    """Apply a non-conflicting plan: write the artifact + update the index. Never call this on a
    `conflict` plan (the gate/caller must refuse first). `unchanged` is a metadata-only no-op."""
    if plan.blocked:
        raise VaultError("refusing to clobber an existing entry without --force")
    from .memory.item import now_iso
    ts = now or now_iso()
    data = load_index(root)

    if plan.status == "new":
        entry = VaultEntry(name=plan.name, kind=plan.kind, title=plan.title, author=author,
                           source=plan.source, content_hash=plan.new_hash,
                           created_at=ts, updated_at=ts, version=1, history=[])
    else:
        prior = plan.prior
        entry = VaultEntry(name=plan.name, kind=plan.kind, title=plan.title, author=author,
                           source=plan.source, content_hash=plan.new_hash,
                           created_at=prior.created_at, updated_at=ts,
                           version=plan.next_version, history=list(prior.history))
        if plan.status == "version":
            # keep the prior version's metadata so a clobber is never silent (auditable trail)
            entry.history.append({"version": prior.version,
                                  "content_hash": prior.content_hash,
                                  "updated_at": prior.updated_at, "author": prior.author})

    os.makedirs(vault_dir(root), exist_ok=True)
    with open(_artifact_path(root, plan.name), "w", encoding="utf-8") as fh:
        fh.write(plan.content)
    data["entries"][plan.name] = entry.to_dict()
    _save_index(root, data)
    return entry
