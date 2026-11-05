"""The explainer PAGE: rendered by mokata from facts + the kept (cited) blocks, with ZERO scripts.

  * Every string goes through `html.escape`; diff and code sit in `<pre>`. Claude never supplies
    HTML: it supplies text, and text is escaped.
  * Diagrams are stored as escaped Mermaid SOURCE in `<pre>` (the readable fallback with no IDE);
    the IDE renders them through P7's sandboxed mermaid-frame and shows each as an `<img>` of a
    sanitized SVG — an image never executes.
  * Cites are `<button type="button" data-cite="k">` — no `href` anywhere — resolved by the IDE
    through the sidecar's closed list of kept cites.
  * `validate(html)` is a POSITIVE allowlist (elements and attributes): no `script`, `meta`,
    `link`, `base`, `form`, `iframe`, `svg`, no `on*`, no `href`/`src`/`srcset`/`ping`, no
    `@import` or `url(` in the one `<style>`. A page that fails is refused, never "fixed".

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

from ..errors import MokataError

BANNER = ("Citations show where a statement comes from; mokata checked that they resolve, not "
          "that they prove the statement.")
INFERRED = "inferred by Claude Code"
SELF_REPORTED = "self-reported"

# ------------------------------------------------------------------------------------ mermaid

_LABEL = r"[^\[\]{}()<>&\"`%#|;\\]*"
_NODE = (r"[A-Za-z][A-Za-z0-9_]{0,40}(?:\[" + _LABEL + r"\]|\{" + _LABEL + r"\}|\(" + _LABEL
         + r"\))?")
_EDGE = r"\s*(?:-->|---|-\.->|==>)(?:\|" + _LABEL + r"\|)?\s*"
_LINE = re.compile(r"^\s*" + _NODE + r"(?:" + _EDGE + _NODE + r")*\s*$")
_HEAD = re.compile(r"^\s*(?:flowchart|graph)\s+(?:TD|TB|LR|RL|BT)\s*$")
_KEYWORDS = frozenset({"click", "style", "classdef", "class", "linkstyle", "call", "href",
                       "javascript", "init", "callback", "subgraph", "end", "direction"})


def mermaid_problem(src: Any) -> str:
    """"" when `src` is a plain flowchart of nodes and edges (the allowlist); else why not.
    Refused by construction: directives (`%%`), front matter, `click`/`style`/`class*`, links,
    HTML or entity labels, quotes, backticks."""
    if not isinstance(src, str) or not src.strip():
        return "empty"
    lines = [ln for ln in src.replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
    if not _HEAD.match(lines[0]):
        return "the first line must be `flowchart TD|LR|…`"
    for n, ln in enumerate(lines[1:], 2):
        if not _LINE.match(ln):
            return f"line {n} is not a node/edge statement"
        for ident in re.findall(r"(?:^|-->|---|-\.->|==>|\|)\s*([A-Za-z][A-Za-z0-9_]*)", ln):
            if ident.lower() in _KEYWORDS:
                return f"line {n} uses the reserved word {ident!r}"
    return ""


def _label(s: Any) -> str:
    """A label safe inside `[...]` / `|...|` — every character the allowlist refuses becomes a
    space (a symbol or path cannot inject Mermaid syntax into mokata's own diagrams)."""
    out = re.sub(r"[\[\]{}()<>&\"`%#|;\\]", " ", str(s))
    return re.sub(r"\s+", " ", out).strip()[:80] or "?"


def fact_diagram(kind: str, facts: List[Dict[str, Any]],
                 defined: Optional[List[str]] = None) -> Optional[str]:
    """mokata's own diagram from facts (generated node ids, encoded labels), or None when the
    change has nothing to draw. `flow`: the call path before→after (call edges added / removed,
    to callees the changed files define — a builtin or a method name is a fact, not a path step);
    `architecture`: module edges added / removed."""
    ids: Dict[str, str] = {}

    def node(label: str) -> str:
        if label not in ids:
            ids[label] = f"n{len(ids)}"
        return f"{ids[label]}[{_label(label)}]"
    lines = ["flowchart LR"]
    if kind == "flow":
        for f in facts:
            if f["kind"] == "call_edge" and (defined is None or f["callee"] in defined):
                arrow = "-->|added|" if f["change"] == "added" else "-.->|removed|"
                lines.append(f"{node(f['caller'])} {arrow} {node(f['callee'])}")
    elif kind == "architecture":
        for f in facts:
            if f["kind"] == "module_edge":
                arrow = "-->|added|" if f["change"] == "added" else "-.->|removed|"
                lines.append(f"{node(f['path'])} {arrow} {node(f['module'])}")
    if len(lines) == 1:
        return None
    src = "\n".join(lines)
    assert mermaid_problem(src) == "", mermaid_problem(src)
    return src


# ------------------------------------------------------------------------------------- render

CSS = """
:root{color-scheme:light dark;--fg:#1a1d21;--bg:#ffffff;--muted:#5b6470;--line:#d9dee4;
--add:#e6f6ec;--del:#fdecec;--inf:#f4f6fb;--accent:#22c55e;--warn:#9a5b00}
@media (prefers-color-scheme:dark){:root{--fg:#e6e9ee;--bg:#14171a;--muted:#9aa3ad;
--line:#2c3238;--add:#16301f;--del:#3a1d1d;--inf:#1b2026;--accent:#3df58c;--warn:#f0b35a}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);
font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;max-width:1100px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 8px;border-bottom:1px solid
var(--line);padding-bottom:4px}h3{font-size:14px;margin:12px 0 4px}
.meta,.small{color:var(--muted);font-size:12px}.banner{border:1px solid var(--warn);border-radius:6px;padding:8px 12px;margin:12px 0}
.inf{background:var(--inf);border:1px solid var(--line);border-radius:6px;padding:8px 12px;
margin:8px 0}
.lab{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:0}
pre{overflow:auto;border:1px solid var(--line);padding:8px;margin:4px 0;font:12px/1.45
ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre}
.add{background:var(--add);display:block}.del{background:var(--del);display:block}
.ctx{display:block}table{border-collapse:collapse;width:100%;margin:4px 0}
th,td{border:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top;
font-size:12px}button.cite{font:12px ui-monospace,Menlo,Consolas,monospace;
background:none;border:1px solid var(--line);border-radius:4px;padding:1px 6px;color:inherit;
cursor:pointer}button.cite:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
td{font-variant-numeric:tabular-nums}::selection{background:var(--add)}.untested{color:var(--warn);font-weight:600}.fact{border:1px dashed
var(--line);border-radius:6px;padding:4px 12px;margin:8px 0}figure{margin:8px 0}figcaption{font-size:12px;
color:var(--muted)}ul.cites{list-style:none;padding:0;margin:6px 0 0}
"""


def _e(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


class _Page:
    def __init__(self) -> None:
        self.parts: List[str] = []
        self.cites: List[Dict[str, Any]] = []
        self.diagrams: List[Dict[str, Any]] = []
        self.drawn: set = set()            # fact diagrams already on the page (drawn once)

    def w(self, s: str) -> None:
        self.parts.append(s)

    def cite_button(self, r: Dict[str, Any]) -> str:
        k = len(self.cites)
        if r["kind"] == "fact":
            f = r["fact"]
            # `tree`: a fact that lives only at base (a removed symbol, a dropped call edge) is a
            # BASE location — the viewer must open it at the base commit, never at head (P46 r2).
            entry = {"k": k, "cite": r["cite"], "fact": f["id"], "path": f.get("path") or "",
                     "tree": "base" if f.get("tree") == "base" else "head",
                     "line": f.get("line") or (f.get("new") or [None])[0] or 0}
            label = f["id"]
        else:
            entry = {"k": k, "cite": r["cite"], "tree": r["tree"], "path": r["path"],
                     "line": r["a"], "end": r["b"]}
            label = r["cite"]
        self.cites.append(entry)
        return f'<button type="button" class="cite" data-cite="{k}">{_e(label)}</button>'

    def diagram(self, caption: str, src: str, by: str) -> None:
        k = len(self.diagrams)
        self.diagrams.append({"k": k, "caption": caption, "by": by})
        self.w(f'<figure class="diagram" data-diagram="{k}"><figcaption>{_e(caption)} · '
               f'{_e(by)}</figcaption><pre class="mermaid-src">{_e(src)}</pre></figure>')


def _hunk(p: _Page, h: Dict[str, Any]) -> None:
    rows = []
    for op, old, new, text in h["lines"]:
        cls = {"+": "add", "-": "del"}.get(op, "ctx")
        no = new if op != "-" else old
        rows.append(f'<span class="{cls}">{_e(op)}{_e(str(no or "").rjust(5))} {_e(text)}</span>')
    p.w('<pre class="diff">' + "".join(rows) + "</pre>")


def _block(p: _Page, b: Dict[str, Any], facts: List[Dict[str, Any]],
           defined: Optional[List[str]] = None) -> None:
    if b["kind"] == "use_fact_diagram":
        if b["diagram"] in p.drawn:      # the section already drew it: never twice on one page
            return
        p.drawn.add(b["diagram"])
        src = fact_diagram(b["diagram"], facts, defined)
        if src:
            p.diagram(f"{b['diagram']} (drawn by mokata from the facts)", src, "fact")
        return
    p.w('<div class="inf">')
    label = {"prose": INFERRED, "example": f"worked example · {INFERRED}",
             "edge_case": f"edge case · {INFERRED}", "finding": f"discrepancy · {INFERRED}",
             "code": "code (filled by mokata from the cited lines)",
             "flowchart": f"flowchart · {INFERRED}"}[b["kind"]]
    p.w(f'<p class="lab">{_e(label)}</p>')
    if b.get("text"):
        p.w(f"<p>{_e(b['text'])}</p>")
    if b["kind"] == "edge_case":
        if b.get("covered_by"):
            p.w(f'<p class="small">covered by {_e(b["covered_by"])}</p>')
        else:
            p.w('<p class="untested">UNTESTED — no test fact covers this case</p>')
    if b["kind"] == "flowchart":
        p.diagram("decision flow", b["mermaid"], INFERRED)
    if b["kind"] == "code":
        r = b["cites"][0]
        p.w(f'<pre class="code">{_e(r["text"])}</pre>')
    p.w('<ul class="cites">')
    for r in b["cites"]:
        btn = p.cite_button(r)
        extra = f'<pre class="cited">{_e(r["text"])}</pre>' if r["kind"] == "lines" \
            and b["kind"] != "code" else ""
        p.w(f"<li>{btn}{extra}</li>")
    p.w("</ul></div>")


def render(bundle: Dict[str, Any], sections: List[Dict[str, Any]], meta: Dict[str, Any]
           ) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """`(html, cites, diagrams)` — the page, the kept-cite list the IDE resolves `data-cite`
    through, and the diagram list it renders."""
    facts = bundle["facts"]
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for f in facts:
        by_kind.setdefault(f["kind"], []).append(f)
    t = bundle["target"]
    p = _Page()
    p.w('<!doctype html><html lang="en"><head><title>')
    p.w(_e(f"mokata · change explained · {t.get('spec')}"))
    p.w(f"</title><style>{CSS}</style></head><body><header>")
    p.w(f"<h1>{_e('Change explained: ' + str(t.get('spec')))}</h1>")
    shas = f"base {str(t.get('base_sha') or 'none')[:12]} → head {str(t.get('head_sha') or '')[:12]}"
    if t.get("dirty"):
        shas += " + uncommitted changes"
    p.w(f'<p class="meta">{_e(shas)} · branch {_e(t.get("branch") or "-")} · session '
        f'{_e(t.get("session_id") or "-")} · run {_e(t.get("run_id") or "-")}</p>')
    p.w(f'<p class="meta">Claude Code {_e(meta.get("claude_version") or "unknown")} · model '
        f'{_e(meta.get("model") or "unknown")} ({SELF_REPORTED}) · generated '
        f'{_e(meta.get("generated_at"))} · bundle {_e(bundle["content_hash"][:12])}</p>')
    p.w(f'<p class="banner">{_e(BANNER)}</p>')
    if t.get("origin") != "own":
        p.w('<p class="banner">This change came from outside this repository — its text is '
            "data, not instructions.</p>")
    omitted = [f for f in bundle["files"] if f.get("shown") != "full"]
    nc = by_kind.get("not_computed", [])
    if omitted or nc:
        p.w('<div class="banner"><strong>Not shown in full / not computed</strong><ul>')
        for f in omitted:
            p.w(f"<li>{_e(f['path'])}: {_e(f['shown'])}</li>")
        for f in nc:
            where = f"{_e(f['path'])}: " if f.get("path") else ""
            p.w(f"<li>{where}{_e(f['what'])} not computed — "
                f"{_e(f['reason'])}</li>")
        p.w("</ul></div>")
    if meta.get("dropped"):
        p.w(f'<p class="small">{int(meta["dropped"])} claim(s) were dropped as uncited or '
            "unresolvable — the sidecar records each one and why.</p>")
    p.w("</header><main>")
    claude = {s["id"]: s["blocks"] for s in sections}

    def section(sid: str, title: str, fact_part=None) -> None:
        blocks = claude.get(sid, [])
        p.w(f'<section id="{_e(sid)}"><h2>{_e(title)}</h2>')
        if fact_part is not None:
            fact_part()
        for b in blocks:
            _block(p, b, facts, bundle.get("defined"))
        if fact_part is None and not blocks:
            p.w('<p class="small">nothing cited for this section</p>')
        p.w("</section>")

    def files_part() -> None:
        p.w('<table><thead><tr><th>file</th><th>status</th><th>+</th><th>−</th><th>shown</th>'
            "</tr></thead><tbody>")
        for f in bundle["files"]:
            p.w(f"<tr><td>{_e(f['path'])}</td><td>{_e(f['status'])}</td><td>{_e(f['added'])}"
                f"</td><td>{_e(f['removed'])}</td><td>{_e(f['shown'])}</td></tr>")
        p.w("</tbody></table>")

    def walk_part() -> None:
        for h in by_kind.get("hunk", []):
            p.w(f'<h3>{_e(h["id"])}</h3>')
            _hunk(p, h)

    def flow_part() -> None:
        if "flow" in p.drawn:
            return
        p.drawn.add("flow")
        src = fact_diagram("flow", facts, bundle.get("defined"))
        if src:
            p.diagram("call path, before → after (drawn by mokata from the facts)", src, "fact")
        else:
            p.w('<p class="small">no call edge was added or removed</p>')

    def cx_part() -> None:
        rows = by_kind.get("complexity", [])
        if not rows:
            p.w('<p class="small">no changed function has a complexity basis</p>')
            return
        p.w("<table><thead><tr><th>function</th><th>before</th><th>after</th><th>delta</th>"
            "</tr></thead><tbody>")
        for c in rows:
            p.w(f"<tr><td>{_e(c['path'])}::{_e(c['name'])}</td><td>{_e(c['before'])}</td>"
                f"<td>{_e(c['after'])}</td><td>{_e(c['delta'])}</td></tr>")
        p.w("</tbody></table>")

    def arch_part() -> None:
        if "architecture" in p.drawn:
            return
        p.drawn.add("architecture")
        src = fact_diagram("architecture", facts)
        if src:
            p.diagram("module edges added / removed (drawn by mokata from the facts)", src,
                      "fact")
        else:
            p.w('<p class="small">no module edge was added or removed</p>')

    def blast_part() -> None:
        refs = by_kind.get("refs", [])
        if not refs:
            p.w('<p class="small">no changed or removed name to look up</p>')
            return
        p.w(f'<p class="small">{_e(bundle.get("refs_basis"))}</p>')
        for r in refs:
            tag = " (REMOVED — surviving references)" if r.get("removed") else ""
            p.w(f"<h3>{_e(r['name'])} at {_e(r['tree'])}{_e(tag)} · {_e(r['count'])} "
                "reference(s)</h3>")
            if r["refs"]:
                p.w("<table><tbody>")
                for rel, ln, enc in r["refs"]:
                    p.w(f"<tr><td>{_e(rel)}:{_e(ln)}</td><td>{_e(enc)}</td></tr>")
                p.w("</tbody></table>")

    def tests_part() -> None:
        acs, tests = by_kind.get("ac", []), by_kind.get("test", [])
        if not acs and not tests:
            p.w('<p class="small">no acceptance criteria or tests are recorded for this '
                "change</p>")
            return
        if acs:
            p.w("<table><thead><tr><th>AC</th><th>text</th><th>status</th><th>tests</th></tr>"
                "</thead><tbody>")
            for a in acs:
                p.w(f"<tr><td>{_e(a['ac'])}</td><td>{_e(a['text'])}</td><td>{_e(a['status'])}"
                    f"</td><td>{_e(', '.join(a['tests']) or 'none')}</td></tr>")
            p.w("</tbody></table>")
        if tests:
            p.w("<table><thead><tr><th>test</th><th>TDD</th><th>last</th></tr></thead><tbody>")
            for x in tests:
                p.w(f"<tr><td>{_e(x['test'])}</td><td>{_e(x['tdd'])}</td><td>{_e(x['last'])}"
                    "</td></tr>")
            p.w("</tbody></table>")

    def disc_part() -> None:
        for f in by_kind.get("pattern", []):
            p.w(f'<div class="fact"><p class="lab">pattern observation (mokata)</p>'
                f"<p>{_e(f['what'])}: {_e(f['path'])}::{_e(f['symbol'])}</p>"
                f'<p class="small">this change: {_e("; ".join(f"{a}:{b} {c}" for a, b, c in f["inside"]))}'
                f'<br>elsewhere: {_e("; ".join(f"{a}:{b} {c}" for a, b, c in f["outside"]))}</p>'
                "</div>")

    section("summary", "Summary", files_part)
    section("walkthrough", "Per-change walkthrough", walk_part)
    section("flow", "Flow diagram", flow_part)
    section("flowchart", "Flowchart (complexity)", cx_part)
    section("architecture", "Architecture view", arch_part)
    section("blast_radius", "Blast radius", blast_part)
    section("edge_cases", "Edge cases")
    section("discrepancies", "Discrepancies", disc_part)
    section("tests", "Tests", tests_part)
    notes = bundle.get("notes") or []
    if notes:
        p.w('<section id="notes"><h2>Notes</h2><ul>')
        for n in notes:
            p.w(f"<li>{_e(n)}</li>")
        p.w("</ul></section>")
    p.w("</main></body></html>")
    return "".join(p.parts), p.cites, p.diagrams


# ----------------------------------------------------------------------------------- validator

ALLOWED: Dict[str, Tuple[str, ...]] = {
    "html": ("lang",), "head": (), "title": (), "style": (), "body": (), "header": (),
    "main": (), "section": ("id",), "h1": (), "h2": (), "h3": (), "p": ("class",),
    "ul": ("class",), "li": (), "table": (), "thead": (), "tbody": (), "tr": (), "th": (),
    "td": ("colspan",), "pre": ("class",), "span": ("class",), "div": ("class",),
    "figure": ("class", "data-diagram"), "figcaption": (), "strong": (), "em": (), "br": (),
    "button": ("type", "class", "data-cite"),
}
_VOID = frozenset({"br"})


class PageRefused(MokataError, ValueError):
    """The rendered page failed the positive allowlist — refused, never repaired."""


class _V(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: List[str] = []
        self.stack: List[str] = []
        self.styles = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag not in ALLOWED:
            self.problems.append(f"element <{tag}> is not allowed")
            return
        for k, v in attrs:
            if k not in ALLOWED[tag]:
                self.problems.append(f"attribute {k} on <{tag}> is not allowed")
            elif k == "type" and v != "button":
                self.problems.append("a button is type=button only")
            elif k in ("data-cite", "data-diagram", "colspan") and not re.match(r"^\d{1,6}$",
                                                                                v or ""):
                self.problems.append(f"{k} must be a number")
        if tag == "style":
            self.styles += 1
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1] == "style":
            low = data.lower()
            for bad in ("@import", "url(", "expression(", "behavior", "-moz-binding", "\\"):
                if bad in low:
                    self.problems.append(f"style contains {bad!r}")

    def handle_comment(self, data: str) -> None:
        self.problems.append("comments are not allowed")

    def handle_decl(self, decl: str) -> None:
        if decl.lower() != "doctype html":
            self.problems.append(f"declaration {decl!r} is not allowed")

    def handle_pi(self, data: str) -> None:
        self.problems.append("processing instructions are not allowed")

    def unknown_decl(self, data: str) -> None:
        self.problems.append("CDATA / unknown declarations are not allowed")


def validate(page: str) -> List[str]:
    """Every problem with `page` against the positive allowlist ([] = valid)."""
    v = _V()
    v.feed(page)
    v.close()
    if v.styles > 1:
        v.problems.append("one <style> element only")
    if "<script" in page.lower():
        v.problems.append("a <script> tag is present")
    return v.problems


__all__ = ["BANNER", "render", "validate", "mermaid_problem", "fact_diagram", "PageRefused",
           "ALLOWED"]
