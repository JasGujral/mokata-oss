# Map failures to HTTP exceptions without leaking internals

**Rule id:** `errors-as-http-exceptions` · **Impact:** MEDIUM · **Applies:** the project's `@nestjs/core` major · **Pack:** backend-nestjs

## Why it matters
An unhandled error becomes a generic 500, and a hand-built error response that includes `err.message` or a stack can reveal queries, file paths or library versions.

## Incorrect
```ts
catch (err) { return res.status(500).json({ error: err.message, stack: err.stack }); }
```

## Correct
```ts
if (!order) throw new NotFoundException('order not found');
// unexpected errors: let the exception filter log details server-side and answer generically
```

## Verify
No response body in the diff includes raw error messages or stacks; expected failures throw the matching HTTP exception, and a test asserts the status code.

## Source
https://docs.nestjs.com/exception-filters — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
