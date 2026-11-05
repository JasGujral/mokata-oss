"""IDE e2e — review context (0.0.26 P7, ID-REVIEW-CONTEXT): a changed README renders as a rendered
diff with its Mermaid diagram drawn and its <script> inert; Blame shows the hunk's own history; the
Tests-by-AC panel groups the run's tests by AC, flags the untested AC and re-runs a test as a job;
the rendered spec diff names the added AC; the Code view previews a Markdown file.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import subprocess
import sys
import textwrap
import unittest

import _harness as H

RUN = "e" * 32

README_OLD = "# Shop\n\nA tiny shop.\n"
README_NEW = textwrap.dedent("""\
    # Shop

    A tiny shop, now with a diagram.

    <script>window.__pwned = 1</script>
    <img src=x onerror="window.__pwned = 2">

    [evil](javascript:window.__pwned=3)

    ```mermaid
    graph LR
      order --> slug
    ```
    """)

PLANT = textwrap.dedent("""\
    import sys
    sys.path[:0] = [sys.argv[2], sys.argv[3]]
    root, run = sys.argv[1], sys.argv[4]
    from _plan import commit_planned
    from mokata.engine.spec import AcceptanceCriterion, Spec, TestRef
    from mokata.govern.resume import PipelineCheckpoint
    from mokata.session_state import scoped_store
    from mokata.spec_scope import archive_key
    from mokata.state import StateStore
    from mokata.tdd_state import state_dir
    from mokata import tdd_state
    from mokata.test_runs import seed_ac_map
    st = scoped_store(StateStore(state_dir(root)), run)
    PipelineCheckpoint(st, run).ensure_registered()
    st.write("approved_approach", {"topic": "slugs", "approach": {"name": "Slug from title",
             "summary": "", "pros": [], "cons": [], "targets": [], "decisions": []},
             "answered_questions": [], "approver": "jas", "approved_at": "2026-10-20T09:00:00+00:00",
             "domains": []})
    crit = [AcceptanceCriterion("AC-1", "a title becomes a lower-case slug"),
            AcceptanceCriterion("AC-2", "an order's ref carries its slug"),
            AcceptanceCriterion("AC-3", "an empty title is refused")]
    commit_planned(st, Spec("slugs", crit[:1]), version=1, run_id=run)
    commit_planned(st, Spec("slugs", crit), version=2, archive_key=archive_key(run, 1), run_id=run)
    seed_ac_map(root, run, [TestRef(name="t_ok", ac_ids=["AC-1"]),
                            TestRef(name="t_fail", ac_ids=["AC-2"])])
    tdd_state.record(st, run, red=["t_ok", "t_fail"], green=["t_ok"])
    """)

RUNNER = ("import sys\nprint('running', sys.argv[1], flush=True)\n"
          "sys.exit(1 if 'fail' in sys.argv[1] else 0)\n")


def build(base):
    repo = H.make_repo("shop", base)
    H._write(repo, "README.md", README_OLD)
    H._write(repo, "runner.py", RUNNER)
    H._write(repo, "docs/arch.md", "# Architecture\n\n```mermaid\ngraph TD\n  a --> b\n```\n")
    H._git(repo, "add", "-A")
    H._git(repo, "commit", "-q", "-m", "readme")
    H._write(repo, "README.md", README_NEW)
    tests_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, "-c", PLANT, repo, tests_dir, H.SRC, RUN], check=True,
                   env=H._env(), stdin=subprocess.DEVNULL)
    import json
    man = os.path.join(repo, ".mokata", "manifest.json")
    with open(man, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {}).setdefault("baseline", {})["test_command"] = \
        '"%s" runner.py' % sys.executable
    with open(man, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return repo


class TheReviewContextWorks(H.IdeFlow):
    FIXTURE = staticmethod(build)

    def test_1_a_changed_readme_renders_as_a_diff_with_its_diagram_and_no_script_runs(self):
        p = self.page
        p.evaluate("MK.setMode('review')")
        p.wait_for_selector("#nav-list li", state="attached")
        p.locator("#nav-list li", has_text="README.md").first.click()
        p.wait_for_selector("#diff .rv-md-btn")
        p.click("#diff .rv-md-btn")
        p.wait_for_selector("#diff .mk-diff .mk-row")
        self.until("!!document.querySelector('#diff .mk-new .mk-mermaid img.mk-m-img')",
                   "the mermaid diagram to be drawn as an image")
        src = p.get_attribute("#diff .mk-new img.mk-m-img", "src")
        self.assertTrue(src.startswith("data:image/svg+xml"), src[:60])
        side = "[...document.querySelectorAll('#diff .mk-%s')].map(n => n.textContent).join('\\n')"
        self.assertIn("A tiny shop.", p.evaluate(side % "old"))
        new = p.evaluate(side % "new")
        self.assertIn("<script>window.__pwned = 1</script>", new, "raw HTML must show as text")
        self.assertIsNone(p.evaluate("window.__pwned"), "a script in the Markdown ran")
        self.assertEqual(0, p.locator("#diff script, #diff [onerror], #diff iframe").count())
        hrefs = p.evaluate("[...document.querySelectorAll('#diff a')].map(a => a.getAttribute('href'))")
        self.assertFalse([h for h in hrefs if h and h.lower().startswith("javascript")], hrefs)
        # the replaced paragraph is ONE changed row (old | new side by side), not a far-off pair
        self.assertIn("A tiny shop.", p.text_content("#diff .mk-row.mk-chg .mk-old"))
        self.assertIn("now with a diagram", p.text_content("#diff .mk-row.mk-chg .mk-new"))

    def test_2_blame_shows_the_hunks_lines_and_history(self):
        p = self.page
        p.click("#diff .rv-md-btn")                       # back to the line diff
        # UX-SIMPLIFY #36: Blame is an item of the hunk's ⋯ menu, not a pill
        p.click("#diff section.hunk .hk-btn")
        p.click("#hk-menu [data-item='blame']")
        p.wait_for_selector("#diff .rv-blame .rv-history")
        text = p.text_content("#diff .rv-blame")
        self.assertIn("readme", text, "the commit that wrote the hunk's old lines")
        self.assertIn("uncommitted", text)
        self.assertIn("line history of this hunk", text)
        p.click("#diff section.hunk .hk-btn")
        self.assertEqual("Hide blame", p.text_content("#hk-menu [data-item='blame']"))
        p.click("#hk-menu [data-item='blame']")
        p.wait_for_selector("#diff .rv-blame", state="detached")

    def test_3_tests_by_ac_groups_flags_and_re_runs_under_proof(self):
        """UX-SIMPLIFY #35: Tests by AC is part of Review › Proof (the same change set)."""
        p = self.page
        self.assertEqual(0, p.locator("#rv-tab-tests, #rv-tab-changes").count(), "the old toggle is still drawn")
        p.evaluate("MK.setMode('proof')")
        p.wait_for_selector("#rv-acs .rv-ac")
        self.assertEqual(["AC-1", "AC-2", "AC-3"],
                         p.evaluate("[...document.querySelectorAll('#rv-acs .rv-ac')].map(l => l.dataset.ac)"))
        self.assertIn("untested", p.text_content("#rv-acs .rv-ac[data-ac='AC-3']"))
        self.assertIn("jas", p.text_content("#rv-tests .rv-approval"))
        self.assertTrue(p.is_visible("#rv-acs .rv-ac[data-ac='AC-2'] .rv-test"), "a red AC opens")
        p.click("#rv-acs .rv-ac[data-ac='AC-2'] .rv-rerun-one")
        self.text_until("#rv-job", "1 failed", "the re-run job to finish")
        self.assertIn("running t_fail", p.text_content("#rv-job .rv-out"))
        p.wait_for_selector("#rv-acs .rv-ac[data-ac='AC-2'] .rv-h-red")

    def test_4_the_rendered_spec_diff_names_the_added_acs(self):
        p = self.page
        p.click("#rv-spec-diff")
        p.wait_for_selector("#rv-spec-render .mk-row")
        changes = p.text_content("#rv-ac-changes")
        self.assertIn("+ AC-2", changes)
        self.assertIn("+ AC-3", changes)
        self.assertIn("AC-3", p.evaluate(
            "[...document.querySelectorAll('#rv-spec-render .mk-new')].map(n => n.textContent).join(' ')"))
        self.press("Escape")
        p.evaluate("MK.setMode('review')")

    def test_5_the_code_view_previews_a_markdown_file(self):
        p = self.page
        self.open_file("docs/arch.md")
        p.wait_for_selector("#rv-preview:not([hidden])")
        p.click("#rv-preview")
        p.wait_for_selector("#rv-md-preview h1")
        self.assertEqual("Architecture", p.text_content("#rv-md-preview h1"))
        self.until("!!document.querySelector('#rv-md-preview img.mk-m-img')", "the preview's diagram")

    def test_6_a_hostile_diagram_cannot_make_the_frame_call_the_ide(self):
        """A diagram's own config (an `%%{init}%%` directive or front matter) cannot put a
        `url(/api/...)` into its CSS, a font, or HTML labels: nothing is requested from the IDE
        (the frame's CSP loads nothing; Mermaid drops the keys) — P7 verification F1."""
        p = self.page
        hostile = [
            '%%{init:{"themeCSS":"* { background: url(/api/health?probe=1) }"}}%%\ngraph LR\n a-->b',
            '%%{init:{"fontFamily":"x; src: url(/api/health?probe=2)"}}%%\ngraph LR\n a-->b',
            '%%{init:{"themeVariables":{"fontFamily":"x\\";background:url(/api/health?probe=3)"}}}%%'
            '\ngraph LR\n a-->b',
            '%%{init:{"flowchart":{"htmlLabels":true}}}%%\ngraph LR\n a["<img src=/api/health?probe=4>"]-->b',
            '---\nconfig:\n  themeCSS: "* { background: url(/api/health?probe=5) }"\n---\ngraph LR\n a-->b',
            '%%{init:{"securityLevel":"loose","secure":[]}}%%\ngraph LR\n a-->b\n click a "javascript:alert(1)"',
        ]
        before = len(self.requests)
        svgs = p.evaluate("""async (srcs) => {
            const out = [];
            for (const s of srcs) { try { out.push(await MK.render.diagram(s)); } catch (e) { out.push("ERR " + e.message); } }
            return out; }""", hostile)
        p.wait_for_timeout(800)
        asked = [u for _m, u in self.requests[before:] if "probe=" in u]
        self.assertEqual([], asked, "a diagram made the browser call the IDE")
        for src, svg in zip(hostile, svgs):
            self.assertNotIn("probe=", svg, src)
            self.assertNotIn("<foreignObject", svg, src)
            self.assertNotIn("javascript:", svg, src)


if __name__ == "__main__":
    unittest.main()
