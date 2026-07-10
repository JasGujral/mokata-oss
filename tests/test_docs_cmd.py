"""RT.S3 A2 (rework) — /mokata:docs: a site pointer, NOT a bundled-docs browser.

The product docs live ONLY at repo-root docs/ (the single source mkdocs builds the published
site from). `mokata docs` never ships doc content in the wheel and never fetches at runtime — it
resolves a topic to its published-site URL and prints it. No topic lists the shipped topics with
their URLs, rendered through the A4 legibility box/table + colour gate (plain ASCII when piped /
NO_COLOR / ascii_only).
"""

import io
import os
import re
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import cli, product_docs
from mokata.harness_setup import plan_setup

ROOT = os.path.join(os.path.dirname(__file__), "..")
DOCS_DIR = os.path.join(ROOT, "docs")
CMDS_DIR = os.path.join(ROOT, "src", "mokata", "templates", "commands")

ESC = "\x1b["
BASE = "https://mokata.ai/"

# Immediate subdirs of docs/ that are INTERNAL planning or mkdocs presentation, never product
# topics. Everything else under docs/ is a public product section the pointer must cover.
_NON_PRODUCT_DIRS = {"build", "launch", "marketing", "assets", "stylesheets"}


def _product_sections():
    return sorted(
        d for d in os.listdir(DOCS_DIR)
        if os.path.isdir(os.path.join(DOCS_DIR, d)) and d not in _NON_PRODUCT_DIRS
    )


# ============================================================= the topic -> URL rule (mkdocs)
class TestTopicUrl(unittest.TestCase):
    def test_top_level_guide_maps_to_a_trailing_slash_page(self):
        self.assertEqual(product_docs.topic_url("getting-started"), BASE + "getting-started/")

    def test_nested_section_page_maps_under_its_section(self):
        self.assertEqual(product_docs.topic_url("concepts/execution-model"),
                         BASE + "concepts/execution-model/")

    def test_index_maps_to_the_site_root(self):
        self.assertEqual(product_docs.topic_url("index"), BASE)

    def test_a_trailing_md_or_slashes_are_tolerated(self):
        self.assertEqual(product_docs.topic_url("quickstart.md"), BASE + "quickstart/")
        self.assertEqual(product_docs.topic_url("/quickstart/"), BASE + "quickstart/")


# ============================================================= the shipped topic list
class TestTopicList(unittest.TestCase):
    def test_list_is_non_empty_and_well_formed(self):
        topics = product_docs.list_topics()
        self.assertTrue(topics)
        for t in topics:
            self.assertTrue(t.slug and t.title)
            self.assertTrue(product_docs.topic_url(t.slug).startswith(BASE))

    def test_covers_every_top_level_product_section_in_repo_docs(self):
        # Drift guard: if a new section dir is added under docs/ but not represented in the
        # topic map, this FAILS — the pointer can never silently miss a whole section.
        slugs = [t.slug for t in product_docs.list_topics()]
        for section in _product_sections():
            self.assertTrue(
                any(s.startswith(section + "/") for s in slugs),
                f"docs/{section}/ has no topic in the /mokata:docs map — add one",
            )

    def test_find_topic_resolves_known_and_rejects_unknown(self):
        self.assertIsNotNone(product_docs.find_topic("getting-started"))
        self.assertIsNone(product_docs.find_topic("no-such-topic"))


# ============================================================= degrade-clean rendering
class TestRendering(unittest.TestCase):
    def test_no_arg_list_routes_through_the_legibility_helpers(self):
        out = product_docs.render(ascii_only=False, color=False)
        self.assertIn("│", out)                            # legibility.table column separator
        self.assertIn("┌", out)                            # legibility.box border
        self.assertIn(BASE, out)                           # the live site is named

    def test_no_arg_list_names_every_topic_and_its_url(self):
        out = product_docs.render(ascii_only=True, color=False)
        for t in product_docs.list_topics():
            self.assertIn(t.slug, out)
            self.assertIn(product_docs.topic_url(t.slug), out)

    def test_ascii_mode_has_zero_escape_codes(self):
        self.assertNotIn(ESC, product_docs.render(ascii_only=True, color=False))

    def test_no_color_env_forces_plain_ascii(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            out = product_docs.render()                    # auto-detect -> colour off
        self.assertNotIn(ESC, out)

    def test_known_topic_prints_its_url_and_title(self):
        out = product_docs.render("getting-started", ascii_only=True, color=False)
        self.assertIn(BASE + "getting-started/", out)
        self.assertIn("Getting started", out)
        self.assertNotIn(ESC, out)

    def test_unknown_topic_renders_none(self):
        self.assertIsNone(product_docs.render("no-such-topic"))


# ============================================================= CLI wiring + piped output
class TestCliDocs(unittest.TestCase):
    def test_docs_subcommand_is_registered(self):
        parser = cli.build_parser()
        self.assertIn("docs", cli._subcommand_set(parser))

    def test_no_arg_lists_topics_and_urls_exit_zero(self):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = cli.main(["docs"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn(BASE, out)
        for t in product_docs.list_topics():
            self.assertIn(t.slug, out)

    def test_topic_prints_its_site_url_exit_zero(self):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = cli.main(["docs", "getting-started"])
        self.assertEqual(rc, 0)
        self.assertIn(BASE + "getting-started/", buf.getvalue())

    def test_unknown_topic_lists_with_a_hint_and_exits_one(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            rc = cli.main(["docs", "no-such-topic"])
        self.assertEqual(rc, 1)                            # unknown topic -> non-zero
        self.assertIn(BASE, out.getvalue())                # the list is still offered
        self.assertIn("no-such-topic", err.getvalue())     # the hint names the miss

    def test_piped_output_has_no_escape_codes(self):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            cli.main(["docs"])
        self.assertNotIn(ESC, buf.getvalue())

    def test_pointer_never_fetches_at_runtime(self):
        # Local-first: rendering the list or a topic must not open any URL / network handle.
        import urllib.request
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=AssertionError("docs must not fetch at runtime")):
            product_docs.render()
            product_docs.render("getting-started")


# ============================================================= the docs.md command template
class TestDocsTemplate(unittest.TestCase):
    def test_template_exists_with_valid_frontmatter(self):
        path = os.path.join(CMDS_DIR, "docs.md")
        self.assertTrue(os.path.isfile(path), "templates/commands/docs.md is missing")
        with open(path, encoding="utf-8") as fh:
            md = fh.read()
        from mokata.agent_skills import parse_frontmatter
        fm = parse_frontmatter(md)
        self.assertEqual(fm.get("name"), "docs")
        self.assertTrue(fm.get("description"), "docs.md has no frontmatter description")

    def test_template_is_read_only_no_gate(self):
        with open(os.path.join(CMDS_DIR, "docs.md"), encoding="utf-8") as fh:
            self.assertIsNone(re.search(r"(?m)^## Gate", fh.read()))

    def test_template_ships_through_the_setup_command_set(self):
        plan = plan_setup("claude")
        self.assertIn("docs.md", plan.command_files)


# ============================================================= NO product docs in the wheel
class TestNoDocsInWheel(unittest.TestCase):
    def test_docs_are_not_bundled_inside_the_package(self):
        self.assertFalse(os.path.isdir(os.path.join(ROOT, "src", "mokata", "docs")),
                         "product docs must NOT live inside the package (they'd ship in the wheel)")

    def test_pyproject_package_data_ships_no_docs(self):
        with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
            pyproject = fh.read()
        block = pyproject.split("[tool.setuptools.package-data]", 1)[-1]
        block = block.split("[", 1)[0]                     # just the package-data table
        self.assertNotIn("docs/", block,
                         "package-data must not glob product docs into the wheel")


if __name__ == "__main__":
    unittest.main()
