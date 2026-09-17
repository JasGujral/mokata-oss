"""A removal record's remedy is RUN, not read — the runnability half of `RECONFIGURE-REMOVE-…`.

0.0.20 stage 11b (`RECONFIGURE-REMOVE-CANNOT-UNWIRE-A-REMOVED-PROVIDER`, doc 84). ⛔ **The row does
not exist because a remedy was worded badly. It exists because a remedy was ASSERTED TO BE NAMED
AND NEVER RUN.**

The instance: `mokata reconfigure --remove` filtered its candidate set through
`OPTIONAL_INTEGRATIONS`. Taking `neo4j` out of that tuple — which the removal stage MUST do, or the
wizard goes on selling it — turned **the one command the removal record points at** into a silent
no-op that still exits 0, reporting *"no changes — your setup already matches"* to a user whose
manifest plainly still names it. Every text-level check stayed green: the record NAMED a command,
the command EXISTED, and the flag PARSED.

⭐ **NOTHING CONNECTED "this id left `OPTIONAL_INTEGRATIONS`" TO "a removal record names
`--remove <id>`", AND THAT LINK IS THIS MODULE.** The instance was repaired at 0.0.18 stage 14 by
widening `_removable_tools`; the CLASS is that the next removal repeats it, because the two facts
still live in different files and nothing reads both.

THREE VERDICTS, THREE REPRESENTATIONS (§7g)
--------------------------------------------
    RUNNABLE     the plan was computed and the channel is in `plan.removed`
    NO_OP        the plan was computed and the channel is NOT in it — the remedy is a LIE, told by
                 the notice whose entire job is to stop lies
    UNRUNNABLE   computing the plan RAISED, so the question was not answered

⛔ `NO_OP` and `UNRUNNABLE` must not collapse into one "did not work", and neither may collapse into
a skip. The original defect *presented as success* — exit 0, a reassuring sentence — so a checker
that reports "not removed" for both a no-op and a crash reproduces the ambiguity one layer up.

⚠ THE FIXTURE IS A MANIFEST A REAL USER WOULD HAVE, AND THAT IS LOAD-BEARING (§7e)
-----------------------------------------------------------------------------------
A removed channel cannot be written through `config set` — the schema refuses an unknown tool, and
correctly. **But the user this remedy addresses never took that path:** their manifest was written
by an OLDER mokata that still shipped the channel, so it carries the id in **`tools` AND in a
capability `fallback`**, and it validates. `plant_removed_channel` reproduces exactly that shape by
writing the manifest file directly.

⛔ **A fixture that plants only the fallback entry produces `ManifestError: references unknown tool`
and would have been reported as "the remedy is unrunnable" — a FALSE finding**, arrived at by
driving the reader instead of the boundary. It was written that way first. The half-plant is kept
as a named control (`plant_removed_channel(..., tools_table=False)`) so the distinction stays
graded rather than remembered.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import re


# ---- the three verdicts ---------------------------------------------------------------------

#: The plan was computed and the channel is in it.
RUNNABLE = "runnable"
#: The plan was computed and the channel is NOT in it. The remedy does not do what it says.
NO_OP = "no_op"
#: Computing the plan raised. The question was not answered; never green.
UNRUNNABLE = "unrunnable"

#: Whether a record's remedy claims `reconfigure --remove` at all. Two facts, two names (§7g).
CLAIMED = "claimed"
NOT_CLAIMED = "not_claimed"


#: A command named in a remedy sentence. Remedies quote commands in backticks and nothing else is
#: a command, so the delimiter is the grammar rather than a guess about wording.
_COMMAND = re.compile(r"`(?P<cmd>mokata [^`]+)`")

#: The one remedy shape this module can EXECUTE. Placeholders (`<path>`, `<tag>`) are deliberately
#: not matched — a remedy the user must fill in is not a command this harness can run, and calling
#: it runnable would be a claim about something never executed.
_RECONFIGURE_REMOVE = re.compile(r"^mokata reconfigure --remove (?P<channel>[a-z0-9][a-z0-9._-]*)$")


def remedy_commands(record):
    """Every `mokata …` command a record's remedy names, in order, as written."""
    remedy = getattr(record, "remedy", "") or ""
    return tuple(m.group("cmd").strip() for m in _COMMAND.finditer(remedy))


def reconfigure_remove_claim(record):
    """(state, channel) — does this record's remedy claim `--remove` can unwire something?

    ⚠ Returns the channel the REMEDY names, not the record's own `channel` field. They are the same
    today and a record that pointed `--remove` at a different id would be a defect this must be able
    to SEE rather than paper over by reading the record's key.
    """
    for cmd in remedy_commands(record):
        m = _RECONFIGURE_REMOVE.match(cmd)
        if m is not None:
            return CLAIMED, m.group("channel")
    return NOT_CLAIMED, None


def claims(removed_registry):
    """{channel -> the id its remedy claims `--remove` unwires} over a removal registry."""
    out = {}
    for channel, record in (removed_registry or {}).items():
        state, target = reconfigure_remove_claim(record)
        if state == CLAIMED:
            out[channel] = target
    return out


# ---- the fixture ------------------------------------------------------------------------------

def plant_removed_channel(root, channel, need=None, *, tools_table=True):
    """Make `root`'s manifest look like one written BEFORE `channel` was removed.

    `tools_table=False` is the CONTROL, not a convenience: it reproduces the half-plant that made
    the first cut of this harness report a false `UNRUNNABLE`, and a test pins that it still does.
    """
    path = os.path.join(root, ".mokata", "manifest.json")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    caps = data.get("capabilities") or {}
    if need is None:
        need = sorted(caps)[0] if caps else None
    if need is None or need not in caps:
        raise KeyError("no capability to plant %r into (have %s)" % (channel, sorted(caps)))

    if tools_table:
        tools = data.setdefault("tools", {})
        template = None
        for tid, entry in tools.items():
            if isinstance(entry, dict) and entry.get("provides") == need:
                template = entry
                break
        if template is None:
            raise KeyError("no tools-table entry provides %r to copy" % (need,))
        planted = dict(template)
        planted["provides"] = need
        tools[channel] = planted

    caps[need]["fallback"] = [channel] + list(caps[need].get("fallback") or [])
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return need


# ---- the run ----------------------------------------------------------------------------------

class Result(object):
    """A verdict plus the evidence, because a refusal that cannot say why gets overridden."""

    __slots__ = ("state", "channel", "removed", "error")

    def __init__(self, state, channel, removed=(), error=None):
        self.state = state
        self.channel = channel
        self.removed = tuple(removed)
        self.error = error

    def render(self):
        if self.state == UNRUNNABLE:
            return ("remedy `mokata reconfigure --remove %s`: UNRUNNABLE — computing the plan "
                    "raised %s: %s. This is NOT a no-op and NOT a pass; the question was not "
                    "answered." % (self.channel, type(self.error).__name__, self.error))
        if self.state == NO_OP:
            return ("remedy `mokata reconfigure --remove %s`: NO-OP — the plan was computed and "
                    "removed %s. The removal record points a user at a command that exits 0 and "
                    "changes nothing, which is a lie told by the notice that exists to stop lies."
                    % (self.channel, list(self.removed) or "nothing"))
        return ("remedy `mokata reconfigure --remove %s`: RUNNABLE — the plan removes %s."
                % (self.channel, list(self.removed)))


def run_remedy(root, channel, planner):
    """RUN the remedy against `root` and grade it. `planner` is `onboarding.plan_reconfigure`."""
    try:
        plan = planner(root, remove=[channel])
    except Exception as exc:                      # noqa: BLE001 — the verdict IS the exception
        return Result(UNRUNNABLE, channel, (), exc)
    removed = tuple(getattr(plan, "removed", ()) or ())
    if channel in removed:
        return Result(RUNNABLE, channel, removed)
    return Result(NO_OP, channel, removed)
