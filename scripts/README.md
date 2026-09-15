# Scripts

`make` is optional (see `docs/decisions.md` D-004). Every Make target has a single-line equivalent
that works in PowerShell, CMD, or Git Bash from the repo root. Add a row here whenever a target is added.

| Make target | Without make (Windows) |
|---|---|
| `make up` | `docker compose up -d --build` |
| `make down` | `docker compose down` |
| `make logs` | `docker compose logs -f app` |
| `make api` | `.venv\Scripts\python -m uvicorn app.main:app --reload` |
| `make test` | `.venv\Scripts\python -m pytest` |
| `make lint` | `.venv\Scripts\python scripts/lint.py` |

Multi-step operations always live in a `scripts/*.py` file, never in a multi-line shell command.
