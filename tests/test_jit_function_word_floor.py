"""JIT-LEXICAL-FUNCTION-WORD-FLOOR (doc 84) — function words are not match EVIDENCE.

The defect, as filed: `lexical_score` is a Jaccard overlap over RAW word tokens with no stopword
handling, and `jit_recall`'s admission test is "any non-zero signal" — so a prompt and an item
sharing only the word *the* score above zero and the item is injected. On a per-turn surface that
spends budget on noise, and noise is how a channel gets tuned out.

WHAT THIS FIX IS, AND IS NOT. It is NOT a minimum-relevance constant — the row is explicit that a
threshold picked against fixtures this small would be tuned to the fixtures, and Jaccard's length
bias makes any such number wrong at a different corpus size. It is the categorical rule
**"function words aren't match evidence"**: the ADMISSION test asks whether the query and the item
share a CONTENT term, and a closed list of English function words is excluded from that question.
That is a property, not a tuning parameter, which is why H-4's BM25 SUBSUMES it rather than
deleting it — IDF drives a term appearing in most of the corpus toward zero weight, i.e. it
reaches the same verdict by measuring what this list asserts.

RANKING IS DELIBERATELY UNTOUCHED. `lexical_score` keeps its raw-token Jaccard, because the FTS
parity work (DB.S3) compares against it and the tiered path keys the same function. Only the
injection path's ADMISSION test changes. Among items that all carry content evidence, ordering is
still the v1 floor's — H-4 is where ranking gets fixed.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import pathlib
import re
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.govern import AuditLedger
from mokata.memory.backends import SQLiteBackend
from mokata.memory.brain import jit_recall
from mokata.memory.episodic import FUNCTION_WORDS, content_tokens, lexical_score
from mokata.memory.item import MemoryItem
from mokata.memory.store import MemoryStore

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "mokata"


def _store(tmp):
    return MemoryStore(SQLiteBackend(os.path.join(tmp, "m.db")),
                       ledger=AuditLedger(os.path.join(tmp, "ledger.jsonl")))


def _put(store, subject, value, kind="context"):
    store.backend.put(MemoryItem.create(subject, value, kind=kind, id=subject))


class TestFunctionWordsAreNotMatchEvidence(unittest.TestCase):
    """P1/P2 — the pinned pair. Both directions, because either alone is passed by a mutant:
    'never inject' passes P1, 'always inject' passes P2."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = _store(self._tmp.name)
        _put(self.store, "invoices", "the invoice template lives in billing/")

    def test_a_query_sharing_only_a_function_word_does_not_inject(self):
        """P1. "the" is the ONLY token this query and the item share. Before the fix this
        returned the item — the exact behaviour doc 84 filed."""
        hits = jit_recall(self.store, "what colour is the bikeshed?")
        self.assertEqual([], hits,
                         "an item sharing only a function word with the query was admitted — "
                         "function words are not match evidence (doc 84 JIT-LEXICAL-FUNCTION-"
                         "WORD-FLOOR)")

    def test_a_real_shared_term_still_injects(self):
        """P2. The other half of the pin: the floor must still FIRE on real evidence."""
        hits = jit_recall(self.store, "where does the invoice template live?")
        self.assertEqual(["invoices"], [h.id for h in hits])

    def test_one_content_term_is_enough_even_amid_many_shared_function_words(self):
        """The admission test is 'shares a content term', not 'shares mostly content terms' —
        a mutant that required a majority, or that scored function words NEGATIVELY, fails here."""
        hits = jit_recall(self.store, "is it the one in the billing folder or not?")
        self.assertEqual(["invoices"], [h.id for h in hits])

    def test_a_query_of_nothing_but_function_words_retrieves_nothing(self):
        """Degenerate case, pinned separately: with every query token filtered out there is no
        evidence at all, so nothing is admitted — NOT 'everything is admitted' (an empty
        intersection must not read as an empty FILTER)."""
        self.assertEqual([], jit_recall(self.store, "is it the one and the other of them?"))

    def test_the_filter_does_not_silence_a_store_with_real_matches(self):
        """Two items, one genuinely relevant and one sharing only function words: the weak one
        drops and the strong one survives — the fix narrows, it does not empty."""
        _put(self.store, "bikeshed", "the bikeshed colour is not a technical decision")
        hits = jit_recall(self.store, "which invoice template do we use?")
        self.assertEqual(["invoices"], [h.id for h in hits])


class TestTheClosedListIsSingleSourced(unittest.TestCase):

    def test_the_list_is_a_closed_frozen_set_of_lowercase_words(self):
        self.assertIsInstance(FUNCTION_WORDS, frozenset)
        self.assertGreater(len(FUNCTION_WORDS), 20, "too small to cover English function words")
        self.assertLessEqual(len(FUNCTION_WORDS), 120,
                             "a stopword list this long stops being closed-class")
        for w in FUNCTION_WORDS:
            self.assertTrue(w.isalpha() and w.islower(), f"{w!r} is not a lowercase word")

    def test_there_is_exactly_one_definition_site_in_the_tree(self):
        """SINGLE-SOURCING, pinned structurally. A second copy is the failure mode this guards:
        two lists that drift is worse than one list in an imperfect place."""
        pat = re.compile(r"^FUNCTION_WORDS\s*=", re.M)
        sites = [p for p in _SRC.rglob("*.py")
                 if pat.search(p.read_text(encoding="utf-8"))]
        self.assertEqual([_SRC / "memory" / "episodic.py"], sites,
                         "the injection stopword list must be defined in exactly one place")

    def test_it_is_deliberately_NOT_the_secret_guards_list(self):
        """The secret guard has a frozen `_FUNCTION_WORDS` too, and reusing it was the preferred
        option. It is the wrong SHAPE and the wrong DIRECTION:

          * SHAPE — its two-letter members are frozen to exactly {is, on, no} because an
            unrestricted two-letter set collided with ~4% of random key debris (0.0325% residual
            vs a 0.007% budget). So it deliberately omits `a`, `an`, `of`, `to`, `in`, `it`, `as`,
            `at`, `by`, `be`, `or` — several of the most common words in English, and precisely
            the ones a stopword list exists to remove.
          * DIRECTION — there the list is a WHITELIST that widens what counts as a word, so an
            entry makes the scanner MORE permissive (a security-relevant loosening). Here it is a
            BLACKLIST that narrows match evidence, so an entry makes injection STRICTER. The two
            are under opposite pressure: a word added for one is a regression for the other.

        Pinned as an assertion so a later 'de-duplicate these' refactor fails loudly."""
        from mokata.govern.secrets import _FUNCTION_WORDS as SECRET_WORDS
        self.assertNotEqual(SECRET_WORDS, FUNCTION_WORDS)
        missing = {"a", "an", "of", "to", "in", "it", "as", "at", "by", "be", "or"}
        self.assertEqual(set(), missing & SECRET_WORDS,
                         "the secret guard's list grew two-letter entries — re-check its own "
                         "entropy-collision pin before assuming it is now stopword-shaped")
        self.assertEqual(missing, missing & FUNCTION_WORDS,
                         "the injection stopword list must carry the common short words the "
                         "secret guard's list cannot")


class TestRankingIsUnchanged(unittest.TestCase):
    """The blast radius is the ADMISSION test only. `lexical_score` is the shared ranker that
    DB.S3's FTS parity compares against — a stopword-aware `lexical_score` would silently move
    that comparison, so the filter must NOT be applied inside it."""

    def test_lexical_score_still_scores_a_shared_function_word(self):
        self.assertGreater(lexical_score("the bikeshed", "the invoice"), 0.0)

    def test_content_tokens_drops_function_words_and_keeps_the_rest(self):
        self.assertEqual({"invoice", "template", "billing", "folder"},
                         content_tokens("the invoice template is in the billing folder"))

    def test_content_tokens_keeps_digits_and_identifiers(self):
        self.assertEqual({"utf8", "3", "retries"}, content_tokens("utf8 has 3 retries"))


if __name__ == "__main__":
    unittest.main()
