# With the Compiler on, hunt bailouts before tuning by hand

**Rule id:** `compiler-hunt-bailouts` · **Impact:** MEDIUM · **Applies:** React ≥ 19 with the React Compiler enabled · **Pack:** frontend-react

## Why it matters
The Compiler skips a component it cannot prove safe and the build still succeeds, so a slow component may simply be uncompiled. Adding `useMemo`/`useCallback` by hand then treats the symptom and can mask the real cause (a rules violation the Compiler refused to optimize).

## Incorrect
```tsx
// re-renders a lot, so: wrap everything
const options = useMemo(() => buildOptions(rows), [rows]);
const onPick = useCallback((r: Row) => pick(r), []);
```

## Correct
```tsx
// 1. confirm whether the component compiled (compiler diagnostics / lint / build logger)
// 2. if it bailed out, fix the reported rule violation (often a mutation or a ref read in render)
// 3. re-measure; add manual memoization only if a measurement still demands it
const options = buildOptions(rows);
```

## Verify
Name the evidence that the component compiled or bailed out (the Compiler's diagnostics as surfaced by the project's lint or build). A temporary `"use no memo"` directive is a bisect tool for isolating a Compiler-introduced change, not a fix to leave in.

## Source
https://react.dev/learn/react-compiler — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
