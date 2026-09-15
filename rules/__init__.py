"""Rule registry. Order is the order exceptions are reported in; every rule runs on every row regardless."""

from __future__ import annotations

from rules.base import Masters, Rule, RuleContext, Violations
from rules.completeness import V001RequiredFields, V010ValuesParsed, V012RequiredFromDate
from rules.dates_quantities import (
    V002DateOrder,
    V003InvoicedWeightTolerance,
    V007NonNegativeQuantities,
    V011OrderedWhenInvoiced,
)
from rules.duplicates import V008DuplicateLine
from rules.master_data import V004KnownCustomer, V005KnownItem, V006LotNotExpired, V009UomMatchesItemMaster

RULES: tuple[Rule, ...] = (
    V001RequiredFields(), V002DateOrder(), V003InvoicedWeightTolerance(), V004KnownCustomer(),
    V005KnownItem(), V006LotNotExpired(), V007NonNegativeQuantities(), V008DuplicateLine(),
    V009UomMatchesItemMaster(), V010ValuesParsed(), V011OrderedWhenInvoiced(), V012RequiredFromDate(),
)
BY_ID: dict[str, Rule] = {r.id: r for r in RULES}

__all__ = ["BY_ID", "RULES", "Masters", "Rule", "RuleContext", "Violations"]
