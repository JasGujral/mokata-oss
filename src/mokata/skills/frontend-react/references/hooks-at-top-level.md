# Hooks run at the top level, every render, in the same order

**Rule id:** `hooks-at-top-level` · **Impact:** CRITICAL · **Applies:** all React versions · **Pack:** frontend-react

## Why it matters
React identifies each hook call by its position in the call order. A hook behind a condition, inside a loop, or after an early return shifts every later hook onto the wrong state slot the first time that branch flips.

## Incorrect
```tsx
function Profile({ id }: { id?: string }) {
  if (!id) return null;            // early return BEFORE a hook
  const user = useUser(id);
  return <Card user={user} />;
}
```

## Correct
```tsx
function Profile({ id }: { id?: string }) {
  const user = useUser(id);        // always called; the hook handles a missing id
  if (!id) return null;
  return <Card user={user} />;
}
```

## Verify
Every `use*` call sits before any `return` and outside `if`/`for`/`&&`/try blocks. The `rules-of-hooks` lint rule reports violations deterministically when the project runs it.

## Source
https://react.dev/reference/react/rules-of-hooks — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
