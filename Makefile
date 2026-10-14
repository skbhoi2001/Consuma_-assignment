PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin

.PHONY: install up down logs ps test lint clean

install: $(VENV)/.installed

$(VENV)/.installed: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"
	@touch $@

.env:
	cp .env.example .env

up: .env
	docker compose up -d --wait postgres redis
	docker compose ps

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

test: install
	$(BIN)/pytest

lint: install
	$(BIN)/ruff check backend loadgen

clean:
	docker compose down -v
	rm -rf $(VENV) .pytest_cache .ruff_cache
