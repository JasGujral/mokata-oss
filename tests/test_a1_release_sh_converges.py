"""0.0.22 stage A1 · `release-sh-converges` — a red release is retried by re-running ONE command.

Three rows, graded here (the fourth, `BRANCH-PROTECTION-DEGRADED-PASS`, is graded by
`test_a1_branch_protection_cache.py`, because its subject SHIPS and this file's does not):

  1. `RELEASE-SH-HAS-NO-RESUME` — one bad keystroke at the 0.0.21 cut cost the whole preflight, so
     the tail was finished BY HAND and `verify_release_notes`, `verify_tag_sets` and
     `wait_for_release` never ran. Every run now DERIVES what is done from git and gh, and skips the
     expensive preflights only on a REMOTE fact: a `mokata/release-preflight` commit status on the
     dev repo, naming HEAD.
  2. `CLOSED-BRANCH-GONE-REMEDY-IS-FALSE` — the close REASON is read from gh's issue events, never
     inferred from whether the branch exists; a deletion-closed PR whose branch came back gets a NEW
     PR; a reason gh cannot give refuses on its own.
  3. `SYNC-DIVERGENCE-REFUSAL-OFFERS-NO-FAST-FORWARD` — a published head that is an ANCESTOR of the
     new sync commit is fast-forwarded behind a typed "yes"; one that is not still refuses; and the
     refusal's CI wording is derived from the run's conclusion instead of always saying "green".

HOW `release.sh` IS DRIVEN, and why this is not sourcing it. Sourcing `release.sh` runs a release, so
the existing tests EXTRACT pure functions (`_release_retry.shell_function_source`). That grades the
functions and never the FLOW, and this stage's subject is the flow: which steps a re-run skips and
which it must not. So `_ReleaseHarness` runs the REAL script's whole main body against REAL git and
LOCAL bare remotes, with a fake `gh` on PATH, and overrides only the leaves that would leave the
sandbox (the three venv preflights, the cheap gates that call GitHub or Python, the CI and release
waits, and `confirm`, which reads a scripted answer file instead of the terminal). The overrides are
inserted at one line — just before the cheap gates run, where every function is already defined —
and every override records itself in a trace, so "which steps ran" is a list a test can compare.

⚠ WHAT THIS DOES NOT PROVE, declared rather than discovered: that GitHub answers as the fake does —
in particular that a branch deletion's `head_ref_deleted` event precedes or shares a timestamp with
the `closed` it causes, that `gh pr view <branch>` returns the NEWEST PR for a branch, and that a
commit status written by `gh api -X POST …/statuses/<sha>` reads back from `…/commits/<sha>/statuses`.
Those are facts about a live API, and every credentialed action stays Jas's. The first cut that runs
this code exercises them; until then they are dated obligations in the stage report.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import _release_retry as RR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")

_MISSING = [t for t in ("bash", "rsync", "git") if shutil.which(t) is None]

_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}

VER = "9.9.9"
BRANCH = f"release/{VER}"
TAG = f"v{VER}"

#: A value that must never be printed by anything this file runs. `gh` supplies its own auth and
#: nothing in either script should ever echo a token; the canary makes "never" observable.
TOKEN_CANARY = "a1-canary-gh-token-value-never-printed"

#: Where the overrides go in: the line that starts the cheap gates. Every function `release.sh`
#: defines is defined above it, so an override placed here wins; nothing has been pushed yet.
_SEAM = 'say "Cheap preflight gates — every one runs, and the cut stops ONCE with the whole list"'

#: Phrases only the NEW re-entry code prints. The fresh-run negative asserts none of them appears —
#: the "byte-identical to today's preflight path" half of the brief, checked from inside the tree.
_REENTRY_PHRASES = ("re-entry:", "ALREADY at origin", "already PASSED", "CONVERGED, not re-pushed",
                    "remote fact")

_OVERRIDES = r'''
# ---- test harness overrides (tests/test_a1_release_sh_converges.py) -------------------------------
__trace() { printf '%s\n' "$*" >> "$A1_TRACE"; }
run_test_preflight()          { __trace "PREFLIGHT run_test_preflight"; }
run_hostile_path_preflight()  { __trace "PREFLIGHT run_hostile_path_preflight"; }
run_public_subset_preflight() { __trace "PREFLIGHT run_public_subset_preflight"; }
verify_branch_protection()    { __trace "GATE verify_branch_protection"; }
verify_mirror_gate()          { __trace "GATE verify_mirror_gate"; printf 'main\n' > "$MIRROR_BRANCH_FILE"; }
verify_disclosure_resolves()  { __trace "GATE verify_disclosure_resolves"; }
verify_release_notes()        { __trace "TAIL verify_release_notes $2"; }
verify_versions()             { __trace "TAIL verify_versions $2"; }
verify_tag_sets()             { __trace "TAIL verify_tag_sets $1"; }
wait_for_ci_green()           { __trace "TAIL wait_for_ci_green $1 $2"; }
wait_for_release()            { __trace "TAIL wait_for_release $1 $2"; }
confirm() {
  local n a
  __trace "CONFIRM $1"
  n="$(cat "$A1_ANSWERS.n" 2>/dev/null || echo 0)"; n=$((n + 1)); echo "$n" > "$A1_ANSWERS.n"
  a="$(sed -n "${n}p" "$A1_ANSWERS" 2>/dev/null)"
  [ "$a" = "yes" ]
}
# ---- end of overrides ------------------------------------------------------------------------------
'''

#: A `gh` that answers from files. Every call is logged. Nothing it does reaches a network.
_FAKE_GH = r'''#!/usr/bin/env bash
printf '%s\n' "$*" >> "$A1_GH_LOG"
S="$A1_GH_STATE"
case "$1 $2" in
  "auth status") exit 0 ;;
  "pr view")     if [ -f "$S/pr" ]; then cat "$S/pr"; exit 0; fi; exit 1 ;;
  "pr create")   head="$(git ls-remote "$A1_MIRROR_BARE" "refs/heads/$A1_BRANCH" | cut -f1)"
                 n="$(cat "$S/pr.number" 2>/dev/null || echo 76)"; n=$((n + 1)); echo "$n" > "$S/pr.number"
                 printf 'OPEN\t%s\t%s\n' "$head" "$n" > "$S/pr"; echo "https://example.invalid/pull/$n"; exit 0 ;;
  "run list")    if [ -f "$S/run" ]; then cat "$S/run"; exit 0; fi; exit 1 ;;
esac
if [ "$1" = "api" ]; then
  method=GET; path=""; desc=""; state=""
  shift
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -X) method="$2"; shift ;;
      -f) case "$2" in description=*) desc="${2#description=}" ;; state=*) state="${2#state=}" ;; esac; shift ;;
      --jq|--paginate) [ "$1" = "--jq" ] && shift ;;
      *) [ -z "$path" ] && path="$1" ;;
    esac
    shift
  done
  case "$method $path" in
    "POST repos/"*"/statuses/"*)
      [ -f "$S/statuses-unwritable" ] && exit 1
      sha="${path##*/}"; printf '%s\t%s\n' "$state" "$desc" > "$S/status-$sha"; exit 0 ;;
    "GET repos/"*"/commits/"*"/statuses"*)
      [ -f "$S/statuses-unreadable" ] && exit 1
      sha="${path#*/commits/}"; sha="${sha%%/*}"
      if [ -f "$S/status-$sha" ]; then cat "$S/status-$sha"; else printf '\t\n'; fi; exit 0 ;;
    "GET repos/"*"/issues/"*"/events"*)
      [ -f "$S/events-unreadable" ] && exit 1
      cat "$S/events" 2>/dev/null; exit 0 ;;
  esac
fi
echo "fake gh: unhandled: $*" >&2
exit 1
'''


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _pure(names, text):
    out = []
    for name in names:
        body = RR.shell_function_source(text, name)
        assert body, f"{name} is not in the script"
        out.append(body)
    return "\n".join(out) + "\n"


def _call(funcs_path, expr):
    proc = subprocess.run(
        _support.bash_argv("-c", f"source '{_support.as_posix(funcs_path)}'; {expr}"),
        stdin=subprocess.DEVNULL, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


class _Git:
    """Real git, isolated from the machine's config (§7c)."""

    def tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        return os.path.realpath(d)

    @staticmethod
    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def git(self, *args, cwd, check=True):
        return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True,
                              text=True, env={**os.environ, **_GIT_ENV},
                              stdin=subprocess.DEVNULL)

    def sha(self, ref, cwd):
        return self.git("rev-parse", ref, cwd=cwd).stdout.strip()

    def bare(self, head):
        bare = self.tmp()
        self.git("init", "-q", "--bare", ".", cwd=bare)
        self.git("symbolic-ref", "HEAD", f"refs/heads/{head}", cwd=bare)
        return bare


# ====================================================================== 2 · the close reason, pure
@unittest.skipUnless(os.path.exists(RELEASE_SH) and os.path.exists(SYNC_SH),
                     "release.sh and sync-public.sh are dev-only, excluded from the public mirror")
class TheCloseReasonIsReadNotInferred(unittest.TestCase):
    """`CLOSED-BRANCH-GONE-REMEDY-IS-FALSE`. `close_reason` and `pr_state` are PURE, so they are fed
    synthetic event streams; the caller's arms are graded as what they DO (exit, or reach the one
    `gh pr create` path)."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.text = _read(RELEASE_SH)
        cls.funcs = os.path.join(cls.tmpdir, "pure.sh")
        with io.open(cls.funcs, "w", encoding="utf-8") as fh:
            fh.write(_pure(("pr_state", "close_reason"), cls.text))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def reason(self, rc, events):
        stream = "".join(f"{ev}\t{at}\n" for ev, at in events)
        return _call(self.funcs, f"close_reason '{rc}' \"$(printf '%s' '{stream}')\"")

    def state(self, *args):
        return _call(self.funcs, "pr_state " + " ".join(f"'{a}'" for a in args))

    def test_closed_branch_gone_remedy_is_false_close_reason_branch_gone_is_not_declined(self):
        """⭐ THE 0.0.21 CUT'S PR #76, REPLAYED. The branch was deleted (which closed the PR), then
        re-pushed. The old `pr_state` saw *closed + branch present* and answered CLOSED-DECLINED,
        whose remedy is "bump the version". FAIL-ON-OLD: the old function ignores the reason and
        returns CLOSED-DECLINED here."""
        why = self.reason(0, [("head_ref_deleted", "2026-10-06T10:00:00Z"),
                              ("closed", "2026-10-06T10:00:00Z")])
        self.assertEqual(why, "BRANCH-DELETED")
        self.assertEqual(self.state("CLOSED", "abc", "abc", "yes", why), "CLOSED-BRANCH-REPUSHED")
        self.assertNotEqual(self.state("CLOSED", "abc", "abc", "yes", why), "CLOSED-DECLINED")

    def test_closed_branch_gone_remedy_is_false_the_order_gh_lists_them_in_does_not_matter(self):
        """GitHub stamps the deletion and the close it causes in the same instant. Which one is
        LISTED first is not something the classification may depend on."""
        why = self.reason(0, [("closed", "2026-10-06T10:00:00Z"),
                              ("head_ref_deleted", "2026-10-06T10:00:00Z")])
        self.assertEqual(why, "BRANCH-DELETED")

    def test_closed_branch_gone_remedy_is_false_a_person_closing_it_is_a_decline(self):
        why = self.reason(0, [("closed", "2026-10-06T10:00:00Z")])
        self.assertEqual(why, "BY-ACTOR")
        self.assertEqual(self.state("CLOSED", "abc", "abc", "yes", why), "CLOSED-DECLINED")

    def test_closed_branch_gone_remedy_is_false_a_branch_deleted_AFTER_a_decline_stays_a_decline(self):
        """A person declined it and then tidied the branch away. The close came first and was
        theirs; the later deletion caused nothing."""
        why = self.reason(0, [("closed", "2026-10-06T10:00:00Z"),
                              ("head_ref_deleted", "2026-10-06T10:05:00Z")])
        self.assertEqual(why, "BY-ACTOR")
        self.assertEqual(self.state("CLOSED", "abc", "abc", "no", why), "CLOSED-DECLINED")

    def test_closed_branch_gone_remedy_is_false_a_reopen_resets_an_earlier_deletion(self):
        why = self.reason(0, [("head_ref_deleted", "T1"), ("closed", "T1"),
                              ("head_ref_restored", "T2"), ("reopened", "T3"), ("closed", "T4")])
        self.assertEqual(why, "BY-ACTOR")

    def test_closed_branch_gone_remedy_is_false_a_still_deleted_branch_is_GONE(self):
        why = self.reason(0, [("head_ref_deleted", "T1"), ("closed", "T1")])
        self.assertEqual(self.state("CLOSED", "abc", "abc", "no", why), "CLOSED-BRANCH-GONE")

    def test_closed_branch_gone_remedy_is_false_an_unreadable_reason_is_its_own_state(self):
        """§7g. 'gh could not say why' is folded into neither DECLINED nor BRANCH-GONE."""
        for rc, events in ((1, [("closed", "T1")]),      # the read failed
                           (0, []),                       # nothing came back
                           (0, [("labeled", "T1")])):     # a CLOSED PR with no close in its stream
            with self.subTest(rc=rc, events=events):
                why = self.reason(rc, events)
                self.assertEqual(why, "UNREADABLE")
                for remote in ("yes", "no"):
                    self.assertEqual(self.state("CLOSED", "abc", "abc", remote, why),
                                     "CLOSED-REASON-UNREADABLE")

    def test_closed_branch_gone_remedy_is_false_close_reason_is_pure(self):
        body = RR.shell_function_source(self.text, "close_reason")
        code = "\n".join(ln for _n, ln in RR.code_lines(body))
        for forbidden in (" gh ", "git ", "curl", "$(", "`"):
            self.assertNotIn(forbidden, code, f"close_reason reaches outside its arguments: {forbidden!r}")

    def test_closed_branch_gone_remedy_is_false_the_reason_is_ASKED_of_gh(self):
        body = RR.shell_function_source(self.text, "pr_close_events")
        self.assertRegex(body, r"gh api \"repos/\$\{PUB_REPO\}/issues/\$1/events")
        self.assertIn("head_ref_deleted", RR.shell_function_source(self.text, "close_reason"))
        facts = RR.shell_function_source(self.text, "pr_facts")
        self.assertIn("--json state,headRefOid,number", facts,
                      "the PR number must come from the SAME read as the state and the head")

    def test_closed_branch_gone_remedy_is_false_each_arm_does_what_its_fact_needs(self):
        arms = RR.case_arms(self.text, "PR_STATE")
        self.assertTrue(arms["CLOSED-DECLINED"])
        self.assertTrue(arms["CLOSED-BRANCH-GONE"])
        self.assertTrue(arms["CLOSED-REASON-UNREADABLE"], "an unreadable close reason must refuse")
        self.assertFalse(arms["CLOSED-BRANCH-REPUSHED"],
                         "a re-pushed branch is offered a NEW PR, so its arm does not exit itself")
        start = self.text.index("    CLOSED-BRANCH-REPUSHED)")
        arm = self.text[start:self.text.index(";;", start)]
        self.assertIn("open_release_pr", arm)
        self.assertIn("NEW", arm)
        unread = self.text[self.text.index("    CLOSED-REASON-UNREADABLE)"):]
        unread = unread[:unread.index("exit 1")]
        self.assertIn("Do NOT bump the version", unread)

    def test_closed_branch_gone_remedy_is_false_one_create_path_behind_one_confirm(self):
        creates = [n for n, ln in RR.code_lines(self.text)
                   if re.search(r"gh pr create\b", ln.split("#")[0])
                   and not ln.strip().startswith("echo")]
        self.assertEqual(1, len(creates), "expected ONE live `gh pr create`: %r" % creates)
        body = self.text[self.text.index("  open_release_pr() {"):]
        body = body[:body.index("\n  }\n")]
        self.assertLess(body.index('confirm "$1"'), body.index('&& gh pr create'),
                        "the NEW-PR offer must be confirm-gated, like the first one")
        self.assertIn('open_release_pr "Open the release PR now?"', self.text)

    def test_closed_branch_gone_remedy_is_false_the_false_sentence_is_gone(self):
        self.assertNotIn("which re-opens the PR", self.text)
        for _n, line in RR.code_lines(self.text):
            self.assertNotIn("re-opens the PR", line)


# ============================================================== 1 · re-entry, driven end to end
class _ReleaseHarness(_Git):
    """A synthetic dev repo carrying an INSTRUMENTED copy of the real `release.sh`, a mirror
    checkout, two local bare remotes and a fake `gh`. See the module docstring.

    ⚠ It names neither script constant (the `test_b2` lesson): the path arrives as `release_script`
    / `sync_script`, set by the guarded concrete class below."""

    release_script = None
    sync_script = None

    def build(self):
        root = self.tmp()
        self.dev_bare = self.bare("master")
        self.mirror_bare = self.bare("main")
        self.dev = os.path.join(root, "dev")
        self.mirror = os.path.join(root, "mirror")
        self.state = os.path.join(root, "gh-state")
        self.bin = os.path.join(root, "bin")
        self.trace = os.path.join(root, "trace")
        self.gh_log = os.path.join(root, "gh.log")
        self.answers = os.path.join(root, "answers")
        os.makedirs(self.state)
        os.makedirs(self.bin)
        self.write(os.path.join(self.bin, "gh"), _FAKE_GH)
        os.chmod(os.path.join(self.bin, "gh"), 0o755)

        # --- the dev repo: pushed once at a PREVIOUS commit, so a fresh cut has work to do
        os.makedirs(self.dev)
        text = _read(self.release_script)
        assert text.count(_SEAM) == 1, "the harness seam moved; update _SEAM"
        self.write(os.path.join(self.dev, "scripts", "release.sh"),
                   text.replace(_SEAM, _OVERRIDES + _SEAM))
        os.chmod(os.path.join(self.dev, "scripts", "release.sh"), 0o755)
        _support.install_release_scripts(ROOT, os.path.join(self.dev, "scripts"))
        self.write(os.path.join(self.dev, "pyproject.toml"), f'version = "{VER}"\n')
        self.write(os.path.join(self.dev, "README.md"), "shippable\n")
        self.write(os.path.join(self.dev, "RELEASE_NOTES.md"), f"# mokata {VER}\n")
        self.write(os.path.join(self.dev, "src", "pkg", "mod.py"), "def widget():\n    return 0\n")
        self.git("init", "-q", cwd=self.dev)
        self.git("symbolic-ref", "HEAD", "refs/heads/master", cwd=self.dev)
        self.git("add", "-A", cwd=self.dev)
        self.git("commit", "-qm", "previous", cwd=self.dev)
        self.git("remote", "add", "origin", self.dev_bare, cwd=self.dev)
        self.git("push", "-q", "origin", "master", cwd=self.dev)
        self.write(os.path.join(self.dev, "src", "pkg", "mod.py"), "def widget():\n    return 1\n")
        self.git("commit", "-qam", f"Bump to {VER}", cwd=self.dev)
        self.head = self.sha("HEAD", cwd=self.dev)

        # --- the mirror checkout, on main, with an origin
        os.makedirs(self.mirror)
        self.write(os.path.join(self.mirror, "seed.txt"), "seed\n")
        self.git("init", "-q", cwd=self.mirror)
        self.git("add", "-A", cwd=self.mirror)
        self.git("commit", "-qm", "seed", cwd=self.mirror)
        self.git("branch", "-M", "main", cwd=self.mirror)
        self.git("remote", "add", "origin", self.mirror_bare, cwd=self.mirror)
        self.git("push", "-q", "-u", "origin", "main", cwd=self.mirror)
        self.git("remote", "set-head", "origin", "-a", cwd=self.mirror)

    # ---- arranging the remotes ------------------------------------------------------------
    def push_master(self):
        self.git("push", "-q", "origin", "master", cwd=self.dev)

    def record(self, sha, state="success", names=None):
        self.write(os.path.join(self.state, f"status-{sha}"),
                   f"{state}\tpreflight passed at {names or sha}\n")

    def publish_and_merge(self):
        """The mirror as a run that died AFTER the merge leaves it: the release content is on
        main, and `gh pr merge --delete-branch` removed the branch."""
        proc = subprocess.run(
            _support.bash_argv(_support.as_posix(os.path.join(self.dev, "scripts", "sync-public.sh")),
                               _support.as_posix(self.mirror)),
            cwd=self.dev, capture_output=True, text=True, stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, **_GIT_ENV, "MOKATA_MIRROR_DEFAULT_BRANCH": "main",
                 "PATH": self.bin + os.pathsep + os.environ.get("PATH", "")})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.git("checkout", "-q", "main", cwd=self.mirror)
        self.git("merge", "-q", "--ff-only", BRANCH, cwd=self.mirror)
        self.git("push", "-q", "origin", "main", cwd=self.mirror)
        self.git("branch", "-q", "-D", BRANCH, cwd=self.mirror)
        self.write(os.path.join(self.state, "pr"), f"MERGED\t{self.sha('HEAD', cwd=self.mirror)}\t77\n")

    def tag_both(self):
        for repo in (self.dev, self.mirror):
            self.git("tag", "-a", TAG, "-m", f"mokata {VER}", cwd=repo)
            self.git("push", "-q", "origin", TAG, cwd=repo)

    # ---- running it -----------------------------------------------------------------------
    def run_release(self, answers=()):
        self.write(self.answers, "".join(f"{a}\n" for a in answers))
        for f in (self.trace, self.gh_log, self.answers + ".n"):
            if os.path.exists(f):
                os.remove(f)
        env = {**os.environ, **_GIT_ENV,
               "PATH": self.bin + os.pathsep + os.environ.get("PATH", ""),
               "PYTHON": sys.executable, "GH_TOKEN": TOKEN_CANARY,
               "A1_TRACE": self.trace, "A1_ANSWERS": self.answers, "A1_GH_STATE": self.state,
               "A1_GH_LOG": self.gh_log, "A1_MIRROR_BARE": self.mirror_bare, "A1_BRANCH": BRANCH}
        env.pop("MOKATA_MIRROR_DEFAULT_BRANCH", None)
        proc = subprocess.run(
            _support.bash_argv("scripts/release.sh", _support.as_posix(self.mirror)),
            cwd=self.dev, capture_output=True, text=True, stdin=subprocess.DEVNULL,
            start_new_session=True, env=env, timeout=300)
        self.assertNotIn(TOKEN_CANARY, proc.stdout + proc.stderr, "a token reached the output")
        return proc

    def traced(self, prefix=""):
        if not os.path.exists(self.trace):
            return []
        return [ln for ln in _read(self.trace).splitlines() if ln.startswith(prefix)]

    def gh_calls(self):
        return _read(self.gh_log).splitlines() if os.path.exists(self.gh_log) else []


_PREFLIGHTS = ["PREFLIGHT run_test_preflight", "PREFLIGHT run_hostile_path_preflight",
               "PREFLIGHT run_public_subset_preflight"]
_GATES = ["GATE verify_branch_protection", "GATE verify_mirror_gate",
          "GATE verify_disclosure_resolves"]


@unittest.skipUnless(os.path.exists(RELEASE_SH) and os.path.exists(SYNC_SH),
                     "release.sh and sync-public.sh are dev-only, excluded from the public mirror")
@unittest.skipIf(_MISSING, f"needs {', '.join(_MISSING)} on PATH")
class ReEntryIsDerivedFromTheRemotes(_ReleaseHarness, unittest.TestCase):
    release_script, sync_script = RELEASE_SH, SYNC_SH

    def setUp(self):
        self.build()

    def test_release_sh_has_no_resume_a_fresh_run_takes_todays_preflight_path(self):
        """NEGATIVE. Nothing on the remote proves anything: master at origin is the PREVIOUS
        commit. The three preflights run, in order, the record is never even asked for, and the
        run prints none of the new re-entry lines — so this path is the one that existed before."""
        proc = self.run_release(answers=("no",))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.traced("PREFLIGHT"), _PREFLIGHTS)
        self.assertEqual(self.traced("CONFIRM"), ["CONFIRM Push master?"])
        self.assertTrue(proc.stdout.rstrip().endswith("stopped."), proc.stdout[-400:])
        self.assertFalse([c for c in self.gh_calls() if "statuses" in c],
                         "a fresh run asked for a preflight record it cannot have")
        for phrase in _REENTRY_PHRASES:
            self.assertNotIn(phrase, proc.stdout + proc.stderr)

    def test_release_sh_has_no_resume_a_preflighted_head_skips_the_expensive_preflights(self):
        """⭐ THE ROW. A previous run passed the preflight at HEAD and pushed master; the next run
        derives both from the remotes and skips the three expensive legs — and the confirm the 0.0.21
        cut died on is not asked again. FAIL-ON-OLD: the old script runs all three every time."""
        self.push_master()
        self.record(self.head)
        proc = self.run_release(answers=("no",))
        self.assertEqual(self.traced("PREFLIGHT"), [], proc.stdout)
        self.assertIn("remote fact: commit status 'mokata/release-preflight'", proc.stdout)
        self.assertIn(self.head, proc.stdout, "the skip must name the sha it was proven at")
        self.assertNotIn("CONFIRM Push master?", self.traced("CONFIRM"))
        self.assertIn("master is ALREADY at origin", proc.stdout)
        self.assertFalse([c for c in self.gh_calls() if c.startswith("api -X POST")],
                         "a run that did NOT run the preflight wrote a record saying it passed")

    def test_release_sh_has_no_resume_the_cheap_gates_rerun_every_time(self):
        self.push_master()
        self.record(self.head)
        self.run_release(answers=("no",))
        self.assertEqual(self.traced("GATE"), _GATES)

    def test_release_sh_has_no_resume_a_HEAD_other_than_the_preflighted_sha_runs_preflight(self):
        """NEGATIVE. The record sits on the PREVIOUS commit; HEAD moved on and was pushed. Nothing
        proves THIS sha, so the preflight runs."""
        previous = self.sha("HEAD~1", cwd=self.dev)
        self.record(previous)
        self.push_master()
        proc = self.run_release(answers=("no",))
        self.assertEqual(self.traced("PREFLIGHT"), _PREFLIGHTS)
        self.assertIn("records no 'mokata/release-preflight' status on it", proc.stdout)

    def test_release_sh_has_no_resume_a_record_naming_another_sha_runs_preflight(self):
        """The comparison is explicit, not left to the URL: a success whose description names a
        different sha than HEAD proves nothing about HEAD."""
        self.push_master()
        self.record(self.head, names="0" * 40)
        proc = self.run_release(answers=("no",))
        self.assertEqual(self.traced("PREFLIGHT"), _PREFLIGHTS)
        self.assertIn("does not name HEAD", proc.stdout)

    def test_release_sh_has_no_resume_a_record_that_is_not_success_runs_preflight(self):
        self.push_master()
        self.record(self.head, state="failure")
        proc = self.run_release(answers=("no",))
        self.assertEqual(self.traced("PREFLIGHT"), _PREFLIGHTS)
        self.assertIn("is not 'success'", proc.stdout)

    def test_release_sh_has_no_resume_an_unreadable_record_runs_preflight(self):
        """§7g. "could not ask" and "nothing recorded" both run the preflight — the safe way — and
        they still say different things."""
        self.push_master()
        self.record(self.head)
        self.write(os.path.join(self.state, "statuses-unreadable"), "")
        proc = self.run_release(answers=("no",))
        self.assertEqual(self.traced("PREFLIGHT"), _PREFLIGHTS)
        self.assertIn("could not be read", proc.stdout)

    def test_release_sh_has_no_resume_the_record_is_written_only_after_the_preflight_and_the_push(self):
        """The record is what a later run will trust, so it is written at the last moment it is
        true: every preflight passed at HEAD, and HEAD is on origin."""
        proc = self.run_release(answers=("yes", "no"))
        self.assertEqual(self.traced("PREFLIGHT"), _PREFLIGHTS, proc.stdout + proc.stderr)
        posts = [c for c in self.gh_calls() if c.startswith("api -X POST")]
        self.assertEqual(1, len(posts), self.gh_calls())
        self.assertIn(f"statuses/{self.head}", posts[0])
        self.assertIn(f"description=preflight passed at {self.head}", posts[0])
        self.assertEqual(self.sha("master", cwd=self.dev_bare), self.head)
        # and a re-run now derives it
        proc = self.run_release(answers=("no",))
        self.assertEqual(self.traced("PREFLIGHT"), [], proc.stdout)

    def test_release_sh_has_no_resume_a_failed_record_write_is_loud_and_not_fatal(self):
        self.write(os.path.join(self.state, "statuses-unwritable"), "")
        proc = self.run_release(answers=("yes", "no"))
        self.assertIn("could not record the preflight", proc.stderr)
        self.assertIn("CONFIRM Release branch", " ".join(self.traced("CONFIRM")),
                      "a failed record write stopped the cut")

    def test_release_sh_has_no_resume_a_finished_release_reruns_to_convergence_asking_nothing(self):
        """⭐ P22's answer as an assertion. Master pushed and preflighted, the PR merged, both tags
        published: re-running the ONE command converges — no confirm is asked, nothing is pushed,
        and EVERY step of the tail the 0.0.21 hand-finish skipped runs. FAIL-ON-OLD: the old script
        re-runs all three preflights and stops at the "Push master?" confirm."""
        self.push_master()
        self.record(self.head)
        self.publish_and_merge()
        self.tag_both()
        before = (self.sha("master", cwd=self.dev_bare), self.sha("main", cwd=self.mirror_bare))
        proc = self.run_release(answers=())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.traced("CONFIRM"), [], "a converged re-run asked a question")
        self.assertEqual(self.traced("PREFLIGHT"), [])
        self.assertEqual(self.traced("GATE"), _GATES)
        tail = self.traced("TAIL")
        for step in ("TAIL verify_versions the dev checkout (HEAD)",
                     "TAIL verify_versions the public mirror (merged main)",
                     "TAIL verify_release_notes the public mirror (merged main)",
                     "TAIL wait_for_ci_green JasGujral/mokata-oss",
                     "TAIL verify_tag_sets this cut's tail, immediately after tagging",
                     "TAIL wait_for_release JasGujral/mokata-oss v9.9.9"):
            self.assertTrue(any(t.startswith(step) for t in tail), f"{step!r} did not run: {tail}")
        self.assertIn("ALREADY ON JasGujral/mokata-oss@main", proc.stdout)
        self.assertIn("ALREADY created and published on BOTH repositories", proc.stdout)
        self.assertIn("Done — mokata 9.9.9 released", proc.stdout)
        self.assertEqual(before, (self.sha("master", cwd=self.dev_bare),
                                  self.sha("main", cwd=self.mirror_bare)),
                         "a converged re-run moved a remote")

    def test_release_sh_has_no_resume_a_tag_published_on_one_side_only_is_still_asked_for(self):
        """NEGATIVE, at the narrowest gap: three of the four tags in place, the mirror's tag
        created locally but never pushed. That is NOT converged, so the confirm is asked."""
        self.push_master()
        self.record(self.head)
        self.publish_and_merge()
        self.git("tag", "-a", TAG, "-m", f"mokata {VER}", cwd=self.dev)
        self.git("push", "-q", "origin", TAG, cwd=self.dev)
        self.git("tag", "-a", TAG, "-m", f"mokata {VER}", cwd=self.mirror)
        proc = self.run_release(answers=("no",))
        self.assertEqual(self.traced("CONFIRM"), [f"CONFIRM Create + push {TAG} on BOTH repos now?"],
                         proc.stdout)

    def test_release_sh_has_no_resume_a_missing_tag_is_still_asked_for(self):
        """NEGATIVE for the tag skip: one tag absent is NOT a converged step, so the confirm is
        asked and a 'no' stops before anything is tagged."""
        self.push_master()
        self.record(self.head)
        self.publish_and_merge()
        proc = self.run_release(answers=("no",))
        self.assertEqual(self.traced("CONFIRM"), [f"CONFIRM Create + push {TAG} on BOTH repos now?"],
                         proc.stdout)
        self.assertIn("NOT tagged", proc.stdout)


@unittest.skipUnless(os.path.exists(RELEASE_SH) and os.path.exists(SYNC_SH),
                     "release.sh and sync-public.sh are dev-only, excluded from the public mirror")
class ThereIsNoWayToTalkPastThePreflight(unittest.TestCase):
    """The brief, verbatim: no `--skip-preflight` flag and no environment override."""

    def setUp(self):
        self.text = _read(RELEASE_SH)
        self.code = "\n".join(ln for _n, ln in RR.code_lines(self.text))

    def test_release_sh_has_no_resume_no_skip_flag_and_no_override_variable(self):
        self.assertNotIn("--skip-preflight", self.code)
        self.assertFalse(re.findall(r"\$\{?[A-Z_]*SKIP[A-Z_]*", self.code),
                         "a SKIP variable is read by live code")
        # The only positional argument is the mirror checkout.
        self.assertEqual(sorted(set(re.findall(r"\$\{?([1-9])", self.code.split("\nconfirm()")[0]))),
                         ["1"])

    def test_release_sh_has_no_resume_the_skip_rests_on_a_remote_read_and_nothing_local(self):
        body = RR.shell_function_source(self.text, "read_preflight_record")
        self.assertIn('gh api "repos/${DEV_REPO}/commits/$1/statuses', body)
        for local in ("cat ", "< ", "[ -f", "mktemp", ".mokata"):
            self.assertNotIn(local, "\n".join(ln for _n, ln in RR.code_lines(body)))

    def test_release_sh_has_no_resume_the_record_state_is_pure_and_total(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "f.sh")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(_pure(("preflight_record_state", "remote_head_state"), self.text))
        for args, want in ((("0", "success", "preflight passed at abc", "abc"), "PROVEN"),
                           (("0", "", "", "abc"), "ABSENT"),
                           (("0", "pending", "preflight passed at abc", "abc"), "NOT-SUCCESS"),
                           (("0", "success", "preflight passed at def", "abc"), "OTHER-SHA"),
                           (("0", "success", "preflight passed at ", ""), "OTHER-SHA"),
                           (("1", "success", "preflight passed at abc", "abc"), "UNREADABLE")):
            with self.subTest(args=args):
                self.assertEqual(_call(path, "preflight_record_state " +
                                       " ".join(f"'{a}'" for a in args)), want)
        for args, want in ((("0", "abc", "abc"), "AT-HEAD"), (("0", "def", "abc"), "ELSEWHERE"),
                           (("2", "", "abc"), "ABSENT"), (("128", "", "abc"), "UNREADABLE"),
                           (("0", "", "abc"), "UNREADABLE")):
            with self.subTest(args=args):
                self.assertEqual(_call(path, "remote_head_state " +
                                       " ".join(f"'{a}'" for a in args)), want)

    def test_release_sh_has_no_resume_the_tail_is_unconditional(self):
        """The tail steps are top-level calls after the merge block, never inside a re-entry arm:
        a re-run may skip what is DONE, never what GRADES what was done."""
        lines = self.text.splitlines()
        for call in ('verify_versions "$PUB_CHECKOUT"', 'verify_release_notes "$PUB_CHECKOUT"',
                     'verify_tag_sets "this cut', 'wait_for_release "$PUB_REPO"'):
            hits = [ln for ln in lines if ln.startswith(call)]
            self.assertEqual(1, len(hits), f"{call!r} is not an unconditional top-level call")


# ==================================================== 3 · the sync fast-forwards, behind a "yes"
def _pty_run(argv, cwd, env, answer):
    """Run `argv` with a CONTROLLING TERMINAL that has `answer` typed into it, the way an operator
    runs the sync. Returns (rc, combined output). `confirm` reads /dev/tty, so this is the only
    honest way to say "yes" to it from a test."""
    import pty
    import select
    pid, fd = pty.fork()
    if pid == 0:                                          # child: has the pty as its tty
        try:
            os.chdir(cwd)
            os.execvpe(argv[0], argv, env)
        finally:
            os._exit(127)
    os.write(fd, (answer + "\n").encode())
    chunks = []
    while True:
        try:
            ready, _w, _x = select.select([fd], [], [], 120)
            if not ready:
                break
            data = os.read(fd, 4096)
        except OSError:
            break
        if not data:
            break
        chunks.append(data)
    _pid, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), b"".join(chunks).decode("utf-8", "replace")


_HAVE_PTY = os.name == "posix" and hasattr(os, "waitstatus_to_exitcode")


class _SyncHarness(_Git):
    sync_script = None

    def src(self, body):
        src = self.tmp()
        _support.install_release_scripts(ROOT, os.path.join(src, "scripts"))
        self.write(os.path.join(src, "pyproject.toml"), f'version = "{VER}"\n')
        self.write(os.path.join(src, "src", "pkg", "mod.py"), body)
        return src

    def dest(self):
        bare = self.bare("main")
        dest = self.tmp()
        self.write(os.path.join(dest, "seed.txt"), "seed\n")
        self.git("init", "-q", cwd=dest)
        self.git("add", "-A", cwd=dest)
        self.git("commit", "-qm", "seed", cwd=dest)
        self.git("branch", "-M", "main", cwd=dest)
        self.git("remote", "add", "origin", bare, cwd=dest)
        self.git("push", "-q", "-u", "origin", "main", cwd=dest)
        self.git("remote", "set-head", "origin", "-a", cwd=dest)
        return dest, bare

    def env(self, run=None):
        fake = self.tmp()
        self.write(os.path.join(fake, "gh"), _FAKE_GH)
        os.chmod(os.path.join(fake, "gh"), 0o755)
        state = self.tmp()
        if run is not None:
            self.write(os.path.join(state, "run"), run)
        return {**os.environ, **_GIT_ENV, "MOKATA_MIRROR_DEFAULT_BRANCH": "main",
                "PATH": fake + os.pathsep + os.environ.get("PATH", ""),
                "A1_GH_STATE": state, "A1_GH_LOG": os.path.join(state, "log"),
                "GH_TOKEN": TOKEN_CANARY}

    def sync(self, src, dest, env, expect_rc=0):
        proc = subprocess.run(
            _support.bash_argv(_support.as_posix(os.path.join(src, "scripts", "sync-public.sh")),
                               _support.as_posix(dest)),
            capture_output=True, text=True, stdin=subprocess.DEVNULL, start_new_session=True,
            env=env)
        self.assertEqual(proc.returncode, expect_rc, proc.stdout + proc.stderr)
        self.assertNotIn(TOKEN_CANARY, proc.stdout + proc.stderr)
        return proc

    def published(self, run=None):
        """A release branch pushed once — the state after the first CI run went RED."""
        src, (dest, bare) = self.src("def widget():\n    return 1\n"), self.dest()
        env = self.env(run)
        self.sync(src, dest, env)
        self.git("push", "-q", "-u", "origin", BRANCH, cwd=dest)
        head = self.sha(BRANCH, cwd=dest)
        # the fix commit: a different LENGTH, so rsync's size+mtime quick check sees it
        self.write(os.path.join(src, "src", "pkg", "mod.py"),
                   "def widget():\n    return 2   # the fix for the red run\n")
        return src, dest, bare, env, head

    def remote_head(self, bare):
        return self.sha(f"refs/heads/{BRANCH}", cwd=bare)


@unittest.skipUnless(os.path.exists(RELEASE_SH) and os.path.exists(SYNC_SH),
                     "release.sh and sync-public.sh are dev-only, excluded from the public mirror")
@unittest.skipIf(_MISSING, f"needs {', '.join(_MISSING)} on PATH")
class TheSyncFastForwardsBehindAYes(_SyncHarness, unittest.TestCase):
    sync_script = SYNC_SH

    @unittest.skipUnless(_HAVE_PTY, "needs a POSIX pty to type 'yes' at the terminal")
    def test_sync_divergence_refusal_offers_no_fast_forward_an_ancestor_head_fast_forwards(self):
        """⭐ THE ROW. After a red CI run the fix makes the sync produce a different tree. The
        published head is an ANCESTOR of the commit the sync makes, so a typed "yes" fast-forwards
        the branch: the PR keeps its number, no branch is deleted, no force. FAIL-ON-OLD: the old
        sync refused here with exit 1 and offered only a branch delete."""
        src, dest, bare, env, published = self.published(
            run="completed\tfailure\n")
        rc, out = _pty_run(_support.bash_argv(
            _support.as_posix(os.path.join(src, "scripts", "sync-public.sh")),
            _support.as_posix(dest)), cwd=dest, env=env, answer="yes")
        self.assertEqual(rc, 0, out)
        new = self.remote_head(bare)
        self.assertNotEqual(new, published, out)
        self.assertEqual(self.sha(f"{new}^", cwd=dest), published,
                         "the new head is not a child of the published one — not a fast-forward")
        self.assertIn("FAST-FORWARDED", out)
        self.assertIn("that CI graded RED", out, "the prompt must say what CI said about the old head")
        self.assertNotIn(TOKEN_CANARY, out)

    def test_sync_divergence_refusal_offers_no_fast_forward_unconfirmed_touches_nothing(self):
        """No terminal, no "yes": the confirm fails CLOSED and nothing is committed or pushed."""
        src, dest, bare, env, published = self.published()
        before = self.sha("HEAD", cwd=dest)
        proc = self.sync(src, dest, env, expect_rc=1)
        self.assertIn("was not confirmed", proc.stderr)
        self.assertEqual(self.remote_head(bare), published)
        self.assertEqual(self.sha("HEAD", cwd=dest), before, "a commit was made without a yes")
        self.assertNotIn("--delete", proc.stderr,
                         "the old remedy — delete the branch — closed PR #76; it must not be offered")

    def test_sync_divergence_refusal_offers_no_fast_forward_a_non_ancestor_still_refuses(self):
        """NEGATIVE. Another writer moves the branch AFTER this sync has read it — planted with a
        `post-checkout` hook, which fires between the sync's fetch and its decision. The live head
        is not an ancestor of what the sync built on, so it refuses, and the other writer's commit
        is left exactly where it is."""
        src, dest, bare, env, published = self.published()
        other = self.tmp()
        self.git("clone", "-q", bare, other, cwd=self.tmp())
        self.git("checkout", "-q", BRANCH, cwd=other)
        self.write(os.path.join(other, "intruder.txt"), "someone else\n")
        self.git("add", "-A", cwd=other)
        self.git("commit", "-qm", "a concurrent writer", cwd=other)
        intruder = self.sha("HEAD", cwd=other)
        hook = os.path.join(dest, ".git", "hooks", "post-checkout")
        marker = os.path.join(self.tmp(), "fired")
        self.write(hook, "#!/bin/sh\n"
                         f"[ -e '{marker}' ] && exit 0\n: > '{marker}'\n"
                         f"cd '{other}' && git push -q origin {BRANCH}\n")
        os.chmod(hook, 0o755)
        proc = self.sync(src, dest, env, expect_rc=1)
        self.assertTrue(os.path.exists(marker), "the concurrent writer never ran — fixture broken")
        self.assertIn("NOT an", proc.stderr)
        self.assertIn("ancestor", proc.stderr)
        self.assertEqual(self.remote_head(bare), intruder, "the other writer's commit was replaced")
        self.assertNotIn("FAST-FORWARDED", proc.stdout)

    def test_sync_divergence_refusal_offers_no_fast_forward_an_unreadable_live_head_refuses(self):
        """§7g: "the remote moved" and "the remote could not be asked" are different refusals. The
        origin is made unreachable AFTER the sync's fetch (the same post-checkout seam)."""
        src, dest, bare, env, published = self.published()
        hook = os.path.join(dest, ".git", "hooks", "post-checkout")
        self.write(hook, "#!/bin/sh\ngit remote set-url origin /nonexistent/a1-gone\n")
        os.chmod(hook, 0o755)
        proc = self.sync(src, dest, env, expect_rc=1)
        self.assertIn("could not be read", proc.stderr)
        self.assertNotIn("ancestor of what this sync built on", proc.stderr)
        self.assertEqual(self.remote_head(bare), published)

    def test_sync_divergence_refusal_offers_no_fast_forward_ci_wording_is_derived(self):
        """⛔ The refusal said the head was one "that CI graded green" when CI had graded it RED.
        Each conclusion now gets its own words, and unknown says unknown. FAIL-ON-OLD: the old text
        is the same sentence whatever CI said."""
        for run, want in (("completed\tsuccess\n", "that CI graded GREEN"),
                          ("completed\tfailure\n", "that CI graded RED (failure)"),
                          ("in_progress\t\n", "UNKNOWN (its run is still 'in_progress')"),
                          ("\t\n", "UNKNOWN (no CI run is registered for it)"),
                          (None, "UNKNOWN (gh could not be asked)")):
            with self.subTest(run=run):
                src, dest, _bare, env, _p = self.published(run=run)
                proc = self.sync(src, dest, env, expect_rc=1)
                self.assertIn(want, proc.stderr)
                if "GREEN" not in want:
                    self.assertNotIn("graded GREEN", proc.stderr)
                    self.assertNotIn("graded green", proc.stderr)

    def test_sync_divergence_refusal_offers_no_fast_forward_the_false_text_is_gone(self):
        text = _read(SYNC_SH)
        code = "\n".join(ln for _n, ln in RR.code_lines(text))
        self.assertNotIn("that CI graded green", code)
        self.assertNotIn("push origin --delete", code)
        self.assertEqual(RR.no_force_pushes(text), [])

    def test_sync_divergence_refusal_offers_no_fast_forward_the_header_argues_ONE_design(self):
        """⚠ The brief: this reverses `sync-public.sh`'s recorded design, so the header must not
        keep both rationales. The old sentence is gone and the withdrawal says why."""
        text = _read(SYNC_SH)
        self.assertNotIn("The third state refuses rather than fast-forwarding on purpose", text)
        self.assertIn("THAT ARGUMENT IS WITHDRAWN", text)
        self.assertIn("ANCESTOR", text)

    def test_sync_divergence_refusal_offers_no_fast_forward_the_PR_arm_accepts_only_that_head(self):
        """The brief asks that `release.sh`'s OPEN-ELSEWHERE arm accept a head this run just
        fast-forwarded to, and only that head. ⚠ Grounding found that head ALREADY reads
        OPEN-AT-THIS-COMMIT: `WANT_SHA` is read AFTER the sync, so a fast-forwarded branch IS the
        commit this run publishes. An extra clause in OPEN-ELSEWHERE would be unreachable (§7f).
        The property is therefore pinned on the pure function both arms are keyed by."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "f.sh")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(_pure(("pr_state",), _read(RELEASE_SH)))
        new, old = "b" * 40, "a" * 40
        self.assertEqual(_call(path, f"pr_state OPEN {new} {new} yes ''"), "OPEN-AT-THIS-COMMIT")
        self.assertEqual(_call(path, f"pr_state OPEN {old} {new} yes ''"), "OPEN-ELSEWHERE")
        text = _read(RELEASE_SH)
        self.assertLess(text.index('scripts/sync-public.sh "$PUB_CHECKOUT"'),
                        text.index('WANT_SHA="$( cd "$PUB_CHECKOUT" && git rev-parse "$BRANCH" )"'),
                        "WANT_SHA must be read after the sync, or a fast-forward reads as ELSEWHERE")

    def test_sync_divergence_refusal_offers_no_fast_forward_ci_wording_is_pure(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "f.sh")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(_pure(("ci_wording",), _read(SYNC_SH)))
        for args, want in ((("0", "completed", "success"), "that CI graded GREEN"),
                           (("0", "completed", "timed_out"), "that CI graded RED (timed_out)"),
                           (("0", "completed", "cancelled"), "UNKNOWN (it concluded 'cancelled')"),
                           (("1", "", ""), "UNKNOWN (gh could not be asked)")):
            with self.subTest(args=args):
                self.assertIn(want, _call(path, "ci_wording " + " ".join(f"'{a}'" for a in args)))


# ====================================== 4 · release.sh's side of BRANCH-PROTECTION-DEGRADED-PASS
@unittest.skipUnless(os.path.exists(RELEASE_SH) and os.path.exists(SYNC_SH),
                     "release.sh and sync-public.sh are dev-only, excluded from the public mirror")
class ReleaseShReadsTheEightProtectionStates(unittest.TestCase):

    def setUp(self):
        self.body = RR.shell_function_source(_read(RELEASE_SH), "verify_branch_protection")

    def test_branch_protection_degraded_pass_every_cache_refusal_has_its_own_arm_and_exits(self):
        arms = RR.case_arms(self.body, "rc")
        self.assertEqual(set(arms), {"0", "1", "2", "3", "4", "5", "6", "7", "*"})
        self.assertFalse(arms["0"])
        self.assertFalse(arms["3"])
        for code in ("1", "2", "4", "5", "6", "7", "*"):
            self.assertTrue(arms[code], f"arm {code} does not refuse")

    def test_branch_protection_degraded_pass_the_four_cache_arms_say_four_different_things(self):
        code = "\n".join(ln for _n, ln in RR.code_lines(self.body))
        for name in ("CACHE MISS", "CACHE DISAGREES", "CACHE TOO OLD", "CACHE UNUSABLE"):
            self.assertEqual(1, code.count(name), name)
        self.assertNotIn("PUT", code)
        self.assertEqual(1, code.count("Apply protection (admin)"))

    def test_branch_protection_degraded_pass_the_stale_banner_is_gone(self):
        self.assertNotIn("0.0.19", self.body)
        self.assertNotIn("corroborating boolean alone", self.body)
        self.assertIn("24h", self.body)


if __name__ == "__main__":
    unittest.main()
