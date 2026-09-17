# The story of the trusted data foundation

*Written to be read start to finish by someone who has never worked with data pipelines, and to leave that
person able to argue about them. No prior knowledge assumed. Every term is explained the first time it appears.*

All the data here is invented and the customer is fictional. That is not a limitation — as Chapter 2 explains,
it is the only reason the numbers in this document mean anything.

---

## Chapter 0 · The factory, the spreadsheets, and the argument

Picture a company that turns animals into food. Not a metaphor — an actual meat processor, three factories, a
few hundred people. Call it Prairie Bend Foods.

Every day it takes orders from supermarkets and restaurants, produces the goods, and ships them. A single order
might say: *500 cases of chicken breast, delivered Thursday, to the depot in Cedar Falls.*

Two things about this business make it harder than it sounds.

**The first: nothing weighs what it says it weighs.** A case of chicken breast is roughly 10 kg — never exactly.
Meat is sold by *catch weight*: you order 500 cases, you receive 500 cases, and the invoice is for whatever they
actually weighed, which might be 4,940 kg or 5,080 kg. So "did we deliver the full order?" has two different
answers depending on whether you count cases or kilograms, and both answers are legitimate.

**The second: the clock is running.** Fresh meat has a shelf life measured in days. A pallet that sits in the
wrong corner of a warehouse for a week isn't inventory any more, it's a write-off.

The company measures itself mainly with one number: **OTIF** — *on time, in full*. Of everything we promised to
deliver, what share arrived when we said it would, complete? If OTIF is 95%, one delivery line in twenty
disappointed somebody.

Now the problem.

The company runs its operations on a system called **Infor M3** — an ERP, meaning the big central software that
knows about orders, stock and invoices. Each of the three factories exports data out of it into files. Those
files go to the reporting team, who load them into two different business-intelligence tools — the software that
draws the charts executives look at.

And the two tools disagree. Both report OTIF. They differ by a couple of points, every month. Nobody can say
which is right.

Meanwhile someone senior has read about AI and asks the reasonable question: *"can we get an AI agent to sort
this out?"*

That request is where this project starts, and the first thing worth doing is refusing it — carefully.

### Why "we want an AI agent" is a symptom, not a specification

Here is what happens if you say yes immediately.

You build something clever that reads the company's data and answers questions about it. It is fast, it is
fluent, and it is wrong in exactly the ways the underlying data is wrong — except now the wrongness arrives in
confident prose instead of a spreadsheet, which makes it harder to catch, not easier.

You haven't settled the argument between the two BI tools. You've automated it.

So the first question in the room isn't *what shall we build* but **"walk me through the worst Tuesday of your
month."** That question is the whole job. The answers were: the kilogram file, the three date formats, the
customer code that arrives with a hyphen in it, the Monday morning when a dashboard goes blank and three people
spend a day finding out why.

None of those needs intelligence. They need **trust**: knowing what a number means, where it came from, and what
was thrown away on the way to producing it.

That is what this first system does. The agent comes later — and it is better for having waited.

> **In the room:** "You don't have an AI problem yet. You have three spreadsheets that disagree, and an agent
> would just argue with them faster. Let me show you the sequencing I'd suggest instead."

---

## Chapter 1 · Building the ruler before the thing you measure

Before writing a single line of the actual system, I spent four days building something that produces no
features at all: a **generator** that invents a fake version of this company.

Fifty thousand order lines. Three factories, thirty-odd customers, eighty products. Deliveries, inventory
snapshots, production runs. All synthetic, all plausible.

And then — the important part — I deliberately broke some of it, in places I wrote down.

### Why fake data, when real data exists?

This is the part that sounds backwards until you sit with it.

Suppose you point a data-quality system at the company's real files and it reports: *"4,312 problems found."*

Good? Bad? To know, you'd have to know how many problems were actually in there. Nobody does. That's the whole
reason you're building the system. So the number is **unfalsifiable** — it cannot be checked, which means every
claim you build on top of it is decoration.

With invented data, you know the answer before you start. You put exactly 340 malformed customer codes in there.
If the system finds 340, that's **100% recall** — and "recall" is now a word with a real meaning instead of a
gesture.

Every number in the rest of this document exists because of this choice.

### Two details that mattered more than they should have

**The world has to be identical every time.** Same starting seed, same fifty thousand rows, byte for byte. My
first version had a subtle flaw: the order of some internal dictionary changed between runs, which quietly
changed the answer key. So yesterday's score of 96% and today's 94% were measured against *different worlds*.
The system hadn't got worse. The ruler had moved.

That class of bug can make an entire evaluation suite meaningless while every individual test still passes.

**The generator refuses to lie.** After creating the world, it re-measures each fault it planted — comparing the
affected window against a baseline period — and if any planted fault isn't actually visible in the numbers, it
**fails the build and writes nothing**. A ground truth that quietly lies would invalidate every measurement
downstream, forever, and you would never find out.

> **In the room:** "Before I build the detector I build the thing that can prove it's wrong. Four days, no
> features. Everything I tell you afterwards is a number you can check."

**The expert layer.** What's really being constructed here is an *evaluation harness* — the same discipline as
a test set in machine learning, applied to a data platform. The properties that matter are determinism,
committed ground truth, and a self-check that fails closed. Once you have those, arguments about system quality
stop being opinions.

---

## Chapter 2 · Reading the data as it is, not as the documentation claims

The first real component is a **profiler**. Give it a file, and it works out what's actually in it: what type
each column really holds, how often it's empty, what range the values fall in, which columns could serve as a
unique key, and where the file disagrees with the company's master records.

Sounds mundane. It replaces a conversation that otherwise goes badly.

Because there *is* documentation. There's a field specification written years ago describing what each column
should contain. It is a description of intentions, and it was written before the third factory existed.

The data tells you what's there now. The specification tells you what someone hoped for in 2013.

A concrete example from this world: a column called `DATE` contains four different formats, because three
factories and one legacy export each had their own convention. No documentation mentions this, because at the
time each was written, it was true.

So the profiler infers from the files and the master data only, and its findings are **committed to the
repository and checked against the answer key**. Not a report someone reads once — a test that fails if the
profiler starts lying.

> **In the room:** "Your spec says this field is a date. In the last quarter's data it's a date in four
> formats, and 3% of the time it's empty. I'm not saying your spec is wrong — I'm saying it describes a
> different decade."

---

## Chapter 3 · The most important habit in applied AI work

Now the first genuinely hard problem: **mapping**.

Each factory's file has its own column names for the same things. `DELIVERY_DT`, `del_date`, `Delivery Date` —
one field, three spellings. Multiply by forty fields across three sources. Someone has to say which column means
what, and that mapping has to be right, because everything downstream depends on it.

This is a textbook job for a language model. They're genuinely good at it.

**I wrote the stupid version first, on purpose.**

A few hundred lines: normalised string similarity (strip punctuation, lowercase, compare), a synonym table for
the obvious domain terms, a type-compatibility check using the profiler's findings, and a confidence threshold.
No AI anywhere.

Then I built the LLM version. And I scored both against the same answer key — on two separate sets: column
headers the system had already seen, and **headers neither had ever encountered**.

### Why the second set matters

Test a mapper only on headers it's seen, and you're testing its memory. The question that matters operationally
is what happens next quarter when a fourth factory arrives with its own spelling of everything.

### What the baseline actually buys you

Three things, all practical:

**A sentence that means something.** "The model scores 100%" is empty until you can say what it beat. With a
floor, the model's contribution becomes a measured *lift* rather than a feeling.

**A system that degrades instead of stopping.** When the AI provider has an outage — and they do — mapping still
runs. Worse, but running.

**An answerable cost question.** "Is the LLM worth paying for here?" becomes arithmetic instead of a debate
between whoever is loudest.

If I had to compress fifteen years of engineering judgement into one rule for AI features, it would be this:
**ship the non-AI baseline in the same pull request as the AI.** If you can't be bothered to build it, you have
decided the model's value is a matter of faith.

> **In the room:** "The heuristic is the floor. It's free, it runs when the API is down, and it's the only
> reason I can tell you what the model is actually worth."

---

## Chapter 4 · The model proposes; a person with authority decides

Here's the failure this chapter prevents, stated precisely, because it's the most expensive one in the whole
field:

A column called `net_weight_kg` gets mapped to the canonical field the metric layer reads as pounds. Nothing
errors. No test fails. The dashboard renders beautifully. And every weight-based number in the company is wrong
by a factor of 2.2 until somebody happens to notice — which, in my experience, takes a quarter.

So the mapping flow has a shape that has nothing to do with AI:

**The model proposes.** One call, with a strict output schema so the response is machine-checkable rather than
prose to be parsed. If the response doesn't satisfy the contract, retry exactly once; if it fails again, fall
back to the heuristic. The model never gets to grade its own answer.

**A person confirms.** An owner sees the proposal with the profiler's evidence beside it — what's actually in
that column, sample values, how often it's empty — and accepts or corrects it. Nothing downstream moves until
they do.

**The confirmation is versioned and bound to an exact shape.** Not "factory two maps like this" but "this exact
arrangement of column headers maps like this, confirmed by this person, on this date". When the source changes
shape, the binding stops matching, and the pipeline says so instead of guessing.

The obvious objection: *why not auto-apply when the model is confident?*

Because **confidence is the model's opinion of itself.** It is not a probability of being correct, and treating
it as one is exactly how you get the silent 2.2x error above.

The human step costs about four seconds per header, once, per source shape. Skipping it costs a number nobody
can trust and a quarter of retrospective correction.

> **In the room:** "The model reads column names well. It has no authority. A mapping is a business decision
> with an audit trail, and those two facts don't conflict — they're what makes the AI safe to use here."

---

## Chapter 5 · The worst line of code in any data pipeline

It's this one:

```sql
WHERE valid = true
```

It doesn't throw an error. It doesn't write a log line. The job finishes green. The dashboard renders. And four
thousand rows are simply *gone* — because somewhere upstream they failed a check nobody surfaced.

You find out three weeks later, when a customer asks where their order went, and somebody spends a day proving
the pipeline "worked correctly". Which it did. That's the problem.

So this system has a rule with no exceptions: **nothing is ever silently dropped.** Every row ends in exactly one
of three states — published, held, or explained.

### How that works mechanically

Twelve validation rules run over every row. Things like: does this delivery date fall inside a plausible window;
does the weight per case make sense for this product; does this customer code exist in the master data; is this
unit of measure one we recognise.

A row that fails doesn't vanish. It becomes an **exception**: a queue item with an owner, a severity, a reason
in plain language, and a key pointing back to the exact source row it came from.

Not a log line — a thing on somebody's list.

### Two decisions inside that

**Deterministic, not model-judged.** A validation rule must give the same verdict on the same row twice, and be
explainable to an auditor in one sentence. That rules out a language model, and it rules out anything with
randomness in it. These are boring checks by design, because "boring and repeatable" is the requirement.

**Coverage is a hard gate, not a metric.** Not "we validated most rows". Every row, every rule — 51,651 × 12 —
and the build *fails* if that product doesn't match. A metric you're allowed to miss by 2% quietly becomes 80%
over eighteen months. A gate you cannot miss stays at 100% or stops the line.

### The number, and a confession inside it

Against the planted faults: **recall 100%, row-level precision 99.6%.**

And one rule was wrong — mine, not the data's. I'd set a plausible-range ceiling based on an assumption about
case weights. The answer key disagreed. I checked, and the data was right: the real ceiling was higher than I'd
assumed. So the rule changed, not the answer key.

That's the whole reason for having an answer key. It gets to tell you that you were wrong.

> **In the room:** "Every row is accounted for. Not 'most rows' — every row, and the ones that failed have
> somebody's name against them."

---

## Chapter 6 · "Who decided this was fine?"

Exceptions get resolved. Someone looks at 340 rows with a malformed customer code and decides what happens to
them.

The naive design is a status column: `open` → `resolved`. It answers "what is it now?" and it destroys the
question any auditor actually asks: **what happened, in what order, and who decided?**

So resolution here is **append-only**. Assigned, commented, resolved-with-a-kind, reopened — each is an event
with an actor and a timestamp. The current state is *derived* from the events rather than stored instead of
them.

Three things fall out of that, two of which I didn't design for:

**Reversibility.** A resolution applied to the wrong batch is undone by appending another event, not by editing
history into a shape that no longer explains itself.

**Resolutions have kinds, and they aren't all "fixed".** Some mean *the source genuinely does not carry this
field for this period*. That is a legitimate, permanent, correct outcome — and it is a completely different
thing from a correction. If your only verb is "resolve", those two collapse into one, and your data quality
metrics start lying to you in a way that looks like progress.

**Authority is enforced server-side, not implied by the interface.** Resolving with a publishable kind is
owner-only — and not by hiding the button. By rejecting the request.

This is unglamorous plumbing. It is also the first thing a regulated customer asks about, and an engineer who
can't answer "show me who approved this and when" doesn't get a second meeting.

> **In the room:** "The status tells you where we are. The event log tells you how we got here — and that's the
> one your auditor wants."

---

## Chapter 7 · The Monday morning blank dashboard

Upstream systems change without telling you. The only real question is whether you find out from your pipeline
or from your CEO.

The classic sequence: someone renames a column in the source system on Friday. The job runs over the weekend.
The transform finds nothing to map, so it writes nulls. Monday morning the dashboard renders zeros, and three
people spend the day finding out why.

This system takes a **fingerprint** of each source file's *shape* — column names, order, types — coarse enough
to be stable across ordinary variation, sharp enough to catch a rename. When the fingerprint changes, that's a
**drift event**.

Three decisions inside that, each one the difference between an alert people act on and an alert people filter:

**One alert per new shape, not per row.** A renamed column affects forty thousand rows. Forty thousand
notifications teaches everyone to route your alerts to a folder they never open. One alert, naming the shape,
with the count attached.

**Publishing is blocked until someone confirms a mapping for the new shape.** Not "best effort with nulls",
which is the silent drop wearing a different hat. The pipeline stops and says precisely what it needs.

**The alert names what breaks.** When a mapping is confirmed, the path from source column → canonical field →
metric → view is recorded. So the alert doesn't just say "column changed", it says which metrics and which
dashboards were about to go quietly wrong.

Two planted drift events. Both detected, both named, zero false alerts.

One detail worth stealing: that impact list is **derived from the compiled metric definitions**, never
maintained by hand. A hand-written lineage document is accurate on the day it's written and lying within a
month. Nobody updates them — so don't have one.

> **In the room:** "Source changed, fingerprint changed, publish blocked, breakage named — on Friday. Compare
> that to a blank dashboard on Monday and a day of archaeology."

---

## Chapter 8 · The strictest gate in the system

There's a moment in every data platform where numbers stop being "some data" and become "the truth" — the point
where downstream consumers read them. It is the cheapest place to stop a bad number and the most expensive place
to be wrong.

So publishing here refuses more than it accepts. Per batch, it rejects:

**Stale batches** — and staleness is measured from the *extract* timestamp, not the load one. A file that sat in
a folder for three days is three days old, however recently you got around to loading it. This distinction
matters more than it looks: measuring from load time means every delayed file looks perfectly fresh.

**Drifted batches** — the shape changed and nobody has confirmed a mapping for it.

**Rows in an unresolved exception state** — held back *individually*, not failing the whole batch. The clean rows
publish; the disputed ones wait with their owner. All-or-nothing publishing is how you end up with people
resolving exceptions carelessly at 5pm to unblock a release.

And the whole operation is owner-only, checked on the server.

One more property that took a rewrite: **idempotency** — a word meaning "doing it twice has the same effect as
doing it once". Publish the same batch three times, get the same gold data, not three copies. I first tried
keying on a hash of the content, which seemed elegant until I realised it makes a legitimate re-send of
*corrected* data indistinguishable from an accidental duplicate. Keying on the request identity fixed it.

Replay three times, row counts unchanged. That's a test, and it runs on every commit.

> **In the room:** "Publishing is the moment data becomes a decision. It gets the strictest gate in the system,
> and it refuses far more than it accepts."

---

## Chapter 9 · The argument was never about the data

Back to the two BI tools that disagree about OTIF.

Everyone assumed a data problem — a join, a filter, late-arriving records. It wasn't.

Each tool had its own formula for OTIF, written by a different person at a different time, living inside that
tool's own semantic layer. Neither was written down anywhere a human could read. **Both were "correct". They
meant different things by the same word.**

One of them counted a partial delivery as a miss. The other counted it proportionally. Nobody had ever put the
two definitions side by side, because there was nowhere to put them.

The fix is not better plumbing. It's one definition:

**Metrics live in versioned files** — human-readable, reviewed like code — and are *compiled* into the SQL views
that every consumer reads. Change the definition once and every downstream view changes with it, with a version
number and an author attached.

**The expression language is deliberately restricted.** Not arbitrary SQL pasted into a config file, which is
remote code execution wearing a bow tie. A constrained grammar: named fields, a fixed set of operators and
aggregations, compiled and validated. If a metric needs something the grammar can't express, that's a
conversation — which is the correct outcome, because it means somebody is about to encode a business rule and
should say so out loud.

**And reconciliation explains the historical gap by cause.** Not merely "they agree now". For each tool, using
that tool's own export, which specific difference produced which part of the discrepancy.

That last part is political as much as technical. It lets the reporting team see their number wasn't nonsense —
it was a different definition, honestly held. Migrations fail on that human point far more often than on the
engineering.

After the switch: **114 period-by-period comparisons, zero difference, every one.**

> **In the room:** "'Which number is right' is almost never the question. The question is where it's defined,
> who owns it, and when it last changed. If you can't answer that in one place, the data problem is a symptom."

---

## Chapter 10 · A knowledge layer that knows what it must not say

Now the company's documents: standard operating procedures, field specifications, change notices. People ask
questions of them constantly — *what's our tolerance on catch weight? which SOP covers a short shipment?* — and
finding the answer means knowing which of two hundred documents to open.

This is the part everyone now calls RAG — *retrieval-augmented generation*, meaning: find the relevant
documents, then let a model answer using them.

Some of those documents are restricted. And that changes the design in a way most implementations get wrong.

### The wrong way, and exactly why it's wrong

The tempting implementation: retrieve the most relevant twenty documents, drop the ones this user isn't allowed
to see, then answer from what's left.

That is wrong, and precisely so. By the time you filter, the restricted document has already been fetched,
loaded into memory, ranked, and placed one bug away from the output. A logging statement, a citation list
rendered before the filter runs, a prompt injection in the user's question — any of them leaks it.

So the permission check lives **inside the database query, before anything is ranked**. The document this person
may not see is never a candidate. Not hidden. Not filtered later. Never retrieved.

Measured, because assertions like this deserve numbers: in the configuration where the filter ran after
ranking — **four leaks**. With the filter in the query — **zero**.

### Three more details that decide whether this works

**Chunking at section level, with tables kept whole.** Documents are split into retrievable pieces. Split a
table across two pieces and you get confidently wrong answers about it, because half a table looks like a whole
one. Each piece also carries its document's metadata, so a retrieved fragment still knows which SOP version it
belongs to.

**Hybrid retrieval.** Two search methods combined: keyword search, which is excellent at exact tokens like part
numbers and clause references; and vector search, which understands that "short shipment" and "partial
delivery" are the same idea. Neither alone is enough. Fusing their rankings beat either — 74% to 87% recall.

And, as in Chapter 3, a simple statistical baseline is reported beside every configuration. Know what your
sophisticated thing beats.

**Refusal, in a fixed order.** The system refuses to answer when: the asker lacks access, or the best evidence
is too weak, or the question is outside the corpus. The *order* is load-bearing — access first, always. Because
"I don't have anything on that" must be indistinguishable from "you're not allowed to know", or the refusal
itself leaks the document's existence.

51 of 53 test questions answered well. Zero leaks. The two failures are written up rather than hidden: one long
document retrieved badly, which also caused an over-refusal.

> **In the room:** "Filter permissions in the query, not in the answer. I can show you the four documents that
> leaked when I did it the other way — that's the whole argument, and it took one experiment."

---

## Chapter 11 · The agent that cannot act

Now, finally, in week five: the AI agent the customer asked for in week one.

Its job is narrow. It reads an unresolved exception, gathers evidence using a handful of **read-only** tools
(look up the master record, check the source row, find similar past resolutions, read the relevant SOP), and
proposes one of four outcomes: a fix, an escalation, a "not covered by this source", or "I don't know".

Then it stops. Structurally.

### The moment that explains the whole design

The agent examines a customer code that arrived as `CUST-0004` where the master data says `C000004`. It proposes
normalising it.

**The proposal is correct.** Any engineer would make that change.

The policy gate refuses it — in code, before a human is asked. The operating procedure permits automatic changes
to exactly two things: units of measure and date formats. A customer identifier is neither. So the correct fix
becomes a proposal on somebody's queue, and a person approves it in four seconds.

That single interaction is the argument for the entire architecture: **correct and permitted are different
questions, and only one of them is the model's to answer.**

### How that's enforced, since "we have a policy layer" means nothing without mechanism

**The permitted fix kinds and the protected fields live in code**, not in configuration the running process can
read and rewrite. A policy loaded from a file the system can also modify is a suggestion.

**A configuration that widens the allowlist is refused at startup.** You may narrow permissions from config. You
may not broaden them. That asymmetry is deliberate: it means a misconfiguration can only ever make the system
more cautious.

**Approval re-checks everything.** An edited action, a direct API call, a resumed workflow — each re-enters the
gate. Approving means "do the thing policy permits", not "do anything".

**And the graph physically cannot reach the step that writes** without a person resuming it. That's the next
project's chapter, but the same idea starts here.

Sixteen adversarial fixes attempted. Sixteen blocked.

### The number that missed

The agent's end-to-end accuracy is **82% against an 85% target.**

It missed. The report says so, in the same table as the successes, and names the pattern it fails on: exceptions
whose expected resolution has no precedent behind it in the history — which is a data problem, not a prompting
one.

I could have moved the target to 80% and reported a pass. That is the single most common quiet dishonesty in
this field, and it is why nobody believes evaluation tables.

> **In the room:** "Here's the gate blocking a fix that's right. That's the point. And here's the number that
> missed its target, with the reason — because if I hid that one, you'd have no reason to believe the others."

---

## Chapter 12 · The change request nobody planned for

Six weeks in, the business changes its mind — as businesses do.

*From June, lot number becomes mandatory on every delivery line, for traceability. Historic data doesn't have
it. Nobody is backfilling three years of records.*

This is the moment that reveals whether your architecture was a decision or a diagram. In a hand-built pipeline
it's a week of work: a new column, new validation, an argument about the backfill, three report queries to
update, and a meeting about what to do with history.

Here it was **one dictionary file and one rule. About seventeen minutes.**

**The field dictionary is versioned.** So "lot number is required" isn't a global truth — it's `required_from:
2026-06-01`, evaluated against each row's own issue date. History isn't wrong. It's *before the rule*.

**One new validation rule** reads that dictionary instead of hard-coding the field name. Rows before the date
are untouched; rows after it without a lot number become exceptions with the owner and severity the dictionary
specifies.

**Rows from a source that never carried the field** come back as "not covered for this period" — that third kind
of resolution from Chapter 6, doing real work. Not a fix. Not a compliance escalation. The truth.

**Zero report queries changed**, because the consumer views compile from the metric definitions and nothing
downstream knew a field had been added.

The number for the client is seventeen minutes. The number for an engineer is *why*: because "required" was
**data** from day one rather than an `if` statement buried in a transform.

> **In the room:** "Your requirements will change — that's not a risk, it's a certainty. Here's the same change
> request arriving in this design: two files, seventeen minutes, no backfill, nothing downstream touched."

---

## What this project actually is

Strip away the specifics and four ideas remain, in order of how much they matter:

1. **Nothing is silently dropped.** Every row is published, held, or explained.
2. **Every number has one definition**, versioned, compiled to every consumer.
3. **The model proposes; a person with authority decides.** Mappings, resolutions, fixes.
4. **Every claim about the system is measured against a known answer** — including the two that missed.

376 tests. 33 decisions written down with their alternatives and evidence. Continuous integration on every push;
deploy only after green. Live on the internet.

---

## The part this really defends: what a forward-deployed engineer does

If you're reading this to judge whether I can do the job, here is the argument, made explicitly.

A forward-deployed engineer isn't a consultant who writes recommendations, and isn't a platform engineer who
receives specifications. The job is to sit inside somebody else's messy reality and come out the other side with
working software that survives contact with their organisation. Six things that requires, and where each one
appears above:

**Turning a symptom into a specification.** The engagement opened with "we want an AI agent". What it needed was
trust infrastructure first and autonomy second. Chapter 0 — including the part where you say that out loud to
the person who asked, in a way that doesn't sound like refusal.

**Working inside constraints you didn't choose.** SOP-DQ-001 permits automatic changes to units and date formats
and nothing else. I didn't negotiate that clause; I built a gate that enforces it, including when it blocks a
fix I personally think is correct. Chapter 11. An engineer who treats the customer's rules as obstacles doesn't
last a month on site.

**Building the measurement before the thing.** Four days on a generator that produces no features, because the
alternative is a system whose quality is a matter of opinion. Chapters 1 and 5.

**Choosing boring technology on purpose, and being able to say why.** Deterministic rules instead of an LLM for
validation. A heuristic shipped alongside the model. Postgres instead of a specialised vector database.
Chapters 3, 5 and 10. The skill isn't using sophisticated tools, it's knowing which problems don't need them.

**Handling the politics as part of the engineering.** The reconciliation that explains each BI tool's gap by
cause exists so the reporting team can see their number wasn't nonsense. Chapter 9. Migrations fail on that
human point far more often than on the technical one, and pretending otherwise is how you get a technically
correct system nobody adopts.

**Owning it in production, and reporting honestly.** CI on every push, deployed and running, a runbook written
for whoever is holding the pager at 07:00 — and an evaluation report that includes the agent at 82% against an
85% target, with the reason.

That last one is the whole posture. **The report includes what failed.** If it didn't, you'd have no reason to
believe the parts that passed — and neither would I.

---

*The second half of this story — what you do with trustworthy numbers at six in the morning — is in
[the command center's explainer](https://github.com/shekhus/m3-supply-chain-command-center/blob/main/docs/EXPLAINER.md).*
