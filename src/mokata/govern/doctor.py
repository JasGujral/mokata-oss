"""K5 — `mokata doctor`.

Validate the stack and report problems: an invalid manifest, capabilities with no present
provider (missing deps), broken adapters, role conflicts (resolved by precedence), bad
trust levels, and oversized rule tiers. Read-only diagnosis built on the existing
schema validator, router, adapter contract, and rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List

from .. import schema
from ..adapters import overlapping_capabilities, validate_adapter
from ..manifest import ManifestError
from .rules import load_rules, validate_caps
from .trust import TRUST_LEVELS, TRUST_SETTINGS_KEY


@dataclass
class DoctorFinding:
    severity: str            # "error" | "warning" | "info"
    code: str
    detail: str


@dataclass
class DoctorReport:
    findings: List[DoctorFinding] = field(default_factory=list)
    graph_hint: str = ""          # Stage 25 Part B — actionable code-graph guidance
    mode_report: str = ""         # TM.S1 — run mode + the team-readiness preflight
    degrade_report: str = ""      # D5 — what degraded this session (the emitted-notice registry)

    @property
    def errors(self) -> List[DoctorFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self, *, ascii_only: bool = False, color: bool = False) -> str:
        """Presentation only — the same findings, laid out through the shared box/table + colour
        helpers so doctor reads like every other mokata surface. `ascii_only` swaps the Unicode
        box for a pure-ASCII one; `color` (opt-in, gated) tints the pass/fail status line. The
        content (every finding's severity/code/detail, the graph hint) and `ok`/exit are unchanged.
        Both default OFF so existing callers (and MCP/JSON consumers) get deterministic plain text."""
        from ..legibility import green, red, table
        if not self.findings:
            body = green("mokata doctor: all checks passed.", color=color)
        else:
            rows = [(f.severity, f.code, f.detail) for f in self.findings]
            grid = table(("severity", "code", "detail"), rows, ascii_only=ascii_only)
            status = (green("OK", color=color) if self.ok
                      else red("PROBLEMS FOUND", color=color))
            body = "\n".join([f"mokata doctor: {len(self.findings)} finding(s):", grid, status])
        if self.mode_report:
            body += f"\n\n{self.mode_report}"
        if self.degrade_report:
            body += f"\n\n{self.degrade_report}"
        if self.graph_hint:
            body += f"\n{self.graph_hint}"
        return body


@dataclass
class CoverageRow:
    kind: str        # "wiring" (harness capability) | "capability" (declared manifest need)
    name: str        # the harness capability, or the capability need
    status: str      # "pass" | "degraded" | "fail"
    detail: str


@dataclass
class CoverageMatrix:
    """R13 — the COMPLETE coverage matrix: every harness wiring point + every declared
    capability, each classified pass / degraded / fail. Read-only and presentation-only; it
    reuses the SAME `router.resolve()` diagnose() uses (one source of truth — never a second,
    divergent derivation of capability status)."""

    rows: List[CoverageRow] = field(default_factory=list)

    def render(self, *, ascii_only: bool = False, color: bool = False) -> str:
        """Render through the A4 legibility.table() layer, colour-gated (green pass / dim
        degraded / red fail). `ascii_only` gives a pure-ASCII, zero-escape table when piped."""
        from ..legibility import dim, green, red, table
        color = color and not ascii_only

        def paint(status: str) -> str:
            if status == "pass":
                return green(status, color=color)
            if status == "fail":
                return red(status, color=color)
            return dim(status, color=color)

        rows = [(r.kind, r.name, paint(r.status), r.detail) for r in self.rows]
        grid = table(("kind", "capability", "status", "detail"), rows, ascii_only=ascii_only)
        n_fail = sum(1 for r in self.rows if r.status == "fail")
        n_deg = sum(1 for r in self.rows if r.status == "degraded")
        header = (f"mokata doctor — coverage matrix "
                  f"({len(self.rows)} checks, {n_fail} fail, {n_deg} degraded):")
        return "\n".join([header, grid])


def _classify_capability(surface: Any, need: str) -> tuple:
    """Classify one declared capability need using the SAME `router.resolve()` diagnose() uses
    — the (status, detail) is READ from the resolver's own Resolution, never re-derived a second
    way (the T2 'no re-derivation' lesson). Degrades clean: an unroutable/bad capability is
    'fail', never a crash. The 'fail' case is exactly what diagnose() flags missing-provider /
    bad-capability for, so the two can never diverge."""
    try:
        res = surface.router.resolve(need)
    except ManifestError as exc:
        return "fail", str(exc)                      # diagnose(): "bad-capability"
    if not res.available:
        tried = ", ".join(t for t, _ in res.attempted) or "none"
        return "fail", f"no present provider (tried: {tried})"   # diagnose(): "missing-provider"
    if res.degraded:
        return "degraded", res.summary()             # available via a fallback — observable degrade
    return "pass", res.summary()


def coverage_matrix(surface: Any) -> CoverageMatrix:
    """Build the R13 coverage matrix: every declared harness wiring point (HARNESS_CAPABILITIES)
    + every declared capability need, classified pass / degraded / fail. Capability rows reuse
    `_classify_capability` (the same resolver diagnose() uses), so the matrix and diagnose() are
    one source of truth. Read-only; degrades clean (never crashes on an unresolvable capability)."""
    from ..harness import HARNESS_CAPABILITIES, claude_code_harness

    rows: List[CoverageRow] = []

    # 1) harness wiring points — the reference harness supports all four; a harness LACKING one
    #    degrades clean (the boundary returns degraded, never a crash), so absent -> 'degraded'.
    h = claude_code_harness()
    for cap in HARNESS_CAPABILITIES:
        supported = h.supports(cap)
        rows.append(CoverageRow(
            "wiring", cap,
            "pass" if supported else "degraded",
            f"harness '{h.name}' " + ("wires" if supported
                                      else "lacks — degrades clean") + f" '{cap}'"))

    # 2) capability needs — SAME resolver diagnose() uses (one source of truth).
    for need in surface.manifest.capabilities:
        status, detail = _classify_capability(surface, need)
        rows.append(CoverageRow("capability", need, status, detail))

    return CoverageMatrix(rows)


def calibration_drift_findings(surface: Any) -> List[DoctorFinding]:
    """R11 — surface any logged token-estimate calibration DRIFT: a real `actual` that exceeded
    the chars/4 estimate's safety margin (the estimate under-counted, threatening the 2k budget
    claim). Read-only and guarded: any ledger issue -> no findings, never an exception, and no
    ledger dirs are created (only reads an existing ledger). A 'warning' (observability, not a
    setup-breaking error) so doctor's ok/exit semantics stay reserved for real blockers."""
    try:
        import os

        from .. import TEMP_LOCAL_DIRNAME
        from .ledger import AUDIT_DIRNAME, LEDGER_FILENAME, AuditLedger
        from .tokens import CALIBRATION_KIND
        path = os.path.join(surface.mokata_dir, TEMP_LOCAL_DIRNAME, AUDIT_DIRNAME,
                            LEDGER_FILENAME)
        if not os.path.exists(path):
            return []                       # nothing logged yet — no drift to report
        entries = AuditLedger(path).entries()
    except (OSError, ValueError):
        # An unreadable (OSError) or unparseable (json.JSONDecodeError <: ValueError) ledger. No
        # drift can be reported, and the INTEGRITY of that same ledger is reported loudly next door
        # by `ledger_integrity_findings` — so this stays quiet rather than say the same thing twice.
        return []
    out: List[DoctorFinding] = []
    for e in entries:
        if e.get("kind") != CALIBRATION_KIND or not e.get("over_margin"):
            continue
        ctx = e.get("context", "?")
        ratio = e.get("ratio")
        ratio_txt = f"{ratio:.2f}" if isinstance(ratio, (int, float)) else str(ratio)
        out.append(DoctorFinding(
            "warning", "calibration-drift",
            f"token estimate under-counted in '{ctx}': actual {e.get('actual')} > "
            f"estimate {e.get('estimate')} (ratio {ratio_txt}) — chars/4 safety margin blown"))
    return out


def ledger_integrity_findings(surface: Any) -> List[DoctorFinding]:
    """MS.S3 — walk the audit ledger's hash-chain and surface a BREAK (a tampered/removed/truncated
    entry): the ledger is the proof substrate for every gate claim (P16), so a broken chain means
    the trust record is no longer verifiable. This is the `verify` CLI surface — the doctor, the
    existing read-only health command that already reads the ledger, is the least-surface home (no
    new subcommand). Read-only and guarded: any ledger issue -> no finding, never an exception, and
    no ledger dirs are created (only reads an existing ledger). A legacy pre-chain ledger verifies
    intact (its unhashed prefix is a boundary, not a break), so this never false-positives.

    D5 — the WORST CASE this used to hide: a corrupt/truncated ledger LINE made `verify()` raise
    (`json.JSONDecodeError` out of `AuditLedger.entries`), the broad catch returned `[]`, and `[]`
    from this function means exactly one thing to every reader — "the chain is intact". So the one
    state a tamper-evident record exists to reveal (an unverifiable ledger) rendered as a clean bill
    of health. It now reports `ledger-unverifiable` as an ERROR: doctor must not crash, but "I could
    not check" is a finding, never a pass."""
    try:
        import os

        from .. import TEMP_LOCAL_DIRNAME
        from .ledger import AUDIT_DIRNAME, LEDGER_FILENAME, AuditLedger
        path = os.path.join(surface.mokata_dir, TEMP_LOCAL_DIRNAME, AUDIT_DIRNAME,
                            LEDGER_FILENAME)
        if not os.path.exists(path):
            return []                       # nothing logged yet — no chain to verify
        report = AuditLedger(path).verify()
    except (OSError, ValueError) as exc:
        # OSError = the ledger could not be READ; ValueError (json.JSONDecodeError) = a line is
        # present but unparseable. Either way the hash-chain check DID NOT RUN.
        return [DoctorFinding(
            "error", "ledger-unverifiable",
            f"the audit ledger could NOT be verified ({type(exc).__name__}) — the hash-chain "
            f"tamper check did not run, so the trust record is UNVERIFIED. This is NOT a clean "
            f"chain: it is no answer at all. Restore/regenerate the audit ledger under "
            f"`.mokata/temp_local/`, then re-run `mokata doctor`")]
    if report.intact:
        return []
    return [DoctorFinding(
        "error", "ledger-chain-break",
        f"audit ledger hash-chain broken at seq {report.first_break_seq}: {report.reason} — "
        f"the tamper-evident trust record is compromised (`mokata doctor` verified the chain)")]


def render_degrade_report(*, ascii_only: bool = False) -> str:
    """D5 — WHAT SILENTLY DEGRADED THIS SESSION. The one place that question has an answer.

    Before D5 it had none: a degrade notice printed ONCE into a scrollback and was gone, so the
    user who scrolled past it — or who was never looking at the terminal, because the notice fired
    inside a hook — had no way to ask again. `degrade.emitted_notices()` remembers them; this
    renders them (P16: a degrade you can't see is a lie in the proof. P17: recovery starts with
    knowing something broke).

    IMPORTANT — what this can and cannot say. It reports the notices emitted IN THIS PROCESS. Most
    of mokata's degrades happen in the process the user is watching (a CLI command, an MCP tool
    call), so the honest phrasing is "this session", and the block says so rather than implying a
    durable, cross-process degrade history it does not have. A hook is a SEPARATE short-lived
    process: its notice went to the hook's stderr and is not in this registry. That is a real
    limitation, stated here rather than papered over — the durable cross-process record would be a
    ledger row, which D5 deliberately did not add (no new failure semantics).

    Empty is the common case, and it prints NOTHING: a doctor run on a healthy repo must not grow
    a permanent "0 degrades" line that trains the user to skip the section that matters."""
    from ..degrade import emitted_notices
    notices = emitted_notices()
    if not notices:
        return ""
    glyph = "[!]" if ascii_only else "⚠"
    lines = [f"{glyph} degraded this session ({len(notices)}) — a capability fell back to a floor:"]
    for notice in notices:
        lines.append(f"  {notice.render(ascii_only=ascii_only)}")
    return "\n".join(lines)


def diagnose(surface: Any) -> DoctorReport:
    findings: List[DoctorFinding] = []
    m = surface.manifest

    # 1) manifest schema validity
    for e in schema.validate_manifest(m.data):
        findings.append(DoctorFinding("error", "manifest", e))

    # 2) missing providers — capabilities that resolve to nothing present
    for need in m.capabilities:
        try:
            res = surface.router.resolve(need)
        except ManifestError as exc:
            findings.append(DoctorFinding("error", "bad-capability", str(exc)))
            continue
        if not res.available:
            tried = ", ".join(t for t, _ in res.attempted) or "none"
            findings.append(DoctorFinding(
                "error", "missing-provider",
                f"capability '{need}' has no present provider (tried: {tried})"))

    # 3) broken adapters — each tool must satisfy the adapter contract
    for tid, tool in m.tools.items():
        errs = validate_adapter({"name": tid, "provides": [tool.get("provides")],
                                 "kind": tool.get("kind"), "detect": tool.get("detect")})
        for e in errs:
            findings.append(DoctorFinding("error", "broken-adapter", f"{tid}: {e}"))

    # 4) role conflicts — >1 provider for one capability (resolved by precedence)
    for need, providers in overlapping_capabilities(m).items():
        findings.append(DoctorFinding(
            "warning", "role-conflict",
            f"capability '{need}' claimed by {providers} (resolved by precedence)"))

    # 5) config — rule caps + trust levels
    try:
        for e in validate_caps(load_rules(surface)):
            findings.append(DoctorFinding("error", "rules-cap", e))
    except (OSError, ImportError, AttributeError) as exc:
        # D5 — the rule TIERS could not be loaded, so their caps were never checked. Silently
        # skipping the check reads, from doctor's output, exactly like passing it: the user is told
        # nothing and infers the rules are within budget. "Could not check" is a finding.
        findings.append(DoctorFinding(
            "error", "rules-unverifiable",
            f"the rule tiers could NOT be loaded ({type(exc).__name__}) — their line caps were "
            f"NOT validated, so an over-cap tier would be invisible here. The rules injected into "
            f"your session are UNVERIFIED"))
    configured_trust = (m.setting(TRUST_SETTINGS_KEY, {}) or {})
    for tool, level in configured_trust.items():
        if level not in TRUST_LEVELS:
            findings.append(DoctorFinding(
                "error", "bad-trust",
                f"tool '{tool}' has invalid trust level '{level}'"))
    # SI.4 — the SURFACE TRUTH (P22). A dial the user has set must not imply teeth it does not have:
    # on MCP, SI.3 already forces every write through propose → a human mints → redeem, so
    # `propose-only` and `gated-write` land on identical behaviour there. Say so, rather than let the
    # three-rung name imply a three-rung ladder.
    if configured_trust:
        findings.append(DoctorFinding(
            "info", "trust-surfaces",
            "trust is enforced on the mcp write surface (per-tool level, else the `mcp` surface "
            "default, else gated-write). There, the real ladder is read-only ▸ write-allowed: "
            "propose-only == gated-write, because every MCP write already needs a human-minted "
            "`mokata approve <id>`. CLI writes carry their tool identity but are not yet governed "
            "by the dial."))

    # 6b) R11 — token-estimate calibration drift (a logged actual that blew the chars/4 margin).
    findings.extend(calibration_drift_findings(surface))

    # 6b-ii) MS.S3 — audit ledger hash-chain integrity (a broken/tampered/truncated trust record).
    findings.extend(ledger_integrity_findings(surface))

    # 6c) TM.S1 — run mode. Flag a hand-edited invalid `settings.mode` (read_mode stays safe/
    # local, but doctor must SEE it); the local default is never an error.
    try:
        from ..run_mode import is_valid_mode, stored_mode
        raw = stored_mode(surface)
        if raw is not None and not is_valid_mode(raw):
            findings.append(DoctorFinding(
                "error", "bad-mode",
                f"settings.mode is '{raw}' — not a valid run mode; expected local|team "
                f"(reading as local)"))
    except (ImportError, AttributeError, OSError):
        # Type (iv): the stored mode is UNREADABLE, and `read_mode` degrades to LOCAL for exactly
        # this case — so there is no hand-edited invalid value to flag. The mode itself is reported
        # (loudly, with the team verdict) by the `mode_report` block below, which is where a reader
        # looks for "what mode am I in"; a second notice here would say the same thing twice.
        pass

    # the run-mode surface: the current mode + the same team-readiness preflight `mode` shows,
    # plus (TM.S5) the ONE health verdict the badge/mode/in-chat use — a broken team connection
    # is never silent in doctor, and trouble carries the work-locally offer.
    #
    # D5 — built INCREMENTALLY, and never discarded. The old handler reset the whole block to ""
    # on any error, which threw away text ALREADY COMPUTED: the mode line, the preflight, and —
    # worst — the CM.S2 degrade notice ("this read came from the LOCAL fallback, not shared team
    # memory") and the CM.S4 local-only backlog. A failure in the LAST of those lines therefore
    # erased the loud notices the earlier ones had just earned, and doctor rendered no team section
    # at all: indistinguishable, to a reader, from "nothing to say about team".
    mode_report = ""
    try:
        from ..run_mode import mode_line, read_mode, team_preflight, TEAM
        mode_report = mode_line(surface)
        mode_report += "\n" + team_preflight(surface).render()
        if read_mode(surface) == TEAM:
            import os as _os
            from .. import team_health
            from ..degrade import resolve_read_routing
            verdict = team_health.check(surface, environ=_os.environ)
            mode_report += "\n" + team_health.status_block(verdict)
            # CM.S2 (C-2) — when a team-mode READ is actually served from the LOCAL fallback,
            # name it explicitly (env-var NAME + failure class), reusing the SAME cached verdict
            # above so doctor's read-degrade line can never disagree with the health badge.
            routing = resolve_read_routing(surface, environ=_os.environ)
            if routing.degraded and routing.notice is not None:
                mode_report += "\n" + routing.notice.render()
            # CM.S4 (C-4) — the local-only backlog: count + oldest age + last-failure class,
            # reusing the SAME `routing` verdict above so the pending count can never disagree
            # with the degrade notice (one routing input, no second probe).
            from ..flush_liveness import pending_status
            ps = pending_status(surface, routing=routing, environ=_os.environ)
            if ps is not None:
                mode_report += "\n" + ps.doctor_line()
    except (ImportError, OSError, AttributeError) as exc:
        # KEEP everything computed so far and APPEND the honest gap. Silence here would be read as
        # "the connection is fine"; it is not known to be anything.
        mode_report = (mode_report + "\n" if mode_report else "") + (
            f"⚠ team status could not be determined ({type(exc).__name__}) — treat the connection "
            f"as UNVERIFIED: mokata cannot say whether reads are served by shared team memory or "
            f"the LOCAL fallback. Run `mokata mode` / `mokata sync` once the store is reachable.")

    # 6) code-graph guidance (Stage 25 Part B) — actionable, not just status.
    hint = ""
    try:
        from ..knowledge import graph_guidance
        hint = graph_guidance(surface)
    except (ImportError, OSError):
        # Type (iv): the graph HINT is advisory prose ("wire a code graph to get X") — it is not a
        # check, has no verdict, and asserts nothing when absent. The code graph's own degrade (the
        # grep floor) is announced by `note_degraded("code-graph", ...)` at the query sites, so a
        # missing hint here hides nothing.
        hint = ""

    return DoctorReport(findings, graph_hint=hint, mode_report=mode_report,
                        degrade_report=render_degrade_report())
