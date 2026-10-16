"""AXI-LEAN (0.0.23 D11) — measure a LEAN MCP surface against the full one, then decide.

The question (doc 84 AXI-LEAN): every session pays mokata's full MCP schema (~65 tools) up front.
mokata has CLI parity for its reads, so a LEAN surface registers only the consent-critical tools —
the human-gated WRITES and the in-chat APPROVE act, which MUST stay on the consented MCP path — and
the agent reads through the `mokata` CLI instead. Measure-then-decide (P16): G2 runs identical tasks
under `mokata` (full) and `mokata-lean`; adoption needs a MEASURED WIN and the owner's GO.

THE STUB, and why it lives here and not in `src/mokata`: `settings.surface.lean` is declared below,
OFF, and NOT ADOPTED. Nothing in the product reads it (pinned by `tests/test_g2_axi_lean.py`) —
a product setting that does nothing would be a dead knob users could believe in. The condition
applies the lean surface from OUTSIDE (Claude Code's `--disallowedTools`), which measures exactly
what an adopted setting would register without shipping one. Adoption = a product change, after a
measured win and an owner GO; this module can only say whether the win was measured.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .archive import RunArchive
from .metrics import cell_stats

#: The proposed opt-in product setting. OFF. NOT ADOPTED. Changing `adopted` is an owner decision.
LEAN_SETTING = {"key": "settings.surface.lean", "default": False, "adopted": False}

#: Tool kinds the lean surface KEEPS on MCP. Writes are human-gated (SI.3) and approve is the
#: in-chat consent act (AP-MCP): moving either off MCP would move it off the consent path.
KEPT_KINDS = ("write", "approve")

#: "A measured win" — both must hold, on a REAL run:
#:   non-inferior: on no task does lean pass fewer graded runs than full;
#:   cheaper: Σ median total re-sent context is at least this fraction lower than full's.
MIN_CONTEXT_SAVING = 0.10

LEAN_CLAUDE_MD = (
    "\n### Lean surface\n\nmokata's READ tools are not registered as MCP tools in this session. "
    "Use the `mokata` CLI for reads (for example `mokata query …`, `mokata recall …`, "
    "`mokata status`); mokata's write and approve tools remain available over MCP.\n")


def mokata_tool_kinds() -> List[Tuple[str, str]]:
    """(name, kind) for every tool mokata's MCP server registers — read from the product's own
    registry, so the lean list cannot drift from what the server actually exposes."""
    from mokata.mcp import registry, tools_read, tools_write  # noqa: F401  (registration side effect)
    return [(t.name, t.kind) for t in registry.TOOLS]


def would_register(kind: str, lean: bool = LEAN_SETTING["default"]) -> bool:
    """What `settings.surface.lean` WOULD register for a tool of `kind`, were it adopted."""
    return (not lean) or kind in KEPT_KINDS


def lean_disallowed_tools() -> List[str]:
    return sorted(f"mcp__mokata__{name}" for name, kind in mokata_tool_kinds()
                  if not would_register(kind, lean=True))


def mokata_lean():
    from .conditions import Condition, mokata_on
    full = mokata_on()
    return Condition(name="mokata-lean",
                     description="mokata-on with a LEAN MCP surface: only write + approve tools; "
                                 "reads via the mokata CLI (AXI-LEAN measurement; not adopted).",
                     expect_mokata=True, claude_md=full.claude_md + LEAN_CLAUDE_MD,
                     setup=full.setup, disallowed_tools=lean_disallowed_tools(), surface="lean")


def lean_verdict(arch: RunArchive, full: str = "mokata", lean: str = "mokata-lean") -> Dict[str, Any]:
    names = arch.condition_names
    if full not in names or lean not in names:
        return {"measured": False, "measured_win": False, "adopt": False,
                "reason": f"run has no {full!r} vs {lean!r} pair (conditions: {names})"}
    rows, worse, ctx_full, ctx_lean = [], [], 0, 0
    for t in arch.task_ids:
        f, l_ = cell_stats(arch, full, t), cell_stats(arch, lean, t)
        rows.append({"task": t, "full": f, "lean": l_})
        if l_["passed"] < f["passed"]:
            worse.append(t)
        ctx_full += f["median_total_context"] or 0
        ctx_lean += l_["median_total_context"] or 0
    saving = (ctx_full - ctx_lean) / ctx_full if ctx_full else None
    real = arch.is_real and not arch.validate(require_real=True)
    win = bool(real and not worse and saving is not None and saving >= MIN_CONTEXT_SAVING)
    if not real:
        reason = "not a complete real run — no adoption evidence"
    elif worse:
        reason = f"lean passed fewer runs on: {', '.join(worse)}"
    elif saving is None or saving < MIN_CONTEXT_SAVING:
        reason = f"context saving {saving if saving is None else f'{saving:.1%}'} < {MIN_CONTEXT_SAVING:.0%}"
    else:
        reason = "measured win — adoption still needs the owner's GO"
    return {"measured": True, "measured_win": win, "adopt": False, "reason": reason,
            "context_saving": saving, "tasks_worse": worse, "rows": rows,
            "setting": dict(LEAN_SETTING)}


def render_markdown(v: Dict[str, Any]) -> str:
    lines = ["## AXI-LEAN — full vs lean MCP surface", ""]
    if not v["measured"]:
        return "\n".join(lines + [f"Not measured: {v['reason']}."])
    lines += ["| task | full pass | lean pass | full re-sent ctx | lean re-sent ctx | "
              "full first-turn ctx | lean first-turn ctx |", "|---|---|---|---|---|---|---|"]
    for r in v["rows"]:
        f, l_ = r["full"], r["lean"]
        lines.append(f"| `{r['task']}` | {f['passed']}/{f['graded']} | {l_['passed']}/{l_['graded']} | "
                     f"{f['median_total_context']} | {l_['median_total_context']} | "
                     f"{f['median_first_turn_context']} | {l_['median_first_turn_context']} |")
    sv = v["context_saving"]
    lines += ["", f"- Context saving (Σ median re-sent): {'—' if sv is None else f'{sv:.1%}'} "
                  f"(win threshold {MIN_CONTEXT_SAVING:.0%}, and no task may pass fewer runs)",
              f"- **Measured win: {'yes' if v['measured_win'] else 'no'}** — {v['reason']}",
              f"- `{LEAN_SETTING['key']}`: default **off**, adopted **{LEAN_SETTING['adopted']}**. "
              "Adoption is a product change behind an owner GO; this harness never adopts."]
    return "\n".join(lines)
