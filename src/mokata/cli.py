"""mokata CLI — the spine's command surface.

Stage 1 commands (the conductor everything else plugs into):
  init       A7  scaffold a valid config; detect tools; pick a profile
  bootstrap  A4  print the SessionStart briefing (and its token count)
  validate   A1  parse + validate the committed manifest
  route      A2  resolve a capability to its tool (and fallback)
  detect     A3  show tool-presence for the whole catalog
  status         one-line summary of the current stack
  brainstorm D6  launch the Socratic pre-spec brainstorm (standalone, L1)
  query      B2  run a structural query (graph if present, else grep floor)
  memory     C   surface memory + healing proposals (read-only, human-gated writes)
  skills     L4  list the skill/command catalog (name for detail)
  run        L1  run a skill/command standalone (no pipeline prerequisite)
  enter      L2  enter the pipeline at a phase (only that phase's gates apply)
  rules      G1  show the 4-tier rules and their line budgets
  audit      I3  show the append-only audit ledger
  budget     F5  show token savings (live budget + statusline)
  index      B4  build/refresh the freshness index; report stale files
  lat-check  B5  scan @lat anchors and flag concept drift
  coverage   A6  capability coverage + unmet gaps + overlaps
  mcp        H4  discover MCP servers and map them to roles
  doctor     K5  diagnose the manifest/config
  baseline       report the test suite green/red at baseline (degrade-clean)
  config     K1  get/set backend config in the manifest (set is human-gated)
  reset      K6  remove mokata state (uninstall / reset)
  suggest    L6  suggest a relevant command (never runs it)
  chain      L5  plan a manual chain of skills (gates still apply)
  export     J3  export the current manifest as a shareable stack
  import     J3  validate + apply a shared stack manifest (human-gated)
  harness    J2  show the harness boundary's capabilities
  exec       E8  show/select the execution mode (sequential default / parallel)
  playbook       run the full v1 story end-to-end (integration check)
  preview    E7  dry-run: planned phases + gates + file touches (no side effects)
  progress       run-progress tracker (done/current/pending); read-only over run-state

Later stages add more subcommands; this keeps the spine usable from the shell today.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

from . import __version__
from .bootstrap import build_bootstrap
from .brainstorm import ground, load_approved_approach, render_launch
from .config import ConfigError, Surface
from . import config_cmd
from .detect import Detector
from .init import init_repo, plan_init, render_plan
from .prompt import read_yes_no
from . import MOKATA_DIR
from .adapters import (
    AdapterContract,
    MCPRegistry,
    negotiate,
    overlapping_capabilities,
)
from .brainstorm import PIPELINE_PHASES
from .compose import SuggestionContext, plan_chain, suggest
from .execmode import (
    PARALLEL,
    SEQUENTIAL,
    ExecutionChoice,
    resolve_execution_choice,
)
from .govern import (
    AuditLedger,
    BudgetReport,
    budget_statusline,
    diagnose,
    load_rules,
    plan_reset,
    reset_state,
    validate_caps,
    WriteGate,
    WriteRequest,
)
from .harness import HARNESS_CAPABILITIES, claude_code_harness
from .share import SHARE_FILENAME, apply_manifest, export_manifest, load_shared
from .knowledge import QUERY_KINDS, KnowledgeIndex, KnowledgeLayer, lat_check
from .manifest import ManifestError
from .memory import MemoryStore
from .engine import preview_pipeline
from .pipeline import ENTRY_PHASES, PhaseError, plan_entry, render_entry
from .playbook import run_playbook
from .skills import SKILL_NAMES, SkillNotFound, get_skill, list_skills, render_skill
from .profiles import DEFAULT_PROFILE, TOOL_CATALOG, profile_names
from .harness_setup import (
    HARNESSES,
    SCOPES,
    SetupError,
    setup_harness,
    unsetup_harness,
)


def _load_surface(root: str) -> Surface:
    try:
        return Surface.load(root)
    except (ConfigError, ManifestError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)


def cmd_init(args: argparse.Namespace) -> int:
    if getattr(args, "preview", False):
        # Dry-run for the human gate (Stage 23): print the plan, write nothing, exit 0.
        # Used by /mokata:init to preview before the user approves the real write.
        print(render_plan(plan_init(args.path, args.profile)))
        return 0
    result = init_repo(
        root=args.path,
        profile=args.profile,
        assume_yes=args.yes,
        force=args.force,
    )
    if result.aborted:
        print(f"\n{result.message}", file=sys.stderr)
        return 1
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    try:
        result = setup_harness(
            harness=args.harness,
            root=args.path,
            scope=args.scope,
            profile=args.profile,
            with_hooks=not args.no_hooks,
            assume_yes=args.yes,
            force=args.force,
        )
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if result.aborted:
        print(f"\n{result.message}", file=sys.stderr)
        return 1
    return 0


def cmd_unsetup(args: argparse.Namespace) -> int:
    try:
        result = unsetup_harness(
            harness=args.harness,
            root=args.path,
            scope=args.scope,
            assume_yes=args.yes,
        )
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if result.aborted:
        print(f"\n{result.message}", file=sys.stderr)
        return 1
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    result = build_bootstrap(surface)
    sys.stdout.write(result.text)
    if args.show_tokens:
        status = "OK" if result.within_budget else "OVER BUDGET"
        print(
            f"\n[tokens ~{result.token_estimate} / budget {result.budget} — {status}]",
            file=sys.stderr,
        )
    return 0 if result.within_budget else 1


def cmd_validate(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    m = surface.manifest
    print(
        f"OK — manifest valid: profile '{m.profile}', "
        f"{len(m.capabilities)} capabilit{'y' if len(m.capabilities) == 1 else 'ies'}, "
        f"{len(m.tools)} tool(s)."
    )
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    try:
        targets = [args.need] if args.need else list(surface.manifest.capabilities)
        for need in targets:
            r = surface.router.resolve(need)
            chain = ", ".join(
                f"{t}{'+' if present else '-'}" for t, present in r.attempted
            )
            print(r.summary())
            print(f"    attempted: {chain}")
            print(f"    reason: {r.reason}")
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    detector = Detector()
    for tid in sorted(TOOL_CATALOG):
        present = detector.is_present(tid, TOOL_CATALOG[tid])
        mark = "present" if present else "absent "
        print(f"[{mark}] {tid}  ({TOOL_CATALOG[tid]['provides']})")
    return 0


def cmd_brainstorm(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    if args.status:
        handoff = load_approved_approach(surface.state)
        if handoff is None:
            print("brainstorm: no approved approach persisted yet.")
        else:
            print(
                f"brainstorm: approved approach '{handoff.approach.name}' for topic "
                f"'{handoff.topic}' (by {handoff.approver} at {handoff.approved_at})."
            )
        return 0
    # Stage 50 — resume a saved IN-PROGRESS brainstorm if one exists (left mid-stream). The
    # HARD-GATE still holds: this only re-hydrates exploration; nothing is approved by resuming.
    from .brainstorm import restore_brainstorm_progress
    wip = restore_brainstorm_progress(surface.state)
    if wip is not None and not wip.approved:
        print(f"mokata brainstorm: resuming in-progress brainstorm for '{wip.topic}' — "
              f"{len(wip.answered_questions)} answered question(s), {len(wip.approaches)} "
              f"approach(es) on the table; NOT yet approved (the spec stays HARD-GATED).\n")
        sys.stdout.write(wip.design_writeup())
        return 0
    # Standalone launch (L1): print the clean-room protocol + live grounding. No prior
    # pipeline phase is required to run this.
    grounding = ground(surface.router)
    sys.stdout.write(render_launch(grounding))
    return 0


def cmd_onboard(args: argparse.Namespace) -> int:
    # Stage 36 — guided, LLM-driven capture of typed project knowledge (like brainstorm). Prints
    # the clean-room protocol + live grounding; persistence happens through the gated writes the
    # protocol drives. Runs standalone, no prior phase required; degrades cleanly uninitialized.
    skill = get_skill("onboard")
    surface = None
    if Surface.is_initialized(args.path):
        try:
            surface = Surface.load(args.path)
        except (ConfigError, ManifestError):
            surface = None
    grounding = ground(surface.router) if surface is not None else ground(None)
    sys.stdout.write(render_skill(skill, grounding))
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    layer = KnowledgeLayer.from_surface(surface)
    result = layer._run(args.kind, args.target, depth=args.depth)
    mode = "graph" if not result.degraded else "grep fallback"
    print(
        f"{result.kind}({result.target}) via {result.backend} [{mode}] — "
        f"{result.count} result(s)"
    )
    for ref in result.references:
        sym = f"  «{ref.symbol}»" if ref.symbol else ""
        print(f"  {ref.path}:{ref.line}{sym}  {ref.snippet}")
    if result.note:
        print(f"  ({result.note})")
    return 0


def _split_csv(value: Optional[str]) -> list:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def cmd_spec_check(args: argparse.Namespace) -> int:
    # Stage 37 — spec-awareness / regression guard: cross-check a change's touch-set against the
    # saved specs + decision memory; surface a conflict and route it through the deviation gate
    # (human-gated, logged). Degrade-clean: no corpus -> no-op; no graph -> lexical/file overlap.
    from .engine import ChangeSet, guard_change, load_decisions, load_spec_corpus
    from .govern import AuditLedger
    surface = _load_surface(args.path)
    change = ChangeSet(symbols=_split_csv(args.symbols), files=_split_csv(args.files),
                       text=args.text or "")
    specs = load_spec_corpus(surface.state)
    store = MemoryStore.from_surface(surface)
    decisions = load_decisions(store)
    layer = KnowledgeLayer.from_surface(surface)
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    outcome = guard_change(change, specs=specs, decisions=decisions, layer=layer,
                           ledger=ledger, phase=args.phase, assume_yes=args.yes)
    print(outcome.render())
    return 0 if outcome.proceeded else 1


def cmd_memory(args: argparse.Namespace) -> int:
    # Read-only surface by default; `export`/`import` share memory across repos (Stage 35b).
    surface = _load_surface(args.path)
    store = MemoryStore.from_surface(surface)

    action = getattr(args, "action", None)
    if action == "export":
        from .memory import MEMORY_SHARE_FILENAME, export_memory
        dest = args.file or os.path.join(args.path, MOKATA_DIR, MEMORY_SHARE_FILENAME)
        data = export_memory(store, dest=dest)   # read-only on the source
        print(f"exported {len(data['items'])} memory item(s) (with provenance) to {dest}")
        return 0
    if action == "import":
        from .memory import import_memory, load_memory_share
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
        from .memory import migrate_memory
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

    # Stage 36 — the project "brain" view: grouped BY KIND (rules / guardrails / best-practices
    # / context / decisions …), optionally filtered to one --kind. A scannable, committed/
    # reviewable artifact, not a flat dump.
    from .memory import group_by_kind, normalize_kind
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
    return 0


def _memory_edit(store, args) -> int:
    """Stage 36 — `mokata memory edit <subject> --value <new>`: human-gated, routed through the
    self-healing old→new surface (supersede, never silent). Optionally retype with --kind."""
    from .memory import CONTRADICTION, HealingProposal, MemoryItem, normalize_kind
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
                            author=os.environ.get("USER") or "user", source="memory-edit")
    if old.value == new.value and new_kind == old.kind:
        print(f"memory: '{subject}' unchanged (no-op).")
        return 0
    proposal = HealingProposal(kind=CONTRADICTION, subject=subject, mtype=old.mtype,
                               old=old, new=new,
                               rationale="user edit via `mokata memory edit`")
    res = store.apply_proposal(proposal, "approve", assume_yes=args.yes)
    print(res.message if res.message else ("edited" if res.changed else "no change"))
    return 0 if res.changed or not res.aborted else 1


def cmd_vault(args: argparse.Namespace) -> int:
    # Stage 35d — team design & spec vault: push (gated) → list/search/pull (read-only).
    from . import vault as V
    action = args.action
    root = args.path

    if action == "list":
        entries = V.vault_list(root)
        if not entries:
            print("vault: empty — `mokata vault push <name> <file>` to add a brainstorm/spec.")
            return 0
        print(f"vault: {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} "
              f"(.mokata/{V.VAULT_DIRNAME}/)")
        for e in entries:
            print(f"  {e.summary()}")
        return 0

    if action == "search":
        # the query is the `name` positional (quote multi-word: vault search "payments redesign")
        query = " ".join(p for p in (args.name, args.file) if p)
        if not query:
            print("error: `vault search <query>` requires a query", file=sys.stderr)
            return 2
        hits = V.vault_search(root, query)
        if not hits:
            print(f"vault: no matches for {query!r}")
            return 0
        print(f"vault: {len(hits)} match(es) for {query!r}")
        for h in hits:
            print(f"  {h.render()}")
        return 0

    if action == "pull":
        if not args.name:
            print("error: `vault pull <name> [dest]` requires a name", file=sys.stderr)
            return 2
        dest = args.dest or os.path.join(root, f"{args.name}.md")
        try:
            _content, entry = V.vault_pull(root, args.name, dest=dest)
        except V.VaultError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"pulled '{entry.name}' [{entry.kind} v{entry.version}] → {dest}  "
              f"(by {entry.author or 'unknown'} · {entry.updated_at[:10]})")
        return 0

    if action == "push":
        if not args.name or not args.file:
            print("error: `vault push <name> <file>` requires a name and a file",
                  file=sys.stderr)
            return 2
        try:
            plan = V.plan_push(root, args.name, args.file, kind=args.kind, force=args.force)
        except V.VaultError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if plan.status == "unchanged":
            print(f"vault: {plan.reason()}")
            return 0
        if plan.blocked:
            print(f"vault: {plan.reason()}", file=sys.stderr)
            return 1
        # Durable write → universal gate (secret-scan + human approval + audit).
        surface = _load_surface(root)
        ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
        # I4 — publishing to the shared vault is an OUTBOUND action. If the artifact
        # carries private data, the lethal trifecta is live, so gate the publish behind
        # explicit human approval + audit. Clean content is not gated (degrade-clean;
        # normal pushes are untouched).
        from .govern import OutboundRequest, gate_outbound_publish, looks_private
        if looks_private(plan.content):
            decision = gate_outbound_publish(
                OutboundRequest("vault-push", f"vault:{plan.name}", payload=plan.content),
                private_data=True, ledger=ledger, confirm=_cli_ask, assume_yes=args.yes)
            if not decision.allowed:
                print(f"vault push blocked — {decision.reason}", file=sys.stderr)
                return 1
        gate = WriteGate(ledger=ledger)
        author = args.author or os.environ.get("USER") or ""
        box: Dict[str, Any] = {}
        outcome = gate.submit(
            WriteRequest("config", V._artifact_path(root, plan.name),
                         content=plan.content, actor="cli"),
            commit=lambda: box.update(
                entry=V.commit_push(root, plan, author=author)),
            assume_yes=args.yes,
        )
        if not outcome.committed:
            print(f"vault push {outcome.reason}"
                  + (f" — {[f.kind for f in outcome.findings]}" if outcome.findings else ""),
                  file=sys.stderr)
            return 1
        entry = box["entry"]
        print(f"vault: pushed '{entry.name}' [{entry.kind} v{entry.version}] — {plan.reason()}")
        return 0

    print(f"error: unknown vault action '{action}'", file=sys.stderr)
    return 2


def cmd_skills(args: argparse.Namespace) -> int:
    # L4 — catalog with progressive disclosure: bare list is cheap; a name reveals detail.
    if args.name:
        try:
            skill = get_skill(args.name)
        except SkillNotFound as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"/{skill.name} — {skill.summary}")
        print(f"  gate: {skill.gate.id} ({skill.gate.kind}) — {skill.gate.description}")
        if skill.phase:
            print(f"  pipeline phase: {skill.phase}")
        if skill.scaffold:
            print("  (scaffold — deeper engine in a later stage)")
        print("\n" + skill.prompt)
        return 0
    print("mokata skills (run `mokata skills <name>` for detail):")
    for name, summary in list_skills():
        print(f"  /{name:10} {summary}")
    print("\nAuthor your own (G6, RED-GREEN-for-docs; human-gated write):")
    print("  mokata skill author <name> --summary <s> --require <doc>:<must-contain> "
          "--content-file <f>")
    return 0


def cmd_skill(args: argparse.Namespace) -> int:
    if args.action == "author":
        return _skill_author(args)
    print(f"error: unknown skill action '{args.action}'", file=sys.stderr)
    return 2


def _skill_author(args: argparse.Namespace) -> int:
    # G6 — draft a skill test-first (declare doc requirements -> content must satisfy them:
    # RED before GREEN), then HUMAN-GATE the write of the rendered command template. A RED
    # draft writes nothing; degrade-clean.
    from .govern import AuditLedger, SkillDraft, WriteGate, WriteRequest
    from .skills import Gate, command_markdown
    draft = SkillDraft(args.name)
    for spec in (args.require or []):
        rname, sep, must = spec.partition(":")
        if not sep or not rname or not must:
            print(f"error: --require must be name:must-contain (got {spec!r})",
                  file=sys.stderr)
            return 2
        draft.require(rname, must)
    if not draft.requirements:
        print("error: declare at least one --require name:must-contain "
              "(the doc tests, RED-GREEN-for-docs)", file=sys.stderr)
        return 2
    if not args.content_file:
        print("error: --content-file <path> is required (the drafted skill content)",
              file=sys.stderr)
        return 2
    try:
        with open(args.content_file, encoding="utf-8") as fh:
            draft.write(fh.read())
    except OSError as exc:
        print(f"error: cannot read {args.content_file}: {exc}", file=sys.stderr)
        return 1

    result = draft.check()
    if not result.passed:
        # RED — report the failing doc requirements and write NOTHING.
        print(f"skill '{args.name}' is RED — doc requirement(s) unmet: "
              f"{', '.join(result.failures)}")
        print("Revise the content until every requirement passes (RED -> GREEN), "
              "then re-run.")
        return 1

    # GREEN — promote to a Skill and human-gate the write of the rendered command.
    gate = Gate(f"{args.name}-approval",
                args.gate_desc or "Human-gated self-authored skill.", "human")
    skill = draft.to_skill(args.summary or f"mokata · {args.name}", gate)
    rendered = command_markdown(skill)
    dest = args.out or os.path.join(args.path, MOKATA_DIR, "skills", f"{args.name}.md")
    ledger = AuditLedger.from_mokata_dir(os.path.join(args.path, MOKATA_DIR))

    def commit() -> None:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(rendered)

    outcome = WriteGate(ledger=ledger).submit(
        WriteRequest("config", dest, content=rendered, actor="cli"),
        commit=commit, assume_yes=args.yes)
    if not outcome.committed:
        print(f"skill author: {outcome.reason} — nothing written.")
        return 1
    print(f"skill '{args.name}' authored (GREEN) and written to {dest}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    # L1/L3 — run a skill standalone. No init and no upstream phase are required; if the
    # repo is initialized we add live grounding, otherwise we degrade cleanly.
    try:
        skill = get_skill(args.name)
    except SkillNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    surface = None
    if Surface.is_initialized(args.path):
        try:
            surface = Surface.load(args.path)
        except (ConfigError, ManifestError):
            surface = None

    # Stage 32 — implementation entry points (develop/test) require a persisted, complete
    # spec before code/tests; the block (and pass) is an audited gate decision.
    if skill.requires_spec:
        from .engine import check_spec_persisted
        from .govern import AuditLedger
        store = surface.state if surface is not None else None
        ledger = (AuditLedger.from_mokata_dir(surface.mokata_dir)
                  if surface is not None else None)
        res = check_spec_persisted(store, ledger=ledger, phase=skill.name)
        if not res.passed:
            print(f"[BLOCKED] {res.gate_id} — {res.reason}", file=sys.stderr)
            return 1

    grounding = ground(surface.router) if surface is not None else ground(None)
    sys.stdout.write(render_skill(skill, grounding))
    return 0


def cmd_enter(args: argparse.Namespace) -> int:
    # L2 — enter the pipeline at a phase; only the run phases' gates apply.
    try:
        plan = plan_entry(args.phase, stop=args.to)
    except PhaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(render_entry(plan))
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    rules = load_rules(surface)
    print("mokata rules (4 tiers):")
    for tier, rs in rules.items():
        cap = "no cap" if rs.cap is None else f"cap {rs.cap}"
        flag = "OK" if rs.within_cap else "OVER CAP"
        print(f"  {tier:13} {rs.line_count:4d} lines  ({cap}) — {flag}")
    errors = validate_caps(rules)
    if errors:
        for e in errors:
            print(f"  ! {e}")
        return 1
    # G5 — surface human-gated rule PROPOSALS distilled from recurring ledger corrections
    # (declined writes, reverts, spec conflicts). Proposal-only — never auto-added; quiet
    # and bounded when there are none (P11).
    from .govern import learn_from_ledger
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    proposals = learn_from_ledger(ledger)
    if proposals:
        print("\nRule proposals (recurring corrections — human-gated, not auto-added):")
        for p in proposals:
            print(f"  - {p.proposed_rule} [{p.rationale}]")
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    # F5 — aggregate logged savings from the audit ledger into a live budget report.
    ledger = AuditLedger.from_mokata_dir(os.path.join(args.path, MOKATA_DIR))
    report = BudgetReport.from_ledger(ledger)
    if not report.events:
        print("budget: no savings recorded yet.")
        return 0
    print(report.render())
    print(f"statusline: {budget_statusline(report)}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    ledger = AuditLedger.from_mokata_dir(os.path.join(args.path, MOKATA_DIR))
    entries = ledger.entries()
    if not entries:
        print("audit ledger: empty.")
        return 0
    if getattr(args, "why", False):
        # Stage 49 — the read-only "what you did and WHY" timeline: bounded (a tail) and
        # derived from the ledger; surfaces each entry's decision + rationale. Writes nothing.
        from .govern.ledger import WHY_TIMELINE_TAIL, why_timeline
        tail = args.tail if getattr(args, "tail", None) else WHY_TIMELINE_TAIL
        lines = why_timeline(entries, tail=tail)
        shown = min(len(lines), len(entries))
        print(f"audit — why timeline (last {shown} of {len(entries)}):")
        for line in lines:
            print(f"  {line}")
        return 0
    print(f"audit ledger — {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}:")
    for e in entries:
        extra = " ".join(f"{k}={v}" for k, v in e.items()
                         if k not in ("seq", "kind", "at"))
        print(f"  #{e['seq']:<3} {e['kind']:<11} {extra}")
    return 0


def _cli_ask(question: str, default: str) -> str:
    try:
        return input(f"{question} [{default}] ").strip() or default
    except EOFError:
        return default


def cmd_exec(args: argparse.Namespace) -> int:
    # E8 / Stage 25 — choose the execution mode for a run. Explicit flags select
    # non-interactively; otherwise honor the saved settings.execution.default and ask
    # once (default 'ask') — never fan out without a choice.
    if args.parallel:
        isolation = args.isolation or not args.fanout   # parallel implies ≥ isolation
        choice = ExecutionChoice(PARALLEL, isolation=isolation, fanout=args.fanout)
    else:
        manifest = None
        if Surface.is_initialized(args.path):
            try:
                manifest = Surface.load(args.path).manifest
            except (ConfigError, ManifestError):
                manifest = None
        subagents = claude_code_harness().supports("subagents")
        choice = resolve_execution_choice(
            manifest=manifest, ask=_cli_ask, out=print, subagents_available=subagents)
    from .progress import active_banner
    print(active_banner(f"exec ({choice.mode})", running=False))
    print(f"execution mode: {choice.label()}")
    if choice.is_parallel:
        print("  parallel modes surface a token/cost estimate before running, stay "
              "under the gates + audit ledger + token budget, and degrade to "
              "sequential flow if subagents are unavailable.")
    else:
        print("  the sequential gated flow is the default, lowest-cost path.")
    return 0


def cmd_progress(args: argparse.Namespace) -> int:
    # Stage 27 — read-only run-progress tracker. Degrades cleanly with no active run.
    # Stage 40 — `--lanes` renders the parallel-aware multi-lane view (read-only).
    surface = _load_surface(args.path)
    if getattr(args, "lanes", False):
        from .progress import build_run_lanes, render_lanes
        ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
        rl = build_run_lanes(surface.state, ledger=ledger, run_id=args.run)
        print(render_lanes(rl, ascii_only=args.ascii))
        return 0
    from .progress import build_progress, render_progress
    progress = build_progress(surface.state, run_id=args.run)
    print(render_progress(progress, ascii_only=args.ascii))
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    # Stage 50 — list past + active runs (read-only; bounded; friendly empty state).
    from .progress import list_sessions
    surface = _load_surface(args.path)
    sessions = list_sessions(surface.state)
    if not sessions:
        print("mokata sessions: no runs on record yet. Start one with /mokata:brainstorm "
              "or /mokata:refine.")
        return 0
    print(f"mokata sessions — {len(sessions)} run(s):")
    for s in sessions:
        status = ("complete ✓" if s.complete
                  else f"resume at '{s.resume_phase}'") + (" · active" if s.active else "")
        last = f" · last passed '{s.last_passed}'" if s.last_passed else " · not started"
        print(f"  {s.run_id:24} [{s.done}/{s.total}]{last} — {status}")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    # Stage 50 — PREVIEW where a run resumes (read-only): the phase + the gate that still
    # applies. mokata never auto-runs the pipeline; the gates hold on resume.
    from .pipeline import PHASE_GATES
    from .progress import build_progress, find_active_run
    surface = _load_surface(args.path)
    rid = args.id or find_active_run(surface.state)
    if rid is None:
        print("mokata resume: no run to resume. Start one with /mokata:brainstorm.")
        return 0
    progress = build_progress(surface.state, run_id=rid)
    if not progress.active:
        print(f"mokata resume: {progress.message}")
        return 0
    if progress.complete:
        print(f"mokata resume: run '{rid}' is complete — nothing to resume.")
        return 0
    phase = progress.current
    gate = PHASE_GATES.get(phase)
    print(f"mokata resume: run '{rid}' — [{progress.done}/{progress.total}] phases passed.")
    print(f"  resume at: '{phase}'"
          + (f" (the '{gate.id}' gate still applies — {gate.kind})" if gate else ""))
    print(f"  continue with: mokata enter {phase}   (or /mokata:{phase}) — gates hold.")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    # Stage 40 — write the self-contained local HTML dashboard (read-only; never mutates a run).
    # Respects settings.ux.progress: `terminal` writes NO HTML (the terminal tier is the floor).
    from .dashboard import dashboard_enabled, ux_progress_setting, write_dashboard
    surface = _load_surface(args.path)
    if not dashboard_enabled(surface):
        print(f"mokata watch: the dashboard is off (settings.ux.progress="
              f"{ux_progress_setting(surface)}). Enable it with "
              f"`mokata config set settings.ux.progress dashboard` (or `both`).")
        return 0
    refresh = None if args.once else 2
    path = write_dashboard(surface, run_id=args.run, refresh_secs=refresh)
    print(f"mokata watch: wrote {path}")
    if args.open:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(path))
    if args.once:
        return 0
    # Live mode: rewrite the file on an interval; the page meta-refreshes itself. Read-only.
    print("mokata watch: live — refreshing every 2s (Ctrl-C to stop).")
    try:
        import time
        while True:
            time.sleep(2)
            write_dashboard(surface, run_id=args.run, refresh_secs=refresh)
    except KeyboardInterrupt:
        print("\nmokata watch: stopped.")
    return 0


def cmd_govern(args: argparse.Namespace) -> int:
    # Stage 48 — write the self-contained governance dashboard (rules + memory-by-kind +
    # read/write ratio + pending proposals). Read-only; never mutates state. The manage
    # commands are surfaced, not run.
    from .dashboard import write_governance_dashboard
    surface = _load_surface(args.path)
    path = write_governance_dashboard(surface)
    print(f"mokata govern: wrote {path}")
    print("  read-only view of the governed state — manage via the surfaced "
          "`mokata memory edit` commands (human-gated).")
    if args.open:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(path))
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    # E7 — dry-run: print the pipeline plan (actions + gates + file touches). No writes.
    surface = _load_surface(args.path)
    pv = preview_pipeline(start=args.start, stop=args.to,
                          mokata_dir=surface.mokata_dir)
    print(pv.render())
    return 0


def cmd_playbook(args: argparse.Namespace) -> int:
    # Stage 9 — drive the full v1 story end-to-end on this repo. Parallel without a
    # subagent harness degrades to sequential (degrade-safe). Stage 27: announce the
    # active stage (banner) at the start and on completion.
    from .progress import active_banner
    surface = _load_surface(args.path)
    if args.parallel:
        choice = ExecutionChoice(PARALLEL, isolation=True, fanout=args.fanout)
    else:
        choice = ExecutionChoice(SEQUENTIAL)
    print(active_banner("playbook", running=True))
    result = run_playbook(surface, choice, dense=args.dense)
    print(result.render())
    print(active_banner("playbook", running=False))
    return 0 if result.ok else 1


def cmd_index(args: argparse.Namespace) -> int:
    # B4 — build/refresh the per-file freshness index; report what changed + stale files.
    surface = _load_surface(args.path)
    store = surface.state
    data = store.read("knowledge_index")
    idx = KnowledgeIndex.from_dict(data) if data else KnowledgeIndex()
    if data is None:
        built = idx.build(surface.root)
        print(f"index: built {len(built)} file(s)")
    else:
        d = idx.diff(surface.root)
        reindexed = idx.reindex(surface.root)
        print(f"index: reindexed {len(reindexed)} changed, "
              f"+{len(d['added'])} added, -{len(d['removed'])} removed")
    store.write("knowledge_index", idx.to_dict())
    print(f"index: tracking {len(idx.entries)} file(s)")
    # Stage 35f: name the code-graph backend the refresh runs against — the wired adapter
    # (e.g. neo4j) when present, the grep floor when not. Degrade-clean: never a hard error.
    layer = KnowledgeLayer.from_surface(surface)
    if layer.uses_graph:
        print(f"index: code graph '{layer.backend_name}' wired — "
              f"`mokata lat-check` flags drift against it.")
    else:
        print("index: no code graph wired — refresh runs on the grep floor "
              "(`mokata lat-check` still flags concept drift lexically).")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    # J3 — export the current manifest as a shareable stack file. Default destination is
    # under .mokata/ so mokata keeps its footprint contained (Stage 24D); an explicit
    # path still writes wherever the user names. The exported stack is committable config,
    # so it goes at the .mokata/ root (not temp_local/).
    surface = _load_surface(args.path)
    dest = args.file or os.path.join(args.path, MOKATA_DIR, SHARE_FILENAME)
    export_manifest(surface, dest=dest)
    print(f"exported stack to {dest}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    # J3 — validate + apply a shared manifest (human-gated).
    try:
        data = load_shared(args.file)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read {args.file}: {exc}", file=sys.stderr)
        return 1
    result = apply_manifest(args.path, data, assume_yes=args.yes, force=args.force)
    if result.errors:
        print("import rejected — invalid manifest:", file=sys.stderr)
        for e in result.errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    if not result.applied:
        print(f"\n{result.message}", file=sys.stderr)
        return 1
    print(f"applied shared stack to {result.path}")
    return 0


def cmd_harness(args: argparse.Namespace) -> int:
    # J2 / Stage 52a — list the available harnesses + their capability matrix. The engine is
    # harness-agnostic; a harness lacking a capability degrades clearly (never a silent no-op).
    from .harness import available_harnesses, get_harness
    names = available_harnesses()
    if getattr(args, "name", None):
        if args.name not in names:
            print(f"error: unknown harness '{args.name}'; available: {', '.join(names)}",
                  file=sys.stderr)
            return 1
        names = [args.name]
    for nm in names:
        h = get_harness(nm)
        label = "reference" if nm == "claude" else "portable"
        print(f"harness '{nm}' ({h.name}) — {label}:")
        for cap in HARNESS_CAPABILITIES:
            print(f"  [{'yes' if h.supports(cap) else 'no '}] {cap}")
    print("(the engine is harness-agnostic; a harness lacking a capability degrades with a "
          "clear message, never a crash, and never a silent no-op of a gate.)")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    report = diagnose(surface)
    print(report.render())
    return 0 if report.ok else 1


def cmd_baseline(args: argparse.Namespace) -> int:
    # Stage 34B — report the test suite green/red at baseline; degrade-clean if no command
    # is known (mokata never guesses a test framework). Read-only diagnostic.
    from .baseline import baseline_command, baseline_status
    manifest = None
    if Surface.is_initialized(args.path):
        try:
            manifest = Surface.load(args.path).manifest
        except (ConfigError, ManifestError):
            manifest = None
    cmd = baseline_command(manifest, override=args.cmd)
    result = baseline_status(cmd, cwd=args.path)
    print(result.render())
    # green/unknown don't hard-block (unknown degrades clean); only red is non-zero.
    return 0 if result.ok else 1


def cmd_config(args: argparse.Namespace) -> int:
    # Stage 24A — read/update backend config in the committed manifest. `get` is
    # read-only; `set` is human-gated (preview + confirm; secrets are a hard block).
    try:
        if args.action == "get":
            found, val = config_cmd.config_get(args.path, args.key)
            if not found:
                print(f"{args.key}: (unset)")
                return 1
            import json as _json
            print(_json.dumps(val))
            return 0
        # set
        if args.value is None:
            print("error: `config set <key> <value>` requires a value",
                  file=sys.stderr)
            return 2
        # config_set prints its own preview / rejection detail; we add only the result.
        res = config_cmd.config_set(args.path, args.key, args.value,
                                    assume_yes=args.yes)
        if res.committed:
            print(f"set {res.key}")
            return 0
        return 1
    except config_cmd.ConfigCommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_reset(args: argparse.Namespace) -> int:
    plan = plan_reset(args.path, keep_config=args.keep_config)
    if not plan.targets:
        print("reset: nothing to remove.")
        return 0
    print("reset will remove:")
    for t in plan.targets:
        print(f"  {t}")
    result = reset_state(args.path, keep_config=args.keep_config,
                         assume_yes=args.yes, backup_dir=args.backup)
    if result.aborted:
        print(f"\n{result.message}", file=sys.stderr)
        return 1
    print(f"removed {len(result.removed)} path(s)"
          + (f"; backed up to {args.backup}" if args.backup else ""))
    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    ctx = SuggestionContext(
        starting_fresh=args.fresh, has_spec=args.spec,
        has_failing_test=args.failing_test, has_implementation=args.implementation,
        has_diff=args.diff, has_bug_report=args.bug,
        has_stacktrace=args.stacktrace, has_perf_issue=args.perf)
    suggestions = suggest(ctx)
    if not suggestions:
        print("no suggestions for this context.")
        return 0
    print("suggested (not run — your call):")
    for s in suggestions:
        print(f"  /{s.skill} — {s.reason}")
    return 0


def cmd_chain(args: argparse.Namespace) -> int:
    try:
        steps = plan_chain(args.skills)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("chain (each step applies its own gate):")
    for s in steps:
        print(f"  /{s.skill}  [gate: {s.gate}]")
    return 0


def cmd_coverage(args: argparse.Namespace) -> int:
    # A6 — treat the manifest's tools as adapters; report capability coverage + gaps.
    surface = _load_surface(args.path)
    m = surface.manifest
    adapters = [AdapterContract(name=tid, provides=[t.get("provides")],
                                kind=t.get("kind", "external"))
                for tid, t in m.tools.items() if t.get("provides")]
    report = negotiate(list(m.capabilities), adapters)
    print(report.render())
    overlaps = overlapping_capabilities(m)
    if overlaps:
        print("overlaps (resolved by manifest precedence):")
        for need, providers in overlaps.items():
            print(f"  {need}: {' > '.join(providers)}")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    # H4 — discover MCP servers (from .mokata/mcp.json) and map to roles; degrade cleanly.
    surface = _load_surface(args.path)
    reg = MCPRegistry.discover(path=os.path.join(surface.mokata_dir, "mcp.json"))
    if not reg.servers:
        print("mcp: no servers discovered (degraded — none present).")
        return 0
    print(f"mcp: {len(reg.servers)} server(s)")
    for cap, names in reg.map_to_roles().items():
        print(f"  {cap}: {', '.join(names)}")
    return 0


def cmd_lat_check(args: argparse.Namespace) -> int:
    # B5 — scan @lat anchors and flag concept drift (degrades cleanly when absent).
    surface = _load_surface(args.path)
    report = lat_check(surface.root)
    print(report.render())
    return 1 if report.has_drift else 0


def cmd_status(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    m = surface.manifest
    live = [r.summary() for r in surface.router.resolve_all()]
    print(f"mokata {m.mokata_version} · profile '{m.profile}'")
    for line in live:
        print(f"  {line}")
    # Stage 25 Part B — actionable code-graph hint (active queries, or how to wire one).
    from .knowledge import graph_guidance
    print(graph_guidance(surface))
    return 0


def _profile_for(path: str) -> str:
    """The project profile if initialized, else a friendly placeholder. Never raises."""
    try:
        if Surface.is_initialized(path):
            return Surface.load(path).manifest.profile
    except Exception:
        pass
    return "(not initialized)"


def _ledger_for(path: str):
    """The audit ledger if the repo is initialized, else None (degrade-clean)."""
    try:
        if Surface.is_initialized(path):
            return AuditLedger.from_mokata_dir(Surface.load(path).mokata_dir)
    except Exception:
        pass
    return None


def cmd_version(args: argparse.Namespace) -> int:
    # 45b — OFFLINE by default: version + profile + install method + Python, zero egress.
    from .version import check_for_update, version_info
    print(version_info(profile=_profile_for(args.path)).render())
    if args.check:
        # OPT-IN outbound check — netguard-accounted (logged) + degrade-clean offline.
        print(check_for_update(ledger=_ledger_for(args.path)).render())
    return 0


def cmd_upgrade(args: argparse.Namespace) -> int:
    # 45b — easy, HUMAN-GATED upgrade. `--check` just reports; never auto-runs an install.
    from .version import (
        check_for_update,
        detect_install_method,
        run_pip_upgrade,
        upgrade_steps,
    )
    if args.check:
        print(check_for_update(ledger=_ledger_for(args.path)).render())
        return 0
    method = args.method if args.method != "auto" else detect_install_method()
    print(f"mokata {__version__} · install: {method}")
    if method == "plugin":
        # The CLI can't upgrade the plugin itself — print the steps to run in Claude Code.
        print("This is a plugin install — upgrade it from Claude Code:")
        for step in upgrade_steps("plugin"):
            print(f"  {step}")
        return 0
    steps = upgrade_steps(method)
    if method == "source":
        print("Source checkout — upgrade with:")
        for step in steps:
            print(f"  {step}")
        return 0
    # pip install — propose `pip install -U mokata`, HUMAN-GATED (never auto-runs).
    print(f"To upgrade: {steps[0]}")
    if not args.yes:
        if not read_yes_no(f"run `{steps[0]}` now?", "Run the upgrade?"):
            print("not run — run it yourself when ready (or re-run with --yes).")
            return 0
    run_pip_upgrade()
    print(f"ran: {steps[0]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mokata",
        description="mokata — spec-driven TDD framework for Claude Code (spine).",
    )
    parser.add_argument("--version", action="version", version=f"mokata {__version__}")

    # Shared --path option so it works both before and after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--path",
        default=".",
        help="repo root to operate on (default: current directory)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser(
        "init", parents=[common],
        help="scaffold config; detect tools; pick profile",
    )
    p_init.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        choices=profile_names(),
        help=f"starting profile (default: {DEFAULT_PROFILE})",
    )
    p_init.add_argument(
        "--yes", action="store_true", help="non-interactive; skip the write prompt"
    )
    p_init.add_argument(
        "--force", action="store_true", help="overwrite an existing manifest"
    )
    p_init.add_argument(
        "--preview", action="store_true",
        help="print the plan and exit without writing (dry-run for the human gate)"
    )
    p_init.set_defaults(func=cmd_init)

    p_setup = sub.add_parser(
        "setup", parents=[common],
        help="one command: wire mokata into a harness without the plugin "
             "(commands + MCP + hooks)",
    )
    p_setup.add_argument("harness", choices=HARNESSES,
                         help="the harness to wire (currently: claude)")
    p_setup.add_argument("--scope", choices=SCOPES, default="project",
                         help="install into this project (default) or user-global (~/.claude)")
    p_setup.add_argument("--profile", default=DEFAULT_PROFILE, choices=profile_names(),
                         help=f"profile to init with if not already set up "
                              f"(default: {DEFAULT_PROFILE})")
    p_setup.add_argument("--no-hooks", action="store_true",
                         help="skip wiring the SessionStart + secret-guard hooks")
    p_setup.add_argument("--yes", action="store_true",
                         help="non-interactive; skip the confirmation prompt")
    p_setup.add_argument("--force", action="store_true",
                         help="re-init even if a manifest already exists")
    p_setup.set_defaults(func=cmd_setup)

    p_unsetup = sub.add_parser(
        "unsetup", parents=[common],
        help="reverse `mokata setup`: remove wired commands, MCP entry, and hooks",
    )
    p_unsetup.add_argument("harness", choices=HARNESSES,
                           help="the harness to unwire (currently: claude)")
    p_unsetup.add_argument("--scope", choices=SCOPES, default="project",
                           help="which scope to remove from (default: project)")
    p_unsetup.add_argument("--yes", action="store_true",
                           help="non-interactive; skip the confirmation prompt")
    p_unsetup.set_defaults(func=cmd_unsetup)

    p_boot = sub.add_parser(
        "bootstrap", parents=[common], help="print the SessionStart briefing"
    )
    p_boot.add_argument(
        "--show-tokens",
        action="store_true",
        help="print the token estimate + budget check to stderr",
    )
    p_boot.set_defaults(func=cmd_bootstrap)

    p_val = sub.add_parser(
        "validate", parents=[common], help="validate the committed manifest"
    )
    p_val.set_defaults(func=cmd_validate)

    p_route = sub.add_parser(
        "route", parents=[common], help="resolve a capability to its tool"
    )
    p_route.add_argument("need", nargs="?", help="capability name (default: all)")
    p_route.set_defaults(func=cmd_route)

    p_det = sub.add_parser(
        "detect", parents=[common], help="show tool presence for the catalog"
    )
    p_det.set_defaults(func=cmd_detect)

    p_stat = sub.add_parser(
        "status", parents=[common], help="one-line stack summary"
    )
    p_stat.set_defaults(func=cmd_status)

    p_ver = sub.add_parser(
        "version", parents=[common],
        help="show version + profile + install method + Python (offline by default)",
    )
    p_ver.add_argument("--check", action="store_true",
                       help="opt-in: check for a newer release (outbound; degrades clean "
                            "offline)")
    p_ver.set_defaults(func=cmd_version)

    p_up = sub.add_parser(
        "upgrade", parents=[common],
        help="upgrade mokata (human-gated pip install, or print the plugin-update steps)",
    )
    p_up.add_argument("--check", action="store_true",
                      help="opt-in: just check for a newer release, don't upgrade")
    p_up.add_argument("--method", choices=("auto", "pip", "plugin"), default="auto",
                      help="override install-method detection (default: auto)")
    p_up.add_argument("--yes", action="store_true",
                      help="approve the pip upgrade non-interactively (never auto-runs "
                           "without this or a confirm)")
    p_up.set_defaults(func=cmd_upgrade)

    p_brain = sub.add_parser(
        "brainstorm", parents=[common],
        help="launch the Socratic pre-spec brainstorm (standalone)",
    )
    p_brain.add_argument(
        "--status", action="store_true",
        help="show whether an approved approach is persisted, instead of launching",
    )
    p_brain.set_defaults(func=cmd_brainstorm)

    p_onboard = sub.add_parser(
        "onboard", parents=[common],
        help="guided capture of typed project knowledge (rules/guardrails/context/docs)",
    )
    p_onboard.set_defaults(func=cmd_onboard)

    p_query = sub.add_parser(
        "query", parents=[common],
        help="run a structural query (graph if present, else grep floor)",
    )
    p_query.add_argument("kind", choices=QUERY_KINDS, help="the structural question")
    p_query.add_argument("target", help="symbol or module to ask about")
    p_query.add_argument(
        "--depth", type=int, default=2,
        help="hops for blast_radius (default: 2; ignored by other kinds)",
    )
    p_query.set_defaults(func=cmd_query)

    p_speck = sub.add_parser(
        "spec-check", parents=[common],
        help="check a change against saved specs + decisions; raise a conflict via the "
             "deviation gate (Stage 37 regression guard)",
    )
    p_speck.add_argument("--symbols", default=None,
                         help="comma-separated symbols the change touches")
    p_speck.add_argument("--files", default=None,
                         help="comma-separated files the change touches")
    p_speck.add_argument("--text", default=None,
                         help="optional free-text description of the change")
    p_speck.add_argument("--phase", default="develop",
                         help="phase this runs at (develop/refine/spec; default: develop)")
    p_speck.add_argument("--yes", action="store_true",
                         help="confirm the change at the deviation gate (amend/supersede)")
    p_speck.set_defaults(func=cmd_spec_check)

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
    p_mem.set_defaults(func=cmd_memory)

    p_vault = sub.add_parser(
        "vault", parents=[common],
        help="share design artifacts (brainstorm/spec) with the team: push/list/search/pull",
    )
    p_vault.add_argument("action", choices=("push", "list", "search", "pull"),
                         help="push a markdown artifact (gated), or list/search/pull (read-only)")
    p_vault.add_argument("name", nargs="?", default=None,
                         help="entry name (push/pull), or the search query's first word")
    p_vault.add_argument("file", nargs="?", default=None,
                         help="source markdown file (push)")
    p_vault.add_argument("--kind", default=None, choices=("brainstorm", "spec"),
                         help="artifact kind (push; default: inferred from the name/file)")
    p_vault.add_argument("--dest", default=None,
                         help="pull destination file (default: <name>.md in the repo root)")
    p_vault.add_argument("--author", default=None,
                         help="push author for provenance (default: $USER)")
    p_vault.add_argument("--force", action="store_true",
                         help="version a changed re-push instead of refusing (never silent)")
    p_vault.add_argument("--yes", action="store_true",
                         help="non-interactive (approve the gated push)")
    p_vault.set_defaults(func=cmd_vault)

    p_skills = sub.add_parser(
        "skills", parents=[common],
        help="list the skill/command catalog (add a name for detail)",
    )
    p_skills.add_argument("name", nargs="?", help="reveal detail for one skill")
    p_skills.set_defaults(func=cmd_skills)

    p_skill = sub.add_parser(
        "skill", parents=[common],
        help="author a new skill (RED-GREEN-for-docs; human-gated write)",
    )
    p_skill.add_argument("action", choices=("author",), help="author a skill")
    p_skill.add_argument("name", help="skill name (the /<name> command)")
    p_skill.add_argument("--summary", default=None, help="one-line catalog summary")
    p_skill.add_argument("--require", action="append", metavar="DOC:MUST-CONTAIN",
                         help="a doc requirement the content must satisfy (repeatable)")
    p_skill.add_argument("--content-file", default=None,
                         help="path to the drafted skill content (markdown)")
    p_skill.add_argument("--gate-desc", default=None,
                         help="the human gate's description for the authored skill")
    p_skill.add_argument("--out", default=None,
                         help="destination (default: .mokata/skills/<name>.md)")
    p_skill.add_argument("--yes", action="store_true",
                         help="approve the human-gated write non-interactively")
    p_skill.set_defaults(func=cmd_skill)

    p_run = sub.add_parser(
        "run", parents=[common],
        help="run a skill/command standalone (no pipeline prerequisite)",
    )
    p_run.add_argument("name", choices=SKILL_NAMES, help="the skill to run")
    p_run.set_defaults(func=cmd_run)

    p_enter = sub.add_parser(
        "enter", parents=[common],
        help="enter the pipeline at a phase (applies only that phase's gates)",
    )
    p_enter.add_argument("phase", choices=ENTRY_PHASES, help="phase to start at")
    p_enter.add_argument("--to", choices=PIPELINE_PHASES, default=None,
                         help="optional phase to stop after (default: just the start)")
    p_enter.set_defaults(func=cmd_enter)

    p_rules = sub.add_parser(
        "rules", parents=[common],
        help="show the 4-tier rules and their line budgets",
    )
    p_rules.set_defaults(func=cmd_rules)

    p_audit = sub.add_parser(
        "audit", parents=[common], help="show the append-only audit ledger",
    )
    p_audit.add_argument("--why", action="store_true",
                         help="a readable what+decision+why timeline (bounded; read-only)")
    p_audit.add_argument("--tail", type=int, default=None,
                         help="how many recent entries the --why timeline shows (default 50)")
    p_audit.set_defaults(func=cmd_audit)

    p_budget = sub.add_parser(
        "budget", parents=[common],
        help="show token savings (live budget readout + statusline)",
    )
    p_budget.set_defaults(func=cmd_budget)

    p_index = sub.add_parser(
        "index", parents=[common],
        help="build/refresh the freshness index (incremental); report stale files",
    )
    p_index.set_defaults(func=cmd_index)

    p_lat = sub.add_parser(
        "lat-check", parents=[common],
        help="scan @lat anchors and flag concept drift (degrades cleanly when absent)",
    )
    p_lat.set_defaults(func=cmd_lat_check)

    p_cov = sub.add_parser(
        "coverage", parents=[common],
        help="report capability coverage + unmet gaps + overlaps (A6/H6)",
    )
    p_cov.set_defaults(func=cmd_coverage)

    p_mcp = sub.add_parser(
        "mcp", parents=[common],
        help="discover MCP servers and map them to roles (H4; degrades cleanly)",
    )
    p_mcp.set_defaults(func=cmd_mcp)

    p_doc = sub.add_parser(
        "doctor", parents=[common],
        help="diagnose the manifest/config (missing deps, conflicts, bad trust)",
    )
    p_doc.set_defaults(func=cmd_doctor)

    p_base = sub.add_parser(
        "baseline", parents=[common],
        help="report the test suite green/red at baseline (degrades clean if no command)",
    )
    p_base.add_argument("--cmd", default=None,
                        help="test command to run (else settings.baseline.test_command)")
    p_base.set_defaults(func=cmd_baseline)

    p_config = sub.add_parser(
        "config", parents=[common],
        help="get/set backend config in the manifest (set is human-gated; Stage 24A)",
    )
    p_config.add_argument("action", choices=("get", "set"),
                          help="read a key, or set one (preview + confirm)")
    p_config.add_argument("key", help="dotted manifest key, e.g. tools.sqlite.config.path")
    p_config.add_argument("value", nargs="?", default=None,
                          help="value to set (required for 'set')")
    p_config.add_argument("--yes", action="store_true",
                          help="non-interactive; skip the confirmation prompt")
    p_config.set_defaults(func=cmd_config)

    p_exp = sub.add_parser(
        "export", parents=[common],
        help="export the current manifest as a shareable stack (J3)",
    )
    p_exp.add_argument("file", nargs="?", default=None,
                       help="destination file (default: <path>/.mokata/mokata-stack.json)")
    p_exp.set_defaults(func=cmd_export)

    p_imp = sub.add_parser(
        "import", parents=[common],
        help="validate + apply a shared stack manifest (human-gated, J3)",
    )
    p_imp.add_argument("file", help="shared manifest file to apply")
    p_imp.add_argument("--yes", action="store_true", help="non-interactive apply")
    p_imp.add_argument("--force", action="store_true", help="overwrite existing config")
    p_imp.set_defaults(func=cmd_import)

    p_harn = sub.add_parser(
        "harness", parents=[common],
        help="list harnesses + their capability matrix (J2); add a name for one",
    )
    p_harn.add_argument("name", nargs="?", default=None,
                        help="show one harness (default: all)")
    p_harn.set_defaults(func=cmd_harness)

    p_reset = sub.add_parser(
        "reset", parents=[common],
        help="remove mokata state (.mokata/); --keep-config keeps the manifest",
    )
    p_reset.add_argument("--keep-config", action="store_true",
                         help="keep manifest + constitution; remove only state")
    p_reset.add_argument("--backup", default=None,
                         help="move state to this dir instead of deleting (reversible)")
    p_reset.add_argument("--yes", action="store_true",
                         help="non-interactive; skip the confirmation prompt")
    p_reset.set_defaults(func=cmd_reset)

    p_sug = sub.add_parser(
        "suggest", parents=[common],
        help="suggest a relevant command for the context (suggest only, never runs)",
    )
    for flag in ("fresh", "spec", "diff", "bug", "stacktrace", "perf"):
        p_sug.add_argument(f"--{flag}", action="store_true")
    p_sug.add_argument("--failing-test", dest="failing_test", action="store_true")
    p_sug.add_argument("--implementation", action="store_true")
    p_sug.set_defaults(func=cmd_suggest)

    p_chain = sub.add_parser(
        "chain", parents=[common],
        help="plan a manual chain of skills; each step keeps its own gate (L5)",
    )
    p_chain.add_argument("skills", nargs="+", help="skills to chain, in order")
    p_chain.set_defaults(func=cmd_chain)

    p_exec = sub.add_parser(
        "exec", parents=[common],
        help="show/select the execution mode for a run (default: sequential)",
    )
    p_exec.add_argument("--parallel", action="store_true",
                        help="parallel subagents (default is sequential gated flow)")
    p_exec.add_argument("--isolation", action="store_true",
                        help="fresh-subagent isolation + two-stage review (E2/E3)")
    p_exec.add_argument("--fanout", action="store_true",
                        help="concurrent fan-out (run tasks at once)")
    p_exec.set_defaults(func=cmd_exec)

    p_play = sub.add_parser(
        "playbook", parents=[common],
        help="run the full v1 story end-to-end on this repo (integration check)",
    )
    p_play.add_argument("--parallel", action="store_true",
                        help="use parallel subagents (degrades to sequential w/o a harness)")
    p_play.add_argument("--fanout", action="store_true",
                        help="concurrent fan-out (with --parallel)")
    p_play.add_argument("--dense", action="store_true",
                        help="F4 output-density: compress sub-agent handbacks (off by "
                             "default; or set settings.governance.output_density)")
    p_play.set_defaults(func=cmd_playbook)

    p_prev = sub.add_parser(
        "preview", parents=[common],
        help="dry-run: list planned phases, gates, and file touches (no side effects)",
    )
    p_prev.add_argument("--start", choices=PIPELINE_PHASES, default=None,
                        help="phase to start the preview at (default: first)")
    p_prev.add_argument("--to", choices=PIPELINE_PHASES, default=None,
                        help="phase to stop the preview at (default: last)")
    p_prev.set_defaults(func=cmd_preview)

    p_prog = sub.add_parser(
        "progress", parents=[common],
        help="show the run-progress tracker (done/current/pending); read-only",
    )
    p_prog.add_argument("--run", default=None,
                        help="a specific run id (default: the active/most-recent run)")
    p_prog.add_argument("--ascii", action="store_true",
                        help="ASCII glyphs ([x]/[>]/[ ]) instead of unicode")
    p_prog.add_argument("--lanes", action="store_true",
                        help="parallel-aware multi-lane view (one line per concurrent lane)")
    p_prog.set_defaults(func=cmd_progress)

    p_sessions = sub.add_parser(
        "sessions", parents=[common],
        help="list past + active runs (id, phases passed, resume point); read-only",
    )
    p_sessions.set_defaults(func=cmd_sessions)

    p_resume = sub.add_parser(
        "resume", parents=[common],
        help="preview where a run resumes — the phase + the gate that still applies",
    )
    p_resume.add_argument("id", nargs="?", default=None,
                          help="run id to resume (default: the active/most-recent run)")
    p_resume.set_defaults(func=cmd_resume)

    p_watch = sub.add_parser(
        "watch", parents=[common],
        help="write a self-contained local HTML dashboard of the active run (read-only)",
    )
    p_watch.add_argument("--once", action="store_true",
                         help="write a single snapshot and exit (no live refresh loop)")
    p_watch.add_argument("--open", action="store_true",
                         help="open the written HTML file in your browser")
    p_watch.add_argument("--run", default=None,
                         help="a specific run id (default: the active/most-recent run)")
    p_watch.set_defaults(func=cmd_watch)

    p_govern = sub.add_parser(
        "govern", parents=[common],
        help="write a clickable local HTML view of the governed state "
             "(rules + memory by kind + pending proposals; read-only)",
    )
    p_govern.add_argument("--open", action="store_true",
                          help="open the dashboard in your browser after writing it")
    p_govern.set_defaults(func=cmd_govern)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # argparse stores --path before the subcommand; subcommands read args.path.
    if not hasattr(args, "path"):
        args.path = "."
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
