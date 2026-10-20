# Request scope bubbles up the dependency chain

**Rule id:** `request-scope-bubbles-up` · **Impact:** HIGH · **Applies:** the project's `@nestjs/core` major · **Pack:** backend-nestjs

## Why it matters
A provider declared with request scope forces everything that injects it — and everything that injects those — to be created per request. One innocuous-looking injection can turn a large part of the graph per-request. The risk is the unbounded spread, which the code at the injection site does not show.

## Incorrect
```ts
@Injectable({ scope: Scope.REQUEST })
export class TenantContext { constructor(@Inject(REQUEST) private req: Request) {} }
// injected into a widely used service — which is now request-scoped too
```

## Correct
```ts
// pass the tenant explicitly, or read it from an async-local context set by a guard/interceptor
@Injectable()
export class OrdersService { list(tenantId: string) { /* ... */ } }
```

## Verify
For any new request-scoped provider, walk who injects it (`mokata query callers <Provider>`) and state how far the scope spreads in the review finding. Prefer explicit parameters.

## Source
https://docs.nestjs.com/fundamentals/injection-scopes — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
