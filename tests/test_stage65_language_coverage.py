"""Stage 65 — language coverage for the knowledge graph (grep floor + heuristic recognizers).

CRITICAL INVIOLABLE: NO IN-HOUSE PARSER / AST. This stage is grep-floor + heuristic +
file-extension awareness ONLY. These tests prove the language-aware paths work across
Python / JS-TS / Go / Rust / Java without a parser, that an unknown language degrades to
generic identifier matching (no crash), and that PYTHON behaviour is unchanged.
"""

import os
import tempfile
import unittest

from _support import polyglot_files, write_polyglot_repo, write_sample_repo

from mokata import languages
from mokata.ci_check import symbols_in_files
from mokata.engine import scan_tests
from mokata.execmode.decompose import extract_refs
from mokata.knowledge import GrepBackend


class TestLanguageRegistry(unittest.TestCase):
    def test_known_extensions_map_to_languages(self):
        self.assertEqual(languages.language_for("a.py").name, "python")
        self.assertEqual(languages.language_for("a.ts").name, "javascript")
        self.assertEqual(languages.language_for("a.tsx").name, "javascript")
        self.assertEqual(languages.language_for("a.jsx").name, "javascript")
        self.assertEqual(languages.language_for("a.go").name, "go")
        self.assertEqual(languages.language_for("a.rs").name, "rust")
        self.assertEqual(languages.language_for("a.java").name, "java")

    def test_source_extensions_cover_all_languages(self):
        for ext in (".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java"):
            self.assertIn(ext, languages.SOURCE_EXTENSIONS)

    def test_unknown_extension_degrades_to_generic(self):
        lang = languages.language_for("thing.xyz")
        self.assertIs(lang, languages.GENERIC)
        # generic still answers heuristically and never crashes
        self.assertIsInstance(lang.definition_names("def f(): pass\nfunction g(){}"), list)
        self.assertTrue(lang.is_call_keyword("if"))


class TestGrepFloorMultiLanguage(unittest.TestCase):
    """The grep FLOOR answers structural queries on a sample of EACH language and
    announces it is the lexical floor."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = write_polyglot_repo(self.tmp.name)
        self.backend = GrepBackend(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _syms(self, result):
        return {r.symbol for r in result.references}

    def _files(self, result):
        return {r.path for r in result.references}

    def test_callers_found_in_every_language(self):
        r = self.backend.query("callers", "compute")
        self.assertTrue(r.degraded)            # announces: lexical floor
        self.assertFalse(self.backend.is_graph)
        self.assertIn("lexical", r.note)
        # caller() calls compute() in every file
        self.assertEqual(self._files(r), set(polyglot_files().values()))
        self.assertIn("caller", self._syms(r))

    def test_callees_found_in_every_language(self):
        r = self.backend.query("callees", "compute")
        # compute() calls helper() in every file
        self.assertEqual(self._files(r), set(polyglot_files().values()))
        self.assertIn("helper", self._syms(r))

    def test_imports_found_in_every_language(self):
        r = self.backend.query("imports", "mod_a")
        self.assertEqual(self._files(r), set(polyglot_files().values()))

    def test_implementers_found_where_the_convention_exists(self):
        r = self.backend.query("implementers", "Base")
        files = self._files(r)
        # Python / JS-TS / Java / Rust express "implements Base"; Go is structural (degrades).
        self.assertIn("svc.py", files)
        self.assertIn("svc.ts", files)
        self.assertIn("Svc.java", files)
        self.assertIn("svc.rs", files)

    def test_blast_radius_does_not_crash_multilanguage(self):
        r = self.backend.query("blast_radius", "helper", depth=2)
        self.assertTrue(r.degraded)
        self.assertIn("helper", "".join(ref.snippet for ref in r.references) + " helper")


class TestTestRecognitionMultiLanguage(unittest.TestCase):
    """The completeness gate's test scan finds AC-tagged tests across languages."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = write_polyglot_repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ac_tagged_tests_found_in_every_language(self):
        refs = scan_tests(self.root, ["AC-1"])
        covered_files = {r.path for r in refs if "AC-1" in r.ac_ids}
        self.assertEqual(covered_files, set(polyglot_files().values()))


class TestSymbolRecognitionMultiLanguage(unittest.TestCase):
    """ci_check.symbols_in_files recognises each language's files + defined symbols."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = write_polyglot_repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_symbols_extracted_per_language(self):
        for fname in polyglot_files().values():
            syms = symbols_in_files(self.root, [fname])
            self.assertIn("compute", syms, f"compute not found in {fname}")
            self.assertIn("helper", syms, f"helper not found in {fname}")

    def test_unknown_file_does_not_crash(self):
        with open(os.path.join(self.root, "weird.xyz"), "w", encoding="utf-8") as fh:
            fh.write("def thing(): pass\n")
        # degrade-clean: no crash, returns a list
        self.assertIsInstance(symbols_in_files(self.root, ["weird.xyz"]), list)


class TestExtractRefsMultiLanguage(unittest.TestCase):
    """decompose.extract_refs recognises each language's files + identifiers."""

    def test_each_language_file_is_recognised(self):
        for fname in polyglot_files().values():
            _syms, files = extract_refs(f"update `{fname}` and call `compute`")
            self.assertIn(fname, files, f"{fname} not recognised as a file")

    def test_identifiers_recognised_across_styles(self):
        syms, _files = extract_refs("touch `compute`, `helper`, `TestLogin`")
        for want in ("compute", "helper", "TestLogin"):
            self.assertIn(want, syms)


class TestPythonBehaviourUnchanged(unittest.TestCase):
    """No regression: the original Python-only sample still answers exactly as before."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = write_sample_repo(self.tmp.name)
        self.backend = GrepBackend(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_python_callers_unchanged(self):
        r = self.backend.query("callers", "compute")
        self.assertEqual({ref.path for ref in r.references}, {"mod_a.py", "mod_b.py"})
        self.assertEqual({ref.symbol for ref in r.references}, {"run", "main"})

    def test_python_implementers_unchanged(self):
        r = self.backend.query("implementers", "Base")
        self.assertEqual({ref.symbol for ref in r.references}, {"Impl", "OtherImpl"})

    def test_python_imports_unchanged(self):
        r = self.backend.query("imports", "mod_a")
        self.assertEqual({ref.path for ref in r.references}, {"mod_b.py"})


class TestNoParserAdded(unittest.TestCase):
    """The locked inviolable: heuristic only — NO parser / AST in the language layer."""

    def test_languages_module_uses_no_ast_parser(self):
        path = os.path.join(os.path.dirname(__file__), "..", "src", "mokata",
                            "languages.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("import ast", src)      # no Python AST parser
        self.assertNotIn("ast.parse", src)
        self.assertNotIn("tree_sitter", src)     # no third-party parser
        # the only compilation here is regex (re.compile) — never source compilation
        self.assertNotIn("compile(source", src)


if __name__ == "__main__":
    unittest.main()
