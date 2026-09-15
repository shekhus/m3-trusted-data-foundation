"""
The two BI tools, and the reconciliation ground truth.

This is the module that makes the central pain point real: the same shipments,
two tools, two different OTIF numbers, and neither matching the governed
definition. The reconciliation job's job is NOT to pick a winner — it is to
decompose the gap into named causes a business owner can rule on.

Four causes are engineered in, and each is independently attributable:

  weight_basis   both tools score catch-weight items on case count; the
                 governed definition scores them on weight with a 2% tolerance
  date_basis     tool 2 measures against the requested date, so a line the
                 plant legitimately re-promised is counted late
  case_tolerance tool 2 allows a 2% case shortfall; governed and tool 1 do not
  scope_filter   tool 2 silently drops consignment orders

`reconciliation.json` records each cause's contribution per month, so a
pipeline can be scored on whether it attributes the gap correctly rather than
merely noticing that a gap exists.
"""

from __future__ import annotations

from typing import cast

import numpy as np
import pandas as pd

from . import config as C

# ---------------------------------------------------------------------------
# the two tools' own opinions
# ---------------------------------------------------------------------------

def _tool_flags(df: pd.DataFrame, spec: dict) -> pd.DataFrame:
    """Apply one tool's definition and return its per-line verdict."""
    d = df.copy()

    date_col = spec["date_basis"]
    on_time = pd.to_datetime(d["issue_date"]) <= pd.to_datetime(d[date_col])

    tol = spec["case_tolerance"]
    in_full = d["invoiced_qty"] >= d["ordered_qty"] * (1 - tol)

    in_scope = ~d["order_type"].isin(spec["excluded_order_types"])

    d["_on_time"] = on_time.astype(int)
    d["_in_full"] = in_full.astype(int)
    d["_otif"] = (on_time & in_full).astype(int)
    d["_in_scope"] = in_scope.astype(int)
    return d


def build_tool1_feed(lines: pd.DataFrame, customers: pd.DataFrame,
                     items: pd.DataFrame) -> pd.DataFrame:
    """
    Legacy report suite export — the familiar 20-column delivery performance
    report. Line-level, count basis, all order types.
    """
    df = lines.merge(
        customers[["customer_no", "customer_name", "city"]], on="customer_no", how="left"
    ).merge(
        items[["item_no", "item_name", "product_group"]], on="item_no",
        how="left", suffixes=("", "_im")
    )
    df = _tool_flags(df, C.TOOL1)

    fill = np.where(df["ordered_qty"] > 0, df["invoiced_qty"] / df["ordered_qty"], np.nan)
    days_between = (
        pd.to_datetime(df["issue_date"]) - pd.to_datetime(df["requested_date"])
    ).dt.days

    feed = pd.DataFrame(
        {
            "Company Name": C.COMPANY_NAME,
            "Customer Number": df["customer_no"],
            "Customer Name": df["customer_name"],
            "City": df["city"],
            "Customer Order Number": df["order_no"],
            "Customer PO Number": df["customer_po"],
            "Customer Order Type": df["order_type"],
            "Order Line Number": df["line_no"],
            "Item Number": df["item_no"],
            "Item Name": df["item_name"],
            "Product Group": df["product_group"],
            "Confirmed Delivery Date": df["confirmed_delivery_date"],
            "Planning Date": df["requested_date"],
            "Issue Date": df["issue_date"],
            "Days Between Planning and Issue Date": days_between,
            "Ordered Qty": df["ordered_qty"],
            "Invoiced Qty": df["invoiced_qty"],
            "Case Fill Rate": np.round(fill, 4),
            "Case Fill Rate max. 100%": np.round(np.minimum(fill, 1.0), 4),
            "On Time": np.where(df["_on_time"] == 1, "Y", "N"),
        }
    )
    return feed[list(C.REPORT_FEED_COLUMNS)]


def build_tool2_feed(lines: pd.DataFrame, customers: pd.DataFrame) -> pd.DataFrame:
    """
    Dashboard tool export — a different shape entirely, as a second tool would
    be. Monthly aggregate by plant and customer, its own column names, its own
    rounding, and its own (undocumented) scope filter.
    """
    df = lines.merge(customers[["customer_no", "customer_name"]],
                     on="customer_no", how="left")
    df = _tool_flags(df, C.TOOL2)
    df = df[df["_in_scope"] == 1]

    df["Month"] = pd.to_datetime(df["issue_date"]).dt.strftime("%Y-%m")

    g = (
        df.groupby(["Month", "plant", "customer_no", "customer_name"])
        .agg(
            Lines=("order_no", "size"),
            OnTimeLines=("_on_time", "sum"),
            InFullLines=("_in_full", "sum"),
            OTIFLines=("_otif", "sum"),
            OrderedCases=("ordered_qty", "sum"),
            ShippedCases=("invoiced_qty", "sum"),
        )
        .reset_index()
    )
    g["OTIF_Pct"] = (100 * g["OTIFLines"] / g["Lines"]).round(1)
    g["OnTime_Pct"] = (100 * g["OnTimeLines"] / g["Lines"]).round(1)
    g["Fill_Pct"] = (100 * g["ShippedCases"] / g["OrderedCases"]).round(1)

    return g.rename(
        columns={
            "plant": "Facility",
            "customer_no": "CustomerID",
            "customer_name": "CustomerName",
        }
    )[
        ["Month", "Facility", "CustomerID", "CustomerName", "Lines",
         "OnTimeLines", "InFullLines", "OTIFLines", "OrderedCases",
         "ShippedCases", "OTIF_Pct", "OnTime_Pct", "Fill_Pct"]
    ]


# ---------------------------------------------------------------------------
# reconciliation ground truth
# ---------------------------------------------------------------------------

def build_reconciliation_truth(lines: pd.DataFrame,
                               gold_fact: pd.DataFrame) -> dict:
    """
    Decompose, per month, the gap between each tool's OTIF and the governed
    OTIF into the four engineered causes.

    Method: start from the governed verdict and switch one definitional choice
    at a time, measuring how many lines flip. This is exactly what the
    reconciliation feature must reproduce, so the numbers here are the answer
    key for attribution scoring.
    """
    d = gold_fact.copy()
    d["Month"] = pd.to_datetime(d["issue_date"]).dt.strftime("%Y-%m")

    # governed components (recomputed here so this module is self-contained)
    gov_on_time = pd.to_datetime(d["issue_date"]) <= pd.to_datetime(d["confirmed_delivery_date"])
    gov_in_full = np.where(
        d["catch_weight_flag"],
        d["invoiced_weight_lb"] >= d["ordered_weight_lb"] * (1 - C.WEIGHT_TOLERANCE),
        d["invoiced_qty"] >= d["ordered_qty"],
    )
    d["gov_otif"] = (gov_on_time & pd.Series(gov_in_full, index=d.index)).astype(int)

    # single-switch variants
    count_in_full = d["invoiced_qty"] >= d["ordered_qty"]
    d["v_weight_basis"] = (gov_on_time & count_in_full).astype(int)

    req_on_time = pd.to_datetime(d["issue_date"]) <= pd.to_datetime(d["requested_date"])
    d["v_date_basis"] = (req_on_time & pd.Series(gov_in_full, index=d.index)).astype(int)

    tol_in_full = d["invoiced_qty"] >= d["ordered_qty"] * (1 - cast(float, C.TOOL2["case_tolerance"]))
    d["v_case_tolerance"] = (gov_on_time & tol_in_full).astype(int)

    d["_is_cons"] = d["order_type"].isin(C.TOOL2["excluded_order_types"])

    # each tool's full verdict
    t1 = _tool_flags(d, C.TOOL1)
    d["tool1_otif"] = t1["_otif"]
    t2 = _tool_flags(d, C.TOOL2)
    d["tool2_otif"] = t2["_otif"]
    d["tool2_in_scope"] = t2["_in_scope"]

    months = []
    for month, g in d.groupby("Month"):
        n = len(g)
        gov_rate = float(g["gov_otif"].mean())
        t1_rate = float(g["tool1_otif"].mean())
        in_scope = g[g["tool2_in_scope"] == 1]
        t2_rate = float(in_scope["tool2_otif"].mean()) if len(in_scope) else float("nan")

        months.append(
            {
                "month": month,
                "lines": int(n),
                "governed_otif": round(gov_rate, 5),
                "tool1_otif": round(t1_rate, 5),
                "tool2_otif": round(t2_rate, 5),
                "tool1_gap": round(t1_rate - gov_rate, 5),
                "tool2_gap": round(t2_rate - gov_rate, 5),
                "cause_contributions": {
                    "weight_basis": round(float(g["v_weight_basis"].mean()) - gov_rate, 5),
                    "date_basis": round(float(g["v_date_basis"].mean()) - gov_rate, 5),
                    "case_tolerance": round(
                        float(g["v_case_tolerance"].mean()) - gov_rate, 5
                    ),
                    "scope_filter_lines_dropped": int((g["tool2_in_scope"] == 0).sum()),
                },
            }
        )

    overall_t1 = float(d["tool1_otif"].mean()) - float(d["gov_otif"].mean())
    scope = d[d["tool2_in_scope"] == 1]
    overall_t2 = float(scope["tool2_otif"].mean()) - float(d["gov_otif"].mean())

    return {
        "purpose": (
            "Two BI tools compute OTIF from the same shipments and disagree with "
            "each other and with the governed definition. The reconciliation "
            "feature must decompose each gap into named causes, not merely "
            "report that a gap exists."
        ),
        "governed_definition": {
            "on_time": "issue_date <= confirmed_delivery_date",
            "in_full": (
                "catch-weight items: invoiced_weight_lb >= ordered_weight_lb * "
                f"(1 - {C.WEIGHT_TOLERANCE}); other items: invoiced_qty >= ordered_qty"
            ),
            "scope": "all order types",
        },
        "tools": {
            "tool1_legacy_report_suite": {
                "file": "report_feed/tool1_legacy_delivery_report.csv",
                "grain": "order line",
                "differs_by": ["weight_basis"],
                "detail": "scores catch-weight items on case count instead of weight",
            },
            "tool2_dashboard_tool": {
                "file": "report_feed/tool2_dashboard_otif_export.csv",
                "grain": "month x plant x customer",
                "differs_by": ["weight_basis", "date_basis", "case_tolerance",
                               "scope_filter"],
                "detail": (
                    "measures on-time against the requested date, allows a 2% case "
                    "shortfall, and silently excludes consignment (CONS) orders"
                ),
            },
        },
        "overall": {
            "tool1_gap_vs_governed": round(overall_t1, 5),
            "tool2_gap_vs_governed": round(overall_t2, 5),
        },
        "scoring": {
            "gap_detection": "does reconciliation flag a non-zero gap for every "
                             "month where |gap| > 0.001?",
            "cause_attribution": "does it name the correct dominant cause per "
                                 "tool, and size each contribution within 0.005 "
                                 "of cause_contributions below?",
            "note": "cause contributions are single-switch deltas from the "
                    "governed definition and do not sum exactly to the total "
                    "gap, because the switches interact. Reporting them as "
                    "independent additive components is itself an error worth "
                    "catching.",
        },
        "monthly": months,
    }
