#!/usr/bin/env python3
"""Diff two OpenSSF Scorecard readings PER CHECK, and fail on any per-check drop.

WHY THIS EXISTS (SAST-SCORE-REGRESSION, doc 84). Between 2026-06-30 and 2026-08-04 the
mokata-oss aggregate went 5.5 -> 6.2 -> 6.6 and three consecutive readings recorded that as
progress. Across the very same window `SAST` went 10 -> 7 -> 8, sitting below its baseline the
whole time, and nothing noticed -- because every reading compared the headline number. An
aggregate is a mean: it is arithmetically incapable of telling you that a component regressed.

So the contract is narrow and absolute:

    ANY per-check DROP is a regression, whatever the aggregate did.

THE REASON STRING IS STORED, NOT JUST THE SCORE, and that is not decoration. The row was
confirmed rather than closed precisely because SAST moved 7 -> 8 under a BYTE-IDENTICAL reason
("SAST tool detected but not run on all commits"): the number healing while the sentence held
still is what proved the cause was unchanged and the score was merely aging. A differ that
stored only scores would have read that as a fix.

------------------------------------------------------------------------------------------------
THREE ABSENT ANSWERS, THREE REPRESENTATIONS (doc 85 §7g)

This module handles three different kinds of "we do not have that", and none of them is allowed
to wear the representation of a real answer:

  * NO PREVIOUS READING -- a first run has no delta. It does not have a delta of zero. It gets
    its own status (NO_PREVIOUS), its own exit code (3), and prose that says so. Rendering zeros
    here would mean every first run reads exactly like a clean comparison, forever.

  * SCORECARD COULD NOT SCORE A CHECK -- the API sends `-1`. That is a sentinel, not a number.
    10 -> -1 is not a drop of eleven and -1 -> 10 is not a rise of eleven; they are changes of
    KIND (BECAME_UNSCORED / BECAME_SCORED), and only losing the measurement is a regression.
    ⚠ This is not hypothetical bookkeeping: doc 42's 2026-06-30 baseline records `SAST 10`
    beside `CI-Tests -1 "no pull request found"`, and those are the same fact -- with no merged
    PRs to check, Scorecard's per-commit SAST sub-check went inconclusive and the score fell
    through to "a CodeQL config exists". The instrument itself let an absent answer print as 10.

  * WE NEVER RECORDED THE REASON -- a reading reconstructed from prose has scores but not always
    reason strings. A null is OUR missing note, not Scorecard's answer, so it compares as
    REASON_INCOMPARABLE and never as "the reason changed". Manufacturing a finding out of a gap
    in our own records is the same defect one level up.

The verdict vocabulary follows `tests/_mirror_bookkeeping.py`: the ONE stored signal is the
score, and `state` is DERIVED from it. Two fields that can disagree eventually do.

------------------------------------------------------------------------------------------------
USAGE

    # the post-cut step: fetch live, diff against the stored reading, store the new one
    scripts/scorecard_delta.py --repo JasGujral/mokata-oss \
        --previous docs/build/_scorecard_last.json --store docs/build/_scorecard_last.json

    # offline, over two saved readings
    scripts/scorecard_delta.py --previous old.json --current new.json

    # the genuine first run, stated explicitly rather than inferred from a missing file
    scripts/scorecard_delta.py --repo JasGujral/mokata-oss --first-run --store <path>

EXIT CONTRACT

    0  COMPARED, no per-check regression (the pair may still differ -- rises, reason changes)
    1  COMPARED, and at least one check REGRESSED
    3  NOT COMPARED -- there was no previous reading. Nothing was graded.
    2  usage / IO / parse error (argparse's own code for the first, ours for the rest)

DELIBERATELY NOT BUILT HERE: asserting that the API's reading is for the commit we just tagged.
The API serves the last completed scan and can lag a release by weeks; making the post-cut step
fail loud when the run did not fire is `SCORECARD-API-STALE-AGAIN` (doc 84), a different row
with a different remedy. This tool PRINTS the reading's commit and date so staleness is visible,
and does not pretend to check it.
"""

import argparse
import json
import sys

# ---- a check's score state: DERIVED from the score, never stored beside it --------------------
SCORED = "scored"        # a real number Scorecard produced
UNSCORED = "unscored"    # the API's -1: the check did not produce an answer

# ---- a check's reason state ------------------------------------------------------------------
REASON_RECORDED = "recorded"      # we hold the string Scorecard emitted (possibly empty)
REASON_UNRECORDED = "unrecorded"  # we never wrote it down; not comparable to anything

# ---- the delta's status: three outcomes, three representations -------------------------------
NO_PREVIOUS = "no_previous"   # NOT compared. There is no delta, not a delta of zero.
NO_CHANGE = "no_change"       # compared, and every check matched
CHANGED = "changed"           # compared, and something moved

# ---- per-check change kinds ------------------------------------------------------------------
DROP = "drop"                                  # score fell -- THE feature
RISE = "rise"                                  # score climbed
REASON_CHANGED = "reason_changed"              # same score, different sentence
REASON_INCOMPARABLE = "reason_incomparable"    # same score, and one side's reason is unrecorded
BECAME_UNSCORED = "became_unscored"            # we can no longer measure this check
BECAME_SCORED = "became_scored"                # we can now measure it
REMOVED = "removed"                            # the check left the report entirely
ADDED = "added"                                # the check entered the report

#: kind -> is it a regression. The single place the two vocabularies meet, so "is this bad" can
#: never be asserted independently of the kind that earned it.
_REGRESSION_OF = {
    DROP: True,
    BECAME_UNSCORED: True,   # a lost measurement is not a neutral event
    REMOVED: True,           # ditto, one level up: the check itself stopped being reported
    RISE: False,
    REASON_CHANGED: False,   # worth surfacing, explicitly not worth failing
    REASON_INCOMPARABLE: False,
    BECAME_SCORED: False,
    ADDED: False,
}

REGRESSION_KINDS = frozenset(k for k, bad in _REGRESSION_OF.items() if bad)

#: Reporting order WITHIN the regression / non-regression bands. Loudest first, so the line the
#: reader must not miss is never below the line they can skip.
_KIND_RANK = {
    DROP: 0,
    BECAME_UNSCORED: 1,
    REMOVED: 2,
    RISE: 3,
    BECAME_SCORED: 4,
    ADDED: 5,
    REASON_CHANGED: 6,
    REASON_INCOMPARABLE: 7,
}

# ---- exit codes: "not compared" is not "compared and fine" (mutate.sh's 0-vs-6 split) ---------
EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_USAGE = 2
EXIT_NOT_COMPARED = 3

API = "https://api.securityscorecards.dev/projects/github.com/{repo}"


class CheckReading(object):
    """One check in one reading. `state` and `reason_state` are DERIVED, never supplied."""

    __slots__ = ("name", "score", "reason")

    def __init__(self, name, score, reason):
        self.name = name
        self.score = score      # int, or None when UNSCORED
        self.reason = reason    # str, or None when UNRECORDED

    @property
    def state(self):
        return UNSCORED if self.score is None else SCORED

    @property
    def reason_state(self):
        return REASON_UNRECORDED if self.reason is None else REASON_RECORDED

    def __repr__(self):
        return "CheckReading(%r, %r, %r)" % (self.name, self.score, self.reason)


class Reading(object):
    """One Scorecard run: the aggregate, the commit it scanned, and every check."""

    __slots__ = ("repo", "commit", "date", "aggregate", "checks")

    def __init__(self, repo, commit, date, aggregate, checks):
        self.repo = repo
        self.commit = commit
        self.date = date
        self.aggregate = aggregate
        self.checks = checks    # {name: CheckReading}


class CheckChange(object):
    """One per-check difference. `is_regression` is derived from `kind` through _REGRESSION_OF."""

    __slots__ = ("name", "kind", "before_score", "after_score", "before_reason", "after_reason")

    def __init__(self, name, kind, before_score, after_score, before_reason, after_reason):
        if kind not in _REGRESSION_OF:
            raise ValueError("unknown change kind: %r" % (kind,))
        self.name = name
        self.kind = kind
        self.before_score = before_score
        self.after_score = after_score
        self.before_reason = before_reason
        self.after_reason = after_reason

    @property
    def is_regression(self):
        return _REGRESSION_OF[self.kind]

    def __repr__(self):
        return "CheckChange(%r, %r, %r -> %r)" % (
            self.name, self.kind, self.before_score, self.after_score,
        )


class Delta(object):
    """The result of a comparison -- or the explicit statement that none happened."""

    __slots__ = ("status", "changes", "previous", "current")

    def __init__(self, status, changes, previous, current):
        if status not in (NO_PREVIOUS, NO_CHANGE, CHANGED):
            raise ValueError("unknown delta status: %r" % (status,))
        changes = tuple(changes)
        # The invariant that keeps the three representations from collapsing back into one. A
        # NO_PREVIOUS carrying changes would be a delta claiming not to be one; a CHANGED with
        # nothing in it would be a no-change wearing the other status.
        if status == NO_PREVIOUS:
            if previous is not None:
                raise ValueError("NO_PREVIOUS with a previous reading")
            if changes:
                raise ValueError("NO_PREVIOUS cannot carry changes -- nothing was compared")
        else:
            if previous is None:
                raise ValueError("%s requires a previous reading" % (status,))
            if status == NO_CHANGE and changes:
                raise ValueError("NO_CHANGE cannot carry changes")
            if status == CHANGED and not changes:
                raise ValueError("CHANGED must carry at least one change")
        self.status = status
        self.changes = changes
        self.previous = previous
        self.current = current

    @property
    def compared(self):
        """False ONLY when there was nothing to compare against. Not the same as 'all fine'."""
        return self.status != NO_PREVIOUS

    @property
    def regressions(self):
        return tuple(c for c in self.changes if c.is_regression)

    @property
    def is_regression(self):
        return bool(self.regressions)

    @property
    def exit_code(self):
        if not self.compared:
            return EXIT_NOT_COMPARED
        return EXIT_REGRESSION if self.is_regression else EXIT_OK


# =================================================================================================
# parsing
# =================================================================================================


def parse_reading(obj):
    """Parse one OpenSSF Scorecard API response (or a saved copy of one) into a Reading.

    Refuses rather than defaults. A reading that silently parsed to zero checks would diff
    against a real one as "every check was REMOVED", or against nothing as "no change" -- the
    two worst possible lies this module could tell.
    """
    if not isinstance(obj, dict):
        raise ValueError("reading must be a JSON object, got %s" % type(obj).__name__)
    raw = obj.get("checks")
    if not isinstance(raw, list) or not raw:
        raise ValueError("reading has no 'checks' list -- refusing to parse it as empty")

    checks = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("check entry is not an object: %r" % (entry,))
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("check entry has no name: %r" % (entry,))
        if name in checks:
            raise ValueError("duplicate check name %r -- one of the two would be lost" % (name,))

        score = entry.get("score")
        # bool is an int in Python; a JSON true here means a malformed reading, not a score of 1.
        if isinstance(score, bool) or not isinstance(score, int):
            raise ValueError("check %r has a non-integer score: %r" % (name, score))
        if score == -1:
            score = None                        # the sentinel becomes an ABSENT score, not a -1
        elif not 0 <= score <= 10:
            raise ValueError("check %r score out of range: %r" % (name, score))

        reason = entry.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise ValueError("check %r has a non-string reason: %r" % (name, reason))

        checks[name] = CheckReading(name, score, reason)

    repo = obj.get("repo") or {}
    aggregate = obj.get("score")
    if aggregate is not None and not isinstance(aggregate, (int, float)):
        raise ValueError("aggregate score is not a number: %r" % (aggregate,))
    return Reading(
        repo=repo.get("name"),
        commit=repo.get("commit"),
        date=obj.get("date"),
        aggregate=aggregate,
        checks=checks,
    )


# =================================================================================================
# the diff
# =================================================================================================


def _compare_one(name, before, after):
    """One check, both sides present. Returns a CheckChange or None if nothing moved."""
    b, a = before.score, after.score

    # Score STATE first -- a sentinel must never reach arithmetic.
    if before.state != after.state:
        kind = BECAME_UNSCORED if after.state == UNSCORED else BECAME_SCORED
        return CheckChange(name, kind, b, a, before.reason, after.reason)

    if before.state == SCORED and b != a:
        kind = DROP if a < b else RISE
        return CheckChange(name, kind, b, a, before.reason, after.reason)

    # Scores equal (or both absent). The reason is the only thing left that can have moved.
    if REASON_UNRECORDED in (before.reason_state, after.reason_state):
        if before.reason_state == after.reason_state:
            return None                          # neither side recorded one; nothing to compare
        return CheckChange(name, REASON_INCOMPARABLE, b, a, before.reason, after.reason)
    if before.reason != after.reason:
        return CheckChange(name, REASON_CHANGED, b, a, before.reason, after.reason)
    return None


def diff(previous, current):
    """Compare two readings per check. `previous` is None on a genuine first run."""
    if current is None:
        raise ValueError("a current reading is required")
    if previous is None:
        return Delta(NO_PREVIOUS, (), None, current)

    changes = []
    for name in sorted(set(previous.checks) | set(current.checks)):
        before = previous.checks.get(name)
        after = current.checks.get(name)
        if before is None:
            changes.append(
                CheckChange(name, ADDED, None, after.score, None, after.reason))
        elif after is None:
            changes.append(
                CheckChange(name, REMOVED, before.score, None, before.reason, None))
        else:
            change = _compare_one(name, before, after)
            if change is not None:
                changes.append(change)

    # REGRESSIONS FIRST. A report that lists a drop alphabetically, fourteenth, between two
    # reason-string notes, is a report that buries the one line it was built to surface -- which
    # is this row's own defect wearing a different hat. Within each band the order stays
    # alphabetical so two runs over the same pair render identically.
    changes.sort(key=lambda c: (not c.is_regression, _KIND_RANK[c.kind], c.name))

    status = CHANGED if changes else NO_CHANGE
    return Delta(status, changes, previous, current)


# =================================================================================================
# rendering
# =================================================================================================


def _score_text(state_score):
    return "--" if state_score is None else str(state_score)


_HEADLINE = {
    DROP: "DROPPED",
    RISE: "rose",
    REASON_CHANGED: "reason changed",
    REASON_INCOMPARABLE: "reason not comparable (one side was never recorded)",
    BECAME_UNSCORED: "NO LONGER SCORED",
    BECAME_SCORED: "now scored",
    REMOVED: "NO LONGER REPORTED",
    ADDED: "newly reported",
}


def _reading_line(label, reading):
    return "  %-9s %s  commit %s  scanned %s  aggregate %s" % (
        label,
        reading.repo or "<unknown repo>",
        (reading.commit or "<unknown>")[:12],
        reading.date or "<unknown>",
        "--" if reading.aggregate is None else reading.aggregate,
    )


def render(delta):
    """Human-readable report. The three statuses render as three different documents."""
    out = []
    if not delta.compared:
        # Deliberately no table, no counts, no zeros. A first run that printed "0 changed,
        # 0 dropped" would read exactly like a clean comparison, which is the whole defect.
        out.append("SCORECARD PER-CHECK DELTA: NO PREVIOUS READING")
        out.append("")
        out.append(_reading_line("current", delta.current))
        out.append("")
        out.append("  Nothing was compared, so there is no delta to report. This reading has")
        out.append("  been recorded; the NEXT run is the first one that can grade anything.")
        return "\n".join(out)

    prev, cur = delta.previous, delta.current
    out.append("SCORECARD PER-CHECK DELTA")
    out.append("")
    out.append(_reading_line("previous", prev))
    out.append(_reading_line("current", cur))
    if prev.commit and prev.commit == cur.commit:
        out.append("  NOTE: both readings scanned the same commit.")

    if prev.aggregate is not None and cur.aggregate is not None:
        direction = "unchanged"
        if cur.aggregate > prev.aggregate:
            direction = "ROSE"
        elif cur.aggregate < prev.aggregate:
            direction = "fell"
        out.append("  aggregate %s -> %s (%s) -- NOT the verdict; the per-check table is."
                   % (prev.aggregate, cur.aggregate, direction))
    out.append("")

    if delta.status == NO_CHANGE:
        out.append("  Every check matched the previous reading: score AND reason, all %d of them."
                   % len(cur.checks))
        return "\n".join(out)

    for change in delta.changes:
        marker = "!!" if change.is_regression else "  "
        out.append("%s %-24s %-10s %s" % (
            marker,
            change.name,
            "%s -> %s" % (_score_text(change.before_score), _score_text(change.after_score)),
            _HEADLINE[change.kind],
        ))
        if change.kind in (REASON_CHANGED, REASON_INCOMPARABLE):
            out.append("       was: %s" % ("<never recorded>" if change.before_reason is None
                                           else change.before_reason))
            out.append("       now: %s" % ("<never recorded>" if change.after_reason is None
                                           else change.after_reason))
        elif change.before_reason is not None and change.before_reason == change.after_reason:
            # The row's own signature: the number moved and the sentence did not, which is
            # evidence the CAUSE did not change -- only the sample the score was drawn from.
            out.append("       reason unchanged: %s" % change.before_reason)

    out.append("")
    if delta.is_regression:
        out.append("REGRESSION -- %d check(s) went backwards: %s"
                   % (len(delta.regressions), ", ".join(c.name for c in delta.regressions)))
        out.append("An aggregate that rose does not offset this. That is the entire point.")
    else:
        out.append("No per-check regression. %d change(s) reported above." % len(delta.changes))
    return "\n".join(out)


# =================================================================================================
# CLI
# =================================================================================================


def _read_json_file(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        raise SystemExit("%s: cannot read %s: %s" % (what, path, exc))
    except ValueError as exc:
        raise SystemExit("%s: %s is not valid JSON: %s" % (what, path, exc))


def _fetch(repo):
    from urllib.request import urlopen   # imported here so the module stays offline-importable

    url = API.format(repo=repo)
    try:
        with urlopen(url, timeout=30) as resp:      # noqa: S310 (fixed https host)
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:                        # noqa: BLE001 -- any failure is fatal here
        raise SystemExit("could not fetch %s: %s" % (url, exc))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Diff two OpenSSF Scorecard readings per check; fail on any per-check drop.",
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--repo", help="owner/name -- fetch the live reading from the OpenSSF API")
    src.add_argument("--current", metavar="PATH", help="a saved reading to use as the current one")
    ap.add_argument("--previous", metavar="PATH", help="the stored previous reading")
    ap.add_argument("--first-run", action="store_true",
                    help="there is deliberately no previous reading (exits %d)" % EXIT_NOT_COMPARED)
    ap.add_argument("--store", metavar="PATH", help="write the current reading here afterwards")
    args = ap.parse_args(argv)

    if args.previous and args.first_run:
        ap.error("--previous and --first-run contradict each other")

    current_obj = _fetch(args.repo) if args.repo else _read_json_file(args.current, "current")
    try:
        current = parse_reading(current_obj)
    except ValueError as exc:
        raise SystemExit("current reading is malformed: %s" % exc)

    previous = None
    if args.previous:
        # §7g at the CLI boundary. "you gave me no previous" and "the previous you named is not
        # there" are DIFFERENT answers. Treating the second as the first would turn a deleted or
        # mistyped history file into a clean-looking first run -- permanently, and silently.
        try:
            with open(args.previous, encoding="utf-8") as fh:
                previous_obj = json.load(fh)
        except OSError as exc:
            raise SystemExit(
                "previous reading %s could not be read: %s\n"
                "This is NOT the same as having no previous reading. If this really is the "
                "first run, say so with --first-run." % (args.previous, exc)
            )
        except ValueError as exc:
            raise SystemExit("previous reading %s is not valid JSON: %s" % (args.previous, exc))
        try:
            previous = parse_reading(previous_obj)
        except ValueError as exc:
            raise SystemExit("previous reading is malformed: %s" % exc)
    elif not args.first_run:
        raise SystemExit(
            "no previous reading given. Pass --previous PATH to compare against one, or "
            "--first-run to state that there deliberately is none."
        )

    delta = diff(previous, current)
    print(render(delta))

    if args.store:
        try:
            with open(args.store, "w", encoding="utf-8") as fh:
                json.dump(current_obj, fh, indent=2, sort_keys=True)
                fh.write("\n")
        except OSError as exc:
            raise SystemExit("could not store the current reading at %s: %s" % (args.store, exc))

    return delta.exit_code


if __name__ == "__main__":
    sys.exit(main())
