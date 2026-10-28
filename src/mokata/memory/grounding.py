"""R5 (0.0.26) — memory WRITE GROUNDING and `retire_when` expiry predicates.

A learning that cites code is only as good as its citations. Before this stage a memory could name
`src/a.py:120`, `commit 3f2a…` or a symbol that never existed and land anyway — GR.S4 attached a
proposal-level WARNING for a mis-named `about_code` symbol and nothing else, so the store slowly
filled with confident pointers into nowhere. This module is the deterministic check that replaces
that warning with a REFUSAL:

  * every cited PATH exists in the repo (and a line anchor `:N` / `#LN` is within the file);
  * every cited COMMIT resolves (`git cat-file --batch-check`, ONE process for all of them);
  * every cited SYMBOL resolves through the code graph WHEN an authoritative one is adopted — with
    none, the symbol is accepted and the basis says, in words, that it was NOT verified;
  * every LINK is well-formed. No network, ever: mokata does not go online by itself, so a link is
    checked for shape (scheme, host, port), never fetched.

An ungrounded write is refused and the refusal NAMES the unresolved citation. Grounding is
O(citations) — a stat, a bounded line count, one batched git call — and never walks the repo.

WHAT COUNTS AS A CITATION, and why it is conservative. `about_code` anchors and `retire_when`
predicates are explicit, so all of them are checked. In the free-text VALUE only unambiguous forms
are: a URL; a relative path that contains a `/` AND either ends in a known source suffix or carries
a line anchor; a hex id introduced by `commit` / `sha` / `rev`. A bare filename (`store.py`) is not
a citation — resolving it would need the repo walk this module refuses to do — and an absolute or
`~/` path is not a REPO citation at all. Missing a citation leaves a write as it was before R5;
inventing one would refuse a perfectly good learning, so the extractor errs toward missing.

EPISODIC turns are never grounded: a conversation turn is a record of what was SAID, and refusing
to log a turn because the user mistyped a path would lose the record without fixing anything.

`retire_when` — each learning may carry expiry predicates:

    deleted:<path>     fires when the file is gone
    removed:<symbol>   fires when the authoritative graph says the symbol no longer resolves
    after:<commit>     fires once <commit> is an ancestor of HEAD
    date:<YYYY-MM-DD>  fires on/after that date (an ISO instant is accepted too)

`evaluate_retire_when` is PURE and read-only: it returns which predicates fired. The store turns
each into a `retire_due` healing proposal, and a human resolves it with one of the refresh verbs
(keep / update / consolidate / replace / delete) through the same WriteGate every memory write
uses. Nothing is ever retired automatically.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

# --------------------------------------------------------------------------- vocabulary
PATH = "path"
SHA = "sha"
SYMBOL = "symbol"
LINK = "link"
DATE = "date"

# Where a citation came from — rendered in the refusal so the human knows which field to fix.
SRC_ABOUT_CODE = "about_code"
SRC_VALUE = "value"
SRC_RETIRE_WHEN = "retire_when"

# The `retire_when` predicate grammar: `<op>:<argument>`.
RETIRE_DELETED = "deleted"
RETIRE_REMOVED = "removed"
RETIRE_AFTER = "after"
RETIRE_DATE = "date"
RETIRE_OPS = (RETIRE_DELETED, RETIRE_REMOVED, RETIRE_AFTER, RETIRE_DATE)

BASIS_NO_GRAPH = ("NOT verified — no authoritative code graph is adopted, so the symbol was "
                  "accepted on the writer's word")

_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>\"'`]*")
_PATH_RE = re.compile(
    r"(?<![\w./~\\:-])"
    r"((?:\.\.?/)?[\w.-]+(?:/[\w.-]+)+)"
    r"(?::(\d+)(?:-(\d+))?|#L(\d+)(?:-L?(\d+))?)?"
    r"(?![\w/])")
_SHA_RE = re.compile(r"\b(?:commit|sha|rev)\b\s*[:#=]?\s*([0-9a-fA-F]{7,40})\b", re.IGNORECASE)
_HEX_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                      r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
_TRAILING = ".,;:!?)]}>'\""


@dataclass(frozen=True)
class Citation:
    kind: str                 # PATH | SHA | SYMBOL | LINK | DATE
    raw: str                  # exactly what the writer typed (rendered back verbatim)
    target: str               # the path / sha / symbol / url / date to resolve
    source: str               # SRC_ABOUT_CODE | SRC_VALUE | SRC_RETIRE_WHEN
    line: int = 0             # a line anchor's first line (0 = none)
    line_end: int = 0


@dataclass(frozen=True)
class CitationVerdict:
    citation: Citation
    grounded: bool
    basis: str                # how it was checked — always said, grounded or not


@dataclass
class GroundingReport:
    verdicts: List[CitationVerdict] = field(default_factory=list)
    checked: bool = True      # False when there was no repo to ground against
    reason: str = ""          # why `checked` is False

    @property
    def unresolved(self) -> List[CitationVerdict]:
        return [v for v in self.verdicts if not v.grounded]

    @property
    def ok(self) -> bool:
        return not self.unresolved

    def refusal(self) -> Optional[str]:
        """The refusal message naming EVERY unresolved citation, or None when grounded."""
        bad = self.unresolved
        if not bad:
            return None
        named = "; ".join(f"{v.citation.raw!r} in {v.citation.source} ({v.basis})" for v in bad)
        return (f"refused: ungrounded memory write — {len(bad)} citation(s) do not resolve: "
                f"{named}. Nothing was written. Fix or drop the citation and propose again.")

    def lines(self) -> List[str]:
        """The gate-prompt disclosure: one line per citation with its basis."""
        if not self.checked:
            return [f"  grounding: NOT checked — {self.reason}"] if self.reason else []
        out = []
        for v in self.verdicts:
            mark = "ok" if v.grounded else "UNRESOLVED"
            out.append(f"  cites {v.citation.raw} [{v.citation.kind}] — {mark}: {v.basis}")
        return out


# --------------------------------------------------------------------------- extraction
def _strip_trailing(token: str) -> str:
    while token and token[-1] in _TRAILING:
        token = token[:-1]
    return token


def extract_value_citations(text: str) -> List[Citation]:
    """The unambiguous citations in free text (URLs, anchored/suffixed relative paths, keyword-
    introduced commit ids). Deterministic, order-preserving, de-duplicated.

    The suffix list is H-6's (`anchor_fingerprints._PATH_SUFFIXES`), imported late (memory is the
    lower layer) so "what looks like a source path" has one definition in the package."""
    from ..knowledge.anchor_fingerprints import _PATH_SUFFIXES
    out: List[Citation] = []
    seen = set()
    text = text or ""
    spans: List[Tuple[int, int]] = []
    for m in _URL_RE.finditer(text):
        url = _strip_trailing(m.group(0))
        spans.append((m.start(), m.start() + len(url)))
        key = (LINK, url)
        if url and key not in seen:
            seen.add(key)
            out.append(Citation(LINK, url, url, SRC_VALUE))
    # Blank the URLs out so their path-shaped tails are not read twice as repo paths.
    masked = list(text)
    for a, b in spans:
        for i in range(a, b):
            masked[i] = " "
    masked_text = "".join(masked)
    for m in _PATH_RE.finditer(masked_text):
        path = m.group(1)
        line = m.group(2) or m.group(4) or ""
        end = m.group(3) or m.group(5) or ""
        trimmed = _strip_trailing(path)
        if not line and not trimmed.endswith(_PATH_SUFFIXES):
            continue
        raw = _strip_trailing(m.group(0)) if not line else m.group(0)
        key = (PATH, trimmed, line, end)
        if key in seen:
            continue
        seen.add(key)
        out.append(Citation(PATH, raw, trimmed, SRC_VALUE, int(line or 0), int(end or 0)))
    for m in _SHA_RE.finditer(masked_text):
        sha = m.group(1).lower()
        key = (SHA, sha)
        if key not in seen:
            seen.add(key)
            out.append(Citation(SHA, m.group(0), sha, SRC_VALUE))
    return out


def parse_retire_predicate(pred: str) -> Tuple[str, str]:
    """`deleted:src/a.py` → ("deleted", "src/a.py"). Raises ValueError on an unknown op or an empty
    argument — a predicate nobody can evaluate is refused at write time, not ignored at read time."""
    text = (pred or "").strip()
    op, sep, arg = text.partition(":")
    op, arg = op.strip().lower(), arg.strip()
    if not sep or op not in RETIRE_OPS or not arg:
        raise ValueError(f"retire_when {text!r} is not one of "
                         f"{', '.join(o + ':<arg>' for o in RETIRE_OPS)}")
    return op, arg


def _parse_date(arg: str) -> Optional[datetime]:
    try:
        if len(arg) == 10:
            return datetime.strptime(arg, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(arg)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _anchor_citation(anchor: str, root: str) -> Citation:
    from ..knowledge.anchor_fingerprints import (_PATH_SUFFIXES, SHAPE_PATH, classify_anchor,
                                                 split_line_ref)
    text = (anchor or "").strip()
    if root and classify_anchor(text, root) == SHAPE_PATH:
        head, sep, tail = text.rpartition(":")
        line = int(tail) if sep and head and tail.isdigit() else 0
        return Citation(PATH, text, split_line_ref(text), SRC_ABOUT_CODE, line)
    if not root and ("/" in text or text.endswith(_PATH_SUFFIXES)):
        return Citation(PATH, text, split_line_ref(text), SRC_ABOUT_CODE)
    return Citation(SYMBOL, text, text, SRC_ABOUT_CODE)


def citations_of(item: Any, root: str = "") -> List[Citation]:
    """Every citation a memory item carries: its `about_code` anchors, its `retire_when`
    predicates (as the citation each one names), and the unambiguous citations in its value."""
    out: List[Citation] = []
    for a in getattr(item, "about_code", None) or []:
        if str(a).strip():
            out.append(_anchor_citation(str(a), root))
    for pred in getattr(item, "retire_when", None) or []:
        try:
            op, arg = parse_retire_predicate(pred)
        except ValueError:
            out.append(Citation(DATE, str(pred), "", SRC_RETIRE_WHEN))   # unparseable → refused
            continue
        kind = {RETIRE_DELETED: PATH, RETIRE_REMOVED: SYMBOL,
                RETIRE_AFTER: SHA, RETIRE_DATE: DATE}[op]
        out.append(Citation(kind, str(pred), arg.lower() if kind == SHA else arg,
                            SRC_RETIRE_WHEN))
    # Only TEXT is scanned for citations: a structured (dict/list) value is data, not prose.
    texts = [t for t in (getattr(item, "subject", ""), getattr(item, "value", ""))
             if isinstance(t, str)]
    out.extend(extract_value_citations("\n".join(texts)))
    return out


# --------------------------------------------------------------------------- resolution
def _inside(root: str, rel: str) -> Optional[str]:
    """The absolute path for `rel` if it stays inside `root`, else None."""
    try:
        base = os.path.realpath(root)
        ab = os.path.realpath(os.path.join(base, rel))
    except (OSError, ValueError):
        return None
    if ab != base and not ab.startswith(base + os.sep):
        return None
    return ab


def _count_lines_upto(path: str, need: int) -> int:
    """Lines in `path`, stopping as soon as `need` is reached (bounded — never reads past it)."""
    n = 0
    last = b""
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            n += chunk.count(b"\n")
            last = chunk
            if n >= need:
                return n
    if last and not last.endswith(b"\n"):
        n += 1
    return n


def _check_path(c: Citation, root: str) -> CitationVerdict:
    ab = _inside(root, c.target)
    if ab is None:
        return CitationVerdict(c, False, "path escapes the repository")
    try:
        if os.path.isdir(ab):
            if c.line:
                return CitationVerdict(c, False, "a line anchor on a directory")
            return CitationVerdict(c, True, "directory exists in the repo")
        if not os.path.isfile(ab):
            return CitationVerdict(c, False, "no such file in the repo")
        if c.line:
            want = max(c.line, c.line_end)
            have = _count_lines_upto(ab, want)
            if have < want:
                return CitationVerdict(c, False, f"file has {have} line(s); line {want} cited")
            return CitationVerdict(c, True, f"file exists; line {want} is within it")
    except (OSError, ValueError) as exc:
        return CitationVerdict(c, False, f"file could not be read ({type(exc).__name__})")
    return CitationVerdict(c, True, "file exists in the repo")


def resolve_shas(shas: Sequence[str], root: str) -> Dict[str, str]:
    """{sha: ""} for each commit that resolves, {sha: reason} for each that does not — ONE
    `git cat-file --batch-check` process for all of them (O(1) spawns, never a history walk)."""
    shas = list(dict.fromkeys(s for s in shas if s))
    if not shas:
        return {}
    bad = {s: "not a commit id" for s in shas if not _HEX_RE.match(s)}
    good = [s for s in shas if s not in bad]
    if not good:
        return bad
    from ..quiet_spawn import hidden
    try:
        proc = subprocess.run(
            ["git", "-C", root, "cat-file", "--batch-check"],
            input="".join(f"{s}^{{commit}}\n" for s in good), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30, **hidden())
    except (OSError, subprocess.SubprocessError) as exc:
        return {**bad, **{s: f"git could not be run ({type(exc).__name__})" for s in good}}
    if proc.returncode != 0:
        why = "not a git repository" if "not a git repository" in (proc.stderr or "") \
            else f"git cat-file failed ({(proc.stderr or '').strip()[:80]})"
        return {**bad, **{s: why for s in good}}
    lines = (proc.stdout or "").splitlines()
    out = dict(bad)
    for s, line in zip(good, lines):
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "commit":
            out[s] = ""
        elif line.endswith("ambiguous"):
            out[s] = "ambiguous short id"
        else:
            out[s] = "no such commit in this repository"
    for s in good[len(lines):]:
        out[s] = "git gave no answer"
    return out


def _resolver(layer: Any) -> Tuple[Any, str]:
    """(resolves-callable, graph name) when an AUTHORITATIVE graph is adopted, else (None, "")."""
    primary = getattr(layer, "primary", None) if layer is not None else None
    if primary is None or not getattr(primary, "supports_resolve", False):
        return None, ""
    fn = getattr(primary, "resolves", None)
    if not callable(fn):
        return None, ""
    return fn, str(getattr(primary, "name", "") or "code graph")


def _check_symbol(c: Citation, layer: Any) -> CitationVerdict:
    fn, name = _resolver(layer)
    if fn is None:
        return CitationVerdict(c, True, BASIS_NO_GRAPH)
    try:
        found = bool(fn(c.target))
    except Exception as exc:  # noqa: BLE001 — a graph client is a third-party process
        return CitationVerdict(c, True, f"NOT verified — the {name} could not be asked "
                                        f"({type(exc).__name__})")
    if found:
        return CitationVerdict(c, True, f"resolves in the {name}")
    return CitationVerdict(c, False, f"the {name} has no such symbol")


def _check_link(c: Citation) -> CitationVerdict:
    try:
        parts = urlsplit(c.target)
        port = parts.port               # raises ValueError on a malformed port
    except ValueError:
        return CitationVerdict(c, False, "malformed link")
    del port
    if parts.scheme.lower() not in ("http", "https"):
        return CitationVerdict(c, False, f"unsupported link scheme {parts.scheme!r}")
    host = parts.hostname or ""
    if not host or not _HOST_RE.match(host) or ("." not in host and host != "localhost"):
        return CitationVerdict(c, False, "link has no valid host")
    return CitationVerdict(c, True, "well-formed link (not fetched — mokata never goes online)")


def _check_date(c: Citation) -> CitationVerdict:
    if not c.target:
        return CitationVerdict(c, False, "unparseable retire_when predicate (expected one of "
                                         + ", ".join(o + ":<arg>" for o in RETIRE_OPS) + ")")
    if _parse_date(c.target) is None:
        return CitationVerdict(c, False, "not a date (YYYY-MM-DD or ISO-8601)")
    return CitationVerdict(c, True, "valid date")


def check_grounding(item: Any, root: str, layer: Any = None) -> GroundingReport:
    """Ground every citation `item` carries. Read-only; O(citations); never raises.

    `root` empty → nothing can be resolved against a repo, and the report says so (`checked`
    False) rather than pretending: a directly-constructed store with no surface has no repo."""
    from .item import EPISODIC
    if getattr(item, "mtype", "") == EPISODIC:
        return GroundingReport(checked=False, reason="")
    cites = citations_of(item, root)
    if not cites:
        return GroundingReport()
    if not root:
        return GroundingReport(checked=False,
                               reason="no repository root — the store was built without a surface")
    shas = resolve_shas([c.target for c in cites if c.kind == SHA], root)
    verdicts: List[CitationVerdict] = []
    for c in cites:
        if c.kind == PATH:
            v = _check_path(c, root)
            if c.source == SRC_RETIRE_WHEN and not v.grounded:
                v = CitationVerdict(c, False, f"{v.basis} — `deleted:` would fire immediately")
        elif c.kind == SHA:
            why = shas.get(c.target, "unresolved")
            v = CitationVerdict(c, not why, "commit resolves (git cat-file)" if not why else why)
        elif c.kind == SYMBOL:
            v = _check_symbol(c, layer)
        elif c.kind == LINK:
            v = _check_link(c)
        else:
            v = _check_date(c)
        verdicts.append(v)
    return GroundingReport(verdicts=verdicts)


# --------------------------------------------------------------------------- retire_when
@dataclass(frozen=True)
class RetireDue:
    """One FIRED `retire_when` predicate — a verdict handed to `healing.py` as plain fields."""
    item: Any
    predicate: str
    reason: str


def _is_ancestor(root: str, sha: str) -> Optional[bool]:
    from ..quiet_spawn import hidden
    try:
        proc = subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor", sha, "HEAD"],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30, **hidden())
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None                     # unknown commit / not a repo: no opinion, never a fire


def evaluate_retire_when(items: Iterable[Any], *, root: str, layer: Any = None,
                         now: Optional[str] = None) -> List[RetireDue]:
    """The FIRED predicates across `items`. PURE and read-only: one stat per `deleted:`, one git
    call per `after:`, one graph ask per `removed:`. A predicate that cannot be evaluated (no
    root, no graph, unknown commit) has NO opinion — it never fires on missing evidence."""
    ref = _parse_date(now) if now else datetime.now(timezone.utc)
    fn, name = _resolver(layer)
    out: List[RetireDue] = []
    for item in items:
        for pred in getattr(item, "retire_when", None) or []:
            try:
                op, arg = parse_retire_predicate(pred)
            except ValueError:
                continue
            reason = ""
            if op == RETIRE_DATE:
                when = _parse_date(arg)
                if when is not None and ref is not None and ref >= when:
                    reason = f"the date {arg} has passed"
            elif op == RETIRE_DELETED and root:
                ab = _inside(root, arg)
                if ab is not None and not os.path.exists(ab):
                    reason = f"{arg} no longer exists in the repo"
            elif op == RETIRE_AFTER and root:
                if _is_ancestor(root, arg) is True:
                    reason = f"commit {arg} is now in HEAD's history"
            elif op == RETIRE_REMOVED and fn is not None:
                try:
                    if not fn(arg):
                        reason = f"the {name} no longer resolves {arg}"
                except Exception:  # noqa: BLE001 — no opinion on a graph hiccup
                    reason = ""
            if reason:
                out.append(RetireDue(item=item, predicate=str(pred), reason=reason))
    return out


__all__ = [
    "BASIS_NO_GRAPH", "Citation", "CitationVerdict", "GroundingReport", "LINK", "PATH",
    "RETIRE_AFTER", "RETIRE_DATE", "RETIRE_DELETED", "RETIRE_OPS", "RETIRE_REMOVED", "RetireDue",
    "SHA", "SYMBOL", "check_grounding", "citations_of", "evaluate_retire_when",
    "extract_value_citations", "parse_retire_predicate", "resolve_shas",
]
