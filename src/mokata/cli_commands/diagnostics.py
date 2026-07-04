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
    surface = _load_surface(args.path)
    report = diagnose(surface)
    print(report.render())
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


def register(sub, common):
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


__all__ = [
    "cmd_doctor",
    "cmd_baseline",
    "cmd_config",
]
