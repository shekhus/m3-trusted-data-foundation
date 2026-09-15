.PHONY: up down logs api test lint

# Every target is one line. No-make equivalents: scripts/README.md.
PY ?= .venv/Scripts/python

up:                   ## Postgres + app in Docker
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f app

api:                  ## run the API locally (SQLite unless DATABASE_URL is set)
	$(PY) -m uvicorn app.main:app --reload

test:
	$(PY) -m pytest

lint:                 ## ruff + mypy
	$(PY) scripts/lint.py
