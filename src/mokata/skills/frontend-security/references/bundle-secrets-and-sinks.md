# Secrets in the bundle, and the HTML/URL sinks

**Impact:** HIGH · Authored clean-room in mokata's own words. Claims cite a primary source; anything
not confirmed there is **UNVERIFIED**.

## Secrets in the built output
Build tools replace environment variables that carry a client prefix with their literal values in
the JavaScript sent to browsers (for example Vite's `VITE_` variables:
https://vite.dev/guide/env-and-mode). The exact prefix depends on the framework — read its docs.

### Incorrect
```ts
// .env
VITE_STRIPE_SECRET_KEY=sk_live_...
// client code
const stripe = new Stripe(import.meta.env.VITE_STRIPE_SECRET_KEY);
```

### Correct
```ts
// the secret stays server-side; the client calls an endpoint that uses it
const res = await fetch('/api/checkout', { method: 'POST', body: JSON.stringify(cart) });
```

### Verify
Build for production, then search the emitted files for credential patterns (a secret scanner run
over the build output directory, not the git history). mokata's secret-guard blocks a secret from
being WRITTEN into the repo; a value injected at build time from the environment never passes
through that write, which is why the build output is checked separately.

## HTML and URL sinks
React escapes text it renders. It does not sanitize HTML passed to `dangerouslySetInnerHTML`, and a
URL attribute built from user input can carry a `javascript:` scheme
(https://react.dev/reference/react-dom/components/common#dangerously-setting-the-inner-html).

### Incorrect
```tsx
<div dangerouslySetInnerHTML={{ __html: comment.body }} />
<a href={profile.website}>site</a>
```

### Correct
```tsx
<div dangerouslySetInnerHTML={{ __html: sanitize(comment.body) }} />   // a maintained sanitizer
<a href={safeHttpUrl(profile.website)}>site</a>                        // allow only http(s)
```

### Verify
A component test renders the hostile input (`<img src=x onerror=...>`, `javascript:alert(1)`) and
asserts no script-bearing attribute or scheme reaches the DOM. A Content-Security-Policy and Trusted
Types reduce the damage when a sink is missed
(https://developer.mozilla.org/en-US/docs/Web/API/Trusted_Types_API); deploy them report-only first.
