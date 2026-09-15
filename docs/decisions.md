# Decisions

Append-only. Format: **decision** · alternatives considered · reason. Newest at the bottom.

---

### D-001 · 2026-09-14 · Governing plan is plan v1 + Addendum 1; public copies only

- **Decision:** `docs/plan.md` (Project A sections of the master plan) and `docs/addendum1.md` govern the build. Project A is six weeks: weeks 1–4 as planned, week 5 retrieval (A14–A18), week 6 exception agent (A13). The repo holds public, scrubbed copies; engagement-specific material stays outside the repo.
- **Alternatives:** copy the master plan verbatim; keep the original five-week plan and treat retrieval/A13 as stretch.
- **Reason:** the repo is public, so nothing identifying a real engagement may enter git history. The addendum closes the retrieval and agentic gaps and says to defer A13 before retrieval if time is short.

### D-002 · 2026-09-14 · Dependency lower bounds resolved from PyPI

- **Decision:** `pyproject.toml` lists `>=` lower bounds at the versions resolved from PyPI on 2026-09-14 and installed in `.venv`. No lockfile yet.
- **Alternatives:** exact pins; a `uv.lock`.
- **Reason:** CLAUDE.md forbids pinning unverified versions. Lower bounds are verified-installed; exact pins wait until the stack stabilises (before the week-6 deploy). *Recorded retroactively from the `pyproject.toml` comment.*

### D-003 · 2026-09-14 · SQLite fallback when `DATABASE_URL` is unset

- **Decision:** `app/config.py` falls back to `sqlite:///data/m3tdf.sqlite` when `DATABASE_URL` is empty; Docker Compose always sets Postgres.
- **Alternatives:** Postgres-only (tests require a running database).
- **Reason:** Docker is not installed on the dev machine yet; unit tests must run without it. **Open question for task 3:** SQLite has no `bronze/silver/gold/ops` schemas — either `ATTACH` one file per schema or run migration tests against Postgres only. *Recorded retroactively from `app/config.py`.*

### D-004 · 2026-09-15 · Make targets with `scripts/` equivalents

- **Decision:** keep the `Makefile` from the plan, but every target is a one-liner over `python -m pytest`, `python scripts/<name>.py`, or `docker compose`. `scripts/README.md` lists the no-make equivalent for each. Targets are added only when the command they run exists.
- **Alternatives:** `invoke` + `tasks.py` (plan §1.2 option); Make only.
- **Reason:** `make` is not installed on the Windows dev machine and CMD cannot run multi-line commands. `invoke` adds a dependency for no gain. Stub targets for unbuilt commands would be dead code.

### D-005 · 2026-09-15 · Container baseline: `python:3.12-slim`, `postgres:18-alpine`, deps via uv

- **Decision:** the app image is `python:3.12-slim`; dependencies install from `pyproject.toml` with `uv pip install --system -r pyproject.toml` (uv 0.11.3, same as the dev machine). Postgres is `postgres:18-alpine`. Compose runs `db` + `app` only; the Streamlit `portal` service is added in week 2 when `portal/` exists.
- **Alternatives:** `pip install .` (fails: no build backend, flat multi-package layout); Postgres 16/17.
- **Reason:** tags verified on Docker Hub on 2026-09-15; 18 is the newest major. Installing dependencies without packaging the app avoids adding a build backend just for Docker. **Not yet verified by a real `docker compose up` — Docker is not installed locally.**

### D-006 · 2026-09-15 · Repo hygiene test for engagement terms

- **Decision:** `tests/test_repo_hygiene.py` scans every text file in the repo (excluding `.venv`, `data`, `.git`) for banned engagement terms and fails on any whole-word match.
- **Alternatives:** manual review before publishing; a pre-commit hook.
- **Reason:** the planning documents started from a private plan. A test runs on every `make test` and in CI; a hook can be skipped. The banned list matches the synth generator's own guard.
