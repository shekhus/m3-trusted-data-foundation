# M3 Trusted Data Foundation

**All data in this repository is synthetic, and the customer — Prairie Bend Foods — is fictional.**
Tables are modelled on Infor M3 order, delivery, inventory and production structures; field-level
mapping to M3 physical columns is to be confirmed with an M3 SME.

> Two BI tools, one truth: AI proposes the mappings, code validates every record, people confirm,
> and drift is caught before the report breaks.

Status: week 1 of 6 — scaffold. The full README (customer, what was broken, how it works, evaluation,
what changes before production) is written in week 6. Until then see `docs/discovery_brief.md` and
`docs/plan.md`.

## Quickstart

Requires Python 3.12 and, for the full stack, Docker.

```
uv venv --python 3.12
uv pip install -r pyproject.toml --extra dev
.venv\Scripts\python -m pytest
docker compose up -d --build        # Postgres + API on http://localhost:8000/healthz
```

Every `make` target has a no-make equivalent in `scripts/README.md`.

## Docs

- `docs/discovery_brief.md` — the problem
- `docs/plan.md`, `docs/addendum1.md` — the build plan
- `docs/decisions.md` — decision log
