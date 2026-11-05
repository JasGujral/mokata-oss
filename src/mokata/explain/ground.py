"""THE GROUNDING RULE `explain_save` enforces — what a cite is, and what each block must cite.

A CITE is exactly one of:
  * `fact:<id>` — a fact of THIS bundle, on a page of it Claude actually read (`pages_read`);
  * `<rel>:<a>[-<b>]` — lines of a file at the bundle's HEAD (the recorded blob for a changed
    file; `git cat-file <head_sha>:<rel>` for an unchanged one — never the moving worktree);
  * `base:<rel>:<a>[-<b>]` — the same at BASE (refused when the target has no base commit).

A path is POSIX and repo-relative: no leading `/`, no `\\`, no `.`/`..` component, no drive or
NUL, never under `.git/` or `.mokata/`, no Windows reserved name, never a secret-scan sensitive
location; a symlink (mode 120000) is never read. A line span wider than `SMALL_SPAN` (8) lines
must lie inside ONE symbol fact's lines on that tree and path — a new file included, so a whole new
file is never one cite — and is never more than `MAX_SPAN` (120): a claim about more than one
symbol names the symbol facts (`s:…`) instead.

PER BLOCK (anything that fails is DROPPED, and the sidecar records it whole, redacted):
  * prose · example     ≥1 cite (a file-row fact `f:` alone is not enough: it says nothing)
  * edge_case           ≥1 cite; `covered_by` must be a `t:` fact, else it renders UNTESTED
  * finding             ≥1 cite INSIDE the change (a hunk, a changed symbol/edge/complexity, or
                        lines that overlap a changed range) AND ≥1 OUTSIDE it (an AC, a test, a
                        convention, a pattern, a reference, or lines outside every changed range)
  * code                exactly one line cite — mokata fills the code from the blob
  * flowchart           Mermaid (allowlisted) + a cite to a `c:` complexity fact
  * use_fact_diagram    one of mokata's own diagrams (`flow`, `architecture`) — no cite needed,
                        it draws facts

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

MAX_SPAN = 120
SMALL_SPAN = 8
SECTIONS = ("summary", "walkthrough", "flow", "flowchart", "architecture", "blast_radius",
            "edge_cases", "discrepancies", "tests")
BLOCK_KINDS = ("prose", "example", "edge_case", "finding", "code", "flowchart",
               "use_fact_diagram")
FACT_DIAGRAMS = ("flow", "architecture")
INSIDE_KINDS = frozenset({"hunk", "symbol", "call_edge", "module_edge", "complexity"})
OUTSIDE_KINDS = frozenset({"ac", "test", "convention", "pattern", "refs"})
#: Windows device names: a path segment (before its first dot) with one of these names cannot be a
#: file on Windows. Public — `govern.evidence.safe_run_dirname` reuses it (G3-EVIDENCE).
RESERVED_DEVICE_NAMES = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                       *(f"lpt{i}" for i in range(1, 10))})
_LINE_CITE = re.compile(r"^(?:(base):)?([^:\0]+):(\d{1,7})(?:-(\d{1,7}))?$")


def redact(text: str) -> Tuple[str, int]:
    """(text with every secret-bearing line replaced, how many lines) — the rule
    `ide/context._redact` applies since REDACT-FIX: a private key is replaced WHOLE, BEGIN through
    END (an unterminated BEGIN to the end), by the one key pass Copy with Context uses
    (`usages.pem_lines`) — the scanner flags only a key's BEGIN line, and a body wrapped narrow is
    too short for its entropy layer. Every other finding replaces its own line. Line COUNT is
    kept, so cites into a redacted file still resolve. A key that pass cannot hold back (an END
    after a run longer than it buffers) fails closed: every line is replaced."""
    from ..govern.secrets import scan
    from ..ide import usages as U
    from ..ide.context import REDACTED
    if not text:
        return text, 0
    lines = text.split("\n")
    try:
        out = list(U.pem_lines(lines, {}, "", mark=REDACTED))
    except U.RefRefused:
        return "\n".join(REDACTED.format(kind="private-key") for _ in lines), len(lines)
    for f in scan(text):
        if f.layer in ("signature", "entropy") and f.line and 1 <= f.line <= len(out) \
                and out[f.line - 1] == lines[f.line - 1]:
            out[f.line - 1] = REDACTED.format(kind=f.kind)
    n = sum(1 for x, y in zip(lines, out) if x != y)
    return ("\n".join(out) if n else text), n


def sensitive(rel: str) -> bool:
    from ..govern.secrets import scan
    return any(f.layer == "path" for f in scan("", path=rel))


def safe_rel(rel: Any) -> Optional[str]:
    """`rel` when it is a safe repo-relative POSIX path, else None."""
    if not isinstance(rel, str) or not rel or len(rel) > 1024:
        return None
    if rel.startswith("/") or "\\" in rel or "\0" in rel or re.match(r"^[A-Za-z]:", rel):
        return None
    parts = rel.split("/")
    for p in parts:
        if p in ("", ".", ".."):
            return None
        if p.split(".")[0].lower() in RESERVED_DEVICE_NAMES or p.endswith((" ", ".")):
            return None
    if parts[0] in (".git", ".mokata"):
        return None
    if sensitive(rel):
        return None
    return rel


class Resolver:
    """Resolves cites against ONE bundle and its recorded blobs."""

    def __init__(self, bundle: Dict[str, Any], blobs: Dict[str, str], pages_read: Set[int],
                 page_of: Dict[str, int],
                 git_text: Callable[[str, str], Optional[str]]) -> None:
        self.bundle = bundle
        self.blobs = blobs
        self.facts = {f["id"]: f for f in bundle["facts"]}
        self.pages_read = pages_read
        self.page_of = page_of
        self.git_text = git_text
        t = bundle["target"]
        self.base_sha, self.head_sha = t.get("base_sha") or "", t.get("head_sha") or ""
        self.has_base = bool(self.base_sha)
        #: (tree, path) -> [(first, last)] of every symbol fact: a wide cite must sit inside one.
        self.symbols: Dict[Tuple[str, str], List[Tuple[int, int]]] = {}
        for f in bundle["facts"]:
            if f["kind"] == "symbol" and f.get("line") and f.get("end"):
                self.symbols.setdefault((f["tree"], f["path"]), []).append(
                    (int(f["line"]), int(f["end"])))
        self.changed_new: Dict[str, Set[int]] = {}
        self.changed_old: Dict[str, Set[int]] = {}
        for h in bundle["facts"]:
            if h["kind"] != "hunk":
                continue
            for op, old, new, _t in h["lines"]:
                if op == "+" and new:
                    self.changed_new.setdefault(h["path"], set()).add(new)
                if op == "-" and old:
                    self.changed_old.setdefault(h["path"], set()).add(old)
        self.shown = {f["path"]: f.get("shown", "full") for f in bundle["files"]}
        self._texts: Dict[str, Optional[List[str]]] = {}

    def _lines(self, tree: str, rel: str) -> Optional[List[str]]:
        key = f"{tree}:{rel}"
        if key in self._texts:
            return self._texts[key]
        text = self.blobs.get(key)
        if text is None and self.shown.get(rel, "full") != "full":
            text = None                       # a file not shown in full is never cited into
        elif text is None:
            sha = self.base_sha if tree == "base" else self.head_sha
            text = self.git_text(sha, rel) if sha else None
            if text is not None:
                text, _n = redact(text)
        out = text.split("\n") if isinstance(text, str) else None
        self._texts[key] = out
        return out

    def resolve(self, cite: Any) -> Tuple[Optional[Dict[str, Any]], str]:
        """`(resolved, "")` or `(None, why)`."""
        if not isinstance(cite, str) or not cite or len(cite) > 1200:
            return None, "not a cite"
        if cite.startswith("fact:"):
            fid = cite[5:]
            f = self.facts.get(fid)
            if f is None:
                return None, f"no fact {fid!r} in this bundle"
            if self.page_of.get(fid, 1) not in self.pages_read:
                return None, f"fact {fid!r} is on page {self.page_of.get(fid)}, which was not read"
            return {"cite": cite, "kind": "fact", "fact": f}, ""
        m = _LINE_CITE.match(cite)
        if not m:
            return None, "a cite is fact:<id>, <path>:<a>[-<b>] or base:<path>:<a>[-<b>]"
        tree = "base" if m.group(1) else "head"
        rel = safe_rel(m.group(2))
        if rel is None:
            return None, "unsafe or refused path"
        a = int(m.group(3))
        b = int(m.group(4) or a)
        if a < 1 or b < a:
            return None, "bad line range"
        width = b - a + 1
        if width > MAX_SPAN:
            return None, (f"span of {width} lines is wider than {MAX_SPAN} — cite the symbol "
                          "facts (s:…) instead")
        if width > SMALL_SPAN and not any(lo <= a and b <= hi for lo, hi in
                                          self.symbols.get((tree, rel), ())):
            return None, (f"span of {width} lines is wider than {SMALL_SPAN} and not inside one "
                          "symbol — cite the symbol fact (s:…) instead")
        if tree == "base" and not self.has_base:
            return None, "this target has no base commit — base: cites are refused"
        if tree == "base":
            renamed = {f["path"]: f["old_path"] for f in self.bundle["files"] if f.get("old_path")}
            rel = renamed.get(rel, rel)
        lines = self._lines(tree, rel)
        if lines is None:
            return None, f"{rel} is not readable at {tree}"
        if b > len(lines):
            return None, f"{rel} has {len(lines)} lines at {tree}"
        changed = self.changed_old.get(rel, set()) if tree == "base" else \
            self.changed_new.get(rel, set())
        inside = any(a <= n <= b for n in changed)
        return {"cite": cite, "kind": "lines", "tree": tree, "path": rel, "a": a, "b": b,
                "text": "\n".join(lines[a - 1:b]), "inside": inside}, ""


def _is_inside(r: Dict[str, Any]) -> bool:
    if r["kind"] == "lines":
        return r["inside"]
    return r["fact"]["kind"] in INSIDE_KINDS


def _is_outside(r: Dict[str, Any]) -> bool:
    if r["kind"] == "lines":
        return not r["inside"]
    return r["fact"]["kind"] in OUTSIDE_KINDS


def check_block(block: Any, res: Resolver, mermaid_ok: Callable[[str], str]
                ) -> Tuple[Optional[Dict[str, Any]], str]:
    """`(kept block with resolved cites, "")` or `(None, why it was dropped)`."""
    if not isinstance(block, dict):
        return None, "a block is an object"
    kind = block.get("kind")
    if kind not in BLOCK_KINDS:
        return None, f"block kind must be one of {list(BLOCK_KINDS)}"
    text = block.get("text", "")
    if not isinstance(text, str):
        return None, "text must be a string"
    cites = block.get("cites") or []
    if not isinstance(cites, list):
        return None, "cites must be a list"
    if kind == "use_fact_diagram":
        d = block.get("diagram")
        if d not in FACT_DIAGRAMS:
            return None, f"diagram must be one of {list(FACT_DIAGRAMS)}"
        return {"kind": kind, "diagram": d, "text": text, "cites": []}, ""
    resolved: List[Dict[str, Any]] = []
    for c in cites:
        r, why = res.resolve(c)
        if r is None:
            return None, f"cite {str(c)[:120]!r}: {why}"
        resolved.append(r)
    if not resolved:
        return None, "uncited — every claim must cite a fact or a code location"
    out = {"kind": kind, "text": text, "cites": resolved}
    if kind in ("prose", "example"):
        if all(r["kind"] == "fact" and r["fact"]["kind"] == "file" for r in resolved):
            return None, "a file row alone says nothing — cite the hunk, symbol or fact"
        if not text.strip():
            return None, "empty text"
    elif kind == "edge_case":
        if not text.strip():
            return None, "empty text"
        cov = block.get("covered_by")
        out["covered_by"] = ""
        if cov:
            r, why = res.resolve(cov if str(cov).startswith("fact:") else f"fact:{cov}")
            if r is None or r["fact"]["kind"] != "test":
                return None, f"covered_by must name a test fact (t:…): {why or 'not a test'}"
            out["covered_by"] = r["fact"]["id"]
    elif kind == "finding":
        if not text.strip():
            return None, "empty text"
        if not any(_is_inside(r) for r in resolved):
            return None, "a finding must cite the change itself (a hunk or changed lines)"
        if not any(_is_outside(r) for r in resolved):
            return None, "a finding must also cite the other side (the pattern, AC, convention " \
                         "or code outside the change)"
    elif kind == "code":
        if len(resolved) != 1 or resolved[0]["kind"] != "lines":
            return None, "a code block cites exactly one line range"
    elif kind == "flowchart":
        if not any(r["kind"] == "fact" and r["fact"]["kind"] == "complexity" for r in resolved):
            return None, "a flowchart cites the complexity fact (c:…) of the function it draws"
        src = block.get("mermaid")
        why = mermaid_ok(src) if isinstance(src, str) else "mermaid must be a string"
        if why:
            return None, f"mermaid refused: {why}"
        out["mermaid"] = src
    return out, ""


def check_doc(doc: Any, res: Resolver, mermaid_ok: Callable[[str], str]
              ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """`(sections with kept blocks, dropped)`. Unknown sections are dropped whole (recorded)."""
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for si, sec in enumerate(doc.get("sections") or []):
        sid = sec.get("id") if isinstance(sec, dict) else None
        blocks = sec.get("blocks") if isinstance(sec, dict) else None
        if sid not in SECTIONS or not isinstance(blocks, list):
            dropped.append({"section": str(sid)[:40], "block": -1, "text": "",
                            "cites": [], "reason": f"section must be one of {list(SECTIONS)}"})
            continue
        out = []
        for bi, b in enumerate(blocks):
            k, why = check_block(b, res, mermaid_ok)
            if k is None:
                t = b.get("text") if isinstance(b, dict) else ""
                c = b.get("cites") if isinstance(b, dict) else []
                dropped.append({"section": sid, "block": bi,
                                "kind": b.get("kind") if isinstance(b, dict) else "",
                                "text": redact(t if isinstance(t, str) else "")[0],
                                "cites": [str(x)[:200] for x in (c if isinstance(c, list)
                                                                 else [])][:50],
                                "reason": why})
            else:
                out.append(k)
        kept.append({"id": sid, "blocks": out})
    return kept, dropped


__all__ = ["MAX_SPAN", "SMALL_SPAN", "SECTIONS", "BLOCK_KINDS", "Resolver", "check_doc", "check_block",
           "safe_rel", "redact", "sensitive"]
