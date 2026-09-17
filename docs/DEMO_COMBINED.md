# Combined demo — both projects, one recording

**Eighteen minutes.** One story in two halves: a foundation that makes numbers trustworthy, and a system that
acts on them. Recorded as one piece, because the second half is only interesting if the first half is true.

Project B has its own twelve-minute script at
[`m3-supply-chain-command-center/docs/DEMO.md`](https://github.com/shekhus/m3-supply-chain-command-center/blob/main/docs/DEMO.md)
for when you want to show only that. This one is the version to publish.

---

## Before you record

Both stacks up, both consoles open, terminals in each repo. Have these tabs ready:

1. Project A portal — the exception queue
2. Project A portal — the agent's blocked fix
3. Project B console — the morning brief
4. `evals/REPORT.md` from each repo

Say once, near the start: **"All the data is synthetic and the customer is fictional."** Then stop
apologising for it — the point of synthetic data is that the measurements mean something.

---

## 0 · The premise (45 seconds)

> "A food manufacturer runs Infor M3 across three plants. They asked for an AI agent.
>
> I built two things instead. The first makes the numbers trustworthy. The second acts on them. The second one
> is only worth anything because of the first, and that's the whole argument."

*On screen:* the two-repo diagram — A produces gold, B consumes it.

---

# Part one — the foundation (7 minutes)

## 1 · Why "we want an agent" wasn't the problem (1 minute)

> "Three plants, three slightly different extracts of the same data. One ships weights in kilograms. Two BI
> tools report the same KPI and disagree with each other, and nobody can say which is right.
>
> Put an agent on that and you get confident, untrustworthy sentences — faster than a person, and harder to
> check. So the first project has no agent in it for four weeks."

## 2 · Nothing is silently dropped (2 minutes)

*Open the exception queue.*

> "Twelve rules run over every row. A row that fails doesn't disappear — it becomes an exception with an owner,
> a severity and a reason, pointing back at the source row.
>
> The line of code this project exists to delete is `WHERE valid = true`. It doesn't error, the dashboard still
> renders, and four thousand rows are simply gone until a customer asks where their order went."

*Point at the coverage number.*

> "51,651 rows times 12 rules. Coverage is a hard gate, not a metric — every row is published, held, or
> explained. And because the faults were seeded, I can tell you recall is 100% and row precision 99.6%.
> That's not a number you can produce from real data, because nobody knows the true fault count."

## 3 · The honest baseline (1.5 minutes)

*Show the mapping comparison.*

> "Mapping each plant's columns to a canonical schema — `DELIVERY_DT`, `del_date`, `Delivery Date`.
>
> I wrote a dumb version first on purpose: string similarity and a rules table. Free, offline, works when the
> provider is down. Then the LLM, scored against the same answer key, on headers it had seen and headers it
> hadn't.
>
> Without that baseline, 'the model got 87%' means nothing — 87% against what? With it, the model's
> contribution is a measured lift. Top-1 is 100% on both, and the heuristic is still reported beside it."

*Then:*

> "And the model never applies a mapping. It proposes; an owner confirms; the confirmed mapping is bound to an
> exact header fingerprint. A mapping is a business decision with an audit trail, not a prediction — wrong
> mappings are the highest-cost silent failure in integration work."

## 4 · One definition, one truth (1.5 minutes)

*Show the metric YAML and the two generated views.*

> "The two BI tools weren't disagreeing about the data. They were disagreeing about the definition — each had
> its own formula in its own semantic layer, and neither was written down where a human could read it.
>
> One definition, versioned, compiled into the views both tools read. 114 period-by-period comparisons, zero
> delta, every one. And the reconciliation explains each tool's historical gap by cause, from that tool's own
> export."

## 5 · The gate that blocks a correct fix (1 minute) — **the moment**

*Open the blocked fix.*

> "This is my favourite thing in either project.
>
> The triage agent looks at a malformed customer code, `CUST-0004`, and proposes normalising it to `C000004`.
> That is the correct answer.
>
> The gate refuses it. The SOP permits automatic changes to units and date formats — nothing else. So the
> correct fix becomes a proposal for a person.
>
> Correct and permitted are different questions, and only one of them is the model's to answer. Sixteen of
> sixteen adversarial fixes blocked, and the allowlist can't be widened from the config file."

*Beat.*

> "That's the handover point. The numbers are trustworthy now. So — what do you do with them at six in the
> morning?"

---

# Part two — the command center (8 minutes)

## 6 · The problem the dashboard can't show you (1.5 minutes)

*Terminal, Project B:*

```
python scripts/run_detect.py --parquet --date 2025-09-15
```

> "One customer's lane at one plant: OTIF at 50%, against a normal of 87.5%.
>
> Here's the part that matters. On that same day, the company total — the number on the executive dashboard —
> never moves outside its own noise. It doesn't even register a finding.
>
> Three weeks of a customer being let down, invisible in the number everybody watches. There's a test asserting
> exactly that, because it's the failure this whole project answers."

*Point at the driver line.*

> "And that 40% isn't a phrase a model chose. It's a decomposition — how much of the plant's movement each lane
> accounts for, computed in code. The identity closes, which is what makes the percentage publishable."

## 7 · The brief, and what the model is allowed to see (2 minutes)

*Console → build the brief → expand "The numbers behind it".*

> "Every sentence with a number carries a reference. A validator resolves every one against the evidence pack
> before this page renders. A brief with a number that doesn't match its citation never ships.
>
> The model gets that pack and nothing else. Not the tables, not the history — it can't average anything
> because it never receives anything to average.
>
> And the numbers arrive pre-rounded. It's asked to copy '74.1%', not to turn 0.74138 into a percentage. A
> model doing that will sometimes write 74%, sometimes 74.14%, and occasionally 74.8% — and inside a confident
> paragraph you cannot tell which is which."

## 8 · Approve, and the pause that survives a restart (2 minutes)

*Edit a title, approve with edits.*

> "Three things just happened. The tracker got my words, not the drafter's. The policy gate re-checked the
> action at the moment of approval, because approving means 'do the thing policy permits', not 'do anything'.
> And a ticket exists."

```
psql $DATABASE_URL -c "select key, summary from ops.mock_jira order by created_at desc limit 3"
```

> "And between building the brief and that approval, the run was paused in Postgres. I could have restarted the
> process, or approved from a different machine tomorrow, and it would have resumed from exactly there.
>
> That's the only reason there's a graph in this project rather than plain code. There's a test that throws the
> whole process away between the pause and the approval — because a pause that only works inside one process
> isn't the property I'm claiming."

## 9 · What it refuses to do (2 minutes)

> "The interesting part of a system like this is what it won't do."

*Build the brief for `2026-07-04`.*

> "OTIF at 34.6%. Every naive detector calls that a crisis. This one says, in the first line, that it's a
> shutdown in the policy calendar — and caps it below incident level so nobody gets woken.
>
> That dip is seeded as a deliberate decoy. And there's a test that the same detectors with the holiday
> calendar emptied *do* raise it — otherwise the decoy proves nothing."

*Then the adversarial table in `evals/REPORT.md`.*

> "Four kinds of action nothing in this system can produce, tried against the gate anyway. All blocked before a
> person is asked, and recorded as violations. A drafter that can only produce allowed actions is one refactor
> away from producing a disallowed one."

## 10 · The measurements, including the misses (1.5 minutes)

*Both reports side by side.*

> "Project A: coverage 51,651 rows, exception recall 100%, drift 2 of 2 with no false alerts, retrieval 87%
> with zero leaks. And the agent at 82% against an 85% target — missed, and the report says which pattern it
> gets wrong.
>
> Project B: detection recall 100%, precision 80%, attribution 100%, the decoy never raised, citation coverage
> 100% across 652 numbers in twenty real briefs, at a fifth of a cent each.
>
> Two caveats both reports state themselves: precision counts every unexplained alert as wrong, and the
> generator promises nothing about the other 529 days — so 80% is a floor. And since I tuned thresholds on the
> first half of the history, the report scores the half I never touched separately. It comes out at 100%."

---

## 11 · Close (45 seconds)

> "My first replay in Project B scored 2% precision. Four things were wrong, and only one was the model's:
> thresholds I'd guessed — the 'high' line for OTIF sat at the median, so it fired on half of all days — rules
> written for a company applied to a single lane, no separation between 'statistics noticed' and 'policy says
> this matters', and a CUSUM that never reset after firing.
>
> One rule I deleted entirely: backlog. The real surge peaks at 2.8 standard deviations and ordinary swings
> reach 5.0, so no threshold separates them. I wrote down what would fix it instead of tuning until the answer
> key looked good.
>
> One foundation, two uses. Both repos are public, with the decision log — including the decisions I reversed."

---

## If they ask

**"Why LangGraph in B and not in A?"** A's pipeline is fixed — profile, map, validate, publish — and a function
call expresses that. B has a pause that must survive a restart. Smallest abstraction that fits, in both
directions.

**"How do you know the model isn't making numbers up?"** It cannot say a number that isn't a fact in the pack
and pass the validator. And the validator is tested with briefs built to get past it: the right citation with
the wrong number, an invented reference, a severity talked up.

**"What happens when the model is down?"** A templated brief ships, validated identically, and the fallback rate
is on the ops page — because a fallback rate nobody publishes quietly becomes 100%.

**"Isn't synthetic data a cop-out?"** The opposite. It's the only way "recall is 100%" means anything: I know
what was wrong before I started. Point either system at real data and the honest claim shrinks to "it found
some things".

**"What would you change before production?"** Real Jira with SSO, alert routing and on-call, detector drift
monitoring, a better backlog measure, and cost caps. In that order.

**"How long did this take?"** Ten weeks of evenings and weekends, two repositories, about 640 tests between
them.

---

## Recording notes

- **Rehearse section 5 and section 9.** The blocked-correct-fix and the holiday decoy are the two moments
  people remember; everything else can be slightly rough.
- **Do not narrate the architecture.** Show the failure, then the thing that catches it. The diagram earns
  thirty seconds at the start and never comes back.
- **Leave the misses in.** The 82% agent, the 2% first replay, the deleted rule. A demo where everything works
  invites the question you're avoiding.
- **One take per part** if you can. Part one and part two can be recorded separately and joined at the handover
  line ("that's the handover point") without anybody noticing.
