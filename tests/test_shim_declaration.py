"""0.0.17 stage 2 — SHIM-FALSE-GREEN: no test runs Postgres SQL on SQLite without declaring it.

THE DEFECT. A `_PgShim`-shaped test double executes the Postgres backend's SQL on SQLite after
rewriting it. A suite using one is green against a TRANSLATION of the query, not against the query,
so `green` meant two incompatible things at once and nothing could tell them apart — doc 85 §7g,
the master defect class of 0.0.17. MEASURED at DB.S7b: a `WITH RECURSIVE` traversal run through a
shim returns `[('a',0),('b',1),('c',2)]`, i.e. a "Postgres traversal" test would PASS while
comparing SQLite against itself.

WHY THE PRIOR GUARD WAS NOT ENOUGH. `test_db_s7b_bounded_expansion.NoShimInTraversalTests` greps
two named files for the string `_PgShim`. It is correct and it stays, but it is (a) per-suite, so
every other file is open, and (b) NAME-BASED, so it works by coincidence until an author picks a
different name. That is not hypothetical: deriving the sites STRUCTURALLY at this stage found
SEVEN translating doubles where every name-based count had said three or four. `_PgVectorShim`
(`test_db_s4_pgvector.py`) and `SharedPg` (`test_ms_s5_single_flusher.py`, `test_ms_s7_stress.py`)
were invisible to the name, and two of those three translate MORE than the ones that were found —
`SharedPg` rewrites `now()` into `CURRENT_TIMESTAMP`, swapping a transaction-stable clock for a
statement-stable one underneath tests whose whole subject is cross-process write ordering.

THE THREE CHECKS HERE, and what each cannot see:

  1. NO UNDECLARED TRANSLATION SITE — structurally detect the execution of a rewritten SQL string
     anywhere under `tests/`. LIMIT: it sees the string-rewriting idioms (`.replace`, `.format`,
     `re.sub`) reaching an `execute`. A rewrite hidden behind an arbitrary helper OBJECT would
     evade it, which is exactly why check 3 exists.
  2. THE DECLARED SET IS DERIVED AND BIDIRECTIONAL — every translating double in the tree is
     declared, and every declaration still corresponds to one. Asserted as MEMBERSHIP, never as a
     COUNT: a count cannot tell "we closed one" from "we dropped one", and this row exists because
     a count was wrong twice.
  3. THE BASE CLASS IS NOT BYPASSED — a subclass may not override `execute` without delegating to
     `super().execute`, so a declared rule list cannot become a rule list nobody applies. A
     declaration that isn't enforced is a caller list asserted in a comment.

Each check has a companion test proving THE CHECK ITSELF CAN FIRE, because a guard that cannot
fail is the defect it was written to stop, wearing a different hat.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from _translating import (
    DIVERGENCE_RECURSIVE_CTE,
    DIVERGENCE_TEXT_COLLATION,
    Declaration,
    EmulatedFunction,
    EngineBasis,
    Interception,
    Rewrite,
    TranslatingConnection,
    UndeclaredTranslation,
    placeholder_rewrite,
)

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)


def _rel(full, root):
    """`full` spelled relative to the PARENT of the tree being swept.

    For the real sweeps (`root` is `TESTS_DIR`) that parent IS `REPO_ROOT`, so the reported keys
    stay `tests/<file>.py` — the form `ALLOWED_TRANSLATION_SITES` and `MECHANISM_OWN_TESTS` are
    written in. Nothing about the tree's own naming changes.

    What changes is that the base is DERIVED FROM `root` instead of being the module-level
    `REPO_ROOT` constant, which was only correct while `root` happened to sit under the checkout.
    The three "the check can actually fire" tests hand these sweeps a tempdir instead, and on
    Windows the tempdir (`C:`) and the checkout (`D:`) are different mounts — where
    `os.path.relpath` does not return a longer path, it RAISES `ValueError`. Deriving the base from
    the walked root makes both arguments share a mount by construction, so the base can no longer
    disagree with what is being walked. POSIX hid this for the same reason it hides most of them:
    it answers with `../../..` instead of refusing."""
    return os.path.relpath(full, os.path.dirname(os.path.normpath(root))).replace(os.sep, "/")


# The ONE sanctioned translation mechanism. Every rewrite in the tree happens here or it is a
# finding. Named by PATH, so the check does not depend on what any class is called.
SANCTIONED_MECHANISM = "tests/_translating.py"

# This file itself builds throwaway translating doubles in order to prove the ENFORCEMENT fires.
# Excluded by name and by reason, never by a quiet filter: the doubles here are constructed inside
# test bodies against a scratch table, and no code under test ever sees one.
MECHANISM_OWN_TESTS = "tests/test_shim_declaration.py"


# ===================================================================== the allow-list (DERIVED)
# Keyed by (path, enclosing scope) and carrying the reason each entry EARNS its place. Never a
# count: the assertions below check membership in both directions, so a fifth site cannot hide
# behind a number and a removed site cannot leave a stale entry behind.
ALLOWED_TRANSLATION_SITES = {
    ("tests/test_db_s7b_bounded_expansion.py",
     "NoShimInTraversalTests.test_the_shim_really_can_run_a_recursive_cte"):
        "THE EVIDENCE, not a use. This test performs the translation in order to MEASURE that "
        "SQLite runs a Postgres recursive CTE perfectly — the finding the DB.S7b guard exists to "
        "act on. It asserts the hazard rather than depending on it, and it executes against a "
        "throwaway connection, never against code under test.",

    ("tests/integration/test_db_s7a_live_db.py", "_provision_v4"):
        "SAME-ENGINE substitution, so not this defect at all. It edits a schema-version literal "
        "inside a provisioning DDL and runs it on LIVE POSTGRES via MOKATA_TEST_DSN — the SQL is "
        "authored for Postgres and executed by Postgres. Nothing is standing in for anything.",
}

# Every module holding a translating double, with the suite it declares. Derived by AST below and
# asserted BOTH WAYS against this map.
DECLARED_TRANSLATING_MODULES = {
    "tests/test_db_s2a_pushdown.py": "DB.S2a mtype/status pushdown",
    "tests/test_db_s2b_scope_pushdown.py": "DB.S2b scope/precedence pushdown",
    "tests/test_db_s3_fts.py": "DB.S3 Postgres full-text search",
    "tests/test_db_s4_pgvector.py": "DB.S4 pgvector semantic tier",
    "tests/test_db_s7c2_stale_ref.py": "DB.S7c2 stale-ref index epoch",
    "tests/test_ms_s5_single_flusher.py": "MS.S5 single-flusher (cross-process exactly-once)",
    "tests/test_ms_s7_stress.py": "MS.S7 exactly-once under stress",
}


# ============================================================================ the detectors
_REWRITE_METHODS = {"replace", "format"}
_RE_REWRITE = {"sub", "subn"}
_EXEC_METHODS = {"execute", "executemany", "executescript"}


def _is_rewrite(node):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in _REWRITE_METHODS:
            return f".{node.func.attr}()"
        if node.func.attr in _RE_REWRITE:
            return f"re.{node.func.attr}()"
    return None


def _scan_scope(fn, path, scope, out):
    """Flag `execute(X)` where X passed through a string rewrite, directly or via a local."""
    rewritten = set()

    def rewritten_expr(node):
        for sub in ast.walk(node):
            why = _is_rewrite(sub)
            if why:
                return why
            if isinstance(sub, ast.Name) and sub.id in rewritten:
                return f"via `{sub.id}`"
        return None

    for stmt in ast.walk(fn):
        if isinstance(stmt, ast.Assign) and stmt.value is not None and rewritten_expr(stmt.value):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    rewritten.add(target.id)
        if isinstance(stmt, ast.Call) and isinstance(stmt.func, ast.Attribute) \
                and stmt.func.attr in _EXEC_METHODS and stmt.args:
            why = rewritten_expr(stmt.args[0])
            if why:
                out.append({"path": path, "line": stmt.lineno, "scope": scope, "why": why,
                            "code": ast.unparse(stmt.args[0])[:120]})


def translation_sites(root):
    """Every execution of a rewritten SQL string under `root`, structurally."""
    found = []
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            rel = _rel(full, root)
            try:
                with open(full, "r", encoding="utf-8") as fh:
                    # filename= so a SyntaxWarning from a swept file names THAT file rather than
                    # `<unknown>` — the sweep reads the whole tree, so its noise must be traceable.
                    tree = ast.parse(fh.read(), filename=full)
            except (SyntaxError, UnicodeDecodeError):        # pragma: no cover
                continue

            def walk(node, stack):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        _scan_scope(child, rel, ".".join(stack + [child.name]), found)
                        walk(child, stack + [child.name])
                    elif isinstance(child, ast.ClassDef):
                        walk(child, stack + [child.name])
                    else:
                        walk(child, stack)
            walk(tree, [])
    return found


def undeclared_sites(sites, allow_list):
    """THE GATE, as a pure function so it can be graded on synthetic input.

    It was a loop inside the test until the stage-2 mutation run, where killing the allow-list
    check outright came back GREEN: after the retrofit every real site is allow-listed, so the
    loop had no offender to catch and passed whether or not it worked. A guard whose only
    evidence is that the tree currently happens to be clean is the pin-that-grades-nothing shape
    three stages running have found. Now the gate takes its inputs and the tests below feed it
    offenders directly."""
    offenders = []
    for site in sites:
        if site["path"] == SANCTIONED_MECHANISM:
            continue
        if (site["path"], site["scope"]) in allow_list:
            continue
        offenders.append(
            f"{site['path']}:{site['line']} [{site['scope']}] executes a REWRITTEN SQL "
            f"string ({site['why']}): {site['code']}")
    return offenders


def stale_entries(sites, allow_list):
    """The reverse gate, pure for the same reason — `stale = []` also survived mutation."""
    live = {(s["path"], s["scope"]) for s in sites}
    return sorted(k for k in allow_list if k not in live)


def module_set_drift(found, expected):
    """The bidirectional membership comparison, pure so it too can be graded.

    Third instance of the same lesson in one mutation run: `assertEqual(expected, found)` mutated
    to `assertEqual(expected, expected)` came back GREEN, because a comparison written inline
    inside a test has nothing standing outside it to check that it compares the right two things.
    Returns (undeclared, no_longer_translating) — empty pair means no drift."""
    return sorted(found - expected), sorted(expected - found)


def translating_classes(root):
    """Every class under `root` that subclasses `TranslatingConnection`, and whether it overrides
    `execute` without delegating to `super()`."""
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            rel = _rel(full, root)
            try:
                with open(full, "r", encoding="utf-8") as fh:
                    # filename= so a SyntaxWarning from a swept file names THAT file rather than
                    # `<unknown>` — the sweep reads the whole tree, so its noise must be traceable.
                    tree = ast.parse(fh.read(), filename=full)
            except (SyntaxError, UnicodeDecodeError):        # pragma: no cover
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                bases = {ast.unparse(b) for b in node.bases}
                if "TranslatingConnection" not in bases:
                    continue
                override = None
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "execute":
                        override = item
                delegates = True
                if override is not None:
                    delegates = any(
                        isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                        and c.func.attr == "execute"
                        and isinstance(c.func.value, ast.Call)
                        and getattr(c.func.value.func, "id", None) == "super"
                        for c in ast.walk(override))
                out.append({"path": rel, "class": node.name,
                            "overrides_execute": override is not None,
                            "delegates": delegates})
    return out


def declaring_modules(root):
    """Every module under `root` that CONSTRUCTS a Declaration, with the suite names it names."""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            rel = _rel(full, root)
            try:
                with open(full, "r", encoding="utf-8") as fh:
                    # filename= so a SyntaxWarning from a swept file names THAT file rather than
                    # `<unknown>` — the sweep reads the whole tree, so its noise must be traceable.
                    tree = ast.parse(fh.read(), filename=full)
            except (SyntaxError, UnicodeDecodeError):        # pragma: no cover
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Declaration":
                    for kw in node.keywords:
                        if kw.arg == "suite" and isinstance(kw.value, ast.Constant):
                            out.setdefault(rel, set()).add(kw.value.value)
    return out


# ================================================================ 1 · no undeclared translation
class NoUndeclaredTranslationTest(unittest.TestCase):
    """The pin that matters: a suite that translates Postgres SQL and does not declare it cannot
    be green."""

    def test_every_translation_site_is_the_mechanism_or_allow_listed(self):
        offenders = undeclared_sites(translation_sites(TESTS_DIR), ALLOWED_TRANSLATION_SITES)
        self.assertEqual([], offenders, "\n".join([
            "",
            "UNDECLARED TRANSLATION. These sites execute Postgres SQL on another engine after",
            "rewriting it, without declaring the translation. A green here means 'this passed",
            "against a translation of the query', which is not what a reader will take it to mean.",
            "",
            "Route the double through `_translating.TranslatingConnection` with a Declaration, or",
            "add a reasoned entry to ALLOW_LISTED_TRANSLATION_SITES if it genuinely is not one.",
            "",
        ] + offenders))

    def test_no_allow_list_entry_is_stale(self):
        """The other direction. An entry whose site has moved or gone silently widens the
        allow-list, and the next translation that lands in that scope inherits permission nobody
        granted it. Membership both ways, never a count."""
        stale = stale_entries(translation_sites(TESTS_DIR), ALLOWED_TRANSLATION_SITES)
        self.assertEqual([], stale,
                         f"allow-listed sites that no longer translate anything: {stale}. Delete "
                         f"the entries — a permission outliving its reason is how an allow-list "
                         f"stops being a reviewed decision.")

    def test_the_sweep_can_actually_fire(self):
        """The guard's own mutation, run inline. Without this the sweep could be vacuously green
        forever — three stages running have found a pin that graded nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            planted = os.path.join(tmp, "test_planted_shim.py")
            with open(planted, "w", encoding="utf-8") as fh:
                fh.write(
                    "import sqlite3\n"
                    "\n"
                    "class _SomethingEntirelyElse:\n"
                    "    '''Deliberately NOT named like a shim — the sweep must not need the "
                    "name.'''\n"
                    "    def __init__(self):\n"
                    "        self._c = sqlite3.connect(':memory:')\n"
                    "    def execute(self, sql, params=()):\n"
                    "        return self._c.execute(sql.replace('%s', '?'), tuple(params))\n")
            caught = translation_sites(tmp)
        self.assertEqual(1, len(caught), f"the sweep missed a planted translating double: {caught}")
        self.assertEqual("_SomethingEntirelyElse.execute", caught[0]["scope"])

    def test_the_allow_list_GATE_can_actually_fire(self):
        """Added because killing the gate came back GREEN in the stage-2 mutation run.

        The tree's real sites are all allow-listed, so the gate never sees an offender in normal
        operation — meaning nothing proved it would report one. These synthetic sites do."""
        offender = {"path": "tests/test_new_thing.py", "scope": "_Fake.execute",
                    "line": 12, "why": ".replace()", "code": "sql.replace('%s', '?')"}
        allowed = {"path": "tests/test_known.py", "scope": "_Known.execute",
                   "line": 5, "why": ".replace()", "code": "sql.replace('%s', '?')"}
        mechanism = {"path": SANCTIONED_MECHANISM, "scope": "TranslatingConnection.execute",
                     "line": 1, "why": ".replace()", "code": "run"}
        allow_list = {("tests/test_known.py", "_Known.execute"): "reasoned entry"}

        reported = undeclared_sites([offender, allowed, mechanism], allow_list)
        self.assertEqual(1, len(reported), f"the gate did not report the offender: {reported}")
        self.assertIn("tests/test_new_thing.py:12", reported[0])
        self.assertEqual([], undeclared_sites([allowed, mechanism], allow_list))

    def test_the_STALE_check_can_actually_fire(self):
        """Added for the same reason: `stale = []` survived mutation, because no entry is stale
        today. Feed it one that is."""
        live = [{"path": "tests/test_known.py", "scope": "_Known.execute",
                 "line": 5, "why": ".replace()", "code": "x"}]
        allow_list = {
            ("tests/test_known.py", "_Known.execute"): "still translating",
            ("tests/test_gone.py", "_Gone.execute"): "no longer translates anything",
        }
        self.assertEqual([("tests/test_gone.py", "_Gone.execute")],
                         stale_entries(live, allow_list))
        self.assertEqual([], stale_entries(live, {
            ("tests/test_known.py", "_Known.execute"): "still translating"}))

    def test_the_sweep_does_not_fire_on_prose(self):
        """A file must be able to EXPLAIN the trap without tripping the guard that enforces it —
        the same property the DB.S7b guard pinned for its own name-based check."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "test_prose.py"), "w", encoding="utf-8") as fh:
                fh.write(
                    '"""A shim would do `sql.replace("%s", "?")` and execute it, proving '
                    'nothing."""\n'
                    "\n"
                    "def documented(conn, sql):\n"
                    "    # conn.execute(sql.replace('%s', '?'))  <- exactly what we must not do\n"
                    "    return conn.execute(sql)\n")
            self.assertEqual([], translation_sites(tmp))


# ================================================================ 2 · the declared set is derived
class DeclaredSetIsDerivedTest(unittest.TestCase):
    """The allow-list is DERIVED and asserted in both directions. Doc 84 is explicit that a sweep
    whose allow-list is a literal count cannot notice the next one — and it was right twice: the
    row said three, its own correction said four, and the structural derivation says seven."""

    def test_every_translating_module_is_declared(self):
        found = set(declaring_modules(TESTS_DIR)) - {MECHANISM_OWN_TESTS}
        undeclared, gone = module_set_drift(found, set(DECLARED_TRANSLATING_MODULES))
        self.assertEqual(([], []), (undeclared, gone), "\n".join([
            "",
            f"undeclared translating modules: {undeclared}",
            f"declared but no longer translating: {gone}",
            "Update DECLARED_TRANSLATING_MODULES deliberately — this set is a reviewed decision,",
            "not a tally to be nudged until the test goes green.",
        ]))

    def test_the_module_set_check_can_actually_fire(self):
        """Added because asserting the declared set AGAINST ITSELF came back GREEN in the stage-2
        mutation run. Drift in either direction has to be reported, so both are fed in here."""
        declared = {"tests/a.py", "tests/b.py"}
        self.assertEqual(([], []), module_set_drift(declared, declared))
        self.assertEqual((["tests/c.py"], []),
                         module_set_drift(declared | {"tests/c.py"}, declared))
        self.assertEqual(([], ["tests/b.py"]),
                         module_set_drift({"tests/a.py"}, declared))

    def test_each_module_declares_the_suite_it_claims(self):
        found = declaring_modules(TESTS_DIR)
        for module, suite in sorted(DECLARED_TRANSLATING_MODULES.items()):
            with self.subTest(module=module):
                self.assertIn(suite, found.get(module, set()),
                              f"{module} does not declare the suite `{suite}`")

    def test_membership_is_asserted_not_size(self):
        """The guard against the guard. If this file ever asserts a COUNT of shims, it can no
        longer tell 'we closed one' from 'we dropped one' — the exact confusion the exception-set
        work taught, and the reason doc 84 forbade a hardcoded 3."""
        with open(os.path.abspath(__file__), "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in ("assertEqual", "assertLen") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Call) and getattr(first.func, "id", None) == "len" \
                        and "translating" in ast.unparse(node).lower():
                    self.fail(f"line {node.lineno} asserts a COUNT of translating doubles; assert "
                              f"MEMBERSHIP instead")


# ================================================================ 3 · the base is not bypassed
class BaseClassIsNotBypassedTest(unittest.TestCase):
    """A declared rule list that the shim does not obey is CALLER-LIST-UNPINNED one layer over:
    a claim nothing derives. Declaration and enforcement land together or neither is worth much."""

    def test_no_subclass_overrides_execute_without_delegating(self):
        offenders = [f"{c['path']}::{c['class']}" for c in translating_classes(TESTS_DIR)
                     if c["overrides_execute"] and not c["delegates"]]
        self.assertEqual([], offenders,
                         f"these subclasses override `execute` without calling `super().execute`, "
                         f"so the declared rules are not what runs: {offenders}")

    def test_the_bypass_check_can_actually_fire(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "test_bypass.py"), "w", encoding="utf-8") as fh:
                fh.write(
                    "class Sneaky(TranslatingConnection):\n"
                    "    def execute(self, sql, params=()):\n"
                    "        return self._c.execute(sql, params)\n"
                    "\n"
                    "class Honest(TranslatingConnection):\n"
                    "    def execute(self, sql, params=()):\n"
                    "        return super().execute(sql, params)\n")
            found = {c["class"]: c for c in translating_classes(tmp)}
        self.assertFalse(found["Sneaky"]["delegates"])
        self.assertTrue(found["Honest"]["delegates"])

    def test_every_translating_class_in_the_tree_is_found(self):
        """The detector must see all seven; if it silently saw none, check 3 would pass vacuously."""
        modules = {c["path"] for c in translating_classes(TESTS_DIR)} - {MECHANISM_OWN_TESTS}
        self.assertEqual(set(DECLARED_TRANSLATING_MODULES), modules)


# ============================================================== 4 · the split representation
class BasisIsASplitRepresentationTest(unittest.TestCase):
    """Doc 85 §7g: an absent answer and a real answer must never share a representation. Here the
    two answers are 'this passed against Postgres' and 'this passed against a translation'."""

    def test_the_three_bases_are_distinct(self):
        self.assertEqual(3, len(set(EngineBasis.ALL)))
        self.assertNotEqual(EngineBasis.POSTGRES_LIVE, EngineBasis.SQLITE_TRANSLATED)
        self.assertNotEqual(EngineBasis.SQLITE_NATIVE, EngineBasis.SQLITE_TRANSLATED)

    def test_a_translating_double_reports_the_translated_basis(self):
        shim = _declared_double()
        self.addCleanup(shim.close)
        self.assertEqual(EngineBasis.SQLITE_TRANSLATED, shim.basis)

    def test_the_label_names_the_basis_and_what_is_not_proven(self):
        shim = _declared_double()
        self.addCleanup(shim.close)
        label = shim.label()
        self.assertIn(EngineBasis.SQLITE_TRANSLATED, label)
        self.assertIn("NOT PROVEN HERE", label)
        self.assertIn("nothing at all", label)

    def test_a_declaration_must_state_what_it_does_not_prove(self):
        """An empty `not_proven` is the false green wearing a declaration."""
        with self.assertRaises(UndeclaredTranslation):
            Declaration(suite="silent", reason="none given", rewrites=(placeholder_rewrite(),))

    def test_every_shipped_declaration_enumerates_its_rules_exactly(self):
        """Each suite's declaration names every rewrite, function and interception it applies. An
        APPROXIMATE declaration is worse than none, because it looks checked."""
        import importlib
        for module_path in sorted(DECLARED_TRANSLATING_MODULES):
            name = os.path.basename(module_path)[:-3]
            module = importlib.import_module(name)
            declarations = [v for v in vars(module).values() if isinstance(v, Declaration)]
            with self.subTest(module=module_path):
                self.assertTrue(declarations, f"{module_path} declares nothing")
                for decl in declarations:
                    self.assertTrue(decl.not_proven)
                    self.assertTrue(decl.reason.strip())
                    self.assertEqual(len(decl.rule_names()), len(set(decl.rule_names())),
                                     "two rules share a name, so the log cannot attribute either")


# ============================================================== 5 · runtime refusal
def _declared_double(**kwargs):
    class _Double(TranslatingConnection):
        def __init__(self):
            super().__init__(Declaration(
                suite="unit-under-test",
                reason="exercises the enforcement itself",
                not_proven=("nothing at all — this double exists to test the base class",),
                **kwargs))
            self._c.execute("CREATE TABLE t (id TEXT, n INTEGER)")
    return _Double()


class RuntimeRefusalTest(unittest.TestCase):
    """The other half of the pin. The sweep is static and sees the tree; this sees the STATEMENT,
    so a rewrite that only a particular query needs cannot slip past."""

    def test_an_undeclared_placeholder_is_refused_not_silently_run(self):
        """The nastiest case: SQLite reads a surviving `%s` as a LITERAL rather than raising, so
        the statement runs, matches nothing, and the test goes GREEN on an empty result."""
        double = _declared_double()
        self.addCleanup(double.close)
        with self.assertRaises(UndeclaredTranslation) as caught:
            double.execute("SELECT id FROM t WHERE id = %s", ("x",))
        self.assertIn("%s", str(caught.exception))

    def test_a_declared_rewrite_is_applied_and_counted(self):
        double = _declared_double(rewrites=(placeholder_rewrite(),))
        self.addCleanup(double.close)
        double.execute("INSERT INTO t (id, n) VALUES (%s, %s)", ("a", 1))
        self.assertEqual(1, double.translations["placeholder"])
        self.assertEqual([("a", 1)], double.execute("SELECT id, n FROM t").fetchall())

    def test_a_recursive_cte_is_refused_by_default(self):
        """MEASURED at DB.S7b: SQLite runs this perfectly, which is precisely why it must not be
        allowed to. The refusal is now in the TOOL, not in a per-suite name grep."""
        double = _declared_double(rewrites=(placeholder_rewrite(),))
        self.addCleanup(double.close)
        with self.assertRaises(UndeclaredTranslation) as caught:
            double.execute("WITH RECURSIVE walk(x) AS (SELECT id FROM t) SELECT * FROM walk")
        self.assertIn("WITH RECURSIVE", str(caught.exception))

    def test_a_recursive_cte_can_be_accepted_explicitly(self):
        """The concession exists, and it lands in the DECLARATION where a reader and the sweep
        both see it, rather than in nobody's head."""
        double = _declared_double(rewrites=(placeholder_rewrite(),),
                                  accepts_divergence=(DIVERGENCE_RECURSIVE_CTE,))
        self.addCleanup(double.close)
        double.execute("WITH RECURSIVE walk(x) AS (SELECT id FROM t) SELECT * FROM walk")

    def test_ordering_on_a_text_column_is_refused(self):
        """SQLite sorts text BINARY, Postgres by the database collation, so identical rows can
        legitimately come back in a different order."""
        double = _declared_double(rewrites=(placeholder_rewrite(),))
        self.addCleanup(double.close)
        with self.assertRaises(UndeclaredTranslation) as caught:
            double.execute("SELECT id FROM t ORDER BY id ASC")
        self.assertIn("collation", str(caught.exception))

    def test_ordering_on_a_numeric_column_is_fine(self):
        double = _declared_double(rewrites=(placeholder_rewrite(),))
        self.addCleanup(double.close)
        double.execute("SELECT id FROM t ORDER BY n DESC")

    def test_a_text_column_inside_a_function_call_is_not_flagged(self):
        """The check must not cry wolf: `ORDER BY f(id)` sorts on the NUMBER `f` returned, not on
        the column. A guard that fires on innocent queries is a guard someone switches off."""
        double = _declared_double(rewrites=(placeholder_rewrite(),))
        self.addCleanup(double.close)
        double._c.create_function("scorer", 1, lambda v: len(v or ""))
        double.execute("SELECT id FROM t ORDER BY scorer(id) DESC")

    def test_an_emulated_function_accounts_for_its_own_name(self):
        double = _declared_double(
            rewrites=(placeholder_rewrite(),),
            functions=(EmulatedFunction("ts_rank", 2, lambda a, b: 1.0, "test double"),))
        self.addCleanup(double.close)
        double.execute("SELECT id FROM t WHERE ts_rank(id, 'x') > 0")

    def test_an_unaccounted_postgres_construct_is_refused(self):
        double = _declared_double(rewrites=(placeholder_rewrite(),))
        self.addCleanup(double.close)
        with self.assertRaises(UndeclaredTranslation) as caught:
            double.execute("SELECT id::text FROM t")
        self.assertIn("::", str(caught.exception))

    def test_an_interception_answers_without_reaching_the_engine(self):
        marker = []

        def respond(conn, _sql, _params):
            marker.append("intercepted")
            return conn.execute("SELECT 1")

        double = _declared_double(
            rewrites=(placeholder_rewrite(),),
            interceptions=(Interception("to_regclass", lambda s: "to_regclass" in s, respond,
                                        "test double"),))
        self.addCleanup(double.close)
        double.execute("SELECT to_regclass(%s)", ("t",))
        self.assertEqual(["intercepted"], marker)
        self.assertEqual(1, double.translations["to_regclass"])

    def test_a_double_cannot_be_built_without_a_declaration(self):
        class _Undeclared(TranslatingConnection):
            def __init__(self):
                super().__init__(None)
        with self.assertRaises(UndeclaredTranslation):
            _Undeclared()


# ============================================================== 6 · the prior guard still holds
class PriorGuardIsSubsumedNotBrokenTest(unittest.TestCase):
    """DB.S7b's per-suite guard is name-based and narrow, and this sweep is neither — but it is
    kept, because it also pins the MEASUREMENT behind the whole row."""

    def test_the_db_s7b_guard_still_exists(self):
        path = os.path.join(TESTS_DIR, "test_db_s7b_bounded_expansion.py")
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("class NoShimInTraversalTests", source)
        self.assertIn("test_the_shim_really_can_run_a_recursive_cte", source)

    def test_traversal_suites_still_construct_no_translating_double(self):
        """The same property DB.S7b asserts by name, asserted here STRUCTURALLY — so it holds for
        a double called anything at all."""
        swept = {"tests/test_db_s7b_bounded_expansion.py",
                 "tests/integration/test_db_s7b_live_db.py"}
        offenders = [c for c in translating_classes(TESTS_DIR) if c["path"] in swept]
        self.assertEqual([], offenders)


if __name__ == "__main__":                                        # pragma: no cover
    unittest.main()
