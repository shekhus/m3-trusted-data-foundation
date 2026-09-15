"""
Fault detectability probe (not part of the shipped generator).

Verifies the Project A side of the ground truth: a competent normalise-then-
validate pipeline must be able to find the seeded faults without drowning in
false positives from the ambient format quirks.

This is deliberately a *minimal* implementation — it is the reference that
proves the target is reachable, not the real pipeline. If this probe cannot hit
the recall targets in the plan, the dataset is unfair and the seeding needs
adjusting rather than the student's pipeline.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from synth import config as C  # noqa: E402

DATA = Path(__file__).resolve().parents[2] / "data"
EXCEL_EPOCH = date(1899, 12, 30)
LB_PER_KG = 2.2046226218


# ---------------------------------------------------------------------------
# normalise (the part that must NOT raise exceptions — these are quirks)
# ---------------------------------------------------------------------------

def parse_date(v, style: str):
    if v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() == "":
        return None
    t = str(v).strip()
    try:
        if style == "iso":
            return datetime.strptime(t, "%Y-%m-%d").date()
        if style == "us":
            return datetime.strptime(t, "%m/%d/%Y").date()
        if style == "excel_serial":
            return EXCEL_EPOCH + timedelta(days=int(float(t)))
    except (ValueError, TypeError):
        return None
    return None


def normalise_customer(v, style: str):
    if v is None or str(v).strip() == "":
        return None
    t = str(v).strip()
    if style == "prefixed":
        digits = t.lstrip("C")
    elif style == "bare":
        digits = t
    elif style == "dashed":
        digits = t.replace("CUST-", "")
    else:
        digits = t
    try:
        return f"C{int(float(digits)):06d}"
    except (ValueError, TypeError):
        return None


def load_source(plant: str) -> pd.DataFrame:
    """Read every monthly extract for a plant and map to canonical columns."""
    quirks = C.PLANT_QUIRKS[plant]
    date_style = str(quirks["date_format"])
    weight_unit = str(quirks["weight_unit"])
    cust_style = str(quirks["customer_format"])

    folder = DATA / "sources" / plant.lower().replace("-", "")
    frames = []
    for f in sorted(folder.glob("*.csv")):
        raw = pd.read_csv(f, dtype=str, keep_default_na=False)
        # Handle drift D1: the renamed column maps to the same canonical field.
        if "customer_number" in raw.columns:
            raw = raw.rename(columns={"customer_number": "cust_no"})
        col_map = C.SOURCE_COLUMN_MAP[plant]
        present = {k: v for k, v in col_map.items() if k in raw.columns}
        df = raw[list(present)].rename(columns=present)
        df["_source_file"] = f.name
        frames.append(df)

    df = pd.concat(frames, ignore_index=True)

    for c in ("order_date", "requested_date", "confirmed_delivery_date", "issue_date"):
        if c in df.columns:
            df[c] = df[c].map(lambda v: parse_date(v, date_style))

    df["customer_no"] = df["customer_no"].map(lambda v: normalise_customer(v, cust_style))
    df["item_no"] = df["item_no"].map(lambda v: str(v).strip() if str(v).strip() else None)

    for c in ("ordered_qty", "invoiced_qty", "ordered_weight_lb", "invoiced_weight_lb"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c].replace("", np.nan), errors="coerce")

    # kg -> lb is a normalisation, logged, not an exception
    if weight_unit == "kg":
        for c in ("ordered_weight_lb", "invoiced_weight_lb"):
            df[c] = (df[c] * LB_PER_KG).round(2)

    df["line_no"] = pd.to_numeric(df["line_no"], errors="coerce")
    df["plant"] = plant
    df["_row_key"] = df["order_no"].astype(str) + "|" + df["line_no"].astype("Int64").astype(str)
    return df


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def validate(df: pd.DataFrame, customers: set[str], items: set[str],
             lots: pd.DataFrame | None) -> pd.DataFrame:
    """Return one row per (row_key, rule_id) violation."""
    v: list[dict] = []

    def flag(mask: pd.Series, rule_id: str) -> None:
        for key in df.loc[mask.fillna(False), "_row_key"]:
            v.append({"row_key": key, "rule_id": rule_id})

    required = ["customer_no", "item_no", "confirmed_delivery_date", "ordered_qty"]
    missing = pd.Series(False, index=df.index)
    for c in required:
        missing = missing | df[c].isna()
    flag(missing, "V001_missing_required")

    flag(df["issue_date"].notna() & df["order_date"].notna()
         & (pd.Series([
             (i < o) if (i is not None and o is not None) else False
             for i, o in zip(df["issue_date"], df["order_date"], strict=True)
         ], index=df.index)), "V002_date_reversal")

    flag(df["invoiced_weight_lb"].notna() & df["ordered_weight_lb"].notna()
         & (df["invoiced_weight_lb"] > df["ordered_weight_lb"] * 1.05),
         "V003_weight_over_tol")

    flag(df["customer_no"].notna() & ~df["customer_no"].isin(customers),
         "V004_unknown_customer")
    flag(df["item_no"].notna() & ~df["item_no"].isin(items), "V005_unknown_item")

    flag((df["ordered_qty"] < 0) | (df["invoiced_qty"] < 0), "V007_negative_qty")

    dup = df.duplicated(subset=["order_no", "line_no"], keep="first")
    flag(dup, "V008_duplicate_line")

    if lots is not None and "lot_no" in df.columns:
        j = df[["_row_key", "lot_no", "issue_date"]].merge(
            lots[["lot_no", "expiry_date"]], on="lot_no", how="left"
        )
        bad = [
            rk for rk, iss, exp in zip(j["_row_key"], j["issue_date"], j["expiry_date"], strict=True)
            if iss is not None and exp is not None and not pd.isna(exp) and iss > exp
        ]
        for rk in bad:
            v.append({"row_key": rk, "rule_id": "V006_lot_after_expiry"})

    if "uom" in df.columns:
        # The seeded UOM fault flips uom to 'LB' while the item master basis is CASE.
        flag(df["uom"].astype(str).str.upper().eq("LB"), "V009_uom_mismatch")

    return pd.DataFrame(v).drop_duplicates()


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 72)
    print("FAULT DETECTABILITY PROBE — minimal reference pipeline")
    print("=" * 72)

    customers = set(pd.read_csv(DATA / "master" / "customers.csv")["customer_no"])
    items = set(pd.read_csv(DATA / "master" / "items.csv")["item_no"])
    truth = json.loads((DATA / "ground_truth" / "faults.json").read_text(encoding="utf-8"))
    lots = pd.read_csv(DATA / "master" / "lot_master.csv")
    lots["expiry_date"] = pd.to_datetime(lots["expiry_date"]).dt.date

    frames, all_v = [], []
    for plant in C.PLANT_QUIRKS:
        df = load_source(plant)
        frames.append(df)
        vio = validate(df, customers, items, lots)
        all_v.append(vio)
        print(f"  {plant}: {len(df):,} rows parsed, {len(vio):,} violations raised")

    parsed = pd.concat(frames, ignore_index=True)
    vio = pd.concat(all_v, ignore_index=True).drop_duplicates()

    # parse-quality check: quirks must not produce nulls
    print("\n[Parse quality — quirks should normalise silently]")
    for c in ("order_date", "issue_date", "customer_no", "ordered_weight_lb"):
        nulls = int(parsed[c].isna().sum())
        print(f"  {c:<22} nulls after normalisation: {nulls}")

    # recall / precision per rule
    seeded = pd.DataFrame(truth["faults"])
    print(f"\n[Recall per rule — {len(seeded)} seeded faults]")
    print(f"  {'rule':<26}{'seeded':>8}{'caught':>8}{'recall':>9}")
    total_seeded = total_caught = 0
    for rule_id, grp in seeded.groupby("rule_id"):
        keys = set(grp["row_key"])
        caught_keys = set(vio[vio["rule_id"] == rule_id]["row_key"]) & keys
        # V008's duplicate rows share a row key with their original, so a caught
        # duplicate is credited once.
        r = len(caught_keys) / len(keys) if keys else float("nan")
        total_seeded += len(keys)
        total_caught += len(caught_keys)
        print(f"  {rule_id:<26}{len(keys):>8}{len(caught_keys):>8}{r:>8.1%}")
    print(f"  {'OVERALL':<26}{total_seeded:>8}{total_caught:>8}"
          f"{total_caught / total_seeded:>8.1%}")

    # precision: violations on rows with no seeded fault for that rule
    seeded_pairs = set(zip(seeded["row_key"], seeded["rule_id"], strict=True))
    vio_pairs = set(zip(vio["row_key"], vio["rule_id"], strict=True))
    false_pos = vio_pairs - seeded_pairs
    print("\n[Precision]")
    print(f"  violations raised      : {len(vio_pairs)}")
    print(f"  matching a seeded fault: {len(vio_pairs & seeded_pairs)}")
    print(f"  unexplained            : {len(false_pos)}")
    if false_pos:
        fp = pd.DataFrame(sorted(false_pos), columns=["row_key", "rule_id"])
        print("  breakdown of unexplained by rule:")
        for rule, n in fp["rule_id"].value_counts().items():
            print(f"    {rule:<26}{n:>6}")
    print(f"  precision              : {len(vio_pairs & seeded_pairs) / len(vio_pairs):.1%}")

    print("\n[Drift visibility]")
    for plant in ("PLT-01", "PLT-02"):
        folder = DATA / "sources" / plant.lower().replace("-", "")
        sigs: dict[tuple[str, ...], list[str]] = {}
        for f in sorted(folder.glob("*.csv")):
            cols = tuple(pd.read_csv(f, nrows=0).columns)
            sigs.setdefault(cols, []).append(f.name)
        print(f"  {plant}: {len(sigs)} distinct column signatures across "
              f"{sum(len(v) for v in sigs.values())} files")
        for files in sigs.values():
            print(f"    {files[0]} .. {files[-1]}  ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
