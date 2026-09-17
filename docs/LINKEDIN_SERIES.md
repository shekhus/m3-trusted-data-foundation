# LinkedIn series — ten posts, one per week

Ten posts covering both projects, in build order. Each is ready to paste, with a suggested visual and the
decision record behind it.

**The rules these follow** (plan §4.4, and common sense):

- **No client names, no engagement details, no real numbers from anybody's business.** Every figure here comes
  from synthetic data, and each post says so at least once in the series.
- **Lead with the failure, not the stack.** "I built a thing with LangGraph" is a post nobody finishes. "Three
  weeks of a customer being let down, and the dashboard never moved" is one they do.
- **One idea per post.** The temptation is to list everything built that week; resist it.
- **Publish the misses.** The posts that will get replies are the ones where something was wrong and the
  measurement said so.
- **Anything M3-specific gets a sign-off before it goes out**, per the standing rule.

**Format:** a hook line, three to six short paragraphs, a closing line that invites disagreement rather than
applause. No hashtag spam — two or three at most. Links to the repo in the first comment, not the post body
(LinkedIn suppresses posts with outbound links).

---

## Post 1 — "We want an AI agent" is not a problem statement

**Hook:** A manufacturer asked me for an AI agent. I spent the first week not building one.

Three plants, three slightly different extracts of the same data. Two BI tools reporting the same KPI and
disagreeing with each other. Nobody could say which was right.

An agent on top of that doesn't help. It produces confident, untrustworthy sentences faster than a person
could — and harder to check.

So the first project isn't intelligence. It's trust: what the numbers mean, where they came from, and what got
thrown away on the way. The agent comes second, and it's better for it.

**Closing:** The hardest part of most "AI projects" is the part that isn't AI. What's the worst example you've
seen of a model layered on top of data nobody trusted?

*Visual:* the two-project architecture diagram, A feeding B.
*Backed by:* the discovery brief; `docs/EXPLAINER.md` opening section.

---

## Post 2 — The heuristic exists to make the LLM prove itself

**Hook:** I wrote a dumb version first, on purpose. It's the only reason I can tell you what the model is
worth.

The task: map each plant's columns to a canonical schema. `DELIVERY_DT`, `del_date`, `Delivery Date` — all the
same field, none spelled the same way.

Version one: string similarity and a rules table. Free, offline, works when the provider is down.
Version two: an LLM.

Both scored against the same answer key, on headers they'd seen *and* headers they hadn't.

Without that baseline, "the model got 87%" means nothing — 87% against what? With it, the model's contribution
becomes a measured lift instead of a vibe. And when the API is down, the floor still runs.

**Closing:** If you've deployed an LLM feature without a non-AI baseline beside it, you don't know what it's
worth yet. What would your baseline be?

*Visual:* two-column comparison, heuristic vs LLM, both against the answer key.
*Backed by:* `D-013`, `D-014`.

---

## Post 3 — The enemy is the silent drop

**Hook:** The worst line of code in a data pipeline is `WHERE valid = true`.

It doesn't error. The dashboard still renders. Four thousand rows are simply gone, and you find out when a
customer asks why their order isn't there.

So in this project nothing is ever dropped. A row that fails validation becomes an **exception**: a queue item
with an owner, a severity and a reason, keyed back to the source row. It's either published, held, or
explained — never silently removed.

Coverage is a hard gate, not a metric: every row, every rule. Synthetic data, so I can prove it — 51,651 rows
× 12 rules, and the exceptions match the seeded answer key at 100% recall.

**Closing:** Dropped rows are the cheapest bug to write and the most expensive to find. Where does your
pipeline quietly throw things away?

*Visual:* the exception queue screen.
*Backed by:* `D-018`, `D-021`.

---

## Post 4 — Your dashboard goes blank on Monday. Mine names the breakage on Friday.

**Hook:** Source systems change without telling you. The question is whether you find out from your pipeline or
from your CEO.

A column gets renamed upstream. In the old world: the job runs, the dashboard renders empty, three people spend
Monday finding out why.

Here: each source file has a shape fingerprint. When the shape changes, that's one drift event — not forty
thousand row-level alerts, which is how you train everyone to ignore alerts. Publishing is blocked until
somebody confirms a mapping for the new shape, and the lineage says exactly which metrics and views were about
to break.

Two seeded drift events, both detected and named. Zero false alerts.

**Closing:** Drift detection is unglamorous and it buys you the Monday back. What's your current early warning
for an upstream change — honestly?

*Visual:* the drift alert naming the impacted metrics.
*Backed by:* `D-020`, `D-022`, `D-023`.

---

## Post 5 — They didn't disagree about the data. They disagreed about the definition.

**Hook:** Two BI tools, one KPI, different answers. Everyone assumed a data problem. It was a definitions
problem.

Each tool encoded its own slightly different formula in its own semantic layer, and neither was written down
anywhere a human could read.

The fix isn't a better ETL. It's one definition — versioned, in a file, compiled into the views both tools
read. Change it once, and every consumer changes with it.

114 period-by-period comparisons, 0.00 delta, every one. And a reconciliation that explains each tool's
historical gap *by cause*, from that tool's own export.

**Closing:** "Which number is right?" is usually the wrong question. The right one is "where is this defined,
and who changed it last?"

*Visual:* the metric YAML beside the two generated views.
*Backed by:* `D-011`, `D-024`.

---

## Post 6 — The evidence pack: the model's entire world

**Hook:** The most important design decision in my AI feature was what *not* to give the model.

Project B writes a morning brief. The model never sees the database. It gets one JSON: the flagged items, their
drivers, the reason each was flagged — and a flat list of every number it's allowed to use, each already
rounded and named.

Two consequences:

It can't average anything, because it never receives anything to average.

And it's asked to *copy* "74.1%", never to turn 0.74138 into a percentage. A model doing that will sometimes
write 74%, sometimes 74.14%, and occasionally 74.8% — and the last one is indistinguishable from the others
inside a confident paragraph.

**Closing:** Most hallucination mitigation is prompt engineering. Some of it is just not handing over the raw
material. What's in your prompt that doesn't need to be?

*Visual:* a redacted evidence pack beside the brief it produced.
*Backed by:* `B-007`.

---

## Post 7 — The validator that rejected my model, and the three times it was wrong

**Hook:** I built a fact-checker for my own AI feature. Then it failed 25% of the briefs — and it was mostly my
fault, not the model's.

Every claim in the brief must cite a metric. That part is structural: the citation field is required, so an
uncited claim is a shape the model can't return. The validator's real job is the harder one — catching a claim
that cites a *real* fact and then states a *different* number. Wrong, cited, and completely convincing.

First live run over 20 briefs: citation coverage 100%, but a 25% fallback rate. I went looking for a model
problem. Three of the four causes were mine:

→ The model wrote plant names with a non-breaking hyphen. My validator compared bytes, saw a stray "02", and
rejected perfectly good sentences.
→ It wrote "fell by 9.4 points" where the fact was −9.4. I was enforcing notation, not truth.
→ My JSON schema used a nullable type the provider rejects outright.

After fixing my checker: 100% first-pass validity, 0% fallback, same standard. The adversarial tests still fail
a brief for a wrong number under a right citation.

**Closing:** When your eval says the model is failing, check the eval first. How often does that turn out to be
the answer?

*Visual:* the before/after table.
*Backed by:* `B-008`.

---

## Post 8 — My detector scored 2% precision. That was the most useful day of the project.

**Hook:** First full replay over 18 months of history: recall 100%, precision **2%**. 383 alerts, 377 with
nothing behind them.

Four causes. Only one was subtle:

**1. My thresholds were guesses.** The "high" line for OTIF was 0.90. The measured median was 0.905. It fired
on half of all days. Every level is now set from the observed distribution, with the percentile written next to
it in the policy file.

**2. A line drawn for the company is not the line for one lane.** A single lane ships a handful of orders a
day, so its daily rate is basically 0 or 1. No absolute threshold means anything there.

**3. An incident is a policy breach, not a statistical shift.** Statistics can say "this moved". Only the
business can say "this matters". Before that rule, the statistical detectors produced 322 of 365 alerts.

**4. A real bug:** my CUSUM never reset after signalling, so one shift reported itself every day for a
fortnight.

After: recall 100%, precision 80%, and — because I'd tuned on the first half of the history — the held-out half
is reported separately. It scores 100%.

**Closing:** Every detection system I've seen ships with thresholds someone guessed in a meeting. Has yours
ever been measured against what the data actually does?

*Visual:* the before/after precision numbers with the four causes.
*Backed by:* `B-006`.

---

## Post 9 — The rule I deleted, and the holiday that isn't an incident

**Hook:** The most useful thing my evaluation told me was that one of my detectors could never work.

**The rule I deleted.** Backlog build-up seemed obviously detectable: watch the queue, alert when it spikes.
Then I measured. The genuine seeded surge peaks at 2.8 standard deviations. Ordinary weekly swings reach 5.0.
No threshold admits the real one without admitting all of them.

I removed the rule. It's watched and reported, nobody gets woken, and the policy file says why — with the
numbers and what would fix it. Tuning it until the answer key looked good would have been the easy option and
a lie.

**The holiday.** The synthetic world contains a deliberate decoy: a genuine OTIF collapse over a public
holiday. It's real — and it is not news. A detector without day-of-week and holiday awareness raises it as a
crisis and loses precision.

Mine reports it, names the shutdown before the numbers, and caps it below incident level. And there's a test
that the *same* detectors with the holiday calendar emptied **do** raise it — otherwise the decoy proves
nothing.

**Closing:** "We couldn't measure it reliably, so we don't alert on it" is a perfectly good engineering answer.
When did you last delete a feature because the evidence said to?

*Visual:* the two z-score distributions overlapping.
*Backed by:* `B-006`, `B-004`.

---

## Post 10 — The gate that blocks a fix that's correct

**Hook:** My favourite demo moment is watching the policy gate refuse a fix that is *right*.

The agent looks at a malformed customer code, `CUST-0004`, and proposes normalising it to `C000004`. That is
the correct answer.

The gate refuses it. The standard operating procedure permits automatic changes to units and date formats —
nothing else. So the correct fix becomes a proposal for a person, and a human decides in four seconds instead
of four minutes.

That's the whole idea: **correct** and **permitted** are different questions, and only one of them is the
model's to answer. The allowed actions live in a config file the business owns. They can't be widened from a
prompt, from the console, or at the moment of approval — every route re-checks.

Sixteen of sixteen adversarial fixes blocked. And in the sister project, a graph that *structurally cannot
reach* the step that acts until a person resumes it — verified by a test that throws the whole process away
between the pause and the approval.

**Closing:** Most "human in the loop" designs are a checkbox someone clicks without reading. What makes yours
real?

*Visual:* the blocked fix, with the SOP clause beside it.
*Backed by:* `D-029`, `D-030`, `B-009`, `B-010`.

---

## Optional 11 — The closer

**Hook:** Ten weeks, two systems, every number in the reports reproducible with one command. Here's what I'd
tell myself at the start.

Three things I'd keep:
→ **Always ship a non-AI baseline.** It's the only thing that tells you what the model is worth.
→ **Measure against a known answer.** Synthetic data with seeded faults isn't a shortcut, it's the only way
"recall 100%" means anything.
→ **Publish the misses.** Both reports include targets that were not met and say why. A report without a
failure in it isn't a report.

Two things I got wrong and fixed publicly: a validator that was stricter than the truth, and thresholds I'd
guessed in a config file before looking at the data.

One thing I'd do differently: measure earlier. My detector's 2% precision run should have happened in week one
of that build, not week two.

**Closing:** Both repos are public, with the decision log — including the decisions I reversed. Ask me the
hard ones.

*Visual:* the two report tables side by side, misses included.

---

## Publishing notes

- **One a week** keeps the rhythm the plan set. The order above is the build order, which is also the
  narrative order.
- **Posts 7, 8 and 9 are the strongest** — they contain a failure and a measurement. If the series needs a
  strong start, lead with 8 and fill in the context afterwards.
- **The visuals do the work.** A screenshot of the blocked fix or the before/after precision table is worth
  more than the paragraph explaining it.
- **Reply to every comment for the first two hours.** The series exists to start conversations, not to
  broadcast.
- Every post should be checkable: the repos, the decision records and the generated reports are all public, and
  the numbers in the posts must match them exactly. If a post needs a number the reports don't have, don't
  round it into existence — change the post.
