"""RUN-REG structural (0.0.22 C1, ruling R-C1a option C): a run the user ASKED for is one the gate
waits for — per Claude Code session, so parallel runs never share a verdict.

THE PROBLEM
-----------
The phase gate (`gate_hook.check_write`) fails open when no run is registered: ordinary hand-editing
in a mokata repo is never policed (the SI.1 floor). Registration is `session_save(register=true)`,
which brainstorm's SKILL.md tells the model to call first. If the model skips that step, the gate
cannot tell "a pipeline that forgot to register" from "a person hand-editing", and lets both through.

THE FIX, AND THE ONE IDENTITY IT KEEPS
--------------------------------------
Three hook-side facts, each keyed by Claude Code's `session_id` (which every hook receives and the
MCP process cannot see — harness gap #25642):

  1. INTENT. When this session asks for a pipeline — a typed `/mokata:brainstorm` (UserPromptSubmit)
     or the model invoking the brainstorm skill (PreToolUse on `Skill`) — the hook records
     `run_intent__<session>`. No run is minted here: minting one would be a SECOND identity beside
     the MCP's own run, and the gate would then police a run whose spec never emits.
  2. BIND. When this session's `session_save(register=true)` returns, a PostToolUse hook reads the
     run id the MCP itself registered out of `tool_response` and writes it INTO the intent record
     (`run_id`). The run id is the MCP's — one identity from registration through ship. (It also
     writes the badge's session binding, so the statusline flips at once.)
  3. WAIT. The gate, given the session id, reads this record FIRST. Bound → it polices exactly that
     run. Open and unexpired → it BLOCKS implementation writes and says how to unblock, instead of
     failing open or resolving some other session's run. No record → the gate's resolution is
     byte-identical to before this stage.

⚠ WHY ITS OWN RECORD AND NOT THE GENERAL `session_run_binding__`: that binding is also written by
SessionStart when exactly one run is live, which is a GUESS about which window a new session
belongs to — fine for a badge, wrong for enforcement once two sessions run in parallel (a second
window opened beside a live brainstorm would be policed by the first window's run). The gate
honours only a binding this session's own registration produced.

PARALLEL RUNS (standing constraint, Jas 2026-10-09)
---------------------------------------------------
Every record here is per Claude session, and the join (2) is the session's own tool call — never a
"the newest run in the repo" guess. Two sessions running two brainstorms side by side each bind to
their own run and are each policed by their own run's state. A session with an intent never borrows
another session's run: while its intent is open the gate blocks rather than resolve.

WHAT IT DOES NOT DO
-------------------
It does not police a session that never asked for a pipeline: no intent, no change, so hand-editing
stays free (the positive-trigger contract). An intent expires after `INTENT_TTL_SECS`, and a
`/clear` starts a session with none, so a pipeline abandoned before registering cannot hold a
session hostage.

Never raises: every reader degrades to "no intent" and every writer to a no-op.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

from .state import StateStore
from .tdd_state import state_dir

#: `run_intent__<claude_session_id>.json` — `{at, via}` while OPEN, plus `run_id` once BOUND.
#: Transient run-state under temp_local/.
INTENT_PREFIX = "run_intent__"

#: An intent older than this is not a run the session is still waiting on.
INTENT_TTL_SECS = 2 * 60 * 60

#: The pipeline entries whose first step REGISTERS a run. Only brainstorm registers; the later
#: phases attach to a run that already exists.
REGISTERING_SKILLS = ("brainstorm",)

_SLASH = re.compile(r"^\s*/(?:mokata:)?(%s)(?=\s|$)" % "|".join(REGISTERING_SKILLS))

#: The MCP tool whose `register=true` call registers the run — under either server name Claude
#: Code gives it (`mcp__mokata__…` from `mokata mcp install`, `mcp__plugin_mokata_mokata__…` from
#: the plugin). Also the PostToolUse matcher (Claude Code matchers are regular expressions).
REGISTER_TOOL_RE = r"^mcp__(?:plugin_mokata_)?mokata__session_save$"
_REGISTER_TOOL = re.compile(REGISTER_TOOL_RE)


def _key(session_id: str) -> str:
    return INTENT_PREFIX + session_id


def pipeline_in_prompt(prompt: Any) -> Optional[str]:
    """The registering pipeline a typed prompt starts (`/mokata:brainstorm …`), or None."""
    if not isinstance(prompt, str):
        return None
    m = _SLASH.match(prompt)
    return m.group(1) if m else None


def pipeline_in_skill_call(tool_input: Any) -> Optional[str]:
    """The registering pipeline a `Skill` tool call invokes, or None.

    ⚠ The `Skill` tool's input shape is not in the hooks reference, so this reads the plausible
    name fields and normalises a `mokata:` prefix. A shape it cannot read records NO intent, which
    is today's behaviour (fail open) — never a block on a guess."""
    if not isinstance(tool_input, dict):
        return None
    for field in ("skill", "command", "name"):
        val = tool_input.get(field)
        if isinstance(val, str):
            name = val.strip().lstrip("/")
            if name.startswith("mokata:"):
                name = name[len("mokata:"):]
            if name in REGISTERING_SKILLS:
                return name
    return None


def record_intent(root: str, session_id: str, via: str) -> bool:
    """This session asked for a pipeline: an OPEN record, replacing any earlier one — from here on
    the session's edits belong to the run this pipeline registers, not to the one it had before."""
    if not session_id:
        return False
    try:
        StateStore(state_dir(root)).write(_key(session_id), {"at": time.time(), "via": via})
        return True
    except Exception as exc:  # noqa: BLE001 — an unrecorded intent is today's fail-open, never a block
        _say_degraded("the pipeline this session asked for was NOT recorded, so the gate will not "
                      "wait for its run", exc)
        return False


#: What `session_claim` answers.
CLAIM_NONE = "none"          # this session never asked for a pipeline (or it expired)
CLAIM_OPEN = "open"          # asked, and its registration has not come back yet
CLAIM_BOUND = "bound"        # its own registration returned this run id


def session_claim(root: str, session_id: Optional[str],
                  now: Optional[float] = None) -> "tuple":
    """`(state, run_id)` for this session — `(CLAIM_BOUND, rid)`, `(CLAIM_OPEN, None)` or
    `(CLAIM_NONE, None)`. Unreadable, malformed or no session all read as NONE (today's gate)."""
    if not session_id:
        return CLAIM_NONE, None
    try:
        data = StateStore(state_dir(root)).read(_key(session_id))
    except Exception:  # noqa: BLE001
        return CLAIM_NONE, None
    if not isinstance(data, dict):
        return CLAIM_NONE, None
    rid = data.get("run_id")
    if isinstance(rid, str) and rid.strip():
        return CLAIM_BOUND, rid.strip()
    try:
        at = float(data.get("at"))
    except (TypeError, ValueError):
        return CLAIM_NONE, None
    if ((now if now is not None else time.time()) - at) > INTENT_TTL_SECS:
        return CLAIM_NONE, None
    return CLAIM_OPEN, None


def _say_degraded(what: str, exc: BaseException) -> None:
    """D5 — a swallowed write here is a silent degrade of the RUN-REG gate, so it says so."""
    try:
        from .degrade import FAILURE_LOCAL_IO, note_degraded
        note_degraded("run-registration", FAILURE_LOCAL_IO, fallback=what, detail=str(exc),
                      fix="check permissions/disk under `.mokata/temp_local/`, then run "
                          "`mokata doctor`")
    except Exception:  # noqa: BLE001 — the notice is best-effort; the caller has already decided
        pass


def _find_registered(value: Any, depth: int = 0) -> Optional[str]:
    """The `registered` run id anywhere in a tool response — a dict, a list of content blocks, or
    JSON text inside one. The hooks reference types `tool_response` as `Any`, so the reader is
    shape-tolerant and bounded rather than tied to one transport's framing."""
    if depth > 6:
        return None
    if isinstance(value, dict):
        rid = value.get("registered")
        if isinstance(rid, str) and rid.strip():
            return rid.strip()
        for v in value.values():
            found = _find_registered(v, depth + 1)
            if found:
                return found
    elif isinstance(value, list):
        for v in value:
            found = _find_registered(v, depth + 1)
            if found:
                return found
    elif isinstance(value, str) and "registered" in value:
        try:
            return _find_registered(json.loads(value), depth + 1)
        except (ValueError, TypeError):
            return None
    return None


def registered_run(tool_name: Any, tool_response: Any) -> Optional[str]:
    """The run id a just-returned `session_save(register=true)` registered, or None."""
    if not isinstance(tool_name, str) or not _REGISTER_TOOL.match(tool_name):
        return None
    return _find_registered(tool_response)


def bind_registered(root: str, session_id: str, run_id: str) -> bool:
    """Bind this session to the run ITS OWN registration returned. Writes the record the gate reads
    (and, for the statusline, the general session binding). A registration this session never
    asked for still binds: the call itself is proof of which run this session drives."""
    if not session_id or not run_id:
        return False
    try:
        store = StateStore(state_dir(root))
        prev = store.read(_key(session_id))
        via = prev.get("via") if isinstance(prev, dict) else None
        store.write(_key(session_id), {"at": time.time(), "via": via or "registration",
                                       "run_id": run_id})
    except Exception as exc:  # noqa: BLE001
        _say_degraded("this session's registration was NOT bound to its run, so the gate keeps "
                      "waiting (or, with no intent, fails open as before)", exc)
        return False
    from .run_resolver import bind_session_run
    bind_session_run(root, session_id, run_id)
    return True


__all__ = ["INTENT_PREFIX", "INTENT_TTL_SECS", "REGISTERING_SKILLS", "REGISTER_TOOL_RE",
           "CLAIM_NONE", "CLAIM_OPEN", "CLAIM_BOUND", "pipeline_in_prompt",
           "pipeline_in_skill_call", "record_intent", "session_claim", "registered_run",
           "bind_registered"]
