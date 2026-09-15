"""V001 required fields present; V010 values parsed."""

from __future__ import annotations

import pandas as pd

from rules.base import Rule, RuleContext, Violations

REQUIRED = ["order_no", "line_no", "customer_no", "item_no", "plant", "order_date", "requested_date",
            "confirmed_delivery_date", "issue_date", "ordered_qty"]


class V001RequiredFields(Rule):
    id = "V001"
    name = "required fields present"
    severity = "block"
    message = "Required field(s) blank: {missing}"
    suggested_fix = "Ask the plant to resend the line with the missing field(s) filled"

    def check(self, df: pd.DataFrame, ctx: RuleContext) -> Violations:
        blank = df[REQUIRED].isna()
        for c in REQUIRED:  # a value that failed to parse is V010's, not a blank
            blank[c] &= ~df["parse_errors"].map(lambda errors, col=c: col in errors)
        hit = blank[blank.any(axis=1)]
        return Violations(list(hit.index), [{"missing": [c for c in REQUIRED if row[c]]}
                                            for _, row in hit.iterrows()])


class V010ValuesParsed(Rule):
    id = "V010"
    name = "values parse with the source's detected formats"
    severity = "block"
    message = "Value(s) did not parse: {unparsed}"
    suggested_fix = "Correct the value at source, or confirm a mapping with the right parser for this header"

    def check(self, df: pd.DataFrame, ctx: RuleContext) -> Violations:
        bad = df[df["parse_errors"].map(bool)]
        return Violations(list(bad.index), [{"unparsed": errors} for errors in bad["parse_errors"]])
