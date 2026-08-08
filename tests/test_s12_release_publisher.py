"""Stage 12 — RELEASE-SH-GITHUB-RELEASE-RACE: exactly ONE publisher creates the GitHub Release.

Two independent paths used to create the release for one tag on one repo. `scripts/release.sh`
pushed `$TAG` to `$PUB_REPO`, which triggers `.github/workflows/release.yml`, whose
`github-release` job creates the Release via `softprops/action-gh-release` — and then
`release.sh` ALSO ran `gh release create` for the same tag on the same repo.

THIS WAS NOT THEORETICAL. Across the eight public releases v0.0.9 -> v0.0.16, the race resolved
BOTH ways and the published metadata records which:

    github-actions[bot], titled "v0.0.16"      -> the workflow won   (v0.0.16, v0.0.11, v0.0.9)
    JasGujral,           titled "mokata 0.0.15" -> release.sh won    (v0.0.15 .. v0.0.10, 5 of 8)

The release TITLE on the public repo is therefore a function of who finished first, not of
either author's intent. Nothing in the suite pinned either publisher, so nothing noticed.

THE RULING (doc 99 / doc 84 RELEASE-SH-GITHUB-RELEASE-RACE): the WORKFLOW wins. It owns the
Sigstore-signed wheel + sdist and the SBOM, and attaches them to the Release; `gh release create`
attaches nothing. A release the workflow did not create is a release whose attestations refer to
something else. So `release.sh` WAITS for and VERIFIES the workflow's release; it never creates one.

Both halves are pinned here, because fixing only one re-creates the race by hand:

  * the CALL   (`release.sh` step 7) must not create a release;
  * the PRINTED RUNBOOK (`release.sh` step 11 of the no-checkout branch) must not TELL an
    operator to create one either. That inverts AMEND-STEP-2-IS-UNADVERTISED: an instruction
    naming an action that no longer exists, handing the operator the race the code just dropped.

Comment-hole discipline (doc 84 PIN-SUBSTRING-COMMENT-HOLE): the prose above and the prose in
`release.sh` both NAME `gh release create` while asserting its absence, so every assertion here
reads a COMMENT-STRIPPED view of the script. A pin that greps the raw file for a literal it also
documents is not a pin.

Pure/offline; dependency-free; no PyYAML (the workflow side is a whole-tree literal count, which
needs no parse) — so this battery neither skips nor degrades on any runner. Deterministic.

MIRROR BOUNDARY (stage 28, SHIPPED-TEST-READS-INTERNAL-FILE). `tests/` ships to the public mirror;
`scripts/release.sh` does not. As first written, all eight release.sh assertions here raised
`FileNotFoundError` on the public subset — green in this repo, ERRORing in the one users clone, and
`run_public_subset_preflight` refused the cut over it. The guard that fixes it
(`@unittest.skipUnless(os.path.exists(RELEASE_SH), ...)`) had existed at
`test_stage68_supply_chain.py:342` since stage 68; this file was written straight past it. Three
things follow, and all three are the point:

  * the two release.sh batteries carry the CLASS DECORATOR — never a `setUpClass` skip, which
    collapses the class into ONE skip and drops its tests out of `Ran N` altogether (measured:
    `Ran 0 ... skipped=1` against the decorator's `Ran 2 ... skipped=2`), diverging the mirror's
    count from this repo's;
  * `TestExactlyOneWorkflowPublishes` was SPLIT OUT rather than swept under the guard, because it
    reads only `.github/workflows/`, which ships. Nothing here was weakened to be portable;
  * `TestReleaseShIsAbsentOnlyBecauseOfTheMirrorBoundary` runs UNGUARDED on both sides, so a
    DELETED release.sh cannot hide inside the skip.

`tests/_shipped_reads.py` derives that property over the whole shipped corpus, so the next file to
read an internal path fails there instead of at the next cut.
"""

import os
import re
import unittest

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")

# The one action permitted to create a GitHub Release, and the one workflow permitted to use it.
RELEASE_ACTION = "softprops/action-gh-release"
PUBLISHING_WORKFLOW = "release.yml"


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _strip_comments(text):
    """Drop whole-line `#` comments, keeping line numbers stable via blank placeholders.

    Whole-line only, deliberately: a trailing `#` inside a shell string is not a comment, and a
    naive split would silently truncate the very `echo` lines this module inspects. The hazard
    that bit TD-2 (doc 02) was a SIX-LINE COMMENT BLOCK documenting the control it guarded --
    that is precisely what this removes.
    """
    out = []
    for line in text.splitlines():
        out.append("" if line.lstrip().startswith("#") else line)
    return out


# The refusal banner every fail-closed branch of wait_for_release must print AND abort on.
_REFUSAL = "REFUSING TO FINISH"


def _wait_fn():
    """The body of release.sh's wait_for_release(), comments removed.

    Reads the FULL comment-stripped script, not the executed-only subset: a fail-closed branch is
    an `echo` of the refusal followed by an `exit`, so splitting echo from non-echo first would
    drop every refusal banner and leave this reading a body with no refusals in it at all.
    """
    body = "\n".join(_strip_comments(_read(RELEASE_SH)))
    m = re.search(r"wait_for_release\(\)\s*\{(.*?)\n\}", body, re.S)
    assert m is not None, "wait_for_release() is not defined in scripts/release.sh"
    return m.group(1)


def _manual_runbook():
    """Only the numbered steps release.sh prints when it has NO public checkout to drive.

    SCOPED DELIBERATELY. Asserting over every `echo` in the script instead lets a refusal message
    inside wait_for_release() — which also names release.yml — satisfy a pin about what the
    RUNBOOK tells an operator. Mutant M08 gutted the runbook to one bland line and the unscoped
    pin stayed green on that unrelated message: the same too-wide-region defect as TD-2 (doc 02),
    where six lines of comment satisfied a check about an array literal.
    """
    lines = _strip_comments(_read(RELEASE_SH))
    start = end = None
    for i, line in enumerate(lines):
        if start is None and "finish the release MANUALLY" in line:
            start = i
        elif start is not None and "Stopping here" in line:
            end = i
            break
    assert start is not None and end is not None, (
        "could not locate the manual runbook block in release.sh — it is delimited by the "
        "'finish the release MANUALLY' header and the 'Stopping here' footer")
    return "\n".join(lines[start:end + 1])


def _script_lines():
    """(executable lines, printed-runbook lines) of release.sh, comments removed.

    A line that starts with `echo` is INSTRUCTION TO A HUMAN; anything else the shell runs.
    They fail differently and so are asserted separately.
    """
    lines = _strip_comments(_read(RELEASE_SH))
    printed, executed = [], []
    for line in lines:
        (printed if line.lstrip().startswith("echo ") else executed).append(line)
    return executed, printed


class TestReleaseShIsAbsentOnlyBecauseOfTheMirrorBoundary(unittest.TestCase):
    """★ The companion the two guards below cannot do without (stage 28).

    `release.sh` is excluded from the public mirror, so the guards must let this file run there.
    Left alone, that same skip would swallow a DELETED release.sh anywhere else: the two batteries
    would vanish, the run would report OK, and "the file is excluded from the mirror" and "someone
    deleted the file" would share a green. `sync-public.sh` is held back by the SAME two controls
    (`--exclude` + `INTERNAL_PATHS`), so on any tree carrying one, the other must be there too.

    Deliberately UNGUARDED: it is the one thing here that must run on both sides of the boundary.
    """

    def test_release_sh_is_present_wherever_sync_public_is(self):
        if not os.path.exists(SYNC_SH):
            self.skipTest("public subset — neither internal script ships, as intended")
        self.assertTrue(
            os.path.exists(RELEASE_SH),
            "scripts/sync-public.sh is present, so this is the PRIVATE tree — but "
            "scripts/release.sh is gone. Both batteries below would SKIP rather than fail, and "
            "the one-publisher ruling would go unpinned while the run reported OK.")


class TestExactlyOneWorkflowPublishes(unittest.TestCase):
    """The half of the one-publisher ruling that lives in files which SHIP.

    Split out of `TestExactlyOnePublisher` (stage 28) rather than swept under its guard: this
    reads only `.github/workflows/`, which mirrors, so it runs on the public subset as it always
    did. Guarding the whole class to fix the two release.sh tests beside it would have silently
    dropped a pin the mirror can perfectly well enforce — the guard is for what cannot run there,
    not for whatever happens to sit in the same class.
    """

    def test_exactly_one_workflow_step_creates_a_release(self):
        """The whole-tree count, not one file's — a second workflow adding the action is the
        same defect wearing different clothes."""
        hits = []
        for name in sorted(os.listdir(WORKFLOWS)):
            if not name.endswith((".yml", ".yaml")):
                continue
            for i, line in enumerate(_strip_comments(_read(os.path.join(WORKFLOWS, name))), 1):
                if RELEASE_ACTION in line:
                    hits.append("%s:%d" % (name, i))
        self.assertEqual(
            1, len(hits),
            "exactly one workflow step may create the GitHub Release (%s); found %d: %s"
            % (RELEASE_ACTION, len(hits), ", ".join(hits) or "none"))
        self.assertTrue(
            hits[0].startswith(PUBLISHING_WORKFLOW + ":"),
            "the release publisher must live in %s, not %s" % (PUBLISHING_WORKFLOW, hits[0]))


@unittest.skipUnless(os.path.exists(RELEASE_SH),
                     "release.sh is dev-only, excluded from the public mirror")
class TestExactlyOnePublisher(unittest.TestCase):
    """One tag, one repo, one thing that creates the Release — the `release.sh` half."""

    def test_release_sh_does_not_create_a_github_release(self):
        executed, _ = _script_lines()
        offenders = [ln.strip() for ln in executed if re.search(r"gh\s+release\s+create", ln)]
        self.assertEqual(
            [], offenders,
            "scripts/release.sh RUNS `gh release create` — that is the second publisher.\n"
            "The github-release job of .github/workflows/release.yml already creates this\n"
            "release WITH the signed artifacts + SBOM. Two publishers race; the winner\n"
            "decides the published title and notes. Offending line(s):\n  "
            + "\n  ".join(offenders))

    def test_the_printed_runbook_does_not_tell_an_operator_to_create_one(self):
        """Dropping the call but keeping the instruction hands the race to a human instead."""
        _, printed = _script_lines()
        offenders = [ln.strip() for ln in printed if re.search(r"gh\s+release\s+create", ln)]
        self.assertEqual(
            [], offenders,
            "scripts/release.sh PRINTS `gh release create` as a manual step. The call was\n"
            "removed to leave exactly one publisher; an instruction that re-creates it by hand\n"
            "restores the race with a human in the loop. Offending line(s):\n  "
            + "\n  ".join(offenders))


@unittest.skipUnless(os.path.exists(RELEASE_SH),
                     "release.sh is dev-only, excluded from the public mirror")
class TestReleaseShVerifiesRatherThanPublishes(unittest.TestCase):
    """Removing the call is only half the ruling: the script must still CHECK that the one
    publisher did its job, or the release becomes unverified rather than merely un-raced."""

    def test_release_sh_actually_CALLS_the_wait(self):
        """Pinned on the CALL SITE, with the definition excised first.

        `assertIn("wait_for_release", body)` is satisfied by the function's own definition, so a
        release.sh that defines a perfect fail-closed wait and never invokes it would pass —
        dead code reading as an enforced gate. What ships the property is the call.
        """
        body = "\n".join(_strip_comments(_read(RELEASE_SH)))
        without_def = re.sub(r"wait_for_release\(\)\s*\{.*?\n\}", "", body, flags=re.S)
        self.assertIn(
            "wait_for_release", without_def,
            "release.sh defines wait_for_release() but never CALLS it — the script finishes "
            "without ever confirming the workflow's release exists.")

    def test_the_wait_reads_the_published_release(self):
        self.assertTrue(
            re.search(r"gh\s+release\s+view", _wait_fn()),
            "wait_for_release must READ the published release (`gh release view`), not assume "
            "it landed. A wait that polls nothing returns instantly and always succeeds.")

    def test_every_refusal_in_the_wait_actually_aborts(self):
        """Same contract as wait_for_ci_green: a refusal ABORTS, it does not shrug and continue.

        Asserted PER REFUSAL, not once for the function. `assertIn("exit 1", fn)` would be
        satisfied by any ONE surviving abort while the other branches fell through printing a
        refusal and returning success — a script that says REFUSING and then exits 0 is worse
        than one that never checked.
        """
        fn = _wait_fn()
        chunks = fn.split(_REFUSAL)[1:]
        self.assertGreaterEqual(
            len(chunks), 3,
            "wait_for_release should refuse on three distinct grounds (never published, no "
            "attestation, no SBOM); found %d" % len(chunks))
        for i, chunk in enumerate(chunks, 1):
            self.assertIn(
                "exit 1", chunk,
                "refusal #%d in wait_for_release prints '%s' but never exits non-zero, so the "
                "release proceeds as if it had succeeded. Branch:\n%s"
                % (i, _REFUSAL, chunk[:400]))

    def test_the_wait_greps_for_the_artifacts_only_the_workflow_can_attach(self):
        """The whole reason the workflow won the ruling: a release without these is
        indistinguishable from what the deleted `gh release create` produced.

        Pinned on the GREP EXPRESSIONS, not on the word 'sigstore' appearing somewhere in the
        function. The refusal messages name both artifacts, so a substring pin over the function
        body stays green with the checks themselves deleted — the exact shape doc 84's
        PIN-SUBSTRING-COMMENT-HOLE convicted. What is load-bearing is that something MATCHES the
        asset names against the published list.
        """
        # The patterns are shell-quoted regexes (`'\.sigstore\.json$'`), so the backslashes come
        # out with them; drop those before matching on the artifact name.
        greps = [ln.replace("\\", "") for ln in _wait_fn().splitlines() if "grep -q" in ln]
        self.assertTrue(
            any("sigstore.json" in ln for ln in greps),
            "wait_for_release does not GREP the published asset list for *.sigstore.json — "
            "the Sigstore attestation is unverified. grep lines found: %r" % greps)
        self.assertTrue(
            any("sbom" in ln.lower() for ln in greps),
            "wait_for_release does not GREP the published asset list for the SBOM. "
            "grep lines found: %r" % greps)

    def test_the_manual_runbook_names_the_workflow_as_the_publisher(self):
        """An operator on the manual path must be told publication is automatic. Silence here
        is how someone reaches for `gh release create` to 'finish the job'."""
        runbook = _manual_runbook().lower()
        self.assertIn(
            "release.yml", runbook,
            "the manual runbook never names release.yml, so an operator following it has no "
            "way to know the Release is published for them — and will publish it themselves.")

    def test_the_manual_runbook_tells_the_operator_how_to_verify(self):
        """Naming the publisher is not enough: the operator needs the check that tells them it
        actually landed, or 'it's automatic' is the last thing they hear about it."""
        runbook = _manual_runbook()
        self.assertTrue(
            re.search(r"gh\s+release\s+view", runbook),
            "the manual runbook says the release is published automatically but gives no way "
            "to VERIFY it — an unverified claim is what the automated path fails closed on.")


if __name__ == "__main__":
    unittest.main()
