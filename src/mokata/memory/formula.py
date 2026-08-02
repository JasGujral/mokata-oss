"""TM.S9 — the formula item + applicability matching (doc 62 §2 axis B, §5, §8).

A FORMULA is a parameterized TEMPLATE STRING (named `{param}` slots) + APPLICABILITY metadata
(when it applies: trigger keywords and/or a subject/topic condition). It is RETRIEVED by
applicability match and INJECTED into context at recall (the template + its params) — it is NOT
computed/evaluated (computed formulae are explicitly deferred, doc 62 §8). Its precedence CLASS
is preference, never safety (precedence.precedence_class). A formula lives in the EXISTING item
model — `kind=formula` (S7), scope (S6), the `value` holds the template, and `item.applicability`
holds `{triggers, topic, params}` — so there is NO DDL and legacy items are unaffected.

This module is PURE (no I/O, no package side-effects) and duck-typed over items: it reads only
`item.kind` / `item.value` / `item.applicability`, so both `MemoryItem` and lightweight test
structs work. Matching is deterministic (token containment), never an LLM / wall-clock call.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import re
import string
from typing import Any, List, Optional, Sequence

from .item import FORMULA, MemoryItem, governance_kind

# A dependency-free word tokenizer (clean-room; matches the lexical tokenizer used elsewhere) —
# so a trigger/topic term matches the SAME tokens the rest of the memory layer scores on.
_WORD = re.compile(r"[a-z0-9]+")
_FMT = string.Formatter()


def _tokens(text: str) -> set:
    return set(_WORD.findall((text or "").lower()))


# ---------------------------------------------------------------- template params
def template_params(template: str) -> List[str]:
    """The named `{slot}` params of a template, in first-seen order, de-duplicated. Ignores
    positional / empty `{}` fields and index/attribute suffixes (`{x.y}`/`{x[0]}` → `x`).
    Deterministic; a template with no named slots yields `[]`."""
    out: List[str] = []
    try:
        parsed = list(_FMT.parse(template or ""))
    except ValueError:
        return out                      # a malformed template contributes no params
    for _lit, field_name, _spec, _conv in parsed:
        if not field_name:
            continue
        name = re.split(r"[.\[]", field_name, maxsplit=1)[0]
        if name and name not in out:
            out.append(name)
    return out


# ---------------------------------------------------------------- the TYPED ENVELOPE (DB.S5)
# The locked 2026-07-14 decision, landed: a formula's applicability is a TYPED ENVELOPE — a fixed
# set of named fields with declared shapes — rather than a free-form dict, and every read of it
# goes through `normalize_applicability` so the type is enforced at the boundary rather than
# assumed at each use site.
#
# The gap this closes was real, not theoretical. `make_formula` has always BUILT a well-shaped
# envelope, but nothing VALIDATED one that arrived any other way — from a hand-edited doc, a
# teammate's client, an older build's `extra`, or an import. A doc carrying
# `{"triggers": "auth"}` (a bare string where a list belongs) fed `any(_term_matches(t, …) for t
# in "auth")`, which iterates the CHARACTERS 'a','u','t','h' and silently matches on single
# letters — a formula that fires on almost every query, with no error anywhere to attribute it to.
# A typed envelope makes that unrepresentable at the read boundary instead of trusting the writer.
ENVELOPE_FIELDS = ("triggers", "topic", "params")


def normalize_applicability(raw: Any) -> dict:
    """Coerce ANY applicability payload into the typed envelope `{triggers: [str], topic: str,
    params: [str]}`. Total: it never raises and always returns all three fields.

    Coercion, not rejection, and deliberately so: this sits on the READ path of items a human
    already approved, so refusing a malformed envelope would make a mis-typed field silently
    delete a live formula from recall. Coercing makes it behave as its shape honestly permits.

      * a bare STRING where a list belongs becomes a ONE-ELEMENT list, never a character
        iteration — the specific bug above, fixed at its cause;
      * a non-string scalar in a list is stringified; empty/whitespace entries are dropped
        (an empty trigger is a blank wildcard, and `_term_matches` already refuses to match one);
      * a non-string `topic` is stringified, `None` becomes "";
      * anything that is not a mapping at all (a list, a string, None) yields the EMPTY envelope,
        which never matches — the correct answer for metadata we cannot read as metadata.

    Order is preserved and duplicates are dropped, so the envelope is deterministic for a given
    input and two clients reading the same doc agree on the same trigger list.
    """
    if not isinstance(raw, dict):
        return {"triggers": [], "topic": "", "params": []}
    return {"triggers": _string_list(raw.get("triggers")),
            "topic": "" if raw.get("topic") is None else str(raw.get("topic")),
            "params": _string_list(raw.get("params"))}


def _string_list(value: Any) -> List[str]:
    """A field declared as a list of strings, from whatever arrived. A bare string is ONE entry
    (never iterated character-by-character); a mapping is not a list and yields none."""
    if value is None or isinstance(value, dict):
        return []
    items = [value] if isinstance(value, (str, bytes)) else (
        value if isinstance(value, (list, tuple, set)) else [value])
    out: List[str] = []
    for entry in items:
        text = (entry.decode("utf-8", "replace") if isinstance(entry, bytes) else str(entry)).strip()
        if text and text not in out:
            out.append(text)
    return out


def applicability_of(item: Any) -> dict:
    """The typed envelope for `item` — THE one accessor. Every read of `item.applicability` in
    this module goes through here, so no use site can be handed a raw dict again."""
    return normalize_applicability(getattr(item, "applicability", None))


# ---------------------------------------------------------------- construct a formula item
def make_formula(subject: str, template: str, *,
                 triggers: Optional[Sequence[str]] = None,
                 topic: str = "",
                 params: Optional[Sequence[str]] = None,
                 **kw: Any) -> MemoryItem:
    """Build a formula `MemoryItem`: `kind=formula`, `value`=the parameterized template, and the
    trigger/topic APPLICABILITY metadata (+ named params) carried in `item.applicability`. `params`
    default to the template's own named slots. Extra kwargs (id / scope_level / scope_id / author
    / …) pass straight to `MemoryItem.create`, so a formula is scoped + gated like any other item."""
    # Built through the same normalizer every read uses, so an authored envelope and a parsed one
    # are the identical shape — the constructor cannot mint something the reader would coerce.
    applicability = normalize_applicability({
        "triggers": triggers,
        "topic": topic,
        "params": params if params is not None else template_params(template),
    })
    return MemoryItem.create(subject, template, kind=FORMULA,
                             applicability=applicability, **kw)


def is_formula(item: Any) -> bool:
    """True when `item`'s governance kind is `formula`."""
    return governance_kind(getattr(item, "kind", "")) == FORMULA


def formula_params(item: Any) -> List[str]:
    """The named params of a formula item: the explicit `applicability.params` when present, else
    derived from the template (`item.value`). `[]` for a non-formula item."""
    if not is_formula(item):
        return []
    params = applicability_of(item)["params"]
    if params:
        return params
    return template_params(getattr(item, "value", "") or "")


# ---------------------------------------------------------------- applicability match
def _term_matches(term: str, qtokens: set) -> bool:
    """A trigger/topic term matches when ALL of its tokens appear in the query (so a single-word
    trigger needs just that word; a multi-word term needs every token). An empty term never
    matches — matching is by explicit metadata, never a blank wildcard."""
    tt = _tokens(term)
    return bool(tt) and tt <= qtokens


def applies_to(item: Any, query: str, *, context: Any = None) -> bool:
    """True when this formula's APPLICABILITY matches `query` — matched by its trigger keywords
    and/or topic condition (doc 62 §2/§8), NOT by general similarity. A formula with no
    applicability metadata never matches (nothing to trigger on). `context` is accepted for
    forward-compatibility (scope filtering is applied by the store's union read, not here)."""
    if not is_formula(item):
        return False
    # Through the TYPED ENVELOPE, never the raw dict: a doc whose `triggers` is a bare string used
    # to be iterated character-by-character here, matching on single letters.
    applic = applicability_of(item)
    qtokens = _tokens(query)
    if any(_term_matches(t, qtokens) for t in applic["triggers"]):
        return True
    return _term_matches(applic["topic"], qtokens)


def recall_applicable(items: Sequence[Any], query: str, *, context: Any = None) -> List[Any]:
    """The subset of `items` that are formulae AND whose applicability matches `query`, preserving
    input order (stable). Pure — the caller supplies the already-scoped item set (e.g. the S6
    union read), so scope is honoured upstream."""
    return [it for it in items if applies_to(it, query, context=context)]


# ---------------------------------------------------------------- inject at recall (never compute)
def render_injection(item: Any) -> str:
    """The formula rendered for INJECTION into context — the template verbatim plus its named
    params. It is NOT evaluated/computed (computed formulae are deferred, doc 62 §8); the agent
    receives the template + params to apply itself."""
    params = formula_params(item)
    subject = getattr(item, "subject", "") or "formula"
    template = getattr(item, "value", "") or ""
    tail = f"  (params: {', '.join(params)})" if params else ""
    return f"formula · {subject}: {template}{tail}"
