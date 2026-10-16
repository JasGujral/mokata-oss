"""0.0.23 stage A1 · `release-waits-are-three-state` — a wait reports PENDING and UNREADABLE as such.

Two rows, both hit live at the 0.0.22 cut (doc 84; `handoff/0022-CUT-CHECKLIST.md` steps 5 and 7):

  1. `CI-WAIT-READS-A-NETWORK-RESET-AS-RED` — `wait_for_ci_green` took `gh run watch --exit-status`'s
     exit as the verdict, so `connection reset by peer` was printed as *"run 38511809434 did NOT
     conclude success"*. The run was green, 13/13. Now a non-zero watch is followed by a READ of the
     run's conclusion; a failed read is UNREADABLE, retried with bounded backoff, and never red.
  2. `RELEASE-WAIT-TIMES-OUT-ON-THE-PYPI-APPROVAL` — `wait_for_release` printed *"REFUSING TO
     FINISH"* at 1800 s while release.yml waited on a human approving `pypi`. Now that is PENDING
     (exit 3), the operator is told what to do, and calling the same wait once it has published
     finishes the tail (the asset reads). The FLOW half — that a re-run of the whole script reaches
     this wait again — is graded by `test_a1_release_sh_converges.py`
     (`..._a_finished_release_reruns_to_convergence_asking_nothing`).

HOW: the wait functions are EXTRACTED from `release.sh` (sourcing it runs a release) and driven in
bash with `gh` and `sleep` replaced by shell FUNCTIONS — no PATH games, so the harness is the same on
Windows' bash. `gh` answers from per-command queues: each call consumes one `rc|stdout` line, and
the last line repeats.

⚠ WHAT THIS DOES NOT PROVE: that GitHub reports a run blocked on an environment's required
reviewers as `status: waiting`, and that `gh run list --branch <tag>` finds a tag-push run. Both are
documented GitHub behaviour; the first cut that runs this code exercises them.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import _support

import _release_retry as RR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")

_FUNCS = ("say", "ci_run_state", "read_ci_run", "wait_for_ci_green",
          "release_run_state", "read_release_run", "wait_for_release")

#: `gh` and `sleep` as shell functions. Queues live in "$GHQ/<key>"; calls are logged.
_FAKES = r'''
sleep() { printf 'SLEEP %s\n' "$1" >> "$GHQ/trace"; }
gh() {
  local key n total line rc out
  printf 'GH %s\n' "$*" >> "$GHQ/trace"
  case "$1 $2" in
    "run watch")    key=run-watch ;;
    "run view")     key=run-view ;;
    "run list")     case "$*" in *"--workflow CI"*) key=run-list-ci ;; *) key=run-list-release ;; esac ;;
    "release view") case "$*" in *assets*) key=release-assets ;; *) key=release-view ;; esac ;;
    *) echo "fake gh: unhandled: $*" >&2; return 99 ;;
  esac
  [ -f "$GHQ/$key" ] || { echo "fake gh: no queue for $key" >&2; return 98; }
  n="$(cat "$GHQ/$key.n" 2>/dev/null || echo 0)"; n=$((n + 1)); echo "$n" > "$GHQ/$key.n"
  total="$(wc -l < "$GHQ/$key")"; total=$((total + 0))
  [ "$n" -gt "$total" ] && n="$total"
  line="$(sed -n "${n}p" "$GHQ/$key")"
  rc="${line%%|*}"; out="${line#*|}"
  [ -n "$out" ] && printf '%b\n' "$out"
  [ "$rc" != "0" ] && echo "read tcp: connection reset by peer" >&2
  return "$rc"
}
'''

_RESET = "1|"                      # a dropped connection: non-zero, nothing on stdout


@unittest.skipIf(shutil.which("bash") is None, "bash is required to drive release.sh's functions")
@unittest.skipUnless(os.path.exists(RELEASE_SH), "release.sh is dev-only, excluded from the public mirror")
class TheReleaseWaitsAreThreeState(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with io.open(RELEASE_SH, encoding="utf-8") as fh:
            cls.text = fh.read()
        cls.tmpdir = tempfile.mkdtemp()
        backoff = re.search(r"^READ_BACKOFF=\(.*\)$", cls.text, re.M)
        assert backoff, "READ_BACKOFF is not defined at the top level of release.sh"
        parts = [backoff.group(0)]
        for name in _FUNCS:
            body = RR.shell_function_source(cls.text, name)
            assert body, f"{name} is not in release.sh"
            parts.append(body)
        cls.funcs = os.path.join(cls.tmpdir, "waits.sh")
        with io.open(cls.funcs, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(parts) + _FAKES)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # ---- driving ------------------------------------------------------------------------------
    def setUp(self):
        self.q = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.q, True)

    def queue(self, key, *answers):
        with io.open(os.path.join(self.q, key), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(a + "\n" for a in answers))
        counter = os.path.join(self.q, key + ".n")
        if os.path.exists(counter):
            os.remove(counter)

    def run_fn(self, expr):
        trace = os.path.join(self.q, "trace")
        if os.path.exists(trace):
            os.remove(trace)
        script = ("set -euo pipefail\n"
                  f"source '{_support.as_posix(self.funcs)}'\n"
                  f"GHQ='{_support.as_posix(self.q)}'\n"
                  "PUB_CHECKOUT=/path/to/mokata-oss\n"
                  f"{expr}\n")
        proc = subprocess.run(_support.bash_argv("-c", script), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True)
        self.trace = []
        if os.path.exists(trace):
            with io.open(trace, encoding="utf-8") as fh:
                self.trace = fh.read().splitlines()
        return proc

    def sleeps(self):
        return [int(t.split()[1]) for t in self.trace if t.startswith("SLEEP ")]

    # ================================================= CI-WAIT-READS-A-NETWORK-RESET-AS-RED
    def test_a1_ci_wait_a_reset_watch_on_a_green_run_is_green(self):
        """⭐ THE 0.0.22 CUT, REPLAYED: run 38511809434 green, the watch dies on a reset.
        FAIL-ON-OLD: the old wait printed "did NOT conclude success" and exited 1."""
        self.queue("run-list-ci", "0|38511809434")
        self.queue("run-watch", _RESET)
        self.queue("run-view", "0|completed\\tsuccess")
        proc = self.run_fn("wait_for_ci_green JasGujral/mokata-oss abc123 'the public mirror (merged main)'")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("CI GREEN", proc.stdout)
        self.assertNotIn("did NOT conclude success", proc.stdout + proc.stderr)
        self.assertNotIn("REFUSING", proc.stdout + proc.stderr)

    def test_a1_ci_wait_an_unreadable_conclusion_is_retried_with_backoff_then_answers(self):
        """The read ALSO resets, twice, then answers. Retried — with growing, bounded delays."""
        self.queue("run-list-ci", "0|42")
        self.queue("run-watch", _RESET)
        self.queue("run-view", _RESET, _RESET, "0|completed\\tsuccess")
        proc = self.run_fn("wait_for_ci_green o/r abc123 here")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("UNREADABLE, not red", proc.stdout)
        self.assertEqual(self.sleeps(), [5, 10])

    def test_a1_ci_wait_a_read_that_never_answers_is_UNREADABLE_not_red(self):
        """§7g: the bounded retries run out — exit 4 and the word UNREADABLE, never the red text."""
        self.queue("run-list-ci", "0|42")
        self.queue("run-watch", _RESET)
        self.queue("run-view", _RESET)
        proc = self.run_fn("wait_for_ci_green o/r abc123 here")
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        self.assertIn("UNREADABLE", proc.stderr)
        self.assertIn("NOT a red CI run", proc.stderr)
        self.assertNotIn("did NOT conclude success", proc.stderr)
        self.assertNotIn("REFUSING TO TAG", proc.stderr)
        sleeps = self.sleeps()
        self.assertEqual(len(sleeps), 6, "the retry budget must be bounded and spent")
        self.assertEqual(sleeps, sorted(sleeps), "backoff must not shrink")
        self.assertLessEqual(sum(sleeps), 600, "the backoff is bounded (~5 min)")

    def test_a1_ci_wait_a_real_red_is_still_red(self):
        """NEGATIVE: the fix must not soften a real failure. A read that SAYS failure refuses."""
        self.queue("run-list-ci", "0|42")
        self.queue("run-watch", "1|")
        self.queue("run-view", "0|completed\\tfailure")
        proc = self.run_fn("wait_for_ci_green o/r abc123 here")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("REFUSING TO TAG", proc.stderr)
        self.assertIn("'failure'", proc.stderr)
        self.assertEqual(self.sleeps(), [], "a red answer is final; nothing to retry")

    def test_a1_ci_wait_a_watch_dropped_mid_run_watches_again(self):
        """The reset arrives while the run is still in progress: resume watching, do not judge."""
        self.queue("run-list-ci", "0|42")
        self.queue("run-watch", _RESET, "0|")
        self.queue("run-view", "0|in_progress\\t")
        proc = self.run_fn("wait_for_ci_green o/r abc123 here")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(sum(1 for t in self.trace if t.startswith("GH run watch")), 2)

    def test_a1_ci_wait_an_unreadable_run_list_is_not_called_unregistered(self):
        """The registration poll: every `gh run list` fails. FAIL-ON-OLD: `|| true` folded that
        into "no CI run registered" — an ABSENT answer for an UNREADABLE fact."""
        self.queue("run-list-ci", _RESET)
        proc = self.run_fn("wait_for_ci_green o/r abc123 here")
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        self.assertIn("UNREADABLE", proc.stderr)
        self.assertNotIn("no CI run registered", proc.stderr)

    def test_a1_ci_run_state_is_three_state_and_pure(self):
        cases = {("0", "completed", "success"): "GREEN",
                 ("0", "completed", "failure"): "RED",
                 ("0", "completed", "cancelled"): "RED",
                 ("0", "in_progress", ""): "RUNNING",
                 ("0", "queued", ""): "RUNNING",
                 ("1", "", ""): "UNREADABLE",
                 ("0", "", ""): "UNREADABLE",
                 ("0", "completed", ""): "UNREADABLE"}
        for args, want in cases.items():
            with self.subTest(args=args):
                proc = self.run_fn("ci_run_state " + " ".join(f"'{a}'" for a in args))
                self.assertEqual(proc.stdout.strip(), want)
        code = "\n".join(ln for _n, ln in RR.code_lines(RR.shell_function_source(self.text, "ci_run_state")))
        for forbidden in ("gh ", "$(", "`"):
            self.assertNotIn(forbidden, code)

    # ============================================ RELEASE-WAIT-TIMES-OUT-ON-THE-PYPI-APPROVAL
    def test_a1_release_wait_an_unapproved_pypi_is_PENDING_not_a_refusal(self):
        """⭐ THE 0.0.22 CUT, REPLAYED: the release never appears in the window because release.yml
        is WAITING on the `pypi` approval. FAIL-ON-OLD: "REFUSING TO FINISH", exit 1."""
        self.queue("release-view", "1|")
        self.queue("run-list-release", "0|38600000001\\twaiting\\t")
        proc = self.run_fn("wait_for_release JasGujral/mokata-oss v0.0.22")
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 3, out)
        self.assertIn("PENDING", proc.stderr)
        self.assertNotIn("REFUSING TO FINISH", out)
        # what the human must do: approve THAT run, then re-run the same command
        self.assertIn("https://github.com/JasGujral/mokata-oss/actions/runs/38600000001", proc.stderr)
        self.assertIn("pypi", proc.stderr)
        self.assertIn("Approve", proc.stderr)
        self.assertIn("scripts/release.sh /path/to/mokata-oss", proc.stderr)
        # and it said so as soon as it knew, not only at the deadline
        self.assertIn("WAITING for a human to approve", proc.stdout)
        self.assertEqual(proc.stdout.count("Review deployments"), 1, "told once, not every poll")
        self.assertEqual(sum(self.sleeps()), 1800, "the publish window itself is unchanged")

    def test_a1_release_wait_a_rerun_after_approval_converges_and_reads_the_assets(self):
        """The same wait, called again once the release has published (what a re-run of the
        script reaches): it finishes — the asset reads run and pass."""
        self.queue("release-view", "1|")
        self.queue("run-list-release", "0|7\\twaiting\\t")
        self.assertEqual(self.run_fn("wait_for_release o/r v1.2.3").returncode, 3)
        self.queue("release-view", "0|")
        self.queue("release-assets", "0|mokata-1.2.3.tar.gz\\nmokata-1.2.3.tar.gz.sigstore.json\\nsbom.cdx.json")
        proc = self.run_fn("wait_for_release o/r v1.2.3")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("with attestation + SBOM attached", proc.stdout)
        self.assertTrue(any("release view v1.2.3" in t and "assets" in t for t in self.trace),
                        "the tail did not read the assets")

    def test_a1_release_wait_approval_inside_the_window_finishes_in_one_run(self):
        """The operator approves while the script is still waiting: no exit, no re-run."""
        self.queue("release-view", "1|", "1|", "1|", "0|")
        self.queue("run-list-release", "0|7\\twaiting\\t", "0|7\\tin_progress\\t")
        self.queue("release-assets", "0|a.sigstore.json\\nsbom.cdx.json")
        proc = self.run_fn("wait_for_release o/r v1.2.3")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a1_release_wait_a_still_running_workflow_is_PENDING_too(self):
        self.queue("release-view", "1|")
        self.queue("run-list-release", "0|7\\tin_progress\\t")
        proc = self.run_fn("wait_for_release o/r v1.2.3")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("still running", proc.stderr)
        self.assertNotIn("REFUSING TO FINISH", proc.stderr)

    def test_a1_release_wait_a_failed_workflow_still_refuses_and_early(self):
        """NEGATIVE: a release run that FAILED will never publish — refuse now, not at 1800 s."""
        self.queue("release-view", "1|")
        self.queue("run-list-release", "0|7\\tcompleted\\tfailure")
        proc = self.run_fn("wait_for_release o/r v1.2.3")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("REFUSING TO FINISH", proc.stderr)
        self.assertEqual(self.sleeps(), [])

    def test_a1_release_wait_no_run_at_all_still_refuses_at_the_deadline(self):
        """NEGATIVE: nothing is publishing. That is not PENDING."""
        self.queue("release-view", "1|")
        self.queue("run-list-release", "0|")
        proc = self.run_fn("wait_for_release o/r v1.2.3")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("REFUSING TO FINISH", proc.stderr)
        self.assertNotIn("PENDING", proc.stderr)

    def test_a1_release_wait_an_unreadable_run_at_the_deadline_is_UNREADABLE(self):
        self.queue("release-view", "1|")
        self.queue("run-list-release", _RESET)
        proc = self.run_fn("wait_for_release o/r v1.2.3")
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        self.assertIn("UNREADABLE", proc.stderr)
        self.assertNotIn("REFUSING TO FINISH", proc.stderr)

    def test_a1_release_wait_a_reset_asset_read_is_not_a_missing_attestation(self):
        """The asset read is a read too. FAIL-ON-OLD: `|| true` turned a reset into "carries no
        Sigstore attestation", exit 1."""
        self.queue("release-view", "0|")
        self.queue("release-assets", _RESET, "0|a.sigstore.json\\nsbom.cdx.json")
        proc = self.run_fn("wait_for_release o/r v1.2.3")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.sleeps(), [5])
        self.queue("release-assets", _RESET)
        proc = self.run_fn("wait_for_release o/r v1.2.3")
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        self.assertNotIn("carries no Sigstore attestation", proc.stderr)

    def test_a1_release_wait_a_published_release_without_its_attestation_still_refuses(self):
        self.queue("release-view", "0|")
        self.queue("release-assets", "0|sbom.cdx.json")
        proc = self.run_fn("wait_for_release o/r v1.2.3")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("carries no Sigstore attestation", proc.stderr)

    def test_a1_release_run_state_names_the_approval(self):
        cases = {("0", "waiting", ""): "PENDING-APPROVAL",
                 ("0", "in_progress", ""): "RUNNING",
                 ("0", "completed", "success"): "SUCCEEDED",
                 ("0", "completed", "failure"): "FAILED",
                 ("0", "", ""): "NO-RUN",
                 ("1", "", ""): "UNREADABLE"}
        for args, want in cases.items():
            with self.subTest(args=args):
                proc = self.run_fn("release_run_state " + " ".join(f"'{a}'" for a in args))
                self.assertEqual(proc.stdout.strip(), want)

    def test_a1_the_exit_codes_are_documented_where_they_are_raised(self):
        """Three exits, three meanings, written down beside the code that raises them."""
        for line in ("exit 1  REFUSED", "exit 3  PENDING", "exit 4  UNREADABLE"):
            self.assertIn(line, self.text)


if __name__ == "__main__":
    unittest.main()
