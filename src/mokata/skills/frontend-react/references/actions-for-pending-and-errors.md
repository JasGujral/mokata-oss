# Use actions for pending and error state

**Rule id:** `actions-for-pending-and-errors` · **Impact:** MEDIUM · **Applies:** React ≥ 19 · **Pack:** frontend-react

## Why it matters
Hand-rolled `isPending`/`error` flags around an async submit are easy to leave stuck (a thrown error skips the reset) and race when submitted twice. React 19's form actions and `useActionState` track pending and the latest result for you.

## Incorrect
```tsx
const [pending, setPending] = useState(false);
async function onSubmit(e: FormEvent) {
  e.preventDefault(); setPending(true);
  await save(new FormData(e.currentTarget));   // a throw leaves pending=true forever
  setPending(false);
}
```

## Correct
```tsx
const [state, formAction, isPending] = useActionState(saveAction, { error: null });
return (
  <form action={formAction}>
    <button disabled={isPending}>Save</button>
    {state.error && <p role="alert">{state.error}</p>}
  </form>
);
```

## Verify
Read the project's React major first; below 19 this rule does not apply. A server-side action is still a public endpoint — authorize inside it (`frontend-security`).

## Source
https://react.dev/reference/react/useActionState — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
