"""The code graph answers on TypeScript, and says so out loud when it cannot.

0.0.20 stage 05, `JS-TS-GRAPH-FLOOR`, second half. `test_a22` grades the WALKER against a fixture;
this grades the two things that make it a feature rather than a module — **the graph actually uses
it**, and **a user who has not installed it is told at the first structural question rather than at
emit.**

⭐ THE EXIT CRITERION IS A BEHAVIOUR, AND IT IS PINNED AS ONE. doc 105 §5: *"A TypeScript repo gets
a non-degraded graph answer."* `TheGraphAnswersOnTypescript` asks `AstBackend` real queries against
a real repo and requires `degraded is False` — not that a walker exists, not that a module imports.

G14 CLAUSE 3 — THE CLAUSE THE RULING RESTS ON
-----------------------------------------------
`[graph-ts]` is an optional extra, so the DEFAULT experience on a TypeScript repo is the degraded
one. doc 105 §9 states the cost plainly and then states the condition:

> *"Clause 3 is what makes that acceptable, and without clause 3 this ruling would be wrong. If
> clause 3 is not built, re-open G14."*

`TheDegradationIsAnnouncedUpFront` is clause 3. Four states, because *"there is no TypeScript
here"*, *"there is and the parser is in"*, *"there is and it is not"* and *"the question could not
be asked"* are four different facts (§7g) — and only one of them is a warning.

⚠ ONE FIXTURE PROPERTY IS DOING REAL WORK: the announcement counts files through the SAME corpus
predicate and the SAME `prune_source_dirs` walk the walker uses, so a repo whose only TypeScript
sits in `node_modules` is not a TypeScript repo for this purpose. Two implementations of *"is this
a TS repo"* is how a check comes to describe something other than the thing it is about.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.config import Surface
from mokata.govern import doctor as D
from mokata.init import init_repo
from mokata.knowledge import ts_edges
from mokata.knowledge.ast_backend import AstBackend
from mokata.repo_paths import name_of


_WIDGET_TSX = '''import type {Config} from "./cfg";
interface Renderable { render(): void; }
export class Widget extends Base implements Renderable {
  render() { helper(); }
}
'''
_OTHER_TS = 'import {Widget} from "./widget";\nexport function use(){ new Widget(); }\n'
_VENDORED = "export class Widget {}\nexport function vendoredOnly(){}\n"
_PY = "def py_thing():\n    pass\n"

_needs_parser = unittest.skipUnless(
    ts_edges.available(), "the [graph-ts] extra is not installed — the graph cannot read TS here")


def _tree(files):
    root = tempfile.mkdtemp()
    for rel, body in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
    return root


def _repo(files):
    root = tempfile.mkdtemp()
    init_repo(root=root, profile="standard", assume_yes=True, out=lambda _m: None)
    for rel, body in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
    return Surface.load(root)


_FULL = {"src/widget.tsx": _WIDGET_TSX, "src/other.ts": _OTHER_TS,
         "node_modules/dep/index.ts": _VENDORED, "app.py": _PY}


@_needs_parser
class TheGraphAnswersOnTypescript(unittest.TestCase):
    """doc 105 §5's exit criterion, asked as a question of the graph rather than of the code."""

    @classmethod
    def setUpClass(cls):
        cls.root = _tree(_FULL)
        cls.backend = AstBackend(cls.root)

    def _paths(self, kind, target):
        return sorted(r.path for r in self.backend.query(kind, target).references)

    def test_IMPLEMENTERS_is_answered_structurally(self):
        """⭐ The edge the row is most about — the one the lexical floor cannot answer at all."""
        self.assertEqual(self._paths("implementers", "Renderable"), ["src/widget.tsx"])

    def test_the_answer_is_NOT_DEGRADED(self):
        """The exit criterion in one assertion. A degraded answer is what the row complains of."""
        self.assertFalse(self.backend.query("implementers", "Renderable").degraded)

    def test_CALLERS_reaches_inside_a_tsx_method(self):
        self.assertEqual(self._paths("callers", "helper"), ["src/widget.tsx"])

    def test_a_CROSS_FILE_import_edge_exists(self):
        self.assertEqual(self._paths("imports", "widget"), ["src/other.ts"])

    def test_PYTHON_still_answers_from_the_SAME_index(self):
        """⛔ One graph, not two. A second backend would have meant a second precedence rule and a
        second set of answers to one question."""
        self.assertEqual(self._paths("defs", "py_thing"), ["app.py"])

    def test_the_VENDORED_copy_is_not_in_the_graph(self):
        """`node_modules` holds a `Widget` too. The user's own is the only right answer."""
        self.assertEqual(self._paths("defs", "Widget"), ["src/widget.tsx"])

    def test_a_symbol_that_exists_ONLY_in_node_modules_is_answered_NOWHERE(self):
        self.assertEqual(self._paths("defs", "vendoredOnly"), [])

    def test_the_fixture_really_HAS_the_vendored_file(self):
        """⛔ Anti-vacuity for the two assertions above."""
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "node_modules", "dep", "index.ts")))


class TheCorpusFollowsTheEXTRA(unittest.TestCase):
    """Membership is recomputed every walk, so installing or removing the extra takes effect at once."""

    def test_TS_files_are_out_of_the_corpus_when_the_parser_is_absent(self):
        root = _tree(_FULL)
        backend = AstBackend(root)
        real = ts_edges.available
        ts_edges.available = lambda: False
        try:
            seen = sorted(name_of(f, root) for f in backend._source_files())
        finally:
            ts_edges.available = real
        self.assertEqual(seen, ["app.py"],
                         "TypeScript entered the corpus with no parser to read it")

    @_needs_parser
    def test_and_they_are_IN_it_when_the_parser_is_present(self):
        root = _tree(_FULL)
        seen = sorted(name_of(f, root) for f in AstBackend(root)._source_files())
        self.assertEqual(seen, ["app.py", "src/other.ts", "src/widget.tsx"])

    def test_the_walk_prunes_node_modules_on_BOTH_paths(self):
        root = _tree(_FULL)
        for present in (True, False):
            real = ts_edges.available
            ts_edges.available = lambda: present
            try:
                seen = [name_of(f, root) for f in AstBackend(root)._source_files()]
            finally:
                ts_edges.available = real
            self.assertFalse([p for p in seen if "node_modules" in p], (present, seen))


class TheDegradationIsAnnouncedUpFront(unittest.TestCase):
    """⭐ G14 clause 3. doc 105 §9: *"If clause 3 is not built, re-open G14."*"""

    def test_a_TS_repo_with_NO_PARSER_gets_a_WARNING(self):
        surface = _repo({"src/a.tsx": "export const A = () => <div/>;\n"})
        findings = D.ts_parser_findings(surface, available=lambda: False)
        self.assertEqual([f.code for f in findings], ["graph-ts-absent"])
        self.assertEqual(findings[0].severity, "warning")

    def test_the_warning_NAMES_the_command_and_says_no_config_is_needed(self):
        surface = _repo({"src/a.tsx": "export const A = () => <div/>;\n"})
        detail = D.ts_parser_findings(surface, available=lambda: False)[0].detail
        self.assertIn("pip install 'mokata[graph-ts]'", detail)
        self.assertIn("mokata index", detail)

    def test_the_warning_says_the_answers_are_INCOMPLETE_not_merely_absent(self):
        """⚠ The user's real risk is an answer that LOOKS fine. The sentence has to say that."""
        surface = _repo({"src/a.tsx": "export const A = () => <div/>;\n"})
        detail = D.ts_parser_findings(surface, available=lambda: False)[0].detail
        self.assertIn("incomplete", detail.lower())

    def test_a_TS_repo_WITH_the_parser_is_told_nothing(self):
        surface = _repo({"src/a.tsx": "export const A = () => <div/>;\n"})
        self.assertEqual(D.ts_parser_findings(surface, available=lambda: True), [])

    def test_a_repo_with_NO_TYPESCRIPT_is_told_nothing(self):
        surface = _repo({"app.py": _PY})
        self.assertEqual(D.ts_parser_findings(surface, available=lambda: False), [])

    def test_TYPESCRIPT_ONLY_IN_node_modules_is_not_a_typescript_repo(self):
        """⚠ The check counts through the SAME corpus predicate and the SAME walk the walker uses.
        Two implementations of *"is this a TS repo"* is how a check comes to describe something
        other than the thing it is about."""
        surface = _repo({"node_modules/dep/index.ts": _VENDORED, "app.py": _PY})
        verdict, count = D.ts_parser_verdict(surface.root, available=lambda: False)
        self.assertEqual((verdict, count), (D.TS_NOT_APPLICABLE, 0))
        self.assertEqual(D.ts_parser_findings(surface, available=lambda: False), [])

    def test_a_DECLARATION_file_does_not_count_either(self):
        surface = _repo({"src/types.d.ts": "export declare const x: number;\n"})
        self.assertEqual(D.ts_parser_verdict(surface.root, available=lambda: False)[0],
                         D.TS_NOT_APPLICABLE)

    def test_an_UNREADABLE_probe_is_UNDECIDABLE_and_says_it_is_not_a_pass(self):
        """§7f. A check that could not run must not look like a check that passed."""
        surface = _repo({"src/a.tsx": "export const A = () => <div/>;\n"})

        def _boom():
            raise RuntimeError("planted")
        verdict, _count = D.ts_parser_verdict(surface.root, available=_boom)
        self.assertEqual(verdict, D.TS_UNDECIDABLE)
        findings = D.ts_parser_findings(surface, available=_boom)
        self.assertEqual([f.code for f in findings], ["graph-ts-unverifiable"])
        self.assertEqual(findings[0].severity, "info")
        self.assertIn("not a pass", findings[0].detail)

    def test_NOT_APPLICABLE_and_UNDECIDABLE_do_NOT_share_a_representation(self):
        self.assertNotEqual(D.TS_NOT_APPLICABLE, D.TS_UNDECIDABLE)

    def test_an_UNWALKABLE_TREE_is_UNDECIDABLE_not_NO_TYPESCRIPT(self):
        """⛔ THE COLLAPSE THIS REFUSES, and a mutant survived until this test existed.

        Reporting the ABSENCE of TypeScript you could not look for is byte-identical to a repo
        that genuinely has none — and this check exists precisely so a user is not told *fine*
        about a question nobody asked. The other UNDECIDABLE test plants a failing PROBE; this one
        plants a failing WALK, and they reach different handlers.
        """
        surface = _repo({"src/a.tsx": "export const A = () => <div/>;\n"})

        def _boom(_name):
            raise OSError("planted: the tree could not be read")

        verdict, count = D.ts_parser_verdict(surface.root, available=lambda: False, walker=_boom)
        self.assertEqual((verdict, count), (D.TS_UNDECIDABLE, 0))
        self.assertEqual([f.code for f in D.ts_parser_findings(surface, available=lambda: False)],
                         ["graph-ts-absent"],
                         "the real walk still works — this test must not have broken it globally")

    def test_the_two_UNDECIDABLE_paths_are_reached_by_DIFFERENT_failures(self):
        """A probe that raises and a walk that raises are two handlers, and a batch that only
        plants one leaves the other ungraded — which is exactly what happened."""
        surface = _repo({"src/a.tsx": "export const A = () => <div/>;\n"})

        def _boom_probe():
            raise RuntimeError("probe")

        def _boom_walk(_name):
            raise OSError("walk")

        self.assertEqual(D.ts_parser_verdict(surface.root, available=_boom_probe)[0],
                         D.TS_UNDECIDABLE)
        self.assertEqual(D.ts_parser_verdict(surface.root, available=lambda: False,
                                             walker=_boom_walk)[0], D.TS_UNDECIDABLE)

    def test_ONE_typescript_file_is_enough(self):
        """⚠ The threshold is deliberately at the low end: a single `.ts` file whose symbols are
        missing from the graph is still a wrong answer, and a threshold chosen to avoid seeming
        noisy would hide the smallest instance of the defect. What keeps it quiet is that it fires
        only when the parser is ABSENT."""
        self.assertEqual(D.TS_MINIMUM_FILES, 1)
        surface = _repo({"only.ts": "export function f(){}\n"})
        self.assertEqual(D.ts_parser_verdict(surface.root, available=lambda: False)[0],
                         D.TS_UNPARSED)

    def test_the_check_REACHES_a_full_diagnose_run(self):
        """⛔ A check nothing calls grades nothing — `REACHABILITY`, filed six times here."""
        surface = _repo({"src/a.tsx": "export const A = () => <div/>;\n"})
        codes = [f.code for f in D.diagnose(surface).findings]
        if ts_edges.available():
            self.assertNotIn("graph-ts-absent", codes)
        else:
            self.assertIn("graph-ts-absent", codes)

    def test_the_count_in_the_sentence_is_DERIVED(self):
        surface = _repo({"a.ts": "export function a(){}\n", "b/c.tsx": "export const C = () => 1;\n"})
        verdict, count = D.ts_parser_verdict(surface.root, available=lambda: False)
        self.assertEqual((verdict, count), (D.TS_UNPARSED, 2))
        self.assertIn("2 TypeScript file(s)",
                      D.ts_parser_findings(surface, available=lambda: False)[0].detail)


if __name__ == "__main__":
    unittest.main()
