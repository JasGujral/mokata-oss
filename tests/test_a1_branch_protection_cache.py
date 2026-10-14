"""0.0.22 stage A1 · `BRANCH-PROTECTION-DEGRADED-PASS`, option (b) — the last-read cache.

WHAT WAS LEFT OPEN. Since 0.0.18 an unreadable protection document could still PASS (exit 3) on two
live reads alone: `branches/<b>` saying `protected: true`, and a consistent `/rulesets`. That says
protection EXISTS and nothing about what it forbids — the four assurances that live only in the
document. The row named two ways to restore them and chose neither; this stage builds (b):

    cache the last SUCCESSFULLY READ protection document per repo, with its read time, and make the
    degraded path corroborate against it.

⭐ THE §7g TRAP THE ROW SAID TO DESIGN IN, NOT DISCOVER. A cache MISS and a cache HIT that DISAGREES
are different facts, and a cache with no age is an undated claim in a file. So the degraded path has
four ways NOT to corroborate, and each is its own outcome, its own failure line and its own exit:

    CACHE_MISS           nothing was ever read in full here      -> exit 4, refuse
    CACHE_HIT_DISAGREES  the last full read was NOT safe         -> exit 5, refuse
    CACHE_HIT_TOO_OLD    older than PROTECTION_CACHE_MAX_AGE_HOURS -> exit 6, refuse
    CACHE_UNUSABLE       a file is there and cannot be trusted   -> exit 7, refuse

and only CACHE_HIT_AGREES (fresh AND safe, plus both live reads) passes, degraded, at exit 3.

The `gh` layer and the clock are both injected, so every outcome is exercised offline and at an exact
age. No test here reads an internal file, so this module SHIPS with the code it grades.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import datetime
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import branch_protection as BP
from mokata.branch_protection import (
    CACHE_HIT_AGREES,
    CACHE_HIT_DISAGREES,
    CACHE_HIT_TOO_OLD,
    CACHE_MISS,
    CACHE_UNUSABLE,
    EXIT_CACHE_DISAGREES,
    EXIT_CACHE_MISS,
    EXIT_CACHE_TOO_OLD,
    EXIT_CACHE_UNUSABLE,
    EXIT_DEGRADED,
    EXIT_NOT_PROTECTED,
    EXIT_PROTECTED,
    EXIT_UNREADABLE,
    PROTECTION_CACHE_MAX_AGE_HOURS,
    RESTORE_ROW,
    ProtectionCache,
    check_branch_protection,
    default_cache_dir,
)

REPO = "JasGujral/mokata-oss"
DETAIL = f"repos/{REPO}/branches/main/protection"
BRANCH = f"repos/{REPO}/branches/main"
RULESETS = f"repos/{REPO}/rulesets"
SAFE = {"required_status_checks": {"strict": True, "contexts": []},
        "allow_force_pushes": {"enabled": False}, "allow_deletions": {"enabled": False}}
WEAK = dict(SAFE, allow_force_pushes={"enabled": True})
NOW = datetime.datetime(2026, 10, 7, 12, 0, 0, tzinfo=datetime.timezone.utc)
UNREADABLE_503 = (1, "", "gh: HTTP 503: Service Unavailable")


def _routed(responses):
    def run(path):
        code, out, *err = responses.get(path, UNREADABLE_503)
        return code, out, (err[0] if err else "")
    return run


def _outage(branch='{"protected": true}', rulesets="[]"):
    """The 0.0.18 outage: the DETAIL 503s, the two corroborating reads answer."""
    return _routed({DETAIL: UNREADABLE_503, BRANCH: (0, branch), RULESETS: (0, rulesets)})


def _green(doc=SAFE):
    return _routed({DETAIL: (0, json.dumps(doc))})


class _Cache(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.cache = ProtectionCache(self.dir)

    def seed(self, doc=SAFE, hours_ago=1.0):
        self.cache.store(REPO, "main", doc, NOW - datetime.timedelta(hours=hours_ago))

    def _plant(self, body):
        with io.open(self.cache.path_for(REPO, "main"), "w", encoding="utf-8") as fh:
            fh.write(body)

    def degraded(self, **kw):
        return check_branch_protection(runner=_outage(**kw), cache=self.cache, now=lambda: NOW)


class TheFourWaysNotToCorroborateAreFourOutcomes(_Cache):

    def test_branch_protection_degraded_pass_a_cache_miss_refuses(self):
        """⭐ THE ROW. FAIL-ON-OLD: before A1 this exact outage, with nothing cached, PASSED at exit
        3 on the corroborating boolean alone."""
        v = self.degraded()
        self.assertFalse(v.ok)
        self.assertEqual(v.cache_outcome, CACHE_MISS)
        self.assertEqual(v.exit_code, EXIT_CACHE_MISS)
        self.assertIn("`protected: true` alone is not a pass", v.render())

    def test_branch_protection_degraded_pass_no_cache_at_all_is_a_miss_too(self):
        v = check_branch_protection(runner=_outage(), now=lambda: NOW)
        self.assertEqual(v.exit_code, EXIT_CACHE_MISS)
        self.assertIn("no cache was supplied", v.render())

    def test_branch_protection_degraded_pass_a_fresh_safe_hit_passes_degraded(self):
        self.seed(hours_ago=1.0)
        v = self.degraded()
        self.assertTrue(v.ok)
        self.assertTrue(v.degraded)
        self.assertEqual(v.cache_outcome, CACHE_HIT_AGREES)
        self.assertEqual(v.exit_code, EXIT_DEGRADED)
        text = v.render()
        self.assertIn("LAST SUCCESSFULLY READ DOCUMENT", text)
        self.assertIn((NOW - datetime.timedelta(hours=1)).isoformat(), text,
                      "the notice must say WHEN the document it leaned on was read")
        self.assertIn("1.0h ago", text)
        self.assertIn(RESTORE_ROW, text)

    def test_branch_protection_degraded_pass_a_hit_that_disagrees_refuses(self):
        """The last full read showed force-push ENABLED; the live boolean still says protected.
        They disagree, so neither is a pass."""
        self.seed(doc=WEAK)
        v = self.degraded()
        self.assertEqual(v.cache_outcome, CACHE_HIT_DISAGREES)
        self.assertEqual(v.exit_code, EXIT_CACHE_DISAGREES)
        self.assertIn("force-push is not disabled", v.render())
        self.assertIn("disagree", v.render())

    def test_branch_protection_degraded_pass_a_hit_too_old_refuses(self):
        self.seed(hours_ago=PROTECTION_CACHE_MAX_AGE_HOURS + 0.1)
        v = self.degraded()
        self.assertEqual(v.cache_outcome, CACHE_HIT_TOO_OLD)
        self.assertEqual(v.exit_code, EXIT_CACHE_TOO_OLD)
        self.assertIn(f"the limit is {PROTECTION_CACHE_MAX_AGE_HOURS}h", v.render())

    def test_branch_protection_degraded_pass_the_age_limit_is_inclusive_at_the_boundary(self):
        self.seed(hours_ago=PROTECTION_CACHE_MAX_AGE_HOURS)
        self.assertEqual(self.degraded().exit_code, EXIT_DEGRADED)
        self.seed(hours_ago=PROTECTION_CACHE_MAX_AGE_HOURS - 0.01)
        self.assertEqual(self.degraded().exit_code, EXIT_DEGRADED)

    def test_branch_protection_degraded_pass_an_unusable_cache_is_not_a_miss(self):
        """§7g one level down: "nothing is cached" and "something is cached and I cannot trust it"
        are different facts. Each shape gets the UNUSABLE outcome and says what is wrong."""
        path = self.cache.path_for(REPO, "main")
        good = {"repo": REPO, "branch": "main", "read_at": NOW.isoformat(), "document": SAFE}
        shapes = {
            "not json": "{nope",
            "not a record": json.dumps(["x"]),
            "another repo": json.dumps(dict(good, repo="someone/else")),
            "no timestamp": json.dumps(dict(good, read_at="yesterday-ish")),
            "from the future": json.dumps(dict(good, read_at=(NOW + datetime.timedelta(hours=2)).isoformat())),
        }
        for label, body in shapes.items():
            with self.subTest(label=label):
                with io.open(path, "w", encoding="utf-8") as fh:
                    fh.write(body)
                v = self.degraded()
                self.assertEqual(v.cache_outcome, CACHE_UNUSABLE, v.render())
                self.assertEqual(v.exit_code, EXIT_CACHE_UNUSABLE)

    def test_branch_protection_degraded_pass_four_outcomes_four_exits_four_lines(self):
        verdicts = {}
        for outcome, arrange in ((CACHE_MISS, lambda: None),
                                 (CACHE_HIT_DISAGREES, lambda: self.seed(doc=WEAK)),
                                 (CACHE_HIT_TOO_OLD, lambda: self.seed(hours_ago=48)),
                                 (CACHE_UNUSABLE, lambda: self._plant("{"))):
            shutil.rmtree(self.dir, True)
            os.makedirs(self.dir)
            arrange()
            verdicts[outcome] = self.degraded()
        exits = {o: v.exit_code for o, v in verdicts.items()}
        self.assertEqual(len(set(exits.values())), 4, exits)
        self.assertNotIn(EXIT_DEGRADED, exits.values())
        self.assertNotIn(EXIT_UNREADABLE, exits.values(),
                         "a cache refusal shares the exit of an uncorroborated live read")
        lines = {o: v.failures[-1] for o, v in verdicts.items()}
        self.assertEqual(len(set(lines.values())), 4, lines)
        for o, v in verdicts.items():
            self.assertIn(o, v.render(), "the refusal does not name its own outcome")

    def test_branch_protection_degraded_pass_no_cache_refusal_ever_suggests_a_write(self):
        for arrange in (lambda: None, lambda: self.seed(doc=WEAK), lambda: self.seed(hours_ago=48)):
            shutil.rmtree(self.dir, True)
            os.makedirs(self.dir)
            arrange()
            text = self.degraded().render()
            self.assertNotIn("PUT", text)
            self.assertIn("do NOT apply protection", text)

    def test_branch_protection_degraded_pass_a_failed_live_read_still_refuses_before_the_cache(self):
        """The cache adds to the live corroboration; it never replaces it. A HIT does not rescue a
        `protected: false`."""
        self.seed()
        v = self.degraded(branch='{"protected": false}')
        self.assertEqual(v.exit_code, EXIT_UNREADABLE)
        self.assertEqual(v.cache_outcome, "")


class TheCacheIsWrittenOnlyAfterASuccessfulFullRead(_Cache):

    def test_branch_protection_degraded_pass_a_successful_read_writes_the_document_and_its_time(self):
        v = check_branch_protection(runner=_green(), cache=self.cache, now=lambda: NOW)
        self.assertEqual(v.exit_code, EXIT_PROTECTED)
        with io.open(self.cache.path_for(REPO, "main"), encoding="utf-8") as fh:
            record = json.load(fh)
        self.assertEqual(record["document"], SAFE)
        self.assertEqual(record["read_at"], NOW.isoformat())
        self.assertEqual((record["repo"], record["branch"]), (REPO, "main"))

    def test_branch_protection_degraded_pass_an_unsafe_full_read_is_cached_too(self):
        """So a later outage corroborates against what was REALLY last seen — not an older safe
        document that a weakening has since made false."""
        self.seed(doc=SAFE, hours_ago=3)
        v = check_branch_protection(runner=_green(WEAK), cache=self.cache, now=lambda: NOW)
        self.assertEqual(v.exit_code, EXIT_NOT_PROTECTED)
        self.assertEqual(self.degraded().cache_outcome, CACHE_HIT_DISAGREES)

    def test_branch_protection_degraded_pass_a_failed_read_writes_nothing(self):
        for runner in (_outage(), _outage(branch='{"protected": false}'),
                       _routed({DETAIL: UNREADABLE_503})):
            check_branch_protection(runner=runner, cache=self.cache, now=lambda: NOW)
        self.assertEqual(os.listdir(self.dir), [], "a read that did not succeed wrote the cache")

    def test_branch_protection_degraded_pass_a_degraded_pass_does_not_refresh_the_cache(self):
        """Otherwise a cache could be kept 'fresh' forever by outages alone, and the age limit
        would bound nothing."""
        self.seed(hours_ago=20)
        def read():
            with io.open(self.cache.path_for(REPO, "main"), encoding="utf-8") as fh:
                return fh.read()
        before = read()
        self.assertEqual(self.degraded().exit_code, EXIT_DEGRADED)
        self.assertEqual(read(), before)

    def test_branch_protection_degraded_pass_a_read_saying_no_protection_clears_the_entry(self):
        """A `404 Branch not protected` is a successful read with no document. Leaving the older
        safe document behind would let a later outage corroborate against protection known gone."""
        self.seed()
        v = check_branch_protection(
            runner=_routed({DETAIL: (1, "", "Branch not protected (HTTP 404)")}),
            cache=self.cache, now=lambda: NOW)
        self.assertEqual(v.exit_code, EXIT_NOT_PROTECTED)
        self.assertEqual(self.degraded().cache_outcome, CACHE_MISS)

    def test_branch_protection_degraded_pass_an_unwritable_cache_never_breaks_the_check(self):
        blocker = os.path.join(self.dir, "file")
        with io.open(blocker, "w", encoding="utf-8"):
            pass
        cache = ProtectionCache(os.path.join(blocker, "sub"))     # a directory under a FILE
        v = check_branch_protection(runner=_green(), cache=cache, now=lambda: NOW)
        self.assertEqual(v.exit_code, EXIT_PROTECTED)
        self.assertIn("could not be written", v.cache_note)
        self.assertEqual(v.render(), check_branch_protection(runner=_green()).render(),
                         "the PASS line changed because of a cache side effect")


class TheAgeLimitAndTheRestoreRowSayWhatIsTrue(unittest.TestCase):

    def test_branch_protection_degraded_pass_the_age_limit_is_a_named_24h_constant(self):
        self.assertEqual(PROTECTION_CACHE_MAX_AGE_HOURS, 24)
        with io.open(BP.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("WHY IT IS 24 HOURS", src, "the age limit carries no justification")

    def test_branch_protection_degraded_pass_restore_row_names_the_mechanism_and_one_release(self):
        """It used to promise a restore and say nothing about how. After A1 it names the mechanism
        that replaces the boolean-only pass, and still exactly one release (B5 resolves it)."""
        self.assertIn("BRANCH-PROTECTION-DEGRADED-PASS", RESTORE_ROW)
        self.assertIn("last-read cache", RESTORE_ROW)
        self.assertIn("boolean-only pass", RESTORE_ROW)
        self.assertEqual(len(re.findall(r"\d+\.\d+\.\d+", RESTORE_ROW)), 1)
        self.assertIn(f"{PROTECTION_CACHE_MAX_AGE_HOURS}h", RESTORE_ROW)

    def test_branch_protection_degraded_pass_the_cache_lives_with_the_users_caches(self):
        old = os.environ.get("XDG_CACHE_HOME")
        try:
            os.environ["XDG_CACHE_HOME"] = "/x/y"
            self.assertEqual(os.path.normpath(default_cache_dir()).split(os.sep)[-3:],
                             ["y", "mokata", "branch-protection"])
            os.environ.pop("XDG_CACHE_HOME")
            parts = os.path.normpath(default_cache_dir()).split(os.sep)
            self.assertEqual(parts[-3:], [".cache", "mokata", "branch-protection"])
        finally:
            if old is not None:
                os.environ["XDG_CACHE_HOME"] = old


class TheCliCarriesTheCache(unittest.TestCase):
    """The command `release.sh` runs: it must WRITE on a green read, READ on an outage, and map the
    four refusals onto 4..7 — end to end, into a private XDG_CACHE_HOME."""

    def setUp(self):
        self.xdg = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.xdg, True)
        old = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = self.xdg
        self.addCleanup(lambda: os.environ.__setitem__("XDG_CACHE_HOME", old) if old is not None
                        else os.environ.pop("XDG_CACHE_HOME", None))

    def run_cli(self, runner):
        import argparse
        from mokata import degrade
        from mokata.cli_commands.core import cmd_branch_protection_check
        orig = BP._default_gh_runner
        BP._default_gh_runner = runner
        degrade.reset_degrade_notices()
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = cmd_branch_protection_check(argparse.Namespace(repo=REPO, branch="main"))
            return rc, out.getvalue(), err.getvalue()
        finally:
            BP._default_gh_runner = orig
            degrade.reset_degrade_notices()

    def test_branch_protection_degraded_pass_cli_miss_then_green_then_hit(self):
        rc, out, _ = self.run_cli(_outage())
        self.assertEqual(rc, EXIT_CACHE_MISS, out)
        rc, _out, _ = self.run_cli(_green())
        self.assertEqual(rc, EXIT_PROTECTED)
        self.assertTrue(os.listdir(os.path.join(self.xdg, "mokata", "branch-protection")))
        rc, out, err = self.run_cli(_outage())
        self.assertEqual(rc, EXIT_DEGRADED, out)
        self.assertIn("branch-protection: DEGRADED", err)

    def test_branch_protection_degraded_pass_cli_disagrees_is_five(self):
        self.run_cli(_green(WEAK))
        rc, out, _ = self.run_cli(_outage())
        self.assertEqual(rc, EXIT_CACHE_DISAGREES, out)


if __name__ == "__main__":
    unittest.main()
