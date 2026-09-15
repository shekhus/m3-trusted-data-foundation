from __future__ import annotations

import json

import pandas as pd
import pytest
from conftest import Loaded
from sqlalchemy import Engine, text

from app.config import REPO_ROOT
from pipeline.canonical import BY_NAME
from pipeline.ingest import ingest_file
from pipeline.profile import discover_sources
from pipeline.transforms import apply

DATA = REPO_ROOT / "data"


# --- transforms (no database) ------------------------------------------------------


def test_transforms_parse_convert_and_report_errors() -> None:
    dates, bad = apply(pd.Series(["03/04/2026", "", "13/45/2026"]), BY_NAME["order_date"], ["parse_date_us"])
    assert [str(d) if d else None for d in dates] == ["2026-03-04", None, None]
    assert bad.tolist() == [False, False, True]

    serial, _ = apply(pd.Series(["46085"]), BY_NAME["issue_date"], ["parse_excel_serial"])
    assert str(serial[0]) == "2026-03-04"

    kg, _ = apply(pd.Series(["10", "x"]), BY_NAME["ordered_weight_lb"], ["kg_to_lb"])
    assert kg[0] == pytest.approx(22.046226218) and kg[1] is None

    cust, bad = apply(pd.Series(["21", "CUST-0013", "C000031", "n/a"]), BY_NAME["customer_no"],
                      ["normalize_customer_no"])
    assert cust.tolist() == ["C000021", "C000013", "C000031", None]
    assert bad.tolist() == [False, False, False, True]

    items, _ = apply(pd.Series(["IT00018  "]), BY_NAME["item_no"], ["trim"])
    assert items.tolist() == ["IT00018"]

    lines, bad = apply(pd.Series(["2", "2.5"]), BY_NAME["line_no"], [])
    assert lines.tolist() == [2, None] and bad.tolist() == [False, True]


# --- Postgres + generated data ---------------------------------------------------------


@pytest.mark.postgres
def test_unconfirmed_header_blocks_then_resumes_after_confirm(loaded: Loaded) -> None:
    blocked = [b for b in loaded.first_pass["plt01"] if b.status == "blocked"]
    assert [b.file_name[-11:-4] for b in blocked] == ["2026-06", "2026-07", "2026-08"]
    assert all(b.silver_rows == 0 and b.rows > 0 for b in blocked)
    assert all("no confirmed mapping" in (b.error or "") for b in blocked)
    assert all(r.replayed and r.status == "validated" and r.silver_rows == r.rows for r in loaded.resumed)
    assert [r.batch_id for r in loaded.resumed] == [b.batch_id for b in blocked]


@pytest.mark.postgres
def test_nothing_dropped_and_replay_writes_nothing(silver_db: Engine, loaded: Loaded) -> None:
    with silver_db.connect() as conn:
        bronze = conn.execute(text("SELECT count(*) FROM bronze.raw_order_lines")).scalar_one()
        batches = conn.execute(text("SELECT count(*) FROM ops.batches")).scalar_one()
    csv_rows = sum(b.rows for batches_ in loaded.first_pass.values() for b in batches_)
    assert bronze == csv_rows and batches == 54

    file = discover_sources(DATA / "sources")["plt02"][0]
    again = ingest_file(silver_db, "plt02", file, DATA / "master")
    assert again.replayed and again.batch_id == loaded.first_pass["plt02"][0].batch_id
    with silver_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM bronze.raw_order_lines")).scalar_one() == bronze
        assert conn.execute(text("SELECT count(*) FROM ops.batches")).scalar_one() == 54


@pytest.mark.postgres
def test_silver_matches_clean_gold_for_every_unfaulted_line(
        silver_db: Engine, loaded: Loaded) -> None:
    """The mapped, transformed silver must equal the generator's clean gold wherever no fault was seeded.

    Covers all three plants' quirks: ISO / US / Excel-serial dates, kg→lb (PLT-02), bare and dashed customer
    numbers, and trailing spaces on PLT-03 items.
    """
    with silver_db.connect() as conn:
        not_mapped = conn.execute(
            text("SELECT count(*) FROM ops.batches "
                 "WHERE status NOT IN ('validated', 'published')")).scalar_one()
        assert not_mapped == 0
        silver = pd.DataFrame(conn.execute(text("SELECT * FROM silver.order_lines")).mappings().all())
        bronze_rows = conn.execute(text("SELECT count(*) FROM bronze.raw_order_lines")).scalar_one()
    assert len(silver) == bronze_rows

    faults = json.loads((DATA / "ground_truth" / "faults.json").read_text(encoding="utf-8"))["faults"]
    faulted = {(f["order_no"], int(f["line_no"])) for f in faults}
    gold = pd.read_parquet(DATA / "gold" / "fact_delivery.parquet")
    gold = gold[[(o, int(n)) not in faulted for o, n in zip(gold["order_no"], gold["line_no"], strict=True)]]
    silver = silver[[(o, int(n)) not in faulted if pd.notna(n) else False
                     for o, n in zip(silver["order_no"], silver["line_no"], strict=True)]]
    merged = gold.merge(silver, on=["order_no", "line_no"], suffixes=("_gold", "_silver"), how="left",
                        indicator=True)
    assert (merged["_merge"] == "both").all(), "every clean gold line must reach silver"
    assert len(merged) > 50_000 and set(merged["plant_gold"]) == {"PLT-01", "PLT-02", "PLT-03"}
    assert (silver["parse_errors"].map(len) == 0).all()

    for col in ("customer_no", "item_no", "plant", "uom", "order_date", "requested_date",
                "confirmed_delivery_date", "issue_date"):
        mismatched = merged[merged[f"{col}_gold"].astype(str) != merged[f"{col}_silver"].astype(str)]
        assert mismatched.empty, (col, mismatched[["order_no", "line_no", f"{col}_gold", f"{col}_silver"]])
    for col in ("ordered_qty", "invoiced_qty"):
        assert (merged[f"{col}_gold"] - merged[f"{col}_silver"]).abs().max() < 1e-9, col
    for col in ("ordered_weight_lb", "invoiced_weight_lb"):
        present = merged[merged[f"{col}_silver"].notna()]
        # PLT-02 weights were written in kg rounded to 0.01 kg, so pounds agree within 0.01 × 2.2046
        assert (present[f"{col}_gold"] - present[f"{col}_silver"]).abs().max() < 0.023, col
    missing_weights = merged[merged["invoiced_weight_lb_silver"].isna()]
    assert set(missing_weights["plant_gold"]) <= {"PLT-02"}  # the documented ~1% missing-weight quirk only
