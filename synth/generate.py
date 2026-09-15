"""
Entry point: `python -m synth.generate --out data`

Pipeline:
    masters -> events -> gold -> metrics -> SELF-CHECK -> faults -> extracts -> truth files

The self-check is a hard gate. If a seeded anomaly is not measurably present in
the metrics, generation exits non-zero and writes nothing to ground_truth/.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from . import bi_tools, events, export, kb_questions, knowledge, truth, world
from . import config as C
from . import gold as gold_mod


def _rng(seed: int, stream: int) -> np.random.Generator:
    """Independent child stream per stage, so adding a stage never shifts others."""
    return np.random.default_rng([seed, stream])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=f"Generate the {C.COMPANY_NAME} dataset.")
    ap.add_argument("--out", default="data", help="output directory")
    ap.add_argument("--seed", type=int, default=C.SEED)
    ap.add_argument("--clean", action="store_true", help="wipe the output directory first")
    ap.add_argument("--skip-extracts", action="store_true",
                    help="skip writing per-plant CSVs (faster for metric work)")
    args = ap.parse_args(argv)

    out = Path(args.out)
    if args.clean and out.exists():
        shutil.rmtree(out)
    for sub in ("master", "gold", "metrics", "report_feed", "ground_truth",
                "kb/docs", "kb/resolutions"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    t0 = datetime.now(UTC)
    print(f"{C.COMPANY_NAME} synthetic dataset — seed {args.seed}")
    print(f"  window: {C.START_DATE} .. {C.END_DATE}")

    # 1. masters -----------------------------------------------------------
    customers = world.build_customers(_rng(args.seed, 1))
    items = world.build_items(_rng(args.seed, 2))
    plants = world.build_plants()
    warehouses = world.build_warehouses()
    print(f"  masters: {len(customers)} customers, {len(items)} items, "
          f"{len(plants)} plants, {len(warehouses)} warehouses")

    # 2. events ------------------------------------------------------------
    lines = events.generate_order_lines(_rng(args.seed, 3), customers, items)
    inventory = events.generate_inventory(_rng(args.seed, 4), items)
    yields = events.generate_yield(_rng(args.seed, 5), items)
    print(f"  events:  {len(lines):,} order lines, {len(inventory):,} inventory "
          f"snapshots, {len(yields):,} yield runs")

    # 3. gold + metrics ----------------------------------------------------
    gold = gold_mod.build_gold(lines, customers, items, inventory, yields)
    metrics = gold_mod.daily_metrics(gold)
    overall = gold["fact_delivery"]["otif"].mean()
    print(f"  baseline OTIF across the whole window: {overall:.3%}")

    # 4. SELF-CHECK (hard gate) -------------------------------------------
    evidence, failures = truth.self_check(metrics)
    print("  self-check:")
    for aid, res in evidence.items():
        if "error" in res:
            print(f"    {aid}  ERROR {res['error']}")
        else:
            print(f"    {aid}  window={res['window_value']:<10} "
                  f"baseline={res['baseline_value']:<10} delta={res['delta']}")
    if failures:
        print("\nSELF-CHECK FAILED — ground truth not written:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 2
    print("    all seeded anomalies verified present in the metrics")

    # 5. faults + per-source extracts -------------------------------------
    damaged, ledger = export.inject_faults(lines, _rng(args.seed, 6))
    print(f"  faults:  {len(ledger)} seeded across {len(C.FAULT_PLAN)} rules")

    plant_files: dict[str, list[str]] = {}
    if not args.skip_extracts:
        plant_files = export.export_plant_extracts(damaged, _rng(args.seed, 7), out)
        n_files = sum(len(v) for v in plant_files.values())
        print(f"  extracts: {n_files} CSV files across {len(plant_files)} plants")

    lot_master = export.build_lot_master(damaged)
    lot_master.to_csv(out / "master" / "lot_master.csv", index=False, lineterminator="\n")
    print(f"  lot master: {len(lot_master):,} lots")

    tool1 = bi_tools.build_tool1_feed(lines, customers, items)
    tool1.to_csv(out / "report_feed" / "tool1_legacy_delivery_report.csv", index=False, lineterminator="\n")
    tool2 = bi_tools.build_tool2_feed(lines, customers)
    tool2.to_csv(out / "report_feed" / "tool2_dashboard_otif_export.csv", index=False, lineterminator="\n")
    recon = bi_tools.build_reconciliation_truth(lines, gold["fact_delivery"])
    print(f"  BI tool 1 (line level)   : {len(tool1):,} rows, {len(tool1.columns)} columns")
    print(f"  BI tool 2 (monthly agg)  : {len(tool2):,} rows, {len(tool2.columns)} columns")
    print(f"  reconciliation: tool1 gap {recon['overall']['tool1_gap_vs_governed']:+.4f}, "
          f"tool2 gap {recon['overall']['tool2_gap_vs_governed']:+.4f} vs governed")

    # 6. write tables ------------------------------------------------------
    customers.to_csv(out / "master" / "customers.csv", index=False, lineterminator="\n")
    items.to_csv(out / "master" / "items.csv", index=False, lineterminator="\n")
    plants.to_csv(out / "master" / "plants.csv", index=False, lineterminator="\n")
    warehouses.to_csv(out / "master" / "warehouses.csv", index=False, lineterminator="\n")

    for name, df in gold.items():
        df.to_parquet(out / "gold" / f"{name}.parquet", index=False)
    for name, df in metrics.items():
        df.to_parquet(out / "metrics" / f"{name}.parquet", index=False)

    # 6b. knowledge base ---------------------------------------------------
    docs = knowledge.build_corpus()
    resolutions = knowledge.build_resolutions(_rng(args.seed, 8))
    questions = kb_questions.build_questions()

    kb = out / "kb"
    for d in docs:
        front = {k: v for k, v in d.items() if k != "body"}
        md = ["---"]
        for k, v in front.items():
            md.append(f"{k}: {json.dumps(v) if isinstance(v, list) else v}")
        md += ["---", "", d["body"], ""]
        (kb / "docs" / f"{d['doc_id']}.md").write_text("\n".join(md), encoding="utf-8", newline="\n")
    (kb / "resolutions" / "prior_resolutions.json").write_text(
        json.dumps(resolutions, indent=2), encoding="utf-8", newline="\n")

    n_restricted = sum(1 for d in docs if d["access_level"] != "all")
    n_super = sum(1 for d in docs if d["status"] != "current")
    print(f"  knowledge base: {len(docs)} documents "
          f"({n_restricted} access-controlled, {n_super} superseded/expired), "
          f"{len(resolutions)} prior resolutions")
    print(f"  golden questions: {len(questions)} across "
          f"{len({q['challenge'] for q in questions})} RAG challenges, "
          f"{sum(1 for q in questions if q['should_refuse'])} refusal cases")

    # 7. ground truth ------------------------------------------------------
    gt = out / "ground_truth"
    truth.write_mappings(gt, plant_files)
    truth.write_faults(gt, ledger)
    truth.write_anomalies(gt, evidence)
    truth.write_drift(gt)
    (gt / "reconciliation.json").write_text(json.dumps(recon, indent=2), encoding="utf-8", newline="\n")
    truth.write_kb_truth(gt, docs, resolutions, questions)

    manifest = {
        "generated_at_utc": t0.isoformat(),
        "seed": args.seed,
        "window": {"start": str(C.START_DATE), "end": str(C.END_DATE)},
        "customer": f"{C.COMPANY_NAME} (FICTIONAL)",
        "row_counts": {
            "order_lines": len(lines),
            "order_lines_after_fault_injection": len(damaged),
            "inventory_snapshots": len(inventory),
            "yield_runs": len(yields),
            "tool1_feed_rows": len(tool1),
            "tool2_feed_rows": len(tool2),
            "lot_master_rows": len(lot_master),
        },
        "baseline_otif_rate": round(float(overall), 5),
        "bi_tool_gaps_vs_governed": recon["overall"],
        "knowledge_base": {
            "documents": len(docs),
            "access_controlled": n_restricted,
            "superseded_or_expired": n_super,
            "prior_resolutions": len(resolutions),
            "golden_questions": len(questions),
            "refusal_cases": sum(1 for q in questions if q["should_refuse"]),
        },
        "seeded": {
            "anomalies": len(C.ANOMALIES),
            "anomalies_expected_detectable": sum(a.expect_detection for a in C.ANOMALIES),
            "faults": len(ledger),
            "drift_events": len(C.DRIFT_EVENTS),
        },
        "self_check": "passed",
    }
    (gt / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")

    _write_dataset_doc(out, manifest, metrics)
    print(f"\n  wrote {out.resolve()}")
    print(f"  elapsed {(datetime.now(UTC) - t0).total_seconds():.1f}s")
    return 0


def _write_dataset_doc(out: Path, manifest: dict, metrics: dict) -> None:
    lines = [
        f"# {C.COMPANY_NAME} — Synthetic Dataset",
        "",
        f"**{C.COMPANY_NAME} is a fictional company.** Every row in this dataset is",
        "generated by code in `synth/`. No real company, customer, product, or",
        "operational metric is represented. Table and column shapes are modelled on",
        "Infor M3 order, delivery, inventory, and production reporting structures;",
        "**physical M3 field names are not asserted anywhere** and would need to be",
        "confirmed with an M3 subject-matter expert before any client use.",
        "",
        f"Generated {manifest['generated_at_utc']} with seed `{manifest['seed']}`.",
        f"Window {manifest['window']['start']} to {manifest['window']['end']}.",
        "Regeneration with the same seed is byte-identical.",
        "",
        "## Row counts",
        "",
        "| Table | Rows |",
        "|---|---|",
    ]
    for k, v in manifest["row_counts"].items():
        lines.append(f"| {k} | {v:,} |")

    lines += [
        "",
        f"Baseline OTIF across the window: **{manifest['baseline_otif_rate']:.3%}**",
        "",
        "## Layout",
        "",
        "```",
        "data/",
        "  sources/plt01|plt02|plt03/   per-plant monthly CSV extracts (messy, Project A input)",
        "  report_feed/                 TWO independent BI tool exports that disagree",
        "  master/                      customers, items, plants, warehouses",
        "  gold/                        clean fact + dimension tables (Project B standalone input)",
        "  metrics/                     daily_* metric series",
        "  ground_truth/                mappings, faults, anomalies, drift, manifest",
        "```",
        "",
        "## The three plants are deliberately inconsistent",
        "",
        "| Plant | Date format | Weight unit | Customer number | Other quirks |",
        "|---|---|---|---|---|",
        "| PLT-01 | ISO `2026-03-04` | lb | `C000123` | clean; gains a `lot_no` column on 2026-06-01 |",
        "| PLT-02 | US `03/04/2026` | **kg** | `123` | ~1% missing weights; renames `cust_no` on 2026-03-01 |",
        "| PLT-03 | Excel serial `46085` | lb | `CUST-0123` | trailing spaces on item numbers, ~0.5% duplicate rows |",
        "",
        "These are **quirks, not faults**. A correct pipeline normalises them and logs",
        "the fix. Raising an exception for a kg weight is a false positive.",
        "",
        "## Seeded anomalies (Project B)",
        "",
        "| ID | Metric | Segment | Window | Planted cause | Detect? |",
        "|---|---|---|---|---|---|",
    ]
    for a in C.ANOMALIES:
        seg = ", ".join(f"{k}={v}" for k, v in a.segment.items())
        lines.append(
            f"| {a.anomaly_id} | `{a.metric}` | {seg} | {a.start} to {a.end} | "
            f"{a.cause} | {'yes' if a.expect_detection else '**NO — decoy**'} |"
        )

    lines += [
        "",
        "A5 is a real dip in the data caused by a holiday shutdown. A detector with",
        "day-of-week and holiday awareness should not escalate it. Flagging it HIGH",
        "counts against precision.",
        "",
        "Each anomaly in `ground_truth/anomalies.json` carries an `observed` block with",
        "the measured window value, baseline value, and delta, produced by the",
        "generator's self-check. Generation fails if any expected effect is absent.",
        "",
        "## Seeded faults (Project A)",
        "",
        "| Rule | Description | Count |",
        "|---|---|---|",
    ]
    for rid, (desc, n) in C.FAULT_PLAN.items():
        lines.append(f"| `{rid}` | {desc} | {n} |")

    lines += [
        "",
        "Every fault is listed in `ground_truth/faults.json` with its row key, so",
        "exception recall and precision are both directly measurable.",
        "",
        "## Schema drift (Project A)",
        "",
        "| ID | Effective | Source | Change |",
        "|---|---|---|---|",
    ]
    for e in C.DRIFT_EVENTS:
        lines.append(f"| {e.drift_id} | {e.effective} | {e.source} | {e.detail} |")

    lines += [
        "",
        "D2 is the mid-project change request: the column appears first, and the",
        "business declares it required afterwards. Historical rows without it must be",
        "queued as exceptions, never dropped.",
        "",
        "## Regenerating",
        "",
        "```bash",
        "python -m synth.generate --out data --clean",
        "```",
        "",
        "Exit code 2 means the self-check failed and no ground truth was written.",
        "",
    ]
    (out / "DATASET.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    raise SystemExit(main())
