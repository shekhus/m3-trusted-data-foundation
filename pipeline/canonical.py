"""The canonical order-line schema every source is mapped onto (docs/plan.md §1.3).

Canonical names only. Physical Infor M3 field names are not asserted here or anywhere — confirm with an M3 SME
before mapping a real extract. `concepts` are the name ideas a heuristic looks for; each inner tuple is one
required idea and its interchangeable spellings (e.g. a shipped quantity is the invoiced quantity).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["order_id", "line", "customer", "item", "plant", "date", "qty", "weight", "uom", "lot"]


@dataclass(frozen=True)
class CanonicalField:
    name: str
    kind: Kind
    required: bool  # V001: must be mapped before a mapping can be confirmed
    description: str
    concepts: tuple[tuple[str, ...], ...]


ORDER_LINE: tuple[CanonicalField, ...] = (
    CanonicalField("order_no", "order_id", True, "Sales order number", (("order",), ("no",))),
    CanonicalField("line_no", "line", True, "Order line number within the order", (("line",),)),
    CanonicalField("customer_no", "customer", True, "Customer number as in the customer master (C+6 digits)",
                   (("customer",), ("no",))),
    CanonicalField("item_no", "item", True, "Item number as in the item master", (("item",), ("no",))),
    CanonicalField("plant", "plant", True, "Shipping plant code", (("plant",),)),
    CanonicalField("order_date", "date", True, "Date the order was entered", (("order",), ("date",))),
    CanonicalField("requested_date", "date", True, "Delivery date the customer asked for",
                   (("requested",), ("date",))),
    CanonicalField("confirmed_delivery_date", "date", True, "Delivery date the plant confirmed (promised)",
                   (("confirmed",), ("delivery",), ("date",))),
    CanonicalField("issue_date", "date", True, "Date goods were issued (shipped)",
                   (("issue", "ship"), ("date",))),
    CanonicalField("ordered_qty", "qty", True, "Ordered quantity in the order unit", (("order",), ("qty",))),
    CanonicalField("invoiced_qty", "qty", False, "Invoiced (shipped) quantity",
                   (("invoiced", "ship"), ("qty",))),
    CanonicalField("ordered_weight_lb", "weight", False, "Ordered weight in pounds",
                   (("order",), ("weight",))),
    CanonicalField("invoiced_weight_lb", "weight", False, "Invoiced (shipped) weight in pounds",
                   (("invoiced", "ship"), ("weight",))),
    CanonicalField("uom", "uom", False, "Order unit of measure code", (("uom",),)),
    CanonicalField("lot_no", "lot", False, "Production lot shipped", (("lot",), ("no",))),
)

BY_NAME: dict[str, CanonicalField] = {f.name: f for f in ORDER_LINE}
REQUIRED: frozenset[str] = frozenset(f.name for f in ORDER_LINE if f.required)

# Row transforms a mapping may request. Anything else fails the contract (plan §2.6: hallucinated output).
Transform = Literal[
    "trim", "parse_date_iso", "parse_date_us", "parse_date_dmy", "parse_excel_serial", "kg_to_lb",
    "normalize_customer_no",
]
