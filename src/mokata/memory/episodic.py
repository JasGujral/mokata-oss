"""C3 — episodic conversation memory.

A searchable local store of past conversation turns, wired as a third memory type
(`episodic`) on top of the existing pluggable backends and the per-type toggle. Recording
a turn goes through the same human-gated write path; search honors the toggle (a disabled
episodic type surfaces nothing).

Embeddings are OPTIONAL: pass an `embedder(text) -> vector` for semantic ranking; with
none, search degrades to a dependency-free lexical (keyword-overlap) ranking. No new
required runtime dependencies.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, List, Optional, Tuple

from .item import DEFAULT_TOP_K, EPISODIC, MemoryItem

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set:
    return set(_WORD.findall(text.lower()))


def lexical_score(query: str, text: str) -> float:
    """Jaccard overlap of word tokens — the no-dependency fallback ranking.

    Deliberately raw-token: NO stopword handling here. This function is the shared v1 ranker —
    `tiered.py` keys it and DB.S3's FTS parity tests compare against it — so filtering inside it
    would silently move a comparison that is not this fix's business. Function words are removed
    from the ADMISSION test instead (`content_tokens`, used by `jit_recall`)."""
    q, t = _tokens(query), _tokens(text)
    if not q or not t:
        return 0.0
    return len(q & t) / len(q | t)


# --- Function words are not match EVIDENCE (doc 84 JIT-LEXICAL-FUNCTION-WORD-FLOOR) ----------
# A closed list of English closed-class words, excluded from the question "do this query and this
# item share a term?" — the admission test on the per-turn injection path. Without it a prompt and
# an item sharing only the word `the` score above zero and the item is injected, so a turn with
# nothing genuinely relevant still spends budget on noise.
#
# THIS IS NOT A TUNED RELEVANCE CONSTANT. A minimum-score threshold is what doc 84 rejected: it
# would be fitted to fixtures, and Jaccard's length bias makes any single number wrong at a
# different corpus size (a 60-token item matching 3 query words scores BELOW a 10-token item
# matching one function word). This is the categorical claim "function words carry no evidence",
# which H-4's BM25 SUBSUMES rather than deletes — IDF reaches the same verdict by measuring what
# this list asserts, so H-4 replaces the mechanism and keeps the property.
#
# WHY NOT `govern.secrets._FUNCTION_WORDS`, the existing frozen list — reuse was preferred and is
# wrong on both counts:
#   * SHAPE. Its two-letter members are frozen to exactly {is, on, no}, because an unrestricted
#     two-letter set collided with ~4% of random key debris (0.0325% residual against a 0.007%
#     budget). It therefore omits `a`, `an`, `of`, `to`, `in`, `it`, `as`, `at`, `by`, `be`, `or`
#     — several of the commonest words in English, and exactly what a stopword list is for.
#   * DIRECTION. There the list is a WHITELIST widening what counts as a word, so adding an entry
#     makes the scanner MORE permissive — a security-relevant loosening measured against a key
#     corpus. Here it is a BLACKLIST narrowing match evidence, so adding an entry makes injection
#     STRICTER. Same words, opposite pressure: an addition that is right for one is a regression
#     for the other, and a shared list would couple those two review bars forever.
# `test_jit_function_word_floor.TestTheClosedListIsSingleSourced` pins both the one definition
# site and the deliberate non-reuse, so neither drifts back.
#
# KNOWN NARROWING, recorded not hidden: several entries are also Python keywords (`if`, `for`,
# `in`, `is`, `not`, `or`, `and`, `as`). A query whose ONLY overlap with an item is one of those
# no longer injects. That is the intended trade — a bare keyword is not evidence a memory is
# relevant — and such queries essentially always carry another term. Kept closed-class only:
# no domain-loaded additions, which is where a stopword list starts eating real evidence.
FUNCTION_WORDS = frozenset({
    # articles / determiners / quantifiers
    "a", "an", "the", "this", "that", "these", "those", "some", "any", "each", "every",
    "all", "both", "no", "other", "such", "same",
    # pronouns / possessives
    "i", "me", "my", "we", "us", "our", "you", "your", "he", "him", "his", "she", "her",
    "it", "its", "they", "them", "their", "who", "whom", "whose",
    # prepositions / particles
    "of", "to", "in", "for", "on", "at", "by", "with", "from", "into", "onto", "over",
    "under", "about", "between", "through", "before", "after", "up", "down", "out", "off",
    # conjunctions
    "and", "or", "but", "nor", "so", "if", "then", "than", "because", "while", "as",
    "until", "unless", "though", "although", "whether",
    # auxiliaries / copulas / negation
    "is", "are", "was", "were", "be", "been", "being", "am", "do", "does", "did", "have",
    "has", "had", "can", "could", "will", "would", "shall", "should", "may", "might",
    "must", "not",
    # interrogatives / pro-forms
    "what", "which", "when", "where", "why", "how", "there", "here",
})


def content_tokens(text: str) -> set:
    """The tokens that count as MATCH EVIDENCE — `_tokens` minus the closed function-word list.

    Used by the injection path's admission test, never by the ranker. Digits and identifiers are
    kept: `utf8`, `3`, `v2` are evidence, they are simply not English function words."""
    return _tokens(text) - FUNCTION_WORDS


def shares_content_term(query: str, text: str) -> bool:
    """Whether `query` and `text` share at least one CONTENT term.

    ONE shared content term is the bar — not a majority, and not a score. An item is worth
    injecting when the turn mentions something it is about; how MUCH it is about it is the
    ranker's question, and the top-k cut's. A query made entirely of function words has no
    content tokens, so it shares none and admits nothing — an empty intersection must read as
    NO EVIDENCE, never as an absent filter."""
    return bool(content_tokens(query) & content_tokens(text))


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class EpisodicMemory:
    """Episodic turns over a MemoryStore (reusing its backend, toggle, and gate)."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def record(self, session: str, text: str, role: str = "user",
               confirm: Optional[Callable[[str], bool]] = None,
               assume_yes: bool = False) -> Any:
        """Record one conversation turn (human-gated like any memory write)."""
        item = MemoryItem.create(subject=session, value=text, mtype=EPISODIC,
                                 source=role)
        return self.store.remember(item, confirm=confirm, assume_yes=assume_yes)

    def search(self, query: str, top_k: int = DEFAULT_TOP_K,
               embedder: Optional[Callable[[str], List[float]]] = None,
               ) -> List[Tuple[MemoryItem, float]]:
        """Return up to `top_k` (turn, score) pairs, best first. Uses `embedder` for
        semantic ranking when supplied; otherwise lexical overlap. Honors the toggle —
        returns [] when episodic memory is disabled."""
        turns = self.store.all_active(mtype=EPISODIC)
        if not turns:
            return []
        if embedder is not None:
            qv = embedder(query)
            scored = [(t, _cosine(qv, embedder(t.value))) for t in turns]
        else:
            scored = [(t, lexical_score(query, t.value)) for t in turns]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]
