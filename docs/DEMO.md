# Demo script — M3 Trusted Data Foundation (3 minutes)

A recording script for the working system. Narration is in quotes; the actions are what to do on screen. Times
are cumulative. Project B (M3 Supply-Chain Command Center) is not built yet: it appears only as the closing
sentence, and the combined film waits until it exists.

**Before recording**

1. The production database should already hold the three plants, published to gold (the walkthrough in
   `docs/RUNBOOK.md` and the README quickstart produce this state). If it is empty, run the ingest and publish
   steps first: the demo is about behaviour, not about waiting for loads.
2. Open, in tabs: the portal, the API docs (`/docs`, authorised with the owner key), `evals/REPORT.md` on
   GitHub, and `docs/decisions.md`.
3. Set the portal to the owner key; have an analyst key ready for the permission moment.
4. Screen at 1080p or better; hide unrelated tabs and notifications.

---

## 0:00–0:20 — The problem

> "Two BI tools at a food processor report different on-time-in-full for the same week. Refreshes break
> silently when a plant changes an extract, and about a third of BI tickets are arguments about definitions
> rather than bugs. This is a governed path from plant file to published numbers. All the data is synthetic."

**Screen:** the portal's **Reconciliation** tab, showing the green grid.

## 0:20–0:50 — AI proposes, code validates, people confirm

**Screen:** **Mappings** tab. Open a plt02 header, point at `WT_ORD_KG → ordered_weight_lb` with `kg_to_lb`,
and at `cust_no → customer_no` with `normalize_customer_no`.

> "A plant's file has its own column names, units and date formats. An LLM proposes the mapping; the heuristic
> proposes too, and is the floor we measure it against. Nothing is applied by the model: every mapping is a
> version an owner has to confirm."

**Action:** switch the portal's key to the **analyst** key, press **Confirm**, and show the refusal.

> "The analyst can propose a correction. Only an owner confirms. That rule lives in the API, not the UI."

## 0:50–1:20 — Nothing is dropped, nothing is guessed

**Screen:** **Exceptions** tab, filtered to `V005`. Open one.

> "Validation is twelve deterministic rules in code, not prompts. A row that fails is never dropped and never
> silently fixed: it waits in the queue with a reason, a suggested fix and an owner. Held rows are counted by
> reason, so the numbers always add up."

**Screen:** **Batches** tab, showing a drift alert.

> "When Cedar Falls added a lot number column, the file's fingerprint changed. The alert names the affected gold
> column and the consumer views that read it, through lineage — and publishing that shape is refused until an
> owner confirms the new mapping. The old world found this out when Monday's dashboard came up blank."

## 1:20–1:50 — One definition, two views, and the tools explained

**Screen:** **Reconciliation** tab: the green grid, then the legacy gap table, then a tool container.

> "Metrics are defined once in YAML and compiled into the consumer views, so the two current views cannot drift
> apart: 114 of 114 comparisons agree. The legacy view is kept deliberately and its gap is reported as the
> 'before'. And each BI tool's gap is explained by named cause — this one measures on-time against the requested
> date instead of the confirmed date — worked out from the tool's own export, not from an answer key."

## 1:50–2:20 — The guardrail that blocks a correct fix

**Screen:** API docs. `POST /exceptions/{id}/agent-runs` on a V004 exception, then the response.

> "This is the only agentic component here, and it earns it: the next tool depends on what the last one
> returned. It looked up the master, found nothing, searched prior resolutions, and classified: needs master
> data, citing the resolutions it used. It stops before applying anything."

**Screen:** scroll to a run whose classification proposed a customer-number fix, or show the gate reasons from
`tests/test_policy_gate.py`.

> "And here is the part I would show first in an interview: when the agent proposes normalising CUST-0004 to
> C000004, it is *right* — and the policy gate rejects it anyway, because the SOP allows automatic changes only
> to units and date formats, never to a customer number. Sixteen of sixteen adversarial fixes are blocked. It
> becomes a proposal for a person."

## 2:20–2:45 — Answers with permissions, and refusals

**Screen:** **Knowledge** tab. Ask "How long do I have to resolve a blocking exception?" as an analyst.

> "The knowledge base answers from the company's own SOPs and memos, citing the excerpts it used — three days
> for blocking rules, from version 2 of the SOP. The superseded version 1 is not retrieved unless you ask about
> versions."

**Action:** ask about a customer's commercial terms as `plant_user` / PLT-01.

> "Ask for something above your level and the filter runs in SQL before ranking: the model never sees it, and
> the answer is that the information exists but is not available at this access level. Zero leaks across the
> golden set, against four when the filter is off."

## 2:45–3:00 — Evidence and what's next

**Screen:** `evals/REPORT.md`, scrolled to the summary table, then the failure analysis.

> "Every number here is generated by code on a database rebuilt from scratch, including the two evaluations
> that fail and why. The agent is at 82% against an 85% target, and the report says exactly which pattern is
> wrong and that the label it misses is my own. Next is the command center that consumes this gold — the same
> definitions, one foundation."

---

## If you have five minutes instead of three

Insert after 1:20:

- **Idempotency:** ingest the same file three times; row counts do not change.
- **The change request:** lot numbers became required in June 2026; 17,695 earlier lines are queued as "not
  covered" rather than failed, and 299 shipped after the date are blocked. One dictionary file and one rule; no
  report SQL touched.
- **Ops tab:** model spend, latency and error counts per purpose; the backlog by age; agent runs awaiting
  approval.

## Recording notes

- Say the number before showing it, so a viewer knows what to look at.
- Do not hide the failing rows. The report's failure analysis is the most credible part of the demo.
- Keep every claim to what the evidence supports: "51 of 53 answers good, zero leaks" beats "our RAG works".
- Total upload target: under 3 minutes for the main film, with the five-minute cut as a second take.
