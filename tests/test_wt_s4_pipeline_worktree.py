"""WT.S4 — the pipeline-in-worktree flow (0.0.16), plus the REVIEW-FIX.R3 instrument fold-in.

0.0.13 shipped the PRIMITIVES and WT.S1 wired them to ONE trigger: a sibling COLLISION. A single
window running a pipeline therefore never hears about a worktree at all, the branch `create_worktree`
cuts is FORGOTTEN the moment it is cut (`SessionEntry` carried `repo_root`/`phase`/`scope` and no
`branch`), and ship hands the human a worktree they have to reason about instead of a branch they
can merge. This stage closes those three, and folds R3's now-registered MCP tools into the prose:

  (a) RUN-START OFFER — the offer fires at the start of a pipeline RUN, not only on collision. It is
      TEXT: it names the cost/benefit and the human-gated command, and creates NOTHING. Silence and
      a bare "start" leave the repo byte-identical — no worktree, no branch. Asserted against REAL
      git, not a mock: `git worktree list` and `git branch` are unchanged after the offer.
  (b) BINDING SURFACED — the binding is the EXISTING registry entry (run_id == session_id, per
      `session.py`), extended by ONE additive field (`branch`). It resolves BOTH directions and
      shows up on the run-progress surface that `mokata progress` and the `progress` MCP tool
      already share. No second binding store.
  (c) MERGE-READY HANDOFF — a worktree-bound run reaching ship is handed a BRANCH and the exact
      next action. An UNBOUND run gets no handoff prose and no error: ship is byte-identical.
  (d) R3 FOLD-IN — review.md and ship.md name the registered `review_record` / `review_status` MCP
      tools as THE instruments, from ONE source, so the two files cannot drift. The CLI stays as the
      named fallback.

Business-level asserts throughout: the text a human is handed, the state of the REAL repo after an
offer, the binding a surface resolves — never an internal call count.
"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import session as S
from mokata import session_registry as SR
from mokata import session_worktree as SW
from mokata.config import Surface
from mokata.skills import (REVIEW_RECORD_INSTRUMENT, REVIEW_STATUS_INSTRUMENT,
                           WORKTREE_MERGE_READY_HANDOFF, WORKTREE_RUN_START_OFFER,
                           command_markdown, get_skill)

_SRC = Path(__file__).resolve().parents[1] / "src" / "mokata"
_TEMPLATES = _SRC / "templates" / "commands"
_SKILLS_DIR = _SRC / "skills"


# --------------------------------------------------------------------------- real-git helpers
def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


def _git(d, *args):
    return subprocess.run(["git", "-C", d, *args], check=True,
                          capture_output=True, text=True).stdout


def _init_remoteless_repo(d):
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t.t")
    _git(d, "config", "user.name", "t")
    _git(d, "commit", "-q", "--allow-empty", "-m", "root")


def _mokata_git_repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    surface = Surface.load(d)
    _init_remoteless_repo(d)
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "mokata")
    return surface


def _repo_shape(d):
    """The two facts an OFFER must never change: the worktrees and the branches."""
    return (_git(d, "worktree", "list"), _git(d, "branch", "--list"))


def run_cli(argv):
    buf = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        with redirect_stdout(buf):
            from mokata.cli import main
            rc = main(argv)
    finally:
        sys.stdin = old
    return rc, buf.getvalue()


class _Base(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()
        SW.reset_offers()
        SW.reset_run_offers()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()
        SW.reset_offers()
        SW.reset_run_offers()


# ======================================================================================
# 1 · (a) RUN-START WORKTREE OFFER — human-gated, NEVER automatic
# ======================================================================================

@unittest.skipUnless(_git_available(), "git not available")
class TestRunStartOffer(_Base):

    def test_the_offer_states_cost_benefit_and_the_gated_command(self):
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            os.environ[S.SESSION_ID_ENV] = "run-alpha"
            S.reset_for_test()
            text = SW.run_start_offer_text(surface)
            self.assertIsNotNone(text, "a fresh unbound run in a git repo gets the offer")
            # benefit + cost are BOTH stated — an offer that only sells is not an offer.
            self.assertIn("isolate", text.lower())
            self.assertIn("branch", text.lower())
            # it names the ONE human-gated command that would act, and says it never acts itself.
            self.assertIn("mokata worktree create", text)
            self.assertIn("never automatic", text.lower())

    def test_the_offer_creates_nothing_in_the_real_repo(self):
        """NEVER-AUTO, asserted against real git: after the offer the repo is byte-identical."""
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            os.environ[S.SESSION_ID_ENV] = "run-alpha"
            S.reset_for_test()
            before = _repo_shape(d)
            self.assertIsNotNone(SW.run_start_offer_text(surface))
            buf = io.StringIO()
            SW.emit_run_start_offer_once(surface, out=buf.write)
            self.assertIn("worktree", buf.getvalue())
            self.assertEqual(_repo_shape(d), before,
                             "the offer is TEXT — it cut no branch and added no worktree")
            # …and no binding was invented either: nothing was bound, because nothing was created.
            self.assertIsNone(SW.binding_for_run(surface))

    def test_silence_and_a_bare_start_never_create_a_worktree(self):
        """A bare `mokata progress` (the run-start surface) offers and stops there."""
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            _mokata_git_repo(d)
            os.environ[S.SESSION_ID_ENV] = "run-alpha"
            S.reset_for_test()
            before = _repo_shape(d)
            rc, _out = run_cli(["progress", "--path", d])
            self.assertEqual(rc, 0)
            self.assertEqual(_repo_shape(d), before)

    def test_the_offer_fires_once_per_run_not_once_per_phase(self):
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            os.environ[S.SESSION_ID_ENV] = "run-alpha"
            S.reset_for_test()
            self.assertTrue(SW.emit_run_start_offer_once(surface, out=lambda _s: None))
            self.assertFalse(SW.emit_run_start_offer_once(surface, out=lambda _s: None),
                             "a phase-start re-print must not re-nag")

    def test_no_offer_once_the_run_is_already_bound(self):
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            os.environ[S.SESSION_ID_ENV] = "run-alpha"
            S.reset_for_test()
            res = SW.create_worktree(surface, topic="auth rework", assume_yes=True,
                                     out=lambda _s: None)
            self.assertTrue(res.created, res.reason)
            SW.reset_run_offers()
            self.assertIsNone(SW.run_start_offer_text(surface),
                              "an already-bound run has nothing to offer")

    def test_a_non_git_dir_gets_no_offer_and_never_crashes(self):
        with tempfile.TemporaryDirectory() as d:
            from mokata.init import init_repo
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            surface = Surface.load(d)
            self.assertIsNone(SW.run_start_offer_text(surface))
            self.assertFalse(SW.emit_run_start_offer_once(surface, out=lambda _s: None))

    def test_the_gated_create_still_refuses_without_confirmation(self):
        """The offer's ONLY action path stays fail-closed — a declined confirm creates nothing."""
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            os.environ[S.SESSION_ID_ENV] = "run-alpha"
            S.reset_for_test()
            before = _repo_shape(d)
            res = SW.create_worktree(surface, topic="auth rework",
                                     confirm=lambda _p: False, out=lambda _s: None)
            self.assertFalse(res.created)
            self.assertEqual(_repo_shape(d), before)
            self.assertIsNone(SW.binding_for_run(surface))


# ======================================================================================
# 2 · (b) RUN <-> WORKTREE BINDING, SURFACED AND RESOLVABLE BOTH DIRECTIONS
# ======================================================================================

@unittest.skipUnless(_git_available(), "git not available")
class TestBindingSurfaced(_Base):

    def _bound(self, d, run_id="run-alpha", topic="auth rework", with_run=False):
        surface = _mokata_git_repo(d)
        os.environ[S.SESSION_ID_ENV] = run_id
        S.reset_for_test()
        if with_run:
            # An ACTIVE pipeline run, so the run-progress block renders the tracker (an inactive
            # run renders only the friendly "no run" message and has nothing to hang a binding on).
            from mokata.govern.resume import PipelineCheckpoint
            PipelineCheckpoint(surface.state, run_id).mark_passed("brainstorm")
        res = SW.create_worktree(surface, topic=topic, assume_yes=True, out=lambda _s: None)
        self.assertTrue(res.created, res.reason)
        return surface, res

    def test_the_binding_is_recorded_on_the_existing_registry_entry(self):
        """No second store: the branch lands on the SessionEntry the registry already keeps."""
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface, res = self._bound(d)
            rows = SR.list_sessions(surface)
            mine = [r for r in rows if r.session_id == "run-alpha"]
            self.assertEqual(len(mine), 1)
            self.assertEqual(mine[0].branch, res.branch)
            self.assertEqual(mine[0].scope, "auth rework")

    def test_run_to_worktree_resolves(self):
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface, res = self._bound(d)
            b = SW.binding_for_run(surface)
            self.assertIsNotNone(b)
            self.assertEqual(b.run_id, "run-alpha")
            self.assertEqual(b.branch, res.branch)
            self.assertEqual(b.scope, "auth rework")

    def test_worktree_to_run_resolves(self):
        """The other direction: given the branch, which run owns it?"""
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface, res = self._bound(d)
            self.assertEqual(SW.run_for_branch(surface, res.branch), "run-alpha")
            self.assertIsNone(SW.run_for_branch(surface, "no-such-branch"))

    def test_the_binding_shows_on_the_run_progress_surface(self):
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface, res = self._bound(d, with_run=True)
            from mokata.progress import build_progress, render_progress
            block = render_progress(build_progress(surface.state), surface=surface)
            self.assertIn(res.branch, block)
            self.assertIn("worktree", block.lower())

    def test_resolving_a_binding_does_not_destroy_it(self):
        """Found on-device: `list_sessions` PRUNES dead-pid rows, so a resolution that pruned would
        answer ONCE and then silently stop — a binding that is present, then gone, with nothing to
        explain it. Resolving is a READ; it must be repeatable and must not reap the row it read."""
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface, res = self._bound(d)
            # a row whose recorded pid is DEAD — exactly what a prune would delete.
            SR.touch(surface)
            store = SR._registry_store(surface)
            blob = store.read(SR.SESSION_REGISTRY_KEY)
            blob["sessions"]["run-alpha"]["pid"] = 2 ** 30      # no such process
            store.write(SR.SESSION_REGISTRY_KEY, blob)
            for i in range(3):
                with self.subTest(read=i):
                    b = SW.binding_for_run(surface)
                    self.assertIsNotNone(b, "the binding survived being read")
                    self.assertEqual(b.branch, res.branch)
                    self.assertEqual(SW.run_for_branch(surface, res.branch), "run-alpha")

    def test_the_binding_shows_in_windows(self):
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            _surface, res = self._bound(d)
            rc, out = run_cli(["windows", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn(res.branch, out)

    def test_an_unbound_run_surfaces_no_binding_line(self):
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            os.environ[S.SESSION_ID_ENV] = "run-alpha"
            S.reset_for_test()
            from mokata.govern.resume import PipelineCheckpoint
            PipelineCheckpoint(surface.state, "run-alpha").mark_passed("brainstorm")
            SR.touch(surface)
            self.assertIsNone(SW.binding_for_run(surface))
            from mokata.progress import build_progress, render_progress
            block = render_progress(build_progress(surface.state), surface=surface)
            self.assertNotIn("bound to worktree", block)


class TestBindingDegradesClean(_Base):
    """No git, no registry, no run — the binding surfaces resolve to None, never to a raise."""

    def test_binding_for_run_on_a_bare_dir(self):
        with tempfile.TemporaryDirectory() as d:
            from mokata.init import init_repo
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            surface = Surface.load(d)
            self.assertIsNone(SW.binding_for_run(surface))
            self.assertIsNone(SW.run_for_branch(surface, "anything"))
            self.assertIsNone(SW.merge_ready_handoff(surface))

    def test_the_registry_entry_is_backward_compatible(self):
        """A pre-WT.S4 entry (no `branch` key) still reads — the field is additive."""
        with tempfile.TemporaryDirectory() as d:
            from mokata.init import init_repo
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            surface = Surface.load(d)
            os.environ[S.SESSION_ID_ENV] = "old-session"
            S.reset_for_test()
            SR.touch(surface, phase="develop", scope="legacy")
            rows = [r for r in SR.list_sessions(surface) if r.session_id == "old-session"]
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0].branch)


# ======================================================================================
# 3 · (c) MERGE-READY BRANCH HANDOFF AT SHIP
# ======================================================================================

@unittest.skipUnless(_git_available(), "git not available")
class TestMergeReadyHandoff(_Base):

    def test_a_bound_run_is_handed_a_branch_and_the_exact_next_action(self):
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            os.environ[S.SESSION_ID_ENV] = "run-alpha"
            S.reset_for_test()
            res = SW.create_worktree(surface, topic="auth rework", assume_yes=True,
                                     out=lambda _s: None)
            self.assertTrue(res.created, res.reason)
            text = SW.merge_ready_handoff(surface)
            self.assertIsNotNone(text)
            self.assertIn(res.branch, text)                 # names the branch
            self.assertIn("git merge", text)                # names the EXACT next action
            self.assertIn(res.path, text)                   # and where the work sits

    def test_an_unbound_run_gets_no_handoff_prose_and_no_error(self):
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            os.environ[S.SESSION_ID_ENV] = "run-alpha"
            S.reset_for_test()
            SR.touch(surface)
            self.assertIsNone(SW.merge_ready_handoff(surface),
                              "no binding ⇒ ship is byte-identical to today")

    def test_the_handoff_never_merges_anything(self):
        """mokata NEVER merges: the handoff is prose the HUMAN runs, and the branch is untouched."""
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            os.environ[S.SESSION_ID_ENV] = "run-alpha"
            S.reset_for_test()
            res = SW.create_worktree(surface, topic="auth rework", assume_yes=True,
                                     out=lambda _s: None)
            head_before = _git(d, "rev-parse", "HEAD").strip()
            self.assertIsNotNone(SW.merge_ready_handoff(surface))
            self.assertEqual(_git(d, "rev-parse", "HEAD").strip(), head_before)
            self.assertIn(res.branch, _git(d, "branch", "--list"))


# ======================================================================================
# 4 · PROSE — the run-start offer + the handoff, single-sourced, LAYERED on CRG-NAV
# ======================================================================================

class TestProse(_Base):

    def test_develop_carries_the_run_start_offer_and_ship_the_handoff(self):
        self.assertIn(WORKTREE_RUN_START_OFFER.strip(), get_skill("develop").prompt)
        self.assertIn(WORKTREE_MERGE_READY_HANDOFF.strip(), get_skill("ship").prompt)

    def test_the_offer_prose_states_the_never_auto_property(self):
        text = WORKTREE_RUN_START_OFFER.lower()
        self.assertIn("never", text)
        self.assertIn("explicit", text)
        for banned in ("automatically create", "auto-create"):
            self.assertNotIn(banned, text)

    def test_the_handoff_prose_keeps_ship_unchanged_for_an_unbound_run(self):
        text = WORKTREE_MERGE_READY_HANDOFF.lower()
        self.assertIn("no binding", text)
        self.assertIn("mokata never merges", text)

    def test_crg_nav_navigation_clause_is_undisturbed(self):
        """WT.S4 LAYERS ON — it must not have overwritten CRG-NAV's clause in either file."""
        from mokata.skills import NAVIGATION_GRAPH_FIRST
        for name in ("develop", "ship"):
            with self.subTest(skill=name):
                md = (_SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn(NAVIGATION_GRAPH_FIRST.strip(), md)

    def test_shipped_templates_match_their_single_source(self):
        """A stage that edits skills.py but ships a stale template hands the agent the OLD prose."""
        for name in ("develop", "ship", "review"):
            with self.subTest(skill=name):
                self.assertEqual((_TEMPLATES / f"{name}.md").read_text(encoding="utf-8"),
                                 command_markdown(get_skill(name)))

    def test_the_shipped_skill_md_carries_the_new_clauses(self):
        dev = (_SKILLS_DIR / "develop" / "SKILL.md").read_text(encoding="utf-8")
        ship = (_SKILLS_DIR / "ship" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(WORKTREE_RUN_START_OFFER.strip(), dev)
        self.assertIn(WORKTREE_MERGE_READY_HANDOFF.strip(), ship)


# ======================================================================================
# 5 · (d) R3 FOLD-IN — the MCP tools are THE instruments, single-sourced
# ======================================================================================

class TestR3InstrumentFoldIn(_Base):

    def test_the_instruments_are_the_registered_mcp_tools(self):
        import mokata.mcp.tools_read  # noqa: F401  (registration side effect)
        from mokata.mcp.registry import read_tool_names
        for name in ("review_record", "review_status"):
            with self.subTest(tool=name):
                self.assertIn(name, read_tool_names())
        self.assertIn("`review_status`", REVIEW_STATUS_INSTRUMENT)
        self.assertIn("`review_record`", REVIEW_RECORD_INSTRUMENT)

    def test_the_prose_leads_with_the_tool_and_keeps_the_cli_as_fallback(self):
        for phrase in (REVIEW_STATUS_INSTRUMENT, REVIEW_RECORD_INSTRUMENT):
            with self.subTest(phrase=phrase):
                tool_at = phrase.index("review_st" if "review_status" in phrase
                                       else "review_record")
                cli_at = phrase.index("mokata progress")
                self.assertLess(tool_at, cli_at, "the prose LEADS with the MCP tool")
        self.assertIn("mokata progress review-status", REVIEW_STATUS_INSTRUMENT)
        self.assertIn("mokata progress record-review", REVIEW_RECORD_INSTRUMENT)

    def test_review_and_ship_pull_the_instruments_from_the_one_source(self):
        # review's record instruction is attached at RENDER time (`record_verdict=True`), ship's
        # status read sits in its prompt — so assert on what each skill actually HANDS the agent.
        self.assertIn(REVIEW_RECORD_INSTRUMENT, command_markdown(get_skill("review")))
        self.assertIn(REVIEW_STATUS_INSTRUMENT, get_skill("ship").prompt)
        self.assertIn(REVIEW_STATUS_INSTRUMENT, command_markdown(get_skill("ship")))

    def test_neither_file_names_the_cli_as_THE_instrument_any_more(self):
        """The drift class R3 closes: a bare `run \\`mokata progress review-status\\`` with no tool."""
        for name in ("review", "ship"):
            with self.subTest(skill=name):
                md = (_SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertNotIn("run `mokata progress review-status`", md)
                self.assertNotIn("run `mokata progress record-review --passed`", md)

    def test_the_shipped_review_and_ship_md_carry_the_instruments(self):
        for name, phrase in (("review", REVIEW_RECORD_INSTRUMENT),
                             ("ship", REVIEW_STATUS_INSTRUMENT)):
            with self.subTest(skill=name):
                md = (_SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn(phrase, md)


# ======================================================================================
# 6 · NEGATIVES — no parallel subsystem, no new tool, prior pins untouched
# ======================================================================================

class TestNegatives(_Base):

    def test_wt_s4_added_no_mcp_tool_and_the_create_half_still_has_none(self):
        """WT.S4 surfaces through the EXISTING progress/windows reads — it registered no tool.

        UPDATED at WT-LIST (FR-WT-1, 0.0.16), with cause: this test originally also asserted
        `worktree_list` was absent, which was true of WT.S4 and is the very thing WT-LIST ships —
        doc 02's WT.S4 row records the slice as "NOT present yet … out of scope". The INTENT the
        assertion was protecting is intact and still pinned below: WT.S4 introduced no tool of its
        own, and the durable CREATE/BIND half is still not an MCP surface (`git worktree add` stays
        gated through the CLI's fail-closed path — doc 85/parity.py). `worktree_list` is a READ,
        and only a read."""
        import mokata.mcp.tools_read  # noqa: F401  (registration side effect)
        from mokata.mcp.registry import read_tool_names, tool_names, write_tool_names
        names = tool_names()
        self.assertNotIn("worktree_create", names)
        self.assertNotIn("worktree_bind", names)
        self.assertNotIn("worktree_clean", names)          # FR-WT-2/3 — still 0.0.17
        self.assertNotIn("worktree_remove", names)
        # the ONE worktree tool that now exists is WT-LIST's, and it is read-kind.
        self.assertIn("worktree_list", read_tool_names())
        self.assertNotIn("worktree_list", write_tool_names())
        self.assertEqual([n for n in names if n.startswith("worktree")], ["worktree_list"])

    def test_there_is_exactly_one_worktree_create_path(self):
        """`create_worktree` stays the single gated creator — no second `git worktree add -b`."""
        src = (_SRC / "session_worktree.py").read_text(encoding="utf-8")
        self.assertEqual(src.count('"worktree", "add"'), 1)

    def test_the_binding_reuses_the_session_registry_entry_fields(self):
        self.assertIn("branch", SR._ENTRY_FIELDS)
        self.assertIn("scope", SR._ENTRY_FIELDS)
        self.assertIn("repo_root", SR._ENTRY_FIELDS)

    def test_the_wt_s1_collision_offer_is_untouched(self):
        """WT.S1's sibling-collision offer keeps its own trigger and its own once-per-repo memory."""
        self.assertTrue(callable(SW.emit_offer_once))
        self.assertTrue(callable(SW.offer_text_once))
        self.assertTrue(callable(SW.build_offer))
        self.assertIsNone(SW.build_offer([]))


if __name__ == "__main__":
    unittest.main()
