# Inference Gateway

High-throughput inference processing that respects changing per-model
RPM (requests/min) and TPM (tokens/min) provider limits.

## Prerequisites
- Python 3.12+
- Node 20+
- Docker **or** local PostgreSQL 16 + Redis 7

## Quick start
    cp .env.example .env
    make install
    make up          # or: make db-native
    make test

## Architecture
_TODO_

## Running the services
_TODO_

## Configuring models and changing limits
_TODO_

## Validation scenarios
_TODO_

## Benchmarks
_TODO_

## Design decisions and tradeoffs
_TODO_

## Scaling to 10B requests/min
_TODO_

## AI usage
_TODO_
