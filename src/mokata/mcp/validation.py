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
                 allowed: Optional[Sequence[str]] = None, *,
                 accepted: Optional[Sequence[str]] = None,
                 did_you_mean: Optional[Sequence[str]] = None,
                 retry_with: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(f"invalid `{field}`: {reason}")
        self.field = field
        self.reason = reason
        self.allowed: Optional[Tuple[str, ...]] = tuple(allowed) if allowed is not None else None
        # 0.0.28 I1 front door (`validate_call`): the closed set of argument NAMES the tool takes,
        # the nearest of them to an unknown name, and the corrected call's shape (types/enums only
        # — never a value the caller sent).
        self.accepted: Optional[Tuple[str, ...]] = tuple(accepted) if accepted is not None else None
        self.did_you_mean: Tuple[str, ...] = tuple(did_you_mean or ())
        self.retry_with = retry_with


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
    if exc.accepted is not None:
        out["accepted"] = list(exc.accepted)
    if exc.did_you_mean:
        out["did_you_mean"] = list(exc.did_you_mean)
    if exc.retry_with is not None:
        out["retry_with"] = {"tool": op, "arguments": dict(exc.retry_with)}
    out["reason"] = f"the `{exc.field}` argument to '{op}' {exc.reason}"
    fix = (f"use `{exc.did_you_mean[0]}` instead" if exc.did_you_mean
           else f"a corrected `{exc.field}`"
           + (f" (one of: {', '.join(exc.allowed)})" if exc.allowed else ""))
    out["hint"] = (
        f"this is a bad ARGUMENT, not a missing approval and not a mokata fault — nothing ran and "
        f"nothing was committed. Re-call '{op}' with {fix}"
        + ("; `retry_with` is the corrected call's shape." if exc.retry_with is not None else "."))
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


# ======================================================================================
# 0.0.28 I1 — the FRONT DOOR: every served call, checked against the tool's live schema
# ======================================================================================
#
# `MCP-FIRST-CALL-FAILS`. Before this, FastMCP silently DROPPED an unknown argument name
# (`remember(text=…)` minted a proposal for an EMPTY memory, `query(q=…)` answered about target
# ""), a missing parameter fell back to its default, and pydantic's lax mode turned `passed="yes"`
# into a recorded verdict. `validate_call` runs before FastMCP's own pre-parse, against the schema
# the client itself was shown (`tools/list`), with one table (design note I §3c, binding):
#
#   unknown argument name                      refused, `did_you_mean` + `accepted`
#   missing required argument                  refused, `retry_with`
#   array/object given a JSON string of it     ACCEPTED (the SDK's `pre_parse_json` does it today)
#   integer given "2" / number given "2.5"     ACCEPTED (unambiguous)
#   boolean given true/false or "true"/"false" ACCEPTED (any case)
#   boolean given anything else                refused ("yes", "1", 1, 0 — ambiguous)
#   enum value not in the set (exact)          refused, `allowed`
#   null for a non-nullable / unparseable      refused, `retry_with`
#
# Strict on NAMES and PRESENCE, lenient on REPRESENTABLE conversions, strict on AMBIGUOUS ones: a
# model that stringifies a nested array (they do) is not failed for it, and a model that typed a
# wrong name is told the right one instead of being silently ignored. A refusal never echoes a value.

_BOOL_STRINGS = {"true": True, "false": False}


def _alternatives(prop: Dict[str, Any]) -> List[Dict[str, Any]]:
    alts = prop.get("anyOf") or prop.get("oneOf")
    if isinstance(alts, list) and alts:
        return [a for a in alts if isinstance(a, dict)]
    return [prop]


def _type_hint(prop: Dict[str, Any]) -> str:
    """The shape of a valid value, for `retry_with` — a type or the enum, never a sample value."""
    parts = []
    for alt in _alternatives(prop):
        if alt.get("type") == "null":
            continue
        if "enum" in alt:
            parts.append("one of: " + " | ".join(str(v) for v in alt["enum"]))
        elif alt.get("type") == "boolean":
            parts.append("<boolean: true | false>")
        elif alt.get("type"):
            parts.append(f"<{alt['type']}>")
        else:
            parts.append("<any>")
    return " or ".join(parts) or "<any>"


_NO = object()


def _match(alt: Dict[str, Any], value: Any) -> Any:
    """`value` as this alternative accepts it (possibly converted), or `_NO`."""
    import json
    import math
    import re
    if "enum" in alt:
        for member in alt["enum"]:
            if value == member and type(value) is type(member):
                return value
        return _NO
    if "const" in alt:
        return value if value == alt["const"] and type(value) is type(alt["const"]) else _NO
    t = alt.get("type")
    if t is None:
        return value
    if t == "null":
        return _NO
    if t == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in _BOOL_STRINGS:
            return _BOOL_STRINGS[value.strip().lower()]
        return _NO
    if t == "integer":
        if isinstance(value, bool):
            return _NO
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and re.fullmatch(r"\s*[+-]?\d+\s*", value):
            return int(value)
        return _NO
    if t == "number":
        if isinstance(value, bool):
            return _NO
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                f = float(value)
            except ValueError:
                return _NO
            return f if math.isfinite(f) else _NO
        return _NO
    if t == "string":
        return value if isinstance(value, str) else _NO
    if t in ("array", "object"):
        want = list if t == "array" else dict
        if isinstance(value, want):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                return _NO
            return parsed if isinstance(parsed, want) else _NO
        return _NO
    return value


def _check_value(field: str, prop: Dict[str, Any], value: Any) -> Any:
    alts = _alternatives(prop)
    if value is None:
        if any(a.get("type") == "null" for a in alts) or not any("type" in a or "enum" in a
                                                                 for a in alts):
            return None
        raise ValidationError(field, "must not be null", retry_with={field: _type_hint(prop)})
    for alt in alts:
        got = _match(alt, value)
        if got is not _NO:
            return got
    enum = next((a["enum"] for a in alts if "enum" in a), None)
    if enum is not None:
        raise ValidationError(field, "is not one of the values this tool accepts",
                              allowed=[str(v) for v in enum],
                              retry_with={field: _type_hint(prop)})
    if any(a.get("type") == "boolean" for a in alts):
        raise ValidationError(field, "must be a boolean: true or false (an ambiguous value such "
                                     "as \"yes\", \"1\" or 1 is never guessed)",
                              allowed=["true", "false"], retry_with={field: _type_hint(prop)})
    raise ValidationError(field, f"must be {_type_hint(prop)}",
                          retry_with={field: _type_hint(prop)})


def validate_call(tool: str, schema: Dict[str, Any], arguments: Optional[Dict[str, Any]]
                  ) -> Dict[str, Any]:
    """Check one call's arguments against the tool's input schema (the table above). Returns the
    arguments to serve — the caller's own, with only the table's representable conversions applied
    — or RAISES `ValidationError` naming the first faulty field. Pure; never echoes a value."""
    import difflib
    args = dict(arguments or {})
    props: Dict[str, Any] = dict((schema or {}).get("properties") or {})
    names = sorted(props)
    required = [r for r in (schema or {}).get("required") or [] if r in props]
    for key in args:
        if key not in props:
            near = difflib.get_close_matches(key, names, n=3, cutoff=0.6)
            missing_now = {r: _type_hint(props[r]) for r in required if r not in args}
            fix = dict(missing_now)
            if near:
                fix[near[0]] = _type_hint(props[near[0]])
            raise ValidationError(key, "is not an argument of this tool; it would have been "
                                       "ignored", accepted=names, did_you_mean=near,
                                  retry_with=fix)
    missing = [r for r in required if r not in args]
    if missing:
        raise ValidationError(missing[0], "is required and was not given", accepted=names,
                              retry_with={r: _type_hint(props[r]) for r in missing})
    out: Dict[str, Any] = {}
    for key, value in args.items():
        try:
            out[key] = _check_value(key, props[key], value)
        except ValidationError as bad:
            bad.accepted = tuple(names)
            raise
    return out
