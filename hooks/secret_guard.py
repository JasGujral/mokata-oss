#!/usr/bin/env python3
"""mokata sync SECURITY hook (G4 + I1) — secret guard.

A synchronous PreToolUse-style hook: scan content (and target path) for secrets and
BLOCK with exit code 2 if any are found. Sync hooks block only for security; this is the
canonical example. Clean content exits 0. Reads from --text/--path flags or stdin.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import os
import sys

# Make the package importable whether run from the repo or an installed location.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mokata.govern.secrets import scan  # noqa: E402

BLOCK_EXIT = 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="mokata secret guard (sync security hook)")
    parser.add_argument("--text", default=None, help="content to scan")
    parser.add_argument("--path", default=None, help="target path being written/sent")
    parser.add_argument("--send", action="store_true", help="content is leaving the machine")
    args = parser.parse_args(argv)

    text = args.text if args.text is not None else sys.stdin.read()
    findings = scan(text=text or "", path=args.path, for_send=args.send)
    if findings:
        for f in findings:
            sys.stderr.write(f"BLOCKED [{f.layer}/{f.kind}] {f.detail}\n")
        sys.stderr.write("mokata: secret detected — write/commit/send blocked.\n")
        return BLOCK_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
