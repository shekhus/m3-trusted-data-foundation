# How this was built — a developer's tour

*The companion to [EXPLAINER.md](EXPLAINER.md). That document answers "why does it work this way". This one
answers "what is in here, in what order was it built, and where do I put my hands".*

*Written for somebody who has cloned the repository, opened it, and felt the way anyone feels opening a
repository with fourteen top-level folders in it. No prior knowledge assumed.*

---

## Chapter 0 · The only three questions that matter

A codebase feels overwhelming when you try to understand all of it at once. You never need to. Every system
like this answers three questions, and every folder belongs to exactly one of them:

**1. Where does data come from, and what shape is it in?**
→ `sources/`, `pipeline/`, `synth/`

**2. What turns it into something people can use?**
→ `rules/`, `metrics/`, `retrieval/`, `agent/`

**3. How do I know any of it is right?**
→ `tests/`, `evals/`, `db/migrations/`

Everything else — `app/`, `portal/`, `scripts/`, `llm/` — is delivery: how a human or another program reaches
the three above.

Hold that in your head and the folder list stops being a wall.

---

## Chapter 1 · The map

Fourteen folders. Here is every one, in a sentence, roughly in the order data flows through them.

| Folder | Lines | What it is |
|---|---:|---|
| `synth/` | 4,550 | **The fake company.** Generates deliveries, inventory, yield — and plants the faults the whole system is measured against. Nothing else depends on it at runtime. |
| `sources/` | 93 | **Adapters.** How a file from a factory becomes rows the pipeline can read. One small module per source type. |
| `pipeline/` | 2,232 | **The path from file to published numbers.** Profiling, mapping, canonicalising, drift, lineage, publish. The heart of the repo. |
| `rules/` | 376 | **The twelve validation rules.** One file per family: completeness, dates and quantities, duplicates, master data. |
| `metrics/` | 494 | **Metric definitions and their compiler.** The YAML files *are* the definitions; `compiler.py` turns them into SQL. |
| `retrieval/` | 1,075 | **The knowledge layer.** Chunking, indexing, hybrid search, access filtering, refusal, answering. |
| `agent/` | 1,278 | **The triage agent.** Its tools, its output contract, its policy gate, and the graph that pauses before it acts. |
| `llm/` | 423 | **The only place this repo calls a model.** One client, one recorder, prompts as files. |
| `app/` | 1,597 | **The HTTP API.** `main.py` plus one router per capability. Thin — the work lives elsewhere. |
| `portal/` | 507 | **The Streamlit UI.** A client of the API, with no database access of its own. |
| `db/` | 131 + 16 SQL | **Migrations** (numbered, checksummed) and the runner that applies them. |
| `scripts/` | 1,177 | **Every multi-step operation as a script**, so the Makefile is thin and Windows works. |
| `evals/` | 1,993 | **The measurements**, and the report they generate. |
| `tests/` | 5,425 | **376 tests.** More lines than any other folder, which is the correct ratio. |

Two observations worth making early.

**`tests/` is the biggest folder.** That isn't diligence for its own sake — it's what lets you change anything
in the other thirteen without fear. If you only read one folder to judge a codebase, read its tests.

**`synth/` is the second biggest and ships nothing.** It exists so every number this project claims can be
checked against a known answer. See Chapter 1 of the explainer for why that's not optional.

---

## Chapter 2 · Follow one row all the way through

The fastest way to understand any pipeline is to trace a single record. Here's one delivery line, from a file
on disk to a number on a dashboard, naming every file it touches.

**A CSV lands.** Somebody uploads a factory extract through the portal, or it's posted to the API.
→ `app/routers/ingest.py` receives it, `sources/` knows how to read that shape.

**Profile it.** Before anything else, look at what's actually there.
→ `pipeline/profile.py` reports each column's real type, null rate, ranges, candidate keys.

**Work out what the columns mean.** Is `del_dt` the delivery date or the deleted-date?
→ `pipeline/mapper/heuristic.py` proposes from string similarity and types.
→ `pipeline/mapper/llm.py` proposes using a model, through `llm/client.py`.
→ `pipeline/mapper/contract.py` defines what a valid proposal looks like — the model cannot return a shape this
rejects.
→ A person confirms via `app/routers/mappings.py`. **Nothing proceeds until they do.**

**Land it raw.** The row goes into bronze exactly as received — no cleaning, no interpretation.
→ `pipeline/ingest.py`, with `pipeline/idempotency.py` making sure the same file twice is not the same row
twice.

**Turn it into the canonical shape.** Now the confirmed mapping is applied and units are normalised.
→ `pipeline/canonical.py`, `pipeline/transforms.py` → silver.

**Check it.** Twelve rules run over every row.
→ `rules/completeness.py`, `rules/dates_quantities.py`, `rules/duplicates.py`, `rules/master_data.py`, all
built on `rules/base.py`.
→ A row that fails becomes an exception via `app/routers/exceptions.py`. **It is never dropped.**

**Has the source changed shape?**
→ `pipeline/drift.py` compares the file's fingerprint to what it saw last time.

**Publish.** Silver becomes gold — the numbers people actually read.
→ `pipeline/publish.py` refuses stale batches, refuses drifted batches, holds rows with unresolved exceptions.
→ `pipeline/lineage.py` recorded where this field came from when the mapping was confirmed.

**Become a metric.**
→ `metrics/otif.v3.yaml` is the definition; `metrics/compiler.py` turns it into SQL in `metrics/compiled/`;
`metrics/consumers/` are the views each BI tool reads.

**Get reconciled.** When two tools disagree, explain why.
→ `pipeline/reconcile.py`, exposed at `app/routers/reconcile.py`.

That's the whole journey. Nine files doing the real work, each with one job. Read those nine in that order and
you understand the system.

---

## Chapter 3 · What a working session actually looked like

Not the romantic version. The actual loop, repeated about twenty-four times over five weeks.

**1. Read three things.** `CLAUDE.md` (the standing rules for this repo), the current week in `docs/plan.md`,
and the last few entries in `docs/decisions.md`. Ten minutes. It prevents the most common failure in a long
build — solving a problem you already solved differently on Tuesday.

**2. State the week's definition of done in one line**, and propose one to three tasks. Then *stop and get
agreement.* A task that turns out to be the wrong task is the most expensive thing in a build, and it's cheapest
to catch here.

**3. One task per turn, with its tests written alongside** — not after. If the test is hard to write, the design
is usually wrong, and you've found out in ten minutes rather than two days.

**4. Run `make lint` and `make test`. Paste the output. Read it before fixing anything.** The discipline is
*analyse then fix*, because the second failure in a list is often the cause of the first.

**5. Write a line in `docs/decisions.md`** — what was decided, what the alternatives were, why this one, what
the evidence is.

**6. Commit, with a message that states the decision** rather than the diff. `git log` becomes a narrative
rather than a list.

That loop produced 33 decisions, 376 tests and no rewrites. The unglamorous steps — 1 and 5 — are the ones
doing the work.

---

## Chapter 4 · The build order, and why that order

Five weeks. Each week's output is the next week's input, which is why the order isn't arbitrary.

### Week 1 — Scaffold and the fake company

**Built:** repo skeleton, settings, database migrations, Docker, CI, and the generator.

**Why first:** you cannot measure a data quality system without knowing the right answer (explainer, Chapter 1).
Everything after this week is scored against what week one produced.

**Key files:** `synth/generate.py`, `synth/faults.py`, `db/migrate.py`, `app/config.py`, `.github/workflows/ci.yml`

**The awkward bit:** I let the database be SQLite when `DATABASE_URL` was unset, so tests would "just run".
Reversed a week later (`D-003` → `D-009`). SQLite is a different database wearing the same interface — a suite
that passes on it and deploys to Postgres tests the parts that agree and hides the parts that matter.

### Week 2 — Profile, map, land

**Built:** the profiler, both mappers, the confirmation flow, bronze and silver.

**Why here:** mapping is the first place a human decision is required, and everything downstream depends on it
being right.

**Key files:** `pipeline/profile.py`, `pipeline/mapper/*`, `pipeline/canonical.py`, `app/routers/mappings.py`

**The thing to copy:** `heuristic.py` was written **before** `llm.py`, deliberately. Without a baseline, "the
model scores 100%" has no denominator.

### Week 3 — Validate, and never drop anything

**Built:** the twelve rules, the exception queue, its workflow, idempotent ingest.

**Why here:** you can only validate a row once it's in a canonical shape — so this had to follow week 2.

**Key files:** `rules/*`, `app/routers/exceptions.py`, `pipeline/idempotency.py`

**Found by the answer key:** one of my rules had a plausible-range ceiling based on an assumption. The answer key
disagreed; the data was right. The rule changed, not the key (`D-018`).

### Week 4 — Lineage, drift, publish

**Built:** fingerprinting, drift events, the lineage graph, the publish gate.

**Why here:** drift detection needs something to compare against — you need at least one confirmed mapping
before "the shape changed" means anything.

**Key files:** `pipeline/drift.py`, `pipeline/lineage.py`, `pipeline/publish.py`

**The rewrite:** publishing was first keyed on a content hash, which made a legitimate re-send of corrected data
indistinguishable from a duplicate. Re-keyed on request identity (`D-019`).

### Week 5 — Metrics, retrieval, the agent, and proving it

**Built:** the metric compiler, consumer views, reconciliation, the knowledge layer, the triage agent, the eval
report, the deploy.

**Why last:** the agent reads exceptions produced in week 3 and documents indexed here; the metric layer needs
published gold from week 4. Every dependency points backwards.

**Key files:** `metrics/compiler.py`, `retrieval/*`, `agent/graph.py`, `agent/policy_gate.py`,
`evals/run_evals.py`

**The number that missed:** the agent at 82% against an 85% target, reported as missed with the failing pattern
named (`D-029`, `D-030`).

---

## Chapter 5 · The plumbing, explained once

These are the pieces every folder relies on. Understanding them removes most of the remaining mystery.

### Dependencies and the virtual environment

`pyproject.toml` declares everything. `uv` installs it — faster than pip, and it resolves the whole set at once
so you get a consistent environment rather than whatever order pip happened to install in.

```
uv venv --python 3.12
uv pip install -r pyproject.toml --extra dev
```

**Dev dependencies are a separate extra** so the production image doesn't ship a test framework.

### The Makefile, and why every target is one line

```makefile
test:
	$(PY) -m pytest
```

Every multi-step operation lives in `scripts/`, and the Makefile just calls it. Two reasons: Windows `cmd`
can't do multi-line shell, and a step you can only run through `make` is a step you can't debug. Everything has
a `python scripts/<name>.py` equivalent, listed in `scripts/README.md`.

### Migrations

`db/migrations/NNNN_name.sql`, plain SQL, applied in order by `db/migrate.py`. Three properties worth knowing:

**Numbered and immutable.** Once applied, a migration's checksum is recorded. Editing it is refused — because
your database has the old version and your colleague's has the new one, and nothing will tell you.

**Advisory lock.** Two containers starting at once won't both try to migrate. One waits.

**Run on container start** (`scripts/start.sh`), before the API binds a port. A container that can't migrate
shouldn't serve traffic.

### Settings

`app/config.py` reads everything from the environment. `.env` is loaded for local work — **and it deliberately
overrides variables already in your shell** (`D-012`), because a developer machine usually has an unrelated
`DATABASE_URL` exported from another project.

That rule caught me out later, in the other repo: a subprocess re-read `.env` and ignored the URL I'd exported
for a test. The rule was right; my expectation wasn't. It's why one-off scripts grew explicit `--database-url`
flags.

### Tests

```
pytest                      # everything
pytest -m postgres          # only the ones needing a database
pytest tests/test_rules.py  # one file
```

Three conventions:

**A `postgres` marker.** Those tests skip when no database is reachable — but in CI, `REQUIRE_POSTGRES=1` makes
them *fail* instead. A skipped test is a green tick that means nothing.

**A fresh database per test** that needs one, created and dropped by a fixture. Slow, and it means no test can
be broken by another test's leftovers.

**Test names are sentences.** `test_a_stale_batch_is_refused` tells you what broke from the failure line alone,
without opening the file.

### Lint

`scripts/lint.py` runs `ruff` (style, imports, line length 110) then `mypy` (types). One command, and it's the
same command CI runs — so "works on my machine" can't diverge from "passes the build".

**Check the exit code, not the tail of the output.** I once read lint output through `tail -2`, saw mypy's
"Success", and committed with ruff failing above it. CI caught it, which is CI doing its job, but it cost a
round trip.

### CI

`.github/workflows/ci.yml`, on every push: install, lint, regenerate the synthetic data, run all tests against
a real Postgres service, run the deterministic evaluations strictly. Deploy only if all of that is green, only
on `main`, and only when a repository variable says deployment is enabled.

**The evaluations run with no model configured.** CI has no API key and a build shouldn't spend money on every
push, so the model-dependent numbers come from a machine that has one — and the report says which.

### Deployment

One Docker image, two roles. `scripts/start.sh` checks `APP_ROLE`: unset runs the API, `portal` runs the UI.
One image means one build, and the portal can never be running different code from the API.

---

## Chapter 6 · Recipes — how to change things safely

The point of the structure is that common changes have an obvious, small shape.

**Add a validation rule.** Write a class in the right `rules/` file, inheriting from `rules/base.py`. Add a test
with a row that should fail and a row that shouldn't. Add its expected finding to the answer key. Done — the
runner discovers it.

**Change a metric definition.** Edit the YAML in `metrics/`, bump the version. `metrics/compiler.py` regenerates
the SQL; the consumer views follow. **Never** edit anything in `metrics/compiled/` — it's generated, and your
edit disappears on the next compile.

**Make a field required from a date.** Edit the field dictionary in `metrics/fields/`. That's it — this is the
seventeen-minute change from `D-025`, and it's short precisely because "required" is data rather than an `if`.

**Add an API endpoint.** New file in `app/routers/`, registered in `app/main.py`. Keep it thin: parse, call the
module that does the work, format the response. A router with business logic in it is a router you can't test
without HTTP.

**Change a threshold or a policy.** Edit the config. If it's an agent policy, note that the allowlist can be
*narrowed* from config but never *widened* — widening is refused at startup, on purpose.

**Add a database table.** New numbered file in `db/migrations/`. Never edit an applied one; add another.

---

## Chapter 7 · If you have thirty minutes

In this order:

1. **`README.md`** — five minutes. What it is and what it measured.
2. **`docs/EXPLAINER.md`** Chapters 0–2 — ten minutes. Why any of it exists.
3. **`pipeline/publish.py`** — five minutes. One file that shows the house style: refuses more than it accepts,
   every refusal explained.
4. **`tests/test_publish.py`** — five minutes. How behaviour is pinned down here.
5. **`docs/decisions.md`**, the last five entries — five minutes. How decisions get recorded.

That's the codebase. Everything else is more of the same shape.

---

## Chapter 8 · What I would tell someone taking this over

**The tests are the specification.** If you want to know what a module promises, its test file says so in
sentences. The code tells you what it does; the tests tell you what it must do.

**`docs/decisions.md` is the memory.** Before changing something that looks odd, search it. Roughly a third of
the odd-looking things are odd because the obvious version was tried and failed — and the entry says how.

**Nothing in `metrics/compiled/` or `data/` is source.** Both are generated. Both can be deleted and rebuilt.

**The generator is load-bearing.** It looks like test scaffolding. It's the measuring instrument, and if you
change the world it produces, every number in `evals/REPORT.md` is about a different company.

**The boring parts are the point.** Numbered migrations, a lint script, one API client, names that are
sentences. None of it is clever, and that's why a change is fifteen minutes instead of an afternoon of
archaeology.

---

*The same tour for the second system is in
[the command center's BUILD.md](https://github.com/shekhus/m3-supply-chain-command-center/blob/main/docs/BUILD.md)
— fewer folders, one extra idea: a workflow that pauses in the middle and survives a restart.*
