#!/usr/bin/env python3
"""mokata sync SECURITY hook (G4 + I1) — secret guard.

A synchronous PreToolUse-style hook: scan the tool's CONTENT (and its target path) for
secrets and BLOCK with exit code 2 if any are found. Sync hooks block only for security;
this is the canonical example. Clean content exits 0.

Input resolution (in priority order):
  1. `--text` (and optional `--path`) flags — explicit content to scan.
  2. stdin as a Claude Code PreToolUse JSON envelope — we parse it and scan ONLY the
     `tool_input` fields (the command / file content / edit strings / target path). We
     deliberately NEVER scan the envelope metadata (session_id, transcript_path, cwd,
     hook_event_name, tool_name): those are high-entropy by nature and scanning them
     false-positives and blocks every tool call.
  3. stdin as raw text — scanned as-is (backward-compatible fallback for non-Claude callers).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Make the package importable whether run from the repo or an installed location.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mokata.govern.secrets import scan  # noqa: E402

BLOCK_EXIT = 2

# Keys inside tool_input that name the target path (used for path-based detection, e.g. .env).
_PATH_KEYS = ("file_path", "path", "notebook_path", "target_file")


def _iter_strings(obj):
    """Yield every string value nested anywhere inside obj (dict/list/scalar)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def _find_path(obj):
    """First path-like string found in a tool_input mapping (for path-based detection)."""
    if isinstance(obj, dict):
        for k in _PATH_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and v:
                return v
        for v in obj.values():
            found = _find_path(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_path(v)
            if found:
                return found
    return None


def _from_envelope(raw):
    """If raw stdin is a PreToolUse JSON envelope, return (content_to_scan, target_path)
    drawn ONLY from tool_input. Return None if it isn't such an envelope (so the caller
    falls back to raw-text scanning). The envelope metadata is never scanned."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or "tool_input" not in data:
        return None
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, (dict, list)):
        return None
    content = "\n".join(_iter_strings(tool_input))   # command / content / edit strings only
    path = _find_path(tool_input)
    return content, path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="mokata secret guard (sync security hook)")
    parser.add_argument("--text", default=None, help="content to scan")
    parser.add_argument("--path", default=None, help="target path being written/sent")
    parser.add_argument("--send", action="store_true", help="content is leaving the machine")
    args = parser.parse_args(argv)

    if args.text is not None:
        text, path = args.text, args.path
    else:
        raw = sys.stdin.read()
        envelope = _from_envelope(raw)
        if envelope is not None:
            text, path = envelope
            path = path or args.path        # explicit --path still honored if given
        else:
            text, path = raw, args.path     # raw-text fallback (non-envelope callers)

    findings = scan(text=text or "", path=path, for_send=args.send)
    if findings:
        for f in findings:
            sys.stderr.write(f"BLOCKED [{f.layer}/{f.kind}] {f.detail}\n")
        sys.stderr.write("mokata: secret detected — write/commit/send blocked.\n")
        return BLOCK_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
