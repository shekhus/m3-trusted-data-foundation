"""V002 date order; V003 invoiced weight tolerance; V007 non-negative quantities; V011 ordered vs invoiced."""

from __future__ import annotations

from typing import ClassVar

import pandas as pd

from rules.base import Rule, RuleContext, Violations

WEIGHT_TOLERANCE = 1.05
CONFIRM_BEFORE_REQUEST_DAYS = 30


class V002DateOrder(Rule):
    id = "V002"
    name = "dates in a possible order"
    severity = "block"
    message = ("Issue date before order date, or confirmed delivery more than 30 days before the "
               "requested date")
    suggested_fix = "Check the dates with the plant; a reversed issue date usually means a keying error"

    def check(self, df: pd.DataFrame, ctx: RuleContext) -> Violations:
        issue_before_order = df["issue_date"] < df["order_date"]
        early_confirm = df["confirmed_delivery_date"] < (
            df["requested_date"] - pd.Timedelta(days=CONFIRM_BEFORE_REQUEST_DAYS))
        return Violations.where(df, issue_before_order | early_confirm,
                                ["order_date", "requested_date", "confirmed_delivery_date", "issue_date"])


class V003InvoicedWeightTolerance(Rule):
    id = "V003"
    name = "invoiced weight within tolerance"
    severity = "block"
    message = "Invoiced weight outside [0, 105% of ordered weight]"
    suggested_fix = "Confirm the invoiced weight against the scale ticket; do not invoice above tolerance"
    columns: ClassVar[list[str]] = ["ordered_weight_lb", "invoiced_weight_lb"]

    def check(self, df: pd.DataFrame, ctx: RuleContext) -> Violations:
        invoiced, ordered = df["invoiced_weight_lb"], df["ordered_weight_lb"]
        mask = (invoiced < 0) | (invoiced > ordered * WEIGHT_TOLERANCE)  # NULL on either side compares False
        return Violations.where(df, mask, self.columns)


class V007NonNegativeQuantities(Rule):
    id = "V007"
    name = "quantities not negative"
    severity = "block"
    message = "Negative ordered or invoiced quantity"
    suggested_fix = "Returns and credits are not order lines; ask the plant to correct or reclassify the line"

    def check(self, df: pd.DataFrame, ctx: RuleContext) -> Violations:
        return Violations.where(df, (df["ordered_qty"] < 0) | (df["invoiced_qty"] < 0),
                                ["ordered_qty", "invoiced_qty"])


class V011OrderedWhenInvoiced(Rule):
    id = "V011"
    name = "ordered quantity positive when invoiced"
    severity = "warn"
    message = "Invoiced quantity with no positive ordered quantity"
    suggested_fix = "Check whether the order line was changed after shipping"

    def check(self, df: pd.DataFrame, ctx: RuleContext) -> Violations:
        return Violations.where(df, (df["invoiced_qty"] > 0) & (df["ordered_qty"] <= 0),
                                ["ordered_qty", "invoiced_qty"])
