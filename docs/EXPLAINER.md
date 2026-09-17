# Explainer — M3 Trusted Data Foundation

Every decision in this project, in four layers:

> **What it is** — in plain words, assuming nothing.
> **Why it exists** — the failure it prevents. This is the layer that matters.
> **What was chosen, and what was rejected** — the alternatives, and why they lost.
> **The number** — the evidence, and where to check it.

Read the first layer of each section and you will understand the system. Read all four and you can defend it
in a room. Each section ends with **"the sentence"** — what to actually say out loud when someone asks.

All data is synthetic; the customer, Prairie Bend Foods, is fictional. Decision references like `D-014` point
at [`decisions.md`](decisions.md), where each has its alternatives and evidence in full.

---

## The problem, before any code

A food manufacturer runs Infor M3. Three plants send extracts. Every plant's extract is *slightly* different —
different column names, different date formats, one of them ships weights in kilograms. Downstream, two BI
tools report "OTIF" and disagree with each other, and nobody can say which is right.

The request that arrives is: *"we want an AI agent."*

**Why that is not a problem statement.** An agent that reads untrustworthy numbers produces confident,
untrustworthy sentences — faster than a person could, and harder to check. The first job is not intelligence,
it is trust: knowing what the numbers mean, where they came from, and what was thrown away on the way.

> **The sentence:** "You don't have an AI problem yet. You have three spreadsheets that disagree, and an agent
> would just argue with them faster."

---

## Week 1 — Foundations

### 1.1 Why a synthetic world, and why it has an answer key

**What it is.** A generator that creates a fictional manufacturer's data: plants, customers, items, orders,
deliveries, inventory, yield — and then deliberately breaks some of it in ways we write down.

**Why it exists.** You cannot measure a data quality system without knowing the right answer. If you point it
at real data, "it found 400 problems" is unfalsifiable: nobody knows how many there were. With seeded faults
and a written answer key, "recall 100%, precision 99.6%" is a claim somebody can check.

**Chosen / rejected.** Chosen: a deterministic generator (same seed, same world) with a committed answer key
(`D-007`, `D-008`). Rejected: anonymised real data — it carries the client's shape, which is exactly what
must not appear in a portfolio piece; and you still would not know the true fault count.

**The number.** The generator's key order was nondeterministic at first, which quietly changed the answer key
between runs — fixed and locked (`D-008`). Every evaluation since is reproducible from `make synth`.

> **The sentence:** "I can tell you the recall because I know what was wrong with the data before I started."

### 1.2 Postgres only, and why the SQLite fallback was a mistake worth admitting

**What it is.** The app refuses to run its database tests against anything but Postgres.

**Why it exists.** SQLite is a different database wearing the same interface. A test suite that passes on
SQLite and deploys to Postgres tests the parts that agree and hides the parts that matter — types, constraints,
concurrency, advisory locks.

**Chosen / rejected.** The first version allowed SQLite when `DATABASE_URL` was unset (`D-003`); that was
reversed (`D-009`). Postgres-only, and in CI a missing database **fails** the build rather than skipping the
tests — a skipped test is a green tick that means nothing.

> **The sentence:** "Tests that skip when the database is missing are tests that pass when it's broken."

### 1.3 `.env` wins over the shell

**What it is.** The repo's `.env` file overrides variables already exported in your terminal (`D-012`).

**Why it exists.** A developer machine has an unrelated `DATABASE_URL` exported from another project. Without
this rule, `make test` silently runs against the wrong database.

**The honest catch:** this bit me in week 9. A subprocess re-read `.env` and ignored the URL I'd exported for
a test. The rule was right; my expectation was wrong — and it is why `scripts/run_metrics.py` later grew an
explicit `--database-url` flag instead of trusting the environment.

> **The sentence:** "Configuration precedence is a decision, not a default. Write it down or it bites you at
> the worst moment."

---

## Week 2 — Profiling and mapping

### 2.1 The profiler infers from data, not from documentation

**What it is.** Given a file, work out what each column actually contains — type, nullability, ranges,
candidate keys — and where it disagrees with the master data.

**Why it exists.** Documentation describes what a field was meant to hold in 2013. The data tells you what it
holds now. A profiler that reads the docs inherits their optimism.

**Chosen / rejected.** Inference from files and masters only, with findings committed and checked against the
answer key (`D-010`). Rejected: trusting the header row's implied types.

> **The sentence:** "The column called `DATE` contains four different formats. The documentation does not
> mention that, because the documentation was written before the third plant existed."

### 2.2 The heuristic mapper exists to make the LLM prove itself

**What it is.** Two mappers from a plant's columns to the canonical schema: a rules-and-similarity heuristic,
and an LLM. Both are measured against the same answer key.

**Why it exists.** This is the single most important idea in the project. Without a baseline, "the LLM got 87%"
is meaningless — 87% against what? The heuristic is free, runs offline, works when the provider is down, and
turns the LLM's contribution into a *measured lift* rather than a vibe.

**Chosen / rejected.** Chosen: heuristic first, LLM second, both scored on known headers *and on headers
neither has seen* (`D-013`, `D-014`). Rejected: LLM-only (no floor, no fallback, unmeasurable); heuristic-only
(leaves real accuracy on the table for unfamiliar headers).

**The number.** Mapping accuracy top-1 **100%** on known and unseen headers, LLM-assisted — with the heuristic
reported beside it, always.

> **The sentence:** "The heuristic is the floor. It's free, it runs when the API is down, and it's the only
> reason I can tell you what the model is actually worth."

### 2.3 The LLM proposes; a person confirms; the mapping is versioned

**What it is.** The model suggests a mapping. Nothing uses it until an owner confirms it, and the confirmed
mapping is bound to an exact header fingerprint (`D-013`, `D-014`).

**Why it exists.** A mapping is a business decision with an audit trail, not a prediction. Wrong mappings are
the highest-cost silent failure in integration work: nothing errors, the numbers are simply wrong, and they
stay wrong for months.

**Chosen / rejected.** One structured-output call, retry once on invalid output, then fall back to the
heuristic (`D-014`). Rejected: auto-applying high-confidence mappings — confidence is the model's opinion of
itself.

> **The sentence:** "The model reads column names well. It has no authority."

---

## Week 3 — Validation and the exception queue

### 3.1 Nothing is ever silently dropped

**What it is.** Twelve deterministic rules run over every row. A row that fails does not vanish — it becomes an
**exception** with an owner, a severity and a reason, keyed back to its source row (`D-018`, `D-021`).

**Why it exists.** The failure mode this project is named after: a filter in a pipeline quietly removes 4,000
rows, the dashboard still renders, and nobody finds out until a customer asks why their order isn't there. A
dropped row is invisible; an exception is a queue somebody owns.

**Chosen / rejected.** Deterministic DataFrame checks, not model judgement — a validation rule must produce
the same verdict twice. Rejected: `WHERE valid = true` (the silent drop), and LLM-judged validity (unrepeatable,
and impossible to explain to an auditor).

**The number.** **51,651 of 51,651 rows × 12 rules** — coverage is a hard gate, not a metric. Exception recall
**100%**, row precision **99.6%**. One rule's ceiling (V006) was corrected *from the data* when the answer key
disagreed with the assumption.

> **The sentence:** "Every row is accounted for. Not 'most rows' — every row, and the ones that failed have
> somebody's name on them."

### 3.2 Resolution is append-only, and only an owner may publish a fix

**What it is.** Exceptions move through assign → resolve, and every event is appended rather than overwritten
(`D-021`).

**Why it exists.** "Who decided this was fine, and when?" must survive the next edit. An updated status field
answers "what is it now"; it cannot answer "what happened".

> **The sentence:** "The current state tells you where you are. The event log tells you how you got there, and
> that's the one the auditor wants."

---

## Week 4 — Lineage, drift and publishing

### 4.1 Drift is a change in shape, detected before it breaks anything

**What it is.** A coarse fingerprint per source file. When the shape changes — a renamed column, a new field, a
vanished one — that is a drift event with one alert, and the batch cannot publish until a mapping is confirmed
for the new shape (`D-022`, `D-023`).

**Why it exists.** The old world: the source changes on Friday, the dashboard goes blank on Monday, and three
people spend a day finding out why. The new world: the pipeline names the breakage before it publishes.

**Chosen / rejected.** One alert per new shape, not per row (`D-022`). Rejected: per-row alerting, which
produces 40,000 identical notifications and trains everyone to ignore them.

**The number.** **2 of 2 drift events detected and named, zero false alerts.**

> **The sentence:** "Source changed → fingerprint changed → publish blocked → named breakage. Compare that to
> a blank dashboard on Monday morning."

### 4.2 Lineage is recorded at confirm; impact is derived, not listed

**What it is.** When a mapping is confirmed, the path from source column → canonical field → metric → view is
recorded. Impact ("what breaks if this changes?") is computed from the compiled metric definitions rather than
maintained by hand (`D-020`).

**Why it exists.** A hand-maintained impact list is correct on the day it is written. Deriving it from the
definitions means it cannot drift from reality.

> **The sentence:** "Nobody updates a lineage document. So don't have one — derive it."

### 4.3 Publishing refuses more than it accepts

**What it is.** Silver → gold per batch, refusing stale batches, refusing drifted batches, holding back rows
that are in an unresolved exception state — owner only (`D-023`).

**Why it exists.** The last gate before numbers become "the truth" is the cheapest place to stop a bad number,
and the most expensive place to be wrong.

> **The sentence:** "Publishing is the moment a number stops being data and starts being a decision. It gets
> the strictest gate in the system."

---

## Week 5 — One definition, one truth

### 5.1 The metric dictionary: define OTIF once

**What it is.** Metrics live in versioned YAML, compiled through a whitelisted expression language into SQL
views (`D-011`). Consumer views are generated from that one definition (`D-024`).

**Why it exists.** Two BI tools disagreeing about OTIF is not a tooling problem, it is a definitions problem:
each tool encodes its own slightly different formula in its own semantic layer, and neither is written down.
One definition, compiled to both, ends the argument.

**Chosen / rejected.** A whitelisted expression language — not arbitrary SQL in YAML, which is a remote code
execution hole wearing a config file's clothes.

**The number.** **114 of 114** period-by-period comparisons agree exactly (0.00 delta), and the reconciliation
explains each BI tool's gap by cause, identified from each tool's own export.

> **The sentence:** "They didn't disagree about the data. They disagreed about the definition — and nobody had
> ever written it down in one place."

### 5.2 Retrieval: the access filter runs in SQL, before ranking

**What it is.** A knowledge base of SOPs and specs, chunked at section level with tables kept atomic, retrieved
by hybrid search (BM25 + embeddings, fused with RRF), where **the permission filter is applied in the SQL query
before anything is ranked** (`D-026`, `D-027`).

**Why it exists.** Filtering after retrieval means the forbidden document was already loaded, ranked, and is
one bug away from the answer. Filtering in the query means it was never a candidate.

**Chosen / rejected.** Postgres + pgvector with Voyage embeddings, and a TF-IDF floor reported beside every
mode — the same "show your baseline" discipline as the mapper. Rejected: a separate vector database (another
system to operate for a corpus this size).

**The number.** Retrieval recall **74% → 87%** with the hybrid, **0 leaks** — and **4 leaks** in the
configuration without the access filter, which is what makes the filter's value measurable rather than assumed.

> **The sentence:** "Four documents leaked when I filtered after ranking. Zero when I filtered in the query.
> That's the whole argument."

### 5.3 Refusal is a feature, in a fixed order

**What it is.** An answer is refused when the access gate says no, when the best evidence is below a floor, or
when the question asks for something outside the corpus — checked in that order (`D-028`).

**Why it exists.** A system that always answers is a system that sometimes invents. The order matters: access
first, because "I don't have that" must never leak the existence of a document the asker cannot see.

**The number.** **51 of 53** answers good, **0 leaks**. Two failures are documented, not hidden: one long
document retrieval miss which also causes an over-refusal.

> **The sentence:** "It refuses before it guesses, and it checks permission before it checks confidence."

### 5.4 The agent that cannot act

**What it is.** One exception-triage agent: five read-only tools, a four-outcome contract, a policy gate whose
SOP floors live in code, and a LangGraph interrupt before `apply` (`D-029`, `D-030`).

**Why it exists.** The agent's job is triage, not authority. The interesting property is not what it can do —
it is what it structurally cannot: the graph cannot reach the node that writes without a person resuming it.

**Chosen / rejected.** Static interrupt before apply, Postgres checkpointer, `apply` writing only to
`ops.exceptions` (`D-030`). Rejected: an agent that applies "safe" fixes automatically — "safe" is exactly the
judgement call a policy is for.

**The number.** Agent accuracy **82%** against an 85% target — **missed, and reported as missed**. The coverage
hard gate passes **17/17**. Adversarial fixes blocked: **16/16**, and the allowlist cannot be widened from the
config file.

**The demo moment.** The agent proposes normalising `CUST-0004` to `C000004`. It is *correct*. The SOP permits
automatic changes only to units and date formats, so the gate refuses it in code and it becomes a proposal for
a person.

> **The sentence:** "Here is the gate blocking a fix that is right. That's the point — correct and permitted
> are different questions, and only one of them is the model's to answer."

---

## What this project is really about

Four ideas, in order of how much they matter:

1. **Nothing is silently dropped.** Every row is published, held or explained.
2. **Every number has one definition**, versioned, compiled to every consumer.
3. **The model proposes; a person with authority confirms.** Mappings, resolutions, fixes.
4. **Every claim about the system is measured against a known answer** — including the two that missed.

**376 tests. 33 decisions with alternatives and evidence. CI on every push; deploy only after green.**

> **The closing sentence:** "The report includes what failed. If it didn't, you'd have no reason to believe the
> parts that passed."
