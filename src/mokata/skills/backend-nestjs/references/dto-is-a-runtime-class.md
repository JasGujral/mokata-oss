# A DTO is a runtime class, never a type-only import

**Rule id:** `dto-is-a-runtime-class` · **Impact:** CRITICAL · **Applies:** the project's `@nestjs/core` major (a schema-based major: the schema object must be a runtime value) · **Pack:** backend-nestjs

## Why it matters
The validation pipe decides what to validate from the parameter's runtime metadata. `import type` (or an interface) is erased by the compiler, so the metadata says `Object` and nothing is validated — the request body reaches the service as-is, and the code still compiles and looks right.

## Incorrect
```ts
import type { CreateUserDto } from './dto/create-user.dto';   // erased at runtime
@Post() create(@Body() dto: CreateUserDto) { return this.users.create(dto); }
```

## Correct
```ts
import { CreateUserDto } from './dto/create-user.dto';        // a class with validation decorators
@Post() create(@Body() dto: CreateUserDto) { return this.users.create(dto); }
```

## Verify
Every `@Body()`, `@Query()` and `@Param()` type in the diff is a class (or the project's runtime schema) imported as a value. Write an e2e test that posts an invalid body and expects a 400 — it fails today if validation is silently off.

## Source
https://docs.nestjs.com/techniques/validation — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
