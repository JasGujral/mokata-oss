"""U8 (0.0.25 Lane HB) — the http GATE door: `POST /api/gate` answers a Claude Code PreToolUse http hook
with the verdict of mokata's OWN gate code.

Opt-in and OFF by default (`mokata setup claude --http-gates`, ruling U8). The `mokata-hook` command
gates (secret-guard, gate-guard) stay wired and stay the default; this is an ADDITIONAL, fail-closed
lane wired with `onFailure: "block"`:

  * the verdict is `hook_cli.pretooluse_verdict` — the SAME `secret_guard_main` / `gate_guard_main`
    functions the command hooks run, on the same payload, under the same matchers. There is no second
    policy here: this module only turns `(exit code, stderr)` into the http hook's answer.
  * allow → 2xx with an EMPTY body ("success, no decision" in the hooks reference); block → 2xx with
    `hookSpecificOutput.permissionDecision: "deny"` and the gate's own message as the reason; an
    unreadable request → 400; a crash inside the gate code → 500. With `onFailure: "block"` a non-2xx,
    a refused connection and a timeout all BLOCK the tool call — so while the IDE server is not
    running, every gated tool call is refused. That is the flag's meaning (fail-closed), stated by
    `mokata setup` before it writes and by `mokata doctor` while it is on.

Auth is the ID.S3 hook door: the per-repo hook token in `X-Mokata-Hook` with NO Origin header (a
browser always sends one on a POST), behind the server's Host allowlist.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

GATE_PATH = "/api/gate"


def http_answer(verdict: Tuple[int, str]) -> Tuple[int, Optional[Dict[str, Any]]]:
    """`(status, json-body-or-None)` for one verdict from `pretooluse_verdict`. Pure: the mapping is
    the whole of this door's logic, so it is tested without a server."""
    code, message = verdict
    if code == 0:
        return 200, None
    return 200, {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": message.strip() or "blocked by a mokata gate"}}


def decide(raw: bytes) -> Tuple[int, Optional[Dict[str, Any]]]:
    """Parse the hook's POST body and run the gates. A body that is not a JSON object is a 400 — with
    `onFailure: "block"` that blocks the call, which is right: a gate that cannot read the call it is
    asked about must not say yes."""
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except (ValueError, UnicodeDecodeError):
        payload = None
    if not isinstance(payload, dict):
        return 400, {"error": "a PreToolUse hook payload (JSON object) is required"}
    from ..hook_cli import pretooluse_verdict
    return http_answer(pretooluse_verdict(payload))


__all__ = ["GATE_PATH", "decide", "http_answer"]
