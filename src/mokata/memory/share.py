"""Stage 35b — first-class shared-memory file: `mokata memory export/import`.

The file/offline path for teams who don't run a shared DB (Part 0 is the live store).
`export` writes a COMMITTABLE artifact (default `.mokata/memory-share.json`, NOT under
`temp_local/`) carrying the active memory items WITH provenance — opt-in and read-only on the
source. `import` is a HUMAN-GATED merge into local memory: it dedups, surfaces each new item
for approval, and routes a CONFLICT (same subject, different value) through the existing
self-healing old->new surface — never a silent overwrite (P2/P12). Provenance is preserved.

Local-first (P8): nothing egresses — sharing is via a file the team already syncs (git, etc.),
not a mokata service. Storage-agnostic: works over any `MemoryBackend`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from .healing import CONTRADICTION, HealingProposal
from .item import ACTIVE, MemoryItem
from ..errors import MokataError

MEMORY_SHARE_FILENAME = "memory-share.json"
SHARE_KIND = "mokata-memory-share"
SHARE_SCHEMA_VERSION = 1


class MemoryShareError(MokataError):
    """Raised when a memory-share file is malformed."""


# ----------------------------------------------------------------------------- export
def scan_export_item(item: MemoryItem) -> list:
    """SI.6 (74 C2 / P23) — the egress scan of ONE item bound for an export artifact.

    `for_send=True` is the point: an export is EGRESS. The share file is written to be COMMITTED and
    handed to a teammate, so it is held to the outbound bar, not the at-rest one — exactly as
    `team_journal`'s per-publish scan already holds a shared DB write."""
    from ..govern.secrets import scan
    return scan(text=f"{item.subject}\n{item.value}", path=item.subject, for_send=True)


def export_memory(store: Any, dest: Optional[str] = None) -> dict:
    """Return the active memory items (with provenance) as a shareable dict; optionally
    write it to `dest`. READ-ONLY on the source — it never mutates the store.

    SI.6 (74 C2): every exported value is secret-scanned FIRST, and a hit HARD-BLOCKS that item — it
    is left out of the artifact entirely. This is the hole the SI.3 deviation note flagged: export
    was human-gated but never scanned, so a credential that reached memory through any unscanned
    path (before SI.6, `apply_consolidation` was exactly such a path) would be copied verbatim into
    a file built to be committed and shared. The gate on the export *file* proves a human asked for
    it; only this scan proves a secret does not leave with it.

    The blocked item is named by its KEY (subject), never its value (P23) — a refusal must not print
    the credential it is refusing. The artifact's on-disk shape is unchanged (schema_version / kind /
    items); `blocked` rides the RETURNED dict only, so the recipient of the file never learns which
    keys held secrets."""
    items = [i for i in store.backend.all(statuses=(ACTIVE,))
             if i.mtype in store.enabled_types]
    kept: List[MemoryItem] = []
    blocked: List[str] = []
    for i in items:
        if scan_export_item(i):
            blocked.append(i.subject)        # named by KEY, never value (P23)
            continue
        kept.append(i)
    data = {
        "schema_version": SHARE_SCHEMA_VERSION,   # the SHARE-FILE format (not the memory-DOC axis)
        "kind": SHARE_KIND,
        # `to_dict`, deliberately NOT `to_doc` (D6). An export is a lossless COPY OUT, not a
        # rewrite: it must carry each doc exactly as it is, including one a newer mokata wrote —
        # its version stamp and the fields this build cannot model ride along verbatim, so the
        # teammate who CAN read them gets them whole. Serializing an export through the durable
        # guard would refuse those items (or, worse, ship them stripped) — turning the export
        # itself into the strip D6 exists to prevent. The guard belongs on WRITE-BACK, and the
        # receiving end has it: `import_memory` funnels every item through the store's gated
        # `remember`/`apply_proposal`, which refuse a doc this build may not write.
        "items": [i.to_dict() for i in kept],   # to_dict carries provenance
    }
    if dest is not None:
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2) + "\n")
    return {**data, "blocked": blocked}


def export_payload(data: dict) -> str:
    """The exact bytes an export would write — the content the WriteGate hashes and scans, so the
    ledger's record is of what actually left, not a summary of it."""
    return json.dumps({k: v for k, v in data.items() if k != "blocked"}, indent=2) + "\n"


def load_memory_share(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ----------------------------------------------------------------------------- import
@dataclass
class ImportResult:
    added: List[str] = field(default_factory=list)        # subjects added (new)
    skipped: List[str] = field(default_factory=list)      # already present / dup
    resolved: List[str] = field(default_factory=list)     # conflicts approved (healed)
    declined: List[str] = field(default_factory=list)     # add/conflict declined at the gate
    blocked: List[str] = field(default_factory=list)      # hard-blocked: a secret in the item
    # D6 — items a NEWER mokata wrote: importable only by a build that can read them in full. This
    # build refuses rather than importing a stripped copy. NOT `declined` — nobody declined these;
    # reporting a client refusal as a human decision is the quiet lie CM.S2/D5 exist to kill.
    refused: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    aborted: bool = False
    message: str = ""

    def render(self) -> str:
        if self.aborted:
            return "memory import aborted: " + self.message
        out = (f"memory import: {len(self.added)} added, {len(self.skipped)} skipped "
               f"(dups), {len(self.resolved)} conflict(s) resolved, "
               f"{len(self.declined)} declined.")
        if self.blocked:
            out += f" {len(self.blocked)} BLOCKED (secret detected — not imported)."
        if self.refused:
            out += (f" {len(self.refused)} REFUSED (written by a newer mokata — upgrade to import "
                    f"them; nothing was imported in part).")
        return out


def _validate(data: Any) -> List[str]:
    errs: List[str] = []
    if not isinstance(data, dict):
        return ["share file must be a JSON object"]
    if data.get("kind") != SHARE_KIND:
        errs.append(f"not a mokata memory share (kind != '{SHARE_KIND}')")
    if not isinstance(data.get("items"), list):
        errs.append("share file has no 'items' list")
    return errs


def import_memory(store: Any, data: Any,
                  confirm: Optional[Callable[[str], bool]] = None,
                  assume_yes: bool = False, ledger: Any = None) -> ImportResult:
    """Human-gated merge of a memory-share into local memory. Dedups (by id, and by an
    identical active subject/value), routes a same-subject-different-value CONFLICT through
    the self-healing old->new surface (gated), and gate-adds genuinely new items. Never a
    silent overwrite; provenance is preserved.

    Stage 37R (H1) / Stage 39 (M2): the imported content is UNTRUSTED (a teammate's share file),
    so every per-item commit goes through the ONE universal `WriteGate` — now via the store's own
    gated `remember`/`apply_proposal` (M2): a secret in `subject`/`value` is HARD-BLOCKED (recorded
    as `blocked`, not imported), the rich render_write / old→new healing surface is preserved, and
    each gated decision is audit-logged. When a `ledger` is given, the per-item gate records route
    to it."""
    errs = _validate(data)
    if errs:
        return ImportResult(aborted=True, errors=errs, message="; ".join(errs))

    incoming = [MemoryItem.from_dict(d) for d in data["items"]]
    result = ImportResult()

    existing_ids = {i.id for i in store.backend.all()}
    active = [i for i in store.backend.all(statuses=(ACTIVE,))
              if i.mtype in store.enabled_types]
    active_by_key = {(i.mtype, i.subject): i for i in active}

    # Route the per-item gate's audit records to the caller's ledger (the store's own writes
    # log via store._ledger; for an import we want them on the provided ledger). Restored after.
    prev_ledger = getattr(store, "_ledger", None)
    if ledger is not None:
        store._ledger = ledger
    try:
        for inc in incoming:
            if inc.mtype not in store.enabled_types:
                result.skipped.append(inc.subject)            # type disabled here
                continue
            if inc.id in existing_ids:
                result.skipped.append(inc.subject)            # dedup by id
                continue
            existing = active_by_key.get((inc.mtype, inc.subject))
            if existing is not None and existing.value == inc.value:
                result.skipped.append(inc.subject)            # dedup: identical fact
                continue

            if existing is None:
                # genuinely new — gate-add it (scan + gate + ledger via the store gate, M2)
                res = store.remember(inc, confirm=confirm, assume_yes=assume_yes)
                committed, blocked, bucket = res.committed, res.blocked, result.added
                refused = res.refused
            else:
                # conflict — route through the healing old->new surface (never silent)
                proposal = HealingProposal(
                    kind=CONTRADICTION, subject=inc.subject, mtype=inc.mtype,
                    old=existing, new=inc,
                    rationale="imported fact disagrees with a local one")
                hr = store.apply_proposal(proposal, "approve",
                                          confirm=confirm, assume_yes=assume_yes)
                committed, blocked, bucket = hr.changed, hr.blocked, result.resolved
                refused = hr.refused

            if committed:
                bucket.append(inc.subject)
            elif blocked:
                result.blocked.append(inc.subject)            # secret — hard-blocked, not imported
            elif refused:
                # D6 — a teammate on a NEWER mokata wrote this item. Importing it through this build
                # would re-serialize it, dropping the fields it cannot read: an approved memory
                # arriving already-damaged. Refuse the item, name it, import the rest.
                result.refused.append(inc.subject)
            else:
                result.declined.append(inc.subject)           # human declined at the gate
    finally:
        store._ledger = prev_ledger

    result.message = "ok"
    return result
