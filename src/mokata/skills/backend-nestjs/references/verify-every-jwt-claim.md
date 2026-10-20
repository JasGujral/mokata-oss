# Pin algorithm, audience and issuer when verifying a JWT

**Rule id:** `verify-every-jwt-claim` · **Impact:** HIGH · **Applies:** the project's `@nestjs/core` major with token-based auth · **Pack:** backend-nestjs

## Why it matters
A verifier that accepts any algorithm, any audience or any issuer will accept a token minted for a different service, or one signed in a way the issuer never intended. The common setup examples configure only the secret, so these checks are easy to omit.

## Incorrect
```ts
JwtModule.register({ secret: process.env.JWT_SECRET });
```

## Correct
```ts
JwtModule.register({
  secret: process.env.JWT_SECRET,
  verifyOptions: { algorithms: ['HS256'], audience: 'orders-api', issuer: 'https://auth.example' },
});
```

## Verify
Find where tokens are verified (the strategy or guard) and confirm all three are pinned. Add a test that a token with a wrong audience is refused. Option names depend on the JWT library in use — read its docs.

## Source
https://docs.nestjs.com/security/authentication — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
