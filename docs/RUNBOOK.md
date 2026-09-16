# Runbook — M3 Trusted Data Foundation

For whoever is on call. Every section matches an alert from the portal's **Ops** tab (`GET /ops/summary`), so
the alert tells you which section to read. All data is synthetic and the customer is fictional; the operational
shape is real.

**Where things are**

| | |
|---|---|
| API | https://app-production-4016.up.railway.app — `/healthz`, `/docs` |
| Portal | https://portal-production-6ba2.up.railway.app |
| Hosting | Railway project `m3-trusted-data-foundation`: services `app`, `portal`, `postgres` (pgvector on a volume) |
| CI/CD | GitHub Actions `ci`: lint, tests, strict evals; deploy runs only on green pushes to `main` |
| Keys | Railway variables on `app` (`railway variables --service app --kv`); roles are `viewer`, `analyst`, `owner` |

**First moves for any incident**

1. `GET /healthz` (public, no key). Not 200 → *Service down*.
2. Portal → **Ops**, or `GET /ops/summary?days=7` with any key. The alert names the section below.
3. `railway logs --service app` for the last deploy and startup lines.
4. Nothing in this system writes to a source system, and nothing reaches gold without an owner. When in doubt,
   stop and leave the queue: held rows are recoverable, published wrong numbers are not.

---

## Failed ingest

**Alert:** `plt0x: N failed batch(es) — re-run ingest (RUNBOOK: failed ingest)`

A source was unreachable or a file could not be read. The batch is recorded `failed`; nothing partial was
published.

1. Find them: `GET /batches?source=plt0x`, or the portal's **Batches** tab.
2. Re-run the ingest (portal **Ingest**, or `POST /ingest/plt0x` with a new `Idempotency-Key`).
3. A replay with the **same** key returns the original result and writes nothing, so retrying is always safe.
4. Still failing → check the adapter's source directory and `railway logs --service app`.

## Mappings

**Alert:** `plt0x: N batch(es) blocked on an unconfirmed mapping (RUNBOOK: mappings)`

A file arrived with a header shape that has no confirmed mapping. This is the system working: nothing is
guessed into silver.

1. Portal → **Mappings** → **Propose mappings** for that source. The heuristic always proposes; the LLM
   proposes too when configured, and falls back to the heuristic if it fails or is unreachable.
2. Review the columns. The transforms matter as much as the names (`kg_to_lb`, the date parser,
   `normalize_customer_no`).
3. **Only an owner may confirm.** Analysts can submit a correction as a new version.
4. Re-run the ingest: blocked files are picked up automatically.

## Stale source

**Alert:** `plt0x: N stale extract(s); publish refuses them (RUNBOOK: stale source)`

The extract is older than `SOURCE_SLA_HOURS` (72 by default). It is ingested and validated, but publish refuses
it, so a stale file cannot quietly become today's numbers.

1. Ask the plant for a fresh extract; ingest it. The fresher batch publishes and both appear in history.
2. If the age is a deployment artefact rather than a real delay (the demo data is generated at image build
   time, so it ages), redeploy to rebuild the image, or raise `SOURCE_SLA_HOURS` deliberately and write down
   why.

## Schema drift

**Alert:** an open drift alert in the portal's **Batches** tab; publish refuses those batches.

A file's shape changed: a column added, renamed, or retyped. The alert names the source column, the gold column
and the consumer views that depend on it, through lineage.

1. Read the alert. It already says what breaks.
2. Confirm a mapping for the new shape (see *Mappings*).
3. Resolve the alert with a note saying what changed. **An alert cannot be resolved until the new header has a
   confirmed mapping**, and only an owner may resolve it.
4. Publish again. Previously refused batches go through.

## Backlog

**Alert:** `N blocking exception(s) older than 3 days, past SOP-DQ-001-v2's window (RUNBOOK: backlog)`

Blocking exceptions hold their rows out of gold. Three business days is the SOP's window for blocking rules.

1. Portal → **Exceptions**, filter by rule, severity and owner. The oldest are listed first in **Ops**.
2. Assign (analyst or owner) and resolve (owner only) with a kind publish can act on:
   - `accept` — the row is correct as sent: publish it.
   - `exclude` — never publish this row.
   - `fixed_at_source` — a corrected extract will replace it; keep it out until then.
3. For a whole pattern, consider the agent (see *Agent approvals*).
4. After a corrected extract: re-run validation for the batch; anything that now passes is closed automatically
   as `no_longer_violated`, and a person's decision is never overwritten.
5. Publish again to pick up newly eligible rows.

## Provider limits

**Alert:** `N model call(s) rate limited in the window (RUNBOOK: provider limits)`

Groq (answers, agent) or Voyage (embeddings) refused calls. Transport limits are waited out within a budget;
beyond that the call fails and the caller degrades.

1. Check `ops.v_model_usage_daily` (Ops tab) for which purpose and model.
2. Nothing breaks silently: the mapper falls back to the heuristic and flags "needs review"; the agent escalates
   saying it could not conclude; the index sync is skipped and the API still starts.
3. Daily quota exhausted → wait for the reset or raise the plan. Re-run whatever degraded afterwards
   (`make eval-agent`, `make index`).
4. A rate-limited eval is not a result: `evals/run_evals.py` counts such runs as `excluded_transport`, never as
   accuracy.

## Agent approvals

**Alert:** `N agent run(s) awaiting an owner's decision (RUNBOOK: agent)`

Every agent run stops before `apply`. Nothing changes until an owner decides.

1. `GET /agent-runs/{run_id}` shows the classification, its evidence, the policy gate's verdict and every tool
   call the run made.
2. Approve or reject: `POST /agent-runs/{run_id}/decision` (**owner only**).
   - Approve assigns the exception to the owner the outcome implies and records the classification as an event.
   - Reject records the decision and leaves the exception alone.
3. A proposed fix the gate refused (`human_proposal`) is a **proposal for a person**, not an applied change. The
   allowlist comes from SOP-DQ-001-v2 and is enforced in code: unit-of-measure and date-format normalisation
   only, never a customer number, item number, quantity or weight.
4. Runs never apply a fix to data. Approved fixes are carried out through the normal correction path (corrected
   extract, or a resolution kind).

## Reconciliation

**Alert:** `latest reconciliation is red: consumer views disagree (RUNBOOK: reconciliation)`

Two consumer views compiled from the same metric definition disagree for some period. They read one definition,
so any difference is a defect.

1. Portal → **Reconciliation** → the red cells name the metric, period and grain.
2. Check whether the compiled views are current: `make metrics` regenerates them from `metrics/*.yaml`.
   Hand-edited SQL is the usual cause, and is forbidden.
3. A legacy-view gap is **not** a failure: the legacy definition differs on purpose and is reported as the
   "before".
4. A BI tool gap is explained by named causes (date basis, weight basis, case tolerance, scope filter). Those
   are differences in the tool's definition, not in gold.

## Service down

**Symptom:** `/healthz` is not 200, or the portal cannot reach the API.

1. `railway logs --service app`. Migrations run at startup and must succeed; a failed migration stops the API
   deliberately.
2. `railway deployment list --service app` — roll back by redeploying the last SUCCESS deployment from the
   Railway dashboard.
3. The portal reaching nothing: check `API_BASE_URL` on the `portal` service. It must resolve the app's private
   domain, `http://${{app.RAILWAY_PRIVATE_DOMAIN}}:8000`.
4. Database: `railway logs --service postgres`. Its data lives on a volume mounted at `/var/lib/postgresql`;
   redeploying the service does not erase it.

## Routine operations

| Task | How |
|---|---|
| Deploy | Push to `main`. CI must pass; the deploy job then rebuilds app and portal. |
| Rebuild the knowledge index | `make index` locally, or redeploy: the app syncs the index at startup (best effort). |
| Regenerate the evaluation report | `make eval` → `evals/REPORT.md` (deterministic sections re-run; model sections come from their saved results). |
| Re-score the agent | `make eval-agent` (real model calls). |
| Rotate an API key | `railway variables --service app --set-from-stdin API_KEYS`, then redeploy. |
| Rotate a provider key | Same, for `GROQ_API_KEY` or `VOYAGE_API_KEY`. |
| Back up the database | `pg_dump` against the Railway Postgres; keep dumps outside the repo (`backups/` is gitignored). |
| Local stack | `docker compose up` — Postgres, API and portal, with `data/` mounted from the working copy. |
