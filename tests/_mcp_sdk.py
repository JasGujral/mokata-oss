"""MCP-SDK-2-BREAKS-THE-SERVER — the test-side gate: a skip that CANNOT stand in for a pass.

`@unittest.skipUnless(MS.mcp_available(), ...)` conflated two facts that must never share a
representation (doc 85 §7g): *no SDK is installed* (a stripped env — nothing to grade, skipping is
honest) and *an SDK is installed at a major mokata cannot use* (the product is DEAD and the suite
says `OK (skipped=8)`). `mcp` 2.0.0 shipped 2026-07-28 and removed `mcp.server.fastmcp` outright;
from that day CI installed it, `mcp_available()` went False, and 61 tools' worth of coverage
evaporated into a skip while the gate stayed green for fifteen days.

This module is stage 2's `PYYAML-SKIP-CLUSTER` remedy one subsystem over: grade the DISPOSITION,
not the import style, and refuse loudly at the use site rather than reporting nothing.

  ABSENT       — no `mcp` distribution at all              -> skip (honest: nothing to grade)
  BROKEN       — a supported `mcp`, its own dep missing    -> skip (a broken env, not our defect)
  INCOMPATIBLE — `mcp` present at an unsupported major     -> **FAIL LOUD** (this IS the defect)
  SUPPORTED    — run

⚠ `skipUnless` also evaluated its condition at IMPORT time, i.e. at decoration. This gate evaluates
at CALL time, so a test cannot be silently disarmed by whatever the module happened to see while it
was being imported.

**The positive control (`GATE-COUNT-TRUTH`).** Two of the four states above still skip, so on a leg
where the SDK is *supposed* to be installed a skip is itself a defect. Setting
`MOKATA_REQUIRE_MCP_SDK=1` turns EVERY non-SUPPORTED state into a failure — CI sets it on the legs
that install the SDK, so "it ran" is asserted rather than assumed. Without it a developer in a
stripped checkout still gets a polite skip.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import functools
import os
import unittest

import _support  # noqa: F401 - puts src/ on the path

# ⚠ The MODULE is imported, not the function. `from ... import sdk_state` would bind the callable
# once, at import — which is the very late-vs-early distinction this gate exists to fix, and it
# would make the gate unpatchable (and so ungradable) by a test.
from mokata.mcp import server as _server                     # noqa: E402
from mokata.mcp.server import (  # noqa: E402
    SDK_ABSENT,
    SDK_BROKEN,
    SDK_INCOMPATIBLE,
    SDK_SUPPORTED,
)

#: Set to a truthy value on any CI leg that installs the MCP SDK. Turns a skip into a failure, so
#: an absent/broken SDK on a leg that is supposed to have one cannot pass as coverage.
REQUIRE_ENV = "MOKATA_REQUIRE_MCP_SDK"

#: States that may legitimately skip — an environment fact about the machine, not about mokata.
#: `SDK_INCOMPATIBLE` is deliberately NOT here: that one is mokata's own defect and must be loud.
_SKIPPABLE = (SDK_ABSENT, SDK_BROKEN)


def required() -> bool:
    """True when this leg has DECLARED the SDK must be present, so no state may skip."""
    return bool(os.environ.get(REQUIRE_ENV, "").strip())


def check_sdk_or_raise(state=None, require=None) -> None:
    """Raise the right exception for the SDK's disposition, or return for a supported one.

    Pure over its arguments so the gate itself is gradable (§7i) — a test can hand it a synthetic
    disposition instead of waiting for a machine that happens to have the wrong SDK installed.
    """
    state = _server.sdk_state() if state is None else state
    require = required() if require is None else require
    if state.state == SDK_SUPPORTED:
        return
    if state.state == SDK_INCOMPATIBLE:
        # NEVER skippable, and never silenced by the absence of the env var: an SDK at an
        # unsupported major means the shipped server cannot start for a real user.
        raise AssertionError(state.diagnosis())
    if require:
        raise AssertionError(
            f"{REQUIRE_ENV} is set, so the MCP SDK must be usable on this leg, but it is not.\n"
            + state.diagnosis())
    raise unittest.SkipTest(state.diagnosis())


def requires_mcp_sdk(target):
    """Replaces `@unittest.skipUnless(MS.mcp_available(), ...)` at every MCP-gated test.

    Accepts a test METHOD or a TestCase CLASS — `skipUnless` was used both ways, and a conversion
    that only handled one shape would leave the other silently ungated. Either way the check runs
    when the test RUNS, never when the module is imported.
    """
    if isinstance(target, type):
        original_setup = target.setUp

        @functools.wraps(original_setup)
        def setUp(self, *args, **kwargs):
            check_sdk_or_raise()
            return original_setup(self, *args, **kwargs)

        target.setUp = setUp
        return target

    @functools.wraps(target)
    def wrapper(*args, **kwargs):
        check_sdk_or_raise()
        return target(*args, **kwargs)
    return wrapper


# --- the sweep: no test may go back to the two-meanings gate ------------------------------
# Written as a PURE FUNCTION over source text so it can be graded against a planted offender
# rather than against a tree this stage just emptied (doc 85 §7i).

def skip_unless_offenders(source: str, filename: str = "<test>") -> list:
    """Return `(lineno, text)` for every `skipUnless`/`skipIf` whose condition reads the SDK.

    Catches the shape regardless of how the callable is spelled — `mcp_available()`,
    `MS.mcp_available()`, `M.mcp_available()` — because the defect is the CONFLATION, not the
    import style (stage 2's lesson: grade the disposition, not the idiom).
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    lines = source.splitlines()
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in ("skipUnless", "skipIf"):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            sub_fn = sub.func
            sub_name = sub_fn.attr if isinstance(sub_fn, ast.Attribute) else getattr(sub_fn, "id", "")
            if sub_name in ("mcp_available", "sdk_state"):
                text = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
                offenders.append((node.lineno, text))
                break
    return offenders
