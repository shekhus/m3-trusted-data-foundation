# Prairie Bend Foods — Synthetic Dataset Generator

Generates the dataset behind two FDE portfolio projects:

- **Project A — M3 Trusted Data Foundation** consumes `data/sources/` (messy,
  per-plant extracts) and `data/report_feed/` (the second-opinion report).
- **Project B — M3 Supply-Chain Command Center** consumes `data/gold/` and
  `data/metrics/` in standalone mode, or Project A's output when integrated.

**Prairie Bend Foods is fictional.** Every row is produced by code in `synth/`.
No real company, customer, product, or operational metric appears anywhere.

The name is a single constant in `synth/config.py`, and `make name-check`
documents the release routine. One adjacency is known and accepted: an unrelated
direct-to-consumer farm business trades as "Prairie Foods". The names are
distinct, the businesses are different, and every artifact here states its
fictional status — but the check exists so the decision stays deliberate.
Table and column *shapes* are modelled on Infor M3 order, delivery, inventory
and production reporting; **no physical M3 field names are asserted** and any
mapping to real M3 columns must be confirmed with an M3 subject-matter expert.

## Why synthetic rather than a public dataset

Both projects are evaluated on questions only a generated dataset can answer:

| Evaluation needs | Requires |
|---|---|
| Mapping accuracy | the true source→canonical mapping |
| Exception recall and precision | every defect's row key and rule |
| Detection recall, precision, lead time | each anomaly's window, segment and **cause** |
| Attribution accuracy | which segment actually drove the change |
| Drift detection | when the schema changed and what broke |

A public dataset gives you rows. It does not give you a labelled answer key, so
you cannot honestly report recall on it. Here the generator plants the faults
and anomalies and writes what it planted to `ground_truth/`.

## Quick start

```bash
uv pip install -r pyproject.toml --extra dev   # from the repo root
make synth       # ~27s, writes ./data
make test        # 40 ground-truth integrity tests
make probe       # anomaly + fault calibration probes
python -m synth.probes.rag_probe   # needs scikit-learn; not a dependency until week 5
make name-check  # company-name collision checklist (run before publishing)
```

## The self-check gate

`make synth` recomputes every seeded anomaly at the grain it was planted at and
compares the anomaly window against a trailing baseline. If an expected effect
is absent or points the wrong way, **generation exits non-zero and writes no
ground truth**. A dataset whose answer key is wrong is worse than no dataset.

Current verified effects:

| ID | Metric | Baseline → window | Delta |
|---|---|---|---|
| A1 | `otif_rate`, PLT-02 › C000031 lane | 0.936 → 0.478 | −0.459 |
| A2 | `fill_rate_weight`, PLT-01 / CASE-READY | 0.998 → 0.908 | −0.090 |
| A3 | `yield_variance_pct`, PLT-03 L2 PRIMALS | +0.44 → −5.61 | −6.04 |
| A4 | `inventory_age_days`, DC-EAST | 3.61 → 9.58 | +5.97 |
| A5 | `otif_rate`, all plants (**decoy**) | 0.900 → 0.708 | −0.192 |
| A6 | `open_backlog_lines`, PLT-02 | 306 → 446 | +140 |

## Calibration — the dataset is tuned, not arbitrary

`calibration_probe.py` proves the difficulty is right before the real detectors
are written:

| Detector | Noise flags | A5 decoy at HIGH |
|---|---|---|
| naive z-score, 28-day pooled baseline | 33 | **2 — false positive** |
| day-of-week + holiday aware | 38 | **0** |
| aware + 2 consecutive days | 12 | 0 |
| aware + 3 consecutive days | 0 | 0 |

So: a naive detector cannot escape the decoy, a seasonality-aware one can, and
consecutive-day confirmation is what tames the noise. The A5 window sits
entirely inside the known-holiday blackout — enforced by a test — so a correct
detector is never unfairly penalised.

Two more calibrated lessons:

- **A2 is invisible on the count basis.** 14 flags on `fill_rate_weight`, zero
  on `fill_rate_count`. Catch-weight items must be measured by weight or the
  anomaly cannot be seen at all.
- **A1's attribution is unambiguous.** The seeded lane contributes −0.054 of the
  −0.067 plant-level delta; the next lane contributes −0.007. Top-1 attribution
  is correct and 8× clear of the runner-up.

## Honest recall ceilings

`fault_probe.py` runs a minimal reference pipeline: **96.9% recall, 99.6%
precision**. One rule is deliberately capped:

```
V006_lot_after_expiry   33/50 = 66%
  17 faults sit on PLT-01 rows shipped before 2026-06-01, when the source
  carried no lot_no column. Undetectable by design.
```

This is the point. You cannot validate what the source does not send, and a
pipeline claiming 100% on V006 is reporting a bug. `ground_truth/faults.json`
carries an `achievable_ceiling` block so recall is scored against what is
possible, not against what was planted.

## Coverage of the domain considerations

Every supply-chain consideration in `docs/plan.md` is represented:

| Consideration | How the dataset carries it |
|---|---|
| Catch-weight products | `catch_weight_flag`; in-full scored on weight (2% tol) vs count; anomaly A2; rule V003 |
| Yield variability | `production_yield` with `std_yield_pct` per product family; anomaly A3 |
| Perishable, dated inventory | `shelf_life_days`, `expiry_date`, `lb_at_risk_within_5d`; anomaly A4 |
| Multiple plants and warehouses | 3 plants, 2 DCs, each with its own date format, weight unit and customer-number convention |
| **Two BI tools** | **two independent exports that disagree — see below** |
| Lot traceability | `lot_master.csv`, `lot_no` on every line, rule V006 |

The three worked examples of a morning brief map directly
to seeded anomalies: *OTIF slipped on a key retail lane* → A1, *yield variance
outside tolerance* → A3, *cold-storage stock nearing shelf life* → A4.

## Two BI tools, two different OTIF numbers

The headline pain — "two reports, two OTIF numbers" — is modelled literally.
Both tools compute OTIF from the same shipments; neither matches the governed
definition, and they disagree with each other.

| | Governed | Tool 1 (legacy suite) | Tool 2 (dashboard) |
|---|---|---|---|
| On-time basis | confirmed date | confirmed date | **requested date** |
| Catch-weight in-full | **weight, 2% tol** | case count | case count |
| Case tolerance | none | none | **2%** |
| Scope | all order types | all order types | **excludes CONS** |
| Grain | order line | order line | month × plant × customer |

Overall gap versus governed: **tool 1 +0.64pp, tool 2 −15.97pp**.

`ground_truth/reconciliation.json` decomposes both gaps per month into four
named causes, so a pipeline is scored on *attribution*, not merely on noticing a
gap exists. Mean contributions: `date_basis` −0.167 (dominant for tool 2),
`weight_basis` +0.006, `case_tolerance` +0.006, `scope_filter` 10,147 lines
dropped.

The causes interact and do **not** sum to the total gap. Presenting them as an
additive decomposition is an error the ground truth deliberately catches.

### The sharpest lesson in the dataset

A2 is a catch-weight shortfall. A tool measuring in-full on case count is
structurally incapable of seeing it:

| Month | Governed OTIF | Tool 1 reports | Gap |
|---|---|---|---|
| Oct 2025 | 90.6% | 91.3% | +0.8pp |
| **Nov 2025 — A2 window** | **85.9%** | **90.4%** | **+4.5pp** |
| Dec 2025 | 89.2% | 89.4% | +0.2pp |

During a real service failure the legacy tool reports a *rosier* number than the
truth, and its gap widens six-fold. This is the same event Project B must detect
as an anomaly and Project A must explain as a definitional gap — one incident,
two projects, one root cause. A test (`test_legacy_tool_is_blind_to_the_A2_weight_anomaly`)
locks the property in place.

## Knowledge base — 26 documents, 53 golden questions, 10 engineered challenges

Both projects need retrieval: Project A's exception agent searches prior
resolutions and source notes; Project B's brief looks up escalation policy and
service commitments. `data/kb/` holds SOPs, source system notes, the metric
dictionary narrative, customer agreements, product specs, plant profiles and
memos — **8 of them distractors** that are plausible, well-written, and never
the right answer.

`rag_probe.py` establishes a TF-IDF lexical floor and reports which challenges
actually defeat a naive retriever:

| Challenge | Verdict | Evidence |
|---|---|---|
| **C3 access control** | **BITES HARD** | 4/4 unauthorised questions leaked a restricted document |
| **C5 refusal** | **BITES HARD** | top-k always returns something; unanswerable questions score 0.00–0.25, overlapping legitimate hits |
| **C1 superseded versions** | **BITES** | 3/3 retrieved the superseded SOP alongside the current one |
| **C9 synthesis** | **BITES** | both multi-document questions incomplete at k=6 — and the missing document is the one that changes the answer |
| **C8 near-duplicates** | **BITES** | 3/3, and dedup-by-type did not fix it |
| C2 contradictory docs | retrieval fine, **precedence** is the problem | both documents return; the memo must win |
| C4 stale content | retrieval fine, **scoping** is the problem | expired ≠ useless — right for its period, wrong for today |
| C6 terminology | does not bite at this size | TF-IDF 4/4. **Do not claim embeddings earned this** |
| C7 / C10 tables, long docs | does not bite at retrieval | chunking affects answer precision and token cost, not recall |

Five of ten defeat a naive retriever. The other five are real problems that live
in prompting, precedence and answer quality rather than retrieval recall. That
distinction is the point: *"our RAG scores 94%"* is noise, while *"recall is X
against a lexical floor of Y, permission leakage is zero, refusal rate is Z, and
near-duplicates are the class still failing"* is an engineer talking.

The golden set covers easy lookups, conflicting versions, contradictory sources,
permissions (in **matched pairs** — every denial has an authorised twin, so a
system that refuses everything scores badly), stale information, refusals,
terminology, near-duplicates, synthesis, and long documents.

### The decoy runs three layers deep

`MEMO-2026-06` announces the July 2026 shutdown and states that a 15–20 point
OTIF drop is expected. So anomaly A5 now has a full correct response: the
**detector** does not escalate it, **retrieval** finds the notice, and the
**brief explains it** — citing the memo and the clause in `SOP-OPS-001` saying an
announced event is not an escalation trigger. Question `Q081` scores it end to
end.

## Quirks versus faults

Kept strictly separate, because conflating them is the most common way to get
data-quality metrics wrong.

**Quirks** — ambient format differences. A correct pipeline normalises these
silently and logs the fix. Raising an exception for one is a false positive.

| Plant | Dates | Weight | Customer number | Other |
|---|---|---|---|---|
| PLT-01 | ISO `2026-03-04` | lb | `C000123` | gains `lot_no` on 2026-06-01 (D2) |
| PLT-02 | US `03/04/2026` | **kg** | `123` | ~1% missing weights; renames `cust_no` on 2026-03-01 (D1) |
| PLT-03 | Excel serial `46085` | lb | `CUST-0123` | trailing spaces on item numbers |

**Faults** — 580 real defects across 9 rules, each with its row key in
`ground_truth/faults.json`. Faults never land on rows carrying an anomaly
marker, so Project A and Project B scoring cannot corrupt each other (enforced
by a test).

## Output layout

```
data/
  sources/plt01|plt02|plt03/   54 monthly CSV extracts — Project A input
  report_feed/                 two independent BI tool exports that disagree
  master/                      customers, items, plants, warehouses, lot_master
  gold/                        fact_delivery, fact_inventory, fact_yield, dims
  metrics/                     7 daily_* series — Project B input
  kb/docs/                     26 knowledge base documents (markdown + frontmatter)
  kb/resolutions/              93 prior exception resolutions (agent search corpus)
  ground_truth/                mappings, faults, anomalies, drift, reconciliation,
                               kb_questions, kb_manifest, manifest
  DATASET.md                   generated data dictionary
```

Scale: ~51,600 order lines, ~26,200 inventory snapshots, ~6,200 yield runs,
50,600 lots, 18 months, 3 plants, 31 customers, 80 items. About 18 MB.

## Determinism

Same seed, byte-identical output — verified across independent runs on
`faults.json`, `anomalies.json` and the report feed. Each generation stage draws
from its own child RNG stream, so adding a stage later never shifts the others.

## Regenerating with different characteristics

Everything about the world lives in `synth/config.py`: window, plant quirks,
baseline rates, the anomaly list, the fault plan, drift events. Change the seed
for a fresh dataset with identical structure; change `ANOMALIES` or `FAULT_PLAN`
to alter the difficulty. The self-check will tell you if you have planted
something that is not actually there.
