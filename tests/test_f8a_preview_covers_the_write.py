"""F8a — `preview-covers-the-write` (0.0.19 stage 10a). A P2 violation THIS RELEASE created.

WHAT WAS WRONG
--------------
`cli_commands/setup.py` returned from `--preview` after `render_plan(plan_init(...))` — BEFORE
`_wire_or_disclose` — so the dry-run showed the `.mokata/` manifest and nothing else. Meanwhile
`templates/commands/init.md` previewed at step 3 and ran `--yes` at step 5, and F8 (stage 03) had
taught `--yes` to wire the harness: 37 commands, 26 Agent Skills, `.mcp.json` and `settings.json`.
**A human approved a plan omitting the majority of the write.** P2 — *every durable write is
human-gated* — is doc 00's hard constraint, and a gate whose preview omits the write is a
formality. F8 introduced this three stages before the docs gate found it.

WHAT THESE TESTS REFUSE TO LET DRIFT
------------------------------------
* **THE COMPARISON IS DERIVED, NEVER A SNAPSHOT.** `ThePreviewCoversTheWrite` runs the real
  `--yes` init into one tree and the `--preview --yes` dry-run into another, and grades the
  rendered text against **the files that actually landed**. A test asserting "37 commands, 26
  skills" would pass forever and re-break the moment a template is added — the stale-constant
  class this release has now filed five times. Nothing here hard-codes a count.
* **THREE CASES ARE THREE FACTS.** `--preview` alone previews a run that wires nothing and says
  so; `--preview --yes` previews the wiring; `--preview --mode <m>` previews the mode's resolved
  profile on the same condition. A preview that showed harness wiring for a non-`--yes` init
  would be the same lie pointing the other way, so the negative is pinned as hard as the positive.
* **THE GUARD IS THE LOAD-BEARING HALF.** Stage 10 filed *"nothing guards that divergence"* as its
  own finding: without a guard, the next flag added to the `--yes` wiring re-opens this defect and
  nothing notices. `TheDivergenceGuard` captures the keywords BOTH sites actually pass — at
  runtime, not by reading source — and compares them over `plan_setup`'s own signature. The
  apply-only exclusions are not an allowlist anyone can grow: a name is exempt only because
  `plan_setup` cannot take it.
* **§7i — EVERY GRADER IS SHOWN CATCHING A PLANTED OFFENDER.** `uncovered_paths`,
  `declared_counts` and `template_drift` are pure functions over a SUPPLIED corpus, and each has a
  negative that feeds it doctored input and asserts it complains. A grader nobody has watched fail
  is a grader nobody knows the shape of.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import inspect
import io
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import harness_setup                                     # noqa: E402
from mokata.adoption_modes import profile_for_mode                   # noqa: E402
from mokata.cli import main                                          # noqa: E402
from mokata.cli_commands import setup as SETUP                       # noqa: E402
from mokata.harness_setup import plan_setup                          # noqa: E402

SRC_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
TEMPLATE = os.path.join(SRC_ROOT, "mokata", "templates", "commands", "init.md")


# ======================================================================================
# The graders. Pure functions over a SUPPLIED corpus (doc 85 §7i) — never a walk that goes
# and finds the tree, so every one of them can be handed a planted offender below.
# ======================================================================================

def uncovered_paths(preview_text, root, paths):
    """The paths a write created that the preview does not account for.

    A path is COVERED when the preview names it outright, or names an ancestor directory of it
    that is a PROPER descendant of `root`. The ancestor rule is what lets one line —
    `Will write 37 commands -> <dir>` — cover 37 files without the preview turning into a
    manifest; the proper-descendant rule is what stops a bare mention of the project root from
    covering everything and making this vacuous."""
    root = os.path.realpath(root)
    missing = []
    for p in sorted(paths):
        real = os.path.realpath(p)
        if real in preview_text or p in preview_text:
            continue
        anc = os.path.dirname(real)
        while anc.startswith(root + os.sep):
            if anc in preview_text:
                break
            anc = os.path.dirname(anc)
        else:
            missing.append(os.path.relpath(real, root))
    return missing


_COUNT = re.compile(r"Will write (\d+) (commands|Agent Skills)")


def declared_counts(preview_text):
    """`{"commands": n, "Agent Skills": n}` — what the preview CLAIMS it will write. Read out of
    the rendered text, so it is graded against the disk rather than against the plan object that
    produced it: a renderer that counted one list and wrote another would survive that."""
    return {kind: int(n) for n, kind in _COUNT.findall(preview_text)}


def written_counts(root):
    """The same two numbers, measured on disk after a real write."""
    commands = Path(root) / ".claude" / "commands"
    skills = Path(root) / ".claude" / "skills"
    return {
        "commands": len([p for p in commands.glob("*") if p.is_file()]) if commands.is_dir() else 0,
        "Agent Skills": len([p for p in skills.glob("*/SKILL.md")]) if skills.is_dir() else 0,
    }


_ENGINE_INIT = re.compile(r'^\s*eval\s+"\$ENGINE\s+(init\s+[^"]*)"\s*$')


def init_invocations(template_text):
    """Every `$ENGINE init ...` the template RUNS, as argv lists. Commented lines are not runs and
    are not collected — a `#`-prefixed variant is prose about a flag, not an invocation."""
    found = []
    for line in template_text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _ENGINE_INIT.match(line)
        if m:
            found.append(m.group(1).split())
    return found


def template_drift(template_text):
    """THE INVARIANT, as complaints: the argv `/mokata:init` previews and the argv it then runs
    must differ by `--preview` and nothing else.

    Stated as a set difference rather than a string comparison because flag ORDER is not the
    contract — a flag reaching one command and not the other is. Exactly one of each: a second
    apply with no matching preview is the defect wearing a different shape."""
    runs = init_invocations(template_text)
    previews = [a for a in runs if "--preview" in a]
    applies = [a for a in runs if "--preview" not in a]
    problems = []
    if len(previews) != 1:
        problems.append(f"expected exactly 1 previewed init, found {len(previews)}")
    if len(applies) != 1:
        problems.append(f"expected exactly 1 executed init, found {len(applies)}")
    if problems:
        return problems
    previewed, executed = sorted(previews[0]), sorted(applies[0] + ["--preview"])
    if previewed != executed:
        only_preview = sorted(set(previews[0]) - set(applies[0]) - {"--preview"})
        only_apply = sorted(set(applies[0]) - set(previews[0]))
        problems.append(
            f"the previewed argv and the executed argv differ by more than --preview: "
            f"only in the preview {only_preview}, only in the run {only_apply}")
    return problems


# ======================================================================================
# fixtures
# ======================================================================================

def _preview(argv, root, home):
    """The real CLI, stdout captured. Returns `(rc, text)` and asserts nothing itself."""
    buf = io.StringIO()
    with mock.patch.dict(os.environ, {**os.environ, "HOME": home}), redirect_stdout(buf):
        rc = main(list(argv) + ["--path", root])
    return rc, buf.getvalue()


def _tree(root):
    """Every file under `root`, absolute. The measurement both halves of the comparison use."""
    return [os.path.join(dp, f) for dp, _dn, fn in os.walk(root) for f in fn]


def _args(**kw):
    ns = argparse.Namespace(path=".", profile="standard", yes=False, force=False,
                            preview=False, mode=None)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# ======================================================================================
# Three cases, three facts.
# ======================================================================================
class TheThreeCases(unittest.TestCase):

    def test_f8a_regression_preview_with_yes_names_the_harness_write(self):
        """THE closer. On the old code this output stopped at the `.mokata/` file list."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            d = os.path.realpath(d)
            rc, out = _preview(["init", "--profile", "standard", "--preview", "--yes"], d, home)
            self.assertEqual(rc, 0)
            for surface in (os.path.join(d, ".claude", "commands"),
                            os.path.join(d, ".claude", "skills"),
                            os.path.join(d, ".claude", "settings.json"),
                            os.path.join(d, ".mcp.json")):
                self.assertIn(surface, out,
                              f"the preview never names {surface}, which `--yes` writes")

    def test_f8a_preview_alone_promises_no_harness_wiring(self):
        """The other direction. A non-`--yes` init wires nothing here, so a preview that showed
        the harness plan would be `DISCLOSURE-PRESENCE-IS-NOT-DISCLOSURE-TRUTH` pointed the other
        way — and it would teach the reader that the section means nothing."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            d = os.path.realpath(d)
            rc, out = _preview(["init", "--profile", "standard", "--preview"], d, home)
            self.assertEqual(rc, 0)
            self.assertNotIn(os.path.join(d, ".claude"), out)
            self.assertNotIn(".mcp.json", out)
            self.assertIn("Harness wiring: NOT part of this run.", out)

    def test_f8a_the_negative_still_names_the_consent_that_would_wire(self):
        """Three states, never two (doc 85 §7g): "this run wires nothing" is not "mokata cannot
        wire this". A negative that stops at the first half leaves the reader with no next step."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            _rc, out = _preview(["init", "--profile", "standard", "--preview"],
                                os.path.realpath(d), home)
            self.assertIn("--yes", out)
            self.assertIn("mokata setup claude", out)

    def test_f8a_a_mode_previews_the_modes_profile_and_wires_on_the_same_condition(self):
        """`--mode` is an alias for a profile plus a quickstart, and it reaches the SAME
        `_wire_or_disclose`. So the preview shows the resolved profile, and the harness half turns
        on `--yes` exactly as it does everywhere else — never on the mode."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            d = os.path.realpath(d)
            resolved = profile_for_mode("seatbelt")
            _rc, plain = _preview(["init", "--mode", "seatbelt", "--preview"], d, home)
            self.assertIn(f"profile '{resolved}'", plain)
            self.assertIn("Harness wiring: NOT part of this run.", plain)

            _rc, consented = _preview(["init", "--mode", "seatbelt", "--preview", "--yes"],
                                      d, home)
            self.assertIn(f"profile '{resolved}'", consented)
            self.assertIn(os.path.join(d, ".claude", "commands"), consented)

    def test_f8a_no_preview_writes_anything(self):
        """P2, and the reason `AuditLedger.path_for` exists: naming the ledger among the files
        init would write must not CREATE it. A dry-run that leaves a directory behind is the
        failure this whole stage is about, arriving through the fix."""
        for argv in (["init", "--profile", "standard", "--preview"],
                     ["init", "--profile", "standard", "--preview", "--yes"],
                     ["init", "--mode", "seatbelt", "--preview", "--yes"]):
            with self.subTest(argv=" ".join(argv)):
                with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
                    d = os.path.realpath(d)
                    rc, _out = _preview(argv, d, home)
                    self.assertEqual(rc, 0)
                    self.assertEqual([], sorted(os.listdir(d)),
                                     "a dry-run left something on disk")


# ======================================================================================
# The comparison: what is SHOWN against what LANDS. Derived from the same run, both ends.
# ======================================================================================
class ThePreviewCoversTheWrite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """One real `--yes` init and one `--preview --yes` of the same command, into two fresh
        trees. Both go through the CLI: a comparison that called the renderer directly would not
        be grading the surface the human reads."""
        cls._tmp = tempfile.TemporaryDirectory()
        cls._home = tempfile.TemporaryDirectory()
        base = os.path.realpath(cls._tmp.name)
        cls.shown_root = os.path.join(base, "shown")
        cls.done_root = os.path.join(base, "done")
        os.makedirs(cls.shown_root)
        os.makedirs(cls.done_root)
        argv = ["init", "--profile", "standard", "--yes"]
        _rc, cls.preview_text = _preview(argv + ["--preview"], cls.shown_root, cls._home.name)
        _rc, cls.write_text = _preview(argv, cls.done_root, cls._home.name)
        cls.written = _tree(cls.done_root)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        cls._home.cleanup()

    def test_f8a_the_write_actually_wired_something(self):
        """The comparison's own floor. Every assertion below is trivially true over an empty
        write, so the write is pinned as non-trivial before anything is compared to it."""
        self.assertGreater(len(self.written), 50, self.write_text)
        self.assertTrue(os.path.isfile(os.path.join(self.done_root, ".mcp.json")))
        self.assertTrue(os.path.isdir(os.path.join(self.done_root, ".claude", "skills")))

    def _rebased(self):
        """The preview text with its own tree's paths rebased onto the tree that was written.

        The two runs deliberately use different roots — a preview that could only be compared
        against the tree it previewed would be comparing a run to itself. ⚠ Forgetting this
        rebase makes EVERY path uncovered, which passes the negatives below for a reason that
        has nothing to do with the harness half. The live comparison caught exactly that."""
        return self.preview_text.replace(self.shown_root, self.done_root)

    def test_f8a_the_preview_names_every_path_the_write_creates(self):
        """THE load-bearing test. Not a snapshot of the preview and not a count typed into a
        test: the file list comes off the disk the write just made."""
        self.assertEqual([], uncovered_paths(self._rebased(), self.done_root, self.written))

    def test_f8a_the_counts_the_preview_claims_are_the_counts_that_land(self):
        """The renderer's numbers come from the plan; the apply installs from that same plan. This
        is what makes that true rather than believed — and it is what caught the plan naming 16
        Agent Skills while 26 directories landed."""
        self.assertEqual(written_counts(self.done_root), declared_counts(self.preview_text))

    def test_f8a_the_declared_counts_are_not_empty(self):
        """§7j — the comparison above passes vacuously if the preview declares no counts at all
        (a renderer reworded to drop "Will write N commands" escapes it silently)."""
        self.assertEqual({"commands", "Agent Skills"}, set(declared_counts(self.preview_text)))
        self.assertTrue(all(n > 0 for n in declared_counts(self.preview_text).values()))

    def test_f8a_the_coverage_grader_catches_a_preview_with_the_harness_half_stripped(self):
        """§7i — the grader, shown failing. THE OLD BEHAVIOUR is the planted offender: a preview
        holding only the `.mokata/` half. If this passes, the test above proves nothing."""
        manifest_only = self._rebased().split("`--yes` is consent")[0]
        missing = uncovered_paths(manifest_only, self.done_root, self.written)
        self.assertTrue(missing, "the grader accepted the exact defect this stage closes")
        # EXACTLY the harness half, and nothing else: the `.mokata/` half of the old preview was
        # honest, so a grader reporting it as missing too would be complaining about the rebase
        # rather than about the defect — and would pass no matter what the preview said.
        self.assertEqual([], [m for m in missing if m.startswith(".mokata")], missing)
        self.assertGreater(len(missing), 60, missing)

    def test_f8a_the_coverage_grader_is_not_satisfied_by_naming_the_root(self):
        """The ancestor rule stops one line from covering the tree. A 'preview' that says only
        `<root>` must not grade as complete."""
        self.assertTrue(uncovered_paths(self.done_root, self.done_root, self.written))

    def test_f8a_the_count_grader_catches_an_understated_count(self):
        """§7i — the count half, shown failing, over the under-report that was actually there."""
        understated = self.preview_text.replace("Will write 26 Agent Skills",
                                                "Will write 16 Agent Skills")
        self.assertNotEqual(self.preview_text, understated,
                            "re-anchor: the rendered skills line moved")
        self.assertNotEqual(written_counts(self.done_root), declared_counts(understated))


# ======================================================================================
# THE GUARD. Without this the next flag added to the wiring re-opens the defect in silence.
# ======================================================================================
class TheDivergenceGuard(unittest.TestCase):
    """One predicate decides whether a run wires; one keyword set decides with what. Both are
    graded at RUNTIME — what the two call sites actually pass — so a second spelling introduced
    anywhere between here and `setup_harness` is caught, not just one in a line this test reads."""

    def _wire_kwargs(self, argv, root, home):
        """The keywords `_wire_or_disclose` really hands `setup_harness` on a `--yes` init."""
        seen = {}

        def _fake(**kw):
            seen.update(kw)
            return harness_setup.SetupResult(touched=[], plan=None)

        with mock.patch.object(SETUP, "setup_harness", _fake):
            _preview(argv, root, home)
        return seen

    def _plan_kwargs(self, argv, root, home):
        """The keywords the preview really hands `plan_setup`."""
        seen = {}
        real = SETUP.plan_setup

        def _spy(**kw):
            seen.update(kw)
            return real(**kw)

        with mock.patch.object(SETUP, "plan_setup", _spy):
            _preview(argv, root, home)
        return seen

    def test_f8a_the_preview_asks_for_exactly_the_wiring_the_write_performs(self):
        """THE guard. Compared over `plan_setup`'s OWN signature, so the exemption for
        `assume_yes`/`force` is derived rather than declared: a keyword `plan_setup` could have
        taken cannot slip past by being called apply-only."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            d = os.path.realpath(d)
            base = ["init", "--profile", "standard", "--yes"]
            wired = self._wire_kwargs(base, d, home)
            planned = self._plan_kwargs(base + ["--preview"], d, home)
        self.assertTrue(wired, "no wiring happened — re-anchor this guard")
        self.assertTrue(planned, "the preview planned no wiring — the defect is back")
        shared = set(inspect.signature(plan_setup).parameters)
        self.assertEqual({k: v for k, v in wired.items() if k in shared}, planned)

    def test_f8a_an_apply_only_keyword_is_one_plan_setup_cannot_take(self):
        """The other half of that derivation. If `plan_setup` ever grows an `assume_yes`, the
        comparison above starts covering it — and this test is what says so out loud."""
        params = set(inspect.signature(plan_setup).parameters)
        for apply_only in ("assume_yes", "force"):
            self.assertNotIn(apply_only, params)

    def test_f8a_the_guard_catches_a_flag_added_to_one_side_only(self):
        """§7i — the guard, shown failing. This is the exact future edit it exists for: someone
        teaches the `--yes` wiring to pass `with_hooks=False` and never touches the preview."""
        shared = set(inspect.signature(plan_setup).parameters)
        wired = {"harness": "claude", "root": "/r", "scope": "project", "profile": "standard",
                 "assume_yes": True, "force": False, "with_hooks": False}
        planned = {"harness": "claude", "root": "/r", "scope": "project", "profile": "standard"}
        self.assertIn("with_hooks", shared, "re-anchor: plan_setup no longer takes with_hooks")
        self.assertNotEqual({k: v for k, v in wired.items() if k in shared}, planned)

    def test_f8a_one_predicate_decides_both_surfaces(self):
        """Not "both read `args.yes`" — both read the SAME function. Forced to False, the preview
        must drop the harness half AND the executor must wire nothing; forced to True, both must
        act. A second copy of the condition survives the first half and fails here."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            d = os.path.realpath(d)
            with mock.patch.object(SETUP, "init_wires_harness", lambda _a: False):
                _rc, out = _preview(["init", "--profile", "standard", "--preview", "--yes"],
                                    d, home)
                self.assertIn("Harness wiring: NOT part of this run.", out)
                with mock.patch.object(SETUP, "setup_harness") as spy:
                    _preview(["init", "--profile", "standard", "--yes"], d, home)
                spy.assert_not_called()

        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            d = os.path.realpath(d)
            with mock.patch.object(SETUP, "init_wires_harness", lambda _a: True):
                _rc, out = _preview(["init", "--profile", "standard", "--preview"], d, home)
                self.assertIn(os.path.join(d, ".claude", "commands"), out)

    def test_f8a_an_unplannable_harness_is_reported_and_never_dropped(self):
        """Degrade-clean, but LOUD. `plan_setup` raises `SetupError` when the command templates
        are missing (pip-without-clone). Swallowing that would leave a preview whose silence
        reads as "nothing is written here" — the defect, arriving through its own fix."""
        def _boom(**_kw):
            raise harness_setup.SetupError("command templates not found")

        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            d = os.path.realpath(d)
            with mock.patch.object(SETUP, "plan_setup", _boom):
                rc, out = _preview(["init", "--profile", "standard", "--preview", "--yes"],
                                   d, home)
        self.assertEqual(rc, 0, "a preview must not fail because one half cannot be planned")
        self.assertIn("could NOT plan it here", out)
        self.assertIn("command templates not found", out)
        self.assertNotIn("Harness wiring: NOT part of this run.", out,
                         "'cannot plan' and 'wires nothing' are different facts (doc 85 §7g)")


# ======================================================================================
# The template previews the command it then runs.
# ======================================================================================
class TheTemplatePreviewsTheRunItRuns(unittest.TestCase):

    def setUp(self):
        with open(TEMPLATE, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_f8a_the_template_previews_the_argv_it_executes(self):
        """THE actual invariant. `/mokata:init` previewed `--preview` and ran `--yes`, which are
        two different runs — and after this stage that is not a wording problem but a wrong
        preview, because `--preview` alone correctly declines to describe wiring."""
        self.assertEqual([], template_drift(self.text))

    def test_f8a_the_template_still_runs_two_inits_at_all(self):
        """§7j — `template_drift` is silent over a template with no invocations left to compare.
        The corpus is pinned as non-empty before its verdict is trusted."""
        runs = init_invocations(self.text)
        self.assertEqual(2, len(runs), runs)

    def test_f8a_the_invariant_catches_the_drift_it_was_written_for(self):
        """§7i — the grader, shown failing on the tree as it stood at `a7b5402`: previewed
        `--preview`, ran `--yes`."""
        before = self.text.replace('init --profile <profile> --yes --preview --path .',
                                   'init --profile <profile> --preview --path .')
        self.assertNotEqual(self.text, before, "re-anchor: the previewed command moved")
        self.assertTrue(template_drift(before))

    def test_f8a_the_invariant_catches_a_flag_added_to_the_run_alone(self):
        """The forward-looking half — `--force` reaching step 5 and not step 3."""
        drifted = self.text.replace('init --profile <profile> --yes --path .',
                                    'init --profile <profile> --yes --force --path .')
        self.assertNotEqual(self.text, drifted, "re-anchor: the executed command moved")
        problems = template_drift(drifted)
        self.assertTrue(problems)
        self.assertIn("--force", " ".join(problems))

    def test_f8a_the_template_warns_that_the_flag_must_not_be_dropped(self):
        """The template is read by a model, not executed by a shell. The invariant above pins the
        argv; this pins the sentence that stops the next editor from 'tidying' the flag away."""
        self.assertIn("must not be dropped", self.text)


if __name__ == "__main__":
    unittest.main()
