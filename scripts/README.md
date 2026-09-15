# Scripts

`make` is optional (see `docs/decisions.md` D-004). Every Make target has a single-line equivalent
that works in PowerShell, CMD, or Git Bash from the repo root. Add a row here whenever a target is added.

| Make target | Without make (Windows) |
|---|---|
| `make synth` | `.venv\Scripts\python -m synth.generate --out data --clean` |
| `make probe` | `.venv\Scripts\python scripts/probe.py` |
| `make profile` | `.venv\Scripts\python scripts/profile.py` |
| `make metrics` | `.venv\Scripts\python scripts/compile_metrics.py --apply` (drop `--apply` to compile without a database) |
| `make name-check` | `.venv\Scripts\python scripts/check_company_name.py` |
| `make up` | `docker compose up -d --build` |
| `make down` | `docker compose down` |
| `make logs` | `docker compose logs -f app` |
| `make migrate` | `.venv\Scripts\python scripts/migrate.py` (needs `DATABASE_URL`; or `docker compose exec app python scripts/migrate.py`) |
| `make api` | `.venv\Scripts\python -m uvicorn app.main:app --reload` |
| `make test` | `.venv\Scripts\python -m pytest` |
| `make lint` | `.venv\Scripts\python scripts/lint.py` |

Multi-step operations always live in a `scripts/*.py` file, never in a multi-line shell command.
