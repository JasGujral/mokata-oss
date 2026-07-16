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
    surface = _load_surface(args.path)
    report = diagnose(surface)
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
    # never from parity. DB.S1 folds `mcp_admin.version_parity` into its DSN deep-check later.
    for line in mcp_admin.parity_lines(root=args.path, home=getattr(args, "home", None),
                                       quiet_when_ok=False):
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
