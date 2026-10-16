"""0.0.23 D15 — PM.S1: prospective memory, `kind=intention` ("remind me when X").

Business claims:
  * a new memory kind INTENTION exists, deliberately pinned into the taxonomy (and NOT always-on,
    JIT, a part, or a drafter-assignable kind);
  * it carries a due condition — a date, a keyword, or a path — validated at write, strict at read;
  * writes are human-gated exactly like any memory write, and the due condition is part of what the
    human approves (the content hash);
  * due-ness is checked at SessionStart ("N intention(s) due", inside the briefing budget) and in the
    healing sweep (an `intention_due` proposal; approve = DONE = archived, never deleted; defer =
    comes back);
  * no schema change: existing stores open unchanged; an intention whose envelope cannot be read
    is reported UNREADABLE, loudly, and never rewritten or lost.
"""

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import approval, hook_cli
from mokata.config import Surface
from mokata.init import init_repo
from mokata.mcp import tools_memory as TM
from mokata.memory import MemoryItem, MemoryStore, normalize_kind
from mokata.memory import intentions as I
from mokata.memory.consolidation import _DRAFTABLE_KINDS
from mokata.memory.healing import INTENTION_DUE, render_proposal
from mokata.memory.item import (ALWAYS_ON_KINDS, ARCHIVED, ACTIVE, DECISION, EPISODIC, INTENTION,
                                JIT_KINDS, MEMORY_KINDS, PART_KINDS, PERSISTENT)


def _repo(test):
    d = tempfile.mkdtemp(prefix="mk-pm-")
    test.addCleanup(shutil.rmtree, d, True)
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_: None)
    return d


def _store(d):
    return MemoryStore.from_surface(Surface.load(d))


def _seed(d, subject, value, **due):
    item = MemoryItem.create(subject, value, mtype=PERSISTENT, kind=INTENTION,
                             applicability=I.make_envelope(**due))
    res = _store(d).remember(item, assume_yes=True)
    assert res.committed, res
    return item


class TheKindIsDeliberate(unittest.TestCase):
    def test_pinned_taxonomy(self):
        # PINNED ON PURPOSE (D15): adding a kind is a product decision, so the tuple is asserted
        # whole. A future kind must update this line in the same commit.
        self.assertEqual(MEMORY_KINDS, ("rule", "guardrail", "best-practice", "context",
                                        "reference", "decision", "episodic", "intention"))

    def test_not_always_on_jit_part_or_draftable(self):
        for group in (ALWAYS_ON_KINDS, JIT_KINDS, PART_KINDS, _DRAFTABLE_KINDS):
            self.assertNotIn(INTENTION, group)

    def test_aliases(self):
        for alias in ("intention", "Intentions", "reminder", " REMINDERS "):
            self.assertEqual(normalize_kind(alias), INTENTION)


class TheEnvelope(unittest.TestCase):
    def test_make_validates(self):
        with self.assertRaises(I.IntentionError):
            I.make_envelope()
        with self.assertRaises(I.IntentionError):
            I.make_envelope(due_at="2026-02-30")
        with self.assertRaises(I.IntentionError):
            I.make_envelope(due_at="next tuesday")
        with self.assertRaises(I.IntentionError):
            I.make_envelope(keywords=",".join(f"k{i}" for i in range(I.MAX_TERMS + 1)))
        env = I.make_envelope(due_at="2026-11-01", keywords="auth, ,token", paths="src\\auth")
        self.assertEqual(env["due"]["due_at"], "2026-11-01T00:00:00+00:00")
        self.assertEqual(env["due"]["keywords"], ["auth", "token"])
        self.assertEqual(env["due"]["paths"], ["src/auth"])          # Windows separators folded

    def test_read_is_strict(self):
        def item(app):
            return MemoryItem.create("s", "v", kind=INTENTION, applicability=app)
        self.assertIsNotNone(I.envelope_of(item(I.make_envelope(keywords="x"))))
        for bad in ({}, {"due": "2026-01-01"}, {"due": {"due_at": "garbage"}},
                    {"due": {"keywords": "auth"}}, {"due": {"keywords": [1]}},
                    {"due": {"keywords": ["x"], "when": "later"}}, {"due": {}}):
            with self.subTest(bad=bad):
                self.assertIsNone(I.envelope_of(item(bad)))


class DueIsChecked(unittest.TestCase):
    def _it(self, **due):
        return MemoryItem.create("s", "v", kind=INTENTION, applicability=I.make_envelope(**due))

    def test_dates(self):
        past, future = self._it(due_at="2020-01-01"), self._it(due_at="2999-01-01")
        due, bad = I.due_intentions([past, future], now="2026-10-15T00:00:00+00:00")
        self.assertEqual([d.item for d in due], [past])
        self.assertEqual(bad, [])
        # instants compare as instants, not strings: 09:00+02:00 == 07:00Z is due at 08:00Z
        tz = self._it(due_at="2026-10-15T09:00:00+02:00")
        self.assertEqual(len(I.due_intentions([tz], now="2026-10-15T08:00:00+00:00")[0]), 1)

    def test_paths(self):
        it = self._it(paths="src/auth")
        sig = I.Signals(paths=["src/auth/token.py"])
        self.assertEqual(len(I.due_intentions([it], signals=sig)[0]), 1)
        self.assertEqual(I.due_intentions([it], signals=I.Signals(paths=["src/authz/x.py"]))[0],
                         [])
        glob = self._it(paths="*.sql")
        self.assertEqual(len(I.due_intentions([glob], signals=I.Signals(paths=["m.sql"]))[0]), 1)

    def test_keywords_whole_word(self):
        it = self._it(keywords="auth")
        self.assertEqual(len(I.due_intentions([it], signals=I.Signals(text="feature/auth-v2"))[0]),
                         1)
        self.assertEqual(I.due_intentions([it], signals=I.Signals(text="author-page"))[0], [])

    def test_no_signals_fire_only_dates_and_others_are_ignored(self):
        kw = self._it(keywords="auth")
        fact = MemoryItem.create("s", "v", kind="context")
        self.assertEqual(I.due_intentions([kw, fact])[0], [])


class HumanGated(unittest.TestCase):
    def test_propose_mint_commit_and_the_due_is_in_the_hash(self):
        d = _repo(self)
        args = dict(path=d, subject="auth-review", value="re-check token TTL", kind="intention",
                    due_at="2020-01-01")
        r = TM.remember(**args)
        self.assertEqual(r["status"], "proposed")
        self.assertEqual(_store(d).peek_active(), [])                      # nothing written
        self.assertFalse(TM.remember(**args, approve=True).get("committed"))
        self.assertEqual(TM.remember(**args, proposal_id="p-forged0000")["status"], "refused")
        approval.approve(d, r["proposal_id"])
        # the approval was for due 2020 — it cannot be redeemed for a different due date
        moved = dict(args, due_at="2099-01-01")
        self.assertFalse(TM.remember(**moved, proposal_id=r["proposal_id"]).get("committed"))
        self.assertTrue(TM.remember(**args, proposal_id=r["proposal_id"])["committed"])
        [item] = _store(d).peek_active()
        self.assertEqual((item.kind, item.mtype), (INTENTION, PERSISTENT))
        self.assertEqual(I.envelope_of(item)["due_at"], "2020-01-01T00:00:00+00:00")

    def test_bad_requests_write_nothing(self):
        d = _repo(self)
        for kw in (dict(kind="intention"), dict(due_at="2020-01-01"),
                   dict(kind="decision", when_path="src"),
                   dict(kind="intention", due_at="2020-13-45")):
            with self.subTest(kw=kw):
                r = TM.remember(path=d, subject="x", value="y", **kw)
                self.assertEqual(r["status"], "error")
        self.assertEqual(_store(d).peek_active(), [])
        self.assertEqual(approval.pending(d), [])


class TheHealingSweep(unittest.TestCase):
    def test_due_surfaces_future_does_not(self):
        d = _repo(self)
        _seed(d, "now", "do it", due_at="2020-01-01")
        _seed(d, "later", "not yet", due_at="2999-01-01")
        props = [p for p in _store(d).detect_issues() if p.kind == INTENTION_DUE]
        self.assertEqual([p.subject for p in props], ["now"])
        self.assertIn("-> done", props[0].diff())
        self.assertIn("Default is DEFER", render_proposal(props[0]))

    def test_approve_is_done_archived_never_deleted(self):
        d = _repo(self)
        _seed(d, "now", "do it", due_at="2020-01-01")
        st = _store(d)
        [p] = [p for p in st.detect_issues() if p.kind == INTENTION_DUE]
        res = st.apply_proposal(p, "approve", assume_yes=True)
        self.assertTrue(res.changed)
        st = _store(d)
        self.assertEqual([p for p in st.detect_issues() if p.kind == INTENTION_DUE], [])
        [kept] = [i for i in st.backend.all() if i.subject == "now"]
        self.assertEqual(kept.status, ARCHIVED)
        self.assertTrue(kept.valid_to)
        self.assertEqual(kept.value, "do it")

    def test_defer_keeps_it_and_it_comes_back(self):
        d = _repo(self)
        _seed(d, "now", "do it", due_at="2020-01-01")
        st = _store(d)
        [p] = [p for p in st.detect_issues() if p.kind == INTENTION_DUE]
        self.assertFalse(st.apply_proposal(p, "defer").changed)
        self.assertEqual(len([p for p in _store(d).detect_issues() if p.kind == INTENTION_DUE]), 1)

    def test_mcp_apply_proposal_is_gated(self):
        d = _repo(self)
        _seed(d, "now", "do it", due_at="2020-01-01")
        r = TM.apply_proposal(path=d, subject="now", decision="approve")
        self.assertEqual(r["status"], "proposed")
        self.assertEqual(_store(d).peek_active()[0].status, ACTIVE)
        approval.approve(d, r["proposal_id"])
        r = TM.apply_proposal(path=d, subject="now", decision="approve",
                              proposal_id=r["proposal_id"])
        self.assertTrue(r["committed"])
        self.assertEqual(_store(d).peek_active(), [])


class TheBriefing(unittest.TestCase):
    def _brief(self, d):
        buf = io.StringIO()
        with mock.patch.object(hook_cli, "_payload_from_stdin",
                               return_value={"cwd": d, "session_id": "S-pm"}), \
                redirect_stdout(buf):
            self.assertEqual(hook_cli.session_start_main([]), 0)
        return json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"]

    def test_n_intentions_due_inside_the_budget(self):
        from mokata.bootstrap import estimate_tokens
        from mokata.context_budget import ContextBudget
        d = _repo(self)
        _seed(d, "auth-review", "re-check token TTL", due_at="2020-01-01")
        _seed(d, "later", "not yet", due_at="2999-01-01")
        ctx = self._brief(d)
        self.assertIn("1 intention(s) due", ctx)
        self.assertIn("auth-review", ctx)
        self.assertNotIn("not yet", ctx)
        self.assertLessEqual(estimate_tokens(ctx), ContextBudget().briefing_tokens)

    def test_silent_without_intentions(self):
        d = _repo(self)
        _store(d).remember(MemoryItem.create("f", "v", mtype=DECISION), assume_yes=True)
        self.assertNotIn("intention", self._brief(d))

    def test_the_line_counts_no_read(self):
        d = _repo(self)
        _seed(d, "now", "do it", due_at="2020-01-01")
        before = _store(d).stats.to_dict()
        self.assertIn("1 intention(s) due", I.briefing_line(_store(d), root=d))
        self.assertEqual(_store(d).stats.to_dict(), before)

    def test_a_broken_check_is_said_not_swallowed(self):
        d = _repo(self)
        _seed(d, "now", "do it", due_at="2020-01-01")
        with mock.patch("mokata.memory.intentions.briefing_line", side_effect=OSError("disk")):
            ctx = self._brief(d)
        self.assertIn("intentions could NOT be checked", ctx)

    def test_unreadable_is_loud_and_left_untouched(self):
        d = _repo(self)
        st = _store(d)
        bad = MemoryItem.create("broken", "v", mtype=PERSISTENT, kind=INTENTION,
                                applicability={"due": {"due_at": "not-a-date"}})
        st.backend.put(bad)                       # a hand-edited / foreign doc, not our writer
        before = bad.to_dict()
        self.assertIn("1 intention(s) UNREADABLE", self._brief(d))
        self.assertEqual([p for p in _store(d).detect_issues() if p.kind == INTENTION_DUE], [])
        [after] = [i for i in _store(d).backend.all() if i.subject == "broken"]
        self.assertEqual(after.to_dict(), before)

    @unittest.skipIf(shutil.which("git") is None, "git not installed")
    def test_path_trigger_fires_from_the_working_tree(self):
        d = _repo(self)
        subprocess.run(["git", "init", "-q", d], check=True, timeout=60)
        _seed(d, "auth-touch", "update the threat model", paths="src/auth")
        self.assertNotIn("intention(s) due", self._brief(d))
        os.makedirs(os.path.join(d, "src", "auth"))
        with open(os.path.join(d, "src", "auth", "token.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        ctx = self._brief(d)
        self.assertIn("1 intention(s) due", ctx)
        self.assertIn("path src/auth changed", ctx)


class ExistingStoresSurvive(unittest.TestCase):
    def test_pre_d15_items_round_trip_unchanged(self):
        d = _repo(self)
        legacy = MemoryItem.create("old", "fact", mtype=EPISODIC)
        doc = legacy.to_dict()
        self.assertEqual(doc.get("applicability"), {})
        _store(d).remember(legacy, assume_yes=True)
        [got] = _store(d).peek_active()
        self.assertEqual((got.kind, got.value, got.applicability), ("", "fact", {}))
        self.assertEqual(I.due_intentions([got]), ([], []))

    def test_no_new_doc_key(self):
        from mokata.memory.item import DOC_KEYS, MEMORY_DOC_VERSION
        it = MemoryItem.create("s", "v", kind=INTENTION, applicability=I.make_envelope(keywords="k"))
        self.assertTrue(set(it.to_dict()) <= DOC_KEYS)
        self.assertEqual(MEMORY_DOC_VERSION, 1)


if __name__ == "__main__":
    unittest.main()
