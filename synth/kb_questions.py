"""
The golden question set.

Sixty questions across the categories the playbook requires (easy lookups,
cross-document synthesis, conflicting sources, stale information, questions the
system should refuse) plus the permission cases, each tagged with the RAG
challenge it exercises.

Every question carries:
    expected_doc_ids     documents that MUST appear in the retrieved set
    forbidden_doc_ids    documents that must NOT be used to answer (superseded,
                         expired, or above the asker's access level)
    must_contain         substrings the answer must include
    must_not_contain     substrings whose presence indicates a specific failure
    should_refuse        the correct behaviour is to decline, not to improvise
    asker                role and plant, so permission filtering is testable

Scoring retrieval against this set gives per-challenge recall rather than a
single opaque number: "retrieval is 87%" is useless, "retrieval is 95% except on
superseded-version questions where it is 40%" tells you what to fix.
"""

from __future__ import annotations

Q: list[dict] = []


def q(
    qid: str,
    question: str,
    *,
    category: str,
    challenge: str,
    project: str,
    expected: tuple[str, ...] = (),
    forbidden: tuple[str, ...] = (),
    must_contain: tuple[str, ...] = (),
    must_not_contain: tuple[str, ...] = (),
    should_refuse: bool = False,
    asker_role: str = "analyst",
    asker_plant: str | None = None,
    note: str = "",
) -> None:
    Q.append(
        {
            "question_id": qid,
            "question": question,
            "category": category,
            "challenge": challenge,
            "project": project,
            "expected_doc_ids": list(expected),
            "forbidden_doc_ids": list(forbidden),
            "must_contain": list(must_contain),
            "must_not_contain": list(must_not_contain),
            "should_refuse": should_refuse,
            "asker": {"role": asker_role, "plant": asker_plant},
            "note": note,
        }
    )


def build_questions() -> list[dict]:
    Q.clear()

    # =====================================================================
    # easy lookups — the floor. If these fail, nothing else matters.
    # =====================================================================
    q("Q001", "What is the standard yield for the PRIMALS product group?",
      category="easy_lookup", challenge="C7", project="both",
      expected=("SPEC-PG-001",), must_contain=("71.5",))

    q("Q002", "How many days of shelf life does CASE-READY product have?",
      category="easy_lookup", challenge="C7", project="both",
      expected=("SPEC-PG-001",), must_contain=("12",))

    q("Q003", "What date format does the Sheldon extract use?",
      category="easy_lookup", challenge="C1", project="project_a",
      expected=("SRC-PLT03-001",), must_contain=("Excel serial",))

    q("Q004", "At what remaining shelf life should inventory be flagged to the "
              "commercial team?",
      category="easy_lookup", challenge="C7", project="project_b",
      expected=("SOP-OPS-002",), must_contain=("3", "5"))

    q("Q005", "Which date is on-time performance measured against?",
      category="easy_lookup", challenge="C10", project="both",
      expected=("MD-001",), must_contain=("confirmed",),
      must_not_contain=("requested date is used",))

    q("Q006", "What is the response time for a Tier 3 escalation?",
      category="easy_lookup", challenge="C7", project="project_b",
      expected=("SOP-OPS-001",), must_contain=("1 hour",))

    q("Q007", "What is the minimum quantity for a shelf-life alert?",
      category="easy_lookup", challenge="C7", project="project_b",
      expected=("SOP-OPS-002",), must_contain=("2,000",))

    q("Q008", "How is customer number formatted in the Hastings extract?",
      category="easy_lookup", challenge="C2", project="project_a",
      expected=("SRC-PLT02-001",), must_contain=("without",))

    # =====================================================================
    # C1 — superseded versions
    # =====================================================================
    q("Q010", "How long do I have to resolve a blocking data quality exception?",
      category="conflicting_versions", challenge="C1", project="project_a",
      expected=("SOP-DQ-001-v2",), forbidden=("SOP-DQ-001-v1",),
      must_contain=("3",), must_not_contain=("10 business days",),
      note="v1 says 10 days, v2 says 3 for blocking rules. Answering 10 means "
           "the superseded version was retrieved and trusted.")

    q("Q011", "Who owns a data quality exception raised on a Hastings order line?",
      category="conflicting_versions", challenge="C1", project="project_a",
      expected=("SOP-DQ-001-v2",), forbidden=("SOP-DQ-001-v1",),
      must_contain=("plant",), must_not_contain=("central data team owns all",),
      note="v1 says central data team, v2 says the originating plant.")

    q("Q012", "What happens to an exception that is never resolved?",
      category="conflicting_versions", challenge="C1", project="project_a",
      expected=("SOP-DQ-001-v2",), forbidden=("SOP-DQ-001-v1",),
      must_contain=("never discarded",), must_not_contain=("written off",),
      note="v1 discards after 10 days; v2 forbids discarding. The v1 answer "
           "would reintroduce the reconciliation gap v2 exists to prevent.")

    q("Q013", "Is SOP-DQ-001 version 1.0 still in force?",
      category="conflicting_versions", challenge="C1", project="project_a",
      expected=("SOP-DQ-001-v2", "SOP-DQ-001-v1"),
      must_contain=("superseded",),
      note="Both documents are legitimately relevant here — the question is "
           "about the version relationship itself.")

    # =====================================================================
    # C2 — contradictory documents, resolved by recency
    # =====================================================================
    q("Q020", "What unit are the weight fields in the Hastings extract recorded in?",
      category="conflicting_sources", challenge="C2", project="project_a",
      expected=("MEMO-2025-11",), forbidden=(),
      must_contain=("kilogram",), must_not_contain=("in pounds, despite",),
      note="SRC-PLT02-001 says pounds and is WRONG. MEMO-2025-11 corrects it "
           "and explicitly takes precedence. Retrieval must surface the memo; "
           "an answer of 'pounds' is the failure mode.")

    q("Q021", "Should I convert the Hastings shipped weight before loading it?",
      category="conflicting_sources", challenge="C2", project="project_a",
      expected=("MEMO-2025-11",),
      must_contain=("2.20462",))

    q("Q022", "The source note and a later memo disagree about Hastings weight "
              "units. Which one applies?",
      category="conflicting_sources", challenge="C2", project="project_a",
      expected=("MEMO-2025-11", "SRC-PLT02-001"),
      must_contain=("memo",),
      note="Correct behaviour is to surface the conflict and state which wins, "
           "not to silently pick one.")

    # =====================================================================
    # C3 — access control
    # =====================================================================
    q("Q030", "What is the on-time-in-full commitment for Northgate Markets?",
      category="permission", challenge="C3", project="project_b",
      expected=("SLA-C000031",), must_contain=("96",),
      asker_role="leadership",
      note="Leadership may see restricted commercial terms.")

    q("Q031", "What is the on-time-in-full commitment for Northgate Markets?",
      category="permission", challenge="C3", project="project_b",
      forbidden=("SLA-C000031",), should_refuse=True,
      must_not_contain=("96",),
      asker_role="plant_user", asker_plant="PLT-01",
      note="Same question, unauthorised asker. The SLA is RESTRICTED. Correct "
           "response: the information exists but is not available at this "
           "access level. Leaking the number here is the worst failure in the "
           "whole set.")

    q("Q032", "Tell me about the Hastings facility's production lines.",
      category="permission", challenge="C3", project="project_b",
      forbidden=("PLANT-PLT-02",), should_refuse=True,
      asker_role="plant_user", asker_plant="PLT-03",
      note="Plant-scoped document, asker belongs to a different facility.")

    q("Q033", "Tell me about the Hastings facility's production lines.",
      category="permission", challenge="C3", project="project_b",
      expected=("PLANT-PLT-02",), must_contain=("L1", "L2"),
      asker_role="plant_user", asker_plant="PLT-02",
      note="Same question, correct facility. Must succeed — over-restriction "
           "is also a failure.")

    q("Q034", "What are our data classification levels?",
      category="permission", challenge="C3", project="both",
      forbidden=("POL-ACC-001",), should_refuse=True,
      asker_role="plant_user", asker_plant="PLT-01",
      note="The access policy is itself restricted.")

    q("Q035", "Which rebate terms apply to our strategic retail account?",
      category="permission", challenge="C3", project="project_b",
      forbidden=("SLA-C000031",), should_refuse=True,
      asker_role="analyst",
      note="An analyst is not a data owner. Commercial terms stay closed.")

    # =====================================================================
    # C4 — stale content
    # =====================================================================
    q("Q040", "Should weight variance exceptions be suppressed for Hastings?",
      category="stale_information", challenge="C4", project="project_a",
      expected=("MEMO-2025-07",), must_contain=("expired",),
      must_not_contain=("suppress weight variance exceptions for Hastings",),
      note="The suppression was real but ended 2025-09-30. Applying it today "
           "would hide genuine failures.")

    q("Q041", "How were Hastings weights captured in August 2025?",
      category="stale_information", challenge="C4", project="project_a",
      expected=("MEMO-2025-07",), must_contain=("manual",),
      note="Historical question — the expired memo IS the right source here. "
           "Expired does not mean useless; it means scoped to a past period.")

    # =====================================================================
    # C5 — unanswerable, must refuse
    # =====================================================================
    q("Q050", "What price do we charge Northgate Markets per pound of ground beef?",
      category="refusal", challenge="C5", project="project_b",
      should_refuse=True, asker_role="leadership",
      note="Pricing appears nowhere in the corpus. Even an authorised asker "
           "must get a refusal, not an inferred number.")

    q("Q051", "How many people work at the Sheldon facility?",
      category="refusal", challenge="C5", project="project_b",
      should_refuse=True, asker_role="leadership",
      note="Headcount is not in the corpus. The plant profile is tempting and "
           "topically adjacent, which is exactly the trap.")

    q("Q052", "What is our forecast accuracy target for next quarter?",
      category="refusal", challenge="C5", project="both",
      should_refuse=True, asker_role="leadership")

    q("Q053", "Which carrier do we use for Northgate deliveries?",
      category="refusal", challenge="C5", project="project_b",
      should_refuse=True, asker_role="leadership")

    q("Q054", "What was the OTIF rate last Tuesday?",
      category="refusal", challenge="C5", project="project_b",
      should_refuse=True, asker_role="leadership",
      note="A metric VALUE, not a definition. The knowledge base holds "
           "definitions and policy; numbers come from the metric layer. The "
           "system must not invent one or read one out of a policy example.")

    # =====================================================================
    # C6 — terminology mismatch
    # =====================================================================
    q("Q060", "What is OTIF?",
      category="terminology", challenge="C6", project="both",
      expected=("MD-001",), must_contain=("on time", "in full"),
      note="The documents spell it out as 'on time in full'. A purely lexical "
           "match on the acronym may miss the definition entirely.")

    q("Q061", "How do we handle random weight products?",
      category="terminology", challenge="C6", project="both",
      expected=("SPEC-PG-001",), must_contain=("catch",),
      note="'Random weight' and 'variable weight' are industry synonyms for "
           "catch weight. SPEC-PG-001 names all three; retrieval must bridge.")

    q("Q062", "Which items are sold by variable weight?",
      category="terminology", challenge="C6", project="both",
      expected=("SPEC-PG-001",), must_contain=("catch",))

    q("Q063", "What is the fill rate definition on a poundage basis?",
      category="terminology", challenge="C6", project="both",
      expected=("MD-001",), must_contain=("weight",),
      note="'Poundage' never appears in the corpus; 'weight fill rate' does.")

    # =====================================================================
    # C8 — near-duplicates
    # =====================================================================
    q("Q070", "Which facility runs the offal processing line?",
      category="near_duplicate", challenge="C8", project="project_b",
      expected=("PLANT-PLT-03",), forbidden=("PLANT-PLT-01", "PLANT-PLT-02"),
      must_contain=("Sheldon",), asker_role="leadership",
      note="The three plant profiles share heavy boilerplate. Only the "
           "facility-specific note distinguishes them. Returning all three is "
           "a retrieval failure even though one of them is correct.")

    q("Q071", "Which facility ships the strategic retail volume?",
      category="near_duplicate", challenge="C8", project="project_b",
      expected=("PLANT-PLT-02",), forbidden=("PLANT-PLT-01", "PLANT-PLT-03"),
      must_contain=("Hastings",), asker_role="leadership")

    q("Q072", "Which facility has the value-added line?",
      category="near_duplicate", challenge="C8", project="project_b",
      expected=("PLANT-PLT-01",), forbidden=("PLANT-PLT-02", "PLANT-PLT-03"),
      must_contain=("Cedar Falls",), asker_role="leadership")

    q("Q073", "Do the facilities despatch on Sundays?",
      category="near_duplicate", challenge="C8", project="project_b",
      expected=("PLANT-PLT-01",), must_contain=("No",), asker_role="leadership",
      note="This one is genuinely in the shared boilerplate, so ANY plant "
           "profile answers it. The system should not agonise; one is enough.")

    # =====================================================================
    # C9 — cross-document synthesis
    # =====================================================================
    q("Q080", "OTIF for Northgate Markets dropped to 91% for three straight days. "
              "What tier is this and who owns it?",
      category="synthesis", challenge="C9", project="project_b",
      expected=("SOP-OPS-001", "SLA-C000031"),
      must_contain=("Tier 2", "Supply Chain Manager"),
      asker_role="leadership",
      note="Needs the escalation policy AND the SLA: the account is strategic, "
           "which forces Tier 2 independently of the three-day duration.")

    q("Q081", "Total on-time-in-full fell about 18 points over 2 to 6 July 2026. "
              "Should I escalate?",
      category="synthesis", challenge="C9", project="project_b",
      expected=("MEMO-2026-06", "SOP-OPS-001"),
      must_contain=("shutdown",), must_not_contain=("Tier 3",),
      asker_role="leadership",
      note="THE decoy question. The shutdown notice plus the escalation "
           "policy's 'expected variation is not a breach' clause make this a "
           "no-escalate. This is the RAG counterpart to the A5 anomaly decoy: "
           "the detector must down-weight it AND the brief must explain why.")

    q("Q082", "A Cedar Falls line from March 2026 has no lot number. Is that a "
              "compliance failure?",
      category="synthesis", challenge="C9", project="project_a",
      expected=("SRC-PLT01-001",),
      must_contain=("not covered",), must_not_contain=("compliance finding",),
      note="Cedar Falls supplied no lot numbers before June 2026, so this is a "
           "coverage gap, not a failure. Reporting it as a breach is the error.")

    q("Q083", "A lot shipped after its expiry date. What do I do?",
      category="synthesis", challenge="C9", project="both",
      expected=("SOP-OPS-002",),
      must_contain=("quality",), must_not_contain=("correct the record",),
      note="Never a data correction — a compliance finding.")

    q("Q084", "Yield variance on one line has been outside tolerance for four "
              "production days. What happens?",
      category="synthesis", challenge="C9", project="project_b",
      expected=("SPEC-PG-001",), must_contain=("engineering",),
      note="Three or more days triggers an engineering investigation.")

    q("Q085", "Ground beef yield came in at 76.8% against a standard of 78%. "
              "Is that reportable?",
      category="synthesis", challenge="C9", project="project_b",
      expected=("SPEC-PG-001",), must_contain=("tolerance",),
      note="1.2 points is inside the plus-or-minus 1.5 point tolerance for "
           "GROUND, so no. Requires reading the table AND doing the comparison.")

    q("Q086", "Can I auto-correct a customer number that fails the master lookup?",
      category="synthesis", challenge="C9", project="project_a",
      expected=("SOP-DQ-001-v2",), forbidden=("SOP-DQ-001-v1",),
      must_contain=("No",),
      note="v2 permits auto-fix ONLY for UOM and date normalisation, and "
           "explicitly forbids changing a customer number.")

    q("Q087", "A consignment order was excluded from a delivery report. Is that "
              "correct?",
      category="synthesis", challenge="C9", project="both",
      expected=("MD-001",), must_contain=("not sanctioned",),
      note="MD-001 section 6 says there are no scope exclusions and that the "
           "undocumented consignment filter is not sanctioned. This is the "
           "knowledge-base counterpart to tool 2's scope_filter gap.")

    # =====================================================================
    # C10 — long document, answer in one section
    # =====================================================================
    q("Q090", "Are OTIF lines weighted by value?",
      category="long_document", challenge="C10", project="both",
      expected=("MD-001",), must_contain=("not weighted",),
      note="One sentence in section 4 of a long document.")

    q("Q091", "What changed between version 2 and version 3 of the delivery "
              "metric definitions?",
      category="long_document", challenge="C10", project="both",
      expected=("MD-001",), must_contain=("catch", "weight"),
      note="Answer is in the version history at the very end of a long doc — "
           "the section most likely to be truncated by naive chunking.")

    q("Q092", "Why is measuring catch-weight items on case count a problem?",
      category="long_document", challenge="C10", project="both",
      expected=("MD-001",), must_contain=("short",),
      note="Section 3. This is the same failure tool 1 exhibits in the "
           "reconciliation ground truth — the corpus explains what the data "
           "demonstrates.")

    q("Q093", "What are the two classes of issue that may be auto-corrected?",
      category="long_document", challenge="C10", project="project_a",
      expected=("SOP-DQ-001-v2",), forbidden=("SOP-DQ-001-v1",),
      must_contain=("date",))

    # =====================================================================
    # prior-resolution retrieval — Project A's agent corpus
    # =====================================================================
    q("Q100", "Item number fails the master lookup but the value looks correct. "
              "Has this been seen before?",
      category="prior_resolution", challenge="C6", project="project_a",
      must_contain=("trailing",),
      note="Should retrieve the trailing-whitespace resolutions for Sheldon.")

    q("Q101", "Customer number CUST-0044 is not in the master. What is this?",
      category="prior_resolution", challenge="C6", project="project_a",
      must_contain=("normalise",),
      note="A formatting difference, not a genuine unknown customer.")

    q("Q102", "Weights on this batch look about half what I'd expect. Known issue?",
      category="prior_resolution", challenge="C2", project="project_a",
      must_contain=("kilogram",),
      note="Should connect the symptom to the Hastings kg issue via prior "
           "resolutions AND the correction memo.")

    q("Q103", "Invoiced weight is 12% over the ordered weight on a catch-weight "
              "line. Do I fix it?",
      category="prior_resolution", challenge="C9", project="project_a",
      must_contain=("commercial",),
      note="Escalate, do not correct. The customer is invoiced on actual weight.")

    q("Q104", "A negative quantity appeared on an order line. What is the "
              "standing guidance?",
      category="prior_resolution", challenge="C5", project="project_a",
      must_contain=("finance",))

    return list(Q)
