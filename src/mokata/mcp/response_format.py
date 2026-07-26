"""MCP-R.D1b · `response_format` — concise (default) vs detailed, the ONE place a render is dropped.

Before this stage several read tools paid DOUBLE token cost: the full structured dict AND a
pre-rendered human view (`block`/`report`) in the SAME result, so an agent's context was burned on
an ASCII table it never asked for (P22 — efficient by default). This module is the single mechanism
that fixes it: a tool builds its structured dict and wraps each render field in a `LazyRender`
marker; `apply_response_format` — the ONLY site of the concise/detailed branch — DROPS those markers
under concise (the default) and REALIZES them under detailed. The human view is never lost (P16
legibility): it moves behind `response_format:detailed`, byte-identical to today's output.

Two properties make this the shared mechanism rather than a per-tool copy-paste:
  * LAZY — a render field is a callable, so concise never pays to build the string it drops.
  * ORDER-PRESERVING — markers sit in their natural position in the tool's dict, so the detailed
    result iterates in the SAME key order as before D1b (byte-identical, not merely equal).

Pure stdlib — never imports the optional MCP SDK (same discipline as `registry`/`status`/
`tool_annotations`), so the concise/detailed decision is unit-testable without the SDK. Typed-enum
VALIDATION of the format value is deliberately deferred to D1d: `is_detailed` is lenient (anything
that is not exactly `detailed` — including the default and any unknown value — is concise), which
keeps concise the safe, token-frugal default and adds no validation surface this stage.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

CONCISE = "concise"
DETAILED = "detailed"
RESPONSE_FORMATS = (CONCISE, DETAILED)


class LazyRender:
    """A render field that is only BUILT when the caller asked for `detailed`. A tool places one of
    these at the key its human view belongs under (`block`/`report`); `apply_response_format` drops
    it under concise and calls `build()` under detailed. Holding a callable (not the string) is what
    makes concise free: the ASCII view is never computed when it is about to be dropped."""

    __slots__ = ("_build",)

    def __init__(self, build: Callable[[], Any]) -> None:
        self._build = build

    def build(self) -> Any:
        return self._build()


def is_detailed(response_format: str) -> bool:
    """The single predicate. Detailed IFF exactly `detailed`; everything else — the `concise`
    default and any unknown value — is concise. Strict-detailed keeps the frugal path the default;
    validating the enum is D1d's job, not this stage's."""
    return response_format == DETAILED


def apply_response_format(response_format: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """The ONE shared branch (MCP-R.D1b). Given a tool's already-built dict — its structured keys
    plus any `LazyRender` render fields in their natural positions — return the SAME keys with the
    render fields dropped (concise, the default) or realized in place (detailed). Detailed iterates
    `data` in insertion order, so the result is BYTE-IDENTICAL to the pre-D1b return; concise is that
    result minus the render string(s). No tool hand-rolls the concise/detailed decision — every tool
    routes its render through here, so a NEW tool that wraps its render in a `LazyRender` and returns
    `apply_response_format(...)` gets concise/detailed for free."""
    detailed = is_detailed(response_format)
    out: Dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, LazyRender):
            if detailed:
                out[key] = value.build()
            # concise: the render field is dropped entirely (the token win)
        else:
            out[key] = value
    return out
