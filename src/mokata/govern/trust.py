"""K3 — the per-tool trust dial, and the ONE seam that carries it to a write.

Each write surface and each wired tool can be set to a trust level: read-only (cannot write at all),
propose-only (never AUTO-approved — an explicit human decision is required), or gated-write (the
default: human-gated, and a non-interactive run may stand in with `assume_yes`). The levels are read
from the manifest's `settings.trust` and ENFORCED by the WriteGate.

Keying — a surface default, overridable per tool (SI.4)
------------------------------------------------------
`settings.trust` stays a FLAT `{key: level}` map (no schema change). A key is either a SURFACE
(`mcp`, `cli`) or a TOOL name (`remember`, `session_push`, …), and the tool wins:

    trust:
      mcp:      propose-only     # the default for every write arriving over MCP
      remember: read-only        # …except this one, which may not write at all

Resolution is: the tool's own level, else the surface's, else `gated-write`. That means a team can
govern a whole surface in one line and still carve out a single tool, and a repo that sets nothing is
byte-identical to before.

What `propose-only` MEANS (SI.4 — the semantic fix)
---------------------------------------------------
It means **no auto-approval; an explicit human decision is required.** It does NOT mean "prompt at a
TTY" — a prompt is merely one WAY to obtain that decision, and it is the only way that exists on a
CLI. The MCP server is stdio-bound to the harness and owns no TTY, so there the human decision is
obtained the way SI.3 built it: a human MINTS an approval out-of-band with `mokata approve <id>`,
content-hash-bound to that exact write, single-use and session-scoped. That is explicit human consent
by any honest reading, so it SATISFIES propose-only, and the gate accepts it as `human_approved`.

Treating "propose-only" as "force a TTY prompt" is what made the rung a dead end on MCP: the gate set
`assume_yes=False`, `prompt.read_yes_no` found no TTY, failed CLOSED, and declined EVERY write —
including ones a human had genuinely approved.

The honest ladder this produces on MCP (P22 — say what is NOT true)
------------------------------------------------------------------
SI.3 already forces every MCP write through propose → human mints → redeem. So on that surface
`propose-only` and `gated-write` coincide, and the real dial is:

    read-only  ▸  write-allowed        (propose-only == gated-write == the SI.3 floor)

We document that rather than implying a three-rung ladder MCP does not have. Setting `propose-only`
on MCP is still meaningful — it pins the floor SI.3 built, so a future loosening cannot silently
un-pin it — but it buys no EXTRA teeth today. Giving it extra teeth (a second approval, narrowed
write kinds) would be new product; it is filed, not built.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

READ_ONLY = "read-only"
PROPOSE_ONLY = "propose-only"
GATED_WRITE = "gated-write"
TRUST_LEVELS = (READ_ONLY, PROPOSE_ONLY, GATED_WRITE)
DEFAULT_TRUST = GATED_WRITE
TRUST_SETTINGS_KEY = "trust"

# The write SURFACES a `settings.trust` key may name (alongside the tool names). These are the two
# ways a durable write can originate: a tool call over MCP, or a command the human ran themselves.
MCP_SURFACE = "mcp"
CLI_SURFACE = "cli"
SURFACES = (MCP_SURFACE, CLI_SURFACE)

# The refusal code a read-only tool call reports. Named, like SI.3's refusals, so the answer says
# WHICH way it failed rather than a generic "no".
REFUSED_READ_ONLY = "read-only"


@dataclass
class TrustPolicy:
    """The resolved `settings.trust` map: which tools/surfaces may write, and how freely."""

    levels: Dict[str, str] = field(default_factory=dict)
    default: str = DEFAULT_TRUST

    def level(self, tool: Any = None, surface: Any = None) -> str:
        """The trust level governing a write by `tool` arriving over `surface`.

        The tool's OWN level wins over the surface default, so one carve-out never forces a team to
        restate the whole surface. Neither set -> the global default (gated-write)."""
        if tool and tool in self.levels:
            return self.levels[tool]
        if surface and surface in self.levels:
            return self.levels[surface]
        return self.default

    def can_write(self, tool: Any = None, surface: Any = None) -> bool:
        return self.level(tool, surface) != READ_ONLY

    def requires_human(self, tool: Any = None, surface: Any = None) -> bool:
        return self.level(tool, surface) in (PROPOSE_ONLY, GATED_WRITE)

    def allows_auto_approve(self, tool: Any = None, surface: Any = None) -> bool:
        """Whether a STAND-IN approval (`assume_yes`) suffices. Only gated-write allows one;
        propose-only demands a real human decision — which a verified, human-minted SI.3 approval
        supplies (see `WriteGate.submit(human_approved=...)`)."""
        return self.level(tool, surface) == GATED_WRITE

    @classmethod
    def from_manifest(cls, manifest: Any) -> "TrustPolicy":
        levels = manifest.setting(TRUST_SETTINGS_KEY, {}) or {}
        return cls(levels=dict(levels) if isinstance(levels, dict) else {})

    @classmethod
    def from_surface(cls, surface: Any) -> "TrustPolicy":
        """The policy for a repo. Degrade-clean: a repo with no manifest (or an unreadable one) is
        an EMPTY policy, i.e. the gated-write default — a broken dial must never fail OPEN into
        read-only-blocks-everything, nor into ungoverned writes."""
        try:
            return cls.from_manifest(surface.manifest)
        except (AttributeError, TypeError, ValueError):
            return cls()


def trust_for(manifest: Any, tool: str) -> str:
    return (manifest.setting(TRUST_SETTINGS_KEY, {}) or {}).get(tool, DEFAULT_TRUST)


@dataclass(frozen=True)
class WritePolicy:
    """THE SEAM. One value, threaded from the surface that originates a write down to the WriteGate
    that actually performs it — however many layers sit between.

    It carries the three things a gate deep in the stack cannot otherwise know:
      * `trust`          — the dial (nobody below the surface can read the manifest for it);
      * `tool`/`surface` — WHO is writing, which is what the dial keys on. Without this the gate sees
                           an anonymous request and the dial has nothing to resolve;
      * `human_approved` — whether an explicit human decision has ALREADY been verified for this exact
                           write (SI.3). This is what lets a propose-only write commit on a surface
                           with no TTY, and it is deliberately NOT `assume_yes`: a stand-in
                           auto-approval and a verified human decision must never be the same flag.

    A `None` policy anywhere means "ungoverned, as before" — every pre-SI.4 caller is byte-identical.
    """

    trust: Optional[TrustPolicy] = None
    tool: Optional[str] = None
    surface: Optional[str] = None
    human_approved: bool = False

    @classmethod
    def for_mcp(cls, surface_obj: Any, tool: str, *,
                human_approved: bool = False) -> "WritePolicy":
        """The policy for a write arriving over MCP, resolved once at that boundary."""
        return cls(trust=TrustPolicy.from_surface(surface_obj), tool=tool,
                   surface=MCP_SURFACE, human_approved=human_approved)

    def approved(self) -> "WritePolicy":
        """The same policy, with the human's verified decision attached (SI.3 redemption)."""
        return WritePolicy(trust=self.trust, tool=self.tool, surface=self.surface,
                           human_approved=True)

    def level(self) -> str:
        return (self.trust or TrustPolicy()).level(self.tool, self.surface)

    def can_write(self) -> bool:
        return (self.trust or TrustPolicy()).can_write(self.tool, self.surface)


# The convenience accessors the gate-constructing call sites use, so none of them has to spell out
# `policy.trust if policy else None` (and none can get that conditional subtly wrong).

def policy_trust(policy: Optional[WritePolicy]) -> Optional[TrustPolicy]:
    return policy.trust if policy is not None else None


def policy_approved(policy: Optional[WritePolicy]) -> bool:
    return bool(policy is not None and policy.human_approved)


def policy_tool(policy: Optional[WritePolicy], default: Optional[str] = None) -> Optional[str]:
    """The tool identity to stamp on a WriteRequest: the ORIGINATING tool when a policy threaded one
    in (an MCP call keeps its own name all the way down), else the call site's own identity."""
    if policy is not None and policy.tool:
        return policy.tool
    return default


def policy_surface(policy: Optional[WritePolicy],
                   default: Optional[str] = None) -> Optional[str]:
    if policy is not None and policy.surface:
        return policy.surface
    return default
