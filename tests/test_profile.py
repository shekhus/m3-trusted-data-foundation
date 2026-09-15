from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.config import REPO_ROOT
from pipeline.profile import (
    SourceProfile,
    _classify,
    _header_unit,
    _header_variants,
    _shape,
    load_masters,
    profile_source,
    render_markdown,
)

DATA = REPO_ROOT / "data"


# --- unit: no generated data needed --------------------------------------------


@pytest.mark.parametrize(
    ("values", "dtype", "fmt"),
    [
        (["2026-03-04", "2025-12-31"], "date", "iso"),
        (["03/04/2026", "12/31/2025"], "date", "us"),
        (["31/12/2025", "04/03/2026"], "date", "dmy"),
        (["03/04/2026", "04/03/2026"], "date", "slash_ambiguous"),
        (["46085", "45717"], "date", "excel_serial"),
        (["24.0", "3", "-2"], "integer", None),
        (["546.48", "12"], "decimal", None),
        (["SO0000001", "SO0000002"], "string", None),
        ([], "empty", None),
    ],
)
def test_classify(values: list[str], dtype: str, fmt: str | None) -> None:
    got_dtype, date_profile = _classify(pd.Series(values, dtype=str))
    assert got_dtype == dtype
    assert (date_profile.format if date_profile else None) == fmt


def test_excel_serial_converts_to_calendar_dates() -> None:
    _, date_profile = _classify(pd.Series(["45717", "46085"], dtype=str))
    assert date_profile is not None
    assert (date_profile.min, date_profile.max) == (date(2025, 3, 1), date(2026, 3, 4))


@pytest.mark.parametrize(
    ("name", "unit"),
    [("WT_ORD_KG", "kg"), ("weightKg", "kg"), ("ship_wt_lbs", "lb"), ("WeightOrdered", None),
     ("ord_wt", None), ("package_id", None)],
)
def test_header_unit(name: str, unit: str | None) -> None:
    assert _header_unit(name) == unit


def test_shape_marks_trailing_whitespace() -> None:
    assert _shape(pd.Series(["IT00018  ", "CUST-0013"])).tolist() == ["AA99999··", "AAAA-9999"]


def test_header_variants_report_rename() -> None:
    variants = _header_variants([("m1.csv", ["a", "cust_no"]), ("m2.csv", ["a", "cust_no"]),
                                 ("m3.csv", ["a", "customer_number"])])
    assert [(v.first_file, v.last_file, v.file_count) for v in variants] == [("m1.csv", "m2.csv", 2),
                                                                            ("m3.csv", "m3.csv", 1)]
    assert (variants[1].added, variants[1].removed) == (["customer_number"], ["cust_no"])


def _write_masters(master_dir: Path) -> None:
    master_dir.mkdir()
    (master_dir / "customers.csv").write_text("customer_no\nC000001\n", encoding="utf-8")
    (master_dir / "items.csv").write_text("item_no,avg_lb_per_case\nIT00001,40.0\nIT00002,10.0\n",
                                          encoding="utf-8")
    (master_dir / "plants.csv").write_text("plant\nPLT-09\n", encoding="utf-8")


def test_profile_source_small_extract(tmp_path: Path) -> None:
    _write_masters(tmp_path / "master")
    rows = ["order,line,item,qty,weight"]
    for i in range(60):
        item = "IT00001" if i % 2 else "IT00002  "
        lb = 40.0 if i % 2 else 10.0
        rows.append(f"SO{i:07d},1,{item},2,{2 * lb / 2.2046226218:.2f}")  # weights in kg, header says nothing
    rows.append(rows[1])  # one exact duplicate
    extract = tmp_path / "src.csv"
    extract.write_text("\n".join(rows) + "\n", encoding="utf-8")

    p = profile_source("demo", [extract], load_masters(tmp_path / "master"))
    cols = {c.name: c for c in p.columns}
    assert cols["weight"].unit is not None and cols["weight"].unit.unit == "kg"
    assert cols["weight"].unit.header_unit is None
    assert cols["item"].reference is not None and cols["item"].reference.master == "items"
    assert cols["item"].whitespace_pct == pytest.approx(100 * 31 / 61, abs=0.01)
    assert p.candidate_key is not None and p.candidate_key.columns == ["order"]
    assert p.candidate_key.duplicate_rows == 1 and p.exact_duplicate_rows == 1
    assert any("kg" in f for f in p.findings)


# --- integration: against the generated dataset and its answer key --------------


@pytest.fixture(scope="module")
def profiles(generated_profiles: dict[str, SourceProfile]) -> dict[str, SourceProfile]:
    return generated_profiles


@pytest.fixture(scope="module")
def truth() -> dict:
    mappings = json.loads((DATA / "ground_truth" / "mappings.json").read_text(encoding="utf-8"))
    faults = json.loads((DATA / "ground_truth" / "faults.json").read_text(encoding="utf-8"))
    drift = json.loads((DATA / "ground_truth" / "drift.json").read_text(encoding="utf-8"))
    return {"mappings": mappings, "faults": faults, "drift": drift}


def _source_dir(plant: str) -> str:
    return plant.lower().replace("-", "")


def _physical(spec: dict, canonical: str) -> list[str]:
    maps = [spec["column_map"], *(v["column_map"] for v in spec.get("drift_variants", []))]
    return sorted({src for m in maps for src, dst in m.items() if dst == canonical})


def test_all_three_sources_profiled(profiles: dict[str, SourceProfile]) -> None:
    assert sorted(profiles) == ["plt01", "plt02", "plt03"]


def test_date_format_matches_answer_key(profiles: dict[str, SourceProfile], truth: dict) -> None:
    for plant, spec in truth["mappings"]["sources"].items():
        cols = {c.name: c for c in profiles[_source_dir(plant)].columns}
        for canonical in ("order_date", "requested_date", "confirmed_delivery_date", "issue_date"):
            for name in _physical(spec, canonical):
                date_profile = cols[name].date
                assert date_profile is not None, (plant, name)
                assert date_profile.format == spec["date_format"], (plant, name)


def test_weight_unit_matches_answer_key(profiles: dict[str, SourceProfile], truth: dict) -> None:
    for plant, spec in truth["mappings"]["sources"].items():
        cols = {c.name: c for c in profiles[_source_dir(plant)].columns}
        for canonical in ("ordered_weight_lb", "invoiced_weight_lb"):
            for name in _physical(spec, canonical):
                unit = cols[name].unit
                assert unit is not None and not unit.conflict, (plant, name)
                assert unit.unit == spec["weight_unit"], (plant, name)
                assert unit.magnitude_unit == spec["weight_unit"], (plant, name)  # magnitude alone agrees


def test_only_answer_key_columns_are_inferred_as_weights(profiles: dict[str, SourceProfile],
                                                         truth: dict) -> None:
    for plant, spec in truth["mappings"]["sources"].items():
        expected = set(_physical(spec, "ordered_weight_lb") + _physical(spec, "invoiced_weight_lb"))
        assert {c.name for c in profiles[_source_dir(plant)].columns if c.unit} == expected


def test_customer_number_shape_matches_answer_key(profiles: dict[str, SourceProfile], truth: dict) -> None:
    expected_top_shape = {"prefixed": "A999999", "dashed": "AAAA-9999"}
    for plant, spec in truth["mappings"]["sources"].items():
        cols = {c.name: c for c in profiles[_source_dir(plant)].columns}
        for name in _physical(spec, "customer_no"):
            if spec["customer_format"] == "bare":
                assert cols[name].dtype == "integer", (plant, name)
            else:
                expected = expected_top_shape[spec["customer_format"]]
                assert cols[name].shapes[0].shape == expected, (plant, name)


def test_header_changes_match_drift_events(profiles: dict[str, SourceProfile], truth: dict) -> None:
    found = {(_source_dir(e["source"]), e["effective"][:7]) for e in truth["drift"]["events"]}
    seen = {(name, v.first_file.rsplit("_", 1)[-1].removesuffix(".csv"))
            for name, p in profiles.items() for v in p.header_variants[1:]}
    assert seen == found
    plt02 = profiles["plt02"].header_variants[1]
    assert (plt02.added, plt02.removed) == (["customer_number"], ["cust_no"])
    assert profiles["plt01"].header_variants[1].added == ["lot_no"]


def test_trailing_whitespace_only_on_plt03_items(profiles: dict[str, SourceProfile], truth: dict) -> None:
    item_col = _physical(truth["mappings"]["sources"]["PLT-03"], "item_no")
    spaced = {(name, c.name) for name, p in profiles.items() for c in p.columns if c.whitespace_pct > 0}
    assert spaced == {("plt03", col) for col in item_col}


def test_candidate_key_duplicates_equal_seeded_duplicate_faults(profiles: dict[str, SourceProfile],
                                                               truth: dict) -> None:
    seeded = Counter(f["plant"] for f in truth["faults"]["faults"] if f["rule_id"] == "V008_duplicate_line")
    for plant, spec in truth["mappings"]["sources"].items():
        key = profiles[_source_dir(plant)].candidate_key
        assert key is not None
        assert key.columns == _physical(spec, "order_no") + _physical(spec, "line_no")
        assert key.duplicate_rows == seeded[plant], plant


def test_negative_quantities_equal_seeded_faults(profiles: dict[str, SourceProfile], truth: dict) -> None:
    seeded = Counter(f["plant"] for f in truth["faults"]["faults"] if f["rule_id"] == "V007_negative_qty")
    for plant in truth["mappings"]["sources"]:
        found = sum(c.negative_count or 0 for c in profiles[_source_dir(plant)].columns)
        assert found == seeded[plant], plant


def test_committed_findings_are_current(profiles: dict[str, SourceProfile]) -> None:
    committed = (REPO_ROOT / "docs" / "findings.md").read_text(encoding="utf-8")
    current = render_markdown(list(profiles.values()))
    assert committed == current, "docs/findings.md is stale: run `make profile`"


def test_rare_uom_shape_equals_seeded_uom_faults(profiles: dict[str, SourceProfile], truth: dict) -> None:
    seeded = Counter(f["plant"] for f in truth["faults"]["faults"] if f["rule_id"] == "V009_uom_mismatch")
    for plant, spec in truth["mappings"]["sources"].items():
        p = profiles[_source_dir(plant)]
        uom = next(c for c in p.columns if c.name in _physical(spec, "uom"))
        rare = [s for s in uom.shapes[1:] if s.share_pct < 5.0]
        assert len(rare) == 1, plant
        assert round(rare[0].share_pct * p.rows / 100) == seeded[plant], plant
