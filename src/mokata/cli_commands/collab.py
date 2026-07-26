"""vault / session — share design artifacts and portable tagged sessions across the team (push gated, list/search/pull read-only)."""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict

from ._common import (
    Surface,
    _current_user,
    AuditLedger,
    WriteGate,
    WriteRequest,
    _load_surface,
    _SCOPE_CURRENT,
    _review_scope,
    _backend_projects,
    _cli_ask,
)


def cmd_vault(args: argparse.Namespace) -> int:
    # Stage 35d — team design & spec vault: push (gated) → list/search/pull (read-only).
    from .. import vault as V
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
        from ..govern import OutboundRequest, gate_outbound_publish, looks_private
        from ..govern.trust import CLI_SURFACE
        if looks_private(plan.content):
            decision = gate_outbound_publish(
                OutboundRequest("vault-push", f"vault:{plan.name}", payload=plan.content),
                private_data=True, ledger=ledger, confirm=_cli_ask, assume_yes=args.yes)
            if not decision.allowed:
                print(f"vault push blocked — {decision.reason}", file=sys.stderr)
                return 1
        gate = WriteGate(ledger=ledger)
        author = args.author or _current_user() or ""
        box: Dict[str, Any] = {}
        outcome = gate.submit(
            WriteRequest("config", V._artifact_path(root, plan.name),
                         content=plan.content, actor="cli",
                         tool="vault_push", surface=CLI_SURFACE),
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


def cmd_session(args: argparse.Namespace) -> int:
    # Stage 55a/55b — portable tagged sessions: push / pull / list / name across transports.
    from .. import session_bundle as SB
    from .. import session_transport as STX
    action = args.action
    root = args.path

    def _kind(explicit):
        """SIMP.S1 — resolve the effective transport kind: `--file` forces local (the explicit
        escape hatch); an explicit --to/--from is honored verbatim; otherwise it is DERIVED from
        the repo mode (team-connected → postgres, solo → local)."""
        if getattr(args, "file", False):
            return "local"
        return explicit if explicit is not None else STX.transport_kind_for_mode(root)

    def _open(explicit):
        """Build a transport for the resolved kind, degrading clean (clear message, rc 1) on an
        unavailable remote — a team-connected repo with no DSN REFUSES here, never silently
        downgrades to a local file."""
        try:
            return STX.make_transport(_kind(explicit), root), 0
        except STX.SessionTransportUnavailable as exc:
            print(f"session: {exc}", file=sys.stderr)
            return None, 1

    if action == "list":
        # Stage 71a — the shared Postgres leg is SCOPED to the current project by default; --all
        # spans all, --project selects one, --list-projects enumerates. Local/vault are per-repo.
        from ..project import ALL_PROJECTS
        scope = _review_scope(args)
        initialized = Surface.is_initialized(root)
        has_shared = bool(STX.resolve_pg_dsn(root=root))

        if getattr(args, "list_projects", False):
            if not has_shared:
                print("session: no shared backend configured — projects apply to a shared DSN.")
                return 0
            try:
                pg = STX.make_transport("postgres", root, project=ALL_PROJECTS)
            except STX.SessionTransportUnavailable as exc:
                print(f"session: {exc}", file=sys.stderr)
                return 1
            # D5 — the transport CONSTRUCTED, but the query can still fail (the shared DB went away
            # between connect and select). That used to be swallowed into `0 project(s)` — an
            # unreachable backend rendered as an EMPTY one. Degrade with the reason, never a count
            # we did not read. Same shape + rc as the construct failure directly above.
            try:
                projs = _backend_projects(pg) or []
            except Exception as exc:
                print(f"session: shared backend unavailable ({exc})", file=sys.stderr)
                return 1
            print(f"session: {len(projs)} project(s) with bundles on the shared backend:")
            for p in projs:
                print(f"  {p}")
            return 0

        # OUTSIDE a project (bare CLI, no .mokata) with a shared DSN: never silently dump every
        # project — require --all/--project, or point at --list-projects.
        if has_shared and not initialized and scope is _SCOPE_CURRENT:
            print("session: not inside a mokata project. On a shared backend, choose a scope: "
                  "`--all` (every project), `--project <id>`, or `--list-projects` to see what's "
                  "present. (Refusing to dump every project's sessions.)")
            return 2

        # span local + the committed vault, plus shared Postgres when a DSN is configured.
        transports = [STX.LocalTransport(root), STX.VaultTransport(root)]
        if has_shared:
            try:
                # default scope → derive the current project; --all → span (None); --project → id.
                pg = (STX.make_transport("postgres", root) if scope is _SCOPE_CURRENT
                      else STX.make_transport("postgres", root, project=scope))
                transports.append(pg)
            except STX.SessionTransportUnavailable:
                pass                                       # degrade clean — local/vault still list
        infos = SB.list_session_bundles_across(root, transports)
        if not infos:
            print("session: no shared bundles — `mokata session push <tag>` to package this "
                  "session for another machine / a teammate.")
            return 0
        print(f"session: {len(infos)} bundle(s) (local + remote)")
        for i in infos:
            print(f"  {i.summary()}")
        return 0

    if action == "push":
        if not args.tag:
            print("error: `session push <tag>` requires a tag", file=sys.stderr)
            return 2
        transport, rc = _open(args.to)
        if transport is None:
            return rc
        surface = _load_surface(root)
        try:
            plan = SB.plan_session_push(root, surface, args.tag, run_id=args.run,
                                        force=args.force, author=args.author or "",
                                        transport=transport, save_first=args.save_first,
                                        requirements_only=args.requirements_only)
        except SB.SessionBundleError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if plan.status in ("empty", "unchanged"):
            print(f"session: {plan.reason()}")
            return 0
        if plan.blocked:
            print(f"session: {plan.reason()}", file=sys.stderr)
            return 1
        # SS.S4 — sharing UNFINISHED thinking (a not-yet-approved brainstorm) is an EXPLICIT
        # consent. Without --allow-in-progress it refuses with the honest summary; requirements-only
        # IS that consent. A checkpoint-only / spec-only / approved session is unaffected.
        in_progress = SB.in_progress_summary(plan.bundle)
        if SB.shares_unfinished_thinking(plan.bundle) and not args.allow_in_progress \
                and not args.requirements_only:
            print(f"session: refusing to share an in-progress session ({in_progress['label']}) — "
                  "re-run with --allow-in-progress to share unfinished thinking "
                  "(or --requirements-only to share just the distilled requirements).",
                  file=sys.stderr)
            return 1
        # Durable write → universal gate (secret-scan hard-block + human approval + audit).
        ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
        res = SB.commit_session_push_gated(plan, ledger=ledger, confirm=_cli_ask,
                                           assume_yes=args.yes)
        if not res.committed:
            print(f"session push {res.reason}"
                  + (f" — {[f.kind for f in res.findings]}" if res.findings else ""),
                  file=sys.stderr)
            return 1
        print(f"session: pushed '{plan.tag}' to {transport.name} — {plan.reason()}. "
              f"`mokata session pull {plan.tag} --from {transport.name}` on the other side "
              f"→ `mokata resume`.")
        return 0

    if action == "pull":
        if not args.tag:
            print("error: `session pull <tag> [--into <repo>]` requires a tag", file=sys.stderr)
            return 2
        transport, rc = _open(args.frm)
        if transport is None:
            return rc
        target = args.into or root
        try:
            plan = SB.plan_session_pull(root, args.tag, target, force=args.force,
                                        transport=transport)
        except SB.SessionBundleError as exc:
            print(f"error: {exc}", file=sys.stderr)       # corrupt/unreadable bundle
            return 1
        if plan.status in ("missing", "mismatch"):
            print(f"session: {plan.reason()}", file=sys.stderr)
            return 1
        target_surface = _load_surface(target)
        ledger = AuditLedger.from_mokata_dir(target_surface.mokata_dir)
        res = SB.hydrate_bundle(target_surface, plan.bundle, ledger=ledger,
                                confirm=_cli_ask, assume_yes=args.yes)
        if not res.committed:
            print(f"session pull {res.reason}"
                  + (f" — {[f.kind for f in res.findings]}" if res.findings else ""),
                  file=sys.stderr)
            return 1
        rp = (plan.bundle.get("resume") or {}).get("resume_phase")
        print(f"session: pulled '{plan.tag}' (from {transport.name}) into {target} — "
              f"`mokata resume` continues" + (f" at '{rp}'." if rp else "."))
        return 0

    if action == "name":
        if not args.tag or not args.newname:
            print("error: `session name <tag> <newname>` requires both names", file=sys.stderr)
            return 2
        transport, rc = _open(args.to)
        if transport is None:
            return rc
        try:
            plan = SB.plan_session_rename(root, args.tag, args.newname, transport=transport,
                                          force=args.force)
        except SB.SessionBundleError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if plan.status == "noop":
            print(f"session: {plan.reason()}")
            return 0
        if plan.status in ("missing", "collision"):
            print(f"session: {plan.reason()}", file=sys.stderr)
            return 1
        ledger = AuditLedger.from_mokata_dir(_load_surface(root).mokata_dir)
        res = SB.commit_session_rename_gated(plan, ledger=ledger, confirm=_cli_ask,
                                             assume_yes=args.yes)
        if not res.committed:
            print(f"session name {res.reason}"
                  + (f" — {[f.kind for f in res.findings]}" if res.findings else ""),
                  file=sys.stderr)
            return 1
        print(f"session: renamed '{plan.old}' → '{plan.new}' on {transport.name} "
              f"(provenance preserved).")
        return 0

    if action == "save":
        # SS.S0 — snapshot this session's in-flight state (UNGATED: local, transient, the user's
        # own state — the gate is at `push`/share, not here). The CLI is the use-anywhere surface;
        # with no in-flight objects to pass it re-affirms + reports the on-disk resumable state.
        from ..session_save import save_session
        surface = _load_surface(root)
        res = save_session(surface)
        d = res.to_dict()
        if res.empty:
            print("session: nothing in-flight to save yet — start a brainstorm/run first "
                  "(a save snapshots the current session so it's recoverable).")
            return 0
        keys = ", ".join(sorted(d["saved"]))
        print(f"session: saved [{keys}] for run {res.run_id} (local, ungated — "
              f"`mokata resume` continues; `mokata session push <tag>` to share).")
        return 0

    print(f"error: unknown session action '{action}'", file=sys.stderr)
    return 2


def register(sub, common):
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

    p_session = sub.add_parser(
        "session", parents=[common],
        help="portable / shareable tagged sessions: push (gated) / pull (gated) / list",
    )
    p_session.add_argument("action", choices=("save", "push", "pull", "list", "name"),
                           help="save this session's in-flight state (ungated, local), push the "
                                "current session (gated), pull+rehydrate (gated), list bundles "
                                "(local + remote, read-only), or rename a tag (gated)")
    p_session.add_argument("tag", nargs="?", default=None,
                           help="the bundle tag (push/pull/name), e.g. auth-refactor")
    p_session.add_argument("newname", nargs="?", default=None,
                           help="the new tag for `name <tag> <newname>` (rename)")
    p_session.add_argument("--run", default=None,
                           help="scope push to one run id (default: every recorded run)")
    p_session.add_argument("--into", default=None,
                           help="pull target repo root (default: this repo)")
    p_session.add_argument("--author", default=None,
                           help="push author for provenance (default: $USER)")
    p_session.add_argument("--to", choices=("local", "vault", "postgres"), default=None,
                           help="push/name transport (default: DERIVED from the repo mode — a "
                                "team-connected repo uses postgres, a solo repo uses local); "
                                "vault = committed/synced. An explicit value is honored verbatim")
    p_session.add_argument("--from", dest="frm",
                           choices=("local", "vault", "postgres"), default=None,
                           help="pull transport (default: derived from the repo mode) — same "
                                "choices as --to")
    p_session.add_argument("--file", dest="file", action="store_true",
                           help="force the LOCAL file transport regardless of mode (the explicit "
                                "escape hatch on a team-connected repo)")
    p_session.add_argument("--force", action="store_true",
                           help="push: overwrite a changed bundle; pull: apply despite a "
                                "cross-codebase fingerprint mismatch; name: overwrite a colliding "
                                "name (never silent)")
    p_session.add_argument("--yes", action="store_true",
                           help="non-interactive (approve the gated push/pull/name)")
    # SS.S4 — the three share flags (push).
    p_session.add_argument("--save-first", dest="save_first", action="store_true",
                           help="push: snapshot this session (ungated save) FIRST, then bundle it "
                                "— one atomic action (no gap between what you see and what you share)")
    p_session.add_argument("--allow-in-progress", dest="allow_in_progress", action="store_true",
                           help="push: consent to share an IN-PROGRESS (not-yet-approved) session; "
                                "without it such a push refuses (completed sessions need no flag)")
    p_session.add_argument("--requirements-only", dest="requirements_only", action="store_true",
                           help="push: bundle ONLY the distilled requirements (anchor + synthesis "
                                "+ requirement lines; no approaches/approval/transcript) as a "
                                "cross-repo handoff (fingerprint check replaced by an origin label)")
    # Stage 71a — `list` scoping over a shared backend (default: the current project only).
    p_session.add_argument("--all", action="store_true",
                           help="list bundles across ALL projects on a shared backend")
    p_session.add_argument("--project", default=None,
                           help="list a specific project's bundles on a shared backend")
    p_session.add_argument("--list-projects", dest="list_projects", action="store_true",
                           help="list the projects present on the shared backend, then exit")
    p_session.set_defaults(func=cmd_session)


__all__ = [
    "cmd_vault",
    "cmd_session",
]
