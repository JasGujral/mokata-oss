"""EXPLICIT-EMBEDDER-ASK — an ask that NAMED an embedder and got hashing must say so.

THE GAP. `detect_embedder` deliberately stays SILENT when the `embeddings` extra is merely absent:
"a notice that fires on every default install is noise" (the DB.S3 lexical-floor lesson), and that
reasoning is exactly right for ``auto``, which is a request to use whatever is best.

It is NOT right for an ask that named the extra. A user who wrote ``memory.embedder: model2vec``
and got token-hashing was told nothing — the config said one thing, the process did another, and
the only way to find out was to run `doctor` and think to compare.

WHAT CHANGED THE SEVERITY, and it is why this is being fixed now rather than filed as a nicety:
DB.S8f's K2 bound. Before it, an unnoticed fallback meant "semantic recall is worse than you
think". After it, `HashingEmbedder`'s 0.65 noise floor weights the semantic tier down to **0.077**
— so the tier the user explicitly asked for is very nearly INERT. The degrade was always real; it
is now large enough that silence is misreporting.

TWO ASK SITES, and the second is the one that would not have been found by reading the config
schema. `selection.py`'s pgvector branch reaches a live embedder — the ONE path DB.S8f found that
does so without `memory.embedder` being set anywhere. Landing on hashing there fills a REAL
pgvector index with token-hash vectors, which deserves the same notice as the named ask.

THAT SECOND SITE NO LONGER LANDS ON HASHING AT ALL (VECTOR-TIER-NOISE, 2026-08-02), and the two
sites have parted company. Announcing the fallback was the right fix for a NAMED ask, where the
user chose and mokata must honour the choice out loud. It was never sufficient for the UNSET one:
DB.S8g measured at the declared N=100,000 that the hashing tier is net-NEGATIVE on recall (arm C
0.4306 < arm B 0.4444), and a tier that subtracts recall must not be switched on by the ABSENCE of
configuration, however loudly. So the branch now splits the ask:

  * NAMED  -> resolves exactly as before, and everything below still holds for it.
  * UNSET  -> a real embedder or NOTHING. The tier stays OFF and the store falls to the local
              floor with a notice naming the cause. `hashing` is still available — by ASKING.

Both halves are pinned in `TheAskSiteIsREACHED`, and they are pinned as REACHABILITY pins (driving
the real `_select_raw_backend`) rather than by calling `make_embedder` directly, because that is
the exact failure mutation caught here the first time.

WHAT STAYS SILENT, pinned as hard as what speaks — a notice nobody wants is how a channel dies:
  * ``auto`` on its own — the documented zero-dep default. Unchanged.
  * ``hashing``/``local`` — they asked for hashing and got hashing. Nothing happened.
  * the extra INSTALLED but the model unloadable — `detect_embedder` already says so with the
    better message, because it knows the cause. Saying it twice for one fallback is noise.
"""
import unittest
from unittest import mock

import _support  # noqa: F401

from mokata import degrade
from mokata.memory import embed
from mokata.memory.embed import HASHING_ID, HashingEmbedder, ModelUnavailable, make_embedder


class FreshNoticeGuard(unittest.TestCase):
    """`note_degraded` fires ONCE PER SUBSYSTEM PER PROCESS, so without this the first test to
    trip a notice spends it for every test after — which reads as "the notice does not fire" and
    is how a real regression would be mistaken for a dedup. Each test gets a fresh guard.

    BOTH module stores are isolated, not just `_EMITTED`. `_NOTICES` is the RECORD that
    `emitted_notices()` (and `doctor`) reads, and it is written on a different key path from the
    dedup set — so a test that asserts against `emitted_notices()` while only `_EMITTED` is fresh
    is reading every earlier test's notices too. Found by mutation: silencing the unset-pgvector
    notice outright left `TheAskSiteIsREACHED`'s notice pins GREEN, because an earlier test in the
    same process had already recorded a real one under the same subsystem."""

    def setUp(self):
        for name, empty in (("_EMITTED", set()), ("_NOTICES", {})):
            patch = mock.patch.object(degrade, name, empty)
            patch.start()
            self.addCleanup(patch.stop)


class _Sink:
    """A `degrade_out` that records instead of printing."""

    def __init__(self):
        self.lines = []

    def __call__(self, msg):
        self.lines.append(msg)

    @property
    def text(self):
        return "\n".join(self.lines)


def _extra_absent():
    """The extra is not installed: the model load fails AND the import probe says why."""
    return (mock.patch.object(embed, "_load_model2vec",
                              side_effect=ModelUnavailable("model2vec is not installed")),
            mock.patch.object(embed, "_extra_is_installed", return_value=False))


def _extra_present_model_broken():
    """The extra IS installed, but the model cannot be loaded (cold cache, hub error)."""
    return (mock.patch.object(embed, "_load_model2vec",
                              side_effect=ModelUnavailable("hub unreachable")),
            mock.patch.object(embed, "_extra_is_installed", return_value=True))


class TheNamedAskIsAudible(FreshNoticeGuard):
    def _resolve(self, name, **kw):
        sink = _Sink()
        load, installed = _extra_absent()
        with load, installed:
            got = make_embedder(name, degrade_out=sink, **kw)
        return got, sink

    def test_an_explicit_model2vec_ask_that_lands_on_hashing_says_so(self):
        got, sink = self._resolve("model2vec")
        self.assertEqual(HASHING_ID, got.embedder_id)
        self.assertIn("memory-embedder-ask", sink.text,
                      "an explicit `model2vec` ask fell back to token-hashing and said NOTHING — "
                      "the config says one thing and the process does another")

    def test_the_notice_carries_the_actionable_fix(self):
        _got, sink = self._resolve("model2vec")
        self.assertIn("mokata[embeddings]", sink.text, "the notice does not say how to fix it")

    def test_the_notice_names_the_consequence_not_just_the_fallback(self):
        """The K2 weight is the whole reason this notice exists — "you got hashing" understates
        it, because a reader has no way to know 0.65 noise means a 0.077 weight."""
        _got, sink = self._resolve("model2vec")
        self.assertIn("0.077", sink.text)

    def test_the_embeddings_alias_is_treated_as_the_same_ask(self):
        _got, sink = self._resolve("embeddings")
        self.assertIn("memory-embedder-ask", sink.text)

    def test_a_semantic_STORE_optin_is_an_ask_even_with_no_embedder_named(self):
        """`selection.py`'s pgvector branch — the one path that reaches a live embedder without
        `memory.embedder` being set. A vector index full of token-hash vectors is worth a word."""
        _got, sink = self._resolve("auto", semantic_store=True)
        self.assertIn("memory-embedder-ask", sink.text)
        self.assertIn("pgvector", sink.text,
                      "the notice does not say WHICH ask it is answering — a user who never wrote "
                      "`memory.embedder` will not recognise themselves in it")


class TheQuietPathsStayQuiet(FreshNoticeGuard):
    """A notice nobody wants is how a channel gets tuned out. Each of these is a case where the
    old silence was RIGHT and must survive."""

    def test_bare_auto_with_the_extra_absent_is_silent(self):
        sink = _Sink()
        load, installed = _extra_absent()
        with load, installed:
            got = make_embedder("auto", degrade_out=sink)
        self.assertEqual(HASHING_ID, got.embedder_id)
        self.assertEqual("", sink.text,
                         "`auto` now warns on every default install — this is the exact noise "
                         "`detect_embedder` documents itself as avoiding")

    def test_an_explicit_hashing_ask_is_silent(self):
        for name in ("hashing", "local"):
            sink = _Sink()
            got = make_embedder(name, degrade_out=sink)
            self.assertIsInstance(got, HashingEmbedder)
            self.assertEqual("", sink.text, f"`{name}` warned about getting what it asked for")

    def test_an_unset_embedder_is_silent_and_still_OFF(self):
        sink = _Sink()
        self.assertIsNone(make_embedder(None, degrade_out=sink))
        self.assertEqual("", sink.text)

    def test_the_installed_but_broken_model_is_announced_ONCE_by_detect_not_twice(self):
        """`detect_embedder` already announces this case with the better message — it knows the
        cause is the MODEL, not the install. The ask-site notice must not double it."""
        sink = _Sink()
        load, installed = _extra_present_model_broken()
        with load, installed:
            got = make_embedder("model2vec", degrade_out=sink)
        self.assertEqual(HASHING_ID, got.embedder_id)
        self.assertIn("memory-embedder", sink.text, "the model-load degrade went unreported")
        self.assertNotIn("memory-embedder-ask", sink.text,
                         "ONE fallback produced TWO notices — the ask-site notice is firing for a "
                         "case `detect_embedder` already explains better")


class TheAskSiteIsREACHED(FreshNoticeGuard):
    """THE REACHABILITY PIN (doc 84 REACHABILITY-PINS-MISSING).

    Every test above constructs the precondition itself — it calls `make_embedder` with the flag
    and checks what comes out. That proves the CODE works and says NOTHING about whether any
    production path passes the flag. Mutation caught exactly that: deleting `semantic_store=True`
    from `selection.py`'s pgvector branch left all ten pins GREEN.

    This one drives the REAL selection path and never names `semantic_store` at all."""

    def _select(self, config):
        """Drive the real pgvector branch to the local floor. `MOKATA_ABSENT_DSN` is unset, so
        `build_pgvector_backend` returns None after the embedder question has been answered —
        which is the only part under test here."""
        import tempfile

        from mokata.memory import selection

        return selection._select_raw_backend(
            "pgvector", tempfile.mkdtemp(), {}, config, None, None)

    def test_a_NAMED_pgvector_ask_still_flags_itself_as_a_semantic_store(self):
        """The named half, unchanged by VECTOR-TIER-NOISE: `embedder: model2vec` in a pgvector
        store still resolves, and still reaches the notice when it lands on hashing."""
        sink = _Sink()
        load, installed = _extra_absent()
        seen = []
        real = embed.make_embedder

        def spy(name, **kw):
            seen.append(kw.get("semantic_store", False))
            return real(name, degrade_out=sink, **{k: v for k, v in kw.items()
                                                   if k != "degrade_out"})

        # Patched at its SOURCE module, not on `selection`: the pgvector branch does a function-
        # local `from .embed import make_embedder`, so there is no `selection.make_embedder`
        # attribute to replace — and patching a name that does not exist is how a reachability pin
        # ends up asserting against a spy nothing ever calls.
        with load, installed, mock.patch.object(embed, "make_embedder", spy):
            be = self._select({"dsn_env": "MOKATA_ABSENT_DSN", "embedder": "model2vec"})
        be.close()
        self.assertTrue(seen, "the pgvector branch never resolved the embedder it was GIVEN — "
                              "this pin proved nothing and the branch it guards has moved")
        self.assertTrue(any(seen),
                        "`selection.py`'s pgvector branch resolved an embedder WITHOUT marking it "
                        "a semantic-store ask, so a team that opts into a vector store and lands "
                        "on token-hashing is told nothing — the notice exists but nothing reaches "
                        "it")

    def test_an_UNSET_pgvector_ask_leaves_the_tier_OFF_rather_than_using_hashing(self):
        """VECTOR-TIER-NOISE. The branch that used to default to ``auto``: with nothing configured
        and the extra absent, the answer is OFF — never a vector index full of token hashes.

        Asserted through `make_embedder`, which is the ONLY way hashing can be reached from here:
        the pin is "hashing is not reachable on this path", not "some particular call was skipped",
        so it survives the branch being rewritten as long as the guarantee holds."""
        load, installed = _extra_absent()
        reached = []

        def spy(name, **kw):
            reached.append(name)
            return HashingEmbedder()

        with load, installed, mock.patch.object(embed, "make_embedder", spy):
            be = self._select({"dsn_env": "MOKATA_ABSENT_DSN"})
        be.close()
        self.assertEqual([], reached,
                         "a pgvector store with NO embedder configured resolved one anyway — the "
                         "branch is defaulting again, and an unset config silently fills a real "
                         "vector index with token-hash vectors that measure net-negative on "
                         "recall at N=100,000 (DB.S8g arm C 0.4306 < arm B 0.4444)")

    def test_the_UNSET_path_says_the_tier_is_off_and_how_to_turn_it_on(self):
        """Off must not be SILENT either — that is the same bug facing the other way. The notice
        has to name the cause and leave the user both doors: install the extra, or ask for hashing
        deliberately."""
        load, installed = _extra_absent()
        with load, installed:
            be = self._select({"dsn_env": "MOKATA_ABSENT_DSN"})
        be.close()
        rendered = "\n".join(n.render() for n in degrade.emitted_notices())
        # The CAUSE, not just "OFF". `_note_vector_degrade` fires for this same subsystem right
        # after (the DSN is unset too) and also says "semantic recall is OFF" — so asserting on
        # "OFF" alone is satisfied by a notice about a completely different problem, and would let
        # this one be deleted without a red. This sentence is only in the notice under test.
        self.assertIn("no embedder was configured", rendered,
                      "the semantic tier was switched off for want of an embedder and nothing "
                      "said so — the user sees local memory and no reason for it")
        self.assertIn("OFF", rendered)
        self.assertIn("mokata[embeddings]", rendered, "the notice does not say how to turn it on")
        self.assertIn("memory.embedder: hashing", rendered,
                      "the notice refuses hashing without saying that ASKING for it still works — "
                      "a refusal with no door is a removal")

    def test_the_UNSET_path_names_the_RIGHT_cause_when_the_extra_is_installed(self):
        """Same verdict, different fix. Extra installed + model unloadable is a network/cache
        problem, and sending that user to `pip install` is sending them to the wrong place."""
        load, installed = _extra_present_model_broken()
        with load, installed:
            be = self._select({"dsn_env": "MOKATA_ABSENT_DSN"})
        be.close()
        rendered = "\n".join(n.render() for n in degrade.emitted_notices())
        self.assertIn("could not be loaded", rendered)
        self.assertIn("mokata doctor", rendered)


class TheSuccessPathIsUnchanged(FreshNoticeGuard):
    def test_a_named_ask_that_RESOLVES_is_silent(self):
        class _Fake:
            embedder_id = "model2vec:minishlab/potion-base-8M"
            dim = 256

            def __call__(self, text):
                return [0.1] * 256

        sink = _Sink()
        with mock.patch.object(embed, "Model2VecEmbedder", return_value=_Fake()):
            got = make_embedder("model2vec", degrade_out=sink)
        self.assertEqual("model2vec:minishlab/potion-base-8M", got.embedder_id)
        self.assertEqual("", sink.text, "a SUCCESSFUL explicit ask emitted a degrade notice")


if __name__ == "__main__":
    unittest.main()
