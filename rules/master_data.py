"""V004 known customer; V005 known item; V006 lot valid and unexpired; V009 UOM matches the item master."""

from __future__ import annotations

import pandas as pd

from rules.base import Rule, RuleContext, Violations


class V004KnownCustomer(Rule):
    id = "V004"
    name = "customer exists in the customer master"
    severity = "block"
    message = "Customer {customer_no} is not in the customer master"
    suggested_fix = "Correct the customer number at source, or have master data add the customer"

    def owner(self, ctx: RuleContext) -> str:
        return "master_data:customers"

    def check(self, df: pd.DataFrame, ctx: RuleContext) -> Violations:
        mask = df["customer_no"].notna() & ~df["customer_no"].isin(ctx.masters.customers)
        return Violations.where(df, mask, ["customer_no"])


class V005KnownItem(Rule):
    id = "V005"
    name = "item exists in the item master"
    severity = "block"
    message = "Item {item_no} is not in the item master"
    suggested_fix = "Correct the item number at source, or have master data add the item"

    def owner(self, ctx: RuleContext) -> str:
        return "master_data:items"

    def check(self, df: pd.DataFrame, ctx: RuleContext) -> Violations:
        mask = df["item_no"].notna() & ~df["item_no"].isin(ctx.masters.items)
        return Violations.where(df, mask, ["item_no"])


class V006LotNotExpired(Rule):
    """Checked only where the source sends lot_no. Rows from a header without a lot column are not checked —
    that coverage gap is reported by the eval, not hidden as a pass."""

    id = "V006"
    name = "lot known and not expired at issue"
    severity = "block"
    message = "Lot {lot_no}: {problem}"
    suggested_fix = "Hold the shipment record; quality to confirm the lot and expiry before it is reported"

    def owner(self, ctx: RuleContext) -> str:
        return "quality"

    def check(self, df: pd.DataFrame, ctx: RuleContext) -> Violations:
        if "lot_no" not in ctx.mapped:
            return Violations.none()
        expiry = df["lot_no"].map(ctx.masters.lots["expiry_date"])
        blank = df["lot_no"].isna()
        unknown = ~blank & expiry.isna()
        expired = ~blank & ~unknown & df["issue_date"].notna() & (df["issue_date"] > expiry)
        problem = pd.Series(None, index=df.index, dtype=object)
        problem[blank], problem[unknown], problem[expired] = (
            "lot number blank", "lot not in the lot master", "shipped after expiry")
        hit = problem.dropna().index
        return Violations(list(hit), [{
            "lot_no": None if blank[i] else df.at[i, "lot_no"], "problem": problem[i],
            "expiry_date": None if pd.isna(expiry[i]) else expiry[i].date().isoformat(),
            "issue_date": None if pd.isna(issued := df.at[i, "issue_date"]) else issued.date().isoformat(),
        } for i in hit])


class V009UomMatchesItemMaster(Rule):
    """Warn only. The suggested fix carries the magnitude evidence; nothing is changed here — an approved
    UOM normalisation is applied through the exception workflow (policy/autofix.yaml, week 6)."""

    id = "V009"
    name = "unit of measure matches the item master"
    severity = "warn"
    message = "UOM {uom} differs from item master basis {master_uom}"
    suggested_fix = ("Weights look recorded in {likely_unit}: convert to lb and set UOM to {master_uom}, "
                     "keeping the original")

    def check(self, df: pd.DataFrame, ctx: RuleContext) -> Violations:
        master_uom = df["item_no"].map(ctx.masters.item_uom)
        uom = df["uom"].str.strip().str.upper()
        mask = uom.notna() & master_uom.notna() & (uom != master_uom)
        rows, details = [], []
        for i in df.index[mask.fillna(False)]:
            lb_per_case = ctx.masters.item_lb_per_case.get(df.at[i, "item_no"], float("nan"))
            expected = df.at[i, "ordered_qty"] * lb_per_case
            ratio = df.at[i, "ordered_weight_lb"] / expected if expected and pd.notna(expected) else None
            likely = "kg" if ratio is not None and 0.35 < ratio < 0.6 else "an unknown unit"
            rows.append(i)
            details.append({"uom": df.at[i, "uom"], "master_uom": master_uom[i],
                            "item_no": df.at[i, "item_no"],
                            "weight_to_expected_lb_ratio": None if ratio is None else round(float(ratio), 3),
                            "likely_unit": likely})
        return Violations(rows, details)
