# List keys are stable identities

**Rule id:** `stable-keys` · **Impact:** HIGH · **Applies:** all React versions · **Pack:** frontend-react

## Why it matters
React matches list children to their previous state by key. An index key moves state (input text, focus, open/closed) onto the wrong row when the list is filtered, sorted, or inserted into.

## Incorrect
```tsx
{todos.map((todo, i) => <TodoRow key={i} todo={todo} />)}
```

## Correct
```tsx
{todos.map((todo) => <TodoRow key={todo.id} todo={todo} />)}
```

## Verify
Every `key` in the diff comes from the item's own identity (an id, a stable slug). Write a component test that reorders or removes an item and asserts the surviving rows keep their state.

## Source
https://react.dev/learn/rendering-lists — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
