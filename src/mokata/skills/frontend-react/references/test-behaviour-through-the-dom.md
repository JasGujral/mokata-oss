# Component tests assert behaviour through the DOM

**Rule id:** `test-behaviour-through-the-dom` · **Impact:** HIGH · **Applies:** all React versions · **Pack:** frontend-react

## Why it matters
mokata counts a component test as the RED that licenses implementation. A test that reads a hook's internal state or a component instance breaks on every refactor and passes when the user-visible behaviour is wrong. A test that queries by role and label, fires real events, and asserts what is rendered fails for the right reason.

## Incorrect
```tsx
it('sets loading', () => {
  const { result } = renderHook(() => useSave());
  act(() => result.current.save());
  expect(result.current.state).toBe('loading');     // internal detail
});
```

## Correct
```tsx
it('disables Save and shows progress while saving', async () => {
  render(<Editor />);
  await user.click(screen.getByRole('button', { name: /save/i }));
  expect(screen.getByRole('button', { name: /save/i })).toBeDisabled();
  expect(screen.getByRole('progressbar')).toBeInTheDocument();
});
```

## Verify
Run the test before the change: it must fail for the missing behaviour (RED), then pass after it (GREEN). Queries prefer role/label/text over test ids; no assertion reads component internals.

## Source
https://testing-library.com/docs/queries/about/#priority — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
