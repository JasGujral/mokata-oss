r"""THE INVARIANT, GRADED — a NAME is POSIX-spelled, a PATH is native, and one of each conversion.

`src/mokata/repo_paths.py` declares the rule. This file is what makes it a rule rather than a
docstring, and it grades three different things which are deliberately not merged:

  1. THE CHOKE POINT ITSELF — `name_of` / `path_of` / `RepoName`, with `ntpath` injected so the
     Windows branch EXECUTES on a POSIX host. Every claim here is verifiable on the machine it
     was written on (doc 85 §7i).

  2. THE STATIC SWEEP — no `os.path.relpath` outside `repo_paths` may become a NAME. This is the
     one that scales: `relpath` is the ONLY way a repo-relative string is born, so guarding
     `relpath` guards the invariant, and it does not require guessing what a value is used for
     from a hundred call sites. It is graded against a PLANTED OFFENDER and against a NEAR-MISS
     it must acquit — a `relpath` that is genuinely a PATH.

  3. THE TWO DEFECTS THAT MOTIVATED IT, by name. `GATE_A` and `GATE_B` below are not
     illustrations; they are the exact failures from mirror runs 32117189879 and 32144708930, so
     the fix is pinned to the things it was built for and cannot be refactored away from them.

⭐ WHY THE MEASURE OF SUCCESS IS NEGATIVE. The strongest evidence that the invariant holds is not
an assertion that passes — it is that `TestNoComparisonNeedsToNormalise` finds nothing left to
convict. Three previous rounds each converted a set of sites and each left a fresh half-converted
pair, because a conversion that a site performs is a conversion a site can forget. There is now no
site that performs it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ntpath
import os
import posixpath
import shutil
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

import _windows_portability as WP
from mokata import repo_paths as RP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# =================================================================================================
# 1 — THE CHOKE POINT, with the Windows branch EXECUTED on this host
# =================================================================================================

class TestNameOfIsTheOnlyProducer(unittest.TestCase):

    def test_a_windows_path_becomes_a_posix_name(self):
        r"""★ THE WHOLE POINT, on macOS. `ntpath.relpath` yields `svc\core.py`; `name_of` must
        hand back `svc/core.py` — the spelling every consumer, key and declaration uses."""
        got = RP.name_of(r"D:\a\repo\svc\core.py", r"D:\a\repo", ospath=ntpath)
        self.assertEqual("svc/core.py", got)
        self.assertNotIn("\\", got)

    def test_the_same_call_on_posix_is_a_no_op_on_the_spelling(self):
        self.assertEqual("svc/core.py",
                         RP.name_of("/a/repo/svc/core.py", "/a/repo", ospath=posixpath))

    def test_a_deep_windows_path_converts_every_separator_not_just_the_first(self):
        self.assertEqual(
            "a/b/c/d.py",
            RP.name_of(r"C:\r\a\b\c\d.py", r"C:\r", ospath=ntpath))

    def test_the_result_is_a_RepoName_not_a_bare_str(self):
        """The type is the producer's proof. It is not a persistent tag — a name that round-trips
        through JSON comes back a plain `str` — and this is the assertion that says which of the
        two claims is being made."""
        self.assertIsInstance(RP.name_of(r"C:\r\a.py", r"C:\r", ospath=ntpath), RP.RepoName)

    def test_a_traversal_survives_as_a_POSIX_traversal(self):
        r"""`..\outside.py` must arrive as `../outside.py`. If it did not, `escapes_root` would be
        asked a question about a spelling it was not designed for — which is exactly Gate A."""
        self.assertEqual("../outside.py",
                         RP.name_of(r"C:\r\outside.py", r"C:\r\repo", ospath=ntpath))

    def test_relpath_still_raises_where_it_always_did(self):
        """The drop-in property. Callers that already handle `ValueError` from a cross-drive
        `relpath` keep working, which is why routing a site through `name_of` is a one-line change
        rather than a refactor of its error handling."""
        with self.assertRaises(ValueError):
            RP.name_of(r"D:\elsewhere\x.py", r"C:\r", ospath=ntpath)


class TestRepoNameRefusesTheWrongSpelling(unittest.TestCase):

    def test_a_backslash_is_refused_at_construction(self):
        r"""★ THE GUARD B3 ASKS FOR, arm one: a NAME-typed value that carries `os.sep`. It cannot
        exist, because the constructor will not build one."""
        with self.assertRaises(RP.NotARepoName):
            RP.RepoName(r"svc\core.py")

    def test_the_refusal_names_the_fix_rather_than_the_symptom(self):
        """A message that said "convert it" would be advice to commit the original defect."""
        with self.assertRaises(RP.NotARepoName) as caught:
            RP.RepoName(r"a\b.py")
        self.assertIn("name_of", str(caught.exception))

    def test_NotARepoName_is_a_ValueError(self):
        """So the `except ValueError` a caller already wrote around `relpath` keeps catching it."""
        self.assertTrue(issubclass(RP.NotARepoName, ValueError))

    def test_a_posix_name_is_accepted_and_behaves_as_a_str(self):
        name = RP.RepoName("svc/core.py")
        self.assertEqual("svc/core.py", name)
        self.assertEqual({"svc/core.py": 1}[name], 1)          # a dict key
        self.assertTrue(name.startswith("svc/"))
        self.assertEqual(["svc/core.py"], sorted({name}))

    def test_a_bare_dot_name_with_a_leading_dot_is_NOT_refused(self):
        """`.github/workflows/ci.yml` and `..config` are ordinary names. The refusal is about the
        SEPARATOR, never about a leading dot — a `startswith("..")` check is what would have got
        this wrong, and it is why `escapes_root` requires the separator too."""
        self.assertEqual(".github/workflows/ci.yml", RP.RepoName(".github/workflows/ci.yml"))
        self.assertEqual("..config", RP.RepoName("..config"))


class TestPathOfIsTheOnlyConsumerBoundary(unittest.TestCase):

    def test_a_name_becomes_a_native_path(self):
        self.assertEqual(r"C:\r\svc\core.py",
                         RP.path_of(r"C:\r", "svc/core.py", ospath=ntpath))

    def test_it_does_not_produce_the_MIXED_spelling_that_os_join_would(self):
        r"""★ THE TRAP THIS EXISTS TO AVOID. `ntpath.join("C:\r", "svc/core.py")` is
        `C:\r\svc/core.py` — accepted by the Windows API, and then unequal to every natively-built
        path it is later compared against. A mixed value is worse than either pure spelling
        because it passes the `open()` and fails the comparison."""
        self.assertEqual(r"C:\r\svc/core.py", ntpath.join(r"C:\r", "svc/core.py"))
        self.assertNotIn("/", RP.path_of(r"C:\r", "svc/core.py", ospath=ntpath))

    def test_a_round_trip_is_the_identity_on_the_name(self):
        for name in ("a.py", "svc/core.py", "a/b/c/d.py", ".github/workflows/ci.yml"):
            with self.subTest(name=name):
                native = RP.path_of(r"C:\r", name, ospath=ntpath)
                self.assertEqual(name, RP.name_of(native, r"C:\r", ospath=ntpath))

    def test_a_root_relative_dot_yields_the_root(self):
        self.assertEqual(r"C:\r", RP.path_of(r"C:\r", ".", ospath=ntpath))

    def test_it_really_opens_a_file_on_this_host(self):
        """The consumer half, exercised rather than asserted: `path_of` must produce something
        `open()` accepts, on whichever platform is running."""
        tmp = tempfile.mkdtemp(prefix="repopaths-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.makedirs(os.path.join(tmp, "svc"))
        with open(os.path.join(tmp, "svc", "core.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        with open(RP.path_of(tmp, "svc/core.py"), encoding="utf-8") as fh:
            self.assertEqual("x = 1\n", fh.read())


class TestNamesOf(unittest.TestCase):

    def test_it_sorts_and_converts_every_member(self):
        got = RP.names_of([r"C:\r\b\x.py", r"C:\r\a\y.py"], r"C:\r", ospath=ntpath)
        self.assertEqual(["a/y.py", "b/x.py"], got)
        self.assertTrue(all(isinstance(n, RP.RepoName) for n in got))


# =================================================================================================
# 2 — THE STATIC SWEEP: no `relpath` outside the choke point becomes a NAME
# =================================================================================================

class TestNoComparisonNeedsToNormalise(unittest.TestCase):
    r"""★ THE NEGATIVE MEASURE. Every detector in `_windows_portability` that exists because one
    side of a pair was converted and the other was not must now find NOTHING outstanding in the
    product. If any of them does, a producer has not been routed — and the correct repair is to
    route the producer, never to normalise at the comparison."""

    def _sources(self):
        return WP.walked_sources(ROOT)

    def test_no_relpath_used_as_an_identity_is_unconverted(self):
        kept, _excused = WP.apply_exemptions(
            "unwrapped_relpath", WP.unwrapped_relpath_sites(self._sources()))
        self.assertEqual(
            [], list(kept),
            "a repo-relative NAME is still being produced by a bare relpath:\n%s"
            % "\n".join("  %s:%d  %s" % s for s in kept))

    def test_no_module_converts_one_side_of_a_comparison_and_not_the_other(self):
        kept, _excused = WP.apply_exemptions(
            "one_sided_posix", WP.one_sided_posix_sites(self._sources()))
        self.assertEqual([], list(kept),
                         "\n".join("  %s:%d  %s" % s for s in kept))

    def test_no_os_sep_comparison_survives_in_a_module_that_names(self):
        kept, _excused = WP.apply_exemptions(
            "native_sep_comparison", WP.native_sep_comparison_sites(self._sources()))
        self.assertEqual([], list(kept),
                         "\n".join("  %s:%d  %s" % s for s in kept))


class TestTheProductSideIsRoutedThroughTheChokePoint(unittest.TestCase):
    r"""The sweep above asks "is any site wrong". This one asks the stronger question the
    invariant actually claims: is `repo_paths` the ONLY module in `src/` that spells the
    conversion at all?"""

    #: Every `src/` module allowed to hold a `relpath` whose result is a NAME. Exactly one.
    CHOKE_POINT = "src/mokata/repo_paths.py"

    #: `relpath` sites in `src/` whose result is a PATH, not a name — declared with the reason,
    #: because "we looked and it is fine" and "we did not look" must not share a representation.
    PATH_RESULTS = {
        "src/mokata/repo_identity.py":
            "`worktree_label()` builds a HUMAN-FACING LABEL of where a checkout sits relative to "
            "the main one, printed by `mokata windows`. It is never a key, never compared against "
            "a declaration, and never opened — it is a signpost to a directory on the reader's "
            "own disk, so the reader's own separator is the correct one. Converting it would "
            "show a Windows user a path they cannot paste.",
    }

    #: Every caller of `as_name`, with the EXTERNAL source of its value named. `as_name` is a
    #: converter over a bare string — the shape of the deleted `posix_name` — and the only thing
    #: separating it from that mistake is that its input crossed in from outside the process.
    #: A row here is the claim that it did, so a fourth caller has to be argued for.
    AS_NAME_CALLERS = {
        "src/mokata/spec_scope.py":
            "`_rel`'s fallback: a path the caller supplied that is NOT under the root, matched "
            "against the spec's `/`-spelled globs. The in-root branch above it uses `name_of`.",
        "src/mokata/gate_hook.py":
            "`is_test_path`: the harness's PreToolUse payload `file_path`, split on `/` and "
            "matched against `_TEST_DIRS`. ⭐ A GATE — on Windows the payload arrives natively "
            "spelled and used to be normalised by a local `.replace` here.",
        "src/mokata/docsync.py":
            "`resolve_command_route`: a doc path suffix-matched against `_PAGE_COMMAND_ROUTE`'s "
            "`/`-spelled keys. The value is whatever the caller was handed.",
    }

    def test_every_as_name_caller_row_still_resolves(self):
        """The staleness control the `EXEMPT` table carries, applied to this table too."""
        callers = {name for name, text in self._src_sources().items() if "as_name(" in text}
        stale = sorted(set(self.AS_NAME_CALLERS) - callers)
        self.assertEqual([], stale, "these AS_NAME_CALLERS rows name no caller: %s" % stale)

    @staticmethod
    def _spells_a_separator_conversion(text):
        r"""Every `x.replace(<a separator>, "/")` in `text`, as `(lineno, source)`.

        ⚠ IT PARSES, IT DOES NOT GREP, and both halves of that matter here — a text match got
        this wrong in BOTH directions on its first draft:

          * it convicted a DOCSTRING in `knowledge/index.py` that describes the very `.replace`
            the invariant deleted. A pin whose scanned region contains prose about itself is the
            `PIN-SUBSTRING-COMMENT-HOLE` lesson with the sign reversed.
          * it convicted `harness_setup`'s `.replace("\\", "\\\\")` (JSON escaping) and
            `injection_ledger`'s `.replace(os.sep, "_")` (sanitising a session id into a
            filename). Neither converts a separator to `/`; they merely mention one.

        So the shape is stated exactly: the FIRST argument is a separator (`os.sep`,
        `FOREIGN_SEP`, or a literal backslash) and the SECOND is `"/"`. That is the conversion
        this invariant owns, and nothing else is.
        """
        import ast
        try:
            tree = ast.parse(text)
        except SyntaxError:                                   # pragma: no cover - parsed corpus
            return []
        found = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "replace"
                    and len(node.args) == 2):
                continue
            first, second = node.args
            if not (isinstance(second, ast.Constant) and second.value == "/"):
                continue
            is_sep = ((isinstance(first, ast.Constant) and first.value == "\\")
                      or (isinstance(first, ast.Attribute) and first.attr == "sep")
                      or (isinstance(first, ast.Name) and first.id == "FOREIGN_SEP"))
            if is_sep:
                found.append((node.lineno, ast.unparse(node)[:90]))
        return found

    def _src_sources(self):
        return {name: text for name, text in WP.walked_sources(ROOT).items()
                if name.startswith("src/")}

    def test_every_relpath_in_src_is_the_choke_point_or_a_declared_path_result(self):
        """★ THE SWEEP B2 ASKS FOR. It does not try to infer what a value is used for; it asks
        the one question a static reader can answer honestly — WHERE does a repo-relative string
        get born — and requires every birth site outside the choke point to carry an argument."""
        holders = sorted({name for name, text in self._src_sources().items()
                          if "relpath(" in text})
        allowed = {self.CHOKE_POINT} | set(self.PATH_RESULTS)
        undeclared = [h for h in holders if h not in allowed]
        self.assertEqual(
            [], undeclared,
            "these modules produce a repo-relative string outside the choke point and are not "
            "declared as producing a PATH: %s.\nRoute the producer through "
            "`repo_paths.name_of(path, root)`, or add a row to PATH_RESULTS saying why the "
            "native spelling is the correct one there." % ", ".join(undeclared))

    def test_the_declarations_are_not_stale(self):
        """A row that no longer resolves to a real site is a claim outliving the thing it excuses
        — the same control the `EXEMPT` table in `_windows_portability` carries."""
        holders = {name for name, text in self._src_sources().items() if "relpath(" in text}
        stale = sorted(set(self.PATH_RESULTS) - holders)
        self.assertEqual(
            [], stale,
            "these PATH_RESULTS rows no longer name a module containing a relpath: %s" % stale)

    def test_the_choke_point_itself_still_contains_one(self):
        """Anti-vacuity: if `repo_paths` stopped calling `relpath`, every assertion above would
        pass for the wrong reason and the invariant would have no producer at all."""
        self.assertIn(self.CHOKE_POINT, self._src_sources())
        self.assertIn("relpath(", self._src_sources()[self.CHOKE_POINT])

    def test_the_conversion_itself_is_spelled_in_exactly_one_module(self):
        r"""★ THE OTHER HALF OF "ONE CHOKE POINT", and the one the `relpath` sweep alone misses.
        A site can re-introduce the old habit without any `relpath` at all, simply by writing
        `value.replace(os.sep, "/")` where it compares. That is what `knowledge/index.py`'s
        `skipped_checkout_lines` held: a defensive normalisation at a CONSUMER, standing in for a
        producer nobody had routed. It is deleted, and this is what keeps it deleted."""
        offenders = []
        for name, text in sorted(self._src_sources().items()):
            if name == self.CHOKE_POINT:
                continue
            offenders += ["%s:%d  %s" % (name, line, src)
                          for line, src in self._spells_a_separator_conversion(text)]
        self.assertEqual(
            [], offenders,
            "these spell a separator conversion outside `repo_paths`:\n  %s\nA `.replace` at a "
            "consumer is evidence of an unrouted PRODUCER, not a fix — route the producer through "
            "`name_of`, or use `as_name` if the value genuinely crossed in from outside this "
            "process, or `split_path` if it is not repo-relative at all."
            % "\n  ".join(offenders))

    def test_the_conversion_sweep_is_graded_on_shapes_it_must_and_must_not_convict(self):
        r"""§7i for the sweep above, and every case here is one it got WRONG on its first draft."""
        convict = self._spells_a_separator_conversion
        # must convict — the shape the invariant owns
        self.assertEqual(1, len(convict('norm = path.replace("\\\\", "/")\n')))
        self.assertEqual(1, len(convict('norm = p.replace(os.sep, "/")\n')))
        self.assertEqual(1, len(convict('norm = p.replace(FOREIGN_SEP, "/")\n')))
        # must ACQUIT — prose describing the deleted defect
        self.assertEqual([], convict('def f():\n    """held p.replace(os.sep, "/") once."""\n'))
        # must ACQUIT — escaping a backslash for JSON, not converting a separator
        self.assertEqual([], convict('d = s.replace("\\\\", "\\\\\\\\")\n'))
        # must ACQUIT — sanitising a session id into a filename
        self.assertEqual([], convict('safe = sid.replace("/", "_").replace(os.sep, "_")\n'))
        # must ACQUIT — a real string substitution that happens to mention a slash
        self.assertEqual([], convict('u = url.replace("http://", "https://")\n'))

    def test_as_name_has_a_short_enumerated_caller_list(self):
        r"""`as_name` is a converter over a bare string — the exact shape of the deleted
        `posix_name`. Its docstring argues why that is safe HERE (the other half of the pair is a
        person, not another module), and this is what stops the argument being borrowed for a
        second, third and fourth site until it is `posix_name` again under a new name."""
        callers = sorted(name for name, text in self._src_sources().items()
                         if name != self.CHOKE_POINT and "as_name(" in text)
        self.assertEqual(
            sorted(self.AS_NAME_CALLERS), callers,
            "the `as_name` caller set changed: %s. Each caller must be a genuine EXTERNAL "
            "boundary — a value that never went through this process's relpath. If it did, it "
            "wants `name_of(path, root)`." % callers)

    def test_posix_name_is_gone_from_the_product(self):
        """The converter that made the previous three rounds unconvergeable. Its ABSENCE is the
        structural change; leaving it importable would leave the old habit available."""
        for name, text in self._src_sources().items():
            if name in ("src/mokata/repo_walk.py", "src/mokata/repo_paths.py"):
                continue          # both mention it in prose, saying it is gone and why
            self.assertNotIn("posix_name", text,
                             "%s still references posix_name" % name)
        import mokata.repo_walk as RW
        self.assertFalse(hasattr(RW, "posix_name"),
                         "repo_walk still exports posix_name")


class TestTheSweepIsGradedNotAssumed(unittest.TestCase):
    r"""§7i. The sweep above passes on a healthy tree, which by itself proves nothing. Here it is
    handed a PLANTED OFFENDER it must convict and a NEAR-MISS it must acquit."""

    #: A producer of a NAME, written the wrong way. This is `grep_backend._rel` as it stood on
    #: run 32144708930 — four of the eleven failures came through that one method.
    PLANTED_OFFENDER = (
        "import os\n"
        "class GrepBackend:\n"
        "    def _rel(self, path):\n"
        "        return os.path.relpath(path, self.root)\n"
        "    def refs(self, paths):\n"
        "        return sorted(self._rel(p) for p in paths)\n"
    )

    #: The NEAR-MISS. A `relpath` whose result is handed to `open()` — a PATH, correctly native.
    #: A guard that convicted this would be answered with exemptions and then switched off.
    NEAR_MISS = (
        "import os\n"
        "def read_under(root, target):\n"
        "    rel = os.path.relpath(target, root)\n"
        "    with open(os.path.join(root, rel), encoding='utf-8') as fh:\n"
        "        return fh.read()\n"
    )

    def _holders(self, sources):
        """The birth-site question, applied to a synthetic corpus exactly as the real sweep
        applies it to `src/`."""
        return sorted(name for name, text in sources.items() if "relpath(" in text)

    def test_the_planted_offender_is_a_holder_and_would_need_a_declaration(self):
        self.assertEqual(["m.py"], self._holders({"m.py": self.PLANTED_OFFENDER}))

    def test_the_near_miss_is_ALSO_a_holder_which_is_why_the_declaration_exists(self):
        """★ THE HONEST LIMIT OF THE STATIC HALF, stated rather than papered over. A birth-site
        sweep cannot tell a name from a path — that is a question about what happens to the value
        a hundred lines later, in another module, which is precisely what defeated the previous
        three rounds of value-analysis. So it does not guess: it requires a human argument for
        every site outside the choke point, and `PATH_RESULTS` is where those arguments live.
        What makes that safe is that the argument is WRITTEN and its staleness is checked."""
        self.assertEqual(["m.py"], self._holders({"m.py": self.NEAR_MISS}))

    def test_the_value_level_detectors_DO_discriminate_the_two(self):
        """And this is the half that does the discriminating: B.1 convicts the offender, whose
        relpath is matched on, and acquits the near-miss, whose relpath is opened."""
        self.assertEqual(
            (), WP.unwrapped_relpath_sites({"m.py": self.NEAR_MISS}),
            "a relpath handed to open() is a PATH and must not be convicted")
        offender = (
            "import os\n"
            "def corpus(files, ROOT):\n"
            "    out = {}\n"
            "    for path in files:\n"
            "        rel = os.path.relpath(path, ROOT)\n"
            "        out[rel] = read(path)\n"
            "    return out\n")
        self.assertEqual(1, len(WP.unwrapped_relpath_sites({"m.py": offender})))

    def test_routing_the_offender_through_the_choke_point_clears_it(self):
        """The remedy must actually be the remedy — a detector that convicts both the defect and
        its fix teaches nobody anything."""
        fixed = self.PLANTED_OFFENDER.replace(
            "os.path.relpath(path, self.root)", "name_of(path, self.root)")
        self.assertEqual([], self._holders({"m.py": fixed}))
        self.assertEqual((), WP.unwrapped_relpath_sites({"m.py": fixed}))


# =================================================================================================
# 3 — THE TWO DEFECTS THAT MOTIVATED THE INVARIANT, PINNED BY NAME
# =================================================================================================

class TestGateA_SecretIgnorePathTraversal(unittest.TestCase):
    r"""GATE A — `test_secret_ignore.TestAdversarialLaundering.test_route_8_path_traversal_out_of
    _the_repo(path='../outside.py')`, "IgnoreError not raised", on all three Windows legs of
    mirror run 32117189879.

    THE MECHANISM. `rel` had been routed to the name boundary and read `../outside.py`; the
    containment check beside it still read `rel.startswith(os.pardir + os.sep)`, and `os.sep` is
    `\` there. `"../outside.py"` does not start with `"..\"`, so a path-traversal defence accepted
    a traversal. Never committed to master, never tagged — nothing shipped.

    ⚠ WHY IT IS A CASE HERE AND NOT ONLY IN `test_secret_ignore`. The end-to-end test could only
    ever exercise the separator the host uses, which is why it was green on macOS for three weeks.
    These assertions run the predicate against BOTH spellings on ANY host, and they live beside
    the invariant because the invariant is what makes a second instance unconstructible."""

    def test_both_spellings_of_a_traversal_are_refused_on_any_host(self):
        for rel in ("../outside.py", r"..\outside.py", "..", ".", ""):
            with self.subTest(rel=rel):
                self.assertTrue(RP.escapes_root(rel), "%r was not treated as escaping" % rel)

    def test_a_name_that_merely_begins_with_two_dots_is_NOT_a_traversal(self):
        r"""The discrimination, without which the fix is a wall: `..config` is a file. A bare
        `startswith("..")` would refuse it, and a reader would conclude the check was paranoid
        rather than correct."""
        for rel in ("..config", "..github/x.py", "a/../b.py", "src/mokata/repo_paths.py"):
            with self.subTest(rel=rel):
                self.assertFalse(RP.escapes_root(rel))

    def test_the_producer_makes_the_windows_spelling_unreachable(self):
        r"""★ THE STRUCTURAL HALF, and the reason this is closed rather than patched. Gate A
        needed `escapes_root` to answer for `\` because the producer could hand it one. It cannot
        any more: `name_of` under `ntpath` yields `../outside.py`, and `RepoName` refuses to be
        constructed from anything else. The both-separator answer above is now belt under braces,
        which is stated at its definition as the ONE deliberate exception to this module's rule."""
        rel = RP.name_of(r"C:\repo\outside.py", r"C:\repo\inner", ospath=ntpath)
        self.assertEqual("../outside.py", rel)
        self.assertTrue(RP.escapes_root(rel))
        with self.assertRaises(RP.NotARepoName):
            RP.RepoName(r"..\outside.py")

    def test_the_real_normalize_target_still_refuses_it(self):
        """The gate itself, not a reconstruction of it."""
        from mokata.govern.secret_ignore import IgnoreError, normalize_target
        tmp = tempfile.mkdtemp(prefix="gatea-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with self.assertRaises(IgnoreError):
            normalize_target(tmp, "../outside.py")


class TestGateB_TheDeclaredCarveOutMustBeANameToo(unittest.TestCase):
    r"""GATE B — four failures in `test_h6_mint_site` and `test_h1a_s2_s3_injection_pack` on run
    32117189879, and it is Gate A's defect in the other direction.

    THE MECHANISM. Both modules snapshot the governed tree, keying each file by a NAME, and
    EXCLUDE one leaf — the audit ledger recording the refusal, which is the gate doing its job.
    The snapshot keys moved to the name boundary; the exclusion predicates were still written
    `os.path.join(".mokata", "temp_local", "audit")`, which spells `\` on Windows and therefore
    matched none of the `/`-spelled keys it was meant to exclude. The refusal's own audit record
    then read as an unexpected write.

    ⚠ THE HYPOTHESIS IN THE ORIGINAL BRIEF WAS WRONG AND THAT IS RECORDED HERE ON PURPOSE: there
    is no double write. Both ledgers are append-only and the tests run five turns, so 8 -> 16 ->
    24 bytes is one append per turn. A DECLINED write does not land. The defect was entirely in
    the predicate — which is why the case below is about a PREDICATE, not about a write.

    WHAT THE INVARIANT CHANGES. A declared carve-out is a NAME (it is matched against name-keyed
    snapshot entries), so it is spelled `/` and `RepoName` will refuse anything else. The class is
    closed at the declaration rather than at the hundred places that consult it."""

    #: The carve-out as it was written when it failed, and as it must be written now.
    BROKEN = os.path.join(".mokata", "temp_local", "audit")
    DECLARED = ".mokata/temp_local/audit"

    def test_a_natively_spelled_carve_out_cannot_be_a_name(self):
        r"""★ THE GUARD B3 ASKS FOR, arm two: a native path used as a key. On Windows `BROKEN`
        carries a `\` and `RepoName` refuses it, so the declaration reds where it is WRITTEN
        instead of silently matching nothing a hundred lines away.

        On POSIX the two strings are identical, so the host cannot demonstrate this — which is
        exactly the blindness that let the defect ship. The `ntpath` construction below forces the
        Windows spelling on any host, so the claim is checked here and not only on the mirror."""
        windows_spelling = ntpath.join(".mokata", "temp_local", "audit")
        self.assertEqual(r".mokata\temp_local\audit", windows_spelling)
        with self.assertRaises(RP.NotARepoName):
            RP.RepoName(windows_spelling)
        self.assertEqual(self.DECLARED, RP.RepoName(self.DECLARED))

    def test_the_declared_carve_out_matches_a_name_keyed_snapshot_on_any_host(self):
        """The property the four failing tests were actually asserting, stated once: an exclusion
        written as a NAME prefixes the keys a name-keyed snapshot produces, whatever the host."""
        keys = [RP.name_of(ntpath.join(r"C:\r", *parts), r"C:\r", ospath=ntpath)
                for parts in ((".mokata", "temp_local", "audit", "ledger.jsonl"),
                              (".mokata", "temp_local", "audit", ".ledger.jsonl.count"),
                              ("src", "mokata", "cli.py"))]
        excluded = [k for k in keys if k.startswith(self.DECLARED)]
        self.assertEqual(
            [".mokata/temp_local/audit/ledger.jsonl",
             ".mokata/temp_local/audit/.ledger.jsonl.count"], excluded)

    def test_the_broken_spelling_would_have_excluded_NOTHING(self):
        """The counterfactual, executed rather than described — this is what the four failures
        were. Without it, the assertion above is a fact with no consequence attached."""
        broken = ntpath.join(".mokata", "temp_local", "audit")
        keys = [RP.name_of(ntpath.join(r"C:\r", *parts), r"C:\r", ospath=ntpath)
                for parts in ((".mokata", "temp_local", "audit", "ledger.jsonl"),
                              ("src", "mokata", "cli.py"))]
        self.assertEqual([], [k for k in keys if k.startswith(broken)])


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
