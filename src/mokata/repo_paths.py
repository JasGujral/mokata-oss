"""THE INVARIANT. Every repo-relative string in mokata is a NAME or a PATH, never both.

    a NAME is POSIX-spelled, on every platform.
        Anything used as a key, an identifier, a comparison subject, or a serialized value:
        a knowledge-index key, an `Anchor.path`, a `Reference.path`, a freshness entry, an
        exemption-table row, the file a human types into a query.

    a PATH is native.
        Anything handed to `open()`, `os.stat`, `os.walk`, `subprocess`, or printed for a human
        to go and find on their own disk. On Windows a `\\` there is not merely acceptable, it
        is correct.

ONE CONVERSION EACH WAY — `name_of` and `path_of` — applied at the boundary between them, and
NOWHERE ELSE.

WHY THIS FILE EXISTS AND `posix_name` NO LONGER DOES. The pendulum swung three times. Round 1
spelled producers native and consumers POSIX; round 2 reversed it; round 3 repaired 24 sites and
mirror run 32144708930 still failed eleven times, in `crg`'s grep floor, the anchor scan, the AC
mapper and the index's skipped-checkout record. Every one of those was the SAME shape: a producer
that had not moved meeting a consumer that had.

That is not a run of bad luck, it is the arithmetic of the previous fix. `posix_name(rel)` took an
ALREADY-RELATIVE string and converted it, so it was something a site could *remember* to apply —
and every site that remembered created a new half-converted pair with every site that did not. A
conversion you can forget converges only when every site has been visited at once, which is a
thing nobody has ever achieved on a 721-file corpus.

So the conversion is no longer a step. `os.path.relpath` is the ONLY way a repo-relative string is
born, and `name_of` is `relpath` and the spelling in ONE call. There is no state in which the
relativisation has happened and the spelling has not, because there is no line between them. The
choke point is not a convention; it is the absence of an intermediate value.

⭐ THE MEASURE OF SUCCESS IS NEGATIVE: after this, NO comparison anywhere needs to normalise,
because both sides were already the same kind of thing. A `.replace(os.sep, "/")` at a comparison
is now evidence of a producer that has not been routed, not a fix.

`ospath` IS INJECTABLE ON EVERY FUNCTION, and that is not a testing nicety — it is the only reason
the Windows behaviour of this module can be graded on the machine it is written on. Pass `ntpath`
and the Windows branch executes on macOS (doc 85 §7i). Every caller uses the default.

NEAR-LEAF MODULE: stdlib plus `errors` (which is stdlib plus `degrade`, both stdlib-only at module
scope), so `knowledge/`, `engine/`, `govern/`, `spec_scope` and the CLI can all share it. The one
mokata import buys taxonomy membership for `NotARepoName` — D5's rule is that every failure mokata
DEFINES is catchable as a `MokataError`, and a would-be leaf module is not a reason to leave one
exception outside the vocabulary. `tests/test_si_1_hook_gates.TestLatency` pins that the gate hook's
import surface stays cheap, and this edge is inside it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import os.path
from typing import Iterable, List

from .errors import MokataError

#: The separator a NAME is spelled with, on every platform. Not `os.sep` — that is the point.
NAME_SEP = "/"

#: The separator a name must NEVER carry. Refused at construction rather than at comparison,
#: because a comparison that has to ask is the defect this module ends.
FOREIGN_SEP = "\\"


class NotARepoName(MokataError, ValueError):
    """A value was offered as a NAME and is spelled like a PATH.

    Raised at CONSTRUCTION, which is the whole design: the invariant is enforced where the value
    is born, so the hundred places that later match on it do not each have to ask.

    HARD, never a degrade (D5): there is no floor to fall to. A value that cannot be a name cannot
    be half a name, and coercing it would put into the index the exact thing this module exists to
    keep out.

    The SECONDARY `ValueError` base is the house pattern (`LockTimeout` is still a `TimeoutError`,
    `SkillNotFound` still a `KeyError`) and it is what makes routing a site through `name_of` a
    one-line change: every caller that already wrapped `relpath` in `except ValueError` keeps
    working unmodified.
    """


class RepoName(str):
    """A repo-relative IDENTITY, POSIX-spelled, that cannot be constructed any other way.

    A `str` subclass rather than a wrapper so that every existing consumer — dict keys, `sorted`,
    `json.dumps`, `startswith`, `fnmatch`, `==` against a hand-written declaration — keeps working
    with no call-site change. What it adds is a constructor that refuses the wrong spelling.

    ⚠ THE GUARANTEE IS AT CONSTRUCTION, NOT IN THE TYPE, and the difference matters: a name that
    round-trips through JSON comes back a plain `str`. This class proves the PRODUCER was right;
    it does not tag the value forever. The static half of the guard — no `relpath` outside this
    module without a declared reason — is what covers the rest, and the two are complementary
    rather than redundant.

    ⚠ A BACKSLASH IS REFUSED UNCONDITIONALLY, including on POSIX, where it is a legal character in
    a filename. That is a deliberate, stated cost: a repo file named `a\\b.py` cannot be checked
    out on Windows at all, so mokata declining to NAME one loses nothing a Windows user could have
    had, and accepting it would put a value into the index that is indistinguishable from the
    exact defect this module exists to end.
    """

    __slots__ = ()

    def __new__(cls, value: str) -> "RepoName":
        text = str(value)
        if FOREIGN_SEP in text:
            raise NotARepoName(
                "a NAME must be POSIX-spelled on every platform and this one carries a "
                "backslash: %r. It was almost certainly produced by a bare os.path.relpath on "
                "Windows — route the producer through repo_paths.name_of() rather than "
                "converting the value here, which is what makes the next pair half-converted "
                "again." % (text,))
        return super().__new__(cls, text)


def name_of(path: str, root: str, *, ospath=os.path) -> RepoName:
    """★ THE PRODUCER. A native `path` becomes the NAME the repo knows it by, relative to `root`.

    `relpath` and the spelling are one call and there is no intermediate value, so "relativised
    but not yet spelled" is not a state this codebase can be in. Every producer of a repo-relative
    identity calls this and nothing else.

    Raises `ValueError` exactly where `ospath.relpath` does (a different drive on Windows) — the
    caller's existing handling is unchanged, which is why this is a drop-in for the sites it
    replaces.
    """
    rel = ospath.relpath(path, root)
    return RepoName(rel.replace(ospath.sep, NAME_SEP))


def path_of(root: str, name: str, *, ospath=os.path) -> str:
    """★ THE CONSUMER BOUNDARY. A NAME becomes a native path under `root`, for `open()` and the
    `os` functions.

    Splits on `NAME_SEP` and re-joins with the host separator rather than handing the whole name
    to `join` — `os.path.join(root, "a/b")` happens to work on Windows because `/` is an accepted
    input separator there, but it yields the mixed spelling `root\\a/b`, which then fails any
    later comparison against a natively-built path. The mixed value is the trap; this avoids
    producing one.
    """
    parts = [p for p in str(name).split(NAME_SEP) if p not in ("", ".")]
    return ospath.join(root, *parts) if parts else root


def as_name(value: str) -> RepoName:
    """A string that is ALREADY repo-relative, arriving from OUTSIDE this process, as a NAME.

    ⚠⚠ READ THIS BEFORE USING IT. This is a converter over a bare string, which is the exact shape
    of `posix_name` — the function whose deletion this module exists to justify. It is not the
    same thing, and the difference is the only reason it is allowed to exist:

        `posix_name` converted values that had just been PRODUCED INSIDE the tree, where the
        conversion had a counterpart somewhere else that could be left unconverted. That is what
        made every sweep create a fresh half-converted pair.

        `as_name` converts a value that CROSSED IN from outside — a hook payload's `file_path`, a
        CLI argument, a glob a human typed into a spec. There is no in-tree counterpart to fall
        out of step with, because the other half of the pair is a person.

    So the rule is narrow and checkable: `as_name` is for a value that has NOT been through
    `relpath` in this process. If you are reaching for it just after computing a path, you want
    `name_of(path, root)` instead — and `tests/test_repo_paths_invariant.py` enumerates every
    caller so a third use has to be argued for rather than added.

    Unlike `name_of` it is TOTAL: an already-POSIX value passes through untouched, and a native
    one is respelled. It cannot raise, because a caller-supplied string that does not name
    anything is a miss at match time, not an error.
    """
    text = str(value).replace(os.sep, NAME_SEP).replace(FOREIGN_SEP, NAME_SEP)
    return RepoName(text[2:] if text.startswith("./") else text)


def split_path(path: str) -> List[str]:
    """The COMPONENTS of a path, split on either separator — for a value that is not repo-relative
    and therefore has no NAME at all.

    `selfprotect` asks "is any component of this absolute path called `site-packages`" and
    "what is the last component of this command token". Both are questions about a PATH, and an
    absolute Windows path is not a `RepoName` — `C:\\x` has no repo to be relative to — so
    `as_name` would be a category error and `os.sep` alone would answer only for the host.

    It lives here because this module is where the tree keeps its knowledge of the two
    separators. Two callers spelling `.replace("\\\\", "/")` themselves is how that knowledge
    starts drifting, and drift in exactly this knowledge is what the last three rounds were.
    """
    text = str(path).replace(FOREIGN_SEP, NAME_SEP)
    return [p for p in text.split(NAME_SEP) if p]


def escapes_root(name: str) -> bool:
    """Whether `name` points at or above its root rather than inside it — the containment
    predicate for anything that will be resolved under a repo root.

    ⚠ IT ANSWERS FOR BOTH SEPARATORS, AND THAT IS THE ONE DELIBERATE EXCEPTION TO THIS MODULE'S
    OWN RULE. Everywhere else, a comparison that normalises is evidence of an unrouted producer.
    Here the comparison is a SECURITY boundary — it is what stopped `add_ignore(root, token,
    "../outside.py")` from writing outside the repo — and a security predicate that is correct
    only when its caller is correct is one refactor away from being neither. `RepoName` already
    makes a `\\`-spelled argument unconstructible; this is the belt under those braces, and it is
    cheap.

    An empty name, `.` and `..` are all refused: none of them names a file inside the root.
    """
    text = str(name)
    if text in ("", os.curdir, os.pardir, "."):
        return True
    return text.startswith((os.pardir + NAME_SEP, os.pardir + FOREIGN_SEP))


def names_of(paths: Iterable[str], root: str, *, ospath=os.path) -> List[RepoName]:
    """`name_of` over a sequence, sorted. The shape four producers wanted, so that "sorted list of
    names" is not spelled four ways with one of them forgetting the conversion."""
    return sorted(name_of(p, root, ospath=ospath) for p in paths)


__all__ = [
    "FOREIGN_SEP", "NAME_SEP", "NotARepoName", "RepoName",
    "as_name", "escapes_root", "name_of", "names_of", "path_of", "split_path",
]
