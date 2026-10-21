# Authorization first — Server Functions and route handlers

**Impact:** CRITICAL · Authored clean-room in mokata's own words. Claims cite a primary source;
anything not confirmed there is **UNVERIFIED**.

## Why it is first
Broken access control is the top category of the OWASP Top 10 (https://owasp.org/Top10/). In a
React app with server rendering, the entry points that reach data are Server Functions (marked
`'use server'`), route handlers, and API routes. A Server Function is invoked by an HTTP POST the
framework generates; nothing stops a caller from sending that POST without the UI
(https://react.dev/reference/rsc/server-functions). Hiding the button is presentation, not control.

## Prove it (before fixing)
Write the test that calls the function or endpoint directly as a user who must NOT succeed —
another tenant's user, a signed-out caller — and assert refusal. On the vulnerable code this test
FAILS. That failing test is the RED that licenses the fix.

## Incorrect
```tsx
'use server';
export async function deleteInvoice(id: string) {
  await db.invoice.delete({ where: { id } });         // anyone who can POST can delete any invoice
  revalidatePath('/invoices');
}
```

## Correct
```tsx
'use server';
export async function deleteInvoice(id: string) {
  const user = await requireUser();                     // authentication
  const invoice = await db.invoice.findUnique({ where: { id } });
  if (!invoice || invoice.orgId !== user.orgId) {       // authorization on THIS object
    throw new Error('not found');                       // do not reveal existence
  }
  await db.invoice.delete({ where: { id } });
  revalidatePath('/invoices');
}
```

## Verify
Re-run the direct-call test: it now passes (the forbidden caller is refused). Keep it as the guard.
Repeat for each entry point in the diff. Middleware that checks a session is fine as an early
filter, but the check above stays inside the function — middleware path matching has a history of
bypasses in more than one framework (**UNVERIFIED** for any specific version: read the framework's
security advisories for the version in use).
