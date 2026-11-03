# Prototype v1 engineering standard

This is a working v1 prototype on GitHub Free. Optimize for a coherent five-scenario system, readable changes and repeatable checks. No paid tooling, compliance certification, Kubernetes, service mesh or enterprise security programme is required. Foundation/pilot tasks build v1; expansion/future tasks remain later work.

## Decisions and research

Read existing architecture and standards before choosing libraries or patterns. For a new framework, shared dependency, API boundary or persistent data change, consult official documentation and the pinned upstream source. Record the URL, version/date checked, choice, reason, tradeoff and small verification result in a short ADR using docs/decisions/TEMPLATE.md. Routine implementation does not need an ADR or permission ceremony. Resolve routine choices within the task; raise only material scope/boundary conflicts. Search results, generated code and model answers are not authority.

## Shared coding rules

- Keep domain rules separate from HTTP handlers, UI components and vendor adapters. Reuse existing modules before adding abstractions. Do not introduce a second backend framework or operational case database.
- Python: snake_case functions/modules, PascalCase classes, explicit types on new public functions and data boundaries, Decimal for money. Use Ruff formatting/import ordering and Mypy checking. Avoid catch-all exception handling that turns failures into success.
- Custom frontends: Vue 3 + TypeScript + Frappe UI with Vite; npm lockfile. Native Frappe pages reuse framework forms and permissions. Follow ADR-002 and DESIGN_STANDARD.md. Build shared components once. The existing plain-JS fixture shells are temporary, not the production frontend framework.
- Tests target behaviour and failure cases. No tests that simply repeat constants or assert prose. Keep pure domain logic testable without Docker. Add a regression test for substantive bugs.
- No magic environment reads scattered through domain logic. Validate configuration at startup. Missing required configuration fails clearly; fixture mode is explicit.
- Use structured errors with stable code and correlation ID; never swallow authorization errors. Logs contain service/action/correlation IDs and outcomes, not credentials, cookies, full documents or taxpayer names.
- New dependencies need a concrete task use, official source, compatible licence and lockfile update. Do not auto-upgrade vendor stacks. Review available audit results and prioritize applicable runtime risks; an audit finding is not automatically a release blocker without context.

## Frappe/backend

Use DocType validation and framework-supported migrations, not manual database edits. Keep migrations repeatable and test on a disposable site. Use permission-aware reads and explicit document/tenant checks for writes. `get_all`, raw SQL and `ignore_permissions` bypass protections: restrict them to documented internal paths with explicit scope checks and negative tests. Avoid long network calls inside a database transaction. Outbox jobs deliver committed changes; retryable handlers must be idempotent. Never directly edit another service's database.

## Integration/API

Follow docs/API_STANDARD.md. Keep each vendor adapter behind an explicit interface. Derive actual vendor endpoints/authentication from deployed specs or locked source. Do not silently substitute fixtures after live errors. Use typed request/response validation at boundaries; do not trust client tenant IDs or model-produced identifiers.

## Quality checks

Run `python3 scripts/dev.py check` for dependency-free workspace checks. Run `python3 scripts/quality.py` after installing dev dependencies for Python lint/format/type checks and frontend lint/format. Run relevant service/UI tests for changed behaviour. The first Vue app task must extend npm check with its build/typecheck and component tests; do not leave new file types outside checks. Product integration tests are added with their implementation tasks; a tooling-only green CI is not product acceptance.

One task branch, independent checkout, current main, focused commits, explicit branch push and human-reviewed PR. No self-merge or direct main push. Free-plan review is a team convention supported by CI and CODEOWNERS, not a server-enforced guarantee. Do not keep asking for a paid plan.
