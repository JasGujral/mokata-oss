# Provide what you inject

**Rule id:** `provide-what-you-inject` · **Impact:** MEDIUM · **Applies:** the project's `@nestjs/core` major · **Pack:** backend-nestjs

## Why it matters
A provider injected in module A must be listed in A's `providers` or exported by a module A imports. When it is not, the app fails at bootstrap — or, worse, a second instance is created when someone 'fixes' it by listing the provider in two modules.

## Incorrect
```ts
@Module({ providers: [OrdersService] })           // OrdersService injects PaymentsService
export class OrdersModule {}
```

## Correct
```ts
@Module({ imports: [PaymentsModule], providers: [OrdersService] })   // PaymentsModule exports PaymentsService
export class OrdersModule {}
```

## Verify
Each new constructor injection in the diff resolves to one provider declaration; a bootstrapping e2e test (or a testing module compile) proves it.

## Source
https://docs.nestjs.com/modules — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
