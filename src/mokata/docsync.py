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
    graph/memory-derived sets that sharpen the symbol + config-key checks when present.

    ``unchecked`` (D5) names the checks that could NOT be armed because the fact behind them could
    not be gathered. An empty command set is INDISTINGUISHABLE, to a checker, from "every command in
    the doc is valid" — `_check_commands` short-circuits on it — so without this field a docsync run
    that checked NOTHING renders exactly like a clean one."""

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
    unchecked: Tuple[str, ...] = ()      # D5 — the checks this fact-set could not arm


def gather_facts(*, extra_symbols: Optional[Sequence[str]] = None,
                 config_keys: Optional[Sequence[str]] = None) -> CodeFacts:
    """Assemble :class:`CodeFacts` from the live code — the curated-skills registry, the shipped
    domain skills, the argparse command set, and the version constant. This is the "graph + memory"
    side of the cross-reference; ``extra_symbols`` / ``config_keys`` let a caller inject a
    graph/memory-derived watch set for the symbol + config checks.

    D5 — a fact that cannot be gathered DISARMS its checker, and a disarmed checker finds nothing,
    which used to render as "OK — every audited doc matches the code". It now records itself in
    ``unchecked`` and says so ONCE, loudly (`note_degraded`): the sweep still runs (the fallback
    still falls back), it just stops claiming a clean bill of health it never earned."""
    from . import __version__
    from .agent_skills import CURATED_SKILLS, installed_skill_names
    from .degrade import FAILURE_UNREACHABLE, note_degraded
    from .parity import cli_command_names, slash_command_names
    curated = tuple(CURATED_SKILLS)
    installed = tuple(installed_skill_names())
    unchecked: List[str] = []
    # A broken parser/registry must never crash an audit — but the drift check it feeds is then
    # NOT RUNNING, and that is the thing the user must be told. Both failures disarm the SAME
    # checker (`command-name`), so they share ONE subsystem key: one loud line, not two.
    try:
        commands = frozenset(cli_command_names())
    except (ImportError, AttributeError) as exc:
        commands = frozenset()
        unchecked.append("`mokata <cmd>` command-name drift (the CLI parser could not be read)")
        note_degraded("docsync-facts", FAILURE_UNREACHABLE,
                      fallback="command-name drift was NOT checked",
                      fix="fix the CLI import, then re-run `mokata docsync`", detail=str(exc))
    try:
        slash = frozenset(slash_command_names())
    except (ImportError, AttributeError) as exc:
        slash = frozenset()
        unchecked.append("`/mokata:<name>` slash-command drift (the surface matrix could not "
                         "be read)")
        note_degraded("docsync-facts", FAILURE_UNREACHABLE,
                      fallback="command-name drift was NOT checked",
                      fix="fix the CLI import, then re-run `mokata docsync`", detail=str(exc))
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
        unchecked=tuple(unchecked),
    )


# --------------------------------------------------------------- D5: the audit's own honesty
@dataclass
class AuditDegradation:
    """D5 — what an audit run could NOT check, so a finding-free result can never be rendered as a
    clean one. An audit is a NEGATIVE claim ("nothing is stale"), and a negative claim is only worth
    the checks that actually ran: a disarmed checker turns "we found no drift" into "we looked for
    no drift", and those two sentences printed the same words.

    Empty ⇒ every check ran ⇒ the render is unchanged, byte for byte."""

    unchecked: List[str] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return bool(self.unchecked)

    def note(self, reason: str) -> None:
        if reason not in self.unchecked:
            self.unchecked.append(reason)

    def banner(self) -> str:
        """The one line every degraded audit renders FIRST — never below the verdict."""
        return ("⚠ DEGRADED AUDIT — these checks did NOT run: " + "; ".join(self.unchecked)
                + ". A finding-free result here is NOT a clean bill of health.")

    @classmethod
    def from_facts(cls, facts: Optional[CodeFacts]) -> "AuditDegradation":
        return cls(list(facts.unchecked) if facts is not None else [])


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


_FENCE = re.compile(r"^\s{0,3}(?:```+|~~~+)\s*(\S*)")

# The fenced languages that hold INVOCATIONS — a shell the reader is told to type into, or an
# undeclared fence (which claims nothing, so the command-position rule below guards it). A fence the
# author LABELLED something else (`text`, `yaml`, `json`, `python`, …) is output or data: the CLI's
# own transcript, a workflow step, a payload. It is not a thing to run, and reading it as one is how
# `mokata initialized with profile 'standard'.` came to be reported as a stale command.
_SHELL_FENCE_LANGS = frozenset({
    "", "bash", "sh", "shell", "shell-session", "shellsession", "sh-session", "bash-session",
    "zsh", "fish", "console", "terminal", "command",
})
# A `#` comment tail is prose the author wrote for a human ("# wire mokata into your agent"), not a
# command. Anchored to a word boundary so a URL fragment or a `#`-bearing argument survives.
_SHELL_COMMENT = re.compile(r"(?:^|\s)#.*$")


def _code_spans(lines: Sequence[str]) -> List[List[str]]:
    """For each line, the CODE fragments on it — the whole line when inside a fenced ``` block, else
    each of its inline `code` spans, SEPARATELY.

    Separately is the whole point. These fragments used to be joined with a space, which fabricated
    text that appears nowhere in the doc: ``> `pip install mokata` → `mokata setup claude` `` became
    the single string "pip install mokata mokata setup claude", and the checkers dutifully reported
    the `mokata mokata` and the `pip install mokata-hook` they had just invented. Two code spans are
    two fragments with prose between them — never one command line."""
    out: List[List[str]] = []
    in_fence = False
    for ln in lines:
        if _FENCE.match(ln):
            in_fence = not in_fence
            out.append([])               # the fence marker line itself carries no claim
            continue
        out.append([ln] if in_fence else _INLINE_CODE.findall(ln))
    return out


def _command_spans(lines: Sequence[str]) -> List[List[str]]:
    """For each line, the fragments on it that a reader could actually RUN — the doc's invocation
    surface, which is what the `mokata <cmd>` checker is asking about ("does the doc tell you to run
    a command that no longer exists?").

    It is narrower than :func:`_code_spans` in exactly two ways: a fence the author labelled as
    something other than a shell is output or data, not commands; and inside a shell fence, a `#`
    comment tail is prose. Inline code spans carry commands and are kept as-is."""
    out: List[List[str]] = []
    in_fence = False
    fence_is_shell = False
    for ln in lines:
        m = _FENCE.match(ln)
        if m:
            if not in_fence:
                fence_is_shell = m.group(1).split(",")[0].lower() in _SHELL_FENCE_LANGS
            in_fence = not in_fence
            out.append([])
            continue
        if not in_fence:
            out.append(_INLINE_CODE.findall(ln))
        elif fence_is_shell:
            out.append([_SHELL_COMMENT.sub("", ln)])
        else:
            out.append([])
    return out


# --------------------------------------------------------------- the checkers
_SKILL_COUNT = re.compile(r"\b(\d+)\s+skills\b")
# `mokata <cmd>` only where `mokata` sits in COMMAND POSITION — at the head of the command, after an
# optional prompt sigil or runner, or straight after a shell separator. The word "mokata" in the
# middle of a sentence is the subject of that sentence, not a program being invoked: "mokata detects
# the `rg` executable" and "recurring corrections mokata noticed" are prose, and the old unanchored
# `\bmokata\s+(\w+)\b` read them as calls to `mokata detects` and `mokata noticed`.
_MOKATA_CMD = re.compile(
    r"(?:^|(?<=[|;&(`]))"                                       # command head, or after a separator
    r"\s*(?:[$>%❯]\s+)?"                                        # an optional prompt sigil: "$ mokata"
    r"(?:(?:sudo|uvx|npx)\s+|(?:pipx|uv|poetry|pdm|hatch)\s+run\s+|python3?\s+-m\s+)*"
    r"mokata\s+([a-z][a-z0-9-]+)\b")                            # …then the subcommand
_SLASH_CMD = re.compile(r"/mokata:([a-z][a-z0-9-]+)\b")
# A BARE slash command — `/name` where the `/` is NOT part of a `mokata:` prefix and NOT a path
# segment (`foo/bar`, `/docs/…`). This is the form the pip-first project route actually renders
# (`.claude/commands/<name>.md` → `/<name>`), and the form check reads it only where the name is a
# real mokata command, so a URL path or an ordinary `/word` in prose is never mistaken for one.
_BARE_SLASH_CMD = re.compile(r"(?<![:\w/])/([a-z][a-z0-9-]+)\b(?!/)")
_PIP_INSTALL = re.compile(r"\bpip\s+install\s+(?:-U\s+|--upgrade\s+)?([A-Za-z0-9._-]+(?:\[[^\]]+\])?)")
_VERSION_PIN = re.compile(r"\bmokata==(\d+\.\d+\.\d+(?:[.\w-]*)?)")
_DOTTED_SYMBOL = re.compile(r"\b((?:[A-Za-z_][A-Za-z0-9_]*\.){1,}[A-Za-z_][A-Za-z0-9_]*)\b")


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


# --------------------------------------------------------------- D-CMDNS: command-NAME FORM per route
# The rendered slash-command NAME depends on the install route, and a doc that shows the wrong form
# tells the reader to type a command their `/` menu does not have:
#   * pip-first project route (`mokata setup claude` → `.claude/commands/<name>.md`) and the
#     user-scope route (`~/.claude/commands/`) render the BARE `/<name>` — no prefix (grounded in
#     Claude Code's slash-command docs; subdirectories do not namespace the name either);
#   * the plugin route (installed from a marketplace, `plugin.json` name = `mokata`) namespaces
#     every command as `/mokata:<name>`.
# The canonical install path is pip-first (doc 00 rule 2), so BARE is the default form a page uses;
# a page whose subject is the plugin/harness surface declares itself otherwise.
ROUTE_PIP = "pip"          # bare `/<name>` only (the shipping, canonical form)
ROUTE_PLUGIN = "plugin"    # `/mokata:<name>` only (marketplace-plugin render)
ROUTE_BOTH = "both"        # a dual-route page: either form is accepted (it states the mapping once)
_ROUTES = (ROUTE_PIP, ROUTE_PLUGIN, ROUTE_BOTH)
_DEFAULT_COMMAND_ROUTE = ROUTE_PIP

# The pages that legitimately carry the `/mokata:<name>` namespaced form — their subject IS the
# harness/plugin surface (the CLI↔slash↔MCP mapping, or "using the plugin"), so they state the
# route→form mapping once and keep the namespaced form as their primary. Keyed by POSIX path
# SUFFIX (matched with endswith) so the map is independent of where the repo root sits. Everything
# not listed defaults to the pip-first bare form. A page may override this inline with a
# `mokata_command_route:` frontmatter key (self-documenting; used by fixtures and future pages).
_PAGE_COMMAND_ROUTE: Dict[str, str] = {
    "docs/reference/command-surfaces.md": ROUTE_BOTH,
    "docs/reference/cli.md": ROUTE_BOTH,
    "docs/how-to/use-the-plugin.md": ROUTE_BOTH,
    "docs/how-to/install-plugin.md": ROUTE_BOTH,
    "docs/how-to/use-mokata-in-cowork.md": ROUTE_BOTH,
}

_FRONTMATTER_ROUTE = re.compile(r"^mokata_command_route:\s*([a-z]+)\s*$")


def _frontmatter_route(text: str) -> Optional[str]:
    """The `mokata_command_route:` value from a leading `---` YAML frontmatter block, if present and
    valid. Degrade-clean: no YAML dependency, no frontmatter or an unknown value → ``None`` (the
    caller falls back to the path map, then the default). A page opts into a route inline with it —
    the self-contained mechanism fixtures use."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        m = _FRONTMATTER_ROUTE.match(ln.strip())
        if m and m.group(1) in _ROUTES:
            return m.group(1)
    return None


def resolve_command_route(text: str, path: str = "") -> str:
    """The command-name route a doc's slash references are checked against — the FORM its install
    route actually renders. Resolution order: an inline `mokata_command_route:` frontmatter key wins;
    else the per-page map (keyed by path suffix); else the pip-first default (bare). Always one of
    :data:`_ROUTES`."""
    fm = _frontmatter_route(text)
    if fm is not None:
        return fm
    norm = str(path).replace("\\", "/")
    for suffix, route in _PAGE_COMMAND_ROUTE.items():
        if norm == suffix or norm.endswith("/" + suffix) or norm.endswith(suffix):
            return route
    return _DEFAULT_COMMAND_ROUTE


def _rewrite_to_bare(line: str, valid: frozenset) -> str:
    """Rewrite every `/mokata:<name>` on ``line`` whose ``name`` is a real command to the bare
    `/<name>`. Unknown names are left alone (they are a different, membership finding)."""
    return _SLASH_CMD.sub(
        lambda m: ("/" + m.group(1)) if m.group(1) in valid else m.group(0), line)


def _rewrite_to_namespaced(line: str, valid: frozenset) -> str:
    """Rewrite every bare `/<name>` on ``line`` whose ``name`` is a real command to `/mokata:<name>`
    (the inverse — for a page whose declared route is the namespaced plugin form)."""
    return _BARE_SLASH_CMD.sub(
        lambda m: ("/mokata:" + m.group(1)) if m.group(1) in valid else m.group(0), line)


def _check_command_form(lines, sections, code, facts: CodeFacts, route: str) -> List[Finding]:
    """D-CMDNS — every slash-command reference uses the FORM its page's install route renders. The
    curated command set (``facts.slash_commands``, the single source of truth) fixes membership; the
    ``route`` fixes form. Read only over ``code`` fragments (inline `code` + fenced blocks) — a real
    invocation is written as code, and confining the check there is what keeps a `/word` in prose or
    a URL path from being read as a command (the false-positive discipline the 0.0.13 fix set).

    A ``both`` page accepts either form (it states the mapping once); a disarmed command set
    (``facts.slash_commands`` empty) checks nothing, exactly like the sibling checkers."""
    valid = facts.slash_commands
    if not valid or route == ROUTE_BOTH:
        return []
    out: List[Finding] = []
    for i, spans in enumerate(code):
        for seg in spans:
            if route == ROUTE_PIP:
                for m in _SLASH_CMD.finditer(seg):
                    name = m.group(1)
                    if name not in valid:            # unknown name → membership check owns it
                        continue
                    fixed = _rewrite_to_bare(lines[i], valid)
                    out.append(Finding(
                        "command-form", BLOCKING, i + 1, sections[i], lines[i].strip(),
                        f"references `/mokata:{name}` but this page's install route (pip-first "
                        f"`mokata setup claude`) renders the BARE `/{name}` — the plugin-namespaced "
                        f"form is not what the reader sees",
                        fixed if fixed != lines[i] else None))
            elif route == ROUTE_PLUGIN:
                for m in _BARE_SLASH_CMD.finditer(seg):
                    name = m.group(1)
                    if name not in valid:
                        continue
                    fixed = _rewrite_to_namespaced(lines[i], valid)
                    out.append(Finding(
                        "command-form", BLOCKING, i + 1, sections[i], lines[i].strip(),
                        f"references the bare `/{name}` but this page's install route (plugin) "
                        f"renders the namespaced `/mokata:{name}`",
                        fixed if fixed != lines[i] else None))
    return out


def _check_commands(lines, sections, code, commands, facts: CodeFacts) -> List[Finding]:
    """The two command surfaces are checked over DIFFERENT text, because they are ambiguous in
    different amounts. `mokata <cmd>` is a shell invocation whose first word is also this project's
    name, so it is read only off the invocation surface (``commands``) — anywhere else, "mokata" is
    just the subject of a sentence. `/mokata:<name>` cannot be prose: the `/mokata:` prefix is
    self-delimiting, so it keeps its full reach over every code fragment (``code``), including the
    output fences the shell rule skips."""
    out: List[Finding] = []
    for i, spans in enumerate(commands):
        for seg in spans:
            for m in _MOKATA_CMD.finditer(seg):
                name = m.group(1)
                if name in facts.command_names or not facts.command_names:
                    continue
                out.append(Finding(
                    "command-name", BLOCKING, i + 1, sections[i], lines[i].strip(),
                    f"references `mokata {name}` — no such command in the CLI "
                    f"(stale/renamed command)", None))
    for i, spans in enumerate(code):
        for seg in spans:
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
    for i, spans in enumerate(code):
        for seg in spans:
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
    for i, spans in enumerate(code):
        for seg in spans:
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
                   resolve: Optional[Callable[[str], bool]],
                   degradation: Optional["AuditDegradation"] = None) -> List[Finding]:
    """Cross-reference dotted symbol references (e.g. `progress.active_banner`) against the code.
    Degrade-clean: with NO resolver AND no ``known_symbols`` watch set, this is a no-op (there is no
    graph to check against, so it never guesses). With a resolver it flags references the graph says
    don't resolve; with a ``known_symbols`` set it flags a near-miss to a known symbol.

    D5 — a resolver that THROWS is not the same as a resolver that says "resolves fine". Skipping
    the symbol on an error is still right (a broken resolver must never manufacture a false
    "stale symbol" finding), but the skip is now COUNTED: a doc every one of whose symbols was
    skipped was not audited, and must not be declared fresh."""
    if resolve is None and not facts.known_symbols:
        return []
    out: List[Finding] = []
    failures = 0
    for i, spans in enumerate(code):
        for seg in spans:
            for m in _DOTTED_SYMBOL.finditer(seg):
                sym = m.group(1)
                if sym in facts.known_symbols:
                    continue
                if resolve is not None:
                    try:
                        ok = bool(resolve(sym))
                    except Exception:    # (iii) the resolver is INJECTED — its class is the
                        failures += 1    # caller's (a graph client, an MCP tool), not nameable here
                        continue         # a failing resolver never turns into a false finding
                    if ok:
                        continue
                    out.append(Finding(
                        "symbol-ref", MINOR, i + 1, sections[i], lines[i].strip(),
                        f"references symbol `{sym}` — the code graph does not resolve it "
                        f"(stale/renamed symbol)", None))
    if failures:
        from .degrade import FAILURE_UNREACHABLE, note_degraded
        if degradation is not None:
            degradation.note(f"symbol-reference drift ({failures} symbol(s) — the code-graph "
                             f"resolver raised)")
        note_degraded("docsync-symbols", FAILURE_UNREACHABLE,
                      fallback="the symbol-reference check did NOT run",
                      fix="check the code-graph resolver",
                      detail=f"{failures} symbol lookup(s) raised")
    return out


def graph_symbol_resolver(layer: Any,
                          degradation: Optional["AuditDegradation"] = None
                          ) -> Optional[Callable[[str], bool]]:
    """GR.S2 rider — a real symbol-existence resolver for `_check_symbols`, through the graph
    client. Returns a `Callable[[str], bool]` ONLY when a real graph that can AUTHORITATIVELY
    answer existence is wired (code-review-graph's `not_found` status); otherwise None.

    Two None cases, deliberately different:
      * no graph wired -> None + SILENT. No graph is the DEFAULT, never a degrade (mirrors
        `make_graph_scorer`), so a plain docsync run on a floor repo does not print a degrade
        banner for a check that was never meant to run.
      * a real graph is wired but exposes no authoritative existence query -> None + the disarm
        is recorded in `degradation`, so the audit is not read as clean when it skipped a check
        it COULD have run.

    NAMED HOOK: when mokata's typed query API gains an authoritative `exists`/`defines` kind,
    wire it here so more backends (e.g. the AST floor) can drive the symbol-drift audit too."""
    if layer is None or not getattr(layer, "uses_graph", False):
        return None
    primary = getattr(layer, "primary", None)
    supports = getattr(primary, "supports_resolve", None)
    if supports is None:
        supports = hasattr(primary, "resolves")
    if supports and hasattr(primary, "resolves"):
        return lambda sym: primary.resolves(sym)
    if degradation is not None:
        degradation.note(
            "symbol-reference drift check disarmed — the wired code graph exposes no "
            "authoritative existence query (GR.S2 named hook: wire an `exists` query)")
    return None


# --------------------------------------------------------------- AUDIT (output mode a, read-only)
def audit_text(text: str, *, path: str = "", facts: Optional[CodeFacts] = None,
               resolve: Optional[Callable[[str], bool]] = None,
               degradation: Optional[AuditDegradation] = None) -> List[Finding]:
    """Audit one doc's TEXT against the code (read-only). Returns every :class:`Finding`, sorted by
    line then checker. Pure given ``facts`` + ``text`` (+ an optional graph ``resolve`` predicate for
    the symbol check). This writes nothing — it is the default, read-only output mode.

    ``degradation`` (D5) is an optional collector: pass one and it comes back naming any check that
    did NOT run this audit, so the caller can render the result honestly (see
    :func:`render_findings`). Omit it and the behaviour is byte-identical to before."""
    facts = facts or gather_facts()
    lines = text.splitlines()
    sections = _sections(lines)
    code = _code_spans(lines)
    commands = _command_spans(lines)
    out: List[Finding] = []
    out += _check_skill_count(lines, sections, facts)
    out += _check_commands(lines, sections, code, commands, facts)
    out += _check_command_form(lines, sections, code, facts,
                               resolve_command_route(text, path))
    out += _check_install(lines, sections, code, facts)
    out += _check_version(lines, sections, code, facts)
    out += _check_symbols(lines, sections, code, facts, resolve, degradation)
    out.sort(key=lambda f: (f.line, f.checker))
    return out


def audit_doc(path: Any, *, facts: Optional[CodeFacts] = None,
              resolve: Optional[Callable[[str], bool]] = None,
              degradation: Optional[AuditDegradation] = None) -> List[Finding]:
    """Read a doc and :func:`audit_text` it. A missing/unreadable file raises ``OSError`` (the
    caller decides whether to skip); an audited file that is fine returns an empty list."""
    text = Path(path).read_text(encoding="utf-8")
    return audit_text(text, path=str(path), facts=facts, resolve=resolve,
                      degradation=degradation)


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


def render_findings(path: str, findings: Sequence[Finding],
                    degradation: Optional[AuditDegradation] = None) -> str:
    """A human/CI-readable audit report for ONE doc, highlighting the stale sections.

    D5 — when a check did NOT run, the verdict is DEGRADED, never "OK": the audit's "OK" is a claim
    that every check ran and found nothing, and a disarmed checker has not earned it."""
    degraded = degradation is not None and degradation.degraded
    if not findings:
        if degraded:
            return (f"docsync audit · {path}: DEGRADED — no discrepancy found, but the audit was "
                    f"INCOMPLETE.\n  {degradation.banner()}")
        return f"docsync audit · {path}: OK — every claim matches the code."
    stale = stale_sections(findings)
    lines = [f"docsync audit · {path}: {len(findings)} discrepancy(ies)"]
    if degraded:
        lines.append(f"  {degradation.banner()}")
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
    from .govern.trust import CLI_SURFACE

    def commit() -> None:
        p.write_text(new_text, encoding="utf-8")

    prompt = (f"mokata docsync · reconcile {edits} doc↔code discrepancy(ies) in {path}:\n"
              f"{diff}\n\nApply these edits to the doc?")
    outcome = WriteGate(ledger=ledger).submit(
        WriteRequest("config", str(p), content=new_text, actor="docsync",
                     tool="docsync", surface=CLI_SURFACE),
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
          include_internal: bool = False,
          degradation: Optional[AuditDegradation] = None) -> Dict[str, List[Finding]]:
    """Sweep + audit the doc tree (targeting mode ii). With ``changed_symbols`` it narrows to the
    drift set (docs that reference a changed symbol); without, it audits the whole public doc tree.
    Returns ``{doc_path: findings}`` for docs with ≥1 finding (a clean doc is omitted).

    ``degradation`` (D5) collects any check that did NOT run across the sweep — pass it to
    :func:`render_sweep` so an unchecked sweep can never read as a clean sweep."""
    facts = facts or gather_facts()
    docs = (drift_docs(root, changed_symbols, include_internal=include_internal)
            if changed_symbols else find_docs(root, include_internal=include_internal))
    out: Dict[str, List[Finding]] = {}
    for d in docs:
        try:
            findings = audit_doc(d, facts=facts, resolve=resolve, degradation=degradation)
        except OSError:
            continue
        if findings:
            out[d] = findings
    return out


def render_sweep(results: Dict[str, List[Finding]],
                 degradation: Optional[AuditDegradation] = None) -> str:
    """A human/CI-readable multi-doc audit report.

    D5 — the sweep's "OK" is the loudest lie in this module: it is printed when the results dict is
    EMPTY, and a sweep whose command-name checker was disarmed produces an empty dict having checked
    no command at all. A degraded sweep now says DEGRADED and names what it skipped."""
    degraded = degradation is not None and degradation.degraded
    if not results:
        if degraded:
            return ("docsync sweep: DEGRADED — no discrepancy found, but the sweep was "
                    f"INCOMPLETE.\n  {degradation.banner()}")
        return "docsync sweep: OK — every audited doc matches the code."
    total = sum(len(v) for v in results.values())
    lines = [f"docsync sweep: {total} discrepancy(ies) across {len(results)} doc(s)"]
    if degraded:
        lines.append(f"  {degradation.banner()}")
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
