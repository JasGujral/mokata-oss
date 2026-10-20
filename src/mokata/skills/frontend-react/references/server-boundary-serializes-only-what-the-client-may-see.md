# Props crossing a server→client boundary are public

**Rule id:** `server-boundary-serializes-only-what-the-client-may-see` · **Impact:** CRITICAL · **Applies:** projects using Server Components · **Pack:** frontend-react

## Why it matters
When a Server Component renders a Client Component, every prop is serialized into the response the browser receives. Passing a whole database row ships every column — including ones the page never displays — to anyone who can load the page.

## Incorrect
```tsx
// server component
export default async function Page() {
  const user = await db.user.findUnique({ where: { id } });   // has passwordHash, email, role
  return <ProfileCard user={user} />;                        // ProfileCard is 'use client'
}
```

## Correct
```tsx
export default async function Page() {
  const user = await db.user.findUnique({ where: { id } });
  return <ProfileCard name={user.name} avatarUrl={user.avatarUrl} />;   // only what is shown
}
```

## Verify
For every Client Component rendered from a Server Component in the diff, list the props and their source. Any field the UI does not display is a finding. Route-level authorization for the page itself is the `frontend-security` skill's first rule.

## Source
https://react.dev/reference/rsc/use-client — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
