"""memory — surface memory (read-only) + share/migrate/edit (human-gated)."""
from __future__ import annotations

import argparse
import sys

from ._common import (
    _current_user,
    AuditLedger,
    MemoryStore,
    _load_surface,
    _SCOPE_CURRENT,
    _review_scope,
    _backend_projects,
)


def _warn_if_degraded(store) -> None:
    """CM.S2 (C-2) — when this team-mode read was served from the LOCAL fallback, print ONE loud
    notice (names the resolved env-var NAME + failure class), once per subsystem — never per
    read call. A healthy/local store prints nothing (byte-identical)."""
    notice = store.degrade_notice
    if notice is None:
        return
    from ..degrade import emit_degrade_notice
    from ..legibility import _color_enabled
    emit_degrade_notice(notice, out=lambda m: print(m, file=sys.stderr),
                        ascii_only=not _color_enabled())


def cmd_memory(args: argparse.Namespace) -> int:
    # Read-only surface by default; `export`/`import` share memory across repos (Stage 35b).
    surface = _load_surface(args.path)
    store = MemoryStore.from_surface(surface)
    _warn_if_degraded(store)

    action = getattr(args, "action", None)
    if action == "export":
        from .. import deprecation
        from ..govern import WriteGate, WriteRequest
        from ..govern.trust import CLI_SURFACE
        from ..memory import (default_backup_path, export_memory,
                              export_payload, is_legacy_share_dest)
        # 35b — a backup is a FILE the human owns (P23): default to a timestamped
        # `.mokata/backups/memory-<UTC>.json`, NOT the SIMP.S2-deprecated `memory-share.json`
        # channel. An explicit --file wins; writing to the legacy path still works but warns once.
        dest = args.file or default_backup_path(args.path)
        if is_legacy_share_dest(dest):
            deprecation.warn_deprecated("memory-share", surface.mokata_dir)
        data = export_memory(store)              # read-only on the source; scans every value
        blocked = data["blocked"]
        # SI.6 (74 C2): the CLI export had the SAME scanning hole as the MCP one — it wrote a
        # committable, shareable artifact with no secret-scan and no ledger entry. It now goes
        # through the universal WriteGate as kind `send`: an export is EGRESS, so the gate scans the
        # exact bytes that leave at outbound strength and records the write (content hashed).
        #
        # `assume_yes=True` is deliberate and is NOT a weakening: CONSENT here is unchanged — it is
        # the human's explicit `mokata memory export` at a terminal, exactly as before. The gate is
        # added for the SCAN and the LEDGER (the two things C2 is chartered to close), not to invent
        # a prompt this command never had. A secret still HARD-BLOCKS regardless (I1/P2 — no
        # assume_yes lifts a security block). That the CLI export carries no *human gate* of its own
        # is a real and separate gap; it belongs to the CLI-surface bypass cluster filed in doc 84,
        # not to this stage.
        ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
        outcome = WriteGate(ledger=ledger).submit(
            WriteRequest("send", dest, content=export_payload(data), actor="cli",
                         tool="memory_export", surface=CLI_SURFACE),
            commit=lambda: export_memory(store, dest=dest),
            assume_yes=True)
        if blocked:
            print(f"memory export: {len(blocked)} item(s) BLOCKED (secret detected — NOT "
                  f"exported): {', '.join(blocked)}")
        if not outcome.committed:
            print(f"memory export: {outcome.reason} — nothing written.", file=sys.stderr)
            return 1
        print(f"backed up {len(data['items'])} memory item(s) (with provenance) to {dest}")
        return 0
    if action == "import":
        from ..memory import import_memory, load_memory_share, plan_memory_import
        if not args.file:
            print("error: `memory import <file>` requires a backup file", file=sys.stderr)
            return 2
        try:
            data = load_memory_share(args.file)
        except (OSError, ValueError) as exc:
            print(f"error: cannot read {args.file}: {exc}", file=sys.stderr)
            return 1
        # 35b — preview-then-approve (the SIMP.S2 plan/run shape): show counts + a keys-only sample
        # (never a value) FIRST, then run the gated, secret-scanned, provenance-stamped restore.
        print(plan_memory_import(store, data, source=args.file).render())
        ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
        res = import_memory(store, data, assume_yes=args.yes, ledger=ledger, source=args.file)
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
    if action == "reembed":
        # DB.S4 — the GATED re-embed migration. The one way out of a stamp mismatch: the runtime
        # refuses to rank a query against another embedder's vectors (turning the semantic tier
        # off), and this re-computes every vector with the configured embedder, then re-stamps.
        # Previewed before the gate — a count is what makes the question answerable — and a
        # decline writes nothing.
        return _memory_reembed(surface, args)
    if action == "edit":
        return _memory_edit(store, args)
    if action == "promote":
        return _memory_promote(store, args)
    if action == "review":
        return _memory_review(store, args)
    if action == "consolidate":
        # C7 — surface PROPOSAL-ONLY consolidations (merge/summarize/prune). Reads only;
        # nothing is applied here (applying stays the gated `apply_consolidation` path).
        if not store.enabled_types:
            print("memory: disabled for this profile (no memory types enabled).")
            return 0
        ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
        # M-4/R5 PHASE 2 — the agent submits a summary it drafted for one session. Handled before
        # the listing because it is a different verb on the same noun: `--draft` SUBMITS, the bare
        # command LISTS.
        if getattr(args, "draft", None):
            return _memory_consolidate_draft(store, args, ledger)
        proposals = store.propose_consolidations(ledger=ledger)
        # M-4/R5 PHASE 1 — the DRAFTING REQUEST, opt-in. Answered BEFORE the empty-case prose
        # below: this caller is a machine parsing JSON, and handing it an English sentence when
        # there happens to be nothing to draft is a parse error, not an answer. Nothing to draft is
        # a valid answer and must be said in the same shape as everything else.
        if getattr(args, "drafting_request", False):
            return _memory_consolidate_drafting_request(proposals)
        if not proposals:
            print("memory consolidate: nothing to propose (memory is already consolidated).")
            return 0
        # The bare command's output below is untouched: a human at a terminal cannot draft a
        # summary and has no use for a dump of raw turns, so the turns are shown only on request.
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
        # D5 — a FAILED query on the shared backend used to come back as the same `None` a LOCAL
        # backend returns, so an unreachable Postgres printed "postgres — local/per-repo (single
        # project: X)": the backend named as shared in the same breath it is called per-repo. Now
        # only a genuinely local backend reaches the `is None` branch; a query failure says so.
        try:
            projs = _backend_projects(store.backend)
        except Exception as exc:                                 # degrade-clean, never a lie
            print(f"memory: shared backend unavailable ({exc}).")
            return 0
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


def _memory_reembed(surface, args) -> int:
    """DB.S4 — `mokata memory reembed`: re-compute the vector index with the configured embedder.

    Builds the pgvector store NON-degrading (via the migrate path): degrading to the SQLite floor
    here would report a successful re-embed of a store that has no vectors at all."""
    from ..govern import AuditLedger
    from ..memory.migrate import MigrateError, build_named_backend
    from ..memory.reembed import run_reembed
    from ..project import project_id
    project = project_id(surface)
    try:
        backend = build_named_backend("pgvector", surface.mokata_dir,
                                      surface.manifest.tool_config("pgvector"), project=project)
    except MigrateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        res = run_reembed(backend, assume_yes=args.yes, project=project,
                          ledger=AuditLedger.from_mokata_dir(surface.mokata_dir))
    finally:
        backend.close()
    print(res.render())
    return 1 if res.aborted else 0


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
                            author=_current_user() or "user", source="memory-edit",
                            scope_level=old.scope_level, scope_id=old.scope_id)
    if old.value == new.value and new_kind == old.kind:
        print(f"memory: '{subject}' unchanged (no-op).")
        return 0
    # TM.S11 — in TEAM mode an editor edit ENTERS the review workflow as a Draft (proposer ≠
    # approver enforced downstream); it does not go live until a PM approves + publishes. In local
    # mode (no policy) the edit applies directly via the self-healing supersede — byte-identical.
    if store.review_required():
        res = store.propose(new, base_id=old.id, change="edit",
                            proposer=_current_user() or None, assume_yes=args.yes)
        print(res.message)
        if res.ok:
            print("  → review it with `mokata memory review` (a PM must approve + publish).")
        return 0 if res.ok or not res.aborted else 1
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


def _memory_consolidate_drafting_request(proposals) -> int:
    """M-4/R5 PHASE 1 — print the SUMMARIZE proposals WITH the turns to draft from, as JSON.

    JSON because the consumer is the harness agent, not a human: it has to read the turns back
    accurately and then submit a draft keyed to the right session, and a prose dump invites both
    mistakes. The instruction rides IN the payload from the single shared constant, so the CLI and
    the MCP tool cannot drift into two differently-worded asks."""
    import json

    from ..memory.consolidation import DRAFTING_INSTRUCTION, drafting_request

    requests = drafting_request(proposals)
    print(json.dumps({"instruction": DRAFTING_INSTRUCTION, "drafting_requests": requests},
                     indent=2))
    return 0


def _memory_consolidate_draft(store, args, ledger) -> int:
    """M-4/R5 PHASE 2 — submit an agent-DRAFTED summary for one session, through the gated apply.

    THE FIRST PRODUCT CALLER of `apply_consolidation`: before this the gated apply existed and was
    reachable only from library code, so no consolidation of any kind could actually be applied.

    The submitted text is injected as a DRAFTER rather than written onto an item by hand, so it
    travels the one path a drafted summary has (`constant_drafter` explains why). Then the proposal
    is applied exactly as `_memory_edit` applies a healing proposal: render the diff, let the human
    choose, and let `apply_consolidation` do the secret-scan + WriteGate + ledger.

    The agent CANNOT approve its own draft. `--yes` is deliberately NOT honoured here (it is on the
    parser for the other memory actions): a model that could pass it would be minting the consent
    for content it just wrote, which is precisely what `approval.py` forbids — the model may
    reference an approval, never mint one. The human answers at the TTY, or nothing is written."""
    session = args.draft
    value = getattr(args, "value", None)
    if not value:
        print("error: `memory consolidate --draft <session>` requires --value \"<summary>\" "
              "(the summary YOU drafted; mokata does not write it)", file=sys.stderr)
        return 2

    from ..memory.consolidation import constant_drafter, find_summarize

    proposals = store.propose_consolidations(ledger=ledger,
                                             drafter=constant_drafter(session, value))
    match = find_summarize(proposals, session)
    if match is None:
        print(f"error: no summarize proposal for session '{session}' — run "
              f"`mokata memory consolidate` to see what is proposed", file=sys.stderr)
        return 1

    from ..prompt import read_approve_edit_reject
    resp = read_approve_edit_reject(store.render_consolidation(match), match.new.value)
    if not resp.is_change:
        print(f"memory consolidate: '{session}' unchanged (no change).")
        return 0
    if resp.action == "edit":
        # The human rewrote the agent's draft at the gate. Re-derive through the SAME seam so the
        # human's text is typed and clamped identically — an edited draft is still a drafted
        # summary, and must not enter by a different door than the one it was proposed through.
        if not (resp.value or "").strip():
            print(f"memory consolidate: '{session}' unchanged (empty edit).")
            return 0
        edited_proposals = store.propose_consolidations(
            drafter=constant_drafter(session, resp.value))
        edited_match = find_summarize(edited_proposals, session)
        if edited_match is None or edited_match.new is None:
            # REFUSE rather than fall through. `apply_consolidation(edited=None)` would store
            # `match.new` — the AGENT's draft — while the human believes they replaced it, which is
            # the one outcome an edit at the gate must never produce.
            print(f"error: could not stage your edited summary for '{session}'; nothing was "
                  f"written", file=sys.stderr)
            return 1
        res = store.apply_consolidation(match, "edit", edited=edited_match.new,
                                        assume_yes=True, ledger=ledger)
    else:
        res = store.apply_consolidation(match, "approve", assume_yes=True, ledger=ledger)
    print(res.message if res.message else ("applied" if res.changed else "no change"))
    return 0 if res.changed or not res.aborted else 1


def _memory_promote(store, args) -> int:
    """TM.S7 — `mokata memory promote <id> --to advisory|soft|hard`: the ONE gated moment that
    changes a rule's ENFORCEMENT binding (doc 62 §4). Binding only — the rule text is untouched;
    the change is human-gated + ledgered. A lower `--to` is a (still-gated) demotion, reported
    honestly. The trailing positional carries the item id (like `edit` carries the subject)."""
    from ..memory import ENFORCEMENT_LEVELS
    item_id = args.file
    to = args.to
    if not item_id or not to:
        print("error: `memory promote <id> --to advisory|soft|hard` requires an item id and "
              "--to", file=sys.stderr)
        return 2
    if to not in ENFORCEMENT_LEVELS:
        print(f"error: --to must be one of {', '.join(ENFORCEMENT_LEVELS)}", file=sys.stderr)
        return 2
    res = store.promote(item_id, to, assume_yes=args.yes, actor=_current_user() or "user")
    print(res.message)
    # unchanged / committed → success; declined / not-found / non-rule → non-zero.
    return 0 if res.changed or (res.aborted is False) else 1


def _memory_review(store, args) -> int:
    """TM.S11 — `mokata memory review`: the PM review surface (CLI). With no op it LISTS the pending
    proposals (Draft / In-Review / Approved) with a rendered "what changed" diff; `--submit`,
    `--approve`, `--reject`, `--publish`, `--rollback <id>` act on one, each human-gated + ledgered
    with separation of duties (proposer ≠ approver) enforced fail-closed. The rich visual dashboard
    is the 0.0.12 cockpit (B6) — this is the mechanism + CLI (doc 63 §5)."""
    from ..memory import diff_line, required_approvers

    # An op flag carries the target item id (unambiguous — the trailing positional stays free).
    # The actor is the store's own run identity (S10 team_audit.actor()); the ops default to it, so
    # the PM acts AS themselves and separation of duties (proposer ≠ approver) is real.
    ops = [("submit", store.submit_for_review), ("approve", store.approve),
           ("reject", store.reject), ("publish", store.publish), ("rollback", store.rollback)]
    for name, fn in ops:
        item_id = getattr(args, name, None)
        if item_id:
            res = fn(item_id, assume_yes=args.yes)
            print(res.message)
            return 0 if res.ok else 1

    # Default: list the pending proposals with their diffs (read-only).
    pending = store.pending_reviews()
    if not pending:
        print("memory review: no pending proposals (nothing awaiting review).")
        if not store.review_required():
            print("  (local mode — edits publish directly; no review is required.)")
        return 0
    print(f"memory review — {len(pending)} proposal(s) awaiting a decision "
          f"(nothing is live until PUBLISHED):")
    for it in pending:
        rv = it.review or {}
        state = rv.get("state", "?")
        base = store.get(rv.get("base_id", "")) if rv.get("base_id") else None
        scope, cat, _o = store._item_scope_cat(it)
        req = required_approvers(store.access, scope, cat)
        req_str = f" · approver(s): {', '.join(sorted(req))}" if req else ""
        print(f"  [{state}] {it.subject} ({it.id})  by {rv.get('proposer', '?')}{req_str}")
        print(f"      what changed: {diff_line(base, it)}")
    print("\nAct with: `mokata memory review --approve|--reject|--publish|--submit|--rollback "
          "<id>` (each gated + ledgered; the proposer cannot self-approve).")
    return 0


def register(sub, common):
    p_mem = sub.add_parser(
        "memory", parents=[common],
        help="surface memory (read-only); `export` backs it up to a file, `import` restores it",
    )
    p_mem.add_argument("action", nargs="?",
                       choices=("export", "import", "migrate", "reembed", "edit", "consolidate",
                                "promote", "review"),
                       default=None,
                       help="export (back up memory to a file) / import (restore a backup, gated), "
                            "migrate the store, reembed the vector index (gated), edit an entry, "
                            "consolidate (propose-only), promote a rule's enforcement binding, or "
                            "review pending memory proposals")
    p_mem.add_argument("file", nargs="?", default=None,
                       help="backup file (export dest / import source), the subject to edit, or "
                            "the item id to promote")
    p_mem.add_argument("--kind", default=None,
                       help="filter the view to one kind (rule/guardrail/best-practice/"
                            "context/reference/decision), or retype an entry on `edit`")
    p_mem.add_argument("--value", default=None,
                       help="new value (with `edit`), or the summary you drafted (with "
                            "`consolidate --draft`)")
    # M-4/R5 — the two-phase drafted-summary flow. `--drafting-request` is phase 1 (what should be
    # drafted, with the turns); `--draft` is phase 2 (here is the draft). Both are opt-in, so the
    # bare `mokata memory consolidate` a human runs is byte-identical to before.
    p_mem.add_argument("--drafting-request", dest="drafting_request", action="store_true",
                       help="with `consolidate`: emit the summarize proposals AND the turns to "
                            "draft from, as JSON (for the agent — mokata never drafts itself)")
    p_mem.add_argument("--draft", metavar="SESSION", default=None,
                       help="with `consolidate`: submit the summary you drafted for SESSION "
                            "(needs --value); human-gated before anything is written")
    p_mem.add_argument("--to", default=None,
                       help="migrate destination backend (sqlite|obsidian|postgres|pgvector), or the "
                            "target enforcement (advisory|soft|hard) with `promote`")
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
    # TM.S11 — review-workflow ops (each carries the target proposal id): `memory review --approve
    # <id>` etc. Kept as options so the id is unambiguous and the trailing positional stays free.
    p_mem.add_argument("--submit", metavar="ID", default=None,
                       help="(review) submit a Draft for review: Draft → In-Review")
    p_mem.add_argument("--approve", metavar="ID", default=None,
                       help="(review) approve a proposal: In-Review → Approved (proposer≠approver)")
    p_mem.add_argument("--reject", metavar="ID", default=None,
                       help="(review) reject a proposal (terminal)")
    p_mem.add_argument("--publish", metavar="ID", default=None,
                       help="(review) publish an Approved proposal: Approved → Published (live)")
    p_mem.add_argument("--rollback", metavar="ID", default=None,
                       help="(review) roll back a published change, restoring the prior")
    p_mem.set_defaults(func=cmd_memory)


__all__ = [
    "cmd_memory",
    "_memory_edit",
]
