"""export / import / stacks / team — share stacks (J3), the community stack catalog, and zero-setup team sync."""
from __future__ import annotations

import argparse
import os
import sys

from ._common import (
    Surface,
    MOKATA_DIR,
    HARNESS_CAPABILITIES,
    SHARE_FILENAME,
    apply_manifest,
    export_manifest,
    load_shared,
    _load_surface,
    _ledger_for,
)


def cmd_export(args: argparse.Namespace) -> int:
    # J3 — export the current manifest as a shareable stack file. Default destination is
    # under .mokata/ so mokata keeps its footprint contained (Stage 24D); an explicit
    # path still writes wherever the user names. The exported stack is committable config,
    # so it goes at the .mokata/ root (not temp_local/).
    #
    # SI.6b: this had the SAME hole as C2's memory export, and a worse one — it wrote a committable,
    # shareable artifact with no secret-scan, no ledger entry and no prompt at all. The write now
    # goes through the universal WriteGate as kind `send`: an export is EGRESS, so the gate scans the
    # exact bytes that leave at outbound strength and records the write with that content hashed.
    # `plan_export` has already DROPPED any secret-bearing key (named below, never its value).
    #
    # `assume_yes=True` is deliberate and is NOT a weakening: CONSENT here is unchanged — it is the
    # human's explicit, typed `mokata export`, exactly as before. The gate is added for the SCAN and
    # the LEDGER, not to invent a prompt this command never had. A secret still HARD-BLOCKS
    # regardless (I1/P2 — no assume_yes lifts a security block).
    from ..govern import WriteGate, WriteRequest
    from ..govern.ledger import AuditLedger
    from ..govern.trust import CLI_SURFACE
    from ..share import ManifestShareError, plan_export

    surface = _load_surface(args.path)
    dest = args.file or os.path.join(args.path, MOKATA_DIR, SHARE_FILENAME)
    plan = plan_export(surface)                  # read-only: scans, drops, writes nothing
    if plan.refused:
        print(f"export refused — {plan.message}", file=sys.stderr)
        return 1

    outcome = WriteGate(ledger=AuditLedger.from_mokata_dir(surface.mokata_dir)).submit(
        WriteRequest("send", dest, content=plan.payload(), actor="cli",
                     tool="stack_export", surface=CLI_SURFACE),
        commit=lambda: export_manifest(surface, dest=dest),
        assume_yes=True)
    if plan.blocked:
        print(f"stack export: {plan.render()}")
    if not outcome.committed:
        print(f"stack export: {outcome.reason} — nothing written.", file=sys.stderr)
        return 1
    print(f"exported stack to {dest}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    # J3 — validate + apply a shared manifest (human-gated).
    #
    # SI.6b: the shared file is UNTRUSTED (P15) and used to overwrite the governing config with no
    # secret-scan and no ledger entry. `apply_manifest` now scans it at the boundary (a hit REFUSES
    # the whole file) and routes the write through the WriteGate — consent is unchanged (the same
    # one prompt, the same text; `--yes` still skips it). Handing it the ledger is what makes the
    # decision auditable (I3).
    try:
        data = load_shared(args.file)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read {args.file}: {exc}", file=sys.stderr)
        return 1
    result = apply_manifest(args.path, data, assume_yes=args.yes, force=args.force,
                            ledger=_ledger_for(args.path))
    if result.blocked:
        print(f"import BLOCKED — {result.message}", file=sys.stderr)
        return 1
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


def cmd_stacks(args: argparse.Namespace) -> int:
    # Stage 70 — community stacks & skill marketplace. list/search/show over a CURATED index
    # (read-only); install = the human-gated, secret-scanned adopt path. NO hosted marketplace.
    from .. import stacks as ST
    action = getattr(args, "action", None) or "list"
    source = getattr(args, "source", None)

    if action in ("list", "search", "show"):
        try:
            index = ST.load_index(source)
        except ST.StackError as exc:
            print(f"stacks: {exc}", file=sys.stderr)
            return 1

    if action == "list":
        entries = ST.list_stacks(index)
        if not entries:
            print("stacks: the catalog is empty.")
            return 0
        print(f"mokata stacks ({len(entries)} available — `mokata stacks show <name>` for detail, "
              f"`mokata stacks install <name>` to adopt):")
        for e in entries:
            print(f"  {e['name']:12} {e.get('framework', '')}")
            print(f"  {'':12} {e.get('summary', '')}")
        print(f"\n{ST.HONEST_NOTE}")
        return 0

    if action == "search":
        query = getattr(args, "target", None)
        if not query:
            print("error: `stacks search <query>` requires a query", file=sys.stderr)
            return 2
        hits = ST.search_stacks(query, index)
        if not hits:
            print(f"stacks: no matches for {query!r}")
            return 0
        print(f"stacks: {len(hits)} match(es) for {query!r}")
        for h in hits:
            print(f"  [{h.score:.2f}] {h.entry['name']:12} {h.entry.get('framework', '')}")
        return 0

    if action == "show":
        name = getattr(args, "target", None)
        if not name:
            print("error: `stacks show <name>` requires a stack name", file=sys.stderr)
            return 2
        entry = ST.show_stack(name, index)
        if entry is None:
            print(f"stacks: no stack named {name!r} (try `mokata stacks list`)", file=sys.stderr)
            return 1
        print(f"{entry['name']} — {entry.get('framework', '')}  (v{entry.get('version', '?')}, "
              f"base profile: {entry.get('profile', '?')})")
        print(f"  {entry.get('summary', '')}")
        print(f"  guardrails: {entry.get('guardrails', 0)} curated · "
              f"skills: {', '.join(entry.get('skills', []) or [])}")
        if entry.get("tags"):
            print(f"  tags: {', '.join(entry['tags'])}")
        print(f"\n{ST.HONEST_NOTE}")
        return 0

    if action == "install":
        name = getattr(args, "target", None)
        if not name:
            print("error: `stacks install <name>` requires a stack name", file=sys.stderr)
            return 2
        res = ST.install_stack(args.path, name, source=source, assume_yes=args.yes,
                               force=getattr(args, "force", False), out=print)
        return 0 if res.installed else 1

    print(f"stacks: unknown action '{action}' (use list | search | show | install).",
          file=sys.stderr)
    return 2


def cmd_team(args: argparse.Namespace) -> int:
    # Stage 69 — zero-setup team sync: adopt a team's governed stack + (optionally) point shared
    # memory/sessions at the team's OWN managed Postgres via an env-var DSN. mokata hosts nothing.
    if not Surface.is_initialized(args.path):
        print(f"team: mokata is not initialized in '{args.path}' — run `mokata init` first.")
        return 0
    from .. import team as T
    action = getattr(args, "action", None) or "status"
    ledger = _ledger_for(args.path)

    if action == "status":
        surface = _load_surface(args.path)
        env = T.connect_status(surface)
        if env:
            ready = T._readiness(env)
            print(f"team: shared memory + sessions point at your managed Postgres via ${env} "
                  f"({'active' if ready.active else 'inactive — driver/DSN not present yet'}).")
        else:
            print("team: local-only (no managed Postgres connected). "
                  f"`mokata team connect --dsn-env {T.DEFAULT_DSN_ENV}` to wire your own.")
        print(T.honest_note())
        return 0

    if action == "init":
        # TM.S3 — first-time team setup: pick backend (guidance) → fail-closed prereqs →
        # ONE idempotent DDL provision pass (the sole DDL owner) → pin project.id → live
        # CONNECTED test. Then `mokata mode set team` activates.
        surface = _load_surface(args.path)
        res = T.team_init(args.path, surface, backend=getattr(args, "backend", None) or "managed",
                          dsn_env=getattr(args, "dsn_env", None) or T.DEFAULT_DSN_ENV,
                          assume_yes=args.yes, ledger=ledger, out=print)
        return 0 if res.ok else 1

    if action == "join":
        # Stage 70b — the ONE guided path: adopt → connect → vault → onboard → verify. Each step
        # is confirmable + degrade-clean; reuses the primitives below (no new engine).
        surface = _load_surface(args.path)
        res = T.team_join(args.path, surface, getattr(args, "source", None),
                          dsn_env=getattr(args, "dsn_env", None) or T.DEFAULT_DSN_ENV,
                          vault_ref=getattr(args, "vault", None), assume_yes=args.yes,
                          force=getattr(args, "force", False), ledger=ledger, out=print)
        return 1 if res.aborted else 0

    if action == "adopt":
        if not getattr(args, "source", None):
            print("team adopt: a <source> (a teammate's stack file or repo) is required.",
                  file=sys.stderr)
            return 1
        res = T.team_adopt(args.path, args.source, assume_yes=args.yes,
                           force=getattr(args, "force", False), ledger=ledger, out=print)
        return 0 if (res.adopted or res.idempotent) else 1

    if action == "connect":
        surface = _load_surface(args.path)
        res = T.team_connect(args.path, surface, getattr(args, "dsn_env", None)
                             or T.DEFAULT_DSN_ENV, assume_yes=args.yes, ledger=ledger, out=print)
        return 0 if res.connected else 1

    if action == "disconnect":
        surface = _load_surface(args.path)
        res = T.team_disconnect(args.path, surface, assume_yes=args.yes, ledger=ledger, out=print)
        return 0 if res.changed or not res.aborted else 1

    print(f"team: unknown action '{action}' (use init | join | status | adopt | connect | "
          f"disconnect).", file=sys.stderr)
    return 2


def cmd_harness(args: argparse.Namespace) -> int:
    # J2 / Stage 52a — list the available harnesses + their capability matrix. The engine is
    # harness-agnostic; a harness lacking a capability degrades clearly (never a silent no-op).
    from ..harness import available_harnesses, get_harness
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


def register(sub, common):
    from ..dsn import DEFAULT_DSN_ENV  # the single source of the default DSN env-var name
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

    p_stacks = sub.add_parser(
        "stacks", parents=[common],
        help="community stacks: list/search/show a curated catalog + install a governed stack "
             "(gated adopt). No hosted marketplace — git/vault publish + a reviewable index.",
    )
    p_stacks.add_argument("action", nargs="?", default="list",
                          choices=("list", "search", "show", "install"),
                          help="list (default) | search <query> | show <name> | install <name>")
    p_stacks.add_argument("target", nargs="?", default=None,
                          help="search: a query · show/install: a stack name")
    p_stacks.add_argument("--source", default=None,
                          help="a git-org/vault catalog dir or index.json to read instead of the "
                               "bundled curated index (same index.json format)")
    p_stacks.add_argument("--yes", action="store_true",
                          help="install: non-interactive (approve the gate)")
    p_stacks.add_argument("--force", action="store_true",
                          help="install: overwrite an existing config")
    p_stacks.set_defaults(func=cmd_stacks)

    p_team = sub.add_parser(
        "team", parents=[common],
        help="zero-setup team sync: adopt a shared governed stack + (optionally) point shared "
             "memory/sessions at your OWN managed Postgres (mokata hosts nothing)",
    )
    p_team.add_argument("action", nargs="?", default="status",
                        choices=("init", "join", "status", "adopt", "connect", "disconnect"),
                        help="init (first-time setup: provision + CONNECTED test) | join <source> "
                             "(guided onboarding) | status (default) | adopt <source> | "
                             "connect --dsn-env <ENV> | disconnect")
    p_team.add_argument("source", nargs="?", default=None,
                        help="join/adopt: a teammate's stack file or repo dir")
    p_team.add_argument("--backend", dest="backend", default=None,
                        choices=("managed", "compose", "local"),
                        help="init: which backend to guide toward — managed DSN (golden path, "
                             "default), compose self-host, or local")
    p_team.add_argument("--dsn-env", dest="dsn_env", default=None,
                        help="join/connect: the env-var NAME holding your managed-Postgres DSN "
                             f"(default {DEFAULT_DSN_ENV}); the DSN value is never stored")
    p_team.add_argument("--vault", dest="vault", default=None,
                        help="join: a repo/dir holding a shared design/spec vault to pull "
                             "(secret-scanned; skipped when absent)")
    p_team.add_argument("--yes", action="store_true", help="non-interactive (approve the gate)")
    p_team.add_argument("--force", action="store_true",
                        help="adopt: overwrite an existing config")
    p_team.set_defaults(func=cmd_team)

    p_harn = sub.add_parser(
        "harness", parents=[common],
        help="list harnesses + their capability matrix (J2); add a name for one",
    )
    p_harn.add_argument("name", nargs="?", default=None,
                        help="show one harness (default: all)")
    p_harn.set_defaults(func=cmd_harness)


__all__ = [
    "cmd_export",
    "cmd_import",
    "cmd_stacks",
    "cmd_team",
    "cmd_harness",
]
