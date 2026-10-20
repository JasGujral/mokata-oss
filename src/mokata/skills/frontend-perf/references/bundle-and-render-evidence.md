# Attributing bytes and renders — bundler-agnostic evidence

Authored clean-room in mokata's own words. Tool behaviour named here is **UNVERIFIED** until read in
the tool's own documentation for the version the project uses.

## Bytes → which dependency?
The honest method does not depend on the bundler's statistics format (which differs between
bundlers, and some emit none):
1. Build the base commit for production; record the size of every emitted JS/CSS file.
2. Build the change; record the same.
3. The difference per entry is the cost of the change. If a dependency was added, build once with
   and once without it (or with it imported lazily) — the delta is its real cost in THIS app, after
   tree-shaking and chunking. A package-registry size figure is not that number.
Source maps can attribute bytes back to source files when you need a breakdown
(https://developer.mozilla.org/en-US/docs/Glossary/Source_map).

## Renders → which component?
- In a test: wrap the component in `<Profiler id onRender>` and count calls during a scripted
  interaction (https://react.dev/reference/react/Profiler). Assert the count — that assertion is the
  guard.
- In the browser: the React DevTools profiler shows which components rendered and why; it is a
  human, interactive tool. Record what it showed in the finding.
- With the React Compiler on, first establish whether the component compiled; an uncompiled
  component re-renders the way hand-written React does (https://react.dev/learn/react-compiler).

## What not to use as evidence
- a development build's timings
- a single run
- a tool that patches React at runtime when the project uses the Compiler (its comparisons assume
  the code the Compiler rewrites) — **UNVERIFIED** per tool; check the tool's own compatibility notes
