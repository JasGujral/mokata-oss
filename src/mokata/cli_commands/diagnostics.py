"""doctor / baseline / config — diagnose the config, report the baseline test suite, and get/set backend config (set is human-gated)."""
from __future__ import annotations

import argparse
import sys

from ._common import (
    ConfigError,
    Surface,
    config_cmd,
    diagnose,
    ManifestError,
    _load_surface,
)


def cmd_doctor(args: argparse.Namespace) -> int:
    from ..legibility import _color_enabled
    # DOC-ONBOARD — `--wiring`: the WIRING-ONLY check, answered BEFORE a Surface is loaded.
    # This is what `mokata upgrade` runs the instant the new package lands and what a user runs
    # when the wiring is the broken thing; demanding an initialized `.mokata/` first would make
    # the diagnostic unavailable in exactly the cases it exists for. Read-only.
    if getattr(args, "wiring", False):
        from ..govern.doctor import wiring_check_lines
        ok, lines = wiring_check_lines(root=args.path, home=getattr(args, "home", None),
                                       ascii_only=not _color_enabled())
        for line in lines:
            print(line)
        return 0 if ok else 1
    surface = _load_surface(args.path)
    report = diagnose(surface)
    # DB.S1 — the DSN DEEP-CHECK. For a TEAM-connected repo, classify the shared-DB connection into
    # NAMED, secret-free findings (driver / network / auth / pooler / schema-version), each with its
    # concrete fix and the env-var NAME (never the DSN value). Reuses `teamdb.probe` (bounded ≤500ms,
    # fail-closed — never a second probe) + `dsn_inspect` (DB.S0 shape). A local / no-DSN repo is
    # SILENT (None — zero probe, zero noise; P8). Computed here, BEFORE `report.render()`, so its rich
    # section prints separately below while its findings feed `report.ok` further down.
    from .. import db_doctor
    db_check = db_doctor.deep_check(surface)
    if db_check is not None:
        # Fold each DSN finding into `report.findings` BEFORE render, so the overall
        # status line AND the exit code both derive from the SAME `report.ok` — a hard
        # failure (driver / network / auth / schema-broken → error) reads as PROBLEMS FOUND
        # and exits non-zero; pooler + an in-range version difference are warnings (OK). The
        # terse `summary` is the table cell; the full fix renders in the section below.
        from ..govern.doctor import DoctorFinding
        for f in db_check.findings:
            report.findings.append(DoctorFinding(f.severity, f.code, f.summary))
    # Colour + a Unicode box only on a real TTY (NO_COLOR unset); a piped / redirected /
    # NO_COLOR run degrades to a clean plain-ASCII table with zero escape codes.
    color = _color_enabled()
    print(report.render(ascii_only=not color, color=color))
    # MCP wiring — registered? enabled? permitted? CONNECTED? — via the SAME shared reporter as
    # `mokata mcp status` (mcp_admin.full_status) so the two can't drift. Informational: it never
    # changes doctor's ok/exit (derived from `report.ok`); a broken/ungranted server prints its
    # named fix (`mokata mcp install`) but doesn't fail doctor.
    from .. import mcp_admin
    print("")
    for line in mcp_admin.full_status(root=args.path,
                                      home=getattr(args, "home", None)).lines:
        print(line)
    # B-VER — the version-parity finding (parity + scope/plugin shadow), the SAME shared reporter
    # `mokata mcp status` uses. Informational: doctor's ok/exit stays derived from `report.ok`,
    # never from parity. This is the MCP-server version axis — a DIFFERENT axis than DB.S1's shared
    # schema-VERSION finding just below (which mokata binary Claude Code launches vs which shared
    # schema the DB carries); the two are kept as distinct findings, never conflated.
    for line in mcp_admin.parity_lines(root=args.path, home=getattr(args, "home", None),
                                       quiet_when_ok=False):
        print(line)
    # DB.S1 — the DSN deep-check SECTION: the full, actionable `database (team DSN)` block for a
    # team-connected repo (each finding names its axis, the concrete fix, and the env-var NAME —
    # never the DSN value). Its findings already fed `report.ok` above; this is the human-facing
    # rich view. Silent on a local / no-DSN repo (P8). The SCHEMA-VERSION axis here is distinct from
    # the MCP-server version parity above — different axes, never conflated.
    if db_check is not None:
        print("")
        for line in db_check.render_lines(ascii_only=not color):
            print(line)
    # B-SKILLS — the skills-visibility finding: are mokata's Agent Skills + `/`-commands actually
    # wired in THIS root (a new session on a worktree / fresh checkout shows an empty `/` menu),
    # and if present, the restart hint that answers the Claude-Code-side caching case. Same shared
    # reporter style as parity_lines; informational — never changes doctor's ok/exit (derived from
    # `report.ok` above). Loud when wrong; on the OK path it shows the "visible ✓ + restart" line.
    from .. import skills_visibility as _skills_visibility
    for line in _skills_visibility.skills_visibility_lines(
            root=args.path, home=getattr(args, "home", None), quiet_when_ok=False):
        print(line)
    # MCP-R.D2 (B-AMEND-STUCK / UX-STUCK) — "what is waiting on YOU". The returning-user answer to
    # the worst UX in the product: a run that is regressed with an amendment pending looks wedged
    # from the outside, and until now doctor — the command you run when things look wedged — said
    # nothing about it. Same shared-reporter style as parity_lines / skills_visibility_lines.
    #
    # INFORMATIONAL, and emphatically so: a pending proposal is the gate WORKING (P2), not a
    # problem. It is never a DoctorFinding and never touches `report.ok`, so doctor's exit stays
    # derived from `report.ok` alone — exactly as it does for the MCP/parity/skills sections above.
    # Secret-safe: names the tool, the id, and the commands; never the proposal's summary/preview.
    from .. import awaiting as _awaiting
    for line in _awaiting.pending_lines(args.path, quiet_when_ok=False):
        print(line)
    # The LIVENESS line. D0 already made "no MCP call hangs past its budget" true (the R1 wall-clock
    # timeout + per-class budgets); this only tells the human that guarantee EXISTS, so a slow call
    # reads as bounded rather than as the hang it used to be. Referencing D0, not rebuilding it.
    for line in _awaiting.liveness_lines():
        print(line)
    # DB.S4 — the RETRIEVAL STACK: which engines actually rank a recall in this repo (semantic:
    # model2vec / hashing / off; lexical: fts5 / tsvector / jaccard). Retrieval quality used to be
    # invisible — two installs could both read "memory: ok" while one ranked by meaning and the
    # other by token-hash — so this says which, and labels the hashing tier as NOT semantic.
    #
    # INFORMATIONAL, like the MCP/parity/skills/awaiting sections above: every state it prints is a
    # supported configuration (hashing + jaccard is a working zero-dep install), so it emits no
    # DoctorFinding and doctor's ok/exit stays derived from `report.ok` alone.
    from ..memory import tier_report
    print("")
    for line in tier_report.retrieval_lines(surface, ascii_only=not color):
        print(line)
    # R13 — the full pass/degraded/fail coverage matrix is opt-in (`--matrix`) so the default
    # problem summary stays lean. It's informational: doctor's ok/exit stays derived from
    # `report.ok` above, never from the matrix. Read-only, degrade-clean.
    if getattr(args, "matrix", False):
        from ..govern import coverage_matrix
        print(coverage_matrix(surface).render(ascii_only=not color, color=color))
    return 0 if report.ok else 1


def cmd_baseline(args: argparse.Namespace) -> int:
    # Stage 34B — report the test suite green/red at baseline; degrade-clean if no command
    # is known (mokata never guesses a test framework). Read-only diagnostic.
    from ..baseline import baseline_command, baseline_status
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
    # RT.S3 A3 — `wizard` is an interactive, gated walk that routes every change through
    # the SAME `config_set` write path (never a second authority).
    try:
        if args.action == "wizard":
            from ..config_wizard import run_wizard
            run_wizard(args.path)
            return 0
        if args.action in ("get", "set") and not args.key:
            print(f"error: `config {args.action} <key>` requires a key", file=sys.stderr)
            return 2
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


def register(sub, common):
    p_doc = sub.add_parser(
        "doctor", parents=[common],
        help="diagnose the manifest/config (missing deps, conflicts, bad trust)",
    )
    p_doc.add_argument("--wiring", action="store_true",
                       help="check ONLY the harness hook wiring — are mokata's gates wired, "
                            "launchable, and current? (works on an uninitialized repo; exit 1 "
                            "if a gate would not fire OR the wiring is out of date)")
    p_doc.add_argument("--matrix", action="store_true",
                       help="also print the full capability coverage matrix "
                            "(pass/degraded/fail); read-only, does not change the exit code")
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
        help="get/set config, or run the gated settings wizard (set/wizard are human-gated)",
    )
    p_config.add_argument("action", choices=("get", "set", "wizard"),
                          help="read a key, set one (preview + confirm), or walk the settings wizard")
    p_config.add_argument("key", nargs="?", default=None,
                          help="dotted manifest key, e.g. tools.sqlite.config.path (get/set)")
    p_config.add_argument("value", nargs="?", default=None,
                          help="value to set (required for 'set')")
    p_config.add_argument("--yes", action="store_true",
                          help="non-interactive; skip the confirmation prompt")
    p_config.set_defaults(func=cmd_config)


__all__ = [
    "cmd_doctor",
    "cmd_baseline",
    "cmd_config",
]
