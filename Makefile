PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: install up down db-native test lint fmt clean

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
