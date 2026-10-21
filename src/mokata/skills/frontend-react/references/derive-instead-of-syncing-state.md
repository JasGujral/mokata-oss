# Derive values during render instead of syncing them with an effect

**Rule id:** `derive-instead-of-syncing-state` · **Impact:** HIGH · **Applies:** all React versions · **Pack:** frontend-react

## Why it matters
Copying a prop or a computed value into state with `useEffect` causes an extra render with stale data on every change, and two sources of truth that drift apart when one update path is forgotten.

## Incorrect
```tsx
function Total({ items }: { items: Item[] }) {
  const [total, setTotal] = useState(0);
  useEffect(() => { setTotal(sum(items)); }, [items]);
  return <span>{total}</span>;
}
```

## Correct
```tsx
function Total({ items }: { items: Item[] }) {
  const total = sum(items);        // computed where it is used
  return <span>{total}</span>;
}
```

## Verify
An effect whose body only calls a state setter with a value computable from props/state is this anti-pattern. Expensive derivations are a measurement question (`memo-only-when-measured`), not a reason to store them.

## Source
https://react.dev/learn/you-might-not-need-an-effect — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
