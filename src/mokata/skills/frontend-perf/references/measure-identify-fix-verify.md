# Measure → identify → fix → verify → guard (frontend)

Authored clean-room in mokata's own words. External claims cite a primary source; anything not
confirmed there is **UNVERIFIED** and must be read at the source before it is quoted.

## 1. Choose the metric that matches the complaint

| Complaint | Metric | Where it comes from |
|---|---|---|
| "the page is heavy / slow to load" | bytes of built JS/CSS per entry (gzip + raw); lab LCP | the build output on disk; a lab run (https://web.dev/articles/lcp) |
| "typing / clicking feels laggy" | long tasks during a scripted interaction (lab); INP (field only) | a trace of the interaction; real-user data (https://web.dev/articles/inp) |
| "the list re-renders constantly" | render count / render duration for one interaction | a render-count component test, or `<Profiler onRender>` (https://react.dev/reference/react/Profiler) |
| "layout jumps" | CLS | a lab run plus the elements that shifted (https://web.dev/articles/cls) |

Pick one. Several metrics at once hide which change moved which number.

## 2. State the conditions, every time
Build mode (production), commit, browser and version, device/network profile, cache state, and the
number of runs. A number without its conditions cannot be compared to the next one.

## 3. Noise
Run each measurement several times and report the spread. A change is a win only if the difference
between before and after is larger than the spread of either side. When it is not, revert it.
Byte counts of a deterministic build are usually stable to the byte; timings are not.

## 4. Fix the identified cause
Typical causes and their fixes — each only after the measurement points at it:
- a heavy dependency in the initial bundle → load it on demand (dynamic import at the route or
  interaction that needs it), or replace it
- a component re-rendering on unrelated state → move the state down, split the context, or (without
  the Compiler, and only if measured) memoize the child
- a long task during interaction → split the work, defer non-urgent updates (`useTransition`), or
  move it off the main thread

## 5. Guard
Leave a check that fails on regression:
- a byte budget enforced by the project's size checker (exit code non-zero over budget)
- a component test asserting a render count for a scripted interaction
- a recorded baseline in the ledger the next change is compared against
A guard that only prints is not a guard. Whether the project has a backed budget gate is a fact to
read from its CI config — never assume one exists.
