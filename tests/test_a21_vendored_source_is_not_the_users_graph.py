"""A package manager's install directory is not the user's source, and the graph must not answer from it.

0.0.20, found while costing stage 05's TypeScript corpus. `repo_walk.prune_source_dirs` already
declared its subject — *"nested checkouts (someone else's source, wherever it sits)"* — and its
docstring already named the harm: *"a vendored dependency … is somebody else's source, and
answering `defs`/`callers` from it duplicates every symbol it holds."*

⛔ **THE CONCEPT WAS RIGHT AND THE DETECTOR WAS TOO NARROW.** The only test was `.git`, and an npm
package has no `.git`. So on any JavaScript or TypeScript repo the shipped grep code-graph floor
walked straight into `node_modules`, whose files carry six of the extensions
`languages.SOURCE_EXTENSIONS` declares. Measured end-to-end through `GrepBackend` before the fix:

    defs myOwnThing  ->  src/app.ts  AND  node_modules/left-pad/index.ts
    defs vendored    ->  node_modules/left-pad/index.ts        (exists NOWHERE else)

⚠ On a real Node repo `node_modules` holds tens of thousands of files, so this is not a cosmetic
duplicate — it is every `defs` / `callers` / blast-radius answer naming files the user does not
maintain. `TheLiveDefect` reproduces both readings and requires them gone.

DERIVED FROM THE LANGUAGE TABLE, NOT LISTED IN THE WALKER (§7j)
----------------------------------------------------------------
`Language.vendor_dirs` sits beside the extensions it belongs with, and `languages.VENDOR_DIRS` is
the union across the table. **The next language brings its own install directory as one line next
to its own file types**, rather than as a second declaration inside a walker that somebody has to
remember to update — which is how `SOURCE_EXTENSIONS` is already built, one field over.

⛔ AND THE EMPTY TUPLES ARE A DECISION, NOT AN OMISSION. Go's `vendor/` and Rust's `target/` are
deliberately NOT pruned: `vendor/` is a real, hand-maintained source directory in some repos, and
pruning it would silently delete a user's own code from their own graph — **the same harm, in the
other direction, and the harder one to notice.** `TheRuleRefusesToGuess` pins both.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import languages
from mokata.knowledge.grep_backend import GrepBackend
from mokata.repo_paths import name_of
from mokata.repo_walk import prune_source_dirs


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "mokata")

#: The vendored file plants a symbol the user's own tree does NOT have, so a leak is detectable as
#: an answer rather than only as a duplicate — a duplicate can be argued away, a phantom cannot.
_MINE = "export function myOwnThing(){ return 1; }\n"
_THEIRS = "export function myOwnThing(){}\nexport function vendored(){}\n"


def _repo(vendor_dir="node_modules"):
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "src"))
    os.makedirs(os.path.join(d, vendor_dir, "left-pad"))
    with open(os.path.join(d, "src", "app.ts"), "w", encoding="utf-8") as fh:
        fh.write(_MINE)
    with open(os.path.join(d, vendor_dir, "left-pad", "index.ts"), "w", encoding="utf-8") as fh:
        fh.write(_THEIRS)
    return d


def _paths(backend, kind, target):
    return sorted(r.path for r in backend.query(kind, target).references)


class TheLiveDefect(unittest.TestCase):
    """Both readings the defect produced, required gone. This is the whole point of the change."""

    def setUp(self):
        self.root = _repo()
        self.backend = GrepBackend(self.root)

    def test_the_users_OWN_symbol_is_answered_ONCE(self):
        self.assertEqual(_paths(self.backend, "defs", "myOwnThing"), ["src/app.ts"],
                         "the graph answers the user's own symbol from a vendored copy as well as "
                         "from their file")

    def test_a_symbol_that_exists_ONLY_in_the_install_dir_is_answered_NOWHERE(self):
        """⭐ The phantom. `vendored` is in no file the user wrote, so any hit is a leak."""
        self.assertEqual(_paths(self.backend, "defs", "vendored"), [])

    def test_the_install_directory_is_not_in_the_corpus_AT_ALL(self):
        # `name_of`, not `relpath`: a repo-relative identity is SPELLED POSIX, and
        # `relpath` alone yields `src\\app.ts` on Windows — a comparison that fails
        # for the platform rather than for the property. `repo_paths` is the one
        # producer, and `test_repo_paths_invariant` caught this line.
        seen = sorted(name_of(f, self.root) for f in self.backend._files())
        self.assertEqual(seen, ["src/app.ts"])

    def test_the_fixture_really_CONTAINS_the_vendored_file(self):
        """⛔ Anti-vacuity: a fixture that planted nothing would pass every assertion above."""
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "node_modules", "left-pad", "index.ts")))

    def test_and_the_users_file_is_still_FOUND(self):
        """The other direction — a prune that ate everything would also pass the tests above."""
        self.assertTrue(_paths(self.backend, "defs", "myOwnThing"))


class TheSetIsDERIVEDFromTheLanguageTable(unittest.TestCase):

    def test_VENDOR_DIRS_is_the_union_across_the_table(self):
        self.assertEqual(set(languages.VENDOR_DIRS),
                         set(languages.vendor_dirs_of(languages.LANGUAGES)))

    def test_the_union_is_a_REAL_CLAIM_and_not_a_coincidence_of_the_current_table(self):
        """⛔ Only `javascript` populates `vendor_dirs` today, so *"the union across the table"*
        and *"whatever javascript declares"* return the same set — and a mutant narrowing the
        union to one language came back GREEN. This plants a SECOND language and requires it to
        contribute, which is what makes the union gradeable at all."""
        planted = dict(languages.LANGUAGES)
        planted["planted"] = languages.Language(
            name="planted", extensions=(".zz",), block_style="brace",
            _define_tmpl="", _func_def="", _scope_def="", _import="",
            vendor_dirs=("zz_modules",))
        union = languages.vendor_dirs_of(planted)
        self.assertIn("zz_modules", union, "a second language's install dir did not reach the "
                                           "union, so the union reads one language")
        self.assertIn("node_modules", union, "the union lost the language it already had")

    def test_a_language_declaring_NOTHING_contributes_nothing(self):
        only_empty = {"py": languages.PYTHON, "go": languages.GO}
        self.assertEqual(languages.vendor_dirs_of(only_empty), frozenset())

    def test_the_set_is_NOT_EMPTY(self):
        """⛔ With an empty set every prune assertion in this file is vacuously satisfied."""
        self.assertTrue(languages.VENDOR_DIRS)

    def test_the_walker_names_no_directory_of_its_own(self):
        """§7j. `repo_walk` must not carry a second list — that is the drift this design avoids."""
        with open(os.path.join(SRC, "repo_walk.py"), "r", encoding="utf-8") as fh:
            source = fh.read()
        for name in languages.VENDOR_DIRS:
            self.assertNotIn('"%s"' % name, source,
                             "%r is spelled inside repo_walk.py as well as in the language table, "
                             "so the two can drift" % (name,))

    def test_the_language_that_OWNS_node_modules_is_the_one_that_declares_it(self):
        owners = [l.name for l in languages.LANGUAGES.values() if "node_modules" in l.vendor_dirs]
        self.assertEqual(owners, ["javascript"])


class TheRuleRefusesToGuess(unittest.TestCase):
    """⛔ Pruning a directory the user hand-maintains is the same harm in the other direction."""

    def _pruned(self, names):
        d = tempfile.mkdtemp()
        for n in names:
            os.makedirs(os.path.join(d, n), exist_ok=True)
        kept = list(names)
        prune_source_dirs(d, kept)
        return sorted(set(names) - set(kept))

    def test_go_vendor_and_rust_target_are_NOT_pruned(self):
        """Both are real source directories in some repos. An empty tuple is a decision."""
        self.assertEqual(self._pruned(["vendor", "target", "src"]), [])
        for lang in ("go", "rust"):
            self.assertEqual(languages.LANGUAGES[lang].vendor_dirs, (),
                             "%s declared a vendor dir — if that is deliberate, this test is the "
                             "place to say why" % lang)

    def test_the_match_is_EXACT_and_not_a_substring(self):
        """`my_node_modules_helper` is somebody's own package. Pruning it would be a silent loss."""
        self.assertEqual(
            self._pruned(["node_modules", "my_node_modules_helper", "node_modules_old"]),
            ["node_modules"])

    def test_hidden_dirs_and_checkouts_still_prune(self):
        """The two rules that were already there must be untouched by the third."""
        d = tempfile.mkdtemp()
        for n in (".venv", "nested", "src"):
            os.makedirs(os.path.join(d, n), exist_ok=True)
        with open(os.path.join(d, "nested", ".git"), "w", encoding="utf-8"):
            pass                                  # a checkout marker; content is irrelevant
        kept = [".venv", "nested", "src"]
        prune_source_dirs(d, kept)
        self.assertEqual(kept, ["src"])

    def test_an_install_dir_is_NOT_reported_as_a_skipped_checkout(self):
        """⚠ Deliberate: it would fire on every walk of every JS repo and bury the line that
        matters. A nested checkout is news; an install directory is the standing rule."""
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "node_modules"), exist_ok=True)
        skipped = []
        kept = ["node_modules"]
        prune_source_dirs(d, kept, skipped=skipped)
        self.assertEqual(kept, [])
        self.assertEqual(skipped, [])


class OneDeclarationReachesEveryWalker(unittest.TestCase):
    """The reason this went in `prune_source_dirs` rather than in the TS corpus it was found for."""

    def _call_sites(self):
        # CORPUS: THE WORKING TREE. The question is "how many walkers in this source tree will
        # actually run this prune", and a walker on disk runs whether or not git tracks it —
        # `sync-public.sh` mirrors with rsync, so an untracked module under `src/` ships and walks
        # exactly like a tracked one. The index would answer a different question (what is
        # committed), and answering it here would let an untracked walker sit outside the count
        # while still walking a user's `node_modules`.
        sites = []
        for dirpath, dirnames, filenames in os.walk(SRC):
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "__"))]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                with open(path, "r", encoding="utf-8") as fh:
                    try:
                        tree = ast.parse(fh.read())
                    except SyntaxError:                     # pragma: no cover - not our subject
                        continue
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                            and node.func.id == "prune_source_dirs"):
                        sites.append(name_of(path, SRC))
        return sorted(set(sites))

    def test_the_fix_reaches_MORE_THAN_THE_ONE_WALKER_IT_WAS_FOUND_FOR(self):
        sites = self._call_sites()
        self.assertGreaterEqual(len(sites), 3,
                                "only %d walker(s) call prune_source_dirs, so 'one declaration, "
                                "every walker' is not the property it claims: %r"
                                % (len(sites), sites))
        self.assertIn("knowledge/grep_backend.py", sites)


if __name__ == "__main__":
    unittest.main()
