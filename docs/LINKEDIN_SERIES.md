# Field Notes — a twenty-post LinkedIn series

Two posts a week for ten weeks. Ready to paste.

---

## The series identity

**Name:** *Field Notes*
**Strapline:** Trust before intelligence — notes from building two production systems.

**The standing blurb** (include in every post, near the top, so a reader landing on post 14 knows what they've
walked into):

> 📌 *Field Notes — post {N} of 20. A series on building two production systems for a food manufacturer: one
> that makes the numbers trustworthy, one that acts on them. Two posts a week.*

**The standing disclaimer** (every post, at the end, italic, one line):

> *Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
> reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Why worded that way.** It says both true things at once: the thinking is from real engagements, the evidence
is from a build you can open and inspect. "Details hidden" alone would imply the numbers are a client's with the
labels stripped — and since both repositories are public and visibly synthetic, that claim would collapse the
first time somebody clicked. Saying it precisely is what makes the numbers usable in an interview.

**Craft rules for all twenty.** The first two lines carry the post — LinkedIn truncates around 200 characters,
and everything after "…see more" is read only by people you've already convinced. Short paragraphs, one idea
each. Two hashtags at most. Links go in the first comment, never the body. Every post ends on a question you
would genuinely like answered.

---

# Post 0 · The series announcement (pin this)

📌 *Field Notes — a new series. Two posts a week for ten weeks.*

I spent ten weeks building two production systems for a food manufacturer, and I'm going to write up every
decision that mattered — including the ones I got wrong and had to reverse.

Here's the setup.

A meat processor. Three factories, a few hundred people, running everything on an ERP. Every factory exports
data slightly differently — different column names, different date formats, one of them ships weights in
kilograms without saying so. Downstream, two business intelligence tools both report the same delivery KPI and
disagree by a couple of points, every month. Nobody can say which is right.

Someone senior reads about AI and asks the reasonable question: *can we get an agent to sort this out?*

The honest answer is "yes, but not yet" — and explaining *why not yet*, in a way that sounds like sequencing
rather than refusal, turned out to be the most important conversation of the engagement.

So I built two things.

**One** makes the numbers trustworthy: profiling, mapping with human confirmation, validation where nothing is
ever silently dropped, drift detection, and one definition of each metric compiled to every consumer.

**Two** acts on them: it writes a morning brief that says what changed, why it matters and what to do — where
code computes every number, a language model only writes the prose, and a validator rejects any sentence whose
number doesn't match its citation.

Over the next ten weeks I'll cover both, two posts at a time: the design decisions, the trade-offs, the
measurements, and the four separate occasions where measuring something proved me wrong.

Starting with the one people find most counterintuitive: **why I spent four days building a fake company before
writing a line of the real system.**

*What's a project where the thing you were asked for wasn't the thing that needed building?*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

---

# Week 1 — Discovery, and building the ruler first

## Post 1 · "We want an AI agent" is a symptom, not a specification

📌 *Field Notes — post 1 of 20. Building two production systems for a food manufacturer: one that makes the
numbers trustworthy, one that acts on them. Two posts a week.*

A manufacturer asked me for an AI agent. I spent the first four weeks not building one — and that sequencing
was the most valuable thing I contributed.

Here's what the discovery conversation surfaced.

Three factories, each exporting the same operational data out of the same ERP, each in a slightly different
shape. One calls the field `DELIVERY_DT`. One calls it `del_date`. One calls it `Delivery Date`. One factory
ships weights in kilograms while the others use pounds, and nothing in the file says so.

Downstream, two BI tools both report on-time-in-full delivery and disagree by a couple of points, every month.
The reporting team has learned to quote whichever one the audience prefers.

Now imagine putting a capable language model on top of that.

It reads the same ambiguous data, produces fluent and confident answers, and is wrong in precisely the ways the
underlying data is wrong — except now the wrongness arrives as prose rather than a spreadsheet cell, which makes
it *harder* to catch, not easier.

You haven't settled the argument between those two BI tools. You've automated it, and given it a more
persuasive voice.

So the question I bring to that first meeting isn't "what shall we build". It's: **"walk me through the worst
Tuesday of your month."**

That question does the whole job. The answers were concrete: the kilogram file nobody flagged, the four date
formats in one column, the customer code that arrives with a hyphen, the Monday morning when a dashboard renders
zeros and three people lose a day finding out why.

None of those needs intelligence. They need **trust** — knowing what a number means, where it came from, and
what got thrown away on the way to producing it.

The agent still gets built. It arrives in week five, it's genuinely useful, and it's useful *because* it waited.

The reframe isn't a rejection of the ask. It's the sequencing the ask actually requires: trust first, autonomy
second. Every engagement I've seen go badly skipped that order — and usually because saying so felt like saying
no.

*What's the most useful reframe you've made of a request that arrived as a solution rather than a problem?*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the two-system diagram — trust foundation feeding the command center.

---

## Post 2 · I built the ruler before I built the thing

📌 *Field Notes — post 2 of 20. Two production systems for a food manufacturer. Two posts a week.*

Four days of work. Zero user-visible features. It's the reason every number in this series is checkable.

Here's the problem it solves, stated precisely.

You build a data quality system, point it at production, and it reports: **"4,312 issues found."**

Good? Bad? To know, you'd need to know how many issues were actually in there. Nobody does — that's the entire
reason the system exists. So the number cannot be checked, which means every claim you build on top of it is
decoration.

You will present that number in a steering meeting. Someone will ask if it's improving. You will genuinely not
know.

So before writing the real system, I built a generator: a fictional manufacturer with fifty thousand order
lines, three factories, eighty products, realistic seasonality and weekday patterns.

And then I broke it on purpose, in places I wrote down.

A unit mismatch on one factory's weights. Date formats that drift. Exactly 340 malformed customer codes. A field
one factory stops sending halfway through the year.

Now "the system found 340 of them" is a sentence with a meaning. **Recall 100%** stops being a gesture and
becomes a claim someone can falsify.

Two details mattered far more than I expected.

**The world has to be byte-identical every run.** My first version had non-deterministic ordering in a
dictionary, which quietly changed the answer key between runs. So yesterday's 96% and today's 94% were measured
against *different worlds*. The system hadn't got worse — the ruler had moved. That's a bug that makes an entire
evaluation suite meaningless while every individual test still passes.

**The generator refuses to lie.** After building the world it re-measures each planted fault against a baseline
window, and if any of them isn't actually visible in the resulting numbers, it fails the build and writes
nothing. A ground truth that quietly lies invalidates every measurement downstream, forever, and you'd never
find out.

The transferable version of this: **before you build the detector, build the thing that can prove it wrong.**
It's four days you won't want to spend, and it's the difference between an evaluation and an opinion.

*Have you ever shipped a metric you couldn't have proven wrong? How did you eventually find out?*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the planted-fault table beside the generator's self-check output.

---

# Week 2 — Mapping, and the honest baseline

## Post 3 · I wrote the dumb version first, on purpose

📌 *Field Notes — post 3 of 20. Two posts a week.*

The task: map each factory's columns onto one canonical schema. `DELIVERY_DT`, `del_date`, `Delivery Date` —
one field, three spellings. Roughly forty fields, three sources.

This is a textbook job for a language model. They're genuinely good at it.

**I built the stupid version first anyway.** A few hundred lines: normalised string similarity, a synonym table
for the obvious domain terms, a type-compatibility check using the profiler's findings, and a confidence
threshold. No AI anywhere.

Then I built the LLM version. And I scored both against the same answer key — on two separate sets.

Headers the system had seen before. And **headers neither had ever encountered.**

That second set is where the real question lives. Evaluate a mapper only on familiar headers and you're testing
its memory. What matters operationally is what happens next quarter, when factory four arrives with its own
spelling of everything.

Three things the baseline bought, all of them practical:

→ **"The model scores 100%" became a sentence with meaning.** Without a floor, 100% of what? Compared to what?
With one, the model's contribution is a measured lift rather than a feeling.

→ **The system degrades instead of stopping.** When the AI provider has an incident — and they do — mapping
still runs. Worse, but running. A feature that dies when a vendor sneezes isn't a feature, it's a demo.

→ **"Is the model worth paying for here?" became arithmetic.** Not a debate settled by whoever is most senior in
the room.

There's a fourth benefit I didn't anticipate. Building the heuristic *forced me to understand the problem* —
which synonyms mattered, where types disambiguated, which headers were genuinely ambiguous even to a human. By
the time I wrote the prompt, I knew what I was asking for. The prompt was better because the baseline existed.

If I had to compress this into one rule for applied AI work: **ship the non-AI baseline in the same pull request
as the AI.** If it isn't worth the afternoon it costs, you've decided the model's value is a matter of faith.

*If your LLM feature disappeared tomorrow, what's the floor it falls back to — and have you ever measured it?*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the two-column scorecard, known headers vs unseen.

---

## Post 4 · The model proposes. It never has authority.

📌 *Field Notes — post 4 of 20. Two posts a week.*

A wrong column mapping is the most expensive silent failure in integration work. Let me be specific about why.

A column called `net_weight_kg` gets mapped to the canonical field the metric layer reads as pounds.

Nothing errors. No test fails. The pipeline is green. The dashboard renders beautifully.

And every weight-based number in that company is wrong by a factor of 2.2 until somebody happens to notice.
In my experience that takes about a quarter, and the discovery usually happens in front of a customer.

So the mapping flow in this build has a shape that has very little to do with AI:

**The model proposes.** One call with a strict output schema, so the response is machine-checkable rather than
prose to be parsed hopefully. If it doesn't satisfy the contract, retry exactly once. If it fails again, fall
back to the heuristic. The model never grades its own answer — that decision belongs to the caller, and the
caller is code.

**A person confirms.** An owner sees the proposal with the profiler's evidence beside it: what's actually in
that column, sample values, how often it's empty, whether the types are compatible. They accept or correct.
Nothing downstream moves until they do.

**The confirmation is versioned and bound to an exact shape.** Not "factory two maps like this" but "*this exact
arrangement of column headers* maps like this, confirmed by this person, on this date." When the source changes
shape, the binding stops matching and the pipeline says so instead of guessing.

The obvious objection — and I've had it in a review — is *why not auto-apply when the model is confident?*

Because **confidence is the model's opinion of itself.** It is not a calibrated probability of correctness.
Treating it as one is exactly how you get the silent 2.2× error above, except now with a number next to it that
made everyone feel better.

The human step costs about four seconds per header, once, per source shape. Skipping it costs a number nobody
can trust and a quarter of retrospective correction.

The framing I'd offer: a mapping isn't a prediction. **It's a business decision with an audit trail.** Those two
facts don't conflict — they're precisely what makes the AI safe to use here.

*Where in your system does a model's output become an action without a person in between — and is that on
purpose, or is it just how it ended up?*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the confirmation screen — proposal and profiler evidence side by side.

---

# Week 3 — Validation, and the enemy of the silent drop

## Post 5 · The worst line of code in a data pipeline

📌 *Field Notes — post 5 of 20. Two posts a week.*

It's this one:

```sql
WHERE valid = true
```

It doesn't throw. It doesn't log. The job finishes green. The dashboard renders. And four thousand rows are
simply gone.

You find out three weeks later, when a customer asks where their order went — and somebody spends a day proving
the pipeline "worked correctly". Which it did. That's the problem.

I've seen versions of that line in every data platform I've worked on, and it's almost never malicious. It's
someone under deadline pressure making a reasonable local decision: *these rows are broken, they'd corrupt the
report, filter them out.* The information that they existed dies right there.

So this build has a rule with no exceptions: **nothing is ever silently dropped.** Every row ends in exactly one
of three states — published, held, or explained.

Mechanically: twelve validation rules run over every row. Does this delivery date fall in a plausible window?
Does the weight per case make sense for this product? Does this customer code exist in the master data?

A row that fails doesn't vanish. It becomes an **exception**: a queue item with an owner, a severity, a reason
in plain language, and a key pointing back at the exact source row.

Not a log line nobody reads. A thing on somebody's list.

Two decisions inside that are worth stealing:

**Deterministic, not model-judged.** A validation rule must give the same verdict on the same row twice and be
explainable to an auditor in one sentence. That rules out an LLM. These checks are boring by design, because
"boring and repeatable" *is* the requirement.

**Coverage is a hard gate, not a metric.** Not "we validate most rows" — every row, every rule, and the build
*fails* if the arithmetic doesn't match. A metric you're allowed to miss by 2% becomes 80% over eighteen months
and nobody can point at the day it happened. A gate you cannot miss stays at 100% or stops the line.

Measured against the planted faults: **recall 100%, row-level precision 99.6%.**

And a confession that's the whole reason for having an answer key: one of my rules was wrong. I'd set a
plausible-range ceiling from an assumption about case weights. The answer key disagreed. I checked — the data
was right and I wasn't. **The rule changed, not the key.**

*What's the quietest way your pipeline loses data? Most teams I ask have to go and look.*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the exception queue, with owner and severity columns.

---

## Post 6 · "Who decided this was fine?" has to survive the next edit

📌 *Field Notes — post 6 of 20. Two posts a week.*

Exceptions get resolved. Somebody looks at 340 rows with a malformed customer code and decides what happens.

The naive design is a status column. `open` → `resolved`. Clean, obvious, and it destroys the only question an
auditor actually asks: **what happened, in what order, and who decided?**

A status field answers "where are we now". It cannot answer "how did we get here", because each update erases
the previous answer.

So resolution here is **append-only**. Assigned, commented, resolved-with-a-kind, reopened — each one an event
with an actor and a timestamp. The current state is *derived* from the events rather than stored instead of
them.

Three things fell out of that, two of which I didn't design for:

**Reversibility comes free.** A resolution applied to the wrong batch is undone by appending another event, not
by editing history into a shape that no longer explains itself.

**Resolutions turn out to have kinds, and they aren't all "fixed".** Some mean *the source genuinely does not
carry this field for this period.* That is a legitimate, permanent, correct outcome — and it is a completely
different thing from a correction.

If your only verb is "resolve", those two collapse into one. Your data-quality metrics then improve in a way
that looks like progress and is actually just a vocabulary problem. I've seen a team celebrate closing 4,000
exceptions that were all "this field never existed here".

**Authority is enforced server-side, not implied by the interface.** Resolving with a publishable kind is
owner-only — and not by hiding a button, which is security theatre. By rejecting the request.

This is unglamorous plumbing. It's also the first thing a regulated customer asks about, usually in the first
meeting, and an engineer who can't answer "show me who approved this and when" doesn't get to the second one.

*Does your system record decisions, or only their outcomes? They are not the same table — and only one of them
survives an audit.*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the event trail for a single exception, raised through resolved.

---

# Week 4 — Drift, lineage, and the Monday blank dashboard

## Post 7 · Your dashboard goes blank on Monday. Mine names the breakage on Friday.

📌 *Field Notes — post 7 of 20. Two posts a week.*

Upstream systems change without telling you. The only real question is whether you find out from your pipeline
or from your CEO.

The classic sequence, and I suspect you've lived it: someone renames a column in the source system on Friday
afternoon. The job runs over the weekend. The transform finds nothing to map, writes nulls, and reports success.
Monday morning the dashboard renders zeros and three people spend the day on archaeology.

Nothing was broken, technically. Every component did exactly what it was told.

So this build fingerprints the **shape** of each source file — column names, order, types. Coarse enough to be
stable across ordinary variation; sharp enough to catch a rename. When the fingerprint changes, that's a drift
event.

Three decisions inside that, each the difference between an alert people act on and an alert people filter:

**One alert per new shape, not per row.** A renamed column affects forty thousand rows. Forty thousand
notifications teaches everybody to route your alerts to a folder they never open — and then the one that
mattered arrives there too.

**Publishing is blocked until somebody confirms a mapping for the new shape.** Not "best effort with nulls",
which is the silent drop from post 5 wearing a different hat. The pipeline stops and states precisely what it
needs.

**The alert names what breaks.** When a mapping is confirmed, the path from source column → canonical field →
metric → view is recorded. So the alert doesn't say "a column changed" — it says which metrics and which
dashboards were about to go quietly wrong.

Two planted drift events. Both detected, both named, **zero false alerts.**

One detail worth stealing outright: the impact list is **derived from the compiled metric definitions**, never
maintained by hand. A hand-written lineage document is accurate the day it's written and lying within a month,
because nobody updates them. So don't have one — generate it from the thing that's already true.

*What's your actual early warning for an upstream schema change? "The dashboard looks wrong" is an answer. It's
just not a good one.*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the drift alert naming the impacted metrics and views.

---

## Post 8 · The publish step should refuse more than it accepts

📌 *Field Notes — post 8 of 20. Two posts a week.*

There's a moment in every data platform where numbers stop being "some data" and become "the truth" — the point
downstream consumers read.

It is the cheapest place in the system to stop a bad number, and the most expensive place to be wrong.

So publishing here is the strictest gate in the build. Per batch, it refuses:

**Stale batches** — where staleness is measured from the *extract* timestamp, not the load one. A file that sat
in a folder for three days is three days old, however recently you got round to loading it. That distinction
sounds pedantic until you notice that measuring from load time makes every delayed file look perfectly fresh,
which is the opposite of what you want from a freshness check.

**Drifted batches** — the shape changed and nobody has confirmed a mapping.

**Rows in an unresolved exception state** — held back *individually*, not failing the whole batch. The clean rows
publish; the disputed ones wait with their owner.

That last one is a workflow decision as much as a technical one. All-or-nothing publishing is how you end up
with people resolving exceptions carelessly at 5pm to unblock a release — you've made rigour the thing standing
between a colleague and going home.

And the operation is owner-only, checked on the server.

One more property that took a rewrite to get right: **idempotency** — doing it twice has the same effect as
doing it once. Publish the same batch three times, get the same result, not three copies.

My first attempt keyed on a hash of the content, which felt elegant. Then I realised it makes a legitimate
re-send of *corrected* data indistinguishable from an accidental duplicate — the system would reject exactly the
case you most need to work. Keying on the request identity fixed it.

Replay three times, row counts unchanged. That's a test, and it runs on every commit.

Why I care about this more than a platform engineer might: I'm the person who'll be on the call when somebody
says "the numbers changed and nobody told us". Everything above exists so that the answer is a timestamp and a
name, rather than a shrug and a week of investigation.

*What does your pipeline do with a batch it isn't sure about — publish it, drop it, or stop and ask?*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** a blocked publish showing its three reasons.

---

# Week 5 — One definition, and a knowledge layer with boundaries

## Post 9 · They weren't disagreeing about the data. They were disagreeing about the definition.

📌 *Field Notes — post 9 of 20. Two posts a week.*

Two BI tools. One KPI. Different answers every month, by a couple of points.

Everyone assumed a data problem — a join, a filter, late-arriving records. Three people had looked at it over
two years. It wasn't a data problem.

Each tool had its own formula, written by a different person at a different time, living inside that tool's own
semantic layer. Neither was written down anywhere a human could read.

One counted a partial delivery as a miss. The other counted it proportionally.

**Both were correct. They meant different things by the same word.** Nobody had ever put the two definitions
side by side, because there was nowhere to put them.

The fix isn't better plumbing. It's one definition:

**Metrics live in versioned files** — human-readable, reviewed like code — and are compiled into the SQL views
every consumer reads. Change the definition once and every downstream view changes with it, with a version
number and an author attached.

**The expression language is deliberately restricted.** Not arbitrary SQL pasted into a config file, which is
remote code execution wearing a bow tie. A constrained grammar: named fields, a fixed set of operators and
aggregations, compiled and validated.

When a metric needs something the grammar can't express, that's a conversation rather than a workaround — which
is the correct outcome, because somebody is about to encode a business rule and should have to say so out loud.

**And reconciliation explains the historical gap by cause.** Not just "they agree now". For each tool, from that
tool's own export, which specific difference produced which part of the discrepancy.

That last part is political as much as technical, and I'd argue it's the reason the migration succeeded. It
lets the reporting team see that their number wasn't nonsense — it was a different definition, honestly held,
by someone who no longer works there. Nobody had to be wrong in a meeting.

Migrations fail on that human point far more often than on the engineering. If your rollout plan doesn't have a
section about how the incumbent team's work gets respected, you don't have a rollout plan.

After the switch: **114 period-by-period comparisons. Zero difference. Every one.**

*How many places in your organisation is your most important metric defined? The honest count is usually more
than one.*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the metric definition file beside both generated views and the reconciliation table.

---

## Post 10 · Filter permissions in the query, not in the answer

📌 *Field Notes — post 10 of 20. Two posts a week.*

A retrieval layer over internal documents — procedures, specifications, change notices. People ask it questions
all day. Some of those documents are restricted.

That single fact changes the design in a way most implementations get wrong, including several I've reviewed.

**The tempting version:** retrieve the twenty most relevant documents, drop the ones this user isn't allowed to
see, then answer from what's left.

That is wrong, and precisely so. By the time you filter, the restricted document has already been fetched,
loaded into memory, ranked, and placed one bug away from the output.

A logging statement. A citation list rendered before the filter runs. A prompt injection in the user's own
question. Any of them leaks it — and the leak looks exactly like normal operation.

So the permission check lives **inside the database query, before anything is ranked.** The document this person
may not see is never a candidate. Not hidden. Not filtered later. Never retrieved.

I measured both configurations, because an assertion like that deserves a number. With the filter after
ranking: **four leaks.** With it in the query: **zero.**

Three other details that decide whether this works at all:

**Chunk at section level, keep tables whole.** Split a table across two chunks and you get confidently wrong
answers about it, because half a table looks exactly like a whole one. Each chunk also carries its document's
metadata, so a retrieved fragment still knows which version of which procedure it belongs to.

**Hybrid retrieval.** Keyword search is excellent at exact tokens — part numbers, clause references — where
embeddings are weak. Vector search understands that "short shipment" and "partial delivery" are the same idea.
Fusing both beat either alone: recall went **74% → 87%**. And as in post 3, a simple statistical baseline is
reported beside every configuration, because you should always know what your sophisticated thing beats.

**Refusal happens in a fixed order**: access first, then evidence strength, then scope. Access *must* be first —
"I don't have anything on that" has to be indistinguishable from "you're not allowed to know", or the refusal
itself leaks the document's existence.

*Where does your retrieval system enforce permissions — in the query, or in the rendering? It's worth checking
rather than assuming.*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the two pipelines side by side with the leak count under each.

---

# Week 6 — The agent, and the change request that tested the design

## Post 11 · My favourite demo moment is a gate refusing a fix that's correct

📌 *Field Notes — post 11 of 20. Two posts a week.*

Week five, the AI agent the customer asked for in week one finally gets built. Its job is narrow: read an
unresolved data exception, gather evidence with read-only tools, propose one of four outcomes.

Here's the moment I show people.

The agent examines a customer code that arrived as `CUST-0004`, where the master data says `C000004`. It
proposes normalising it.

**The proposal is correct.** Any engineer looking at those two strings would make that change.

The policy gate refuses it — in code, before a human is even asked. The company's operating procedure permits
automatic changes to exactly two things: units of measure and date formats. A customer identifier is neither.

So the correct fix becomes a proposal on somebody's queue, and a person approves it in four seconds.

That single interaction is the argument for the entire architecture: **correct and permitted are different
questions, and only one of them is the model's to answer.**

Now, "we have a policy layer" means nothing without mechanism, so here's the mechanism:

→ The permitted fix kinds and the protected fields live **in code**, not in configuration the running process
can read and rewrite. A policy loaded from a file the system can also modify is a suggestion.

→ A configuration that **widens** the allowlist is refused at startup. You may narrow permissions from config;
you may not broaden them. That asymmetry means a misconfiguration can only ever make the system more cautious.

→ **Approval re-checks everything.** An edited action, a direct API call, a resumed workflow — each re-enters the
gate. Approving means "do the thing policy permits", not "do anything".

Sixteen adversarial fixes attempted. Sixteen blocked.

And the number that missed: the agent's end-to-end accuracy is **82% against an 85% target.** The report says
so, in the same table as the successes, and names the pattern it fails on — exceptions whose expected resolution
has no precedent in the history, which is a data problem rather than a prompting one.

I could have moved the target to 80% and reported a pass. That is the most common quiet dishonesty in this
field, and it's why so few people believe evaluation tables.

*"Human in the loop" is on every architecture diagram. What in your system would actually stop the model if the
human clicked approve without reading?*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the blocked fix with the procedure clause beside it.

---

## Post 12 · The real test of a data model is the change request nobody planned for

📌 *Field Notes — post 12 of 20. Two posts a week.*

Six weeks in, the business changes its mind. As businesses do, and should.

*From June, lot number becomes mandatory on every delivery line for traceability. Historic data doesn't have it.
Nobody is backfilling three years of records.*

This is the moment that reveals whether your architecture was a decision or a diagram.

In a typical hand-built pipeline it's a week: a new column, a new validation, an argument about the backfill,
three report queries to update, and a meeting about what to do with history that ends without a conclusion.

Here it was **one dictionary file and one rule. About seventeen minutes.**

**The field dictionary is versioned.** So "lot number is required" isn't a global truth — it's
`required_from: 2026-06-01`, evaluated against each row's own issue date. History isn't wrong. It's *before the
rule*. That single reframing removes the backfill argument entirely.

**One new validation rule** reads that dictionary rather than hard-coding a field name. Rows before the date are
untouched. Rows after it without a lot number become exceptions, with the owner and severity the dictionary
specifies.

**Rows from a source that never carried the field** come back as "not covered for this period" — that third
resolution kind from post 6, doing real work. Not a fix. Not a compliance escalation. The truth.

**Zero report queries changed**, because consumer views compile from the metric definitions and nothing
downstream knew a field had been added.

The number I'd give a client is seventeen minutes. The number I'd give an engineer is *why*: because "required"
was **data** from day one, rather than an `if` statement buried in a transform.

That's what makes a design worth its cost. Not that it handles today's requirement elegantly — anything handles
today's requirement. That it absorbs the one nobody wrote down.

*What would "this field is now mandatory, from a date, retrospectively-ish" cost you this afternoon? It's a good
five-minute thought experiment on your own codebase.*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the diff — one YAML file, one rule.

---

# Week 7 — Detection, and the measurement that rewrote my policy

## Post 13 · The failure your executive dashboard cannot see

📌 *Field Notes — post 13 of 20. The second system begins. Two posts a week.*

One customer, one factory. Their on-time-in-full rate drops from 93% to 48% and stays there for three weeks.

A supermarket depot is being let down, every single day, for the better part of a month.

**And the company-wide delivery number — the one on the executive dashboard, the one in the board pack — never
moves outside its own noise.** On those days it doesn't even register as an unusual value.

That's not a flaw in the dashboard. It's what averages do. A catastrophic failure in a small segment disappears
into a large denominator, and no amount of dashboard design fixes it.

There's a test in this repository whose entire job is to assert that, because it's the premise of the second
system: **aggregate metrics hide the segment-level failures your customers actually experience.**

Which is why detection runs at every grain an anomaly can live at — eleven series across about 173 segments:
company, factory, factory × customer lane, factory × product group, production line, inventory location.

And three detectors, because "something is wrong" has three genuinely different shapes:

**The cliff.** A day-over-day comparison against a rolling 28-day baseline, in standard deviations — how
unusual is today *for this particular segment*?

**The line.** The business's own thresholds, requiring several consecutive days. Statistically unremarkable,
operationally unacceptable: "this is below what we promised, and has been since Tuesday."

**The drift.** A change-point technique that accumulates small deviations and fires when the total is too large
to be noise. Half a standard deviation a day never trips a z-score and is still a serious problem by Friday
fortnight.

Two corrections separate a detector from an alarm. **Day-of-week**: compare a Monday against an average
containing four Sundays and you've built a very sensitive detector of *the calendar*. **Holidays**: excluded
from the baseline, or a shutdown week drags the average down and makes the system *less* sensitive right after
the disruption — exactly backwards.

*Which of your KPIs would survive being broken for one customer for a month without anybody noticing? It's an
uncomfortable question and worth thirty seconds.*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** two charts — the lane collapsing, the company total flat.

---

## Post 14 · My detector scored 2% precision. Best day of the project.

📌 *Field Notes — post 14 of 20. Two posts a week.*

First full replay across eighteen months of history, scored against the planted anomalies:

**Recall 100%. Precision 2%.**

383 alerts raised. 377 with nothing behind them.

That run was worth more than every passing test I'd written to that point. Four causes — and only one was
subtle.

**1. My thresholds were guesses, and the data said so.**

In week one I'd written `otif_rate: high: 0.90` into the policy file, because 90% sounds like a bad day.

The measured median of that series is **0.905.**

My "this is serious" line sat at the middle of the distribution. It fired on half of all days. I had never
looked at the distribution before choosing the number — I'd used intuition about a business I'd known for a
fortnight.

Every threshold now comes from the observed distribution, roughly the worst 5% for a warning and the worst 2%
for an incident, with the percentile written beside the value so the next person knows where it came from.

**2. A line drawn for a company is not the line for one lane.**

A single customer lane ships a handful of order lines a day, so its daily rate is mostly 0 or 1 — median 1.00,
tenth percentile 0.00. No absolute threshold means anything at that grain. A lane is now judged on its seven-day
volume-weighted rate against its own 28-day norm. Like compared with like, which is also how a human judges it.

**3. Statistics can say it moved. Only policy can say it matters.**

This is the one I'd carry to any project. I had the statistical detectors and the business thresholds feeding
the same severity, so anything unusual became an incident. Now the statistical detectors raise a *warning* on
their own — something moved, somebody might look — and only crossing a line in the policy file makes it an
*incident*, the thing that interrupts a person's morning.

Before that separation, the statistical detectors produced **322 of 365** incidents.

**4. An actual bug.** My change-point detector never reset after firing, so one sustained shift re-reported
itself every day for a fortnight. Textbook procedure, skipped.

After: recall 100%, **precision 80%**, median lead time one day. And because I'd tuned on the first half of the
history, the report scores the second half — never used for tuning — separately. It comes out at 100%.

Without that split, "I tuned it until it looked good" and "it works" are indistinguishable.

*Every alerting system I've met shipped with thresholds someone guessed in a meeting. When was yours last
compared against what the data actually does?*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the before/after table with the four causes.

---

# Week 8 — Deleting a feature, and the pack the model can't escape

## Post 15 · I deleted a detector because the evidence said it couldn't work

📌 *Field Notes — post 15 of 20. Two posts a week.*

Backlog build-up looks obviously detectable. Watch the queue of orders not yet shipped, alert when it climbs.

I built it. It fired. It looked fine on the dashboard.

Then I measured it properly against the planted order surge.

**The genuine anomaly peaks at 2.81 standard deviations. Ordinary weekly swings reach 4.09, 4.39 and 5.04.**

Read that again, because it's the whole post. **The real problem is less statistically unusual than the routine
noise.**

There is no threshold that admits the real one without admitting all the false ones. This isn't a tuning
problem — the statistic cannot separate the classes, and no amount of cleverness with the same statistic will
change that.

The reason is structural. A queue is autocorrelated — today's backlog is mostly yesterday's — and mechanically
seasonal: nothing ships over a shutdown, so the queue builds with no underlying problem at all.

So I removed the rule.

Backlog is still computed, still watched, still shown as a warning. Nobody gets woken for it. The policy file
carries the reason with the numbers, and what would probably work instead: backlog measured against throughput,
or compared week-over-week at the same weekday. That's a week of analysis, and it's written down as such rather
than guessed at.

The alternative was obvious and available: tune it until the answer key looked good. That produces a better
report and a detector that fails silently on data it hasn't seen — which is worse than no detector, because
people will trust it.

There's a professional muscle here that separates senior from mid-level more reliably than any technology:
**being able to say "we measured it, it doesn't work, here's what I'd try next"** — to a client, in writing,
without it reading as failure.

It reads as failure only if you've been selling certainty. If you've been selling *measurement*, it reads as the
system working exactly as intended.

Shipping something you know can't discriminate isn't the safe option. It just moves the invoice to a quarter
when you're not in the room.

*When did you last delete a feature because the evidence said to — and how did that conversation actually go?*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the two overlapping z-score distributions.

---

## Post 16 · The most important decision was what *not* to give the model

📌 *Field Notes — post 16 of 20. Two posts a week.*

The second system writes a morning brief: what changed, why it matters, what to do about it. A language model
writes the prose.

**It never sees the database.**

It receives exactly one thing — an *evidence pack*: a structured object containing the flagged items, the
computed drivers, the detector's own reason for each flag, how old the data is, and a flat list of every number
it is permitted to use, each with a name.

Three properties, all load-bearing:

**Every quotable number has a stable reference.** `I1.value`, `I1.driver1.share`. Flat rather than nested, so
the validator that comes later resolves any reference with a dictionary lookup instead of walking a tree —
because a validator with a tree-walker in it is a validator with bugs in it.

**Rounding happens once, in code.** The pack contains the string `"74.1%"`, and the model is asked to copy that
string. It is never asked to turn `0.74138` into a percentage.

That second part is subtler than it looks. A model doing that formatting will sometimes write 74%, sometimes
74.14%, and occasionally 74.8%. Inside a fluent paragraph, a human reader cannot tell which of those is the
careful one and which is the slip.

**Nothing else is in scope.** No tables. No history. No series. It *cannot* compute a trend because it was never
given one. All the arithmetic already happened, in SQL, with tests.

This inverts the usual instinct. Most work in this space asks "how much context can I give it?" For anything
producing numbers people act on, the better question is: **"what is the minimum it needs, and what can I make
structurally impossible?"**

The same idea covers permissions. A factory manager should see their own numbers, not the company's. The wrong
way is to build the full brief and hide the parts they shouldn't see — the data was still in the prompt, still
in the model's context, still in the logs, one bug from the reader.

Here the audience is a *parameter of building the pack*. Another factory's numbers are never in it. A test
serialises one manager's entire prompt and searches it for the other factory's identifiers: zero occurrences.

Hallucination mitigation is mostly prompt engineering. Some of it is just not handing over the raw material.

*What's in your prompt that the model doesn't actually need — and what could it do with that on a bad day?*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the evidence pack beside the brief it produced, references highlighted.

---

# Week 9 — The fact-checker, and a pause that survives a restart

## Post 17 · My AI fact-checker failed a quarter of the briefs. It was mostly my fault.

📌 *Field Notes — post 17 of 20. Two posts a week.*

Every claim in the generated brief must cite the metric it came from. That part is structural: the citation
field is *required* by the output schema, so a claim without one is a shape the model cannot return.

"It forgot to cite" isn't a failure mode I have to defend against. It's impossible.

Which frees the validator to chase the failure that actually matters: **a claim that cites a real fact and then
states a different number.** Wrong, cited, and completely convincing — the one that survives a human skim,
because the citation makes it look checked.

First live run, twenty briefs: citation coverage 100%. First-pass validity **35%**. Fallback rate **25%**.

One brief in four was too broken to fix and shipped as a plain templated version instead.

I went looking for a model problem. **Three of the four causes were mine.**

**Typography.** The model writes factory names with a *non-breaking hyphen* — U+2011, visually identical to the
ordinary one. My validator compared bytes, saw a stray "02" it couldn't match to any fact, and rejected
perfectly good sentences. Three of the four fallbacks were this, and I lost most of a morning to it.

**Notation.** "OTIF fell by 9.4 points" was rejected because the stored fact is −9.4. That's a validator
enforcing *notation* rather than truth. The direction is carried by the verb, and a human would never
misunderstand it.

**Schema.** I'd used a nullable type in the strict output schema, which this provider rejects outright —
costing a brief that fell back for reasons having nothing to do with its writing.

**Token budget.** A six-item brief ran past the output limit and came back truncated.

Same twenty days after fixing my checker: **100% first-pass validity, 0% fallback.** Same standard — the
adversarial tests still reject a wrong number under a right citation, an invented reference and a talked-up
severity.

That sample also caught a bug in the *pack*: a volume label keyed by metric rather than by grain, so a
product-group fill rate weighted by order lines was labelled "ordered pounds". A wrong label on a right number —
passes every numeric check, misleads the reader anyway.

The habit worth taking: **when your evaluation says the model is failing, check the evaluation first.** It's the
cheaper hypothesis, and it's right more often than is comfortable.

*How much of your model's measured failure rate is actually your measurement?*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the before/after table with the four causes.

---

## Post 18 · A pause that survives the process is the only reason there's a framework

📌 *Field Notes — post 18 of 20. Two posts a week.*

An architecture question worth being disciplined about: **when does a workflow justify a framework?**

In the first system, the pipeline is fixed — profile, map, validate, publish. That's a function call. I used
plain code, and there's no workflow framework anywhere in it. Nobody asked why, because nobody missed it.

The second system has one property the first doesn't.

**The brief is built at 06:00. Somebody approves an action at 14:00 — possibly from a different machine, long
after the original process has exited.**

That's a state machine with a checkpoint in the middle. Writing it by hand means writing serialisation,
resumption and a step ledger — which is what a workflow framework already is. So I used one:

```
build pack → narrate → draft actions → policy gate → ⟪PAUSE⟫ → execute → record
```

compiled with an interrupt before the executing step, and a Postgres checkpointer holding the paused state.

**The interrupt is a stop, not a flag.** This is the part I'd defend hardest.

The executing step is never *asked* whether approval happened. The graph cannot reach it without a person
resuming the run. There's no boolean somebody can flip, no branch that skips a check — because the check isn't
code. It's the shape of the graph.

And the test that earns the dependency: start a run, capture the pause, then **throw away the compiled graph,
the checkpointer and the database connection entirely**, and resume from a fresh set in a new process.

If that didn't have to work, plain code would do — and I'd have used it, as I did in the other system.

Smallest abstraction that fits, in both directions. Reaching for a framework you don't need is a cost you pay
immediately, in complexity. Refusing one you *do* need is also a cost — paid later, in somebody's on-call
rotation, by someone who wasn't in the decision.

The useful discipline isn't "avoid frameworks". It's being able to name the specific property that nothing
simpler provides.

*What's in your stack that you'd remove if you had to justify it by a property nothing simpler gives you?*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the graph with the interrupt marked, next to the restart test.

---

# Week 10 — Production, and the report that includes what failed

## Post 19 · Deploying it is how I found the bug

📌 *Field Notes — post 19 of 20. Two posts a week.*

Both systems are deployed — API, console, scheduled job, database. The deployment earned its keep in the first
ten minutes.

The operations page showed **model cost $0.0000**. The usage table directly beside it showed **$0.0029**.

The join keyed on the evidence pack's `audience` field, which stored the reader's *role*, while the run record
stored their *user id*. Two concepts wearing one field name.

Every local test passed — because every local test used the same wrong value on both sides of the join.

Only production surfaced it, because only production had a page where those two numbers appeared next to each
other and disagreed.

Three more things the same session taught:

**A command that runs somewhere you didn't expect.** The platform's `run` command executes on *your* machine
with the deployment's environment variables injected — so it cheerfully tried to reach a private hostname that
only exists inside the cluster. The fix was a one-off execution inside the container, plus an explicit
`--database-url` flag for the workstation case.

**A foreign key that modelled reality wrongly.** I'd pointed the mock ticket table at the actions table with a
foreign key. The mock failed, because the executor runs before the record is written.

The fix wasn't reordering. It's that **a real ticket system has no referential integrity with your database.**
It accepts a ticket whether or not you've finished writing your row, and keeps it if you delete yours. My mock
was failing in a way the live adapter never could — which meant my model of the live adapter was wrong.

**Configuration precedence bites.** The repository's `.env` deliberately wins over the shell, so a subprocess
ignored the test URL I'd exported. The rule was right. My expectation wasn't.

None of these are findable on a laptop. Which is rather the point of deploying things — and the reason I'd
always rather own a small system in production than hand a large one over at the door.

*What did your last deployment teach you that your test suite couldn't have?*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** the ops page showing $0.0000 next to $0.0029.

---

## Post 20 · Ten weeks, two systems, and a report that includes what failed

📌 *Field Notes — post 20 of 20. The last one. Thank you for reading.*

Both systems are finished and running. Here's the closing accounting, including the parts that missed.

**The trust foundation.** Validation coverage: every row, every rule, as a hard gate. Exception recall 100%,
precision 99.6%. Drift detection 2 of 2 with zero false alerts. Two consumer views agreeing across 114 of 114
periods. Document retrieval 87% recall with zero permission leaks. **And the triage agent at 82% against an 85%
target — missed**, with the report naming the pattern it fails on.

**The command center.** Detection recall 100%, precision 80%, attribution 100%, median lead time one day, the
holiday decoy never raised as an incident. Citation coverage 100% across 652 numbers in twenty real briefs, at
about a fifth of a cent each.

Both reports state their own caveats. Precision counts every unexplained alert as wrong, and the synthetic world
promises nothing about the other 529 days — so 80% is a floor, not an estimate. And because thresholds were
tuned on the first half of the history, the untouched half is scored separately.

Three habits I'd carry into any engagement:

**Ship the non-AI baseline in the same pull request as the AI.** It's the only thing that tells you what the
model is worth, and the only thing that still runs when the provider doesn't.

**Measure against a known answer.** Synthetic data with planted faults isn't a shortcut around real data — it's
the only way "recall is 100%" is a sentence that means anything.

**Publish the misses.** A report with no failures in it isn't a report, it's a brochure, and every technical
reader knows it on sight.

One thing I'd do differently: **measure earlier.** The 2% precision run should have happened in week one of that
build, not week two. Everything I did in between was written against thresholds I'd guessed — and I had to
revisit all of it.

Thank you to everyone who argued with me in the comments over these ten weeks. The best follow-up work came from
the disagreements, particularly on the permission-filtering post.

*If you've read this far: which of the two systems would you have built first, and why?*

*Field Notes draws on patterns from recent client work. Every example, figure and screenshot comes from a
reference implementation I built on synthetic data — no client names, data or numbers appear.*

**Visual:** both report tables side by side, misses included.

---

## Publishing notes

**Cadence.** Two a week for ten weeks. Tuesday and Thursday mornings tend to work; avoid Friday afternoons.
Post 0 goes up first and gets pinned to your profile for the duration.

**If you want a strong start**, don't open with post 1. Open with **14** (the 2% precision run) or **15** (the
deleted detector) and backfill context afterwards. Failure posts carrying numbers travel furthest, and the
discovery post reads better once people already believe you can build.

**The five strongest** — in order: **14, 15, 17, 11, 19.** Each contains a measurement that contradicted an
assumption, which is the only thing that reliably separates a practitioner from a summariser. It also can't be
faked, which is why it's persuasive.

**The visuals do the heavy lifting.** The blocked correct fix. The two overlapping distributions. The flat
company-total chart beside the collapsing lane. If a post needs three paragraphs to explain its picture, the
picture is wrong.

**Reply for the first two hours.** The series exists to start conversations, not to broadcast. Someone will
disagree about permission filtering or about frameworks — those are the threads worth your afternoon.

**Every number must be checkable.** The repositories, the decision records and the generated reports are public,
and the figures in these posts must match them exactly. If a post wants a number the reports don't contain,
change the post. Never round one into existence.
