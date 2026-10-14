# Project: High-throughput inference gateway (interview assignment)

## Stack
FastAPI + Uvicorn, Redis, PostgreSQL (SQLAlchemy async + Alembic),
React + Vite + TypeScript (frontend), Python CLI load generator.

## Hard rules
- Never exceed a model's RPM or TPM in ANY rolling 60s window.
- Every request ends in exactly one state;
  submitted = completed + waiting + expired + rejected.
- Batch callback fires only when all requests are final; retried with backoff.
- Each request id appears exactly once in batch results.
- Keep measured results separate from projections.

## Workflow
- One feature per commit; conventional commits (feat:, fix:, test:, docs:, perf:, chore:).
- Write tests with every feature; run `make test` and `make lint` before finishing.
- Plan before coding; don't add features outside the current commit's scope.

## Layout
backend/{common,db,simulator,dispatcher,gateway,callbacks,receiver}, loadgen/, tests/,
frontend/, scenarios/, results/, docs/
