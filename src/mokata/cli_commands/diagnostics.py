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
    # This is what a user runs the instant a new package lands (`mokata update`'s follow-up) and
    # when the wiring is the broken thing; demanding an initialized `.mokata/` first would make
    # the diagnostic unavailable in exactly the cases it exists for. Read-only.
    if getattr(args, "wiring", False):
        from ..govern.doctor import wiring_check_lines
        ok, lines = wiring_check_lines(root=args.path, home=getattr(args, "home", None),
                                       ascii_only=not _color_enabled())
        for line in lines:
            print(line)
        return 0 if ok else 1
    # 0.0.26 L8 (MP.S1) — `--plugin`: the plugin route's health, answered BEFORE a Surface is
    # loaded (a plugin-only user may not have initialized any repo yet). Read-only; what
    # `/mokata:doctor` runs through the plugin's pinned launcher.
    if getattr(args, "plugin", False):
        from ..plugin_cache import read_plugin_root
        from ..plugin_runtime import plugin_route_report
        root = getattr(args, "plugin_root", None) or read_plugin_root()
        rep = plugin_route_report(root)
        if not root:
            print("mokata plugin route: no plugin root recorded (~/.mokata/plugin-root) — the "
                  "plugin's SessionStart hook has not run on this machine; pass --plugin-root.")
        for line in rep.lines(ascii_only=not _color_enabled()):
            print(line)
        unresolved = _pin_does_not_resolve(root, rep)
        if unresolved:
            print(unresolved)
        return 0 if (rep.ok and root) else 1
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
    from ..ide import trust as ide_trust
    trust_line = ide_trust.status_line(args.path)
    if trust_line:
        print(trust_line)
    # 0.0.28 C1 — the private set's verdicts (informational: never changes doctor's ok/exit). A file
    # on a volume that cannot hold privacy is SAID, never claimed private; `--fix` re-protects OUR weak
    # files with their exact bytes and never touches one someone else owns.
    for line in privacy_lines(args.path, fix=getattr(args, "fix", False)):
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


#: How long the plugin-route doctor waits for `uvx` to answer whether the pin resolves. It is a
#: network resolve (not the full install): long enough for a slow index, never forever.
PIN_PROBE_TIMEOUT_SECONDS = 120.0


def _pin_does_not_resolve(root, rep) -> "str | None":
    """0.0.28 I1 (`PLUGIN-PIN-BEFORE-PUBLISH`, H8). When the plugin route has no pinned environment
    yet, ask uv whether the plugin's pin RESOLVES at all — the field failure was `No solution found …
    no version of mokata[full]==0.0.27`: the plugin pinned a version PyPI did not have yet, and the
    server never started. Returns the doctor line naming that cause and its fix, or None (healthy,
    not checkable, or failing for another reason uv's own error names).

    Runs only when "package resolved" failed, so a healthy doctor does no network read."""
    import subprocess
    from .. import quiet_spawn
    from ..legibility import _color_enabled
    from ..plugin_runtime import UV_PYTHON, _manifest_pin, _plugin_manifest, find_uvx
    if not root or any(c.ok for c in rep.checks if c.label == "package resolved"):
        return None
    pin = _manifest_pin(_plugin_manifest(root))
    uvx = find_uvx()
    if not pin or not uvx:
        return None
    argv = [uvx, "--python", UV_PYTHON, "--from", pin, "mokata-mcp", "--version"]
    try:
        # Through the process door: uvx is a tree (uv → python), ended whole on timeout.
        rc, _out, err = quiet_spawn.run_jobbed(argv, timeout=PIN_PROBE_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None
    if rc != 0 and ("No solution found" in err or "no version of" in err):
        return (f"  {'!!' if not _color_enabled() else '✗'} pinned `{pin}` does not resolve on "
                f"the package index — the plugin was published before its release reached PyPI\n"
                f"      fix: update the plugin, or wait for the release to reach PyPI")
    return None


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
    from ..project_env import settings_python
    cmd = baseline_command(manifest, override=args.cmd)
    result = baseline_status(cmd, cwd=args.path, settings_python=settings_python(manifest))
    print(result.render())
    # green and unknown exit 0 (unknown = no command, degrades clean); red AND not-run exit 1 —
    # a suite the door could not run (no job, no runner) proves nothing, so it is not a pass.
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


def private_set_files(root: str) -> "list":
    """The private set's files that exist for `root` (0.0.28 C1): the IDE server and bridge records, the
    workspace-trust record, and every `.claude/settings.local.json` mokata baked a hook token into —
    the repo's own and each copy Claude Code made into a session worktree (`.worktreeinclude`)."""
    import glob
    import os
    from .. import winacl
    from ..ide import bus, server, trust
    out = [p for p in (server.record_path(root), bus.bridge_path(root), trust.record_path(root))
           if os.path.isfile(p)]
    claude = os.path.join(root, ".claude")
    for p in [os.path.join(claude, "settings.local.json")] + sorted(
            glob.glob(os.path.join(glob.escape(claude), "worktrees", "*", ".claude",
                                   "settings.local.json"))):
        got = winacl.read_record(p)                     # a regular file only, never blocking on a FIFO
        if got is not None and got[1] is not None and \
                bus.HOOK_HEADER in got[1].decode("utf-8", "replace").lower():
            out.append(p)
    return out


def privacy_lines(root: str, fix: bool = False) -> "list":
    """`mokata doctor`'s file-privacy section: one line per private-set file with its verdict, read by
    `winacl.read_record` (never inferred): ONE open, no FIFO wait, and the verdict and the bytes `--fix`
    rewrites come from the SAME handle, so a file swapped in after the verdict is never what gets
    re-protected. `fix=True` rewrites each WEAK file that is OURS through the private door with its exact
    bytes (token, port, trust decision kept: the baked gate still authenticates). A file owned by someone
    else is named and never touched; there is no delete remedy."""
    import os
    from .. import fs_identity, winacl
    from ..atomicfile import atomic_write_text
    files = private_set_files(root)
    lines = []
    if files:
        lines += ["", "File privacy (tokens and workspace trust):"]
    for p in files:
        got = winacl.read_record(p, ask=True)
        if got is None:
            lines.append(f"  {p}: REFUSED — not a regular file this user can open; never trusted and "
                         f"never changed")
            continue
        v, raw = got
        if v.private:
            lines.append(f"  {p}: private")
        elif not v.owner_is_me:
            lines.append(f"  {p}: FOREIGN — not yours, never trusted and never changed ({v.reason})")
        elif not v.holds:
            lines.append(f"  {p}: NOT private — {winacl.NO_ACCESS_CONTROL}")
        elif fix:
            try:
                if raw is None:
                    raise OSError("it is larger than a record can be")
                atomic_write_text(p, raw.decode("utf-8"), newline="", private=True)
                lines.append(f"  {p}: was WEAK ({v.reason}); re-protected, bytes unchanged")
            except (OSError, UnicodeDecodeError) as exc:
                lines.append(f"  {p}: WEAK ({v.reason}); could not re-protect: {exc}")
        else:
            lines.append(f"  {p}: WEAK ({v.reason}) — `mokata doctor --fix` re-protects it")
    if not fs_identity.identity_known(root):
        lines.append(f"  {os.path.realpath(root)}: this volume gives the repository folder no file "
                     f"identity — whether a path is inside it is decided by its spelling alone")
    return lines


def register(sub, common):
    p_doc = sub.add_parser(
        "doctor", parents=[common],
        help="diagnose the manifest/config (missing deps, conflicts, bad trust)",
    )
    p_doc.add_argument("--wiring", action="store_true",
                       help="check ONLY the harness hook wiring — are mokata's gates wired, "
                            "launchable, and current? (works on an uninitialized repo; exit 1 "
                            "if a gate would not fire OR the wiring is out of date)")
    p_doc.add_argument("--plugin", action="store_true",
                       help="check ONLY the Claude Code plugin route — uv found, the pinned "
                            "package resolved, plugin/package versions match, the MCP server "
                            "running, the hooks firing (works on an uninitialized repo; exit 1 "
                            "if any check fails)")
    p_doc.add_argument("--plugin-root", default=None,
                       help="the plugin checkout to check (default: the one the plugin's "
                            "SessionStart recorded in ~/.mokata/plugin-root)")
    p_doc.add_argument("--matrix", action="store_true",
                       help="also print the full capability coverage matrix "
                            "(pass/degraded/fail); read-only, does not change the exit code")
    p_doc.add_argument("--fix", action="store_true",
                       help="re-protect mokata's own token and trust files that became readable by "
                            "others (exact bytes kept); a file someone else owns is never touched")
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
