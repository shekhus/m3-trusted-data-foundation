# Discovery Brief — M3 Trusted Data Foundation

Format: FDE Playbook Appendix A. **The customer is fictional** and every number in this repo is synthetic.
Field-level mapping to Infor M3 physical columns is to be confirmed with an M3 SME.

| Field | Entry |
|---|---|
| **Customer** | Prairie Bend Foods (fictional): multi-plant North American protein processor, ~1,200 employees, Infor M3 ERP |
| **Users** | Supply-chain analysts (2), BI developer (1), plant operations leads (3), VP Supply Chain (consumer of reports) |
| **Current workflow** | M3 extracts feed two BI tools. Tool 1 (a legacy report suite) has hand-maintained SQL per report. Tool 2 (a newer dashboard tool) rebuilds metrics independently. A shared inbox receives "this report looks wrong" tickets; an analyst reconciles by hand in a spreadsheet. |
| **Pain point** | The two tools report different OTIF for the same week; refreshes break silently when an M3 extract changes; bad rows (missing weights, duplicate lines) skew totals. Roughly a third of BI tickets are data-definition disputes rather than genuine report bugs. *(Illustrative framing for the fictional customer; no real ticket data.)* |
| **Systems involved** | Infor M3 (orders, deliveries, items, customers, inventory), plant scale/WMS exports, two BI tools, a ticketing system |
| **Constraints** | Read-only access to M3; no changes to existing reports during pilot; plant data varies in format; catch-weight items measured by count and weight; audit trail required for any definition change |
| **Success metric** | Both BI tools show the same OTIF and fill rate for any period; zero silent refresh failures; every excluded row has a reason and an owner; a new source or field is absorbed without rewriting reports |

## Why "wants an AI agent" is not the problem statement

The problem is **two tools disagreeing and nobody knowing which is right**. AI helps in exactly two
places: proposing source-to-canonical mappings (a human confirms) and triaging exceptions (a human
approves). Everything that decides whether a number is correct is deterministic code — see the
justification test in `CLAUDE.md`.
