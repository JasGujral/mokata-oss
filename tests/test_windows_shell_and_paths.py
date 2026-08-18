"""THE WINDOWS CLASSES, GUARDED OVER A WALKED CORPUS.

WHAT THIS FILE USED TO BE, AND WHY IT CHANGED. It was written at the 0.0.18 cut halt to close the
two Windows causes as CLASSES rather than as nineteen repairs. It was graded against a planted
offender and a near-miss it must acquit — and **two cause-B sites still walked past it onto the
mirror**, where they reded as 2 of the 10 failures on run 32094654167.

The reason is not that the detectors were wrong. It is that the CORPUS was a list:

    cause A   `os.listdir(TESTS_DIR)` — the top level of `tests/` only. Not `tests/integration/`,
              not `scripts/`, and NOT `src/` — so a PRODUCT defect was outside the corpus by
              construction. The release-check refusal that welded `"%s/src"` onto a native path
              and shipped `D:\\a\\mokata-oss\\mokata-oss/src` to users lived there.
    cause B   two corpus builders, named by module and function. Every other builder in the tree
              was unexamined, including the two in `test_stage9_removal_release.py` that reded.

"A guard's corpus is a claim" — the sixth instance in 0.0.18, and the first INSIDE a guard written
to close the class. So the corpus now comes from `_windows_portability.walked_sources`, which
WALKS `tests/`, `src/` and `scripts/` (721 files, against the 434 this file used to read), and the
detectors live beside it as pure functions so each can be handed a synthetic offender.

⚠ WHAT A WALKED CORPUS DOES NOT FIX, stated here because the round proved it. Causes A and B
account for five of the ten Windows failures. The other five are three causes NOBODY HAD NAMED:
an unpinned `platform=` fixture, a POSIX-shell-syntax assertion under `cmd.exe`, and an
unresolved temp path compared against a product string built from a resolved one. A perfect
sweep for the causes you know about is still blind to the ones you do not, and widening a corpus
does not widen a taxonomy. Those three are guarded in their own files and filed in doc 84.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import os
import subprocess
import textwrap

import unittest

import _support
import _windows_portability as WP
from _support import as_posix, posix_rel

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)


def _sources():
    """The WALKED corpus — the filesystem, not a list."""
    return WP.walked_sources(ROOT)


def _outstanding(detector, sites):
    """Sites the detector found that no exemption row clears."""
    kept, _excused = WP.apply_exemptions(detector, sites)
    return kept


# =================================================================================================
# THE CORPUS ITSELF — the property that failed last time
# =================================================================================================

class TestTheCorpusIsWalkedAndNotAList(unittest.TestCase):
    """★ THE REGRESSION THIS ROUND EXISTS FOR. Every assertion below is about the corpus, because
    every detector in this file is only as good as the set of files it is pointed at."""

    def test_the_corpus_reaches_src_and_scripts_and_not_only_tests(self):
        """The old corpus could not have found the release-check defect: it was in `src/`."""
        sources = _sources()
        roots = {name.split("/", 1)[0] for name in sources}
        self.assertEqual({"tests", "src", "scripts"}, roots,
                         "the sweep no longer reaches every tree a Windows defect can live in")
        self.assertIn("src/mokata/packaging.py", sources,
                      "the module that shipped `%s/src` to users is outside the corpus again")

    def test_the_corpus_reaches_NESTED_directories_not_just_a_top_level_listing(self):
        """`os.listdir` is not `os.walk`, and the difference is every file in a subdirectory —
        `tests/integration/`, all of `src/mokata/knowledge/`, `src/mokata/cli_commands/`."""
        sources = _sources()
        nested = [n for n in sources if n.count("/") >= 2]
        self.assertGreater(len(nested), 200,
                           "the corpus looks like a flat listing again, not a walk")
        for name in ("tests/integration", "src/mokata/knowledge", "src/mokata/cli_commands"):
            self.assertTrue(any(n.startswith(name + "/") for n in sources),
                            "%s is not in the walked corpus" % name)

    def test_the_corpus_is_bigger_than_the_list_it_replaced(self):
        """A vacuity check with a NUMBER in it: the old corpus was the top level of `tests/`, and
        if the walk ever silently collapses back to that, this is what says so."""
        sources = _sources()
        flat_tests = [n for n in sources if n.startswith("tests/") and n.count("/") == 1]
        self.assertGreater(len(sources), len(flat_tests) + 250,
                           "the walked corpus (%d) has collapsed toward the old flat listing (%d)"
                           % (len(sources), len(flat_tests)))


# =================================================================================================
# CAUSE A — the shell a test runs is the shell PATH names
# =================================================================================================

class TestNoSpawnResolvesThroughSystem32(unittest.TestCase):

    def test_the_sweep_convicts_a_planted_offender_and_acquits_the_fixed_form(self):
        """★ §7i, and it is not decoration here: on a POSIX host the real tree passes this sweep
        whether the sweep works or not, because a bare "bash" and a resolved one behave the same.
        The only way to know the sweep grades anything is to hand it the defect."""
        offender = 'subprocess.run(["bash", SCRIPT, directory])\n'
        got = WP.bare_argv0_sites({"planted.py": offender})
        self.assertEqual((("planted.py", 1, "bash", True),), got)

        self.assertEqual((("p.py", 1, "bash", True),),
                         WP.bare_argv0_sites({"p.py": 'subprocess.Popen(["bash", "-c", body])\n'}))

        fixed = 'subprocess.run(bash_argv(SCRIPT, directory))\n'
        self.assertEqual((), WP.bare_argv0_sites({"planted.py": fixed}))

        # the near-misses that must NOT be convicted
        self.assertEqual((), WP.bare_argv0_sites({"p.py": 'hook = {"shell": "bash"}\n'}))
        # "bash" AWAY FROM argv[0] must not convict as bash: argv[0] is what CreateProcess
        # resolves, and everything after it is an argument the shell reads, not a program.
        away = WP.bare_argv0_sites({"p.py": 'subprocess.run(["sh", "bash", "zsh"])\n'})
        self.assertEqual([("p.py", 1, "sh", False)], list(away))
        self.assertEqual((), WP.bare_argv0_sites(
            {"p.py": 'M = [t for t in ("bash", "rsync") if shutil.which(t) is None]\n'}))

    def test_a_bare_run_that_is_not_subprocess_run_is_not_convicted(self):
        """★ THE PRECISION FIX, and it is not a nicety. The previous sweep matched a call named
        `run`/`Popen` by NAME, so it convicted `run(["baseline", "--path", d])` and
        `run(["config", "--get", …])` — local CLI-driver helpers that spawn no process at all.
        Four false convictions in a list whose whole purpose is "go and change these"."""
        self.assertEqual((), WP.bare_argv0_sites(
            {"p.py": 'rc, out = run(["baseline", "--cmd", "true", "--path", d])\n'}))
        self.assertEqual((), WP.bare_argv0_sites(
            {"p.py": 'rc, out = run(["config", "--get", "remote.origin.url"], root)\n'}))
        # ...while the module-qualified form still is
        self.assertEqual(1, len(WP.bare_argv0_sites(
            {"p.py": 'subprocess.run(["bash", "-c", x])\n'})))

    def test_the_shadow_flag_separates_a_real_collision_from_PATH_order_alone(self):
        """THREE states, not two (§7g). `bash` is shadowed by System32 and cost the cut twenty
        failures; `git` is a bare name too and is shadowed by nothing. Collapsing them would
        demand thirty-three repairs for one defect and teach people to switch the guard off."""
        shadowed = WP.bare_argv0_sites({"p.py": 'subprocess.run(["bash", "-c", x])\n'})
        plain = WP.bare_argv0_sites({"p.py": 'subprocess.run(["git", "status"])\n'})
        self.assertEqual([True], [s[3] for s in shadowed])
        self.assertEqual([False], [s[3] for s in plain])

    def test_the_shadowed_class_sweep_convicts_through_every_hop_it_claims_to_follow(self):
        """★ THE HOP THAT WAS MISSING, graded. Round 2's first answer to "what does this tree
        spawn by bare name?" was `{git, gh, grep}` — and it was an artefact of a detector that
        only understood an argv written inline at the spawn. `src/mokata/notify.py` builds argv in
        helpers and returns them, so five more names were invisible."""
        inline = 'subprocess.run(["tar", "-xf", p])\n'
        bound = 'argv = ["tar", "-xf", p]\nsubprocess.run(argv)\n'
        helper = ('def _argv():\n    return ["tar", "-xf", p]\n'
                  'subprocess.run(_argv())\n')
        crossfn = ('def _argv():\n    return ["tar", "-xf", p]\n'
                   'a = _argv()\nsubprocess.run(a)\n')
        for label, source in (("inline", inline), ("bound name", bound),
                              ("helper return", helper), ("cross-function", crossfn)):
            with self.subTest(hop=label):
                self.assertEqual(1, len(WP.shadowed_name_argv_sites({"p.py": source})),
                                 "the %s hop is not followed" % label)

    def test_the_belt_and_braces_tier_covers_the_hop_dataflow_CANNOT_follow(self):
        """`notify.py`'s audio arm reaches its spawn through an injected `runner=` seam, and no
        static pass follows an injected callable. Tier 2 needs no dataflow — and must still
        acquit a PRESENCE check, which is the one honest reason to put program names in a
        literal, and a correction the cause-A sweep already had to make once."""
        self.assertEqual(1, len(WP.shadowed_name_anywhere_sites(
            {"p.py": 'def _audio_argv(p):\n    return ["tar", "-i", SOUND]\n'})))
        self.assertEqual((), WP.shadowed_name_anywhere_sites(
            {"p.py": 'M = [t for t in ("bash", "rsync", "git") if shutil.which(t) is None]\n'}))
        self.assertEqual((), WP.shadowed_name_anywhere_sites(
            {"p.py": 'roles = ("route", "handler", "endpoint")\n'}))

    def test_no_argv_literal_anywhere_is_headed_by_a_shadowed_name(self):
        """TIER 2 over the walked tree — the class, closed without needing dataflow."""
        sites = WP.shadowed_name_anywhere_sites(_sources())
        self.assertEqual(
            [], list(sites),
            "a System32-shadowed program name heads an argv literal; on Windows CreateProcess "
            "reaches System32 before PATH:\n%s" % "\n".join("  %s:%d  %s" % s for s in sites))

    def test_the_full_spawned_bare_name_set_is_what_the_review_was_told(self):
        """★ 2d — THE EMPTY RESULT, PINNED. The intersection of "names this tree spawns bare" with
        "names System32 shadows" is EMPTY, and an empty result that nothing preserves is a fact
        with a shelf life. This is what preserves it: the SET is asserted, so a future spawn site
        introducing any new bare name reds here and its author has to say which it is — and if it
        is shadowed, the two assertions above red as well."""
        names = WP.all_spawned_bare_names(_sources())
        self.assertEqual({"git", "gh", "grep", "notify-send", "osascript"}, set(names),
                         "the set of bare program names this tree spawns has changed; if the new "
                         "one is shadowed by System32 it must be resolved through shutil.which "
                         "(see `_support.bash_argv`), and if it is not, add it here with a reason")
        for program in names:
            with self.subTest(program=program):
                self.assertFalse(WP.is_shadowed(program),
                                 "%s is shadowed by System32 — resolve it" % program)

    @unittest.skipUnless(os.name == "nt", "the real System32 exists only on Windows")
    def test_the_host_shadows_NOTHING_this_tree_spawns_that_the_declaration_omits(self):
        """⭐ THE DECLARED THRESHOLD, CHECKED WHERE IT CAN BE — AND IN ONE DIRECTION ONLY.

        `SYSTEM32_SHADOWED` is sourced from Microsoft's A-Z Windows commands page, and that page
        **does not list `bash`** — the one name whose presence in System32 cost the 0.0.18 cut
        twenty failures. So the declared set is a lower bound with unknown slack, and a threshold
        nothing ever checks is no better than a corpus nothing ever walks.

        ⚠ BUT THE TWO DIRECTIONS ARE NOT THE SAME FACT, and the first version of this test failed
        on all three Windows legs of run 32117189879 by asserting they were. It asked for EQUALITY
        between a fixed declaration and a host-variable directory, and reported
        `['telnet', 'tftp']` — both of which are OPTIONAL WINDOWS FEATURES, absent from a default
        `windows-latest` image and present the moment a user turns the feature on. An equality
        between a declaration written once and a directory whose contents vary per host can never
        be stable, and a guard that reds on a machine being configured differently is a guard
        that gets skipped.

        So the directions are separated by what each one COSTS:

          * **the host shadows a name the declaration omits** — this is the `bash` case, and it
            is the only direction that can make the sweep say "safe" about a spawn that
            CreateProcess will hijack. It RED-FAILS, here, scoped to the names this tree actually
            spawns (`is_shadowed` consults the host first, so the sweep is already right on
            Windows; what this catches is the DECLARATION going stale for POSIX runs, where the
            declared set is all there is).

          * **the declaration names something this host does not have** — the set is over-broad
            for this machine. It convicts nothing that exists and hides nothing that does; it is
            worth KNOWING and is reported by `test_the_declared_set_reports_its_drift_from_this_
            host`, which never fails. Over-declaring `telnet` costs a reader one unnecessary line;
            under-declaring `bash` cost the cut."""
        spawned = WP.all_spawned_bare_names(_sources())
        undeclared = sorted(n for n in spawned
                            if WP.shadowed_on_host(n)
                            and os.path.splitext(n)[0].lower() not in WP.SYSTEM32_SHADOWED)
        self.assertEqual([], undeclared,
                         "this host's System32 shadows names the declared set omits, and every "
                         "POSIX run of this sweep grades them safe: %r" % (undeclared,))

    @unittest.skipUnless(os.name == "nt", "the real System32 exists only on Windows")
    def test_the_declared_set_reports_its_drift_from_this_host(self):
        """The other direction, REPORTED and never failed — §7g's three states applied to a
        threshold rather than to an answer. `absent-on-this-host` is not `wrong`: `telnet` and
        `tftp` ship as optional features, so the same declaration is over-broad on a CI image and
        exact on a developer's box with them enabled. Printing it keeps the drift visible without
        making the suite's colour depend on which Windows features a machine happens to have."""
        absent = sorted(n for n in WP.SYSTEM32_SHADOWED if not WP.shadowed_on_host(n))
        present = len(WP.SYSTEM32_SHADOWED) - len(absent)
        print("\nSYSTEM32 SHADOW SET vs %s — %d of %d declared names are present here; "
              "absent (optional features / newer removals): %r"
              % (WP.system32_dir(), present, len(WP.SYSTEM32_SHADOWED), absent))
        self.assertTrue(present, "not one declared name is in System32 — the probe is broken, "
                                 "not the declaration")

    def test_the_host_check_reports_CANNOT_ANSWER_rather_than_NOT_SHADOWED_off_windows(self):
        """§7g, and it is the difference between a real green and a green from a machine that has
        never seen the hazard. Off Windows `shadowed_on_host` returns None — three states, not
        two — so a POSIX box cannot silently grade a Windows-only question as safe."""
        if os.name == "nt":
            self.assertIsNotNone(WP.shadowed_on_host("bash"))
        else:
            self.assertIsNone(WP.system32_dir())
            self.assertIsNone(WP.shadowed_on_host("bash"),
                              "a POSIX host must say it cannot answer, not answer 'no'")
            self.assertTrue(WP.is_shadowed("bash"),
                            "off Windows the declared set is the fallback and must still convict")

    def test_no_spawn_in_the_tree_resolves_a_shadowed_name_through_CreateProcess(self):
        """The class, closed, over the WALKED corpus."""
        sites = [s for s in WP.bare_argv0_sites(_sources()) if s[3]]
        self.assertEqual(
            [], sites,
            "these argv lists resolve a name Windows finds in System32 BEFORE PATH — on a runner "
            "that means the WSL launcher, not the tool. Resolve through `shutil.which`:\n%s"
            % "\n".join("  %s:%d  %s" % (s[0], s[1], s[2]) for s in sites))

    def test_bash_argv_refuses_rather_than_falling_back_to_the_bare_name(self):
        """A resolver that degrades to the bare name when `which` finds nothing is the original
        defect with a helper wrapped round it. Absent bash is a REFUSAL the caller must guard for,
        and it is graded by DRIVING that state rather than by reading the source for a word."""
        from unittest import mock
        with mock.patch.object(_support, "BASH", None):
            with self.assertRaises(RuntimeError) as caught:
                _support.bash_argv("-c", "true")
        self.assertIn("un-run", str(caught.exception),
                      "an absent bash must read as un-run, not as a pass (doc 85 §7g)")

    @unittest.skipUnless(_support.BASH, _support.NO_BASH)
    def test_bash_argv_yields_an_absolute_interpreter_and_actually_runs(self):
        """RUN, not read — the resolver's whole claim is that what it names can be executed."""
        argv = _support.bash_argv("-c", "printf ok")
        self.assertTrue(os.path.isabs(argv[0]), "argv[0] is not an absolute path: %r" % argv[0])
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("ok", proc.stdout.strip())


# =================================================================================================
# A' — A SHELL SUBPROCESS MUST SAY WHAT ITS STDIN IS  (the round-2 wedge)
# =================================================================================================

class TestEveryShellSpawnStatesItsStdin(unittest.TestCase):
    """WHAT THIS PINS, and the incident is worth stating because the fix CAUSED it.

    Repairing cause A turned every bash-driven test from a subprocess that failed in
    milliseconds (WSL, exit 1, nothing read) into one that actually runs. A shell that actually
    runs can read fd 0 — and on run 32094654167 the `windows · py3.12 · jsonschema=absent` leg's
    unit-suite step sat at 54m27s against ~25m for the two legs beside it, and was cancelled
    rather than finishing. A faster failure had been hiding an unbounded wait.

    The precedent was already in the tree and said so: `release.sh`'s `run_test_preflight` runs
    the suite `< /dev/null`, and its comment names the mechanism — TTY-aware prompt tests read a
    live stdin and hang. Nothing else in the tree took the same precaution."""

    def test_the_sweep_convicts_a_planted_offender_and_acquits_every_stated_form(self):
        """★ §7i again, and it matters MORE here than for cause A: on a POSIX CI box stdin is
        usually already `/dev/null`, so the offending and the fixed form behave identically and
        the tree would pass this sweep whether it worked or not."""
        offender = 'subprocess.run(bash_argv("-c", body), capture_output=True)\n'
        self.assertEqual(1, len(WP.shell_spawn_without_stdin_sites({"p.py": offender})))

        for stated in ('subprocess.run(bash_argv("-c", b), stdin=subprocess.DEVNULL)\n',
                       'subprocess.run(bash_argv("-c", b), stdin=subprocess.PIPE)\n',
                       # `input=` IS an answer — subprocess opens the pipe, writes, closes. The
                       # two kwargs are mutually exclusive, so demanding `stdin=` beside it would
                       # be demanding a TypeError.
                       'subprocess.run(bash_argv("-c", b), input=payload)\n'):
            self.assertEqual((), WP.shell_spawn_without_stdin_sites({"p.py": stated}),
                             "a stated stdin was convicted: %s" % stated.strip())

    def test_a_non_shell_spawn_is_not_convicted(self):
        """Scoped to SHELLS. A `git` or a `python -m` argv does not read fd 0 looking for a
        human, and convicting all sixty-nine unstated spawns in the tree would bury the twelve
        that can actually wedge a runner."""
        self.assertEqual((), WP.shell_spawn_without_stdin_sites(
            {"p.py": 'subprocess.run(["git", "status"], capture_output=True)\n'}))
        self.assertEqual((), WP.shell_spawn_without_stdin_sites(
            {"p.py": 'subprocess.run([sys.executable, "-m", "mokata"], capture_output=True)\n'}))

    def test_shell_True_is_a_shell_however_it_is_spelled(self):
        self.assertEqual(1, len(WP.shell_spawn_without_stdin_sites(
            {"p.py": 'subprocess.run(cmd, shell=True, timeout=5)\n'})))
        self.assertEqual((), WP.shell_spawn_without_stdin_sites(
            {"p.py": 'subprocess.run(cmd, shell=True, stdin=subprocess.DEVNULL)\n'}))

    def test_an_unreadable_answer_is_REPORTED_and_not_acquitted(self):
        """§7g. `**kwargs` may carry `input=`, and the AST cannot see in. Three states, not two:
        stated / unstated / undecidable — and the undecidable one is reported so a human reads
        the call site, which is how `hook_execution_check.py` came to be cleared by its row in
        the exemption table rather than by the sweep's silence."""
        sites = WP.shell_spawn_without_stdin_sites(
            {"p.py": 'subprocess.run([shell, "-c", argv], **kwargs)\n'})
        self.assertEqual(1, len(sites))
        self.assertIn("undecidable", sites[0][2])

    def test_no_shell_spawn_in_the_tree_leaves_its_stdin_unstated(self):
        sites = _outstanding("shell_stdin", WP.shell_spawn_without_stdin_sites(_sources()))
        self.assertEqual(
            [], list(sites),
            "these spawn a shell with INHERITED stdin — the shape that wedged the Windows leg "
            "for 54 minutes. Pass `stdin=subprocess.DEVNULL`:\n%s"
            % "\n".join("  %s:%d  [%s]" % s for s in sites))


# =================================================================================================
# CAUSE B — a repo-relative path is a NAME, and names are spelled with `/`
# =================================================================================================

class TestRepoRelativePathsAreSpelledPosix(unittest.TestCase):

    def test_as_posix_converts_a_windows_separator_ON_A_POSIX_HOST(self):
        """★ THE POINT OF THE `sep` PARAMETER. Without it this branch executes only on Windows —
        i.e. only where the failure already happened — and the guard would be another line that
        looks like coverage and is not."""
        self.assertEqual("mokata/deprecation.py", as_posix("mokata\\deprecation.py", sep="\\"))
        self.assertEqual("docs/developer-guide.md", as_posix("docs\\developer-guide.md", sep="\\"))

    def test_as_posix_leaves_a_posix_path_alone(self):
        """The other half: a separator that is already `/` must not be touched, or a path with a
        legitimate backslash in a FILENAME would be silently rewritten."""
        self.assertEqual("mokata/deprecation.py", as_posix("mokata/deprecation.py", sep="/"))
        self.assertEqual("a\\b", as_posix("a\\b", sep="/"))

    def test_posix_rel_answers_a_name_not_a_filesystem_path(self):
        got = posix_rel(os.path.join(ROOT, "src", "mokata", "notify.py"),
                        os.path.join(ROOT, "src"))
        self.assertEqual("mokata/notify.py", got)
        self.assertNotIn("\\", got)

    def test_the_product_side_twin_exists_and_agrees(self):
        """`posix_rel` is the test-side twin of `repo_paths.name_of`, and they must not drift: a
        test cannot import product code to CHECK product code, which is why there are two.

        ⚠ THE TWIN CHANGED SHAPE AND THAT IS THE POINT. It used to be `repo_walk.posix_name(rel)`
        — a converter over an ALREADY-RELATIVE string, i.e. a step a caller could remember or
        forget. Three sweeps failed to converge on it. `name_of(path, root)` does the
        relativisation and the spelling in one call, matching what `posix_rel` always did, so the
        two sides are now the same SHAPE and not merely the same output."""
        from mokata.repo_paths import name_of
        for path, root in (
                (os.path.join(ROOT, "src", "mokata", "notify.py"), os.path.join(ROOT, "src")),
                (os.path.join(ROOT, "tests", "_support.py"), ROOT)):
            self.assertEqual(posix_rel(path, root), name_of(path, root))
            self.assertNotIn("\\", name_of(path, root))

    # --- B.1 — a relpath used as an identity -----------------------------------------------
    def test_the_relpath_sweep_convicts_a_planted_builder_and_acquits_the_fixed_one(self):
        offender = ('def corpus():\n'
                    '    out = {}\n'
                    '    for path in files:\n'
                    '        rel = os.path.relpath(path, ROOT)\n'
                    '        out[rel] = read(path)\n'
                    '    return out\n')
        self.assertEqual(1, len(WP.unwrapped_relpath_sites({"p.py": offender})),
                         "the one-hop binding `rel = relpath(...)` then `out[rel]` walked past")

        fixed = offender.replace("os.path.relpath(path, ROOT)", "posix_rel(path, ROOT)")
        self.assertEqual((), WP.unwrapped_relpath_sites({"p.py": fixed}))

    def test_a_relpath_that_is_used_as_a_PATH_is_not_convicted(self):
        """The narrowing, graded. A relpath handed to `open()` or printed is a PATH, and a
        backslash there is not merely acceptable — it is correct."""
        self.assertEqual((), WP.unwrapped_relpath_sites(
            {"p.py": 'print("skipping %s" % os.path.relpath(ab, root))\n'}))
        self.assertEqual((), WP.unwrapped_relpath_sites(
            {"p.py": 'with open(os.path.relpath(ab, root)) as fh:\n    body = fh.read()\n'}))

    def test_no_relpath_in_the_tree_becomes_an_identity_without_conversion(self):
        sites = _outstanding("relpath_identity", WP.unwrapped_relpath_sites(_sources()))
        self.assertEqual(
            [], list(sites),
            "these build a repo-relative NAME with the OS separator and then match on it; on "
            "Windows they stop matching every `/`-spelled declaration:\n%s"
            % "\n".join("  %s:%d  %s" % s for s in sites))

    # --- B.2 — a `/` welded onto an absolute path --------------------------------------------
    def test_the_welded_slash_sweep_convicts_all_three_spellings(self):
        for source in ('cmd = f"PYTHONPATH={root}/src python -m mokata"\n',
                       'cmd = \'PYTHONPATH="%s/src" python\' % (root,)\n',
                       'cmd = root + "/src"\n'):
            self.assertEqual(1, len(WP.welded_slash_sites({"p.py": source})),
                             "not convicted: %s" % source.strip())

    def test_the_welded_slash_sweep_acquits_a_url_route_and_a_git_revspec(self):
        """★ THE NARROWING THAT KEEPS THIS USABLE. `repos/{repo}/branches/{branch}` is a GitHub
        API route and `HEAD:{dir}/{file}` is a git revision spec — both `/`-spelled by their OWN
        grammar, not the filesystem's. The first version convicted twenty-two sites, twenty-one
        of which needed no change; a guard answered with twenty-one exemptions is a guard about
        to be deleted."""
        self.assertEqual((), WP.welded_slash_sites(
            {"p.py": 'path = f"repos/{repo}/branches/{branch}/protection"\n'}))
        self.assertEqual((), WP.welded_slash_sites(
            {"p.py": 'rev = f"HEAD:{MOKATA_DIR}/{MANIFEST_FILENAME}"\n'}))
        # ...and the one that DID matter is still convicted
        self.assertEqual(1, len(WP.welded_slash_sites(
            {"p.py": 'r = \'PYTHONPATH="%s/src" python3\' % (self.root,)\n'})))

    def test_no_shipped_string_welds_a_slash_onto_an_absolute_path(self):
        sites = _outstanding("welded_slash", WP.welded_slash_sites(_sources()))
        self.assertEqual(
            [], list(sites),
            "these build `<native path>/<name>`, which on Windows is a path with BOTH "
            "separators — and one of them was the command a refused release-check told a user "
            "to run:\n%s" % "\n".join("  %s:%d  [%s] %s" % s for s in sites))

    # --- B.3 — one side converted, the other not ---------------------------------------------
    def test_the_one_sided_sweep_convicts_a_half_converted_comparison(self):
        """★ THE DETECTOR THAT CATCHES THE PREVIOUS ROUND'S OWN REPAIR. `test_release_asset_set`
        was converted to pass `as_posix(directory)` INTO the shell script — so the script
        answered in `/` — while the expectation it is compared against stayed `os.path.join`.
        Two of the ten Windows failures on run 32094654167 are exactly that, in a file the last
        round had already edited for this very class."""
        offender = ('import _support\n'
                    'def t(self):\n'
                    '    out = run(_support.as_posix(self.dist))\n'
                    '    self.assertEqual(out, [os.path.join(self.dist, n) for n in names])\n')
        self.assertEqual(1, len(WP.one_sided_posix_sites({"p.py": offender})))

        fixed = offender.replace("[os.path.join(self.dist, n)",
                                 "[_support.as_posix(os.path.join(self.dist, n))")
        self.assertEqual((), WP.one_sided_posix_sites({"p.py": fixed}))

    def test_a_module_that_makes_no_spelling_claim_is_not_convicted(self):
        """Scoped to modules that use a posix wrapper ON PURPOSE. A module with no `as_posix`
        anywhere has not claimed anything about spelling, and convicting its every
        `os.path.join` would produce hundreds of sites nobody can act on."""
        self.assertEqual((), WP.one_sided_posix_sites(
            {"p.py": 'def t(self):\n    self.assertEqual(a, os.path.join(d, "x"))\n'}))

    def test_a_join_consumed_as_a_path_is_not_convicted(self):
        self.assertEqual((), WP.one_sided_posix_sites(
            {"p.py": 'import _support\n'
                     'def t(self):\n'
                     '    _support.as_posix(x)\n'
                     '    self.assertTrue(os.path.exists(os.path.join(dest, "README.md")))\n'}))

    # ------------------------------------------------------ B.4 — the native-built EXPECTATION
    def test_the_native_expectation_sweep_convicts_a_literal_anchored_join_in_an_assertion(self):
        """★ THE DETECTOR FOR WHAT B.3 IS STRUCTURALLY BLIND TO, and the round it was written for.

        On run 32117189879 twelve of the fourteen Windows failures were this shape and B.3 found
        none of them, because B.3 asks *"does THIS MODULE convert one side and not the other?"*
        and the side that had moved was in ANOTHER module — the sweep converted the producers
        (`repo_walk`, `knowledge/index`, `_support.tree_snapshot`) and every consumer went on
        building its expectation with `os.path.join`. A module-scoped question cannot see a pair
        whose halves live in different files, so B.4 asks about the VALUE instead."""
        offender = ('def t(self):\n'
                    '    self.assertEqual([r.path for r in refs], [os.path.join("pkg", "mod.py")])\n')
        self.assertEqual(1, len(WP.native_name_expectation_sites({"p.py": offender})))

        fixed = offender.replace('[os.path.join("pkg", "mod.py")]', '["pkg/mod.py"]')
        self.assertEqual((), WP.native_name_expectation_sites({"p.py": fixed}))

    def test_a_join_anchored_to_a_REAL_LOCATION_is_not_convicted(self):
        """The whole rule, in one pair. `os.path.join(d, "pkg", "mod.py")` is rooted in a tempdir
        — a place on this disk — so it is a PATH and the OS spells its separator. Only a join
        rooted in a bare relative literal is anchored to nothing, and a value anchored to nothing
        cannot be opened: the one thing it can be is a name."""
        self.assertEqual((), WP.native_name_expectation_sites(
            {"p.py": 'def t(self):\n    self.assertEqual(a, os.path.join(d, "pkg", "mod.py"))\n'}))
        self.assertEqual((), WP.native_name_expectation_sites(
            {"p.py": 'def t(self):\n    self.assertEqual(a, os.path.join(self.root, "pkg"))\n'}))

    def test_a_name_builder_that_is_never_MATCHED_is_not_convicted(self):
        """Scoped to identity use, for B.1's reason. A literal-anchored join handed to `makedirs`
        under a cwd is a path being BUILT, and a backslash there is correct — the corpus holds 53
        literal-anchored joins and only 35 of them are ever matched against anything. Convicting
        the other 18 would answer the guard with exemptions, which is how a guard gets tuned off."""
        self.assertEqual((), WP.native_name_expectation_sites(
            {"p.py": 'def t(self):\n    os.makedirs(os.path.join("pkg", "sub"), exist_ok=True)\n'}))

    def test_the_declaration_is_followed_from_where_it_is_BOUND_to_where_it_is_MATCHED(self):
        """★ WITHOUT THIS, B.4 MISSES THE WHOLE OF GATE B. Both carve-outs that failed on Windows
        — `test_h6_mint_site._LEDGER` and `test_h1a_s2_s3_injection_pack._LEDGER_REL` — are
        declared once, at class or module level, and matched a hundred lines away. And one of them
        is matched with `startswith`, a comparison spelled as a method call."""
        bound = ('class C:\n'
                 '    _L = os.path.join(".mokata", "temp_local", "audit")\n'
                 '    def t(self):\n'
                 '        if rel.startswith(self._L):\n'
                 '            pass\n')
        self.assertEqual(1, len(WP.native_name_expectation_sites({"p.py": bound})))

    def test_a_binding_to_the_CONTENTS_of_a_file_named_by_a_join_is_not_convicted(self):
        """The precision this detector had to be given before it was true: following a binding
        blindly convicted `text = self._text(os.path.join("execmode", "orchestrator.py"))`, where
        `text` is the file's CONTENTS and a later assertion on it says nothing about spelling.
        A `join` is the bound value only when it IS the value."""
        self.assertEqual((), WP.native_name_expectation_sites(
            {"p.py": 'def t(self):\n'
                     '    text = self._text(os.path.join("execmode", "orchestrator.py"))\n'
                     '    self.assertIn("X", text)\n'}))

    def test_no_expectation_in_the_tree_is_BUILT_NATIVE_and_matched_as_a_name(self):
        sites = _outstanding("native_name_expectation",
                             WP.native_name_expectation_sites(_sources()))
        self.assertEqual(
            [], list(sites),
            "these build a repo-relative NAME with os.path.join and then match on it:\n%s"
            % "\n".join("  %s:%d  %s" % s for s in sites))

    # ---------------------------------------- B.5 — an `os.sep` MATCH against a posixed value
    def test_the_native_sep_sweep_convicts_THE_LINE_THAT_OPENED_THE_TRAVERSAL(self):
        r"""★ GRADED AGAINST THE REAL DEFECT, not a paraphrase of it. The offender below is the
        exact pair of lines `govern/secret_ignore.normalize_target` held on run 32117189879: a
        producer routed through the name boundary, and the containment check beside it still
        spelled with `os.sep`. `add_ignore(root, token, "../outside.py")` was accepted on all
        three Windows legs — a path-traversal defence that did not fire.

        ⚠ ONE WORD OF THE OFFENDER IS RE-SPELLED, AND THE READER IS OWED THE REASON. The line as
        it actually stood said `posix_name(os.path.relpath(...))`; `posix_name` no longer exists
        (the invariant replaced it with `repo_paths.name_of`, which takes the path AND the root),
        so an offender written against it would be graded against a vocabulary the tree cannot
        produce — a paraphrase of a dead world rather than of a live one. What is preserved is the
        SHAPE the detector actually keys on, unchanged: a producer that has been routed to the
        name boundary meeting a comparison that has not."""
        offender = ('from ..repo_paths import name_of\n'
                    'def normalize_target(root, path):\n'
                    '    rel = name_of(target, root_abs)\n'
                    '    if rel == os.curdir or rel.startswith(os.pardir + os.sep):\n'
                    '        raise IgnoreError("outside the repo")\n')
        self.assertEqual(1, len(WP.native_sep_comparison_sites({"p.py": offender})))

        fixed = offender.replace('rel.startswith(os.pardir + os.sep)',
                                 'rel.startswith((os.pardir + "/", os.pardir + chr(92)))')
        self.assertEqual((), WP.native_sep_comparison_sites({"p.py": fixed}))

    def test_an_expression_that_names_BOTH_separators_acquits_itself(self):
        r"""The remedy must not be convicted as the defect. `raw.endswith(("/", os.sep))` and
        `"/" in c or os.sep in c` are already separator-agnostic — they are what a fixed site
        looks like — so they clear on a DERIVED test (the expression mentions `/`), not on a
        hand-written row. A detector answered with exemptions for its own fix is a detector
        about to be switched off."""
        for line in ('    if raw.endswith(("/", os.sep)):\n',
                     '    if "/" in c or os.sep in c:\n'):
            with self.subTest(line=line.strip()):
                self.assertEqual((), WP.native_sep_comparison_sites(
                    {"p.py": 'import _support\ndef t():\n    _support.as_posix(x)\n' + line}))

    def test_a_module_that_never_posix_wraps_is_not_convicted_for_comparing_two_paths(self):
        """Scoped like B.3. `version.py`, `selfprotect.py`, `packaging.py` and `mcp/validation.py`
        all `startswith(parent + os.sep)` against a `realpath`, and every one of them is right to.
        Convicting them would buy four exemption rows arguing that paths are paths."""
        self.assertEqual((), WP.native_sep_comparison_sites(
            {"p.py": 'def t(c, p):\n    return c == p or c.startswith(p + os.sep)\n'}))

    def test_no_posix_wrapping_module_still_MATCHES_with_a_native_separator(self):
        sites = _outstanding("native_sep_comparison",
                             WP.native_sep_comparison_sites(_sources()))
        self.assertEqual(
            [], list(sites),
            "these convert paths to `/`-spelled names and then match with `os.sep`:\n%s"
            % "\n".join("  %s:%d  %s" % s for s in sites))

    def test_no_assertion_in_the_tree_compares_one_spelling_against_the_other(self):
        sites = _outstanding("one_sided_posix", WP.one_sided_posix_sites(_sources()))
        self.assertEqual(
            [], list(sites),
            "these modules posix-spell one side of a comparison and OS-spell the other:\n%s"
            % "\n".join("  %s:%d  %s" % s for s in sites))


# =================================================================================================
# THE EXEMPTION TABLE — every row still points at something
# =================================================================================================

class TestNoExemptionOutlivesItsSite(unittest.TestCase):
    """An exemption table is only safe while it cannot rot. A row whose site was fixed or moved
    is a standing permission nobody granted for whatever occupies that path next — so every row
    must still RESOLVE, and each must carry a reason a reader can disagree with."""

    def test_every_exemption_names_a_file_that_exists(self):
        for (detector, path), reason in sorted(WP.EXEMPT.items()):
            with self.subTest(detector=detector, path=path):
                self.assertTrue(os.path.isfile(os.path.join(ROOT, path)),
                                "exemption points at a file that is gone: %s" % path)
                self.assertGreater(len(reason), 60,
                                   "an exemption's reason must be an argument, not a label")

    def test_every_exemption_still_excuses_a_site_the_sweep_actually_FINDS(self):
        """★ THE ROW THAT MATTERS. A file existing is not the same as the defect still being in
        it. If a site was repaired, its exemption must go — otherwise the next author to write
        that shape into that file inherits a pass nobody reviewed."""
        sources = _sources()
        detectors = {"bare_argv0": WP.bare_argv0_sites,
                     "shell_stdin": WP.shell_spawn_without_stdin_sites,
                     "relpath_identity": WP.unwrapped_relpath_sites,
                     "welded_slash": WP.welded_slash_sites,
                     "one_sided_posix": WP.one_sided_posix_sites,
                     "native_name_expectation": WP.native_name_expectation_sites,
                     "native_sep_comparison": WP.native_sep_comparison_sites}
        for (detector, path) in sorted(WP.EXEMPT):
            with self.subTest(detector=detector, path=path):
                self.assertIn(detector, detectors, "exemption names an unknown detector")
                hits = [s for s in detectors[detector](sources) if s[0] == path]
                self.assertTrue(hits,
                                "%s no longer triggers %s — delete the exemption rather than "
                                "leaving a standing pass on that file" % (path, detector))

    def test_the_bare_name_class_is_cleared_by_a_DERIVED_flag_not_by_rows(self):
        """The 33 `git`/`gh`/`grep` sites carry no exemption rows ON PURPOSE: they are cleared by
        the `shadowed` flag, which a reader can check against Windows. Thirty-three hand-written
        rows would convert a checkable fact into thirty-three unverifiable assertions."""
        for detector, _path in WP.EXEMPT:
            self.assertNotEqual("bare_argv0", detector,
                                "the bare-name class must stay derived, not enumerated")
        unshadowed = [s for s in WP.bare_argv0_sites(_sources()) if not s[3]]
        self.assertGreater(len(unshadowed), 20,
                           "the unshadowed tier vanished — either the tree changed a great deal "
                           "or the shadow list silently swallowed it")


if __name__ == "__main__":
    unittest.main()
