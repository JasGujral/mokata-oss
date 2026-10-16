"""SPEC-ANCHOR S1→S2→S3 (0.0.23 D4) — per-AC code anchors, captured at emit, surfaced to the test
phase, warned on (never blocked) at the completeness gate.

S1 · CAPTURE. At emit, each acceptance criterion gains `anchors[]` derived from artifacts the
brainstorm ALREADY computed — no new exploration:
  (a) `approach`     — AP-SD `decisions[].about_code` of a decision the AC names (by id) or whose
                       code/statement the AC's words reach.
  (b) `blast-radius` — the approved approach's Lens-1 impact: a target or impacted file the AC's
                       words name (a test-bucket file becomes the `test-target`).
  (d) `scope`        — the spec's `scope.authorized` globs as a coarse `reference`, ONLY for an AC
                       that (a)/(b) could not place.
  Anchors the spec's author wrote (`source: spec`) are kept first and never replaced; derivation
  only adds, deduplicated, capped at `MAX_ANCHORS_PER_AC`. Idempotent: deriving twice changes nothing.
  (c) — a fresh graph query on the AC text — is deliberately NOT run in the committer: it would make
  a durable write depend on a live tool and cost exploration the row says S1 must not pay.

S2 · SURFACE. `ac_anchor_lines` renders one line per AC — `AC-1 (text) → implement `file:sym` · test
`path`` — for `spec_show` / `mokata spec show` and for develop's per-turn pack (the ONE channel: it
rides `engine/live_evidence.develop_turn_lines`'s `extra` hook, inside the same budget).

S3 · GATE + VALIDATE. `anchor_warnings` — the completeness gate WARNS (never blocks) on an anchorless
AC when a structural backend is available (a new-file AC legitimately has none), and on an anchor
that does not RESOLVE (its file is gone, or its symbol is not in the file). `stale_anchor_lines` is
the same resolve check, re-run at develop time as LIVE-EVIDENCE findings.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, List, Optional

from .spec import (ANCHOR_IMPL, ANCHOR_REFERENCE, ANCHOR_TEST_TARGET, CodeAnchor, Spec)

MAX_ANCHORS_PER_AC = 5
MAX_SCOPE_ANCHORS = 2
MAX_LINES = 3                 # per-AC lines in the per-turn pack (the spec_show surface shows all)
TEXT_CHARS = 48
MAX_RESOLVE_BYTES = 512_000

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STOP = frozenset({"the", "and", "for", "with", "that", "this", "when", "then", "must", "should",
                   "are", "not", "from", "into", "each", "every", "its", "has", "have", "via",
                   "shall", "will", "can", "all", "any", "one", "two", "new", "use", "used"})


def _words(text: str) -> set:
    out = set()
    for w in _WORD.findall(text or ""):
        lw = w.lower()
        if len(lw) >= 3 and lw not in _STOP:
            out.add(lw)
            # camelCase / snake_case parts too, so "amountMax" in code meets "amount max" in prose
            for part in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", w):
                p = part.lower()
                if len(p) >= 3 and p not in _STOP:
                    out.add(p)
    return out


def _split_about(entry: str) -> "tuple":
    e = str(entry or "").strip()
    for sep in ("::", ":"):
        if sep in e:
            head, tail = e.split(sep, 1)
            if head and not (len(head) == 1 and sep == ":"):
                return head.strip(), tail.strip()
    if "/" in e or "\\" in e or "." in os.path.basename(e):
        return e, ""
    return "", e                      # a bare symbol


def _stem(path: str) -> str:
    from ..repo_paths import split_path
    parts = split_path(path)
    return os.path.splitext(parts[-1])[0].lower() if parts else ""


def _is_test(path: str) -> bool:
    from ..brainstorm_impact import TEST, classify_path
    return classify_path(path) == TEST


def _add(anchors: List[CodeAnchor], a: CodeAnchor) -> None:
    if len(anchors) < MAX_ANCHORS_PER_AC and all(x.key != a.key for x in anchors):
        anchors.append(a)


def _from_decisions(ac: Any, words: set, decisions: Iterable[Any]) -> List[CodeAnchor]:
    out: List[CodeAnchor] = []
    text = ac.text or ""
    for d in decisions or []:
        did = str(getattr(d, "id", "") or "")
        named = bool(did) and re.search(r"(?<![A-Za-z0-9])" + re.escape(did) + r"(?![A-Za-z0-9])",
                                        text) is not None
        stmt_hit = len(words & _words(str(getattr(d, "statement", "") or ""))) >= 2
        for entry in getattr(d, "about_code", None) or []:
            path, sym = _split_about(entry)
            sym_hit = bool(sym) and sym.lower() in words
            path_hit = bool(path) and _stem(path) in words
            if not (named or stmt_hit or sym_hit or path_hit):
                continue
            if not path:
                continue          # a bare symbol has no location to anchor to (no exploration here)
            conf = "high" if (named or sym_hit) else "medium"
            out.append(CodeAnchor(file=path, symbol=sym,
                                  kind=ANCHOR_TEST_TARGET if _is_test(path) else ANCHOR_IMPL,
                                  source="approach", confidence=conf))
    return out


def _from_impact(words: set, impact: Any) -> List[CodeAnchor]:
    out: List[CodeAnchor] = []
    if impact is None:
        return out
    for t in getattr(impact, "targets", None) or []:
        path, sym = _split_about(t)
        if path and (_stem(path) in words or (sym and sym.lower() in words)):
            out.append(CodeAnchor(file=path, symbol=sym,
                                  kind=ANCHOR_TEST_TARGET if _is_test(path) else ANCHOR_IMPL,
                                  source="blast-radius", confidence="medium"))
    for f in getattr(impact, "impacted_files", None) or []:
        f = str(f or "").strip()
        stem = _stem(f)
        if not f or not stem:
            continue
        test = _is_test(f)
        bare = stem[5:] if stem.startswith("test_") else stem
        if stem in words or (test and bare in words):
            out.append(CodeAnchor(file=f, kind=ANCHOR_TEST_TARGET if test else ANCHOR_IMPL,
                                  source="blast-radius", confidence="medium"))
    return out


def derive_anchors(spec: Spec, handoff: Any = None) -> Spec:
    """S1 — fill each AC's `anchors` from the approved approach + its impact + the spec's scope.
    In place AND returned. Pure over its inputs; never raises on a malformed handoff (it adds
    nothing from what it cannot read). Byte-identical when nothing can be derived."""
    decisions = []
    impact = None
    try:
        decisions = list(getattr(getattr(handoff, "approach", None), "decisions", None) or [])
        impact = getattr(handoff, "impact", None)
    except Exception:  # noqa: BLE001
        decisions, impact = [], None
    authorized = list(getattr(spec.scope, "authorized", ()) or ()) if spec.scope else []
    for ac in spec.criteria:
        words = _words(ac.text) | _words(ac.id)
        anchors = list(ac.anchors)
        try:
            for a in _from_decisions(ac, words, decisions) + _from_impact(words, impact):
                _add(anchors, a)
        except Exception:  # noqa: BLE001 — derivation is advisory; it never fails an emit
            pass
        located = [a for a in anchors if a.kind != ANCHOR_REFERENCE]
        if not located and not any(a.source == "scope" for a in anchors):
            for glob in authorized[:MAX_SCOPE_ANCHORS]:
                _add(anchors, CodeAnchor(file=str(glob), kind=ANCHOR_REFERENCE, source="scope",
                                         confidence="low"))
        ac.anchors = anchors
    return spec


# ---------------------------------------------------------------------------------------------- S3
def _parts(path: str) -> List[str]:
    from ..repo_paths import split_path
    return [x for x in split_path(path or "") if x != "."]


def resolve(anchor: CodeAnchor, root: str) -> str:
    """'' when `anchor` resolves under `root`, else why not. A `reference` (a scope glob) is not a
    location and always resolves. The symbol check is LEXICAL (the name appears in the file) — it
    proves presence, not meaning, and says so by being called a resolve check, not a verification."""
    if anchor.kind == ANCHOR_REFERENCE or not root:
        return ""
    if any(c in anchor.file for c in "*?["):
        return ""
    path = os.path.join(root, *_parts(anchor.file))
    if not os.path.isfile(path):
        return "file not found"
    if anchor.symbol:
        name = re.split(r"[.:]", anchor.symbol)[-1]
        try:
            if os.path.getsize(path) > MAX_RESOLVE_BYTES:
                return ""
            with open(path, "rb") as fh:
                body = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return "file unreadable"
        if not re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", body):
            return f"symbol `{name}` not found in the file"
    return ""


WARN_ANCHORLESS = ("{ac} has no code anchor — the test phase will have to explore for it "
                   "(warn-only: a new-file AC legitimately has none)")
WARN_UNRESOLVED = ("{ac} anchor `{label}` does not resolve ({why}) — anchors are advisory; "
                   "re-verify against live code")


def anchor_warnings(spec: Spec, root: str = "", graph_available: bool = False) -> List[str]:
    """S3 — the completeness gate's anchor advisories. NEVER a block: the caller appends these to
    `GateResult.warnings`, which does not flip `passed`."""
    out: List[str] = []
    for ac in spec.criteria:
        located = [a for a in ac.anchors if a.kind != ANCHOR_REFERENCE]
        if graph_available and not located:
            out.append(WARN_ANCHORLESS.format(ac=ac.id))
        if root:
            for a in ac.anchors:
                why = resolve(a, root)
                if why:
                    out.append(WARN_UNRESOLVED.format(ac=ac.id, label=a.label(), why=why))
    return out


# ---------------------------------------------------------------------------------------------- S2
def ac_anchor_line(ac: Any) -> str:
    text = " ".join((ac.text or "").split())
    if len(text) > TEXT_CHARS:
        text = text[:TEXT_CHARS - 1] + "…"
    impl = [a.label() for a in ac.anchors if a.kind == ANCHOR_IMPL]
    tests = [a.label() for a in ac.anchors if a.kind == ANCHOR_TEST_TARGET]
    refs = [a.label() for a in ac.anchors if a.kind == ANCHOR_REFERENCE]
    parts = []
    if impl:
        parts.append("implement " + ", ".join(f"`{x}`" for x in impl[:2]))
    if tests:
        parts.append("test " + ", ".join(f"`{x}`" for x in tests[:2]))
    if not parts and refs:
        parts.append("within " + ", ".join(f"`{x}`" for x in refs[:2]))
    if not parts:
        return ""
    return f"{ac.id} ({text}) → " + " · ".join(parts)


def ac_anchor_lines(spec: Optional[Spec], limit: Optional[int] = None) -> List[str]:
    """S2 — one line per anchored AC (anchorless ACs are omitted: there is nothing to place)."""
    if spec is None:
        return []
    lines = [ln for ln in (ac_anchor_line(ac) for ac in spec.criteria) if ln]
    return lines if limit is None else lines[:limit]


def stale_anchor_lines(spec: Optional[Spec], root: str) -> List[str]:
    """LIVE-EVIDENCE over anchors — an anchor that no longer resolves in live code is a finding."""
    out: List[str] = []
    for ac in (spec.criteria if spec is not None else []):
        for a in ac.anchors:
            why = resolve(a, root)
            if why:
                out.append(f"- live-evidence: {ac.id} anchor `{a.label()}` is stale ({why}) — the "
                           f"code moved since emit; locate it live, never trust the anchor over it")
    return out


def develop_anchor_lines(handoff: Any, spec: Optional[Spec], layer: Any, *, root: str) -> List[str]:
    """The develop-time lines (D1's `extra` hook): stale anchors first (findings), then the per-AC
    placement lines. Bounded."""
    del handoff, layer                   # the resolve check is lexical over live files, by design
    lines = stale_anchor_lines(spec, root)[:2]
    lines += ["- anchor: " + ln for ln in ac_anchor_lines(spec, limit=MAX_LINES)]
    return lines


__all__ = ["MAX_ANCHORS_PER_AC", "WARN_ANCHORLESS", "WARN_UNRESOLVED", "derive_anchors", "resolve",
           "anchor_warnings", "ac_anchor_line", "ac_anchor_lines", "stale_anchor_lines",
           "develop_anchor_lines"]
