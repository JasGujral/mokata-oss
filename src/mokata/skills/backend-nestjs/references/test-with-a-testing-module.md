# Unit-test through a testing module; e2e through HTTP

**Rule id:** `test-with-a-testing-module` · **Impact:** MEDIUM · **Applies:** the project's `@nestjs/core` major (runner per the project: Jest or Vitest) · **Pack:** backend-nestjs

## Why it matters
Instantiating a service with `new` skips the DI wiring the app depends on, so a test passes while bootstrap fails. A testing module compiles the real wiring with overridden collaborators; an e2e test exercises pipes, guards and filters, which unit tests never touch.

## Incorrect
```ts
const svc = new OrdersService(new PaymentsService());   // real collaborator, wiring untested
```

## Correct
```ts
const mod = await Test.createTestingModule({ providers: [OrdersService, PaymentsService] })
  .overrideProvider(PaymentsService).useValue({ charge: vi.fn() })   // jest.fn() under Jest
  .compile();
const svc = mod.get(OrdersService);
```

## Verify
The failing test written before the change goes through a testing module (unit) or the HTTP layer (anything involving validation, guards or status codes).

## Source
https://docs.nestjs.com/fundamentals/testing — read the page for the version the project uses. A detail this file states that you
could not confirm there is **UNVERIFIED**: say so in the finding rather than quoting it as fact.
