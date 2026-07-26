"""MCP-R.D1d · input validation — the ONE place a bad tool argument becomes a typed refusal.

Before this stage the MCP surface passed caller arguments straight into the engine. An unknown
`query` `kind` reached the backend and raised `ValueError` (D0 reclaimed it as `status:"error"` —
mokata's voice, but the WRONG verdict: it blamed the server for the caller's typo). A comma-list
like `",,,"` silently parsed to `[]`, indistinguishable from the unscoped default, so `ci_check`
reported PASS over a change the caller believed it had scoped. And a relative `path` was joined
straight onto the filesystem, so `../../etc` was read as a repo root. This module is the single
mechanism that fixes all three: the caller's mistake comes back as a bounded, machine-legible
refusal that NAMES the bad field and (where there is a closed set) its allowed values — never a
stack trace, and never a read outside the working root (P14 bounded refusal, P16 legible refusal).

ONE pattern, uniform: every validator RAISES `ValidationError`, and the `_serve` front door
(`mcp/server.py`) converts it — once, in one place — into the structured `status:"refused"` result.
No tool builds a refusal dict by hand, and no tool needs a try/except.

`refused`, not `error` — deliberately (R6 vocab, `mcp/status.py`). `error` is documented as an
uncaught exception / non-dict return, i.e. a MOKATA-side fault, and its hint says so ("run `mokata
doctor`"). A bad argument is a CALLER-side fault the caller can fix by re-calling, which is exactly
what `refused` already means everywhere else on this surface (a refused write names the bound and
the next step). Branching on `status` alone must tell an agent "fix your call" apart from "the
server broke", so bad input can never be `error`.

SECRET-SAFETY: a refusal echoes the FIELD NAME and, for a closed enum, the ALLOWED values — it
NEVER echoes the offending VALUE. A `path` or a comma-list can carry a token, a home directory, or
a customer identifier, and a refusal is rendered into the model's context; naming the field and
the violation CLASS is enough to fix the call, so the value never travels.

Pure stdlib — never imports the optional MCP SDK (same discipline as `registry`/`status`/
`response_format`/`pagination`/`tool_annotations`), so every validation decision is unit-testable
without the SDK.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import status as _status
from ..errors import MokataError
from .response_format import RESPONSE_FORMATS

# The single reason_code every input refusal carries, so a caller can branch on the CLASS of
# refusal (bad argument) before reading the field name.
INVALID_INPUT = "invalid_input"


class ValidationError(MokataError):
    """A caller-side argument fault. Carries the FIELD that was wrong, the allowed values when the
    field has a closed set, and a violation description that never quotes the offending value.

    Raised by the validators below and converted — in exactly one place, `_serve` — into the
    structured `status:"refused"` result. It is deliberately NOT caught per-tool: a tool body that
    swallowed it would turn a legible refusal back into a silent wrong answer.

    D5 taxonomy: a HARD error (`failure_class` stays ""), NOT a `DegradedCapability`. Nothing fell
    back to a floor here — no capability was unavailable and no weaker answer was substituted. The
    call simply did not run, and it propagates to the front door, which is exactly what the hard
    class means. Pairing it with a `note_degraded` would be wrong: a caller's typo is not mokata
    running degraded."""

    def __init__(self, field: str, reason: str,
                 allowed: Optional[Sequence[str]] = None) -> None:
        super().__init__(f"invalid `{field}`: {reason}")
        self.field = field
        self.reason = reason
        self.allowed: Optional[Tuple[str, ...]] = tuple(allowed) if allowed is not None else None


def refusal(exc: ValidationError, op: str) -> Dict[str, Any]:
    """The ONE structured refusal an input fault becomes (R6 `refused`). Mirrors the shape mokata's
    existing refusals already speak (`status`/`committed`/`reason_code`/`reason`/`hint`) and adds
    the two fields that make an input fault actionable: `field`, and `allowed` when the field has a
    closed set. `allowed` is OMITTED (not null) for open-valued fields — a caller must not be told
    there is a list to choose from when there isn't one.

    The offending VALUE is never included (see the module note on secret-safety)."""
    out: Dict[str, Any] = {
        "status": _status.REFUSED, "committed": False, "operation": op,
        "reason_code": INVALID_INPUT, "field": exc.field,
    }
    if exc.allowed is not None:
        out["allowed"] = list(exc.allowed)
    out["reason"] = f"the `{exc.field}` argument to '{op}' {exc.reason}"
    out["hint"] = (
        f"this is a bad ARGUMENT, not a missing approval and not a mokata fault — nothing ran and "
        f"nothing was committed. Re-call '{op}' with a corrected `{exc.field}`"
        + (f" (one of: {', '.join(exc.allowed)})." if exc.allowed else "."))
    return out


# ======================================================================================
# The validators — each RAISES; `_serve` converts. No validator returns a refusal dict.
# ======================================================================================

def validate_enum(value: str, allowed: Sequence[str], field: str) -> str:
    """A closed-set argument. Returns the value UNCHANGED when it is a member (so the valid path is
    byte-identical — this is a guard, not a normalizer: no case-folding, no aliasing, no coercion),
    and raises naming the field + the allowed set otherwise.

    The allowed set is passed IN rather than tabled here on purpose: `kind` means different things
    on different tools (`query`'s query kinds, `remember`'s memory kinds, `vault_push`'s artifact
    kinds), so a by-name table would validate one tool's value against another tool's vocabulary.
    Each call site supplies the set it actually grounds against."""
    if value not in allowed:
        raise ValidationError(field, "is not one of the values this tool accepts",
                              allowed=allowed)
    return value


def validate_comma_list(value: str, field: str) -> List[str]:
    """A comma-separated argument, parsed to the stripped non-empty entries.

    The parse is byte-identical to what the call sites did before this stage
    (`[x.strip() for x in value.split(",") if x.strip()]`) — what is NEW is that two shapes stop
    passing silently:

      * MALFORMED-EMPTY — a NON-empty argument that yields ZERO entries (`","`, `" , , "`). Today
        that is indistinguishable from the default, so the tool runs its UNSCOPED path while the
        caller believes they scoped it: `ci_check` returns PASS over a change it never checked.
        That is a wrong answer dressed as a right one, so it refuses.
      * CONTROL CHARACTERS — an embedded NUL or newline. A comma-list is a single-line argument by
        contract; a NUL is an OS-level path/argument footgun and a newline means the caller pasted
        a multi-line list into a single-line field.

    The EMPTY string stays LEGAL and returns `[]` — it is the documented default of every call site
    (`files=""` on `ci_check` means "no file scope"), so the absent case is not a malformed one."""
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValidationError(
            field, "contains a control character (NUL or newline); it is a single-line, "
                   "comma-separated list")
    entries = [x.strip() for x in value.split(",") if x.strip()]
    if value and not entries:
        raise ValidationError(
            field, "is a comma-separated list with no usable entries (only separators and "
                   "whitespace); leave it empty for the unscoped default instead")
    return entries


def guard_path(path: str, field: str = "path") -> str:
    """The traversal guard for the repo-root `path` every mokata tool takes.

    GROUNDED SEMANTICS — deliberately narrow, because a false positive here breaks all 55 tools:

      * ABSOLUTE paths are ALLOWED, unchanged. There is no server-pinned root to measure them
        against: `mokata-mcp --path` is not wired into the tools (see `server.main` — "each tool
        takes its `path`"), so a repo root is whatever the caller names. A user legitimately runs
        the tools against `/Users/me/other-repo`, and refusing that would be overbroad. An absolute
        path is an explicit, deliberate destination, not an escape.
      * RELATIVE paths must stay inside the process working root. `"."` (the default of every tool)
        IS that root and always passes; `"sub/repo"` passes; `"../../etc/passwd"` climbs OUT and
        refuses. This is the actual traversal class: an argument that LOOKS repo-relative while
        silently resolving somewhere else.
      * SYMLINKS are resolved before the comparison (`realpath`, not `normpath`), so a symlink
        inside the working root that points outside it cannot be used to launder an escape.
      * NUL bytes refuse outright — the OS treats an embedded NUL as a string terminator, so a
        path carrying one is never what the caller believes it is.

    The valid path returns the ORIGINAL string UNCHANGED — this guard never rewrites an argument, so
    every tool's behaviour on a legitimate `path` is byte-identical to before D1d.

    `realpath` is used on a possibly non-existent path deliberately: it normalizes without requiring
    existence, so a not-yet-initialized repo root is judged on its LOCATION (which is the security
    question) rather than on whether it happens to exist yet."""
    if "\x00" in path:
        raise ValidationError(field, "contains an embedded NUL byte")
    if os.path.isabs(path):
        return path
    # "" has always behaved as "." here (`os.path.join("", MOKATA_DIR)` == `.mokata`), so it is
    # judged as the working root rather than refused — this guard adds no new rejection class.
    candidate = path or "."
    root = os.path.realpath(os.getcwd())
    resolved = os.path.realpath(os.path.join(root, candidate))
    if resolved != root and not resolved.startswith(root + os.sep):
        raise ValidationError(
            field, "is a relative path that resolves OUTSIDE the working root; pass a path inside "
                   "it, or an absolute path to the repo you mean")
    return path


def validate_response_format(value: str, field: str = "response_format") -> str:
    """The D1b `{concise, detailed}` enum, validated at last. D1b shipped `is_detailed` lenient on
    purpose (anything not exactly `detailed` is concise) and named THIS stage as the one that would
    make an unknown value loud — see `response_format.py`'s module note. Without it a typo
    (`"detail"`, `"verbose"`) silently returns the concise result and the caller never learns their
    render was dropped because they misspelled the request."""
    return validate_enum(value, RESPONSE_FORMATS, field)


# ======================================================================================
# The shared PRE-STEP — the surface-wide arguments, validated once, before any body runs
# ======================================================================================
#
# Two arguments mean exactly ONE thing on every tool that takes them, so they are validated by NAME
# at the front door rather than at 55 + 8 call sites:
#
#   path             — the repo root. Grounded on all 55 tool signatures: every one is the root the
#                      tool operates on (`_surface(path)` / `_mokata_dir(path)` / `cwd=path`).
#   response_format  — the D1b {concise, detailed} enum, identical on all 8 tools that take it.
#
# Nothing else qualifies. `kind` is tool-specific (three different vocabularies), `source` is a
# catalog URL/path rather than an enum, `phase` is a free-text deviation label, and `transport` is
# already guarded upstream by SIMP.S1 — a by-name table would validate them against a vocabulary
# they do not have. Those stay at their own call sites, or stay alone.
SURFACE_VALIDATORS: Dict[str, Callable[..., Any]] = {
    "path": guard_path,
    "response_format": validate_response_format,
}


def validate_surface_params(args: tuple, kwargs: dict) -> None:
    """The shared pre-step (MCP-R.D1d). Validate the surface-wide arguments of a served call and
    raise on the first fault; returns None on success and mutates nothing — the tool receives its
    arguments exactly as the caller sent them.

    Runs BEFORE the body AND before the R5 self-registration side effect (which itself calls
    `Surface.load(path)`), so a traversing `path` causes NO filesystem read at all — the ordering is
    part of the guarantee, not an accident of implementation.

    `path` is resolved positionally-or-by-keyword, mirroring `server._call_path`: every mokata tool
    takes it as its first parameter. `response_format` is read from keyword arguments only, which is
    how an MCP client always sends it (FastMCP binds a JSON object of named arguments) and how every
    in-repo caller writes it."""
    path = kwargs.get("path")
    if path is None and args and isinstance(args[0], str):
        path = args[0]
    if isinstance(path, str):
        guard_path(path)

    fmt = kwargs.get("response_format")
    if isinstance(fmt, str):
        validate_response_format(fmt)
