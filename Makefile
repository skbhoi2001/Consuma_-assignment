PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: install up down db-native test lint fmt clean migrate migrate-down seed db-reset simulator gateway receiver smoke stack scenario1 scenario2 scenario3 scenarios loadtest bench bench-stretch frontend-install frontend frontend-build

install:            ## create venv and install deps
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

up:                 ## start Postgres + Redis in Docker
	docker compose up -d --wait

down:               ## stop Docker services
	docker compose down

db-native:          ## start natively installed Postgres + Redis
	devdb-start

test:               ## run tests
	$(PY) -m pytest -q

lint:               ## lint + format check
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

fmt:                ## auto-format
	.venv/bin/ruff format .
	.venv/bin/ruff check --fix .

clean:              ## remove caches
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

migrate:            ## apply all migrations
	.venv/bin/alembic upgrade head

migrate-down:       ## roll back all migrations
	.venv/bin/alembic downgrade base

seed:               ## load config/models.yaml into the models table
	$(PY) -m backend.db.seed

db-reset: migrate-down migrate seed   ## wipe and rebuild the schema

simulator:          ## run the provider simulator on :9000
	.venv/bin/uvicorn backend.simulator.app:create_app --factory --port 9000

gateway:            ## run the gateway API on :8000
	.venv/bin/uvicorn backend.gateway.app:create_app --factory --port 8000

receiver:           ## run the test callback receiver on :9100
	.venv/bin/uvicorn backend.receiver.app:create_app --factory --port 9100

smoke:              ## live Scenario-3 check (simulator, receiver, gateway must be running)
	$(PY) scripts/smoke_batch.py --n 10000

stack:              ## simulator :9000 + receiver :9100 + gateway :8000 (Ctrl-C stops all)
	$(PY) scripts/stack.py

scenario1:          ## Scenario 1 - reach provider capacity (~8-10 min)
	$(PY) -m loadgen scenario scenarios/s1.yaml

scenario2:          ## Scenario 2 - different models, changing limits (~9-11 min)
	$(PY) -m loadgen scenario scenarios/s2.yaml

scenario3:          ## Scenario 3 - async batch + callback (~1 min)
	$(PY) -m loadgen scenario scenarios/s3.yaml

scenarios: scenario3 scenario1 scenario2   ## all three (~20 min)

RATE ?= 300
DURATION ?= 60
MODELS ?= model-a=0.6,model-b=0.4
loadtest:           ## custom load: make loadtest RATE=500 DURATION=120
	$(PY) -m loadgen run --rate $(RATE) --duration $(DURATION) --models $(MODELS) --batch-size 20

bench:              ## required benchmark: 400k/s offered, 30M RPM across 10 processes
	$(PY) -m loadgen bench --shards 10 --rate 400000 --duration 20 --rpm 30000000

bench-stretch:      ## stretch: 1.2M/s offered, 72M RPM
	$(PY) -m loadgen bench --shards 10 --rate 1200000 --duration 20 --rpm 72000000 --tpm 72000000000

frontend-install:   ## install UI dependencies
	cd frontend && npm install

frontend:           ## UI dev server on :5173 (proxies the API to :8000)
	cd frontend && npm run dev

frontend-build:     ## type-check + production build
	cd frontend && npm run build
