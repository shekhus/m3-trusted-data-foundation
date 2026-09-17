# LinkedIn series — twenty posts, two a week for ten weeks

Ready to paste. Each post carries a mechanism, a number and a judgement call, because a post without all three
reads like a summary of somebody else's work.

**What this series is trying to demonstrate.** Not "I can use LangGraph". The forward-deployed engineering
footprint: reframing the ask, working inside somebody else's constraints (SOPs, approval chains, a BI team that
already has opinions), owning the path from discovery through production and the 07:00 page, measuring honestly,
and writing down the decisions — including the ones that were reversed.

**The rules** (plan §4.4, and sense): no client names, no engagement details, no real numbers from anybody's
business. Every figure comes from synthetic data, said plainly in the first post and once more mid-series.
Anything M3-specific gets a sign-off before it goes out.

**Craft notes that apply to all twenty.** First two lines are the whole game — LinkedIn truncates at ~200
characters. No hashtag spam; two at most. Links in the first comment, never the post body. One idea per post;
the temptation to list everything you built that week is the thing that kills the series.

---

# Week 1 — Discovery, and building the ruler before the thing you measure

## Post 1 · "We want an AI agent" is a symptom, not a specification

Someone asks for an AI agent. The forward-deployed answer is not "yes" or "no", it's **"what happens on the
worst Tuesday of your month?"**

Here's what that question surfaced at a multi-plant food manufacturer running Infor M3.

Three plants send extracts of the same data. Same business process, three different shapes: one calls it
`DELIVERY_DT`, one `del_date`, one `Delivery Date`. One ships weights in kilograms while the other two ship
pounds — and nothing in the file says so. Downstream, two BI tools both report "OTIF" and disagree by a couple
of points, every month, and nobody can say which is right.

Put an agent on that and you get confident, wrong sentences produced faster than a human could produce them,
and harder to check. You've automated the argument, not settled it.

So the first build has no agent in it for four weeks. It does profiling, mapping with human confirmation,
deterministic validation, and lineage. The agent arrives in week five, when there is something trustworthy for
it to read.

The reframe isn't a rejection of the ask. It's the sequencing the ask actually requires: **trust first,
autonomy second**. Every FDE conversation I've had that went badly skipped that order.

All data here is synthetic and the customer is fictional — which, as the next post explains, is the point.

*What's the most useful reframe you've made of a request that arrived as a solution rather than a problem?*

**Visual:** the two-project architecture, A feeding B.
**Evidence:** `docs/discovery_brief.md`, `docs/EXPLAINER.md` §"The problem, before any code".

---

## Post 2 · I built the ruler before I built the thing

You cannot evaluate a data-quality system on real data. Not "it's hard" — you *cannot*, and here's the precise
reason.

Point it at production and it reports "4,312 issues found". Is that good? You'd need to know how many issues
were actually there. Nobody knows. The number is unfalsifiable, which makes every claim you build on it
decoration.

So week one built a generator instead: a fictional manufacturer with ~50,000 order lines, three plants, and
then **deliberate faults injected in known places** — a unit mismatch on one plant's weights, date formats that
drift, a customer code that arrives malformed, a field one plant stops sending mid-year.

The answer key is committed to the repo. Every later claim is scored against it.

Two details that mattered more than expected:

**Determinism.** Same seed, same world, byte for byte. The first version had nondeterministic dictionary
ordering, which quietly changed the answer key between runs — so yesterday's 96% and today's 94% were measuring
different worlds. That's the kind of bug that makes a whole evaluation suite meaningless while looking fine.

**A self-check that fails the build.** Generation recomputes each seeded anomaly at its own grain against a
baseline window, and refuses to write the answer key if any effect is absent or points the wrong way. A ground
truth that quietly lies invalidates every number downstream, silently, forever.

That's four days of work producing zero user-visible features — and it's why every number in the rest of this
series is checkable.

*Have you ever shipped a metric you couldn't have proven wrong? How did you find out?*

**Visual:** the seeded-fault table beside the generator's self-check output.
**Evidence:** `D-007`, `D-008`.

---

# Week 2 — Mapping, and the discipline of the honest baseline

## Post 3 · The dumb version exists to make the smart version prove itself

Task: map each plant's columns onto a canonical schema. `DELIVERY_DT`, `del_date`, `Delivery Date` — same field,
three spellings, and about forty more like it across three sources.

Obvious move: give it to an LLM. It's genuinely good at this.

I wrote the dumb version first anyway. Normalised string similarity, a synonym table, type compatibility from
the profiler, and a threshold. A couple of hundred lines. It runs offline, costs nothing, and works when the
provider is having an incident.

Then the LLM. Both scored against the same answer key — and critically, on two sets: headers the system had seen
before, and **headers neither had ever seen**.

Why that second set matters: a mapper evaluated only on known headers is being tested on its memory. The
interesting question is what happens when plant four arrives next quarter with its own spelling of everything.

What the baseline buys you, concretely:

→ "The model scores 100%" becomes meaningful, because you can say what it beat.
→ When the provider is down, mapping still runs — degraded, not stopped.
→ When someone asks "is the LLM worth the cost?", that's an arithmetic question, not a debate.

I'd argue this is the single most transferable habit in applied AI work: **ship the non-AI baseline in the same
PR as the AI**. If you can't be bothered to build it, you've decided the model's value is a matter of faith.

*If your LLM feature vanished tomorrow, what's the floor it falls back to — and have you measured it?*

**Visual:** the two-column scorecard, known vs unseen headers.
**Evidence:** `D-013`, `D-014`.

---

## Post 4 · The model proposes. It never has authority.

A wrong column mapping is the most expensive silent failure in integration work. Nothing errors. The dashboard
renders. `net_weight_kg` lands in a column the metric layer reads as pounds, and every OTIF number is quietly
wrong by a factor of 2.2 for as long as nobody checks.

So mapping in this system has a shape that has nothing to do with AI:

**The model proposes.** One structured-output call with a strict JSON schema, a contract object that validates
the shape, one retry on invalid output, then fall back to the heuristic. It never gets a second opinion on its
own answer — that's the caller's decision, and the caller is code.

**A person confirms.** An owner sees the proposal with the profiler's evidence beside it and accepts or changes
it. Nothing downstream moves until they do.

**The confirmation is versioned and bound.** Not "plant 2 maps like this" but "this exact header fingerprint
maps like this, confirmed by this person, on this date". When the source changes shape, the binding no longer
matches and the pipeline says so rather than guessing.

The temptation is always to auto-apply above some confidence threshold. I didn't, for a reason worth stating
plainly: **confidence is the model's opinion of itself.** It is not a probability of correctness, and treating
it as one is how you end up with a silent 2.2x error in a KPI.

The cost of the human step is about four seconds per header, once, per source shape. The cost of skipping it is
a number nobody can trust and a quarter of retrospective correction.

*Where in your system does a model's output become an action without a person in between — and is that on
purpose?*

**Visual:** the confirmation screen, proposal + profiler evidence side by side.
**Evidence:** `D-013`, `D-014`.

---

# Week 3 — Validation, and the enemy of the silent drop

## Post 5 · The worst line of code in a data pipeline is `WHERE valid = true`

It doesn't throw. It doesn't log. The job goes green, the dashboard renders, and four thousand rows are gone.

You find out when a customer asks where their order went, three weeks later, and somebody spends a day proving
the pipeline "worked correctly".

So this system has a rule with no exceptions: **nothing is ever silently dropped.** Every row ends in exactly
one of three states — published, held, or explained.

Mechanically that means twelve deterministic rules over every row, and a row that fails becomes an **exception**:
an owner, a severity, a reason, and a key back to the exact source row. Not a log line. A queue item somebody is
accountable for.

Two design choices behind it:

**Deterministic, not model-judged.** A validation rule must give the same verdict on the same row twice, and be
explainable to an auditor in one sentence. That rules out an LLM, and it rules out anything with a random seed.

**Coverage is a hard gate, not a metric.** Not "we validate most rows". 51,651 rows × 12 rules, and the build
fails if that product doesn't match. A metric you can miss by 2% is a metric that slowly becomes 80%.

The measurement, against the seeded faults: recall 100%, row-level precision 99.6%. One rule's threshold (a
plausible-range ceiling) was **wrong in my implementation and the answer key caught it** — the data said the
real ceiling was higher than I'd assumed, so the rule changed, not the key.

*What's the quietest way your pipeline loses data? Most teams I've asked have to go and look.*

**Visual:** the exception queue with severity and owner columns.
**Evidence:** `D-018`, `D-021`.

---

## Post 6 · "Who decided this was fine?" needs to survive the next edit

Exceptions get resolved. Somebody looks at 340 rows with a malformed customer code and decides what happens.

The naive model: a `status` column. `open` → `resolved`. It answers "what is it now?" and it destroys the only
question an auditor will actually ask: **what happened, in what order, and who decided?**

So resolution here is append-only. Assigned, commented, resolved-with-a-kind, reopened — each one an event with
an actor and a timestamp. The current state is derived from the events, not stored instead of them.

Three things that fall out of it, none of which I designed for directly:

**Reversibility.** A resolution applied to the wrong batch can be reversed by appending, not by editing history
into a shape that no longer explains itself.

**Publishable kinds.** Not every resolution means "fixed". Some mean "the source genuinely does not carry this
field for this period" — which is a legitimate outcome, and a completely different thing from a correction. If
your only verb is "resolve", those collapse into each other and your data quality metrics start lying.

**Authority is enforced, not implied.** Resolving with a publishable kind is owner-only, checked server-side.
Not a hidden button — a rejected request.

This is unglamorous plumbing, and it's the part a regulated customer asks about in the first meeting. An FDE who
can't answer "show me who approved this and when" doesn't get to the second meeting.

*Does your system record decisions, or only their outcomes? They are not the same file.*

**Visual:** the event trail for one exception, from raised to resolved.
**Evidence:** `D-021`.

---

# Week 4 — Lineage, drift, and the Monday morning blank dashboard

## Post 7 · Your dashboard goes blank on Monday. Mine names the breakage on Friday.

Upstream systems change without telling you. The only real question is whether you learn it from your pipeline
or from your CEO.

Classic version: someone renames a column in the source. The job runs. The transform maps nothing to that field.
The dashboard renders zeros. Monday, three people spend a day finding out why.

This system fingerprints the *shape* of each source file — column names, order, types, coarse enough to be
stable and sharp enough to catch a rename. When the fingerprint changes, that's a **drift event**.

Three design decisions inside that:

**One alert per new shape, not per row.** A renamed column affects 40,000 rows. Forty thousand notifications
trains everybody to filter your alerts into a folder they never open. One alert, naming the shape, with the
count attached.

**Publishing is blocked until a mapping is confirmed for the new shape.** Not "best effort with nulls". The
pipeline stops and says what it needs — because a silently null column is the same failure as the silent drop,
wearing a different hat.

**The alert names what breaks.** Lineage is recorded when a mapping is confirmed, so the alert says which
canonical fields, which metrics and which consumer views are downstream of the column that moved.

Two seeded drift events. Both detected, both named, zero false alerts.

The lineage detail worth stealing: impact is **derived from the compiled metric definitions**, not maintained as
a list. Nobody updates a lineage document. So don't have one.

*What's your actual early warning for an upstream schema change? "The dashboard looks wrong" is an answer, just
not a good one.*

**Visual:** the drift alert naming impacted metrics and views.
**Evidence:** `D-020`, `D-022`, `D-023`.

---

## Post 8 · The publish step should refuse more than it accepts

There's a moment in every data platform where numbers stop being "data" and become "the truth" — the point
downstream consumers read. It's the cheapest place in the system to stop a bad number and the most expensive
place to be wrong.

So publishing here is the strictest gate in the build. Silver → gold, per batch, and it refuses:

→ **a stale batch** — where staleness is measured from the *extract* timestamp, not the ingest one. A file that
sat in a folder for three days is three days old, however recently you loaded it.
→ **a drifted batch** — the shape changed and no one has confirmed a mapping for the new shape.
→ **rows in an unresolved exception state** — held back individually, not failing the whole batch. The good rows
publish; the disputed ones wait with their owner.

And the whole operation is owner-only, server-side.

One more property that took a rewrite to get right: **idempotency**. Publishing the same batch three times
produces the same gold, not three times the rows. That sounds obvious until you're building it — I ended up
keying on the request rather than hashing content, because content-keyed batches make a legitimate re-send of
corrected data indistinguishable from a duplicate.

Replay a batch three times, row counts unchanged. That's the test, and it runs in CI.

Why an FDE cares more than a platform engineer might: you are the person who will be on a call when someone
says "the numbers changed and nobody told us". Everything above exists so that the answer is a timestamp and a
name, not a shrug.

*What does your pipeline do with a batch it isn't sure about — publish it, drop it, or stop and ask?*

**Visual:** a blocked publish with its three reasons.
**Evidence:** `D-019`, `D-023`.

---

# Week 5 — One definition, and a knowledge layer that knows what it may not say

## Post 9 · They didn't disagree about the data. They disagreed about the definition.

Two BI tools. One KPI. Different answers, every month, by a couple of points. Everyone assumed a data problem —
a join, a filter, a late-arriving fact.

It wasn't. Each tool had its own formula, written by a different person at a different time, living in its own
semantic layer. Neither was written down anywhere a human could read. They were both "correct" and they meant
different things by the same word.

The fix is not a better ETL. It's one definition:

**Metrics live in versioned YAML**, compiled into SQL views that every consumer reads. Change the definition
once, and every downstream view changes with it — and the change has a version number and an author.

**The expression language is whitelisted.** Not arbitrary SQL in a config file, which is remote code execution
in a bow tie. A constrained grammar: named fields, a fixed set of operators and aggregates, compiled and
validated. If a definition needs something the grammar can't express, that's a conversation, not a workaround.

**Reconciliation explains the historical gap by cause.** Not "they now agree" — for each tool, from that tool's
own export, which specific difference produced which part of the discrepancy. That's what makes the switch
survivable politically: the BI team can see their number wasn't nonsense, it was a different definition.

114 period-by-period comparisons after the switch. Zero delta, every one.

The FDE lesson under it: "which number is right?" is almost never the question. The question is **"where is this
defined, who owns it, and when did it last change?"** If you can't answer that in one place, you don't have a
data problem — you have an organisational one wearing a data costume.

*How many places in your organisation is your most important metric defined? The honest number is usually more
than one.*

**Visual:** the metric YAML next to both generated views and the reconciliation table.
**Evidence:** `D-011`, `D-024`.

---

## Post 10 · Filter permissions in the query, not in the answer

A retrieval layer over internal documents — SOPs, specs, change notices. Some of them are restricted.

The tempting implementation: retrieve the top-k, then drop the ones this user can't see, then answer.

That's wrong, and precisely wrong. By the time you filter, the restricted document has already been loaded into
memory, ranked, and placed one bug away from the answer. A prompt injection, a logging statement, a citation
list rendered before the filter runs — any of them leaks it.

So the access predicate lives **in the SQL query, before ranking**. The document the asker may not see is never a
candidate. Not hidden, not dropped later — never retrieved.

The rest of the retrieval design, briefly, because the details matter:

**Chunking at section level, tables kept atomic.** A table split across two chunks is a table that produces
confidently wrong answers. Every chunk carries its document's frontmatter, so a retrieved fragment still knows
which SOP version it belongs to.

**Hybrid retrieval**: BM25 for exact terms (part numbers, clause references — where embeddings are weak) fused
with vector search via reciprocal rank fusion. And a TF-IDF floor reported beside every mode, for the same
reason the mapper had a heuristic: know what your fancy thing beats.

**Refusal in a fixed order**: access gate → evidence floor → explicit instruction. Access first, always — "I
don't have anything on that" must never differ from "I'm not allowed to tell you", or the refusal itself leaks
the document's existence.

Measured: recall 74% → 87% with hybrid. Zero leaks. And in the configuration where the filter ran *after*
ranking: four.

*Where does your RAG system enforce permissions — in the query, or in the rendering?*

**Visual:** the two pipelines side by side, with the leak count under each.
**Evidence:** `D-026`, `D-027`, `D-028`.

---

# Week 6 — The agent that cannot act, and the change request that proved the design

## Post 11 · My favourite demo moment is a gate refusing a fix that's correct

The triage agent reads an exception: a customer code arrived as `CUST-0004` where the master says `C000004`. It
proposes normalising it.

**The proposal is right.** Any engineer would make that change.

The gate refuses it, in code, before a human is asked. The operating procedure permits automatic changes to two
things: units of measure and date formats. A customer identifier is neither. So the correct fix becomes a
proposal on somebody's queue, and a person approves it in four seconds.

That single interaction is the argument for the whole design: **correct and permitted are different questions,
and only one of them is the model's to answer.**

How it's enforced, because "we have a policy layer" means nothing without mechanism:

→ The allowed fix kinds and the protected fields are **in code**, not in the config the agent can read. A policy
loaded from a file the process can also write is a suggestion.
→ Loading a policy that *widens* the allowlist is refused at startup. You can narrow permissions from config;
you cannot broaden them.
→ Approval re-checks. An edited action, a direct API call, a resumed run — each re-enters the gate. Approving
means "do the thing policy permits", not "do anything".

Sixteen adversarial fixes, sixteen blocked.

And the honest part: the agent's end-to-end accuracy is **82% against an 85% target**. It missed. The report says
so, and says which pattern it fails on — the one whose expected label has no prior resolution behind it, which
is a training-data problem, not a prompting one.

*"Human in the loop" is on every architecture diagram. What in your system would actually stop the model if the
human clicked approve without reading?*

**Visual:** the blocked fix with the SOP clause beside it.
**Evidence:** `D-029`, `D-030`.

---

## Post 12 · The real test of a data model is the change request nobody planned for

Six weeks in, the business changes its mind. From June, lot number becomes mandatory on every delivery line for
traceability. Historic data doesn't have it. Nobody is going back to fill it in.

This is the moment that tells you whether your architecture was a diagram or a decision. In a hand-built
pipeline it's a week: a new column, a new validation, a backfill argument, three report queries, and a meeting
about what to do with history.

Here it was **one dictionary file and one rule**, in about seventeen minutes:

**The field dictionary is versioned**, so "lot_no is required" isn't a global truth — it's `required_from:
2026-06-01`, evaluated against each row's own issue date. History isn't wrong; it's *before the rule*.

**One new validation rule** reads that dictionary rather than hard-coding the field. Rows before the date are
untouched. Rows after it without a lot number become exceptions with an owner, at a severity the dictionary
specifies.

**Rows the source genuinely never carried** come back as "not covered for this period" warnings — not fixes, not
compliance escalations. That distinction is the difference between a data quality programme people trust and one
they route to a folder.

**Zero report SQL changed.** The consumer views compile from the metric definitions; nothing downstream knew a
field had been added.

The number I'd give a client: seventeen minutes, two files, no backfill, no downstream change. The number I'd
give an engineer: the reason it was seventeen minutes is that "required" was data from day one, not an `if`
statement.

*What would a "this field is now mandatory, from a date, retrospectively-ish" request cost you this afternoon?*

**Visual:** the diff — one YAML file, one rule.
**Evidence:** `D-025`.

---

# Week 7 — Detection: three shapes, and the measurement that rewrote my policy

## Post 13 · The lane collapse your executive dashboard cannot see

One customer, one plant. On-time-in-full drops from 93% to 48% and stays there for three weeks.

The company-wide OTIF number on the executive dashboard **never moves outside its own noise**. Not "barely
moves" — on those days, it doesn't even register as a finding, because the movement is below the level policy
calls material at that grain.

There's a test asserting exactly that, and it's the most important test in the second project. It encodes the
failure the whole thing exists to answer: **aggregate metrics hide the segment-level failures that customers
actually experience.**

Which is why detection runs at every grain an anomaly can live at — eleven series across ~173 segments: company
total, plant, plant × customer lane, plant × product group, production line, inventory location.

Three detectors, because three different shapes fail differently:

**A day-over-day baseline** (28-day rolling, de-seasonalised by weekday) catches the cliff. Without the weekday
adjustment you're detecting the calendar — comparing a Monday against a mean containing four Sundays.

**Policy thresholds** with consecutive-day rules catch the level: "this is below the line the business drew, and
has been for three days". Statistically unremarkable, operationally unacceptable.

**CUSUM change-point** catches the drift: half a standard deviation a day that never trips a z-score and hasn't
gone away in a fortnight. That's the shape of a slow weight shortfall or inventory quietly ageing.

Severity is the strongest verdict any of them gives — then capped by four gates, including one for thin volume,
because a rate computed from three order lines is arithmetic, not information.

*Which of your KPIs would survive being broken for one customer for a month without anybody noticing?*

**Visual:** two charts — the lane collapsing, the company total flat.
**Evidence:** `B-004`, `test_the_company_total_never_sees_the_lane_collapse_at_all`.

---

## Post 14 · My detector scored 2% precision. Best day of the project.

First full replay across 18 months of history, scored against seeded anomalies: **recall 100%, precision 2%.**
383 alerts raised, 377 with nothing behind them.

That run was worth more than every green test I'd written. Four causes — three of them mine, and only one
subtle:

**1. My thresholds were guesses.** I'd written `otif_rate: high: 0.90` in a policy file in week one because 90%
sounds like a bad day. The measured median of that series is 0.905. My "alert on this" line sat at the middle of
the distribution — it fired on half of all days. Every threshold is now set from the observed distribution, with
the percentile written next to it in the file.

**2. A line drawn for the company is not the line for one lane.** A single customer lane ships a handful of
order lines a day, so its daily rate is mostly 0 or 1 — median 1.00, tenth percentile 0.00. No absolute
threshold means anything there. A lane is now judged on its seven-day volume-weighted rate against its own
28-day norm: like compared with like.

**3. An incident is a policy breach, not a statistical shift.** Statistics can say "this moved". Only the
business can say "this matters". The statistical detectors now raise WARN on their own; only a line in the
policy file makes something HIGH. Before that separation, they produced **322 of 365** HIGH alerts.

**4. An actual bug.** My CUSUM never reset after signalling, so a single sustained shift re-reported itself
every day for a fortnight. Textbook procedure, skipped.

After: precision 80%, recall still 100%. And since I'd tuned on the first half of the history, the report scores
the held-out half separately — it comes out at 100%.

*Every alerting system I've seen shipped with thresholds someone guessed in a meeting. When was yours last
compared to what the data actually does?*

**Visual:** the before/after table with the four causes.
**Evidence:** `B-006`.

---

# Week 8 — Deleting a feature, and an evidence pack the model cannot escape

## Post 15 · I deleted a detector because the evidence said it couldn't work

Backlog build-up looks obviously detectable. Watch the queue of unshipped order lines, alert when it spikes.
I built it, it fired, it looked fine.

Then I measured it properly against the seeded surge.

**The genuine anomaly peaks at 2.81 standard deviations. Ordinary weekly swings reach 4.09, 4.39 and 5.04.**

There is no threshold that admits the real one without admitting all of the noise. Not a tuning problem — the
statistic cannot separate the classes. Backlog is autocorrelated and mechanically seasonal: nothing ships over a
shutdown, so the queue builds on its own, with no underlying problem at all.

So I removed the rule. Backlog is still computed, still watched, still shown at WARN — and nobody gets woken for
it. The policy file carries the reason with the numbers, and what would probably fix it: level against
throughput, or week-over-week at the same weekday. That's a week of analysis, not a guess, and it's written
down as such.

The alternative was obvious and available: tune it until the answer key looked good. That would have produced a
better-looking report and a detector that fails silently on data it hasn't seen.

There's a professional muscle here that I think separates senior from mid-level more reliably than any
technology: **being able to say "we measured it and it doesn't work, here's what I'd try next"** — to a client,
in writing, without it reading as failure. Shipping a feature you know can't discriminate is the actual failure.
It just defers the invoice.

*When did you last delete a feature because the evidence said to — and how did that conversation go?*

**Visual:** the two overlapping z-score distributions.
**Evidence:** `B-006`.

---

## Post 16 · The most important decision was what not to give the model

The second system writes a morning brief: what changed, why it matters, what to do. A language model writes the
prose.

It never sees the database.

It receives one JSON object — the **evidence pack** — containing the flagged items, their computed drivers, the
detector's own reason for each flag, data freshness, and a **flat map of every number it is allowed to use**.

Three properties, each load-bearing:

**Every quotable number has a stable reference.** `I1.value`, `I1.driver1.share`. Flat, not nested — a validator
resolving `items[0].drivers[0].share` through a tree is a validator with bugs in it.

**Rounding happens once, in code.** The pack carries `"74.1%"`, and the model is asked to copy that string. It
is never asked to turn `0.74138` into a percentage. A model doing that formatting will sometimes write 74%,
sometimes 74.14%, and occasionally 74.8% — and inside a fluent paragraph, you cannot tell which is which.

**Nothing else is in scope.** No tables, no history, no series to average. It cannot compute a trend because it
was never given one. The arithmetic already happened, in SQL, with tests.

This inverts the usual instinct. Most RAG-adjacent work asks "how much context can I give it?" The better
question for anything that produces numbers people act on is **"what is the minimum it needs, and what can I
make structurally impossible?"**

Hallucination mitigation is mostly prompt engineering. Some of it is just not handing over the raw material.

*What's in your prompt that the model doesn't actually need — and what could it do with that, on a bad day?*

**Visual:** the evidence pack beside the brief it produced, with refs highlighted.
**Evidence:** `B-007`.

---

# Week 9 — The validator that was wrong, and a pause that survives a restart

## Post 17 · My AI fact-checker failed 25% of briefs. It was mostly my fault.

Every claim in the generated brief must cite the metric it came from. That part is structural: the citation
field is required by the schema, so an uncited claim is a shape the model cannot return.

The validator's real job is harder — catching a claim that cites a **real** fact and then states a **different
number**. Wrong, cited, and completely convincing. That's the failure mode that survives a human skim.

First live run, twenty briefs through the model: citation coverage 100%, first-pass validity **35%**, fallback
rate **25%**.

I went looking for a model problem. Three of the four causes were mine:

**Typography.** The model writes plant names with a non-breaking hyphen — `PLT‑02`, U+2011. My validator
compared bytes, saw a stray "02" it couldn't match to any fact, and rejected perfectly good sentences. Three of
the four fallbacks were this. Text is now normalised before comparison, and the names it masks come from the
pack rather than a regex guessing at identifier shapes.

**Notation.** "OTIF fell by 9.4 points" was rejected because the fact is −9.4. That's a validator enforcing
notation rather than truth; the direction is carried by the verb and by the item's own direction field.

**Schema.** A nullable union type (`["string", "null"]`) that the provider's strict mode rejects outright —
costing a brief that fell back for reasons unrelated to its writing.

**Token budget.** A six-item brief ran past `max_tokens` and came back truncated.

After fixing my checker: **100% first-pass validity, 0% fallback**, same standard. The adversarial tests still
reject a wrong number under a right citation, an invented reference, and a talked-up severity.

The habit: **when your eval says the model is failing, check the eval first.** It's the cheaper hypothesis and
it's right more often than is comfortable.

*How much of your model's measured failure rate is actually your measurement?*

**Visual:** the before/after table, with the four causes.
**Evidence:** `B-008`.

---

## Post 18 · A pause that survives the process is the only reason there's a graph

Architecture question worth being disciplined about: when does a workflow justify a framework?

In the first project, the pipeline is fixed — profile, map, validate, publish. That's a function call. I used
plain code and there is no graph anywhere in it.

The second project has one property the first doesn't: **the brief is built at 06:00 and somebody approves an
action at 14:00, possibly from a different machine, after the original process is long gone.**

That's a checkpointed state machine. Writing it by hand means writing serialisation, resumption and a step
ledger — which is what LangGraph already is. So: `build_pack → narrate → draft_actions → policy_gate →
⟪interrupt⟫ → execute → record`, compiled with `interrupt_before=["execute"]` and a Postgres checkpointer.

The interrupt is the part I'd defend hardest. **It's a stop, not a flag.** The `execute` node is never asked
whether approval happened — the graph *cannot reach it* without a person resuming the run. There's no boolean
somebody can flip, no code path that skips the check, because the check isn't code.

The test that earns the dependency: start a run, capture the pause, then **throw away the compiled graph, the
checkpointer and the database connection**, and resume from a fresh set in a new context. If that didn't need to
work, plain code would do — and I'd have used it, like I did in the other repo.

Smallest abstraction that fits, in both directions. Reaching for a framework you don't need is a cost. Refusing
one you do need is also a cost, paid later, in someone else's on-call rotation.

*What's in your stack that you'd remove if you had to justify it by a property nothing simpler provides?*

**Visual:** the graph with the interrupt marked, next to the restart test.
**Evidence:** `B-010`.

---

# Week 10 — Production, and the report that includes what failed

## Post 19 · Deploying it is how I found the bug

Both systems are deployed — API, console, scheduled job, Postgres — and the deployment earned its keep in the
first ten minutes.

The ops page showed **model cost $0.0000** while the model usage table right next to it showed **$0.0029**.

The join keyed on the evidence pack's `audience` field, which stored the reader's *role* (`supply_chain_vp`),
while the run record stored their *user id* (`vp`). Two concepts wearing one field name. Every local test
passed, because every local test used the same wrong value on both sides.

Only production surfaced it, because only production had a dashboard where the two numbers appeared side by side
and disagreed.

Three other things the deployment taught, in the same session:

**`railway run` executes on your machine** with the deployment's variables — so it hit a private hostname it
could never reach. The fix was a one-off exec *inside* the container, plus an explicit `--database-url` flag on
the metrics CLI for the workstation case.

**A foreign key that modelled reality wrongly.** I'd pointed the mock ticket table at the actions table with a
FK. The mock failed, because the executor runs before the record is written. The fix wasn't ordering — it's that
**a real tracker has no referential integrity with your database.** It accepts a ticket whether or not you've
finished writing your row. My mock was failing in a way the live adapter never could, which meant my model of
the live adapter was wrong.

**Config precedence bites.** The repo's `.env` deliberately wins over the shell, so a subprocess ignored the
test URL I exported. The rule was right; my expectation wasn't.

Forward-deployed means owning the thing in the environment where it actually runs. You cannot find these on your
laptop.

*What did your last deployment teach you that your test suite couldn't?*

**Visual:** the ops page showing $0.0000 next to $0.0029.
**Evidence:** `B-014`, `B-011`.

---

## Post 20 · Ten weeks, two systems, and a report that includes what failed

Both projects are finished. Here's the closing accounting, including the parts that missed.

**Project A — the trusted foundation.** Validation coverage 51,651 rows × 12 rules as a hard gate. Exception
recall 100%, precision 99.6%. Drift 2 of 2, no false alerts. Two consumer views agreeing 114 of 114 periods.
Retrieval 87% recall, zero leaks. **And the triage agent at 82% against an 85% target — missed**, with the
report naming the pattern it fails on.

**Project B — the command center.** Detection recall 100%, precision 80%, attribution top-1 100%, median lead
time 1 day, the holiday decoy never raised as an incident. Citation coverage 100% across 652 numbers in twenty
real briefs, at $0.0019 each. Every adversarial action blocked.

Both reports state their own caveats. Precision counts every unexplained alert as wrong, and the generator
promises nothing about the other 529 days — so 80% is a floor, not an estimate. And because thresholds were
tuned on the first half of the history, the half never touched is scored separately.

Three habits I'd carry into any engagement:

**Ship the non-AI baseline in the same PR as the AI.** It is the only thing that tells you what the model is
worth, and the only thing that runs when the provider doesn't.

**Measure against a known answer.** Synthetic data with seeded faults isn't a shortcut around real data — it's
the only way "recall is 100%" is a sentence that means anything.

**Publish the misses.** A report with no failures in it isn't a report, it's a brochure — and every technical
reader knows it.

One thing I'd do differently: measure earlier. The 2% precision run should have happened in week one of that
build, not week two. Everything I did in between was written against thresholds I'd guessed.

Both repositories are public, with the full decision log — including the decisions I reversed.

*If you've read this far: which of the two systems would you have built first, and why?*

**Visual:** both report tables side by side, misses included.
**Evidence:** `evals/REPORT.md` in each repo.

---

## Publishing notes

**Cadence.** Two a week for ten weeks, in the order above — it's build order, which is also narrative order.
Tuesday and Thursday mornings tend to work; avoid Friday.

**If you need a strong start**, don't open with Post 1. Open with **14** (the 2% precision run) or **15** (the
deleted detector) and backfill the context. Failure posts with numbers travel furthest; the discovery post reads
better once people already believe you can build.

**The five strongest** — in order: 14, 15, 17, 11, 19. Each contains a measurement that contradicted an
assumption, which is the only thing that reliably distinguishes a practitioner from a summariser.

**The visuals do the work.** The blocked correct fix, the two overlapping distributions, the flat company-total
chart next to the collapsing lane. If a post needs three paragraphs to explain the picture, the picture is
wrong.

**Reply for the first two hours.** The series exists to start conversations, not to broadcast. The best
follow-up work comes from the comments where somebody disagrees.

**Every number must be checkable.** The repos, the decision records and the generated reports are public, and
the figures in these posts must match them exactly. If a post wants a number the reports don't contain, change
the post — never round one into existence.
