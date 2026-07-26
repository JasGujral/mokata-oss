"""D-CMDNS — command-namespace drift: docs use the slash-command FORM their install route renders.

The rendered name depends on the install route — pip-first project commands (`mokata setup claude`
→ `.claude/commands/<name>.md`) and user-scope commands render the BARE `/<name>`; a marketplace
plugin namespaces every command as `/mokata:<name>`. A doc that shows the wrong form tells the
reader to type a command their `/` menu does not have. docsync's new `command-form` check catches
this structurally: membership from the curated command set, form from the page's declared route.

Pure-source (imports via `_support`, no installed package) so it runs on any interpreter — the same
discipline as the existing docsync suites (test_dk_s5_docsync, test_gr_s2_docsync).
"""

import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import docsync
from mokata.docsync import (
    BLOCKING, CodeFacts, audit_text, resolve_command_route,
    ROUTE_PIP, ROUTE_PLUGIN, ROUTE_BOTH,
)

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _facts(**over):
    """Fixed CodeFacts so form tests don't ride the live registry drifting. The slash set is the
    curated command surface the form check validates membership + form against."""
    base = dict(
        skill_count=16, total_skill_count=26,
        command_names=frozenset({"brainstorm", "spec", "review", "docsync"}),
        slash_commands=frozenset({"brainstorm", "spec", "test", "develop", "review",
                                  "ship", "init", "docsync"}),
        package="mokata", install_command="pip install mokata",
        setup_command="mokata setup claude", version="0.0.15",
        known_symbols=frozenset(), config_keys=frozenset())
    base.update(over)
    return CodeFacts(**base)


def _form(text, *, path="docs/quickstart.md"):
    return [f for f in audit_text(text, path=path, facts=_facts())
            if f.checker == "command-form"]


# --------------------------------------------------------------- the four required verdicts
class TestCommandForm(unittest.TestCase):
    def test_wrong_form_flagged(self):
        """A pip-first page that writes the plugin-namespaced form is Blocking + fixable."""
        text = "## Run it\n\nStart with `/mokata:brainstorm` to explore.\n"
        hits = _form(text)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].severity, BLOCKING)
        self.assertEqual(hits[0].section, "Run it")
        self.assertTrue(hits[0].fixable)
        self.assertIn("/brainstorm", hits[0].suggestion)
        self.assertNotIn("/mokata:brainstorm", hits[0].suggestion)
        self.assertIn("/brainstorm", hits[0].message)  # names the expected form

    def test_unknown_command_flagged(self):
        """An unknown command is still caught — by the membership (command-name) check — regardless
        of route, and the form check does NOT double-flag it."""
        text = "Run `/mokata:notacommand` now.\n"
        findings = audit_text(text, path="docs/quickstart.md", facts=_facts())
        names = {f.checker for f in findings}
        self.assertIn("command-name", names)
        self.assertEqual([f for f in findings if f.checker == "command-form"], [])

    def test_correct_pip_page_is_quiet(self):
        """The bare form on a pip-first page is exactly right — no finding (negative)."""
        text = ("## Run it\n\nStart with `/brainstorm`, then `/spec`, then `/develop`.\n"
                "Typing `/mokata` lists them all.\n")
        self.assertEqual(_form(text), [])

    def test_dual_route_page_with_declared_mapping_passes(self):
        """A page whose declared route is `both` states the mapping once and accepts either form —
        the namespaced form is NOT flagged there."""
        text = ("| CLI | Slash |\n|---|---|\n"
                "| `brainstorm` | `/mokata:brainstorm` |\n"
                "| `spec` | `/mokata:spec` |\n")
        self.assertEqual(
            _form(text, path="docs/reference/command-surfaces.md"), [])


# --------------------------------------------------------------- the named regression
class TestDCmdnsRegression(unittest.TestCase):
    def test_d_cmdns_regression(self):
        """A fixture page on the pip-first (default) route using `/mokata:brainstorm` MUST be
        flagged. This fails on pre-stage docsync (which validated membership but not form) and
        passes now — the guard that makes command-namespace drift structurally unshippable."""
        fixture = (
            "# Quickstart\n\n"
            "Install with `pip install mokata`, then `mokata setup claude`.\n"
            "Now run `/mokata:brainstorm` to start the pipeline.\n"
        )
        hits = _form(fixture, path="docs/how-to/first-run.md")
        self.assertTrue(hits, "pip-first page using /mokata:brainstorm must be flagged")
        self.assertEqual(hits[0].checker, "command-form")
        self.assertEqual(hits[0].severity, BLOCKING)


# --------------------------------------------------------------- route resolution
class TestRouteResolution(unittest.TestCase):
    def test_default_is_pip_bare(self):
        self.assertEqual(resolve_command_route("# doc\n", "docs/quickstart.md"), ROUTE_PIP)

    def test_path_map_marks_surface_pages_both(self):
        for p in ("docs/reference/command-surfaces.md", "docs/reference/cli.md",
                  "docs/how-to/use-the-plugin.md", "docs/how-to/install-plugin.md",
                  "docs/how-to/use-mokata-in-cowork.md"):
            self.assertEqual(resolve_command_route("# doc\n", p), ROUTE_BOTH, p)

    def test_path_map_matches_absolute_path_suffix(self):
        abs_path = os.path.join(_REPO, "docs", "reference", "cli.md")
        self.assertEqual(resolve_command_route("# doc\n", abs_path), ROUTE_BOTH)

    def test_frontmatter_override_wins(self):
        text = "---\nmokata_command_route: plugin\n---\n\n# doc\n"
        self.assertEqual(resolve_command_route(text, "docs/quickstart.md"), ROUTE_PLUGIN)

    def test_frontmatter_both_silences_a_pip_path(self):
        text = "---\nmokata_command_route: both\n---\n\nRun `/mokata:brainstorm`.\n"
        self.assertEqual(_form(text, path="docs/quickstart.md"), [])

    def test_unknown_frontmatter_value_falls_through(self):
        text = "---\nmokata_command_route: banana\n---\n\n# doc\n"
        self.assertEqual(resolve_command_route(text, "docs/quickstart.md"), ROUTE_PIP)


# --------------------------------------------------------------- plugin route (inverse) + fixable
class TestPluginRouteAndFix(unittest.TestCase):
    def test_plugin_page_flags_bare_form(self):
        """On a page declared as the plugin route, the BARE form is the wrong one."""
        text = "---\nmokata_command_route: plugin\n---\n\nRun `/brainstorm` to start.\n"
        hits = _form(text, path="docs/quickstart.md")
        self.assertEqual(len(hits), 1)
        self.assertIn("/mokata:brainstorm", hits[0].suggestion)

    def test_multiple_refs_on_one_line_all_fixed_by_one_suggestion(self):
        text = "Pipeline: `/mokata:brainstorm` → `/mokata:spec` → `/mokata:develop`.\n"
        hits = _form(text)
        self.assertTrue(hits)
        sugg = hits[0].suggestion
        self.assertIn("/brainstorm", sugg)
        self.assertIn("/spec", sugg)
        self.assertIn("/develop", sugg)
        self.assertNotIn("mokata:", sugg)


# --------------------------------------------------------------- false-positive discipline
class TestFalsePositiveDiscipline(unittest.TestCase):
    def test_slash_in_prose_not_in_code_is_ignored(self):
        """The check reads only code fragments — a `/mokata:brainstorm` written in plain prose (no
        backticks, no fence) is not an invocation surface and is not flagged."""
        text = "The command is /mokata:brainstorm in the older docs.\n"
        self.assertEqual(_form(text), [])

    def test_url_path_segment_is_not_a_bare_command(self):
        """A path like `/docs/spec` on a plugin page is not a bare command reference."""
        text = ("---\nmokata_command_route: plugin\n---\n\n"
                "See the guide at `/docs/spec/overview`.\n")
        self.assertEqual(_form(text, path="docs/quickstart.md"), [])

    def test_bare_non_command_word_is_ignored_on_pip_page(self):
        text = "Open `/etc/hosts` and check `/tmp`.\n"
        self.assertEqual(_form(text), [])

    def test_disarmed_command_set_checks_nothing(self):
        """An empty slash set (facts could not be gathered) disarms the form check — like its
        sibling checkers, it must not fabricate findings from a fact it never had."""
        text = "Run `/mokata:brainstorm`.\n"
        blank = _facts(slash_commands=frozenset())
        self.assertEqual(
            [f for f in audit_text(text, path="docs/quickstart.md", facts=blank)
             if f.checker == "command-form"], [])


# --------------------------------------------------------------- internal docs are out of scope
class TestInternalDocsOutOfScope(unittest.TestCase):
    def test_sweep_never_reaches_internal_trees(self):
        """The sweep excludes docs/build|launch|marketing, so an internal doc using any form
        produces no findings from the public sweep (out-of-scope honored)."""
        found = docsync.find_docs(_REPO)
        self.assertTrue(found, "sweep should find the public docs")
        for d in found:
            posix = d.replace("\\", "/")
            self.assertFalse(any(seg in posix for seg in
                                 ("docs/build/", "docs/launch/", "docs/marketing/")), d)


if __name__ == "__main__":
    unittest.main()
