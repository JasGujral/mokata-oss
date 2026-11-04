"""G3-EVIDENCE (0.0.27) — the PR check's EVIDENCE render (EV-GATE clause (d)).

Renders, into the PR comment and the `--evidence-file`, two things the verdicts alone do not show:
this check's OWN gate evidence (computed on the runner) and the run records the PR COMMITTED
(`run_record`, `.mokata/runs/<run>/`), which are the PR author's claim and never grade the PR.

Committed content is UNTRUSTED (P15). Every untrusted value is redacted (`explain.ground.redact`)
and rendered inside a `code_span` — no markdown, no raw HTML, no `@mention` ping, no `#123` link.
A `<summary>` is static text. A path becomes a link only when it is a safe repo-relative path that
resolves inside the checkout and exists there; a URL is never an active link.

SIZE (no silent caps, no small caps). GitHub refuses a comment over `GITHUB_COMMENT_MAX`
characters and truncates a step summary over `STEP_SUMMARY_MAX` bytes; both limits are GitHub's.
Sections are ordered failing-first and kept WHOLE or left out WHOLE; the verdict section is never
dropped (it falls back to counts); what is left out is disclosed by counts plus a pointer to where
it is.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote

#: GitHub's limit on an issue / PR comment body, in characters (the REST API refuses a longer one).
GITHUB_COMMENT_MAX = 65536
#: GitHub's limit on one step's job summary (`$GITHUB_STEP_SUMMARY`), in bytes.
STEP_SUMMARY_MAX = 1024 * 1024
#: Room kept for the disclosure block. It is bounded by construction (one line per rank x kind,
#: four ranks and four kinds, plus a fixed pointer), so this is a ceiling it cannot exceed.
DISCLOSURE_RESERVE = 4000

UNVERIFIED = "[committed, unverified]"

# Ranks: lower renders first, so whatever is left out is passing evidence first.
RANK_BLOCK, RANK_DEGRADED, RANK_UNPROVEN, RANK_PASS = 0, 1, 2, 3
RANK_WORDS = {RANK_BLOCK: "blocked", RANK_DEGRADED: "degraded/unreadable",
              RANK_UNPROVEN: "unproven/advisory", RANK_PASS: "passing"}
KIND_WORDS = {"check": "this-check evidence", "committed": "committed evidence",
              "ledger": "committed ledger", "record": "committed record problem"}

_LINE_MAX_DIGITS = 7
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_HTTPS_BASE_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d+)?(?:/[A-Za-z0-9._~-]+)*$")


# ================================================================================ inert text
@dataclass
class _Tally:
    redacted: int = 0


def _clean(value: Any, tally: Optional[_Tally] = None) -> str:
    from .explain.ground import redact
    text = "" if value is None else str(value)
    text, n = redact(text)
    if tally is not None:
        tally.redacted += n
    return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def code_span(value: Any, tally: Optional[_Tally] = None) -> str:
    """`value` as an inline code span no content can close: redacted, newlines flattened, delimited
    by a backtick run ONE LONGER than the longest run inside it, padded so a leading or trailing
    backtick cannot fuse with the delimiter."""
    text = _clean(value, tally)
    if not text.strip():
        return '""'
    runs = re.findall(r"`+", text)
    tick = "`" * ((max(len(r) for r in runs) if runs else 0) + 1)
    return f"{tick} {text} {tick}"


def fence(content: str) -> Tuple[str, str]:
    """(open, close) for a fenced block the content cannot close: a backtick fence one longer than
    the longest backtick run in the content (CommonMark), never shorter than three."""
    runs = re.findall(r"`+", content or "")
    n = max(3, (max(len(r) for r in runs) if runs else 0) + 1)
    return "`" * n, "`" * n


# ================================================================================ links
@dataclass
class LinkContext:
    base: Optional[str] = None          # https://host/owner/repo — None renders paths as text
    head_sha: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, link_base: str = "",
                 head_sha: str = "") -> "LinkContext":
        sha = head_sha or env.get("GITHUB_SHA", "")
        if sha and not _SHA_RE.match(sha):
            sha = ""
        base = link_base.rstrip("/") if link_base else ""
        if not base:
            server, repo = env.get("GITHUB_SERVER_URL", ""), env.get("GITHUB_REPOSITORY", "")
            if server and repo and _REPO_RE.match(repo):
                base = f"{server.rstrip('/')}/{repo}"
        if not base or not _HTTPS_BASE_RE.match(base) or not sha:
            return cls(None, sha)
        return cls(base, sha)


def path_link(root: str, ref: str, link: LinkContext,
              tally: Optional[_Tally] = None) -> str:
    """A path artifact as markdown: a link when it is safe and present, a code span when there is
    no link base, else `path withheld (<reason>)`."""
    from .explain.ground import safe_rel, sensitive
    raw = str(ref or "")
    path, line = raw, ""
    if ":" in raw:
        head, _, tail = raw.rpartition(":")
        path, line = head, tail
        if not (line.isdigit() and len(line) <= _LINE_MAX_DIGITS) or not line.isascii():
            return f"path withheld (line suffix is not a line number): {code_span(raw, tally)}"
    if path and sensitive(path):
        return f"path withheld (sensitive path, not linked): {code_span(path, tally)}"
    rel = safe_rel(path)
    if rel is None:
        return f"path withheld (not a safe repo-relative path): {code_span(raw, tally)}"
    real_root = os.path.realpath(root)
    full = os.path.realpath(os.path.join(real_root, *rel.split("/")))
    if not (full == real_root or full.startswith(real_root.rstrip(os.sep) + os.sep)):
        return f"path withheld (resolves outside the repository): {code_span(raw, tally)}"
    if not os.path.exists(full):
        return f"path withheld (not in this checkout): {code_span(raw, tally)}"
    shown = code_span(raw, tally)
    if link.base is None:
        return shown
    url = f"{link.base}/blob/{link.head_sha}/{quote(rel, safe='/')}"
    if line:
        url += f"#L{int(line)}"
    return f"[{shown}]({url})"


def url_text(ref: str, tally: Optional[_Tally] = None) -> str:
    """A URL artifact: never an active link. `https` shows as code; any other scheme is withheld."""
    m = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", str(ref or ""))
    scheme = (m.group(1).lower() if m else "")
    if scheme != "https":
        return f"url withheld (scheme {code_span(scheme or 'none', tally)})"
    return code_span(ref, tally)


# ================================================================================ sections
@dataclass
class Section:
    rank: int
    kind: str                # check | committed | ledger | record
    md: str


@dataclass
class EvidenceView:
    """Everything the evidence render needs, already gathered (pure of I/O but path checks)."""
    root: str
    check_evidence: List[Any] = field(default_factory=list)   # GateEvidence (this check's own)
    check_blocked: bool = False
    records: Any = None                                        # run_record.RunRecords
    link: LinkContext = field(default_factory=LinkContext)


def _ev_dict(ev: Any) -> Dict[str, Any]:
    return ev.to_dict() if hasattr(ev, "to_dict") else dict(ev or {})


def _evidence_rank(d: Dict[str, Any], *, blocked_hint: bool = False) -> int:
    verdict = str(d.get("verdict", ""))
    tested = [t for t in d.get("tested") or [] if isinstance(t, dict)]
    if verdict == "block" or blocked_hint:
        return RANK_BLOCK
    if verdict in ("would-block", "override") or any(t.get("status") in ("fail", "error")
                                                      for t in tested):
        return RANK_UNPROVEN
    return RANK_PASS


def _render_evidence(d: Dict[str, Any], view: EvidenceView, tally: _Tally, *,
                     committed: bool, counter: List[int]) -> str:
    mark = f"{code_span(UNVERIFIED)} " if committed else ""
    who = "Committed evidence" if committed else "This check's evidence"
    lines = [f"#### {mark}{who} — gate {code_span(d.get('gate_id', ''), tally)} · verdict "
             f"{code_span(d.get('verdict', ''), tally)} · run {code_span(d.get('run_id', ''), tally)}"]
    for t in d.get("tested") or []:
        if not isinstance(t, dict):
            continue
        acs = ", ".join(code_span(a, tally) for a in t.get("ac_ids") or []) or "none"
        lines.append(f"- {mark}tested {code_span(t.get('check_id', ''), tally)} "
                     f"{code_span(t.get('status', ''), tally)} (ACs: {acs})")
    for a in d.get("artifacts") or []:
        if not isinstance(a, dict):
            continue
        typ, ref = str(a.get("type", "")), str(a.get("ref", ""))
        label = a.get("label", "")
        if typ == "path":
            lines.append(f"- {mark}artifact path: {path_link(view.root, ref, view.link, tally)}"
                         + (f" — {code_span(label, tally)}" if label else ""))
        elif typ == "url":
            lines.append(f"- {mark}artifact url: {url_text(ref, tally)}"
                         + (f" — {code_span(label, tally)}" if label else ""))
        elif typ == "content":
            counter[0] += 1
            body = _redacted_block(ref, tally)
            open_f, close_f = fence(body)
            truncated = body.endswith("…[truncated]")
            lines += [f"- {mark}artifact content:", "",
                      f"<details><summary>Evidence: content #{counter[0]}</summary>", "",
                      f"label: {code_span(label, tally)}" if label else "label: none", ""]
            if truncated:
                lines += ["_truncated at record time to 4000 chars_", ""]
            lines += [open_f, body, close_f, "", "</details>", ""]
        else:
            lines.append(f"- {mark}artifact of unknown type {code_span(typ, tally)} — not rendered")
    return "\n".join(lines)


def _redacted_block(text: str, tally: _Tally) -> str:
    from .explain.ground import redact
    out, n = redact(str(text or ""))
    tally.redacted += n
    return out


def _render_ledger(rec: Any, view: EvidenceView, tally: _Tally) -> Tuple[int, str]:
    mark = code_span(UNVERIFIED)
    rank = RANK_PASS
    lines = [f"#### {mark} Committed ledger — record {code_span(rec.rel_dir, tally)} "
             f"({len(rec.rows)} row(s))"]
    for row in rec.rows:
        if str(row.get("decision", "")) in ("blocked", "rejected") \
                or row.get("kind") == "enforcement_change":
            rank = min(rank, RANK_DEGRADED)
        parts = " · ".join(f"{_clean(k)} {code_span(v, tally)}" for k, v in sorted(row.items())
                           if re.match(r"^[a-z_]+$", str(k)))
        lines.append(f"- {mark} {parts}")
    unattributed = rec.meta.get("rows_unattributed", 0)
    if isinstance(unattributed, int) and unattributed:
        lines.append(f"- {mark} {unattributed} ledger row(s) on the exporting machine carried no "
                     f"run id — counted only, not in this record")
    return rank, "\n".join(lines)


def _record_header(rec: Any, view: EvidenceView, tally: _Tally) -> str:
    head = str(rec.meta.get("head_sha", "") or "unknown")
    now = view.link.head_sha or "unknown"
    return (f"{code_span(UNVERIFIED)} record {code_span(rec.rel_dir, tally)} — recorded at "
            f"{code_span(head, tally)}; this head is {code_span(now, tally)}")


def build_sections(view: EvidenceView, tally: _Tally) -> List[Section]:
    from .run_record import OK
    sections: List[Section] = []
    counter = [0]
    for ev in view.check_evidence:
        d = _ev_dict(ev)
        rank = _evidence_rank(d, blocked_hint=view.check_blocked and d.get("verdict") == "block")
        sections.append(Section(rank, "check", _render_evidence(d, view, tally, committed=False,
                                                                counter=counter)))
    recs = view.records
    for rec in (recs.records if recs is not None else []):
        if rec.state != OK:
            sections.append(Section(RANK_DEGRADED, "record",
                                    f"#### {code_span(UNVERIFIED)} record "
                                    f"{code_span(rec.rel_dir, tally)} is "
                                    f"{code_span(rec.state.upper(), tally)} — NOT rendered: "
                                    f"{code_span(rec.reason, tally)}"))
            continue
        header = _record_header(rec, view, tally)
        for d in rec.evidence:
            body = _render_evidence(d, view, tally, committed=True, counter=counter)
            sections.append(Section(_evidence_rank(d), "committed", body + "\n" + header))
        rank, body = _render_ledger(rec, view, tally)
        sections.append(Section(rank, "ledger", body + "\n" + header))
    sections.sort(key=lambda s: s.rank)                 # stable: failing first, order kept within
    return sections


def records_status(records: Any) -> List[str]:
    """The committed-records state, always shown (a fixed number of lines)."""
    from . import run_record as R
    if records is None:
        return ["Committed run records: not read."]
    if records.state == R.NOT_OPTED_IN:
        return ["Committed run records: **not opted in** — no `.mokata/runs/` in this repo, so "
                "this check has no ledger or committed evidence to show (not 'none found'). "
                "Commit a run's record with `mokata evidence commit --run <id>`."]
    if records.state == R.NO_FILE_LIST:
        return ["Committed run records: **not attributable** — this check got no changed-file "
                "list, so it cannot tell which records belong to this PR. None are shown."]
    if records.state == R.NONE_IN_PR:
        return [f"Committed run records: **none in this PR** ({records.untouched} record(s) in "
                f"the repo; this PR touched none of them)."]
    states: Dict[str, int] = {}
    for rec in records.records:
        states[rec.state] = states.get(rec.state, 0) + 1
    counts = ", ".join(f"{n} {s}" for s, n in sorted(states.items()))
    line = (f"Committed run records in this PR: {len(records.records)} ({counts}). Each is the "
            f"PR author's claim — **{UNVERIFIED}**, never graded by this check.")
    if records.degraded:
        line += " ⚠️ Some could not be rendered; see below."
    return [line]


# ================================================================================ composition
def _disclosure(omitted: List[Section], pointer: str) -> str:
    if not omitted:
        return ""
    counts: Dict[Tuple[int, str], int] = {}
    for s in omitted:
        counts[(s.rank, s.kind)] = counts.get((s.rank, s.kind), 0) + 1
    lines = ["", "#### Left out of this output (size limit)",
             f"{len(omitted)} section(s) did not fit and were left out WHOLE, passing ones first:"]
    for (rank, kind), n in sorted(counts.items()):
        lines.append(f"- {n} {KIND_WORDS.get(kind, kind)} section(s), {RANK_WORDS[rank]}")
    lines.append(f"Where to find them: {pointer}")
    return "\n".join(lines)


def compose(verdict_full: str, verdict_counts: str, view: EvidenceView, *, limit: int,
            measure: Callable[[str], int], pointer: str) -> str:
    """The output under `limit` (by `measure`): the verdict section (whole, or its counts when the
    whole does not fit), the records status, then the evidence sections failing-first as a PREFIX
    — the first that does not fit stops the run, so nothing failing is left out while something
    passing behind it is kept — then the disclosure of what was left out."""
    tally = _Tally()
    sections = build_sections(view, tally)
    status = records_status(view.records)
    ev_head = ["", "### Evidence", *status]

    def joined(parts: List[str]) -> str:
        return "\n".join(parts)

    reserve = DISCLOSURE_RESERVE
    verdict = verdict_full
    if measure(joined([verdict, *ev_head])) + reserve > limit:
        verdict = verdict_counts
    out = [verdict, *ev_head]
    used = measure(joined(out))
    kept = 0
    for s in sections:
        cost = measure("\n\n" + s.md)
        if used + cost + reserve > limit:
            break
        out += ["", s.md]
        used += cost
        kept += 1
    omitted = sections[kept:]
    if tally.redacted:
        out.append(f"\n_{tally.redacted} line(s) redacted by mokata's secret scan._")
    disclosure = _disclosure(omitted, pointer)
    if disclosure:
        out.append(disclosure)
    return joined(out)


def verdict_counts(result: Any, pointer: str) -> str:
    """The verdict section reduced to counts — what is shown when the whole section does not fit.
    The headline and every leg's status always survive; the AC lines with no test are listed
    first, while they fit in half the comment, then counted."""
    lines = [f"## {result.icon} mokata PR check — **{result.overall}**", "",
             "_The full verdict detail did not fit in this comment; it is summarized by counts._"]
    budget = GITHUB_COMMENT_MAX // 2
    used = sum(len(x) for x in lines)
    for leg in result.legs:
        notest = [d for d in leg.detail if "(no test)" in d or "not graded" in d]
        lines.append(f"- {leg.name}: **{leg.status.upper()}** — {len(leg.detail)} detail line(s), "
                     f"{len(notest)} without a test or not graded")
        shown = 0
        for d in notest:
            line = f"  - {code_span(d)}"
            if used + len(line) > budget:
                break
            lines.append(line)
            used += len(line)
            shown += 1
        if shown < len(notest):
            lines.append(f"  - … and {len(notest) - shown} more")
    lines.append(f"Full detail: {pointer}")
    return "\n".join(lines)


__all__ = ["GITHUB_COMMENT_MAX", "STEP_SUMMARY_MAX", "LinkContext", "EvidenceView", "Section",
           "code_span", "fence", "path_link", "url_text", "build_sections", "compose",
           "records_status", "verdict_counts", "UNVERIFIED"]
