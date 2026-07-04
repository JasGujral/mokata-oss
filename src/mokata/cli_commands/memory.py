"""memory — surface memory (read-only) + share/migrate/edit (human-gated)."""
from __future__ import annotations

import argparse
import os
import sys

from ._common import (
    _current_user,
    MOKATA_DIR,
    AuditLedger,
    MemoryStore,
    _load_surface,
    _SCOPE_CURRENT,
    _review_scope,
    _backend_projects,
)


def cmd_memory(args: argparse.Namespace) -> int:
    # Read-only surface by default; `export`/`import` share memory across repos (Stage 35b).
    surface = _load_surface(args.path)
    store = MemoryStore.from_surface(surface)

    action = getattr(args, "action", None)
    if action == "export":
        from ..memory import MEMORY_SHARE_FILENAME, export_memory
        dest = args.file or os.path.join(args.path, MOKATA_DIR, MEMORY_SHARE_FILENAME)
        data = export_memory(store, dest=dest)   # read-only on the source
        print(f"exported {len(data['items'])} memory item(s) (with provenance) to {dest}")
        return 0
    if action == "import":
        from ..memory import import_memory, load_memory_share
        if not args.file:
            print("error: `memory import <file>` requires a file", file=sys.stderr)
            return 2
        try:
            data = load_memory_share(args.file)
        except (OSError, ValueError) as exc:
            print(f"error: cannot read {args.file}: {exc}", file=sys.stderr)
            return 1
        ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
        res = import_memory(store, data, assume_yes=args.yes, ledger=ledger)
        print(res.render())
        return 1 if res.aborted else 0
    if action == "migrate":
        from ..memory import migrate_memory
        if not args.to:
            print("error: `memory migrate --to <backend>` requires --to "
                  "(sqlite|obsidian|postgres)", file=sys.stderr)
            return 2
        ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
        res = migrate_memory(surface, to_backend=args.to, from_backend=args.from_backend,
                             assume_yes=args.yes, drop_source=args.drop_source, ledger=ledger)
        print(res.render())
        return 1 if res.aborted else 0
    if action == "edit":
        return _memory_edit(store, args)
    if action == "consolidate":
        # C7 — surface PROPOSAL-ONLY consolidations (merge/summarize/prune). Reads only;
        # nothing is applied here (applying stays the gated `apply_consolidation` path).
        if not store.enabled_types:
            print("memory: disabled for this profile (no memory types enabled).")
            return 0
        ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
        proposals = store.propose_consolidations(ledger=ledger)
        if not proposals:
            print("memory consolidate: nothing to propose (memory is already consolidated).")
            return 0
        print(f"memory consolidate — {len(proposals)} proposal(s) (PROPOSAL-ONLY; nothing "
              f"changes unless you approve each via the gated apply path):")
        for p in proposals:
            print(f"  ({p.kind}) [{p.mtype}] {p.subject}: {p.diff()} — {p.rationale}")
        return 0

    if not store.enabled_types:
        print("memory: disabled for this profile (no memory types enabled).")
        return 0

    # Stage 71a — REVIEW scoping over a shared backend. `--list-projects` enumerates the projects
    # present; `--all` spans them; `--project X` selects one; the default is the current project.
    from ..project import project_id
    scope = _review_scope(args)
    if getattr(args, "list_projects", False):
        projs = _backend_projects(store.backend)
        if projs is None:
            print(f"memory backend: {store.backend.name} — local/per-repo (single project: "
                  f"{project_id(surface)}). --list-projects applies to a shared backend.")
            return 0
        print(f"memory backend: {store.backend.name} — {len(projs)} project(s) present:")
        for p in projs:
            here = "  ← current" if p == project_id(surface) else ""
            print(f"  {p}{here}")
        return 0
    if scope is not _SCOPE_CURRENT:
        store = MemoryStore.from_surface(surface, project=scope)   # re-scope for the review view

    # Stage 36 — the project "brain" view: grouped BY KIND (rules / guardrails / best-practices
    # / context / decisions …), optionally filtered to one --kind. A scannable, committed/
    # reviewable artifact, not a flat dump.
    from ..memory import group_by_kind, normalize_kind
    active = store.all_active()
    kind_filter = ""
    if getattr(args, "kind", None):
        kind_filter = normalize_kind(args.kind) or args.kind
        active = [i for i in active if i.effective_kind == kind_filter]

    print(f"memory backend: {store.backend.name} · "
          f"types on: {', '.join(store.enabled_types)}")
    print(store.stats.log_line())
    suffix = f" · kind: {kind_filter}" if kind_filter else ""
    print(f"active items: {len(active)}{suffix}")
    for kind, items in group_by_kind(active).items():
        print(f"\n{kind} ({len(items)}):")
        for it in items:
            print(f"  {it.subject} = {it.value}")

    proposals = store.detect_issues()
    if proposals:
        print(f"\nself-healing — {len(proposals)} item(s) need your decision "
              f"(nothing changes until you act):")
        for p in proposals:
            print(f"  ({p.kind}) [{p.mtype}] {p.subject}: {p.diff()}")

    # Stage 59 — the actionable memory-health nudge (stale · contradictory · unused), derived
    # from the proposals + the C8 ratio. Proposal-only + SILENT when healthy; it points at the
    # gated review path, it never edits/prunes memory.
    from ..memory.intelligence import memory_health
    nudge = memory_health(proposals, store.stats.reads, store.stats.writes).nudge(
        ascii_only=getattr(args, "ascii", False))
    if nudge:
        print(f"\n{nudge}")
    return 0


def _memory_edit(store, args) -> int:
    """Stage 36 — `mokata memory edit <subject> --value <new>`: human-gated, routed through the
    self-healing old→new surface (supersede, never silent). Optionally retype with --kind."""
    from ..memory import CONTRADICTION, HealingProposal, MemoryItem, normalize_kind
    subject = args.file        # the trailing positional carries the subject for `edit`
    if not subject or args.value is None:
        print("error: `memory edit <subject> --value <new value>` requires a subject and "
              "--value", file=sys.stderr)
        return 2
    existing = store.recall(subject)
    if not existing:
        print(f"error: no active memory item with subject '{subject}' to edit "
              f"(use /mokata:onboard to capture it)", file=sys.stderr)
        return 1
    old = existing[0]
    new_kind = (normalize_kind(args.kind) or args.kind) if getattr(args, "kind", None) else old.kind
    new = MemoryItem.create(subject, args.value, mtype=old.mtype, kind=new_kind,
                            author=_current_user() or "user", source="memory-edit")
    if old.value == new.value and new_kind == old.kind:
        print(f"memory: '{subject}' unchanged (no-op).")
        return 0
    proposal = HealingProposal(kind=CONTRADICTION, subject=subject, mtype=old.mtype,
                               old=old, new=new,
                               rationale="user edit via `mokata memory edit`")
    if args.yes:
        res = store.apply_proposal(proposal, "approve", assume_yes=True)
    else:
        # Stage 54c — one-key approve / edit / reject over the old→new diff; SAFE DEFAULT
        # (reject / EOF) = no change. Reuses the existing apply_proposal mechanism; the
        # WriteGate secret hard-block still fires (approve can't override a security block).
        from ..prompt import read_approve_edit_reject
        resp = read_approve_edit_reject(store.render_proposal(proposal), new.value)
        if not resp.is_change:
            print(f"memory: '{subject}' unchanged (no change).")
            return 0
        if resp.action == "edit":
            edited = MemoryItem.create(subject, resp.value, mtype=old.mtype, kind=new_kind,
                                       author=_current_user() or "user",
                                       source="memory-edit")
            res = store.apply_proposal(proposal, "edit", edited=edited, assume_yes=True)
        else:
            res = store.apply_proposal(proposal, "approve", assume_yes=True)
    print(res.message if res.message else ("edited" if res.changed else "no change"))
    return 0 if res.changed or not res.aborted else 1


def register(sub, common):
    p_mem = sub.add_parser(
        "memory", parents=[common],
        help="surface memory (read-only); `export`/`import` to share it across repos",
    )
    p_mem.add_argument("action", nargs="?",
                       choices=("export", "import", "migrate", "edit", "consolidate"),
                       default=None,
                       help="export/import a share file, migrate the store, edit an entry, "
                            "or consolidate (propose-only merges/prunes)")
    p_mem.add_argument("file", nargs="?", default=None,
                       help="share file (export/import), or the subject to edit (with `edit`)")
    p_mem.add_argument("--kind", default=None,
                       help="filter the view to one kind (rule/guardrail/best-practice/"
                            "context/reference/decision), or retype an entry on `edit`")
    p_mem.add_argument("--value", default=None,
                       help="new value (with `edit`)")
    p_mem.add_argument("--to", default=None,
                       help="migrate destination backend (sqlite|obsidian|postgres)")
    p_mem.add_argument("--from", dest="from_backend", default=None,
                       help="migrate source backend (default: the resolved store)")
    p_mem.add_argument("--drop-source", action="store_true",
                       help="after migrating, delete items from the source (separately gated)")
    p_mem.add_argument("--yes", action="store_true",
                       help="non-interactive (approve the gated import/migrate/edit)")
    # Stage 71a — review scoping over a shared backend (default: the current project only).
    p_mem.add_argument("--all", action="store_true",
                       help="review across ALL projects on a shared backend (default: this one)")
    p_mem.add_argument("--project", default=None,
                       help="review a specific project id on a shared backend")
    p_mem.add_argument("--list-projects", dest="list_projects", action="store_true",
                       help="list the projects present on the shared backend, then exit")
    p_mem.set_defaults(func=cmd_memory)


__all__ = [
    "cmd_memory",
    "_memory_edit",
]
