.PHONY: up down logs test scenario1 scenario2 scenario3 bench

up:
	docker compose up -d --build

down:
	docker compose down -v

logs:
	docker compose logs -f

test:
	pytest backend/tests

scenario1:
	python -m loadgen run scenarios/s1.yaml --out results/s1

scenario2:
	python -m loadgen run scenarios/s2.yaml --out results/s2

scenario3:
	python -m loadgen run scenarios/s3.yaml --out results/s3

bench:
	python -m loadgen run scenarios/bench.yaml --out results/bench
