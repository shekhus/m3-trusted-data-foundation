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
- **Reason:** Docker is not installed on the dev machine yet; unit tests must run without it. **Open question for task 3:** SQLite has no `bronze/silver/gold/ops` schemas — either `ATTACH` one file per schema or run migration tests against Postgres only. **Resolved by D-009: Postgres only.** *Recorded retroactively from `app/config.py`.*

### D-004 · 2026-09-15 · Make targets with `scripts/` equivalents

- **Decision:** keep the `Makefile` from the plan, but every target is a one-liner over `python -m pytest`, `python scripts/<name>.py`, or `docker compose`. `scripts/README.md` lists the no-make equivalent for each. Targets are added only when the command they run exists.
- **Alternatives:** `invoke` + `tasks.py` (plan §1.2 option); Make only.
- **Reason:** `make` is not installed on the Windows dev machine and CMD cannot run multi-line commands. `invoke` adds a dependency for no gain. Stub targets for unbuilt commands would be dead code.

### D-005 · 2026-09-15 · Container baseline: `python:3.12-slim`, `postgres:18-alpine`, deps via uv

- **Decision:** the app image is `python:3.12-slim`; dependencies install from `pyproject.toml` with `uv pip install --system -r pyproject.toml` (uv 0.11.3, same as the dev machine). Postgres is `postgres:18-alpine`. Compose runs `db` + `app` only; the Streamlit `portal` service is added in week 2 when `portal/` exists.
- **Alternatives:** `pip install .` (fails: no build backend, flat multi-package layout); Postgres 16/17.
- **Reason:** tags verified on Docker Hub on 2026-09-15; 18 is the newest major. Installing dependencies without packaging the app avoids adding a build backend just for Docker. ~~Not yet verified by a real `docker compose up`.~~ Verified 2026-09-15: image builds, `/healthz` healthy, Postgres 18.6 (see D-009).

### D-006 · 2026-09-15 · Repo hygiene test for engagement terms

- **Decision:** `tests/test_repo_hygiene.py` scans every text file in the repo (excluding `.venv`, `data`, `.git`) for banned engagement terms and fails on any whole-word match.
- **Alternatives:** manual review before publishing; a pre-commit hook.
- **Reason:** the planning documents started from a private plan. A test runs on every `make test` and in CI; a hook can be skipped. The banned list matches the synth generator's own guard.

### D-007 · 2026-09-15 · Import the synth generator as-is; keep its module layout

- **Decision:** `synth/` is the prepared generator (seed `20260912`), imported with its own modules (`generate`, `config`, `world`, `events`, `gold`, `export`, `bi_tools`, `knowledge`, `kb_questions`, `truth`) and probes under `synth/probes/`. CLAUDE.md's layout line was updated to match rather than renaming modules to `generator/quirks/faults/drift_events`. Its ground-truth tests live in `tests/test_synth.py`. `pyarrow>=25.0.1` added (verified on PyPI 2026-09-15). The RAG probe needs scikit-learn, which is deferred to week 5.
- **Alternatives:** rewrite the generator to the names in CLAUDE.md; keep it in a separate repo.
- **Reason:** its self-check, calibration and 40 integrity tests are the value; renaming adds risk and no information. Output was verified against the shipped sample: all 7 metric series equal, faults.json equal as JSON, sampled source CSVs byte-identical. Probe results match its README (fault recall 96.9%, precision 99.6%; A1 attribution top-1 correct).
- **Edits made on import (all verified output-neutral by regenerating and diffing — only timestamps differ):** UTF-8 + LF on every file write so Windows and Linux produce identical bytes; lint/type fixes (`cast`, annotations, loop-variable binding); scrubbed engagement references from `synth/README.md`; one shared banned-term list (`tests/banned_terms.py`) now scans all generated text, not three files.
- **Finding for week 3:** `fault_probe` reports V006 32/50 caught with 2 unexplained flags, where the generator README says 33/50. The ceiling in `faults.json` is 33. Investigate when V006 is implemented — likely a lot/expiry boundary (same-day expiry) rather than a data defect.

### D-008 · 2026-09-15 · Fix nondeterministic key order in faults.json; lock the answer key

- **Decision:** `truth._achievable_ceiling` iterated a `set`, so key order in `faults.json` changed with `PYTHONHASHSEED` (content identical). Now sorted. `data/` stays gitignored, but `tests/ground_truth.lock` commits sha256 hashes of 95 generated files (ground truth JSON except the timestamped manifest, source extracts, masters, report feeds, KB); `tests/test_ground_truth_lock.py` fails on any drift. Update with `python scripts/lock_ground_truth.py` only for intended changes, logged here.
- **Alternatives:** commit `data/ground_truth/` itself; trust the seed.
- **Reason:** CLAUDE.md says never rewrite ground truth; the seed alone did not guarantee that (this bug proved it). Hashes make any change loud without committing 18 MB. Verified: two runs with different `PYTHONHASHSEED` are byte-identical apart from timestamps, and the lock test fails when one file is altered. Parquet is excluded because its bytes depend on the pyarrow version.

### D-009 · 2026-09-15 · Migrations and database tests are Postgres-only; the app container migrates on start

- **Decision:** `db/migrate.py` applies numbered plain-SQL files from `db/migrations/` to Postgres and refuses any other backend. Each file runs in one transaction with its `public.schema_migrations` row (version, name, sha256 of the LF-normalised file); a session advisory lock serialises runners. It refuses an applied file whose checksum changed, an applied file missing from disk, and a new file numbered below the latest applied one. `0001` creates `bronze/silver/gold/ops`; `0002` creates the six ops tables named in CLAUDE.md with the plan §2.4 columns and constraints that encode the contracts (one confirmed mapping per source; one exception per batch × rule × row; a resolved exception needs who/when/what; `rule_id` matches `V###`). `bronze.raw_*`, `silver.*`, `gold.*` and the later ops tables (`tool_calls`, `runs`, `reconciliation`) arrive with their features. Database tests take a fresh throwaway database per test via the `pg_url` fixture and **skip** when Postgres is unreachable; `REQUIRE_POSTGRES=1` turns the skip into a failure (for CI). The Dockerfile runs `scripts/migrate.py` before uvicorn, so `docker compose up` alone yields the schemas (week-1 definition of done).
- **Alternatives:** one SQLite file per schema via `ATTACH` (tests run without Docker); Alembic; migrate only via `make migrate`.
- **Reason:** production is Postgres and the schema will use Postgres features (`jsonb`, partial unique indexes, regex checks, `gen_random_uuid`), so SQLite tests would test a different database. Alembic adds autogenerate machinery CLAUDE.md rules out (plain SQL, no ORM schema). Migrating on start is safe under the advisory lock. The SQLite fallback in `app/config.py` (D-003) stays for the API's `/healthz` only and is not migrated.
- **Verified:** 13 migration tests green against Postgres 18.6 in Compose, including a deliberately broken migration leaving no table and no record (this caught a real bug: SQLAlchemy did not track the transaction opened by the raw psycopg call, so the rollback was a no-op — fixed by beginning the transaction explicitly). From `docker compose down -v`, `docker compose up` applies 0001–0002 and a restart reports "database is up to date". With Postgres down the 7 database tests skip in ~6 s.
