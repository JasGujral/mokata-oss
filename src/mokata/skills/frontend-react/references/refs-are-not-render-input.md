# Refs are not render input

**Rule id:** `refs-are-not-render-input` · **Impact:** LOW · **Applies:** all React versions · **Pack:** frontend-react

## Why it matters
Changing `ref.current` does not re-render, so output that depends on it is stale; reading it during render also makes the component impure, which the Compiler treats as a reason to skip it.

## Incorrect
```tsx
const count = useRef(0);
return <span>{count.current}</span>;        // never updates on screen
```

## Correct
```tsx
const [count, setCount] = useState(0);
return <span>{count}</span>;
```

## Verify
`ref.current` is read or written only in event handlers and effects in the diff, never in the render body.

## Source
https://react.dev/reference/react/useRef — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
