"""V008 duplicate (order_no, line_no) within a batch: the first occurrence stays, later ones are queued."""

from __future__ import annotations

import pandas as pd

from rules.base import Rule, RuleContext, Violations


class V008DuplicateLine(Rule):
    id = "V008"
    name = "order line unique within the batch"
    severity = "block"
    message = "Order line {order_no}|{line_no} already appears in this batch at row {first_row}"
    suggested_fix = "Discard the repeated line after confirming it is identical to the first"

    def check(self, df: pd.DataFrame, ctx: RuleContext) -> Violations:
        keyed = df[df["order_no"].notna() & df["line_no"].notna()]
        repeated = keyed[keyed.duplicated(subset=["order_no", "line_no"], keep="first")]
        first = keyed.drop_duplicates(subset=["order_no", "line_no"], keep="first").set_index(
            ["order_no", "line_no"])["source_row"]
        return Violations(list(repeated.index), [
            {"order_no": r.order_no, "line_no": int(r.line_no),
             "first_row": int(first[(r.order_no, r.line_no)])}
            for r in repeated.itertuples()])
