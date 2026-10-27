"""R6 (0.0.26 Lane R) — an EXPLICIT model tier for every role mokata dispatches a subagent for.

THE RESEARCH (doc 113 §4, superpowers' subagent-driven development): use the cheapest model that
does the job for each role, put a MID-TIER FLOOR under reviewers ("the cheapest models routinely take
2-3× the turns"), use the TOP tier for the final review, always NAME the model, and keep a per-run
cost record. All of it inside the user's own Claude Code login: a tier is a Claude Code model alias
the harness resolves (`haiku` < `sonnet` < `opus`), never an API model id, never an API key.

THE ROLES and their defaults (`DEFAULTS`), each overridable in the manifest under
`settings.models.<role>` (`mokata config set settings.models.reviewer opus` — human-gated like
every config write, and refused below the role's floor):

    explorer        haiku   read-only searching and summarising
    implementer     sonnet  one task's code against its failing test
    debugger        opus    root-causing after the 3-fix breaker tripped
    reviewer        sonnet  a review round (floor: sonnet)
    final-reviewer  opus    the closing review before ship (floor: opus)

HOW IT IS ENFORCED, NOT JUST SAID. A mokata skill spawns a role's subagent with the Agent tool,
naming the tier in the tool's `model` parameter and tagging the role in its `description`
(`[mokata:reviewer]`). The gate-guard hook already sees every Agent/Task call (the subagent
contract), so it reads both (`check_spawn`): a role-tagged spawn with NO model, an unknown one, or
one below the role's floor is BLOCKED with the tier to use; an allowed one is RECORDED for the run
(`model_tiers__<run>`, run-state) and ledgered (`model_tier`). At SubagentStop the subagent's own
transcript is read (bounded) for the model that actually answered and its token usage
(`usage_from_transcript`), added to the same per-run record — the per-run cost record, from what
Claude Code exposes, never an estimate dressed as a measurement.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

#: Claude Code's model aliases, weakest first. `inherit` (the parent's model) is not a tier: a
#: role must NAME one.
TIERS: Tuple[str, ...] = ("haiku", "sonnet", "opus")

EXPLORER, IMPLEMENTER, DEBUGGER, REVIEWER, FINAL_REVIEWER = (
    "explorer", "implementer", "debugger", "reviewer", "final-reviewer")
ROLES: Tuple[str, ...] = (EXPLORER, IMPLEMENTER, DEBUGGER, REVIEWER, FINAL_REVIEWER)

DEFAULTS: Dict[str, str] = {EXPLORER: "haiku", IMPLEMENTER: "sonnet", DEBUGGER: "opus",
                            REVIEWER: "sonnet", FINAL_REVIEWER: "opus"}
#: The research's floors: reviewers never below mid-tier, the final review at the top.
FLOORS: Dict[str, str] = {REVIEWER: "sonnet", FINAL_REVIEWER: "opus"}

SETTINGS_KEY = "models"
RECORD_PREFIX = "model_tiers__"
LEDGER_KIND = "model_tier"
GATE_MODEL_TIER = "model-tier"
#: A role tag in the Agent call's description: `[mokata:reviewer]`.
ROLE_TAG = re.compile(r"\[mokata:([a-z-]+)\]")
#: How much of a subagent transcript the SubagentStop hook reads for usage (the tail).
TRANSCRIPT_TAIL_BYTES = 4 * 1024 * 1024
#: Rows kept per run record (spawns, usages) — a bound, the oldest dropped and counted.
RECORD_CAP = 500


def rank(tier: str) -> int:
    return TIERS.index(tier) if tier in TIERS else -1


def tier_problem(role: str, tier: Any) -> str:
    """"" when `tier` is a valid choice for `role`, else the one-sentence reason."""
    if role not in ROLES:
        return f"unknown mokata role {role!r} (one of: {', '.join(ROLES)})"
    if not isinstance(tier, str) or tier not in TIERS:
        return (f"the {role} role must name its model tier — one of {', '.join(TIERS)} "
                f"(got {tier!r})")
    floor = FLOORS.get(role)
    if floor and rank(tier) < rank(floor):
        return f"the {role} role's floor is {floor}; {tier} is below it"
    return ""


def settings_problems(raw: Any) -> List[str]:
    """Every problem in a `settings.models` table (empty when it is valid or absent)."""
    if raw is None:
        return []
    if not isinstance(raw, dict):
        return ["settings.models must be a table of role -> tier"]
    return [p for role, tier in sorted(raw.items()) for p in [tier_problem(role, tier)] if p]


def configured(root: str) -> Dict[str, str]:
    """Each role's tier for this repo: the manifest's valid overrides over `DEFAULTS`. One JSON
    load, never raises; an invalid override is ignored (and `mokata config set` refuses it)."""
    tiers = dict(DEFAULTS)
    try:
        with open(os.path.join(root, ".mokata", "manifest.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        raw = (data.get("settings") or {}).get(SETTINGS_KEY) if isinstance(data, dict) else None
    except (OSError, ValueError, AttributeError):
        raw = None
    if isinstance(raw, dict):
        for role, tier in raw.items():
            if not tier_problem(role, tier):
                tiers[role] = tier
    return tiers


def role_of(tool_input: Any) -> Optional[str]:
    """The mokata role an Agent/Task call is tagged with (`[mokata:<role>]` in its description,
    else its prompt), or None for an untagged spawn — which this module does not police."""
    if not isinstance(tool_input, dict):
        return None
    for field in ("description", "prompt"):
        text = tool_input.get(field)
        if isinstance(text, str):
            m = ROLE_TAG.search(text)
            if m:
                return m.group(1)
    return None


def check_spawn(root: str, tool_input: Any) -> Tuple[Optional[str], Optional[str], str]:
    """`(role, model, refusal)` for one Agent/Task call. `refusal` is "" when it may proceed (or
    is not a mokata-role spawn: `role` None)."""
    role = role_of(tool_input)
    if role is None:
        return None, None, ""
    model = tool_input.get("model") if isinstance(tool_input, dict) else None
    problem = tier_problem(role, model)
    if problem:
        want = configured(root).get(role, DEFAULTS.get(role, "sonnet"))
        return role, model, (f"{GATE_MODEL_TIER}: {problem}. Spawn it with `model: \"{want}\"` "
                             f"(this repo's tier for {role}; `mokata progress models` lists them).")
    return role, model, ""


# ----------------------------------------------------------------------------------- the record

def _key(run_id: str) -> str:
    return RECORD_PREFIX + run_id


def _append(root: str, run_id: str, field: str, row: Dict[str, Any]) -> None:
    from .state import StateStore
    from .tdd_state import state_dir

    def _mutate(cur: Any) -> Dict[str, Any]:
        cur = dict(cur) if isinstance(cur, dict) else {}
        rows = [r for r in cur.get(field, []) if isinstance(r, dict)] + [row]
        over = max(0, len(rows) - RECORD_CAP)
        cur[field] = rows[over:]
        cur[field + "_dropped"] = int(cur.get(field + "_dropped", 0) or 0) + over
        cur["run_id"] = run_id
        return cur

    StateStore(state_dir(root)).update(_key(run_id), _mutate, default=None)


def record_spawn(root: str, run_id: str, role: str, model: str, *, session_id: str = "") -> None:
    """Record (and ledger) the tier a role's subagent was spawned with."""
    _append(root, run_id, "spawns", {"at": time.time(), "role": role, "model": model,
                                     "session": session_id})
    try:
        from .govern.ledger import AuditLedger
        AuditLedger.from_mokata_dir(os.path.join(root, ".mokata")).record(
            LEDGER_KIND, run=run_id, role=role, model=model)
    except Exception:  # noqa: BLE001 — the record above is the run's truth; the row is a copy
        pass


def record_usage(root: str, run_id: str, agent_id: str, agent_type: str,
                 usage: Dict[str, Any]) -> None:
    """Record what a finished subagent actually used (model(s), tokens) for the run."""
    _append(root, run_id, "usage", dict(usage, at=time.time(), agent_id=agent_id,
                                        agent_type=agent_type))


def read_record(root: str, run_id: Optional[str]) -> Dict[str, Any]:
    """`{"spawns": [...], "usage": [...]}` for a run — never raises; empty when absent."""
    data = None
    if run_id:
        try:
            from .state import StateStore
            from .tdd_state import state_dir
            data = StateStore(state_dir(root)).read(_key(run_id))
        except Exception:  # noqa: BLE001 — a read of a display record never raises
            data = None
    data = data if isinstance(data, dict) else {}
    return {"spawns": [r for r in data.get("spawns", []) if isinstance(r, dict)],
            "usage": [r for r in data.get("usage", []) if isinstance(r, dict)]}


def usage_from_transcript(path: Any, tail_bytes: int = TRANSCRIPT_TAIL_BYTES) -> Optional[Dict]:
    """The models and token usage in a Claude Code transcript (JSONL; assistant `message.model`
    and `message.usage`), from its last `tail_bytes`. None when there is no readable transcript —
    a usage nobody measured is absent, never zero."""
    if not isinstance(path, str) or not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - tail_bytes))
            raw = fh.read()
    except OSError:
        return None
    lines = raw.splitlines()
    if size > tail_bytes and lines:
        lines = lines[1:]                     # the first line may be cut in half
    models: Dict[str, int] = {}
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
              "cache_creation_input_tokens": 0}
    seen = 0
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        msg = entry.get("message") if isinstance(entry, dict) else None
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        model = msg.get("model")
        if isinstance(model, str) and model:
            models[model] = models.get(model, 0) + 1
        usage = msg.get("usage")
        if isinstance(usage, dict):
            seen += 1
            for k in totals:
                v = usage.get(k)
                if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                    totals[k] += v
    if not models and not seen:
        return None
    return {"models": models, "messages": seen, "partial": size > tail_bytes, **totals}


def skill_line(role: str) -> str:
    """The one sentence a skill carries for a role it dispatches (rendered into the skill text)."""
    floor = FLOORS.get(role)
    floor_txt = f" (floor: {floor} — never lower)" if floor else ""
    return (f"spawn the **{role}** subagent with the Agent tool's `model: \"{DEFAULTS[role]}\"`"
            f"{floor_txt} and `[mokata:{role}]` in its description — or this repo's "
            f"`settings.models.{role}` (`mokata progress models` shows it)")


__all__ = ["TIERS", "ROLES", "DEFAULTS", "FLOORS", "SETTINGS_KEY", "RECORD_PREFIX", "LEDGER_KIND",
           "GATE_MODEL_TIER", "EXPLORER", "IMPLEMENTER", "DEBUGGER", "REVIEWER", "FINAL_REVIEWER",
           "rank", "tier_problem", "settings_problems", "configured", "role_of", "check_spawn",
           "record_spawn", "record_usage", "read_record", "usage_from_transcript", "skill_line"]
