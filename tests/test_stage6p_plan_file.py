"""Stage 6p — the brainstorm PLAN FILE: the approved design is saved as a durable, reviewable
file at approach approval, BEFORE the spec.

Covers:
  * at approval, `persist_approach(..., plans_dir=…)` ALSO writes `.mokata/plans/<slug>.md`, whose
    content is the session's `design_writeup()` (single source — not a second rendering);
  * the HARD-GATE holds: an UNAPPROVED session writes NO plan file (and persist/save still refuse);
  * degrade-clean: a plan-file write failure never breaks the approval handoff;
  * the `plan` group's file ops — list / read / export — copy the internal plan into a
    PROJECT-VISIBLE `plans/<slug>.md`, and export never silently clobbers an existing file;
  * `mokata plan list|show|export` CLI dispatch; the 54e parity declares `plan`;
  * the brainstorm template OFFERS the export and its shipped SKILL.md stays in sync (no drift).
"""

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata.brainstorm import (
    Approach,
    BrainstormGateError,
    BrainstormSession,
    persist_approach,
    save_approach_plan,
)
from mokata.state import StateStore
from mokata import plans as P

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _approved_session(topic="Add a Slugify Helper!"):
    s = BrainstormSession(topic)
    s.ask("ASCII-only or unicode?")
    s.answer("ASCII-only, hyphenated")
    s.propose_approaches([
        Approach("regex", "strip with a regex", pros=["tiny"], cons=["unicode edges"]),
        Approach("library", "use a slug lib", pros=["robust"], cons=["a dependency"]),
    ])
    s.approve("jas", "regex")
    return s


class TestApprovalWritesPlan(unittest.TestCase):
    def test_persist_writes_internal_plan_file(self):
        with tempfile.TemporaryDirectory() as d:
            plans_dir = os.path.join(d, ".mokata", "plans")
            s = _approved_session()
            persist_approach(s, StateStore(os.path.join(d, "state")), plans_dir=plans_dir)
            slug = P.plan_slug(s.topic)
            path = os.path.join(plans_dir, f"{slug}.md")
            self.assertTrue(os.path.isfile(path), "the internal plan file must be written")
            body = open(path, encoding="utf-8").read()
            # content IS the design write-up (topic, the approved approach, the decision).
            self.assertEqual(body, s.design_writeup())
            self.assertIn(s.topic, body)
            self.assertIn("Approved: **regex**", body)

    def test_persist_still_persists_state(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            s = _approved_session()
            state_path = persist_approach(s, store,
                                          plans_dir=os.path.join(d, ".mokata", "plans"))
            self.assertTrue(os.path.isfile(state_path), "the run-state hand-off still writes")

    def test_slug_is_a_safe_filename(self):
        self.assertEqual(P.plan_slug("Add a Slugify Helper!"), "add-a-slugify-helper")
        self.assertEqual(P.plan_slug("  UPPER/slash\\path  "), "upper-slash-path")
        self.assertEqual(P.plan_slug("!!!"), "plan")  # never empty


class TestHardGate(unittest.TestCase):
    def test_unapproved_session_writes_no_plan_via_persist(self):
        with tempfile.TemporaryDirectory() as d:
            plans_dir = os.path.join(d, ".mokata", "plans")
            s = BrainstormSession("unapproved topic")
            s.propose_approaches([
                Approach("a", "x", pros=["p"], cons=["c"]),
                Approach("b", "y", pros=["p"], cons=["c"]),
            ])
            # no approve() → the HARD-GATE must refuse, and NOTHING is written.
            with self.assertRaises(BrainstormGateError):
                persist_approach(s, StateStore(os.path.join(d, "state")), plans_dir=plans_dir)
            self.assertFalse(os.path.isdir(plans_dir), "no plan dir may be created")

    def test_save_approach_plan_refuses_unapproved(self):
        with tempfile.TemporaryDirectory() as d:
            plans_dir = os.path.join(d, ".mokata", "plans")
            s = BrainstormSession("still exploring")
            with self.assertRaises(BrainstormGateError):
                save_approach_plan(s, plans_dir)
            self.assertFalse(os.path.exists(plans_dir))


class TestDegradeClean(unittest.TestCase):
    def test_write_failure_does_not_break_approval(self):
        with tempfile.TemporaryDirectory() as d:
            # A regular FILE sits where the plans dir should be → makedirs/open fail. Approval
            # must still succeed (return the state path); no exception may escape.
            clash = os.path.join(d, "plans")
            with open(clash, "w", encoding="utf-8") as fh:
                fh.write("not a directory")
            s = _approved_session()
            store = StateStore(os.path.join(d, "state"))
            state_path = persist_approach(s, store, plans_dir=os.path.join(clash, "sub"))
            self.assertTrue(os.path.isfile(state_path), "approval survives a plan-write failure")

    def test_save_approach_plan_returns_none_on_failure(self):
        with tempfile.TemporaryDirectory() as d:
            clash = os.path.join(d, "f")
            open(clash, "w", encoding="utf-8").close()
            s = _approved_session()
            # plans_dir under a file → cannot create; degrade to None (not an exception).
            self.assertIsNone(save_approach_plan(s, os.path.join(clash, "plans")))


class TestPlanFileOps(unittest.TestCase):
    def _seed(self, plans_dir, *slugs):
        os.makedirs(plans_dir, exist_ok=True)
        for slug in slugs:
            with open(os.path.join(plans_dir, f"{slug}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# plan {slug}\n")

    def test_list_plans_sorted(self):
        with tempfile.TemporaryDirectory() as d:
            plans_dir = os.path.join(d, "plans")
            self.assertEqual(P.list_plans(plans_dir), [])  # missing dir → empty
            self._seed(plans_dir, "beta", "alpha")
            self.assertEqual(P.list_plans(plans_dir), ["alpha", "beta"])

    def test_read_plan(self):
        with tempfile.TemporaryDirectory() as d:
            plans_dir = os.path.join(d, "plans")
            self._seed(plans_dir, "alpha")
            self.assertEqual(P.read_plan(plans_dir, "alpha"), "# plan alpha\n")
            self.assertIsNone(P.read_plan(plans_dir, "missing"))

    def test_export_creates_project_copy(self):
        with tempfile.TemporaryDirectory() as d:
            plans_dir = os.path.join(d, ".mokata", "plans")
            dest = os.path.join(d, "plans")
            self._seed(plans_dir, "alpha")
            res = P.export_plan(plans_dir, dest, "alpha")
            self.assertTrue(res.written)
            self.assertFalse(res.existed)
            self.assertTrue(os.path.isfile(os.path.join(dest, "alpha.md")))

    def test_export_reports_on_overwrite_and_does_not_clobber(self):
        with tempfile.TemporaryDirectory() as d:
            plans_dir = os.path.join(d, ".mokata", "plans")
            dest = os.path.join(d, "plans")
            self._seed(plans_dir, "alpha")
            P.export_plan(plans_dir, dest, "alpha")
            # the user edited their committable copy
            target = os.path.join(dest, "alpha.md")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("MY EDITS")
            # a second export must NOT silently clobber — it reports.
            res = P.export_plan(plans_dir, dest, "alpha")
            self.assertTrue(res.existed)
            self.assertFalse(res.written)
            self.assertIn("force", res.reason.lower())
            self.assertEqual(open(target, encoding="utf-8").read(), "MY EDITS", "must not overwrite without force")
            # with force, it overwrites and says so.
            res2 = P.export_plan(plans_dir, dest, "alpha", force=True)
            self.assertTrue(res2.written)
            self.assertTrue(res2.existed)
            self.assertEqual(open(target, encoding="utf-8").read(), "# plan alpha\n")

    def test_export_missing_plan_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(P.PlanError):
                P.export_plan(os.path.join(d, ".mokata", "plans"),
                              os.path.join(d, "plans"), "ghost")


class TestCliDispatch(unittest.TestCase):
    def _init(self, d):
        from mokata.init import init_repo
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)

    def _run(self, argv):
        from mokata.cli import main
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_plan_list_show_export_flow(self):
        with tempfile.TemporaryDirectory() as d:
            self._init(d)
            from mokata.config import Surface
            surface = Surface.load(d)
            s = _approved_session()
            persist_approach(s, surface.state, plans_dir=surface.plans_dir)
            slug = P.plan_slug(s.topic)

            rc, out = self._run(["plan", "list", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn(slug, out)

            rc, out = self._run(["plan", "show", slug, "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("Approved: **regex**", out)

            # Export into an EXPLICIT temp dir (never the repo / cwd) so this test can never
            # deposit a plans/<slug>.md into the source tree, regardless of surface resolution.
            export_dir = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, export_dir, ignore_errors=True)
            exported = os.path.join(export_dir, f"{slug}.md")

            rc, out = self._run(["plan", "export", slug, "--path", d, "--to", export_dir])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(exported))
            self.assertIn(f"{slug}.md", out)
            self.assertIn("editable copy", out)

            # second export reports the overwrite (non-zero, not clobbered).
            with open(exported, "w", encoding="utf-8") as fh:
                fh.write("EDITED")
            rc, out = self._run(["plan", "export", slug, "--path", d, "--to", export_dir])
            self.assertNotEqual(rc, 0)
            self.assertIn("force", out.lower())
            self.assertEqual(open(exported, encoding="utf-8").read(), "EDITED")

    def test_plan_list_friendly_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self._init(d)
            rc, out = self._run(["plan", "list", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("no", out.lower())


class TestParityAndSurface(unittest.TestCase):
    def test_plan_is_declared_and_covered(self):
        from mokata import parity
        self.assertIn("plan", parity.SURFACE_MATRIX)
        self.assertTrue(parity.SURFACE_MATRIX["plan"].covered)
        self.assertTrue(parity.verify_parity().ok, parity.verify_parity().render())

    def test_plan_read_tools_registered(self):
        from mokata import mcp_server as M
        reads = set(M.read_tool_names())
        self.assertIn("plan_list", reads)
        self.assertIn("plan_show", reads)


class TestTemplateOffersExport(unittest.TestCase):
    def _brainstorm_md(self):
        path = os.path.join(_REPO, "src", "mokata", "templates", "commands", "brainstorm.md")
        return open(path, encoding="utf-8").read()

    def test_template_mentions_plan_file_and_export(self):
        md = self._brainstorm_md()
        self.assertIn(".mokata/temp_local/plans/", md)
        self.assertIn("mokata plan export", md)

    def test_shipped_skill_matches_generated(self):
        from pathlib import Path
        from mokata.agent_skills import skill_markdown
        templates = os.path.join(_REPO, "src", "mokata", "templates", "commands")
        shipped = open(os.path.join(_REPO, "src", "mokata", "skills", "brainstorm",
                                    "SKILL.md"), encoding="utf-8").read()
        self.assertEqual(shipped, skill_markdown("brainstorm", Path(templates)),
                         "brainstorm SKILL.md drifted — regenerate it from the template")


if __name__ == "__main__":
    unittest.main()
