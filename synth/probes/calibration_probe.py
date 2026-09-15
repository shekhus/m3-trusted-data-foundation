"""
Dataset calibration probe (not part of the shipped generator).

Purpose: prove the dataset is neither too easy nor too hard before Project B's
real detectors are written. Specifically:

  1. A naive z-score detector SHOULD flag the A5 holiday decoy (false positive).
  2. A day-of-week + holiday aware detector should NOT flag A5 at HIGH.
  3. Both should find the genuine anomalies.

If the naive detector already ignores A5, the decoy is pointless. If the aware
detector still flags it, the seasonality signal is too weak to learn from.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from synth import config as C  # noqa: E402

DATA = Path(__file__).resolve().parents[2] / "data"
Z_WARN, Z_HIGH = 2.0, 3.0
BASELINE_DAYS = 28


def load(name: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA / "metrics" / f"{name}.parquet")
    df["metric_date"] = pd.to_datetime(df["metric_date"]).dt.date
    return df


def detect(series: pd.DataFrame, value_col: str, dow_aware: bool) -> pd.DataFrame:
    """
    Rolling-baseline z-score detector.

    dow_aware=False : baseline = trailing 28 calendar days, all days pooled
    dow_aware=True  : baseline = trailing 8 occurrences of the SAME weekday,
                      and known holidays (plus the day either side) are excluded
                      from both baseline and scoring
    """
    s = series.sort_values("metric_date").reset_index(drop=True)
    s["dow"] = pd.to_datetime(s["metric_date"]).dt.dayofweek

    holiday_blackout: set[date] = set()
    for h in C.KNOWN_HOLIDAYS:
        for off in range(-2, 3):
            holiday_blackout.add(h + timedelta(days=off))

    out = []
    for i, row in s.iterrows():
        d = row["metric_date"]
        if dow_aware:
            hist = s.iloc[:i]
            hist = hist[hist["dow"] == row["dow"]]
            hist = hist[~hist["metric_date"].isin(holiday_blackout)].tail(8)
        else:
            lo = d - timedelta(days=BASELINE_DAYS)
            hist = s.iloc[:i]
            hist = hist[hist["metric_date"] >= lo]

        if len(hist) < 5:
            continue
        mu, sd = hist[value_col].mean(), hist[value_col].std(ddof=1)
        if not sd or np.isnan(sd) or sd < 1e-9:
            continue
        z = (row[value_col] - mu) / sd

        if dow_aware and d in holiday_blackout:
            continue  # a known shutdown is expected, not an incident

        sev = "HIGH" if abs(z) >= Z_HIGH else ("WARN" if abs(z) >= Z_WARN else None)
        if sev:
            out.append({"metric_date": d, "z": round(float(z), 2), "severity": sev,
                        "value": round(float(row[value_col]), 4), "baseline": round(float(mu), 4)})
    return pd.DataFrame(out)


def which_anomaly(d: date) -> str:
    for a in C.ANOMALIES:
        if a.start <= d <= a.end:
            return a.anomaly_id
    return ""


def report(label: str, flags: pd.DataFrame, target_ids: set[str]) -> None:
    if flags.empty:
        print(f"  {label}: no flags at all")
        return
    flags = flags.copy()
    flags["anomaly"] = flags["metric_date"].map(which_anomaly)
    hits = flags[flags["anomaly"].isin(target_ids)]
    decoy_high = flags[(flags["anomaly"] == "A5") & (flags["severity"] == "HIGH")]
    decoy_any = flags[flags["anomaly"] == "A5"]
    noise = flags[flags["anomaly"] == ""]

    print(f"  {label}")
    print(f"    flag-days total          : {len(flags)}")
    print(f"    inside target window(s)  : {len(hits)}")
    print(f"    A5 decoy flagged (any)   : {len(decoy_any)}")
    print(f"    A5 decoy flagged (HIGH)  : {len(decoy_high)}  <-- false positive if > 0")
    print(f"    outside any window       : {len(noise)}")


def main() -> int:
    print("=" * 70)
    print("DATASET CALIBRATION PROBE")
    print("=" * 70)

    # --- total OTIF: this is where the A5 decoy lives --------------------
    tot = load("daily_otif_total")
    print("\n[1] Total OTIF — decoy discrimination")
    report("naive (28d pooled baseline)      ", detect(tot, "otif_rate", False), {"A5"})
    report("dow + holiday aware              ", detect(tot, "otif_rate", True), {"A5"})

    # --- A1: lane-level OTIF --------------------------------------------
    lane = load("daily_otif_lane")
    a1 = next(a for a in C.ANOMALIES if a.anomaly_id == "A1")
    sel = lane[(lane["plant"] == a1.segment["plant"])
               & (lane["customer_no"] == a1.segment["customer_no"])]
    sel = sel[sel["lines"] >= 3]
    f = detect(sel, "otif_rate", True)
    inwin = f[(f["metric_date"] >= a1.start) & (f["metric_date"] <= a1.end)] if not f.empty else f
    print(f"\n[2] A1 lane {a1.segment['plant']} > {a1.segment['customer_no']}")
    print(f"    lane days with >=3 lines : {len(sel)}")
    print(f"    flags in A1 window       : {len(inwin)} / {len(f)} total flags")
    if not inwin.empty:
        lead = (inwin["metric_date"].min() - a1.start).days
        print(f"    lead time to first flag  : {lead} days after window start")

    # --- A2: weight fill rate, and the count basis blind spot ------------
    fpg = load("daily_fill_plant_group")
    a2 = next(a for a in C.ANOMALIES if a.anomaly_id == "A2")
    sel2 = fpg[(fpg["plant"] == a2.segment["plant"])
               & (fpg["product_group"] == a2.segment["product_group"])]
    fw = detect(sel2, "fill_rate_weight", True)
    fc = detect(sel2, "fill_rate_count", True)
    inw = fw[(fw["metric_date"] >= a2.start) & (fw["metric_date"] <= a2.end)] if not fw.empty else fw
    inc = fc[(fc["metric_date"] >= a2.start) & (fc["metric_date"] <= a2.end)] if not fc.empty else fc
    print(f"\n[3] A2 {a2.segment['plant']} / {a2.segment['product_group']}")
    print(f"    flags on WEIGHT basis in window : {len(inw)}   <-- should be > 0")
    print(f"    flags on COUNT  basis in window : {len(inc)}   <-- should be ~0 (blind spot)")

    # --- A3 yield, A4 inventory, A6 backlog ------------------------------
    dy = load("daily_yield")
    a3 = next(a for a in C.ANOMALIES if a.anomaly_id == "A3")
    sel3 = dy[(dy["plant"] == a3.segment["plant"]) & (dy["line"] == a3.segment["line"])
              & (dy["product_group"] == a3.segment["product_group"])]
    f3 = detect(sel3, "yield_variance_pct", True)
    in3 = f3[(f3["metric_date"] >= a3.start) & (f3["metric_date"] <= a3.end)] if not f3.empty else f3

    inv = load("daily_inventory_location")
    a4 = next(a for a in C.ANOMALIES if a.anomaly_id == "A4")
    sel4 = inv[inv["location"] == a4.segment["location"]]
    f4 = detect(sel4, "inventory_age_days", True)
    in4 = f4[(f4["metric_date"] >= a4.start) & (f4["metric_date"] <= a4.end)] if not f4.empty else f4

    bl = load("daily_backlog_plant")
    a6 = next(a for a in C.ANOMALIES if a.anomaly_id == "A6")
    sel6 = bl[bl["plant"] == a6.segment["plant"]]
    f6 = detect(sel6, "open_backlog_lines", True)
    in6 = f6[(f6["metric_date"] >= a6.start) & (f6["metric_date"] <= a6.end)] if not f6.empty else f6

    print("\n[4] Remaining genuine anomalies (dow-aware detector)")
    for aid, inw_, tot_ in (("A3", in3, f3), ("A4", in4, f4), ("A6", in6, f6)):
        print(f"    {aid}: {len(inw_)} flags in window / {len(tot_)} total")

    # --- attribution sanity: does A1's lane dominate the plant delta? ----
    print("\n[5] Attribution signal for A1")
    plant_lane = lane[lane["plant"] == "PLT-02"].copy()
    win = plant_lane[(plant_lane["metric_date"] >= a1.start)
                     & (plant_lane["metric_date"] <= a1.end)]
    base = plant_lane[(plant_lane["metric_date"] >= a1.start - timedelta(days=56))
                      & (plant_lane["metric_date"] < a1.start)]

    def wmean(g):
        return (g["otif_rate"] * g["lines"]).sum() / g["lines"].sum() if g["lines"].sum() else np.nan

    contrib = []
    for cust, gw in win.groupby("customer_no"):
        gb = base[base["customer_no"] == cust]
        if gb.empty or gw["lines"].sum() < 5:
            continue
        # contribution to the plant-level delta, volume weighted
        share = gw["lines"].sum() / win["lines"].sum()
        contrib.append({"customer_no": cust, "delta": wmean(gw) - wmean(gb),
                        "volume_share": share,
                        "weighted_contribution": (wmean(gw) - wmean(gb)) * share})
    cdf = pd.DataFrame(contrib).sort_values("weighted_contribution")
    print(f"    plant-level OTIF delta   : {wmean(win) - wmean(base):+.4f}")
    print("    top 3 contributing lanes (most negative first):")
    for _, r in cdf.head(3).iterrows():
        marker = "  <-- SEEDED CAUSE" if r["customer_no"] == a1.segment["customer_no"] else ""
        print(f"      {r['customer_no']}  delta={r['delta']:+.4f}  "
              f"vol={r['volume_share']:.3f}  contrib={r['weighted_contribution']:+.5f}{marker}")

    top = cdf.iloc[0]["customer_no"] if not cdf.empty else None
    print(f"\n    top-1 attribution correct: {top == a1.segment['customer_no']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
