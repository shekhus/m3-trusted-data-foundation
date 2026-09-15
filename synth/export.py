"""
Turn the clean canonical event tables into the messy per-source extracts a real
integration project receives, and record exactly what was broken so the
evaluation harness can score recall.

Two distinct kinds of mess, kept deliberately separate:

  QUIRKS  — ambient format differences (kg vs lb, three date formats, three
            customer-number conventions, trailing whitespace). A correct
            pipeline normalises these silently and logs the fix. They are NOT
            faults and must not appear in ground_truth/faults.json.

  FAULTS  — actual data-quality defects (missing required fields, reversed
            dates, weights outside tolerance, unknown keys, expired lots,
            negatives, duplicates, UOM contradictions). Every one is recorded
            with its row key and the rule id expected to catch it.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd

from . import config as C

EXCEL_EPOCH = date(1899, 12, 30)


# ---------------------------------------------------------------------------
# format conversions
# ---------------------------------------------------------------------------

def _fmt_date(d, style: str):
    if pd.isna(d) or d is None or d == "":
        return ""
    if isinstance(d, pd.Timestamp):
        d = d.date()
    if style == "iso":
        return d.isoformat()
    if style == "us":
        return f"{d.month:02d}/{d.day:02d}/{d.year}"
    if style == "excel_serial":
        return str((d - EXCEL_EPOCH).days)
    raise ValueError(style)


def _fmt_customer(cust: str, style: str) -> str:
    if not isinstance(cust, str) or not cust:
        return ""
    digits = cust.lstrip("C").lstrip("0") or "0"
    if style == "prefixed":
        return cust                      # C000123
    if style == "bare":
        return digits                    # 123
    if style == "dashed":
        return f"CUST-{int(digits):04d}"  # CUST-0123
    raise ValueError(style)


LB_PER_KG = 2.2046226218


# ---------------------------------------------------------------------------
# fault injection
# ---------------------------------------------------------------------------

def inject_faults(
    lines: pd.DataFrame, rng: np.random.Generator
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Apply the seeded faults from config.FAULT_PLAN to a copy of the canonical
    order lines. Returns the damaged frame plus the fault ledger.

    Faults are applied to disjoint row sets so each row carries at most one
    seeded fault. That keeps recall scoring unambiguous.
    """
    df = lines.copy()
    df["_row_key"] = df["order_no"] + "|" + df["line_no"].astype(str)

    # Only damage rows that are otherwise clean, and never rows carrying an
    # anomaly marker — otherwise Project A faults and Project B anomalies
    # interfere with each other's scoring.
    eligible = np.array(df.index[df["_anomaly_id"] == ""].to_numpy(), copy=True)
    rng.shuffle(eligible)

    ledger: list[dict] = []
    cursor = 0

    def take(n: int) -> np.ndarray:
        nonlocal cursor
        chunk = eligible[cursor: cursor + n]
        cursor += n
        return chunk

    def record(idx, rule_id: str, detail: str, extra: dict | None = None) -> None:
        row = df.loc[idx]
        entry = {
            "fault_id": f"F{len(ledger) + 1:05d}",
            "rule_id": rule_id,
            "row_key": row["_row_key"],
            "order_no": row["order_no"],
            "line_no": int(row["line_no"]),
            "plant": row["plant"],
            "issue_date": str(row["issue_date"]),
            "detail": detail,
        }
        if extra:
            entry.update(extra)
        ledger.append(entry)

    # V001 — blank a required field
    for idx in take(C.FAULT_PLAN["V001_missing_required"][1]):
        field = str(rng.choice(["customer_no", "item_no", "confirmed_delivery_date", "ordered_qty"]))
        record(idx, "V001_missing_required", f"{field} blanked")
        df.at[idx, field] = None

    # V002 — issue_date before order_date
    for idx in take(C.FAULT_PLAN["V002_date_reversal"][1]):
        new_issue = df.at[idx, "order_date"] - timedelta(days=int(rng.integers(1, 9)))
        record(idx, "V002_date_reversal", f"issue_date moved to {new_issue}")
        df.at[idx, "issue_date"] = new_issue

    # V003 — invoiced weight above tolerance
    for idx in take(C.FAULT_PLAN["V003_weight_over_tol"][1]):
        factor = float(rng.uniform(1.08, 1.45))
        record(idx, "V003_weight_over_tol", f"invoiced_weight_lb inflated x{factor:.2f}")
        df.at[idx, "invoiced_weight_lb"] = round(df.at[idx, "ordered_weight_lb"] * factor, 2)

    # V004 — unknown customer
    for idx in take(C.FAULT_PLAN["V004_unknown_customer"][1]):
        bogus = f"C{rng.integers(900000, 999999)}"
        record(idx, "V004_unknown_customer", f"customer_no set to {bogus}")
        df.at[idx, "customer_no"] = bogus

    # V005 — unknown item
    for idx in take(C.FAULT_PLAN["V005_unknown_item"][1]):
        bogus = f"IT{rng.integers(90000, 99999)}"
        record(idx, "V005_unknown_item", f"item_no set to {bogus}")
        df.at[idx, "item_no"] = bogus

    # V006 — lot shipped after expiry
    for idx in take(C.FAULT_PLAN["V006_lot_after_expiry"][1]):
        new_expiry = df.at[idx, "issue_date"] - timedelta(days=int(rng.integers(1, 12)))
        record(idx, "V006_lot_after_expiry", f"expiry_date moved to {new_expiry}")
        df.at[idx, "expiry_date"] = new_expiry

    # V007 — negative quantity
    for idx in take(C.FAULT_PLAN["V007_negative_qty"][1]):
        field = str(rng.choice(["ordered_qty", "invoiced_qty"]))
        record(idx, "V007_negative_qty", f"{field} negated")
        df.at[idx, field] = -abs(float(df.at[idx, field] or 1.0))

    # V009 — UOM says LB but the value was recorded in kg
    for idx in take(C.FAULT_PLAN["V009_uom_mismatch"][1]):
        record(idx, "V009_uom_mismatch",
               "weights divided to kg while uom left as CASE/LB basis")
        df.at[idx, "ordered_weight_lb"] = round(df.at[idx, "ordered_weight_lb"] / LB_PER_KG, 2)
        df.at[idx, "invoiced_weight_lb"] = round(df.at[idx, "invoiced_weight_lb"] / LB_PER_KG, 2)
        df.at[idx, "uom"] = "LB"

    # V008 — duplicate lines (appended, so they carry the same row key)
    dup_idx = take(C.FAULT_PLAN["V008_duplicate_line"][1])
    dups = df.loc[dup_idx].copy()
    for idx in dup_idx:
        record(idx, "V008_duplicate_line", "line duplicated within the same batch")
    df = pd.concat([df, dups], ignore_index=True)

    return df, ledger


# ---------------------------------------------------------------------------
# per-source export
# ---------------------------------------------------------------------------

def export_plant_extracts(
    damaged: pd.DataFrame,
    rng: np.random.Generator,
    out_dir: Path,
) -> dict[str, list[str]]:
    """
    Write one CSV per plant per month, in that plant's own format, applying its
    ambient quirks and the two schema-drift events.

    Returns {plant: [relative file paths]}.
    """
    written: dict[str, list[str]] = {}

    for plant_code, quirks in C.PLANT_QUIRKS.items():
        sub = damaged[damaged["plant"] == plant_code].copy()
        if sub.empty:
            written[plant_code] = []
            continue

        date_style = str(quirks["date_format"])
        weight_unit = str(quirks["weight_unit"])
        cust_style = str(quirks["customer_format"])

        # ambient quirk: trailing whitespace on item numbers
        tsr = cast(float, quirks["trailing_space_rate"])
        if tsr > 0:
            mask = rng.random(len(sub)) < tsr
            sub.loc[mask, "item_no"] = sub.loc[mask, "item_no"].astype(str) + "  "

        # ambient quirk: a small share of weights simply absent
        mwr = cast(float, quirks["missing_weight_rate"])
        if mwr > 0:
            mask = rng.random(len(sub)) < mwr
            sub.loc[mask, "invoiced_weight_lb"] = None

        # ambient quirk: occasional duplicated row (format sloppiness, low rate)
        adr = cast(float, quirks["ambient_dup_rate"])
        if adr > 0:
            extra = sub.sample(frac=adr, random_state=int(rng.integers(0, 10**6)))
            sub = pd.concat([sub, extra], ignore_index=True)

        col_map = {v: k for k, v in C.SOURCE_COLUMN_MAP[plant_code].items()}
        files: list[str] = []
        plant_dir = out_dir / "sources" / plant_code.lower().replace("-", "")
        plant_dir.mkdir(parents=True, exist_ok=True)

        sub["_month"] = pd.to_datetime(sub["order_date"]).dt.to_period("M")

        for period, chunk in sub.groupby("_month", sort=True):
            month_start = period.to_timestamp().date()
            out = pd.DataFrame()

            for canonical, physical in col_map.items():
                if canonical not in chunk.columns:
                    continue
                series = chunk[canonical]

                if canonical in {
                    "order_date", "requested_date", "confirmed_delivery_date", "issue_date"
                }:
                    out[physical] = series.map(lambda d, style=date_style: _fmt_date(d, style))
                elif canonical == "customer_no":
                    out[physical] = series.map(lambda c, style=cust_style: _fmt_customer(c, style))
                elif canonical in {"ordered_weight_lb", "invoiced_weight_lb"}:
                    if weight_unit == "kg":
                        out[physical] = series.map(
                            lambda v: "" if pd.isna(v) else round(float(v) / LB_PER_KG, 2)
                        )
                    else:
                        out[physical] = series.map(lambda v: "" if pd.isna(v) else v)
                else:
                    out[physical] = series.map(lambda v: "" if pd.isna(v) else v)

            # --- drift D1: PLT-02 renames cust_no -> customer_number ---------
            if plant_code == "PLT-02" and month_start >= C.DRIFT_EVENTS[0].effective:
                out = out.rename(columns={"cust_no": "customer_number"})

            # --- drift D2: PLT-01 gains a lot_no column ---------------------
            if plant_code == "PLT-01":
                if month_start < C.DRIFT_EVENTS[1].effective:
                    out = out.drop(columns=[c for c in ("lot_no",) if c in out.columns])

            fname = f"{plant_code.lower()}_orderlines_{period}.csv"
            out.to_csv(plant_dir / fname, index=False, lineterminator="\n")
            files.append(f"sources/{plant_code.lower().replace('-', '')}/{fname}")

        written[plant_code] = files

    return written


def build_lot_master(damaged: pd.DataFrame) -> pd.DataFrame:
    """
    Lot dimension extracted from the (damaged) shipment records.

    V006 (lot shipped after expiry) is only detectable by joining order lines to
    this file, which is deliberate: referential integrity across sources is a
    core integration skill, and a rule that needs no join teaches nothing.

    Where a lot appears more than once, the earliest expiry wins — that is what
    makes the seeded V006 rows visible rather than averaged away.
    """
    lots = (
        damaged[["lot_no", "item_no", "plant", "production_date", "expiry_date"]]
        .dropna(subset=["lot_no"])
        .sort_values("expiry_date")
        .drop_duplicates(subset=["lot_no"], keep="first")
        .reset_index(drop=True)
    )
    return lots


# ---------------------------------------------------------------------------
# downstream report feed
# ---------------------------------------------------------------------------

# NOTE: the report feed moved to bi_tools.py, which builds TWO independent tool
# exports plus the reconciliation ground truth. A single feed could not express
# the "two reports, two OTIF numbers" problem this dataset exists to model.
