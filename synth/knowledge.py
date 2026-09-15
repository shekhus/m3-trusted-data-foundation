"""
The knowledge corpus — and the RAG challenges engineered into it.

Both projects need retrieval, for different reasons:

  Project A  the exception-resolution agent searches prior resolutions, source
             system notes, and the data-quality SOPs to work out what a failing
             row actually means and whether anyone has seen it before.

  Project B  the morning brief looks up escalation policy, customer service
             agreements, and product tolerances so it can say who to tell and
             how fast — and so it can recognise that a dip was an announced
             shutdown rather than an incident.

A corpus of clean, consistent documents teaches nothing. Ten challenges are
engineered in, each with golden questions that can only be answered correctly if
the challenge is handled:

  C1  superseded versions      v1 and v2 of the same SOP both sit in the corpus
  C2  contradictory documents  an old source note is wrong; a later memo corrects it
  C3  access control           restricted documents, and plant-scoped documents
  C4  stale content            a temporary process that expired
  C5  unanswerable questions   the system must refuse, not improvise
  C6  terminology mismatch     query says "OTIF", the document says "on-time in-full"
  C7  tables                   answers live inside tables that must not be split
  C8  near-duplicates          three plant profiles sharing boilerplate
  C9  cross-document synthesis two documents needed for one answer
  C10 long documents           the answer is one section of a long policy

Every challenge is recorded in ground_truth/kb_questions.json against the
questions that exercise it, so retrieval quality is measured per challenge
rather than as a single opaque score.
"""

from __future__ import annotations

import textwrap
from datetime import date

import numpy as np

from . import config as C

# ---------------------------------------------------------------------------
# document helpers
# ---------------------------------------------------------------------------

DOCS: list[dict] = []


def doc(
    doc_id: str,
    title: str,
    body: str,
    *,
    audience: str,              # "project_a" | "project_b" | "both"
    doc_type: str,
    version: str = "1.0",
    effective: str = "2025-03-01",
    status: str = "current",    # current | superseded | expired
    access: str = "all",        # all | restricted | plant
    plant: str | None = None,
    supersedes: str | None = None,
    superseded_by: str | None = None,
    challenges: tuple[str, ...] = (),
) -> None:
    DOCS.append(
        {
            "doc_id": doc_id,
            "title": title,
            "doc_type": doc_type,
            "version": version,
            "effective_date": effective,
            "status": status,
            "access_level": access,
            "plant": plant,
            "supersedes": supersedes,
            "superseded_by": superseded_by,
            "audience": audience,
            "challenges": list(challenges),
            "body": textwrap.dedent(body).strip(),
        }
    )


def build_corpus() -> list[dict]:
    DOCS.clear()

    # =======================================================================
    # PROJECT A — data quality, source systems, prior resolutions
    # =======================================================================

    # --- C1: superseded version pair -------------------------------------
    doc(
        "SOP-DQ-001-v1", "Data Quality Exception Handling (SUPERSEDED)",
        """
        # Data Quality Exception Handling

        **Version 1.0 — effective 2025-03-01. SUPERSEDED by SOP-DQ-001-v2 on
        2026-01-15. Retained for audit history only. Do not follow this version.**

        ## Purpose
        How to handle records rejected by validation before they reach reporting.

        ## Exception ownership
        All exceptions are owned by the central data team. Plant teams are
        notified weekly by email.

        ## Resolution window
        Exceptions must be resolved within **10 business days**. Exceptions older
        than 10 days are written off and the records are discarded.

        ## Escalation
        Escalate to the data team lead after 10 days.
        """,
        audience="project_a", doc_type="sop", version="1.0",
        effective="2025-03-01", status="superseded",
        superseded_by="SOP-DQ-001-v2", challenges=("C1",),
    )

    doc(
        "SOP-DQ-001-v2", "Data Quality Exception Handling",
        """
        # Data Quality Exception Handling

        **Version 2.0 — effective 2026-01-15. Supersedes SOP-DQ-001 v1.0.**

        ## Purpose
        How to handle records rejected by validation before they reach reporting.

        ## Exception ownership
        Exceptions are owned by the **originating plant**, not the central data
        team. The central data team owns only cross-plant master data issues
        (unknown customer, unknown item).

        ## Resolution window
        Exceptions must be resolved within **3 business days** for blocking rules
        and 10 business days for warnings.

        **Records are never discarded.** An unresolved exception remains in the
        queue and is reported as excluded volume in the monthly data quality
        report. Discarding records was the practice under v1.0 and caused a
        reconciliation gap that took two months to trace.

        ## Escalation
        Blocking exceptions unresolved after 3 business days escalate to the
        plant operations manager, then to the Supply Chain Director after 5.

        ## Auto-fix policy
        Only two classes of issue may be corrected automatically, and both must
        be logged with the original value retained:

        - Unit-of-measure normalisation (kilograms to pounds)
        - Date-format normalisation

        Everything else requires a human decision. **No automated process may
        change a customer number, item number, quantity, or weight.**
        """,
        audience="project_a", doc_type="sop", version="2.0",
        effective="2026-01-15", status="current",
        supersedes="SOP-DQ-001-v1", challenges=("C1", "C10"),
    )

    # --- C2: wrong source note, corrected later by a memo -----------------
    doc(
        "SRC-PLT02-001", "Source System Notes: PLT-02 Order Extract",
        """
        # Source System Notes: PLT-02 Order Extract

        Author: data team. Last reviewed 2025-04-02.

        ## Extract contents
        Monthly CSV of order lines from the Hastings facility.

        ## Field notes
        - `ORDERNUM` / `ORDERLINE` form the natural key.
        - `DT_ORDER`, `DT_REQ`, `DT_CONFIRM`, `DT_SHIP` are dates in US format
          (MM/DD/YYYY).
        - `cust_no` is the customer number **without** the leading `C` and
          without zero padding.
        - `WT_ORD_KG` and `WT_SHIP_KG` are shipment weights **in pounds**,
          despite the column name. The `_KG` suffix is a legacy naming artefact
          from the original system and can be ignored.

        ## Known issues
        Around 1% of lines have no shipped weight recorded.
        """,
        audience="project_a", doc_type="source_note", version="1.0",
        effective="2025-04-02", status="current", challenges=("C2",),
    )

    doc(
        "MEMO-2025-11", "Correction: PLT-02 weights are in kilograms",
        """
        # Correction Notice: PLT-02 Weight Units

        Issued 2025-11-04 by the supply chain analytics team.

        ## Summary
        The source system note SRC-PLT02-001 states that `WT_ORD_KG` and
        `WT_SHIP_KG` from the Hastings extract are recorded in pounds and that
        the `_KG` suffix can be ignored. **This is incorrect.**

        Reconciliation against plant scale records confirms these fields are in
        **kilograms**. The suffix is accurate. Treating them as pounds
        understates Hastings shipped weight by a factor of approximately 2.2 and
        materially distorts any weight-based fill rate for that facility.

        ## Required action
        - Multiply by 2.20462 to convert to pounds during ingestion.
        - Record the conversion, retaining the original value.
        - SRC-PLT02-001 will be reissued. Until then, **this memo takes
          precedence** over the source note.
        """,
        audience="project_a", doc_type="memo", version="1.0",
        effective="2025-11-04", status="current", challenges=("C2",),
    )

    doc(
        "SRC-PLT03-001", "Source System Notes: PLT-03 Order Extract",
        """
        # Source System Notes: PLT-03 Order Extract

        Author: data team. Last reviewed 2025-06-18.

        ## Extract contents
        Monthly CSV of order lines from the Sheldon facility, exported from a
        spreadsheet-based reporting layer.

        ## Field notes
        - Date columns (`OrderDate`, `RequestDate`, `PromiseDate`, `ShipDate`)
          are **Excel serial numbers**, not formatted dates. Day zero is
          1899-12-30. A value of 46085 is 2026-03-04.
        - `Customer` is formatted `CUST-0123`: a fixed prefix and four digits.
          This is the same customer that PLT-01 reports as `C000123`.
        - `Item` values frequently carry **trailing whitespace** because of the
          spreadsheet export. Trim before matching to the item master.

        ## Known issues
        Item numbers with trailing spaces will not join to the item master
        unless trimmed. This has caused false "unknown item" exceptions.
        """,
        audience="project_a", doc_type="source_note", version="1.0",
        effective="2025-06-18", status="current", challenges=("C6",),
    )

    doc(
        "SRC-PLT01-001", "Source System Notes: PLT-01 Order Extract",
        """
        # Source System Notes: PLT-01 Order Extract

        Author: data team. Last reviewed 2026-06-05.

        ## Extract contents
        Monthly CSV of order lines from the Cedar Falls facility.

        ## Field notes
        - Dates are ISO format (YYYY-MM-DD).
        - `cust_no` is the full padded customer number, e.g. `C000123`.
        - Weights are in pounds.

        ## Change history
        **2026-06-01: a `lot_no` column was added to this extract.** Lot numbers
        were not available from Cedar Falls before this date. Any lot-based
        validation of Cedar Falls shipments is therefore only possible from June
        2026 onward. Historical lot compliance for this facility cannot be
        reconstructed from the extract and should be reported as **not covered**
        rather than as passing.
        """,
        audience="project_a", doc_type="source_note", version="1.1",
        effective="2026-06-05", status="current", challenges=("C9",),
    )

    # --- C3: restricted access policy -------------------------------------
    doc(
        "POL-ACC-001", "Data Access and Classification Policy",
        """
        # Data Access and Classification Policy

        **Classification: RESTRICTED. Access limited to data owners and the
        analytics leadership team.**

        ## Classification levels
        | Level | Examples | Who may access |
        |---|---|---|
        | Open | Metric definitions, SOPs, source notes | All staff |
        | Plant | Plant operating profiles, plant exception detail | That plant plus leadership |
        | Restricted | Customer agreements, commercial terms, this policy | Data owners, leadership |

        ## Rules
        - Plant-scoped material must never be shown to a user assigned to a
          different facility, including in aggregate answers that would let the
          figure be inferred.
        - Restricted material must not be quoted, summarised, or paraphrased to
          an unauthorised user. The correct response is that the information
          exists but is not available at their access level.
        - Access is evaluated at the time of the request, not at the time the
          document was indexed. A role change takes effect immediately.
        """,
        audience="both", doc_type="policy", version="1.0",
        effective="2025-03-01", status="current", access="restricted",
        challenges=("C3", "C7"),
    )

    # --- C4: expired temporary process ------------------------------------
    doc(
        "MEMO-2025-07", "Temporary manual weight capture at Hastings (EXPIRED)",
        """
        # Temporary Process: Manual Weight Capture at Hastings

        Issued 2025-07-14. **Expired 2025-09-30. No longer in force.**

        ## Background
        The automated scale interface at the Hastings facility was offline for
        scheduled replacement between 2025-07-14 and 2025-09-30.

        ## Temporary process
        During this window only, shipped weights were captured manually and
        entered at end of shift. Weight-based fill rate for Hastings should be
        treated as indicative for this period, and weight variance exceptions
        were suppressed.

        ## Expiry
        This process ended on 2025-09-30 when the scale interface was restored.
        Weight-based exceptions resumed from 2025-10-01. **Do not apply this
        suppression to any period after 2025-09-30.**
        """,
        audience="project_a", doc_type="memo", version="1.0",
        effective="2025-07-14", status="expired", challenges=("C4",),
    )

    # --- C10: long metric dictionary narrative -----------------------------
    doc(
        "MD-001", "Metric Definitions: Delivery Performance",
        """
        # Metric Definitions: Delivery Performance

        Version 3.0, effective 2026-09-01. Owner: supply chain analytics.
        This is the governed narrative companion to the machine-readable metric
        definitions. Where this document and a report disagree, this document is
        authoritative.

        ## 1. Scope
        Covers on-time performance, in-full performance, and the combined
        measure for all customer shipments across all facilities and all order
        types, including consignment.

        ## 2. On time
        A line is on time when the issue date is on or before the **confirmed
        delivery date**.

        The confirmed delivery date is the date the facility committed to after
        reviewing capacity. It is not the same as the requested date, which is
        what the customer originally asked for. Measuring against the requested
        date counts a legitimately renegotiated commitment as a failure and will
        understate performance. Version 1.0 of this document used the requested
        date; this was changed in version 2.0 following a review with commercial
        teams.

        ## 3. In full
        In full depends on whether the item is a catch-weight item.

        **Catch-weight items** are sold by actual weight, and the case count is
        only an approximation of what was delivered. For these items a line is in
        full when the invoiced weight is at least 98% of the ordered weight — a
        2% tolerance that reflects normal trim and yield variation.

        **Non-catch-weight items** are in full when the invoiced case count is at
        least the ordered case count, with no tolerance.

        Measuring catch-weight items on case count alone is the most common
        error in delivery reporting. A shipment can be complete on cases while
        being materially short on weight, and the customer is invoiced on weight.
        A report using the case basis for these items will show a better number
        than the customer's own experience.

        ## 4. On time in full
        A line is on time in full when it is both on time and in full. The rate
        is the count of qualifying lines divided by the count of all lines in the
        period. Lines are not weighted by value or volume.

        ## 5. Fill rate
        Fill rate is reported on two bases and both must be labelled:

        - **Case fill rate**: invoiced cases divided by ordered cases.
        - **Weight fill rate**: invoiced pounds divided by ordered pounds.

        For a catch-weight product group the weight basis is the one that
        reflects customer experience.

        ## 6. Scope exclusions
        There are none. Any report excluding an order type must state the
        exclusion on the face of the report. Consignment orders have
        historically been excluded by at least one reporting tool without
        documentation; this is not sanctioned.

        ## 7. Version history
        - 1.0 (2025-03-01) — requested date basis, case count for all items.
        - 2.0 (2025-10-01) — moved to confirmed date basis.
        - 3.0 (2026-09-01) — catch-weight items moved to the weight basis with a
          2% tolerance.
        """,
        audience="both", doc_type="metric_definition", version="3.0",
        effective="2026-09-01", status="current", challenges=("C6", "C10"),
    )

    # =======================================================================
    # PROJECT B — operations, escalation, SLAs, product specs
    # =======================================================================

    doc(
        "SOP-OPS-001", "Supply Chain Escalation Policy",
        """
        # Supply Chain Escalation Policy

        Version 2.1, effective 2026-02-01. Owner: Supply Chain Director.

        ## Tiers
        | Tier | Trigger | Owner | Response time |
        |---|---|---|---|
        | Tier 1 | Single-day metric breach, one facility | Plant operations lead | Same business day |
        | Tier 2 | Breach sustained three consecutive days, or any strategic account affected | Supply Chain Manager | 4 business hours |
        | Tier 3 | Breach affecting multiple facilities, or any food-safety or shelf-life risk | Supply Chain Director | 1 hour, and a written notice |

        ## Determining the tier
        Take the highest tier that applies. A single-day breach on a strategic
        account is Tier 2, not Tier 1, because the account condition applies
        independently of duration.

        ## Expected variation is not a breach
        A metric movement explained by a known and announced event — a holiday
        shutdown, a planned maintenance window, a published customer order
        pattern — is **not** an escalation trigger, regardless of size. Check
        published operational notices before raising. Escalating an announced
        shutdown is a false escalation and is tracked as such.

        ## Records
        Every Tier 2 and Tier 3 escalation requires a ticket with the metric,
        the period, the affected segment, and the evidence used.
        """,
        audience="project_b", doc_type="sop", version="2.1",
        effective="2026-02-01", status="current", challenges=("C7", "C9"),
    )

    doc(
        "SOP-OPS-002", "Cold Chain and Shelf Life Incident Response",
        """
        # Cold Chain and Shelf Life Incident Response

        Version 1.3, effective 2025-12-01.

        ## Shelf-life risk thresholds
        Inventory is at risk when remaining shelf life falls below five days.

        | Remaining shelf life | Action |
        |---|---|
        | 6 days or more | Normal rotation |
        | 3 to 5 days | Flag to the commercial team for priority allocation |
        | 1 to 2 days | Tier 3 escalation; divert or discount decision required |
        | Expired | Quarantine; do not ship; quality notification required |

        ## Ageing stock
        A lot that has not moved for more than ten days at a distribution centre
        is reported as ageing regardless of its remaining shelf life, because it
        indicates an allocation failure rather than a product problem.

        ## Quantity qualifier
        Shelf-life alerts are raised when the at-risk quantity exceeds 2,000
        pounds at a single location. Smaller quantities are managed through
        normal rotation and do not warrant an alert.

        ## Never ship expired product
        A lot whose expiry date precedes the shipment date must never be
        shipped. Any such record found in shipment history is a compliance
        finding and requires quality notification, not a data correction.
        """,
        audience="both", doc_type="sop", version="1.3",
        effective="2025-12-01", status="current", challenges=("C7", "C9"),
    )

    # --- C4 + decoy support: the holiday shutdown notice --------------------
    doc(
        "MEMO-2026-06", "Planned Independence Day shutdown, all facilities",
        """
        # Planned Shutdown Notice: Independence Day 2026

        Issued 2026-06-15 by supply chain operations.

        ## Shutdown window
        All three facilities will run a reduced schedule from **2 July 2026 to
        6 July 2026**, with no despatch on 3 and 4 July.

        ## Expected effect on reporting
        On-time performance will fall across all facilities for the affected
        period. Lines confirmed for delivery inside the window will largely ship
        on 6 or 7 July.

        A drop in on-time in-full of roughly 15 to 20 percentage points at total
        company level is **expected and normal** for this window. Per the
        escalation policy, an announced shutdown is not an escalation trigger.
        Reporting should annotate the period rather than raise an incident.

        ## Recovery
        Normal despatch resumes 7 July 2026. Backlog is expected to clear within
        three business days.
        """,
        audience="project_b", doc_type="memo", version="1.0",
        effective="2026-06-15", status="current", challenges=("C9",),
    )

    # --- C3: restricted customer agreements --------------------------------
    doc(
        "SLA-C000031", "Service Agreement: Northgate Markets (C000031)",
        """
        # Customer Service Agreement: Northgate Markets

        Customer number C000031. **Classification: RESTRICTED — commercial terms.**

        ## Account status
        Strategic account. Highest-volume retail customer by case volume.

        ## Service commitments
        | Measure | Commitment | Measurement |
        |---|---|---|
        | On time in full | 96.0% monthly | Confirmed date basis, all order types |
        | Weight fill rate | 98.0% monthly | Catch-weight items, pounds |
        | Order acknowledgement | 4 business hours | From receipt |

        ## Remedies
        Performance below 94.0% on-time-in-full in any calendar month triggers a
        service review within ten business days. Two consecutive months below
        94.0% permits the customer to request a rebate against the affected
        volume.

        ## Escalation contact
        Account escalations route to the Supply Chain Manager, not the plant.
        This account is designated strategic, so any breach is Tier 2 minimum.
        """,
        audience="project_b", doc_type="sla", version="1.0",
        effective="2025-03-01", status="current", access="restricted",
        challenges=("C3", "C7", "C9"),
    )

    doc(
        "SLA-C000002", "Service Agreement: Thornbury Retail Co (C000002)",
        """
        # Customer Service Agreement: Thornbury Retail Co

        Customer number C000002. **Classification: RESTRICTED — commercial terms.**

        ## Account status
        Standard retail account.

        ## Service commitments
        | Measure | Commitment | Measurement |
        |---|---|---|
        | On time in full | 93.0% monthly | Confirmed date basis |
        | Case fill rate | 97.0% monthly | Cases |

        ## Remedies
        No contractual rebate. Performance is reviewed quarterly.
        """,
        audience="project_b", doc_type="sla", version="1.0",
        effective="2025-03-01", status="current", access="restricted",
        challenges=("C3", "C8"),
    )

    # --- C7 + C6: product group specifications ------------------------------
    doc(
        "SPEC-PG-001", "Product Group Specifications and Yield Tolerances",
        """
        # Product Group Specifications and Yield Tolerances

        Version 2.0, effective 2026-01-01. Owner: operations engineering.

        ## Yield tolerance by product group
        Yield variance is the difference between actual yield and the standard
        yield for the product group, in percentage points. Variation within
        tolerance is normal process variation and is not reported.

        | Product group | Standard yield | Tolerance | Catch weight | Shelf life |
        |---|---|---|---|---|
        | GROUND | 78.0% | plus or minus 1.5 pts | Yes | 14 days |
        | PRIMALS | 71.5% | plus or minus 2.0 pts | Yes | 21 days |
        | CASE-READY | 84.5% | plus or minus 1.5 pts | Yes | 12 days |
        | VALUE-ADDED | 91.0% | plus or minus 1.0 pts | No | 45 days |
        | OFFAL | 64.0% | plus or minus 3.0 pts | Yes | 10 days |

        ## Catch weight
        Catch-weight product is sold by actual weight rather than by a fixed
        weight per case. For these groups, case count alone does not describe
        what the customer received.

        ## Sustained variance
        A yield variance outside tolerance on a single shift is investigated
        locally. Variance outside tolerance sustained across three or more
        production days on the same line indicates an equipment or specification
        problem and requires an engineering investigation.
        """,
        audience="both", doc_type="specification", version="2.0",
        effective="2026-01-01", status="current", challenges=("C6", "C7"),
    )

    # --- C8: near-duplicate plant profiles ----------------------------------
    plant_detail = {
        "PLT-01": ("Cedar Falls", "Iowa", "three", "L1, L2 and L3",
                   "GROUND, PRIMALS and CASE-READY",
                   "Cedar Falls is the largest facility by volume and runs the "
                   "only value-added line in the network."),
        "PLT-02": ("Hastings", "Nebraska", "two", "L1 and L2",
                   "GROUND and PRIMALS",
                   "Hastings ships the majority of strategic retail volume, "
                   "including the Northgate Markets account, and has the "
                   "tightest despatch window in the network."),
        "PLT-03": ("Sheldon", "Minnesota", "two", "L1 and L2",
                   "PRIMALS and OFFAL",
                   "Sheldon runs the network's offal processing and exports a "
                   "spreadsheet-based order extract rather than a direct feed."),
    }
    for code, (town, state, n_lines, lines, groups, distinguishing) in plant_detail.items():
        doc(
            f"PLANT-{code}", f"Plant Operating Profile: {town} ({code})",
            f"""
            # Plant Operating Profile: {town}

            Facility code {code}. **Classification: PLANT — access limited to
            {code} staff and supply chain leadership.**

            ## Overview
            The {town} facility is located in {state} and operates {n_lines}
            production lines ({lines}). Primary product groups are {groups}.

            ## Operating pattern
            The facility operates a six-day despatch week, Monday to Saturday.
            No despatch takes place on Sunday. Production runs are planned
            weekly and confirmed against capacity before delivery dates are
            committed to customers.

            ## Reporting
            Order line data is extracted monthly and supplied to the central
            analytics team. Refer to the source system note for this facility
            for field-level detail and known extract issues.

            ## Facility-specific note
            {distinguishing}
            """,
            audience="project_b", doc_type="plant_profile", version="1.0",
            effective="2025-03-01", status="current", access="plant", plant=code,
            challenges=("C3", "C8"),
        )

    # =======================================================================
    # DISTRACTORS
    # =======================================================================
    # Real corpora are mostly documents that are topically adjacent and wrong
    # for any given question. Without them, top-k over a handful of documents
    # succeeds by having nowhere else to go, and retrieval quality is
    # unmeasurable. These are plausible, well-written, and never the answer.

    doc(
        "SOP-WH-001", "Warehouse Receiving and Put-Away",
        """
        # Warehouse Receiving and Put-Away

        Version 1.2, effective 2025-05-01.

        ## Receiving
        Inbound loads are checked against the advance notice for pallet count,
        temperature, and seal integrity. Discrepancies are recorded before the
        load is accepted.

        ## Temperature verification
        Chilled product must arrive at or below 4 degrees Celsius. Loads outside
        specification are quarantined pending a quality decision.

        ## Put-away
        Product is put away by lot, oldest first. Mixed-lot pallets are not
        permitted in primary pick locations.

        ## Records
        Receiving records are retained for three years.
        """,
        audience="project_b", doc_type="sop", version="1.2",
        effective="2025-05-01", status="current", challenges=("distractor",),
    )

    doc(
        "SOP-QA-001", "Quality Hold and Release",
        """
        # Quality Hold and Release

        Version 3.0, effective 2026-03-01.

        ## Placing a hold
        Any team member may place a hold. Held product is moved to the quarantine
        location and flagged in the inventory system within one hour.

        ## Release
        Only the quality manager may release a hold, and only with a documented
        disposition: release to sale, rework, divert, or destroy.

        ## Notification
        Holds affecting despatched product require customer notification within
        four hours.
        """,
        audience="project_b", doc_type="sop", version="3.0",
        effective="2026-03-01", status="current", challenges=("distractor",),
    )

    doc(
        "SOP-DQ-002", "Source Onboarding Checklist",
        """
        # Source Onboarding Checklist

        Version 1.1, effective 2025-08-01.

        ## Before connecting a new source
        1. Identify the system owner and the refresh schedule.
        2. Obtain a sample extract covering at least one full month.
        3. Profile the sample: types, null rates, distinct counts, key uniqueness.
        4. Agree the natural key with the source owner in writing.
        5. Confirm the date convention and the unit of measure for every
           numeric field. Do not infer either from the column name.
        6. Record the agreed mapping and have the data owner confirm it.

        ## After connecting
        Monitor the first three refreshes manually before scheduling.
        """,
        audience="project_a", doc_type="sop", version="1.1",
        effective="2025-08-01", status="current", challenges=("distractor",),
    )

    doc(
        "MEMO-2026-02", "Reporting calendar change for fiscal 2026",
        """
        # Reporting Calendar Change

        Issued 2026-02-10.

        ## Change
        Monthly reporting periods move from calendar month end to the nearest
        Saturday, effective with the March 2026 period.

        ## Effect
        Period comparisons spanning the change require care. Year-on-year
        comparisons for March through August 2026 will compare a four-week or
        five-week period against a calendar month.

        ## Action
        Reporting owners should annotate affected comparisons.
        """,
        audience="both", doc_type="memo", version="1.0",
        effective="2026-02-10", status="current", challenges=("distractor",),
    )

    doc(
        "SOP-TR-001", "Transport and Carrier Standards",
        """
        # Transport and Carrier Standards

        Version 2.0, effective 2025-09-01.

        ## Load planning
        Loads are planned to maximise cube while respecting weight limits and
        temperature zone separation.

        ## Temperature control
        Trailers are pre-cooled before loading. Continuous temperature recording
        is required for every chilled movement.

        ## Delivery windows
        Where a customer specifies a delivery window, the carrier is measured
        against arrival inside that window. Carrier performance is reviewed
        monthly and is tracked separately from facility despatch performance.
        """,
        audience="project_b", doc_type="sop", version="2.0",
        effective="2025-09-01", status="current", challenges=("distractor",),
    )

    doc(
        "SPEC-LBL-001", "Labelling and Country of Origin Requirements",
        """
        # Labelling and Country of Origin Requirements

        Version 1.4, effective 2025-11-01.

        ## Required elements
        Every case label carries the item number, description, net weight, lot
        number, production date, and use-by date.

        ## Net weight
        For catch-weight cases the net weight printed on the label is the actual
        scaled weight of that case, not a nominal weight. Labels are applied
        after weighing.

        ## Lot format
        Lot numbers follow a facility prefix, the production date, and a
        sequence number.
        """,
        audience="both", doc_type="specification", version="1.4",
        effective="2025-11-01", status="current", challenges=("distractor",),
    )

    doc(
        "MEMO-2025-05", "New item setup lead times",
        """
        # New Item Setup Lead Times

        Issued 2025-05-20.

        ## Lead time
        New item master records require five business days from request to
        availability. Requests submitted without a product group, standard
        yield, and shelf life are returned unprocessed.

        ## Effect on orders
        An order cannot be entered against an item that does not yet exist in
        the master. Commercial teams should confirm item availability before
        committing a delivery date.
        """,
        audience="project_a", doc_type="memo", version="1.0",
        effective="2025-05-20", status="current", challenges=("distractor",),
    )

    doc(
        "SOP-OPS-003", "Weekly Supply Review Meeting",
        """
        # Weekly Supply Review Meeting

        Version 1.0, effective 2025-04-01.

        ## Purpose
        A standing weekly review of demand, supply, and service across the
        network.

        ## Attendees
        Supply Chain Manager (chair), plant operations leads, commercial
        representative, analytics representative.

        ## Standing agenda
        1. Service performance for the prior week
        2. Open escalations and their status
        3. Forward demand signal and capacity
        4. Inventory position and ageing stock
        5. Actions and owners

        ## Outputs
        Actions are recorded with an owner and a due date.
        """,
        audience="project_b", doc_type="sop", version="1.0",
        effective="2025-04-01", status="current", challenges=("distractor",),
    )

    return list(DOCS)


# ---------------------------------------------------------------------------
# prior exception resolutions — the retrieval corpus for the A13 agent
# ---------------------------------------------------------------------------

RESOLUTION_TEMPLATES: dict[str, list[tuple[str, str, str]]] = {
    # rule_id -> [(pattern, classification, resolution)]
    "V004_unknown_customer": [
        ("Customer number present in the extract but absent from the master, "
         "value in the CUST-#### form",
         "auto_fixable",
         "Sheldon formats customer numbers as CUST-0123. Normalise to the padded "
         "C000123 form before the master lookup. Not a real unknown customer."),
        ("Customer number present but unpadded, three or four digits",
         "auto_fixable",
         "Hastings exports the customer number without the leading C and without "
         "zero padding. Pad to six digits and prefix with C."),
        ("Customer number in the C9xxxxx range, no match after normalisation",
         "needs_master_data",
         "Genuinely absent from the customer master. Raised with commercial; the "
         "account had not been created in the master at the time of the order."),
    ],
    "V005_unknown_item": [
        ("Item number fails master lookup but matches after trimming whitespace",
         "auto_fixable",
         "Sheldon's spreadsheet export leaves trailing spaces on item numbers. "
         "Trim before matching. This is the single most common false exception."),
        ("Item number in the IT9xxxx range with no master match",
         "needs_master_data",
         "Item was never created in the item master. Requires a master data "
         "request; the line cannot be validated until then."),
    ],
    "V009_uom_mismatch": [
        ("Weight values roughly 2.2 times smaller than comparable lines, unit "
         "field says LB",
         "source_defect",
         "Hastings records weights in kilograms despite the unit field. See "
         "MEMO-2025-11, which supersedes the original source note. Convert with "
         "2.20462 and retain the original value."),
    ],
    "V003_weight_over_tol": [
        ("Invoiced weight exceeds ordered weight by more than 5% on a "
         "catch-weight item",
         "escalate",
         "Catch-weight overage beyond tolerance is a commercial issue, not a data "
         "issue. The customer is invoiced on actual weight. Route to the "
         "commercial team rather than correcting the record."),
    ],
    "V006_lot_after_expiry": [
        ("Shipment date later than the lot expiry date",
         "escalate",
         "Never treat as a data correction. Per SOP-OPS-002 this is a compliance "
         "finding requiring quality notification."),
        ("Lot number missing entirely for a Cedar Falls line before June 2026",
         "source_defect",
         "Cedar Falls did not supply lot numbers before 2026-06-01. Report as not "
         "covered rather than as a failure. No fix is possible from the extract."),
    ],
    "V002_date_reversal": [
        ("Ship date earlier than order date by a small number of days",
         "source_defect",
         "Usually a date-format misparse rather than a real reversal. Confirm the "
         "source's date convention first; Sheldon uses Excel serial numbers."),
    ],
    "V001_missing_required": [
        ("Confirmed delivery date blank on an otherwise complete line",
         "needs_master_data",
         "Order was never confirmed against capacity. Requires the plant planner "
         "to supply the commitment date; cannot be inferred."),
        ("Shipped weight blank, Hastings, mid-2025",
         "source_defect",
         "Hastings had a scale interface outage in Q3 2025. See MEMO-2025-07, "
         "now expired. For dates after 2025-09-30 a blank weight is a genuine "
         "capture failure and must be escalated, not suppressed."),
    ],
    "V008_duplicate_line": [
        ("Identical order and line number appearing twice in the same batch",
         "auto_fixable",
         "Keep the first occurrence and queue the remainder. Confirm the rows are "
         "genuinely identical before discarding."),
    ],
    "V007_negative_qty": [
        ("Negative ordered or invoiced quantity",
         "escalate",
         "Usually a credit or return posted against the original order line. "
         "Route to finance; do not delete or negate."),
    ],
}


def build_resolutions(rng: np.random.Generator) -> list[dict]:
    """~120 short prior-resolution records — the semantic search corpus."""
    out: list[dict] = []
    seq = 0
    for rule_id, templates in RESOLUTION_TEMPLATES.items():
        for pattern, classification, resolution in templates:
            # several historical instances per pattern, so retrieval has to
            # discriminate rather than memorise a single record
            for _ in range(int(rng.integers(4, 9))):
                seq += 1
                plant = str(rng.choice([p.code for p in C.PLANTS]))
                out.append(
                    {
                        "resolution_id": f"RES-{seq:04d}",
                        "rule_id": rule_id,
                        "plant": plant,
                        "pattern": pattern,
                        "classification": classification,
                        "resolution": resolution,
                        "resolved_by": str(rng.choice(
                            ["data team", "plant analyst", "commercial", "quality"]
                        )),
                        "resolved_on": str(date(2025, int(rng.integers(4, 13)),
                                                int(rng.integers(1, 28)))),
                    }
                )
    return out
