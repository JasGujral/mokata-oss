# Manual memoization needs a measured reason

**Rule id:** `memo-only-when-measured` · **Impact:** MEDIUM · **Applies:** React without the Compiler · **Pack:** frontend-react

## Why it matters
`useMemo`, `useCallback` and `memo` cost memory and comparison work on every render. They pay off for a measured expensive computation, or when an object's identity is a dependency of an effect or of a memoized child. Elsewhere they add complexity without a win.

## Incorrect
```tsx
const label = useMemo(() => `${first} ${last}`, [first, last]);   // trivial work
```

## Correct
```tsx
const label = `${first} ${last}`;
// identity matters here: the effect must not re-subscribe every render
const filter = useMemo(() => ({ status, owner }), [status, owner]);
useEffect(() => subscribe(filter), [filter]);
```

## Verify
Each memoization in the diff names its reason: a before/after measurement (route it through `frontend-perf`) or the effect/child whose dependency identity it stabilizes.

## Source
https://react.dev/reference/react/useMemo — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
