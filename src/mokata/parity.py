"""Stage 54e — the command-surface coverage matrix (single source of truth).

The HARD RULE of 0.0.5 is that every USER-FACING mokata capability is reachable from
*inside* Claude Code — as a `/mokata:…` slash command and/or a native MCP tool — not
CLI-only. The CLI stays the secondary "use-anywhere" surface; the harness is primary.

This module is the declarative mapping that makes that rule self-enforcing. For every
CLI subcommand it records the Claude Code surface(s) it has — a slash command, MCP
tool(s), or an explicit exemption with a one-line rationale. The CLI command set is
DERIVED from the live argparse parser (`build_parser`), never hand-listed, so a new
command can't quietly skip the matrix. The Stage 54e parity test cross-checks the two:
every subcommand must have a declared surface OR an explicit exemption, or the build fails.

Inviolables it preserves: read-only inspection → MCP "read" tools; durable writes →
human-gated MCP "write" tools (the universal WriteGate — secret-scan + human gate + audit);
workflow/interactive phases → `/mokata:` slash commands. No engine logic lives here.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple


@dataclass(frozen=True)
class CommandSurface:
    """How one CLI subcommand is reachable from inside Claude Code.

    A command is *covered* when it has at least one in-harness surface (a slash command
    and/or an MCP tool) OR an explicit `exempt` rationale. `exempt` is reserved for the
    install/diagnostic PLUMBING that is intentionally CLI-or-hook — never a silent gap.
    """

    command: str
    slash: Tuple[str, ...] = ()          # /mokata:<name> slash command(s)
    mcp_read: Tuple[str, ...] = ()       # MCP read tool(s) (safe; expose data directly)
    mcp_write: Tuple[str, ...] = ()      # MCP write tool(s) (human-gated; WriteGate)
    exempt: str = ""                     # non-empty => intentionally CLI/hook-only, with reason
    note: str = ""                       # why this surface choice (docs + reviewers)

    @property
    def mcp(self) -> Tuple[str, ...]:
        return self.mcp_read + self.mcp_write

    @property
    def in_harness(self) -> bool:
        """Reachable from inside Claude Code (slash command and/or MCP tool)."""
        return bool(self.slash) or bool(self.mcp)

    @property
    def covered(self) -> bool:
        """Has an in-harness surface OR a declared exemption (never a silent gap)."""
        return self.in_harness or bool(self.exempt)


# The skill phases mokata exposes as their own `/mokata:<skill>` slash commands. `run`
# dispatches any of these standalone, so its in-harness surface IS this set (no `run.md`).
_SKILL_SLASH = (
    "brainstorm", "refine", "onboard", "spec", "test", "develop", "review",
    "debug", "optimize", "bug", "ship", "version",
)


def _surfaces() -> List[CommandSurface]:
    """The declarative matrix, in roughly CLI-help order. Edit HERE to add a surface."""
    return [
        # --- workflow phases & dispatchers → /mokata: slash commands -------------------
        CommandSurface("brainstorm", slash=("brainstorm",),
                       note="pre-spec exploration is an interactive phase → slash command"),
        CommandSurface("onboard", slash=("onboard",),
                       note="guided typed-knowledge capture is interactive → slash command"),
        CommandSurface("run", slash=_SKILL_SLASH,
                       note="dispatcher — every skill it runs standalone has its own "
                            "/mokata:<skill> slash command"),
        CommandSurface("enter", slash=("enter",),
                       note="entering the pipeline at a phase is a workflow → slash command"),
        CommandSurface("exec", slash=("exec",),
                       note="choosing sequential/parallel execution is interactive → slash"),
        CommandSurface("chain", slash=("chain",),
                       note="planning a manual skill chain is a workflow → slash command"),
        CommandSurface("playbook", slash=("playbook",),
                       note="running the full v1 story end-to-end is a workflow → slash"),
        CommandSurface("resume", slash=("resume",),
                       note="resuming a run (read-only preview, gates still apply) → slash"),
        CommandSurface("skill", slash=("skill",),
                       note="authoring a skill (RED-GREEN-for-docs, human-gated write) → "
                            "slash drives the gated author path"),
        CommandSurface("upgrade", slash=("upgrade",),
                       note="upgrading mokata (human-gated pip / plugin steps) → slash "
                            "drives the gated path"),
        CommandSurface("version", slash=("version",),
                       note="version + how-to-update (offline; opt-in check) → slash"),
        CommandSurface("init", slash=("init",), mcp_write=("init",),
                       note="scaffold config — gated MCP write (preview→approve) + slash; the "
                            "interactive first-run wizard runs through this surface (Stage 56)"),
        CommandSurface("setup", slash=("setup",),
                       note="guided first-run setup wizard — detect → ask profile/what-to-wire → "
                            "wire WITH APPROVAL (init + MCP + hooks); the gated install plumbing "
                            "now has an in-harness surface (Stage 56) → slash drives the gated "
                            "path"),
        CommandSurface("tour", slash=("tour",), mcp_read=("tour",),
                       note="60-second read-only demo (graph query, memory recall, gate catch) "
                            "→ read tool + slash (Stage 56)"),
        CommandSurface("reconfigure", slash=("reconfigure",), mcp_write=("reconfigure",),
                       note="re-runnable reconfigure wizard — change what's wired later "
                            "(add/remove integration, switch backend, change profile); gated + "
                            "idempotent + reversible → slash + gated MCP write (Stage 56b)"),

        # --- read-only inspection → MCP read tools -------------------------------------
        CommandSurface("query", mcp_read=("query",),
                       note="structural code query (graph/grep) → read tool"),
        CommandSurface("status", mcp_read=("status",), note="stack summary → read tool"),
        CommandSurface("doctor", mcp_read=("doctor",), note="config diagnosis → read tool"),
        CommandSurface("coverage", mcp_read=("coverage",),
                       note="capability coverage + gaps → read tool"),
        CommandSurface("budget", mcp_read=("budget",), note="token savings → read tool"),
        CommandSurface("audit", mcp_read=("audit",), mcp_write=("audit_share",),
                       note="audit ledger → read tool (`audit`; `team=true` reads the SHARED "
                            "team-wide log — Stage 71); publishing local entries to the team's "
                            "own shared log → gated write (`audit_share`: append-only, per-actor, "
                            "namespaced; secret-scanned egress; NO telemetry, the team's storage "
                            "only)"),
        CommandSurface("preview", mcp_read=("preview",),
                       note="pipeline dry-run → read tool"),
        CommandSurface("progress", slash=("progress",), mcp_read=("progress", "lanes"),
                       note="run tracker + parallel lanes → read tools + slash (Stage 54d)"),
        CommandSurface("rules", mcp_read=("rules",),
                       note="4-tier rules + budgets + gated proposals → read tool"),
        CommandSurface("skills", mcp_read=("skills",),
                       note="skill/command catalog → read tool"),
        CommandSurface("suggest", mcp_read=("suggest",),
                       note="context-aware command suggestion (never runs) → read tool"),
        CommandSurface("lat-check", mcp_read=("lat_check",),
                       note="@lat anchor / concept-drift scan → read tool"),
        CommandSurface("index", mcp_read=("index_status",),
                       note="freshness-index STATUS (read-only diff; rebuild stays CLI) → "
                            "read tool"),
        CommandSurface("baseline", mcp_read=("baseline",),
                       note="test-suite green/red at baseline → read tool"),
        CommandSurface("ci-check", mcp_read=("ci_check",),
                       note="mokata-as-a-PR-check (completeness gate + spec-awareness over a "
                            "change's files) → read tool (the GitHub Action calls the CLI; the "
                            "tool surfaces the same verdict + comment body in-harness, Stage 58)"),
        CommandSurface("sessions", mcp_read=("sessions",),
                       note="list past + active runs → read tool"),
        CommandSurface("decompose", slash=("decompose",), mcp_read=("decompose",),
                       note="propose an independent-subtask split of the spec ACs (read-only) "
                            "→ read tool + slash; the confirm + fan-out stay the human-gated "
                            "`decompose --run` / exec flow (Stage 54f)"),
        CommandSurface("watch", slash=("watch",), mcp_read=("watch",),
                       note="self-contained run dashboard → read tool + slash (Stage 54d)"),
        CommandSurface("govern", slash=("govern",), mcp_read=("govern",),
                       note="governed-state view → read tool + slash (Stage 54d)"),

        # --- read + durable write → MCP read tool(s) + human-gated MCP write tool(s) ----
        CommandSurface("config", mcp_read=("config_get",), mcp_write=("config_set",),
                       note="config get → read tool; config set → gated write (secret "
                            "hard-block in the manifest is absolute)"),
        CommandSurface("memory", mcp_read=("recall",),
                       mcp_write=("remember", "memory_export", "memory_import",
                                  "apply_proposal"),
                       note="recall → read; remember/share/heal → gated writes"),
        CommandSurface("vault", slash=("vault",),
                       mcp_read=("vault_list", "vault_search", "vault_pull"),
                       mcp_write=("vault_push",),
                       note="list/search/pull → read; push → gated write"),
        CommandSurface("session", slash=("session",),
                       mcp_read=("session_list",),
                       mcp_write=("session_push", "session_pull", "session_name"),
                       note="portable tagged sessions (Stage 55a/55b): list → read (spans local "
                            "+ remote transports); push/pull/name → gated writes (secret-scanned "
                            "+ human-gated on EVERY transport — local/vault/postgres; content-hash "
                            "verified + cross-codebase mismatch surfaced on pull; rename never a "
                            "silent clobber)"),
        CommandSurface("export", mcp_read=("export_preview",), mcp_write=("export_stack",),
                       note="export-preview → read tool; export → gated write"),
        CommandSurface("import", mcp_write=("import_stack",),
                       note="apply a shared stack → gated write (untrusted content)"),
        CommandSurface("stacks", slash=("stacks",),
                       mcp_read=("stacks_list", "stacks_search", "stacks_show"),
                       mcp_write=("stacks_install",),
                       note="community stacks & skill marketplace (Stage 70): list/search/show a "
                            "CURATED versioned index → read tools; install → the human-gated, "
                            "secret-scanned adopt path (reuses scan + apply_manifest). NO hosted "
                            "marketplace — publish is over git/the vault, discover is a reviewable "
                            "index.json, install is gated. Degrade-clean (no index/source → a "
                            "clear message)"),
        CommandSurface("team", slash=("team",),
                       note="zero-setup team sync (Stage 69): adopt a shared governed stack + "
                            "(optionally) point shared memory/sessions at the team's OWN managed "
                            "Postgres via an env-var DSN → slash drives the human-gated, "
                            "secret-scanned adopt/connect (mokata hosts nothing; the DSN secret "
                            "is never stored). status is read-only"),
        CommandSurface("spec-check", mcp_write=("spec_check",),
                       note="regression guard → gated (deviation gate on a conflict)"),
        CommandSurface("reset", mcp_write=("reset",),
                       note="remove mokata state → gated write (propose→approve)"),

        # --- install / diagnostic PLUMBING → intentionally CLI-or-hook (each with reason) -
        CommandSurface("unsetup", exempt=(
            "install plumbing — reverses `setup`; a harness-config + filesystem teardown "
            "run from the shell, the mirror of `setup`.")),
        CommandSurface("mcp", exempt=(
            "diagnostic plumbing — discovers external MCP servers from .mokata/mcp.json and "
            "maps them to roles; introspects the harness wiring itself.")),
        CommandSurface("harness", exempt=(
            "diagnostic plumbing — prints the harness capability matrix (the boundary mokata "
            "runs inside); host introspection, not a user workflow.")),
        CommandSurface("route", exempt=(
            "diagnostic plumbing — resolves a capability to its concrete tool + fallback "
            "chain; internal routing introspection.")),
        CommandSurface("detect", exempt=(
            "diagnostic plumbing — probes tool presence on the host; an environment scan "
            "with no in-harness analogue.")),
        CommandSurface("validate", exempt=(
            "diagnostic plumbing — parses + validates the committed manifest; a lint/CI "
            "check, not a user workflow.")),
        CommandSurface("bench", exempt=(
            "diagnostic plumbing — measures local WALL-CLOCK latency of the hot paths against "
            "their budget (Stage 67); a perf check run from the shell, read-only, with no "
            "in-harness workflow analogue (like `validate`/`detect`). Distinct from `budget` "
            "(tokens), which has an MCP read surface.")),
        CommandSurface("release-check", exempt=(
            "release plumbing — a pure/offline preflight asserting every version field == "
            "the intended tag; run from the shell by `release.sh` (and CI) during a release "
            "cut, the version mirror of `validate`. Not a user workflow.")),
        CommandSurface("bootstrap", exempt=(
            "hook plumbing — prints the SessionStart briefing; invoked BY the SessionStart "
            "hook, never typed by a user.")),
    ]


# command name -> its surface (single source of truth).
SURFACE_MATRIX: Dict[str, CommandSurface] = {s.command: s for s in _surfaces()}


def cli_command_names() -> Set[str]:
    """The live set of `mokata <subcommand>` names, derived from the argparse parser.

    Never hand-listed — a new subcommand appears here automatically (and the parity test
    then demands a matrix surface for it).
    """
    from .cli import build_parser
    parser = build_parser()
    for action in parser._actions:
        if action.__class__.__name__ == "_SubParsersAction":
            return set(action.choices)
    return set()


@dataclass
class ParityReport:
    """The result of cross-checking the live CLI against the matrix."""

    uncovered: List[str] = field(default_factory=list)     # CLI cmd, no surface + no exempt
    undeclared: List[str] = field(default_factory=list)    # CLI cmd with no matrix entry
    stale: List[str] = field(default_factory=list)         # matrix entry, not a real command

    @property
    def ok(self) -> bool:
        return not (self.uncovered or self.undeclared or self.stale)

    def render(self) -> str:
        if self.ok:
            n = len(SURFACE_MATRIX)
            return (f"command-surface parity OK — all {n} CLI command(s) reachable inside "
                    f"Claude Code or explicitly exempted.")
        lines = ["command-surface parity FAILED:"]
        if self.undeclared:
            lines.append(f"  no matrix entry (add a surface or an exemption): "
                         f"{sorted(self.undeclared)}")
        if self.uncovered:
            lines.append(f"  declared but UNREACHABLE in-harness and not exempted: "
                         f"{sorted(self.uncovered)}")
        if self.stale:
            lines.append(f"  matrix entry for a command that no longer exists: "
                         f"{sorted(self.stale)}")
        return "\n".join(lines)


def verify_parity() -> ParityReport:
    """Cross-check the live argparse command set against the declared matrix.

    FAILS (a non-empty report) if any command has neither an in-harness surface nor an
    explicit exemption, or if the matrix references a command that no longer exists.
    """
    cli = cli_command_names()
    declared = set(SURFACE_MATRIX)
    report = ParityReport()
    report.undeclared = sorted(cli - declared)
    report.stale = sorted(declared - cli)
    for name in sorted(cli & declared):
        if not SURFACE_MATRIX[name].covered:
            report.uncovered.append(name)
    return report


def declared_mcp_tools() -> Set[str]:
    """Every MCP tool name the matrix references (read + write)."""
    out: Set[str] = set()
    for s in SURFACE_MATRIX.values():
        out.update(s.mcp)
    return out
