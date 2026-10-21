# Authorize in guards; every route is guarded or explicitly public

**Rule id:** `authorize-in-guards-not-middleware` · **Impact:** CRITICAL · **Applies:** the project's `@nestjs/core` major · **Pack:** backend-nestjs

## Why it matters
In the NestJS request lifecycle, middleware runs before guards and is bound by path pattern through the HTTP adapter — the place where path-normalization bypasses (trailing slashes, encoding, method variants) have repeatedly appeared. A guard runs with the resolved handler in hand, so an authorization decision made there cannot be skipped by spelling the URL differently. A handler with no guard is reachable by anyone.

## Incorrect
```ts
// main.ts
app.use('/admin', requireAdminMiddleware);      // the only check
@Controller('admin')
export class AdminController {
  @Delete('users/:id') remove(@Param('id') id: string) { return this.users.remove(id); }
}
```

## Correct
```ts
@Controller('admin')
@UseGuards(AuthGuard, RolesGuard)              // or a global guard + an explicit @Public() opt-out
export class AdminController {
  @Roles('admin')
  @Delete('users/:id') remove(@Param('id') id: string) { return this.users.remove(id); }
}
```

## Verify
List every handler the diff adds or changes; each resolves to at least one guard (method, class, or global) or carries the project's explicit public marker, and the reviewer agrees the public ones should be public. Object-level checks (can THIS user delete THIS record?) are still the service's job — a role guard does not answer them.

## Source
https://docs.nestjs.com/guards — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
