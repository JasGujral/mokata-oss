# No barrel imports between modules

**Rule id:** `no-barrels-between-modules` · **Impact:** HIGH · **Applies:** the project's `@nestjs/core` major · **Pack:** backend-nestjs

## Why it matters
An `index.ts` that re-exports a module's classes makes it easy to import a neighbour's module through a barrel that imports you back. The result is a circular import that surfaces as `undefined` at decoration time; `forwardRef` silences it without fixing the boundary.

## Incorrect
```ts
// orders/orders.module.ts
import { UsersModule } from '../users';            // barrel that re-exports users/* (and imports orders)
@Module({ imports: [forwardRef(() => UsersModule)] })
```

## Correct
```ts
import { UsersModule } from '../users/users.module';   // direct path; no cycle
@Module({ imports: [UsersModule] })
```

## Verify
New module and provider imports in the diff use direct file paths. A new `forwardRef` is a finding that needs a stated reason (a genuine mutual dependency) or a split.

## Source
https://docs.nestjs.com/fundamentals/circular-dependency — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
