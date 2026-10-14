# CLAUDE.md — project rules

High-throughput inference gateway that respects per-model RPM/TPM limits.
The brief is in `docs/high-throughput-inference-assignment.pdf`; re-read it when in doubt.

## Layout
- `backend/gateway` — FastAPI app: routes, schemas
- `backend/dispatcher` — queues, rate limiter, workers, retries
- `backend/callbacks` — batch completion callback worker
- `backend/simulator` — provider simulator (HTTP + in-process)
- `backend/receiver` — test callback endpoint
- `backend/db` — models, alembic migrations
- `loadgen/` — load generator CLI, scenario presets, report generator
- `frontend/` — React + Vite dashboard
- `scenarios/` — scenario configs; `results/` — committed benchmark evidence; `docs/` — reports

## Commands
- `make install` — create `.venv` and install `.[dev]`
- `make up` / `make down` — start/stop Postgres + Redis (host ports from `.env`, default 5433/6380)
- `make test` — run pytest; `make lint` — ruff

## Rules
- Never exceed a configured RPM or TPM limit in any 60-second window. Limits can change at
  runtime; the limiter must pick up changes without a restart.
- Every request must end in exactly one accounted state (completed-success, completed-failure,
  waiting, expired, rejected). No silent drops; no double counting.
- "Completed" means the simulated provider call reached a final state, not that the API accepted it.
- Batch callbacks fire only after every request in the batch is final; delivery retries with backoff
  and is idempotent (same payload, same batch id).
- Keep measurements and projections separate in reports. State simulation assumptions explicitly.
- The hot path stays async and allocation-light; no per-request DB round trips on the dispatch path.
- Add or update tests alongside behaviour changes; `make test` must pass before committing.
- Config lives in env vars / scenario YAML, never hardcoded. Don't commit `.env`.
- Commits follow Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`).
