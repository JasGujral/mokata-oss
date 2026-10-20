# Validation strips and rejects unknown fields

**Rule id:** `validation-rejects-unknown-fields` · **Impact:** CRITICAL · **Applies:** the project's `@nestjs/core` major · **Pack:** backend-nestjs

## Why it matters
Without whitelisting, a client can send fields the DTO never declared (`role`, `isAdmin`, `ownerId`) and a service that spreads the body into a database call writes them — mass assignment.

## Incorrect
```ts
app.useGlobalPipes(new ValidationPipe());
```

## Correct
```ts
app.useGlobalPipes(new ValidationPipe({
  whitelist: true,              // drop properties without decorators
  forbidNonWhitelisted: true,   // and reject the request when any were sent
  transform: true,
}));
```

## Verify
Read the global pipe configuration once per project; then add an e2e test posting an undeclared field and expecting rejection. The exact option names belong to the validation library the project uses — confirm them at the source.

## Source
https://docs.nestjs.com/techniques/validation — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
