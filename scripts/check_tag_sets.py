#!/usr/bin/env python3
"""Do mokata's two repositories publish the same tags? THREE answers, and one of them is "cannot say".

0.0.19 stage 08, row B3 (`release-tail-ungraded`).

WHY THIS EXISTS. The dev annotated tag `v0.0.18` was never created. PyPI carried the release, the
mirror carried its tag, the GitHub Release carried its signed assets, and the dev repository's tags
stopped one short — for four weeks, with every check in the cut green. Nothing observed it; a plan
went looking, by hand, and found it. `release.sh` labelled that step *last, manual* and graded it
not at all.

⭐ AND THE ROW'S EXAMPLE WAS ONE INSTANCE OF A CLASS. Asking the question mechanically finds an
older divergence still live: `v0.0.4` is published by the mirror and absent from the dev repository.
It has been that way for fifteen releases. Nobody noticed, because until this file nothing asked.

WHY IT CANNOT LIVE INSIDE THE TAG STEP. Stage 07 routed both tags through `ensure_tag`, which now
creates / converges / refuses in the repo AND on its remote — so a run that REACHES the tag step
can no longer leave the two repositories in different states. That is a real fix and it is blind to
what actually happened: the step never ran. A guard inside a step cannot observe the step's own
non-execution. So this check is called from `release.sh`'s PREFLIGHT, before the first push of the
NEXT cut, where it grades the tail of the LAST one whatever that run did or did not do — and again
after the tag step, so the tail of THIS one is graded rather than merely performed.

⛔ TAG NAMES, NEVER TAG COMMITS. `sync-public.sh` builds the mirror as an independent squashed
history, so `v0.0.17` names a DIFFERENT commit in each repository by design. Comparing commits would
report every tag as divergent, and a check that is wrong every time is a check that gets switched
off. `ensure_tag` compares commits because it is asking a different question — is THIS tag, in THIS
repository, where it should be — and both questions are needed.

THREE STATES, THREE REPRESENTATIONS (doc 85 §7g), in stage 06's vocabulary rather than a fourth
spelling of the same idea:

    AGREE          both sides were read and publish the same tags
    DIVERGE        both sides were read and they do not — with WHICH tag missing from WHICH side
    NOT_CHECKABLE  a side could not be read at all. NEITHER green NOR red

⛔ `NOT_CHECKABLE` IS DECIDED BEFORE THE COMPARISON, and the order is the mutant this module dies
of. An unreadable side beside a side that legitimately has no tags compares as "nothing is missing
from anybody" — a green produced by having asked nothing, which is exactly the defect this row
exists to remove. `NOT_CHECKABLE` is not in `PASSING`, and `release.sh` refuses on it while saying
plainly that it is a READ failure and not a divergence, so nobody creates or deletes a tag to
clear a network outage.

    exit 0  AGREE
    exit 1  DIVERGE          -> REFUSE
    exit 2  NOT_CHECKABLE    -> REFUSE, and do not "fix" a tag to clear it

SECRET SAFETY. This runs where a token lives and its whole job is to print the two remotes it
consulted. `git remote get-url` will hand back credentials embedded in a URL if any are configured,
so every rendered spec goes through `redact_remote` — which replaces the userinfo with a marker
rather than deleting it, because a silently stripped URL and a URL that never had one must not look
identical either.

SHIPPED, DELIBERATELY. `tests/test_b3_release_tail_ungraded.py` imports this module, so it must be
excluded by NEITHER mirror control — `scripts/floor-python.sh`'s rule. It reads no internal path and
names no private content: the two repository names it is pointed at arrive as arguments.

Pure comparison over a supplied reading; the only impure part is `_git_ls_remote`, which is
injectable so the decision logic can be graded without a network.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple

# ---- the three states -----------------------------------------------------------------------

#: Both sides were read and publish the same tags.
AGREE = "AGREE"
#: Both sides were read and they do not agree. Never green.
DIVERGE = "DIVERGE"
#: A side could not be read at all. Neither green nor red — see the module docstring.
NOT_CHECKABLE = "NOT_CHECKABLE"

#: The states a run may treat as satisfied. `NOT_CHECKABLE` is deliberately absent.
PASSING = (AGREE,)

EXIT_AGREE = 0
EXIT_DIVERGE = 1
EXIT_NOT_CHECKABLE = 2

#: What replaces credentials in a rendered remote. Present rather than blank, on purpose.
REDACTED = "<redacted>"

#: Bounded, because an unreachable host must produce NOT_CHECKABLE rather than a hung release.
LS_REMOTE_TIMEOUT = 60

_TAG_REF = re.compile(r"^\S+\s+refs/tags/(.+?)(\^\{\})?$")
_USERINFO = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*://)([^/@]+)@")


def redact_remote(spec: str) -> str:
    """`spec` with any embedded credentials replaced by `REDACTED`.

    The marker is left in place rather than the whole userinfo being dropped: a reader must be able
    to tell "this remote carried credentials, which are not shown" from "this remote carried none".
    """
    return _USERINFO.sub(r"\1" + REDACTED + "@", spec or "")


def parse_ls_remote(text: str) -> Tuple[str, ...]:
    """The tag NAMES in `git ls-remote --tags` output, sorted and de-duplicated.

    ls-remote prints the tag OBJECT for `refs/tags/<t>` and the peeled commit for
    `refs/tags/<t>^{}`. Both lines name ONE tag; counting them separately would make every
    annotated tag look like two, and a lightweight tag like one — an asymmetry that would then be
    "explained" rather than fixed.
    """
    names = set()
    for line in (text or "").splitlines():
        found = _TAG_REF.match(line.strip())
        if found:
            names.add(found.group(1))
    return tuple(sorted(names))


# ---- one side's reading ------------------------------------------------------------------------

@dataclass(frozen=True)
class TagSetResult:
    """What one repository publishes, or the reason we do not know. Read-only.

    `tags=()` and `tags=None` are DIFFERENT FACTS and never share a representation: the first is a
    repository that has published no tags, the second is a question that was never answered.
    """

    label: str
    spec: str
    tags: Optional[Tuple[str, ...]]
    error: str = ""

    @property
    def readable(self) -> bool:
        return self.tags is not None

    def render(self) -> str:
        where = redact_remote(self.spec)
        if not self.readable:
            return "  ? %-7s %s\n      COULD NOT BE READ: %s" % (self.label, where, self.error)
        return "    %-7s %s\n      %d tag(s)" % (self.label, where, len(self.tags))


# ---- the comparison --------------------------------------------------------------------------

@dataclass(frozen=True)
class DivergenceReport:
    """The verdict, with the evidence that produced it. Read-only diagnostic."""

    state: str
    dev: TagSetResult
    mirror: TagSetResult
    missing_from_dev: Tuple[str, ...] = ()
    missing_from_mirror: Tuple[str, ...] = ()
    unreadable: Tuple[str, ...] = ()

    @property
    def passing(self) -> bool:
        return self.state in PASSING

    def render(self) -> str:
        lines = ["%s: %s vs %s" % (self.state, self.dev.label, self.mirror.label),
                 self.dev.render(), self.mirror.render()]
        if self.state == NOT_CHECKABLE:
            lines.append(
                "  The tag sets were NOT compared: %s could not be read. This is a READ failure,\n"
                "  not a divergence — do NOT create or delete a tag to clear it."
                % (", ".join(self.unreadable),))
        if self.missing_from_dev:
            lines.append("  MISSING FROM %s (published by %s): %s"
                         % (self.dev.label, self.mirror.label,
                            ", ".join(self.missing_from_dev)))
        if self.missing_from_mirror:
            lines.append("  MISSING FROM %s (published by %s): %s"
                         % (self.mirror.label, self.dev.label,
                            ", ".join(self.missing_from_mirror)))
        if self.state == DIVERGE:
            lines.append(
                "  A release tag published by one repository and absent from the other is the\n"
                "  0.0.18 defect: a tag step that was skipped, and nothing that noticed. Create the\n"
                "  missing tag at that release's commit in the repository that lacks it, or delete\n"
                "  it deliberately from the one that has it. mokata never moves a published tag.")
        return "\n".join(lines)

    @property
    def exit_code(self) -> int:
        return {AGREE: EXIT_AGREE, DIVERGE: EXIT_DIVERGE,
                NOT_CHECKABLE: EXIT_NOT_CHECKABLE}[self.state]


def compare(dev: TagSetResult, mirror: TagSetResult) -> DivergenceReport:
    """The two readings' verdict.

    ⛔ ORDER IS LOAD-BEARING. `NOT_CHECKABLE` is decided BEFORE the set arithmetic, because the set
    arithmetic answers AGREE for an unread side — `None` and `()` both yield "nothing is missing"
    once they reach a difference. Deciding after the comparison is this module's headline mutant.
    """
    unreadable = tuple(side.label for side in (dev, mirror) if not side.readable)
    if unreadable:
        return DivergenceReport(NOT_CHECKABLE, dev, mirror, unreadable=unreadable)

    missing_from_dev = tuple(t for t in mirror.tags if t not in dev.tags)
    missing_from_mirror = tuple(t for t in dev.tags if t not in mirror.tags)
    state = DIVERGE if (missing_from_dev or missing_from_mirror) else AGREE
    return DivergenceReport(state, dev, mirror,
                            missing_from_dev=missing_from_dev,
                            missing_from_mirror=missing_from_mirror)


# ---- acquisition (the only impure part, and it is injectable) -----------------------------------

def _git_ls_remote(spec: str) -> Tuple[int, str, str]:
    """`git ls-remote --tags <spec>` as `(returncode, stdout, stderr)`; a timeout is a failure."""
    try:
        done = subprocess.run(["git", "ls-remote", "--tags", spec],
                              capture_output=True, text=True, timeout=LS_REMOTE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 124, "", "git ls-remote did not answer within %ds" % LS_REMOTE_TIMEOUT
    except OSError as problem:                                  # git absent, spec unusable
        return 127, "", str(problem)
    return done.returncode, done.stdout, done.stderr


def read_tags(spec: str, label: str,
              runner: Optional[Callable[[str], Tuple[int, str, str]]] = None) -> TagSetResult:
    """What the repository at `spec` publishes — or an UNREADABLE reading carrying the reason."""
    code, out, err = (runner or _git_ls_remote)(spec)
    if code != 0:
        detail = (err or out or "").strip().splitlines()
        return TagSetResult(label=label, spec=spec, tags=None,
                            error="git ls-remote exited %d: %s"
                                  % (code, redact_remote(detail[-1] if detail else "no output")))
    return TagSetResult(label=label, spec=spec, tags=parse_ls_remote(out))


def check_tag_sets(dev_spec: str, mirror_spec: str,
                   dev_label: str = "dev", mirror_label: str = "mirror",
                   runner: Optional[Callable[[str], Tuple[int, str, str]]] = None
                   ) -> DivergenceReport:
    """The gate verdict for two repositories, named by their remotes."""
    return compare(read_tags(dev_spec, dev_label, runner),
                   read_tags(mirror_spec, mirror_label, runner))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare the tag sets published by mokata's dev repository and its public "
                    "mirror. Exit 0 AGREE, 1 DIVERGE, 2 NOT CHECKABLE (never a pass).")
    parser.add_argument("--dev", required=True,
                        help="anything `git ls-remote` accepts for the dev repository")
    parser.add_argument("--mirror", required=True,
                        help="anything `git ls-remote` accepts for the public mirror")
    parser.add_argument("--dev-label", default="dev")
    parser.add_argument("--mirror-label", default="mirror")
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = check_tag_sets(args.dev, args.mirror, args.dev_label, args.mirror_label)
    stream = sys.stdout if report.passing else sys.stderr
    print(report.render(), file=stream)
    return report.exit_code


if __name__ == "__main__":                                      # pragma: no cover
    raise SystemExit(main())
