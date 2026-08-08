"""DECLARED cross-engine SQL translation for test doubles.

THE INVARIANT THIS MODULE EXISTS TO HOLD:

    A pass produced by TRANSLATING Postgres SQL onto SQLite can never be reported as a pass
    against Postgres.

Before this module a green meant two incompatible things — "this ran against Postgres semantics"
and "this ran against a rewrite of the query that SQLite happened to accept" — and nothing in the
tree could tell them apart. That is doc 85 §7g exactly: an absent answer and a real answer sharing
one representation, failing open every time because the safe-looking meaning is "nothing to see
here." The remedy is §7g's remedy: SPLIT THE REPRESENTATION. Every result now carries the
`EngineBasis` that produced it, and `SQLITE_TRANSLATED` is a different value from `POSTGRES_LIVE`.

DECLARATION IS NOT ENOUGH, SO THIS ENFORCES.

A rule list a shim declares but does not obey is a caller list asserted in a comment. So the base
class here is the ONLY code in the tree that rewrites SQL, and it applies EXACTLY the rules its
`Declaration` names — no subclass gets to reach past it. A statement that would need a rewrite the
suite did not declare does not quietly run; it raises `UndeclaredTranslation`. The declaration and
its enforcement are the same object.

WHAT IT REFUSES OUTRIGHT (measured hazards, not hypotheses):

  * `WITH RECURSIVE` — MEASURED at DB.S7b (2026-07-31): a recursive CTE runs PERFECTLY on SQLite
    through a shim, returning `[('a',0),('b',1),('c',2)]`. A "Postgres traversal" test written
    against a shim therefore PASSES while comparing SQLite against itself. Postgres's recursive-CTE
    anchor TYPE INFERENCE refuses `SELECT ?, 0` where dynamically-typed SQLite accepts it — the
    same family as the `NULL::bigint` trap `teamdb.py:622-624` records.
  * TEXT-COLLATION ORDERING — SQLite sorts text BINARY; Postgres sorts by the database collation.
    Identical rows legitimately come back in different orders, so an `ORDER BY` over a TEXT column
    is a claim the shim cannot make. Declare it or do not order on text.

A suite that genuinely needs one states it in `accepts_divergence`, which puts the concession in
the declaration where a reader and the sweep both see it, rather than in nobody's head.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import re
import sqlite3
import sys


# ====================================================================== the split representation
class EngineBasis:
    """WHICH ENGINE ACTUALLY ANSWERED. The `RunResolution` analogue: an answer that carries its own
    provenance cannot be mistaken for a different answer.

    These are three values, never a boolean and never a default. `SQLITE_TRANSLATED` is not a
    weaker `POSTGRES_LIVE`; it is a different claim about a different engine."""

    POSTGRES_LIVE = "postgres-live"          # real psycopg against a real server (MOKATA_TEST_DSN)
    SQLITE_NATIVE = "sqlite-native"          # SQLite running SQL written for SQLite
    SQLITE_TRANSLATED = "sqlite-translated"  # SQLite running a REWRITE of SQL written for Postgres

    ALL = (POSTGRES_LIVE, SQLITE_NATIVE, SQLITE_TRANSLATED)


class UndeclaredTranslation(AssertionError):
    """A statement needed a rewrite, or carried a divergence, that the suite did not declare.

    An `AssertionError` on purpose: this is a test-harness honesty failure, and it must read as a
    FAILING TEST rather than as an error in the code under test."""


# ============================================================================ declaration vocabulary
class Rewrite:
    """One named, exactly-enumerated string rewrite applied to the emitted SQL.

    `why` is not decoration. It is the sentence a future reader needs to decide whether the rewrite
    still preserves the semantics the suite is claiming."""

    def __init__(self, name, apply, why):
        self.name = name
        self._apply = apply
        self.why = why

    def apply(self, sql):
        return self._apply(sql)

    @classmethod
    def literal(cls, name, old, new, why):
        return cls(name, lambda s: s.replace(old, new), why)

    @classmethod
    def regex(cls, name, pattern, replacement, why):
        rx = re.compile(pattern)
        return cls(name, lambda s: rx.sub(replacement, s), why)


class EmulatedFunction:
    """A Postgres function or operator re-implemented as a SQLite user function.

    It SURVIVES into the executed SQL (unlike a `Rewrite`, which erases its input), so the refusal
    check has to know its name or it would read as an unaccounted-for Postgres construct."""

    def __init__(self, name, arity, impl, why):
        self.name = name
        self.arity = arity
        self.impl = impl
        self.why = why


class Interception:
    """A statement the double ANSWERS ITSELF instead of executing.

    Materially stronger than a rewrite — the engine never sees the query at all — so it is a
    separate category and is never allowed to hide inside the word "rewrite"."""

    def __init__(self, name, matches, respond, why):
        self.name = name
        self.matches = matches
        self.respond = respond
        self.why = why


class ResultAdaptation:
    """A declared change to what `execute` RETURNS (cursor shape, rowcount source).

    Carries no behaviour here; the subclass implements it in `_adapt`. It exists so a semantically
    load-bearing adaptation — a CAS test reading `rowcount` is trusting one — is written down."""

    def __init__(self, name, why):
        self.name = name
        self.why = why


class Declaration:
    """What one suite translates, why, and what its green therefore does NOT prove."""

    def __init__(self, suite, reason, rewrites=(), functions=(), interceptions=(),
                 adaptations=(), accepts_divergence=(), not_proven=()):
        self.suite = suite
        self.reason = reason
        self.rewrites = tuple(rewrites)
        self.functions = tuple(functions)
        self.interceptions = tuple(interceptions)
        self.adaptations = tuple(adaptations)
        self.accepts_divergence = tuple(accepts_divergence)
        self.not_proven = tuple(not_proven)
        if not self.not_proven:
            raise UndeclaredTranslation(
                f"{suite}: a translating double MUST state what its green does not prove. An "
                f"empty `not_proven` is the false green this module exists to stop.")

    # -- the self-label ----------------------------------------------------------------
    def label(self):
        """The one line that makes a translated pass legible in the suite's own output."""
        counts = (f"{len(self.rewrites)} rewrite(s), {len(self.functions)} emulated function(s), "
                  f"{len(self.interceptions)} interception(s)")
        return (f"[{EngineBasis.SQLITE_TRANSLATED}] {self.suite}: Postgres SQL is TRANSLATED onto "
                f"SQLite ({counts}). NOT PROVEN HERE: {'; '.join(self.not_proven)}")

    def rule_names(self):
        return tuple(sorted(
            [r.name for r in self.rewrites]
            + [f.name for f in self.functions]
            + [i.name for i in self.interceptions]
            + [a.name for a in self.adaptations]))


# ================================================================================ divergence table
# Postgres constructs that SQLite either rejects or SILENTLY REINTERPRETS. Each must be accounted
# for by a declared rewrite (which erases it), a declared emulated function (which keeps its name
# but supplies the behaviour), or an explicit `accepts_divergence` entry.
_DIVERGENT = (
    ("%s", "unbound Postgres placeholder — SQLite reads `%s` as a LITERAL, so the query still "
           "runs and silently matches nothing"),
    ("::", "Postgres cast — SQLite has no `::`"),
    ("@@", "Postgres text-search MATCH operator"),
    ("<=>", "pgvector cosine-distance operator"),
    ("->>", "JSON extraction whose null-and-type semantics differ between the engines"),
    ("to_regclass", "Postgres catalogue lookup"),
    ("to_tsvector", "Postgres text-search vector"),
    ("to_tsquery", "Postgres text-search query"),
    ("ts_rank", "Postgres BM25-family ranking"),
    ("now()", "Postgres transaction timestamp — `now()` is STATEMENT-stable in SQLite and "
              "TRANSACTION-stable in Postgres, so a test about write ordering is not the same test"),
    ("ilike", "Postgres case-insensitive LIKE"),
)

_RECURSIVE = re.compile(r"\bWITH\s+RECURSIVE\b", re.IGNORECASE)
_ORDER_BY = re.compile(r"\bORDER\s+BY\b(.*?)(?:\bLIMIT\b|\bOFFSET\b|$)", re.IGNORECASE | re.DOTALL)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

DIVERGENCE_RECURSIVE_CTE = "recursive-cte"
DIVERGENCE_TEXT_COLLATION = "text-collation-order"

_ORDER_KEYWORDS = {"asc", "desc", "nulls", "first", "last", "collate", "case", "when", "then",
                   "else", "end", "and", "or", "not", "null", "cast", "as"}


def _split_top_level(clause):
    """Split an ORDER BY clause on commas that are NOT inside parentheses."""
    terms, depth, start = [], 0, 0
    for i, ch in enumerate(clause):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            terms.append(clause[start:i])
            start = i + 1
    terms.append(clause[start:])
    return [t.strip() for t in terms if t.strip()]


def _announced():
    # one banner per declaration per process — a label repeated 400 times is noise, and noise is
    # how a label stops being read.
    if not hasattr(_announced, "seen"):
        _announced.seen = set()
    return _announced.seen


class TranslatingConnection:
    """The ONE sanctioned cross-engine translator. Subclass it, hand it a `Declaration`, and the
    rewriting is done here or it is not done at all.

    Subclasses supply schema and helpers; they must NOT override `execute`. The repo-wide sweep in
    `tests/test_shim_declaration.py` is what makes that binding: it detects the EXECUTION OF A
    REWRITTEN SQL STRING anywhere under `tests/`, structurally, and this module is the only place
    allowed to do it."""

    basis = EngineBasis.SQLITE_TRANSLATED

    def __init__(self, declaration, connection=None):
        if not isinstance(declaration, Declaration):
            raise UndeclaredTranslation(
                "a translating double must be constructed with a Declaration — an undeclared "
                "translation is the defect this class exists to make impossible")
        self.declaration = declaration
        self._c = connection if connection is not None else sqlite3.connect(":memory:")
        for fn in declaration.functions:
            self._c.create_function(fn.name, fn.arity, fn.impl)
        self.sql_log = []
        self.translations = {}
        self._text_columns = None
        self._checked = {}
        seen = _announced()
        if declaration.suite not in seen:
            seen.add(declaration.suite)
            if not os.environ.get("MOKATA_QUIET_SHIM_LABEL"):
                print(declaration.label(), file=sys.stderr)

    # -- the connection contract -------------------------------------------------------
    def execute(self, sql, params=()):
        """Translate by the DECLARED rules only, refuse anything undeclared, then execute."""
        self.sql_log.append(sql)
        bound = tuple(params or ())

        for icept in self.declaration.interceptions:
            if icept.matches(sql):
                self._count(icept.name)
                return icept.respond(self._c, sql, bound)

        run = sql
        for rule in self.declaration.rewrites:
            after = rule.apply(run)
            if after != run:
                self._count(rule.name)
                run = after

        self._refuse_undeclared(sql, run)
        return self._adapt(self._c.execute(run, bound))

    def _adapt(self, cursor):
        """Override to declare a ResultAdaptation. The default returns the cursor untouched."""
        return cursor

    def close(self):
        self._c.close()

    # -- enforcement -------------------------------------------------------------------
    def _count(self, name):
        self.translations[name] = self.translations.get(name, 0) + 1

    def _refuse_undeclared(self, original, rewritten):
        cached = self._checked.get(rewritten)
        if cached is None:
            cached = self._analyse(rewritten)
            self._checked[rewritten] = cached
        if cached:
            raise UndeclaredTranslation(
                f"{self.declaration.suite} translated Postgres SQL onto SQLite without declaring "
                f"it.\n  statement: {original.strip()[:400]}\n  after declared rewrites: "
                f"{rewritten.strip()[:400]}\n  undeclared: " + "; ".join(cached) +
                "\n  Declare it in the suite's Declaration (a Rewrite, an EmulatedFunction, an "
                "Interception, or accepts_divergence) — or stop translating. An approximate "
                "declaration is worse than none, because it looks checked.")
        return True

    def _analyse(self, sql):
        problems = []
        declared = {f.name.lower() for f in self.declaration.functions}
        declared |= {i.name.lower() for i in self.declaration.interceptions}
        accepted = {d.lower() for d in self.declaration.accepts_divergence}
        low = sql.lower()

        for token, why in _DIVERGENT:
            if token.lower() not in low:
                continue
            key = token.lower().rstrip("()")
            if key in declared or key in accepted or token.lower() in accepted:
                continue
            problems.append(f"`{token}` survived into the executed SQL ({why})")

        if _RECURSIVE.search(sql) and DIVERGENCE_RECURSIVE_CTE not in accepted:
            problems.append(
                "`WITH RECURSIVE` — MEASURED at DB.S7b: SQLite runs a recursive CTE perfectly, so "
                "this would go GREEN while proving nothing about Postgres (anchor type inference "
                "differs). Use the live-DB leg")

        if DIVERGENCE_TEXT_COLLATION not in accepted:
            ordered = self._text_ordering(sql)
            if ordered:
                problems.append(
                    "`ORDER BY` over TEXT column(s) " + ", ".join(sorted(ordered)) +
                    " — SQLite sorts BINARY, Postgres by the database collation, so identical "
                    "rows can legitimately come back in a different order")
        return problems

    def _text_ordering(self, sql):
        """TEXT columns used as a BARE sort key.

        Only a bare column can carry the collation hazard. `ORDER BY vec_cos_dist(embedding, ?)`
        sorts on a NUMBER the function returned — `embedding` is an argument, and flagging it
        would be the check crying wolf, which is how a check gets switched off."""
        match = _ORDER_BY.search(sql)
        if not match:
            return set()
        text_columns = self._text_column_names(refresh="create" in sql.lower())
        flagged = set()
        for term in _split_top_level(match.group(1)):
            if "(" in term:                       # an expression sorts on its RESULT, not a column
                continue
            words = [w for w in _IDENT.findall(term) if w.lower() not in _ORDER_KEYWORDS]
            if len(words) != 1:                   # not a plain `col [ASC|DESC]` term
                continue
            column = words[0].split(".")[-1].lower()
            if column in text_columns:
                flagged.add(column)
        return flagged

    def _text_column_names(self, refresh=False):
        if self._text_columns is None or refresh:
            names = set()
            try:
                tables = [r[0] for r in self._c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                for table in tables:
                    for row in self._c.execute(f"PRAGMA table_info({table})").fetchall():
                        if (row[2] or "").upper().startswith("TEXT"):
                            names.add(str(row[1]).lower())
            except sqlite3.Error:                                    # pragma: no cover
                names = set()
            self._text_columns = names
        return self._text_columns

    # -- reporting ---------------------------------------------------------------------
    def label(self):
        return self.declaration.label()

    def last_select(self):
        return [s for s in self.sql_log if s.lstrip().upper().startswith("SELECT")][-1]

    def table_names(self):
        return {r[0] for r in self._c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


# ============================================================================= the common rewrite
def placeholder_rewrite():
    """`%s` -> `?`. Every Postgres double needs it; none of them may leave it undeclared."""
    return Rewrite.literal(
        "placeholder", "%s", "?",
        "psycopg's parameter marker. SQLite reads a surviving `%s` as a string LITERAL rather "
        "than raising, so an unrewritten statement matches nothing and the test goes green.")
