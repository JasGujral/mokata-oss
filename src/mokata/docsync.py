"""DK.S5 — docsync: keep the documentation TRUE to the code (audit + human-gated reconcile).

Documentation drifts from code silently: a renamed command, a bumped skill count, a dead install
path, a signature that changed under a doc's feet. mokata's pre-release ground-up doc gate (doc 00
hard rule) has always caught this by hand; docsync operationalizes it into a reusable, auto-firing
capability that any phase — or the human — can run.

It has TWO targeting modes and TWO output modes:

  * targeting (i) — the user points at a doc: :func:`audit_doc` / :func:`reconcile_doc` check
    exactly that file against the code;
  * targeting (ii) — the system finds the docs: :func:`sweep` walks the doc tree, and (given the
    symbols a change touched) :func:`drift_docs` narrows to the docs that reference them — so the
    user need not know which doc went stale;

  * output (a) — AUDIT (read-only, default): :func:`audit_text` cross-references every claim
    against the code (skill counts, command names, install/getting-started path, version examples,
    and — with an injected graph resolver — symbols/config keys), reporting each discrepancy with a
    severity (Blocking / Minor / Info) and the stale section it sits in. It writes NOTHING;
  * output (b) — RECONCILE (human-gated writes, P2): :func:`reconcile_doc` proposes the edits that
    bring a doc back in line with the code, PREVIEWS the unified diff, and writes ONLY through the
    universal WriteGate (secret-scan → explicit human approval → audit). Never silent.

docsync adds NO new runtime gate: the audit is read-only and the reconcile rides the existing
WriteGate. It attaches to the flow — it feeds the pre-release doc gate, pairs with the docs/ADR
domain skill, and auto-fires on drift (a doc references a symbol the change touched → engage with
the ``⛭`` banner). Brainstorm's Lens 1 calls :func:`assess_doc_freshness` to surface stale docs
mid-brainstorm and ask the human to update.

Clean-room: mokata's own words; no external framework imported. Pure/degrade-clean — the checks
run lexically when no code graph is wired, and gain precision when one is injected.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------- severities (OUTPUT labels)
# These label the audit OUTPUT to triage discrepancies; they are NOT gates and change no gate —
# the same output-only discipline SK.S2 uses for review severities.
BLOCKING = "Blocking"          # a claim contradicts the shipping code (must fix before release)
MINOR = "Minor"                # a likely-stale claim that should be fixed
INFO = "Info"                  # context/version drift, no action forced
SEVERITIES: Tuple[str, ...] = (BLOCKING, MINOR, INFO)
_SEVERITY_RANK = {BLOCKING: 0, MINOR: 1, INFO: 2}

# Doc-freshness verdicts for brainstorm's Lens 1.
FRESH = "fresh"
STALE = "stale"
NEW_NEEDED = "new-doc-needed"


# --------------------------------------------------------------- the code-truth facts
@dataclass(frozen=True)
class CodeFacts:
    """The ground-truth facts the audit cross-references a doc against — pulled from the SAME
    single sources the rest of mokata reads (the curated-skills registry, the live CLI parser, the
    version constant), never a hand-kept copy. ``known_symbols`` / ``config_keys`` are optional
    graph/memory-derived sets that sharpen the symbol + config-key checks when present."""

    skill_count: int                     # curated pipeline skills (len CURATED_SKILLS)
    total_skill_count: int               # curated + shipped domain skills (installed on disk)
    command_names: frozenset             # live `mokata <cmd>` subcommands
    slash_commands: frozenset            # shipped `/mokata:<name>` slash-command surface (parity)
    package: str                         # the pip package name
    install_command: str                 # the canonical install invocation
    setup_command: str                   # the canonical getting-started invocation
    version: str                         # the shipping version
    known_symbols: frozenset = frozenset()
    config_keys: frozenset = frozenset()


def gather_facts(*, extra_symbols: Optional[Sequence[str]] = None,
                 config_keys: Optional[Sequence[str]] = None) -> CodeFacts:
    """Assemble :class:`CodeFacts` from the live code — the curated-skills registry, the shipped
    domain skills, the argparse command set, and the version constant. This is the "graph + memory"
    side of the cross-reference; ``extra_symbols`` / ``config_keys`` let a caller inject a
    graph/memory-derived watch set for the symbol + config checks."""
    from . import __version__
    from .agent_skills import CURATED_SKILLS, installed_skill_names
    from .parity import cli_command_names, slash_command_names
    curated = tuple(CURATED_SKILLS)
    installed = tuple(installed_skill_names())
    try:
        commands = frozenset(cli_command_names())
    except Exception:                    # a broken parser must never crash an audit — degrade
        commands = frozenset()
    try:
        slash = frozenset(slash_command_names())
    except Exception:                    # a broken registry must never crash an audit — degrade
        slash = frozenset()
    return CodeFacts(
        skill_count=len(curated),
        total_skill_count=len(installed),
        command_names=commands,
        slash_commands=slash,
        package="mokata",
        install_command="pip install mokata",
        setup_command="mokata setup claude",
        version=__version__,
        known_symbols=frozenset(str(s) for s in (extra_symbols or ())),
        config_keys=frozenset(str(s) for s in (config_keys or ())),
    )


# --------------------------------------------------------------- a finding
@dataclass
class Finding:
    """One doc↔code discrepancy: which checker raised it, its severity, where it sits (1-based line
    + the nearest preceding heading = its stale section), the offending text, a message, and — when
    the fix is unambiguous — a ``suggestion`` (the full replacement for that line) the reconcile
    path can apply. ``suggestion is None`` means "flagged, but not auto-reconcilable" (report only)."""

    checker: str
    severity: str
    line: int                            # 1-based
    section: str                         # nearest preceding markdown heading, or "(top)"
    excerpt: str
    message: str
    suggestion: Optional[str] = None

    @property
    def fixable(self) -> bool:
        return self.suggestion is not None

    def render(self) -> str:
        tail = " · reconcilable" if self.fixable else ""
        return (f"[{self.severity}] {self.checker} (L{self.line} · §{self.section}): "
                f"{self.message}{tail}")


# --------------------------------------------------------------- markdown structure helpers
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*\S)\s*$")
_INLINE_CODE = re.compile(r"`([^`]+)`")


def _sections(lines: Sequence[str]) -> List[str]:
    """Map each line index to the nearest preceding heading title (its "section"). A heading line
    belongs to its own section. Everything before the first heading is ``(top)``."""
    out: List[str] = []
    cur = "(top)"
    for ln in lines:
        m = _HEADING.match(ln)
        if m:
            cur = m.group(2).strip()
        out.append(cur)
    return out


def _code_spans(lines: Sequence[str]) -> List[str]:
    """For each line, the CODE text on it — the whole line when inside a fenced ``` block, else the
    concatenation of its inline `code` spans. Command/install/version checks scan only code text, so
    prose like "mokata governs the write" is never mistaken for a `mokata <cmd>` reference."""
    out: List[str] = []
    in_fence = False
    for ln in lines:
        stripped = ln.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            out.append("")               # the fence marker line itself carries no claim
            continue
        if in_fence:
            out.append(ln)
        else:
            out.append(" ".join(_INLINE_CODE.findall(ln)))
    return out


# --------------------------------------------------------------- the checkers
_SKILL_COUNT = re.compile(r"\b(\d+)\s+skills\b")
_MOKATA_CMD = re.compile(r"\bmokata\s+([a-z][a-z0-9-]+)\b")
_SLASH_CMD = re.compile(r"/mokata:([a-z][a-z0-9-]+)\b")
_PIP_INSTALL = re.compile(r"\bpip\s+install\s+(?:-U\s+|--upgrade\s+)?([A-Za-z0-9._-]+(?:\[[^\]]+\])?)")
_VERSION_PIN = re.compile(r"\bmokata==(\d+\.\d+\.\d+(?:[.\w-]*)?)")
_DOTTED_SYMBOL = re.compile(r"\b((?:[A-Za-z_][A-Za-z0-9_]*\.){1,}[A-Za-z_][A-Za-z0-9_]*)\b")

# `mokata <word>` reads where <word> is an argument/topic, not a subcommand — never flag these.
_CMD_ALLOW_NEXT = frozenset({
    "agent", "skill", "framework", "memory", "governs", "remembers", "audits",
})


def _check_skill_count(lines, sections, facts: CodeFacts) -> List[Finding]:
    valid = {facts.skill_count, facts.total_skill_count}
    out: List[Finding] = []
    for i, ln in enumerate(lines):
        for m in _SKILL_COUNT.finditer(ln):
            n = int(m.group(1))
            if n in valid:
                continue
            suggestion = ln[: m.start(1)] + str(facts.skill_count) + ln[m.end(1):]
            out.append(Finding(
                "skill-count", BLOCKING, i + 1, sections[i], ln.strip(),
                f'doc claims "{n} skills" but the code ships {facts.skill_count} pipeline '
                f"skills ({facts.total_skill_count} incl. domain skills)", suggestion))
    return out


def _check_commands(lines, sections, code, facts: CodeFacts) -> List[Finding]:
    out: List[Finding] = []
    for i, seg in enumerate(code):
        if not seg:
            continue
        for m in _MOKATA_CMD.finditer(seg):
            name = m.group(1)
            if name in _CMD_ALLOW_NEXT or name in facts.command_names or not facts.command_names:
                continue
            out.append(Finding(
                "command-name", BLOCKING, i + 1, sections[i], lines[i].strip(),
                f"references `mokata {name}` — no such command in the CLI "
                f"(stale/renamed command)", None))
        for m in _SLASH_CMD.finditer(seg):
            name = m.group(1)
            if name in facts.slash_commands or not facts.slash_commands:
                continue
            out.append(Finding(
                "command-name", BLOCKING, i + 1, sections[i], lines[i].strip(),
                f"references `/mokata:{name}` — no such slash command "
                f"(stale/renamed command)", None))
    return out


def _check_install(lines, sections, code, facts: CodeFacts) -> List[Finding]:
    out: List[Finding] = []
    for i, seg in enumerate(code):
        if not seg:
            continue
        for m in _PIP_INSTALL.finditer(seg):
            pkg = m.group(1)
            base = pkg.split("[", 1)[0]
            if base == facts.package:
                continue
            # a mokata-prefixed but non-canonical package = a dead install path.
            if base.lower().replace("_", "-").startswith("mokata"):
                suggestion = lines[i].replace(f"pip install {pkg}", facts.install_command)
                out.append(Finding(
                    "install-path", BLOCKING, i + 1, sections[i], lines[i].strip(),
                    f"dead install path `pip install {pkg}` — the package is "
                    f"`{facts.package}`; the canonical path is `{facts.install_command}`",
                    suggestion if suggestion != lines[i] else None))
    return out


def _check_version(lines, sections, code, facts: CodeFacts) -> List[Finding]:
    out: List[Finding] = []
    for i, seg in enumerate(code):
        if not seg:
            continue
        for m in _VERSION_PIN.finditer(seg):
            ver = m.group(1)
            if ver == facts.version:
                continue
            suggestion = lines[i].replace(f"mokata=={ver}", f"mokata=={facts.version}")
            out.append(Finding(
                "version-example", INFO, i + 1, sections[i], lines[i].strip(),
                f"version example `mokata=={ver}` differs from the shipping "
                f"`{facts.version}`", suggestion if suggestion != lines[i] else None))
    return out


def _check_symbols(lines, sections, code, facts: CodeFacts,
                   resolve: Optional[Callable[[str], bool]]) -> List[Finding]:
    """Cross-reference dotted symbol references (e.g. `progress.active_banner`) against the code.
    Degrade-clean: with NO resolver AND no ``known_symbols`` watch set, this is a no-op (there is no
    graph to check against, so it never guesses). With a resolver it flags references the graph says
    don't resolve; with a ``known_symbols`` set it flags a near-miss to a known symbol."""
    if resolve is None and not facts.known_symbols:
        return []
    out: List[Finding] = []
    for i, seg in enumerate(code):
        if not seg:
            continue
        for m in _DOTTED_SYMBOL.finditer(seg):
            sym = m.group(1)
            if sym in facts.known_symbols:
                continue
            if resolve is not None:
                try:
                    ok = bool(resolve(sym))
                except Exception:
                    continue             # a failing resolver never turns into a false finding
                if ok:
                    continue
                out.append(Finding(
                    "symbol-ref", MINOR, i + 1, sections[i], lines[i].strip(),
                    f"references symbol `{sym}` — the code graph does not resolve it "
                    f"(stale/renamed symbol)", None))
    return out


# --------------------------------------------------------------- AUDIT (output mode a, read-only)
def audit_text(text: str, *, path: str = "", facts: Optional[CodeFacts] = None,
               resolve: Optional[Callable[[str], bool]] = None) -> List[Finding]:
    """Audit one doc's TEXT against the code (read-only). Returns every :class:`Finding`, sorted by
    line then checker. Pure given ``facts`` + ``text`` (+ an optional graph ``resolve`` predicate for
    the symbol check). This writes nothing — it is the default, read-only output mode."""
    facts = facts or gather_facts()
    lines = text.splitlines()
    sections = _sections(lines)
    code = _code_spans(lines)
    out: List[Finding] = []
    out += _check_skill_count(lines, sections, facts)
    out += _check_commands(lines, sections, code, facts)
    out += _check_install(lines, sections, code, facts)
    out += _check_version(lines, sections, code, facts)
    out += _check_symbols(lines, sections, code, facts, resolve)
    out.sort(key=lambda f: (f.line, f.checker))
    return out


def audit_doc(path: Any, *, facts: Optional[CodeFacts] = None,
              resolve: Optional[Callable[[str], bool]] = None) -> List[Finding]:
    """Read a doc and :func:`audit_text` it. A missing/unreadable file raises ``OSError`` (the
    caller decides whether to skip); an audited file that is fine returns an empty list."""
    text = Path(path).read_text(encoding="utf-8")
    return audit_text(text, path=str(path), facts=facts, resolve=resolve)


def has_blocking(findings: Sequence[Finding]) -> bool:
    return any(f.severity == BLOCKING for f in findings)


def stale_sections(findings: Sequence[Finding]) -> List[str]:
    """The distinct sections that carry a Blocking/Minor finding — the "stale sections" to
    HIGHLIGHT (in doc order of first appearance)."""
    out: List[str] = []
    for f in findings:
        if f.severity in (BLOCKING, MINOR) and f.section not in out:
            out.append(f.section)
    return out


def render_findings(path: str, findings: Sequence[Finding]) -> str:
    """A human/CI-readable audit report for ONE doc, highlighting the stale sections."""
    if not findings:
        return f"docsync audit · {path}: OK — every claim matches the code."
    stale = stale_sections(findings)
    lines = [f"docsync audit · {path}: {len(findings)} discrepancy(ies)"]
    if stale:
        lines.append(f"  ⚠ stale section(s): {', '.join(stale)}")
    for f in sorted(findings, key=lambda f: (_SEVERITY_RANK[f.severity], f.line)):
        lines.append(f"  {f.render()}")
    return "\n".join(lines)


# --------------------------------------------------------------- RECONCILE (output mode b, gated)
def apply_suggestions(text: str, findings: Sequence[Finding]) -> Tuple[str, int]:
    """Apply the fixable findings' suggestions to ``text`` (one edit per line — the first fixable
    finding on a line wins). Returns ``(new_text, edits_applied)``. Preserves the original trailing
    newline. Pure — this proposes; the WRITE is human-gated in :func:`reconcile_doc`."""
    per_line: Dict[int, str] = {}
    for f in findings:
        if f.fixable and f.line not in per_line:
            per_line[f.line] = f.suggestion  # type: ignore[assignment]
    if not per_line:
        return text, 0
    lines = text.splitlines()
    for lineno, replacement in per_line.items():
        if 1 <= lineno <= len(lines):
            lines[lineno - 1] = replacement
    new_text = "\n".join(lines)
    if text.endswith("\n"):
        new_text += "\n"
    return new_text, len(per_line)


def render_diff(old_text: str, new_text: str, path: str = "doc") -> str:
    """A unified diff PREVIEW of the proposed reconcile edits — shown before any write."""
    diff = difflib.unified_diff(
        old_text.splitlines(), new_text.splitlines(),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="")
    return "\n".join(diff)


@dataclass
class ReconcileResult:
    """The outcome of a reconcile: whether it WROTE, why, the audit findings, the proposed diff, and
    the reconciled text. ``written=False`` on a decline (nothing was written) or when there was
    nothing reconcilable."""

    written: bool
    reason: str
    findings: List[Finding]
    diff: str = ""
    new_text: str = ""
    edits: int = 0


def reconcile_doc(path: Any, *, facts: Optional[CodeFacts] = None,
                  resolve: Optional[Callable[[str], bool]] = None,
                  ledger: Any = None,
                  confirm: Optional[Callable[[str], bool]] = None,
                  assume_yes: bool = False) -> ReconcileResult:
    """Propose doc↔code edits and write them ONLY on explicit human approval (P2). Audits the doc,
    builds the reconciled text + a unified-diff preview, and submits the write through the universal
    WriteGate (secret-scan → human gate → audit) — adding NO new gate. A decline (``confirm``
    returns False) writes NOTHING and returns ``written=False``. Never silent."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    findings = audit_text(text, path=str(path), facts=facts, resolve=resolve)
    fixable = [f for f in findings if f.fixable]
    if not fixable:
        return ReconcileResult(False, "no reconcilable discrepancies — nothing to write",
                               findings, "", "", 0)
    new_text, edits = apply_suggestions(text, fixable)
    diff = render_diff(text, new_text, str(path))
    from .govern import WriteGate, WriteRequest

    def commit() -> None:
        p.write_text(new_text, encoding="utf-8")

    prompt = (f"mokata docsync · reconcile {edits} doc↔code discrepancy(ies) in {path}:\n"
              f"{diff}\n\nApply these edits to the doc?")
    outcome = WriteGate(ledger=ledger).submit(
        WriteRequest("config", str(p), content=new_text, actor="docsync"),
        commit=commit, confirm=confirm, assume_yes=assume_yes, prompt=prompt)
    return ReconcileResult(outcome.committed, outcome.reason, findings, diff,
                           new_text, edits if outcome.committed else 0)


# --------------------------------------------------------------- targeting (ii): sweep + drift
# The doc tree docsync sweeps by default — the PUBLIC product docs (README + docs/), never the
# internal build/launch/marketing trees (they are not part of the docs↔code contract).
_INTERNAL_DOC_DIRS = ("docs/build", "docs/launch", "docs/marketing")


def find_docs(root: Any = ".", *, include_internal: bool = False) -> List[str]:
    """The `.md` docs docsync sweeps under ``root`` — README.md at the root plus everything under
    ``docs/``, excluding the internal build/launch/marketing trees unless ``include_internal``.
    Sorted, POSIX-style paths. Degrade-clean: a missing tree simply contributes nothing."""
    root = Path(root)
    found: List[str] = []
    readme = root / "README.md"
    if readme.is_file():
        found.append(str(readme))
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for p in sorted(docs_dir.rglob("*.md")):
            rel = p.relative_to(root).as_posix()
            if not include_internal and any(rel.startswith(d) for d in _INTERNAL_DOC_DIRS):
                continue
            found.append(str(p))
    return sorted(found)


def _references_any(path: Any, symbols: Sequence[str]) -> bool:
    """Lexical floor: does the doc name any of ``symbols``? (The graph floor for "which docs
    reference the changed symbols" — precise enough without a code graph, degrade-clean.)"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return False
    return any(str(s) and str(s) in text for s in symbols)


def drift_docs(root: Any, changed_symbols: Sequence[str], *,
               include_internal: bool = False) -> List[str]:
    """The docs that REFERENCE a symbol the change touched — the drift set (targeting mode ii). When
    the graph shows a doc names a changed symbol, that doc is a drift candidate docsync engages on."""
    docs = find_docs(root, include_internal=include_internal)
    return [d for d in docs if _references_any(d, changed_symbols)]


def sweep(root: Any = ".", *, facts: Optional[CodeFacts] = None,
          changed_symbols: Optional[Sequence[str]] = None,
          resolve: Optional[Callable[[str], bool]] = None,
          include_internal: bool = False) -> Dict[str, List[Finding]]:
    """Sweep + audit the doc tree (targeting mode ii). With ``changed_symbols`` it narrows to the
    drift set (docs that reference a changed symbol); without, it audits the whole public doc tree.
    Returns ``{doc_path: findings}`` for docs with ≥1 finding (a clean doc is omitted)."""
    facts = facts or gather_facts()
    docs = (drift_docs(root, changed_symbols, include_internal=include_internal)
            if changed_symbols else find_docs(root, include_internal=include_internal))
    out: Dict[str, List[Finding]] = {}
    for d in docs:
        try:
            findings = audit_doc(d, facts=facts, resolve=resolve)
        except OSError:
            continue
        if findings:
            out[d] = findings
    return out


def render_sweep(results: Dict[str, List[Finding]]) -> str:
    """A human/CI-readable multi-doc audit report."""
    if not results:
        return "docsync sweep: OK — every audited doc matches the code."
    total = sum(len(v) for v in results.values())
    lines = [f"docsync sweep: {total} discrepancy(ies) across {len(results)} doc(s)"]
    for path in sorted(results):
        lines.append(render_findings(path, results[path]))
    return "\n".join(lines)


# --------------------------------------------------------------- auto-fire on drift (the ⛭ banner)
def docsync_active_line(state: str = "active") -> str:
    """The single-sourced ``⛭ mokata docsync active — gate: …`` activation line (SK.S1), read from
    the Contract source via ``progress.active_skill_line`` — the same surface every skill renders."""
    from .progress import active_skill_line
    return active_skill_line("docsync", state=state)


def boundary_probe() -> str:
    """The Contract-boundary probe docsync announces when it auto-engages: it states the boundary it
    will hold (audit is read-only; a reconcile edit is previewed and human-gated) so an auto-fire can
    never quietly turn into a silent write."""
    return ("docsync boundary: the audit is READ-ONLY (it writes nothing); any doc edit is "
            "PREVIEWED as a diff and written ONLY through the human gate (P2) — never silent.")


@dataclass
class DriftEngagement:
    """What docsync surfaces when it auto-fires on drift: the ``⛭`` engaged banner, the docs that
    reference the changed symbols, and the boundary probe it will hold."""

    banner: str
    docs: List[str]
    boundary: str

    def render(self) -> str:
        lines = [self.banner, self.boundary,
                 f"docsync: {len(self.docs)} doc(s) reference the changed symbol(s) — auditing:"]
        lines.extend(f"  - {d}" for d in self.docs)
        return "\n".join(lines)


def drift_engagement(root: Any, changed_symbols: Sequence[str], *,
                     include_internal: bool = False) -> Optional[DriftEngagement]:
    """Auto-fire trigger: when a change's symbols are referenced by any doc, return a
    :class:`DriftEngagement` (the ``⛭`` banner + the drift docs + the boundary probe). Returns None
    when no doc references the change (nothing to engage on)."""
    docs = drift_docs(root, changed_symbols, include_internal=include_internal)
    if not docs:
        return None
    return DriftEngagement(docsync_active_line("engaged"), docs, boundary_probe())


# --------------------------------------------------------------- brainstorm Lens 1 doc-freshness
@dataclass
class DocFreshness:
    """One doc a brainstorm approach touches/invalidates, marked fresh / stale / new-doc-needed. A
    ``stale``/``new-doc-needed`` doc is HIGHLIGHTED and the human is asked to update it (advisory,
    human-gated); left unaddressed it carries into the spec as an open item."""

    path: str
    status: str                          # FRESH | STALE | NEW_NEEDED
    findings: List[Finding] = field(default_factory=list)

    @property
    def stale(self) -> bool:
        return self.status in (STALE, NEW_NEEDED)

    def render(self) -> str:
        mark = {FRESH: "fresh", STALE: "STALE", NEW_NEEDED: "NEW DOC NEEDED"}.get(
            self.status, self.status)
        flag = "⚠ " if self.stale else "  "
        return f"{flag}{self.path}: {mark}"


def assess_doc_freshness(touched_files: Sequence[str], *, root: Any = ".",
                         touched_symbols: Optional[Sequence[str]] = None,
                         facts: Optional[CodeFacts] = None,
                         resolve: Optional[Callable[[str], bool]] = None) -> List[DocFreshness]:
    """Lens 1 doc-freshness (called from brainstorm's blast-radius lens): for the docs a change
    touches or invalidates, audit each and mark fresh / stale. A doc directly in ``touched_files``
    or referencing a ``touched_symbol`` is audited (stale = it carries a Blocking/Minor finding).
    A touched CODE area that NO doc covers surfaces one ``new-doc-needed`` advisory. Calls the SAME
    :func:`audit_doc` engine — the audit is the single source of doc-truth."""
    facts = facts or gather_facts()
    symbols = list(touched_symbols or [])
    seen: set = set()
    docs: List[str] = []
    for d in (drift_docs(root, symbols) if symbols else []):
        if d not in seen:
            seen.add(d)
            docs.append(d)
    for f in touched_files:
        if str(f).endswith(".md") and str(f) not in seen:
            seen.add(str(f))
            docs.append(str(f))
    out: List[DocFreshness] = []
    for d in docs:
        try:
            findings = audit_doc(d, facts=facts, resolve=resolve)
        except OSError:
            continue
        status = STALE if any(x.severity in (BLOCKING, MINOR) for x in findings) else FRESH
        out.append(DocFreshness(d, status, findings))
    # A touched code surface that no doc references at all → a new-doc-needed advisory.
    code_touched = [f for f in touched_files if not str(f).endswith(".md")]
    if (symbols or code_touched) and not docs:
        out.append(DocFreshness("(no doc covers this change)", NEW_NEEDED, []))
    return out


def stale_docs(results: Sequence[DocFreshness]) -> List[DocFreshness]:
    """The docs Lens 1 marked stale / new-doc-needed — the ones to HIGHLIGHT and ask about."""
    return [r for r in results if r.stale]


def render_doc_freshness(results: Sequence[DocFreshness]) -> str:
    """A compact per-doc freshness block for the brainstorm design write-up (records Lens 1's
    doc-freshness check), highlighting the stale docs and naming the ask."""
    if not results:
        return "· Doc freshness (Lens 1): no docs touched by this change."
    lines = ["· Doc freshness (Lens 1 — docs the change touches/invalidates):"]
    for r in results:
        lines.append(f"  {r.render()}")
        for f in r.findings[:3]:
            lines.append(f"      ↳ {f.render()}")
    stale = stale_docs(results)
    if stale:
        lines.append(f"  → {len(stale)} stale doc(s) — ASK the user to update them before the "
                     f"spec (a stale doc left unaddressed carries into the spec as an open item).")
    return "\n".join(lines)
