# optimize — measure first (reference)

Pulled in just-in-time. The SKILL.md carries the rule; this file carries the how.

## Why measure before you touch anything

The assumed hot path is rarely the real one. An optimization that isn't measured is a guess
that also risks changing behaviour — the worst trade. So the discipline is strict: no change is
kept without a before/after measurement that proves the win with behaviour preserved.

## Record a baseline first

Before changing a line:

- pick the metric that matters (wall time, allocations, query count, payload size — whatever the
  target actually cares about),
- measure the REAL code path under a realistic input, not a micro-case you hand-picked,
- record the number so the "after" has something honest to compare against.

## Keep a change only when the numbers say so

After the change:

- re-run the same measurement the same way,
- keep it only if it is faster (or lighter) AND the suite is still green (behaviour unchanged),
- if the win doesn't show up in numbers — or it only "feels" faster — REVERT it.

## Behaviour must be preserved

If a change alters behaviour to gain speed, it is no longer a pure optimization — it routes
through the deviation gate like any other plan change. Speed is never a licence to quietly
change what the code does.
