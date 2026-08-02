"""`mokata secret` — the ONLY entry to the secret-scan ignore list (SECRET-IGNORE, deliverable d).

    mokata secret ignore  --token '<string>' --file <path> --reason '<why>'
    mokata secret ignore  --remove --token '<string>'|--hash <h> --file <path>
    mokata secret ignores

The CLI is the only entry because it is the only place a RECOGNISED credential shape gets
refused by name — a hand-edited store cannot run that check, which is why the file's checksum
turns a hand-edit into a loud "re-add it via the CLI". Every decision, the wording, and the
threat model live in `govern/secret_ignore.py`; this module is argparse + exit codes only.
"""
from __future__ import annotations

import argparse
import sys


def cmd_secret(args: argparse.Namespace) -> int:
    from ..govern import secret_ignore

    if args.action == "ignores":
        print(secret_ignore.render_list(args.path))
        return 0

    # `ignore`
    try:
        if args.remove:
            token = args.token or args.hash or ""
            if not token.strip() or not args.file:
                print("error: `secret ignore --remove` needs --token/--hash and --file",
                      file=sys.stderr)
                return 2
            entry = secret_ignore.remove_ignore(args.path, token, args.file,
                                                assume_yes=args.yes)
            return 0 if entry is not None else 1
        if not args.token or not args.file:
            print("error: `secret ignore` needs --token and --file", file=sys.stderr)
            return 2
        entry = secret_ignore.add_ignore(args.path, args.token, args.file,
                                         reason=args.reason, assume_yes=args.yes)
        return 0 if entry is not None else 1
    except secret_ignore.NotIgnorable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except secret_ignore.IgnoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def register(sub, common):
    p = sub.add_parser(
        "secret", parents=[common],
        help="manage secret-scan ignores (entropy-layer false positives only; "
             "a recognised credential shape can never be ignored)",
    )
    p.add_argument("action", choices=("ignore", "ignores"),
                   help="ignore: record (or --remove) one false positive; "
                        "ignores: list what is recorded")
    p.add_argument("--remove", action="store_true",
                   help="revoke a recorded ignore (that string blocks again)")
    p.add_argument("--token", default=None,
                   help="the exact flagged string, as printed in the block message")
    p.add_argument("--hash", default=None,
                   help="the stored hash (--remove only; shown by `mokata secret ignores`)")
    p.add_argument("--file", default=None,
                   help="the ONE file the ignore applies to (repo-relative or absolute)")
    # REQUIRED, and enforced again in `add_ignore` — the reason is what a reviewer reads in the
    # PR diff when they see that a secret finding was suppressed.
    p.add_argument("--reason", default=None,
                   help="why this is not a secret (REQUIRED when recording an ignore)")
    p.add_argument("--yes", action="store_true",
                   help="non-interactive; skip the confirmation prompt")
    p.set_defaults(func=cmd_secret)


__all__ = ["cmd_secret"]
