# inference-gateway

A high-throughput inference gateway that respects per-model RPM/TPM provider limits, with a
provider simulator, async batch callbacks, and a load generator. See
[`docs/high-throughput-inference-assignment.pdf`](docs/high-throughput-inference-assignment.pdf)
for the brief.

> Status: project scaffold. Services, simulator, and load generator land in later commits.

## Requirements
- Python 3.11+
- Docker (with Compose v2)

## Quick start
```bash
make install   # create .venv and install dependencies
make up        # start Postgres + Redis (creates .env from .env.example on first run)
make test      # run the test suite
make down      # stop containers
```

Postgres is exposed on `localhost:5433` and Redis on `localhost:6380` by default, so they don't
collide with local installs. Change `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT` in `.env` if needed.

## Layout
```
backend/
  gateway/      FastAPI app: routes, schemas
  dispatcher/   queues, rate limiter, workers, retries
  callbacks/    callback worker
  simulator/    provider simulator (HTTP + in-process)
  receiver/     test callback endpoint
  db/           models, alembic migrations
  tests/
loadgen/        CLI + scenario presets + report generator
frontend/       React + Vite dashboard
scenarios/      scenario configs
results/        saved benchmark evidence
docs/           assignment brief, benchmark report, scaling plan
```
