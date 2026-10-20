# Render is pure

**Rule id:** `render-is-pure` · **Impact:** CRITICAL · **Applies:** all React versions · **Pack:** frontend-react

## Why it matters
React may call a component more than once for one screen update, may skip calls, and (with the Compiler) reuses results it has already computed. A component that mutates its inputs, writes to a module variable, or starts a request while rendering behaves differently depending on how often it was called — a bug that appears only under concurrency, Strict Mode, or the Compiler.

## Incorrect
```tsx
let renders = 0;
function Cart({ items }: { items: Item[] }) {
  renders++;                       // module state written during render
  items.sort(byPrice);             // mutates the caller's array
  return <List items={items} />;
}
```

## Correct
```tsx
function Cart({ items }: { items: Item[] }) {
  const sorted = [...items].sort(byPrice);   // a new array; the prop is untouched
  return <List items={sorted} />;
}
```

## Verify
Search the component body (outside handlers and effects) for assignments to props, to state objects, or to variables declared outside the component, and for calls that perform I/O. Strict Mode's double render in development surfaces most of these. The React hooks lint's purity checks report this class when the project runs it.

## Source
https://react.dev/reference/react/components-and-hooks-must-be-pure — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
