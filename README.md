# inference-gateway

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
scenarios/      s1.yaml, s2.yaml, s3.yaml, bench.yaml
results/        saved benchmark evidence
docs/           benchmark report, 10B/min plan, workflow PDF
```

## Commands

| Command          | What it does                        |
|------------------|-------------------------------------|
| `make up`        | Start all services via docker compose |
| `make down`      | Stop and remove containers/volumes  |
| `make test`      | Run backend tests                   |
| `make scenario1` | Run scenario 1 (likewise 2, 3)      |
| `make bench`     | Run the benchmark scenario          |
