"""How the suite is INVOKED, compared axis by axis — the half `_preflight_parity` is silent about.

0.0.20 stage 10b, `PREFLIGHT-VS-CI-INVOCATION-PARITY` (doc 84). `test_s27_preflight_parity` derives
the dependency set `release.sh`'s `run_test_preflight` installs from `ci.yml` and reds when they
part — and it derives **nothing about how the suite is INVOKED**. The row's own words: *"a parity
check that compares one axis of two commands and is silent about the other."*

⛔ THE ROW ALSO NAMES THE WRONG FIX, AND NAMES IT AS FORBIDDEN. *"DO NOT close this by adding
`< /dev/null` to `ci.yml`'s `run:` line — that is the exact edit PR #54 made, and it produced 28
failures."* So this module must NOT pin the two commands to being identical. Two invocations of one
suite differ legitimately: `release.sh` may be driven from a terminal and redirects the test
subprocess's stdin for that reason, while a CI runner has no TTY to inherit in the first place.

⭐ THE PROPERTY IS THEREFORE NOT "IDENTICAL" — IT IS "EVERY DIFFERENCE IS DECLARED." Two kinds of
axis, and the split is the whole design:

    SELECTION     -s / -t / -p / -k   — these decide WHICH TESTS RUN. A divergence here means the
                                        preflight grades a different suite than CI does, and there
                                        is no reason that makes that acceptable. Must MATCH.
    ENVIRONMENT   stdin               — decides the CONDITIONS the same tests run under. May differ,
                                        and differs today for a stated reason. Must be DECLARED.

An undeclared environment divergence reds; a declaration with no divergence behind it ALSO reds
(§7i — a register that outlives its entry is a lie that makes the next reader trust the list less).
The two directions are asserted together, which is strictly stronger than a floor.

§7h — SCOPE, STATED SO IT CANNOT BE MISREAD
--------------------------------------------
⚠ **`env:` blocks are NOT an axis here, and that is a limit rather than an omission.** A CI step can
set environment variables the preflight venv never sees, and that changes the conditions exactly as
stdin does. It is a different parse — a YAML mapping merged across three scopes (workflow, job,
step) against a shell function's exports — and G5 says a stage that grows past one mechanism splits.
**Named here so the gap is a decision.**

§7g — the collapses this refuses by construction
-------------------------------------------------
"the preflight runs nothing", "ci.yml runs nothing", and "they agree" must not share a
representation: with either side empty the comparison is VACUOUSLY true and byte-identical to a
real pass. Both render `UNDECIDABLE` with the reason attached.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import re


# ---- verdicts ---------------------------------------------------------------------------------

GREEN = "green"
RED = "red"
UNDECIDABLE = "undecidable"

#: Axes that decide WHICH TESTS RUN. A divergence is never acceptable.
SELECTION_AXES = ("source", "top_level", "pattern", "k_filter")
#: Axes that decide the CONDITIONS. A divergence is acceptable only when declared.
ENVIRONMENT_AXES = ("stdin",)

#: Axes deliberately NOT compared, with the reason. See §7h above.
UNCOMPARED_AXES = {
    "env": ("a CI step's `env:` mapping is merged across workflow/job/step scopes and has no "
            "counterpart to parse on the shell side; a different mechanism, so a different "
            "stage (G5)"),
}

#: An ENVIRONMENT-axis divergence that is allowed, and the reason it is allowed.
#: ⛔ EDITED DELIBERATELY, NEVER NUDGED. An entry here is a statement that the two sides SHOULD
#: differ on this axis — not a note that they currently do.
DECLARED_DIVERGENCES = {
    "stdin": (
        "`release.sh` redirects the test subprocess's stdin from /dev/null and `ci.yml` does not, "
        "and that is CORRECT on both sides. A cut driven from a terminal would hand the suite a "
        "live TTY, and the TTY-aware prompt tests assert the NON-interactive behaviour CI runs "
        "under; a hosted runner has no TTY to inherit, so the redirect would be a no-op there. "
        "⛔ THE FIX IS NOT TO ADD `< /dev/null` TO ci.yml — PR #54 made exactly that edit and "
        "produced 28 failures. The tests set their own stdin explicitly (windows round 2); this "
        "difference is the two callers' own contexts, not a drift."),
}


#: `python -m unittest discover ...` anywhere in a command line. The tail is captured whole and
#: split into axes below — a per-flag regex over the raw text would match a flag in a comment.
_DISCOVER_CALL = re.compile(r"unittest\s+discover\b(?P<tail>[^\n;)]*)")

_OPT = {
    "source": re.compile(r"(?:^|\s)-s\s+(\S+)"),
    "top_level": re.compile(r"(?:^|\s)-t\s+(\S+)"),
    "pattern": re.compile(r"(?:^|\s)-p\s+(?:\"([^\"]*)\"|'([^']*)'|(\S+))"),
    "k_filter": re.compile(r"(?:^|\s)-k\s+(?:\"([^\"]*)\"|'([^']*)'|(\S+))"),
}
_STDIN_REDIRECT = re.compile(r"<\s*/dev/null")

#: unittest's own default when `-p` is not given. Written here so an explicit `-p "test*.py"` on one
#: side and silence on the other read as the SAME selection rather than as a divergence.
DEFAULT_PATTERN = "test*.py"


class Invocation(object):
    """One `unittest discover` call, split into the axes that can differ."""

    __slots__ = ("source", "top_level", "pattern", "k_filter", "stdin", "where")

    def __init__(self, source, top_level, pattern, k_filter, stdin, where):
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "top_level", top_level)
        object.__setattr__(self, "pattern", pattern)
        object.__setattr__(self, "k_filter", k_filter)
        object.__setattr__(self, "stdin", stdin)
        object.__setattr__(self, "where", where)

    def __setattr__(self, *_a):                       # pragma: no cover - immutability guard
        raise AttributeError("Invocation is immutable")

    @property
    def selection(self):
        return tuple(getattr(self, axis) for axis in SELECTION_AXES)

    def axis(self, name):
        return getattr(self, name)

    def render(self):
        return ("%s: -s %s -t %s -p %s%s stdin=%s"
                % (self.where, self.source, self.top_level, self.pattern,
                   (" -k %s" % self.k_filter) if self.k_filter else "", self.stdin))

    def __repr__(self):
        return "<Invocation %s>" % self.render()


def _one(tail, where):
    def grab(name):
        m = _OPT[name].search(tail)
        if m is None:
            return None
        return next((g for g in m.groups() if g is not None), None)

    source = grab("source")
    if source is None:
        return None
    return Invocation(
        source=source,
        # unittest defaults `-t` to `-s` when it is not given; recording the default rather than
        # `None` keeps "not passed" and "passed the same value" from reading as a divergence.
        top_level=grab("top_level") or source,
        pattern=grab("pattern") or DEFAULT_PATTERN,
        k_filter=grab("k_filter"),
        stdin="/dev/null" if _STDIN_REDIRECT.search(tail) else "inherited",
        where=where)


def invocations(text, where):
    """Every `unittest discover` call in `text`, as `Invocation`s. Comment lines are dropped.

    ⚠ Dropping comments is load-bearing, not hygiene — the same reason `_preflight_parity._code_only`
    exists. `run_test_preflight` is more comment than code and its comments QUOTE the redirect they
    describe, so a reader that kept them would find a `< /dev/null` that no shell ever runs.
    """
    code = "\n".join(ln for ln in (text or "").splitlines() if not ln.lstrip().startswith("#"))
    out = []
    for m in _DISCOVER_CALL.finditer(code):
        inv = _one(m.group("tail"), where)
        if inv is not None:
            out.append(inv)
    return tuple(out)


class Divergence(object):
    """One axis on which a preflight invocation and its CI counterpart differ."""

    __slots__ = ("axis", "preflight", "ci", "declared")

    def __init__(self, axis, preflight, ci, declared):
        self.axis = axis
        self.preflight = preflight
        self.ci = ci
        self.declared = declared

    def render(self):
        mark = "DECLARED" if self.declared else "UNDECLARED"
        return ("  %-10s %s  preflight=%r  ci=%r\n    %s"
                % (self.axis, mark, self.preflight.axis(self.axis), self.ci.axis(self.axis),
                   self.preflight.render()))


class Resolution(object):
    """The invocation-parity property, derived."""

    __slots__ = ("verdict", "detail", "unmatched", "divergences", "pairs")

    def __init__(self, verdict, detail="", unmatched=(), divergences=(), pairs=()):
        self.verdict = verdict
        self.detail = detail
        self.unmatched = tuple(unmatched)
        self.divergences = tuple(divergences)
        self.pairs = tuple(pairs)

    @property
    def undeclared(self):
        return tuple(d for d in self.divergences if not d.declared)

    def render(self):
        if self.verdict == UNDECIDABLE:
            return ("UNKNOWN — %s. This is NOT a pass; the two invocations were never compared."
                    % self.detail)
        if self.verdict == GREEN:
            return ("GREEN — every preflight invocation has a ci.yml counterpart on %s, and every "
                    "environment-axis difference is declared (%d pair(s))."
                    % ("/".join(SELECTION_AXES), len(self.pairs)))
        lines = ["RED — the preflight does not invoke the suite the way ci.yml does."]
        for inv in self.unmatched:
            lines.append("  NO COUNTERPART: %s" % inv.render())
            lines.append("    no ci.yml step runs that SELECTION, so the preflight grades a "
                         "different suite than CI ever runs.")
        for d in self.undeclared:
            lines.append("  UNDECLARED DIVERGENCE:\n%s" % d.render())
        return "\n".join(lines)


def resolve(preflight_text, ci_text, declared=None):
    """Compare how `preflight_text` and `ci_text` invoke the suite. Pure over supplied corpora."""
    declared = DECLARED_DIVERGENCES if declared is None else declared
    pre = invocations(preflight_text, "release.sh")
    ci = invocations(ci_text, "ci.yml")
    if not pre:
        return Resolution(UNDECIDABLE, "the preflight text runs `unittest discover` nowhere, so "
                                       "'it invokes the suite like CI' is vacuously true")
    if not ci:
        return Resolution(UNDECIDABLE, "the ci.yml text runs `unittest discover` nowhere, so "
                                       "'the preflight matches it' is vacuously true")

    unmatched, divergences, pairs = [], [], []
    for inv in pre:
        counterparts = [c for c in ci if c.selection == inv.selection]
        if not counterparts:
            unmatched.append(inv)
            continue
        for c in counterparts:
            pairs.append((inv, c))
            for axis in ENVIRONMENT_AXES:
                if inv.axis(axis) != c.axis(axis):
                    divergences.append(Divergence(axis, inv, c, axis in declared))

    verdict = GREEN if not unmatched and all(d.declared for d in divergences) else RED
    return Resolution(verdict, "", unmatched, divergences, pairs)


def stale_declarations(preflight_text, ci_text, declared=None):
    """Declared axes with no divergence behind them — §7i, in the other direction."""
    declared = DECLARED_DIVERGENCES if declared is None else declared
    res = resolve(preflight_text, ci_text, declared)
    if res.verdict == UNDECIDABLE:
        return ()
    live = {d.axis for d in res.divergences}
    return tuple(sorted(set(declared) - live))
