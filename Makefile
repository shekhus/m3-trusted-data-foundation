.PHONY: synth probe profile metrics eval-mapping silver eval-exceptions portal name-check up down logs migrate api test lint

# Every target is one line. No-make equivalents: scripts/README.md.
PY ?= .venv/Scripts/python

synth:                ## generate data/ + ground_truth (deterministic, ~30s)
	$(PY) -m synth.generate --out data --clean

probe:                ## fault + anomaly calibration probes against data/
	$(PY) scripts/probe.py

profile:              ## profile data/sources -> docs/findings.md + docs/findings.json
	$(PY) scripts/profile.py

metrics:              ## compile metrics/*.yaml -> metrics/compiled/*.sql and create the views (needs DATABASE_URL)
	$(PY) scripts/compile_metrics.py --apply

eval-mapping:         ## score mapping proposers against ground truth -> evals/results/mapping_<date>.json
	$(PY) scripts/eval_mapping.py

silver:               ## files -> bronze -> silver for one source (needs a confirmed mapping per header), e.g. make silver SRC=plt01
	$(PY) scripts/build_silver.py $(SRC)

eval-exceptions:      ## exception recall/precision of the loaded sources vs faults.json
	$(PY) scripts/eval_exceptions.py

portal:               ## Streamlit mapping console on http://localhost:8501 (needs the API running)
	$(PY) -m streamlit run portal/app.py

name-check:           ## company-name collision checklist (before publishing)
	$(PY) scripts/check_company_name.py

up:                   ## Postgres + app in Docker
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f app

migrate:              ## apply db/migrations to DATABASE_URL (Postgres only; the app container also runs this on start)
	$(PY) scripts/migrate.py

api:                  ## run the API locally (SQLite unless DATABASE_URL is set)
	$(PY) -m uvicorn app.main:app --reload

test:
	$(PY) -m pytest

lint:                 ## ruff + mypy
	$(PY) scripts/lint.py
