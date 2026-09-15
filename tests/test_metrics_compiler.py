from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import Engine, text

from app.config import REPO_ROOT
from metrics.compiler import (
    COMPILED_DIR,
    CompiledMetric,
    CompileError,
    MetricDefinition,
    aggregate_sql,
    apply,
    compile_all,
    compile_consumers,
    compile_metric,
    load,
)

DATA = REPO_ROOT / "data"
BASE = {
    "metric": "demo", "version": 1, "status": "current", "owner": "o", "changed_by": "c",
    "effective_from": "2026-01-01", "grain": "order_line", "source": "gold.fact_delivery",
    "key": ["order_no", "line_no"], "period": "issue_date", "dimensions": ["plant"],
    "aggregations": {"lines": "count(*)"},
}


def _definition(**overrides: object) -> MetricDefinition:
    return MetricDefinition.model_validate({**BASE, **overrides})


# --- unit ----------------------------------------------------------------------


def test_repo_metrics_compile_and_committed_sql_is_current() -> None:
    compiled = compile_all()
    assert {(c.definition.metric, c.definition.version) for c in compiled} >= {("otif", 3), ("otif", 2),
                                                                              ("fill_rate", 1)}
    for c in compiled:
        committed = COMPILED_DIR / f"{c.definition.metric}_v{c.definition.version}.sql"
        assert committed.read_text(encoding="utf-8") == c.ddl, f"{committed.name} stale: run `make metrics`"
    consumers = compile_consumers(compiled)
    assert (COMPILED_DIR / "consumers.sql").read_text(encoding="utf-8") == consumers.ddl, \
        "consumers.sql stale: run `make metrics`"


def test_rules_render_in_order_with_params_inlined() -> None:
    c = compile_metric(_definition(
        params={"tol": 0.02},
        inputs={"heavy": "invoiced_weight_lb >= ordered_weight_lb * (1 - tol)", "late": "not heavy"},
        rules={"pick": "heavy if catch_weight_flag else late"},
        aggregations={"lines": "count(*)", "rate": "sum(pick) / count(*)"}), "demo.v1.yaml")
    assert "(invoiced_weight_lb >= (ordered_weight_lb * (1 - 0.02)))" in c.ddl
    assert c.ddl.index("AS heavy") < c.ddl.index("AS late") < c.ddl.index("AS pick")
    assert "(CASE WHEN catch_weight_flag THEN heavy ELSE late END)" in c.ddl
    assert c.source_columns == ["catch_weight_flag", "invoiced_weight_lb", "issue_date", "line_no",
                                "order_no", "ordered_weight_lb", "plant"]
    agg = aggregate_sql(c, ["month", "plant"])
    assert "((SUM(pick::int))::double precision / NULLIF(COUNT(*), 0)) AS rate" in agg
    assert "to_char(metric_date, 'YYYY-MM') AS month" in agg


def test_string_literals_are_escaped_and_in_lists_supported() -> None:
    c = compile_metric(_definition(inputs={"cons": "order_type in ('CONS', \"O'Brien\")"}), "demo.v1.yaml")
    assert "(order_type IN ('CONS', 'O''Brien'))" in c.ddl


@pytest.mark.parametrize(
    ("inputs", "rules", "aggregations", "message"),
    [
        ({"x": "__import__('os').system('rm')"}, {}, None, "unsupported"),
        ({"x": "issue_date.year > 2020"}, {}, None, "unsupported"),
        ({"x": "sum(ordered_qty) > 1"}, {}, None, "only allowed in aggregations"),
        ({"x": "later and on_time_basis"}, {"later": "x"}, None, "used before it is defined"),
        ({"x": "1 < ordered_qty < 5"}, {}, None, "unsupported"),
        ({"x": "ordered_qty >"}, {}, None, "cannot parse"),
        ({}, {}, {"bad": "ordered_qty"}, "inside an aggregate"),
        ({}, {}, {"bad": "sum(sum(ordered_qty))"}, "nested aggregate"),
        ({"plant": "True"}, {}, None, "shadow source columns"),
    ],
)
def test_unsafe_or_invalid_expressions_rejected(inputs: dict, rules: dict, aggregations: dict | None,
                                                message: str) -> None:
    with pytest.raises(CompileError, match=message):
        compile_metric(_definition(inputs=inputs, rules=rules,
                                   aggregations=aggregations or BASE["aggregations"]), "demo.v1.yaml")


def test_file_name_must_match_metric_and_version(tmp_path: Path) -> None:
    path = tmp_path / "otif.v9.yaml"
    path.write_text((REPO_ROOT / "metrics" / "otif.v3.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(CompileError, match="does not match"):
        load(path)


def test_two_current_versions_rejected(tmp_path: Path) -> None:
    for version in (3, 4):
        body = (REPO_ROOT / "metrics" / "otif.v3.yaml").read_text(encoding="utf-8")
        (tmp_path / f"otif.v{version}.yaml").write_text(body.replace("version: 3", f"version: {version}"),
                                                        encoding="utf-8")
    with pytest.raises(CompileError, match="more than one current"):
        compile_all(tmp_path)


def test_unknown_group_by_rejected() -> None:
    with pytest.raises(CompileError, match="cannot group by"):
        aggregate_sql(compile_metric(_definition(), "demo.v1.yaml"), ["customer_name"])


# --- Postgres + generated gold: the compiled views must reproduce the generator's numbers ----------


@pytest.fixture(scope="module")
def gold_engine(generator_gold: Engine) -> Engine:
    with generator_gold.begin() as conn:
        apply(conn, compile_all())
    return generator_gold


def _query(engine: Engine, sql: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.DataFrame(conn.execute(text(sql)).mappings().all())


def _compiled(metric: str, version: int) -> CompiledMetric:
    return next(c for c in compile_all() if (c.definition.metric, c.definition.version) == (metric, version))


@pytest.mark.postgres
def test_otif_v3_reproduces_generator_daily_plant_series(gold_engine: Engine) -> None:
    got = _query(gold_engine, aggregate_sql(_compiled("otif", 3), ["day", "plant"]))
    got["day"] = got["day"].astype(str)
    expected = pd.read_parquet(DATA / "metrics" / "daily_otif_plant.parquet")
    expected["day"] = expected["metric_date"].astype(str)
    merged = expected.merge(got, on=["day", "plant"], how="outer", suffixes=("_exp", "_got"), indicator=True)
    assert (merged["_merge"] == "both").all()
    assert (merged["lines_exp"] == merged["lines_got"]).all()
    for col in ("otif_rate", "on_time_rate"):
        assert (merged[f"{col}_exp"] - merged[f"{col}_got"].astype(float)).abs().max() < 1e-9, col


@pytest.mark.postgres
def test_fill_rate_v1_reproduces_generator_daily_total_series(gold_engine: Engine) -> None:
    got = _query(gold_engine, aggregate_sql(_compiled("fill_rate", 1), ["day"]))
    expected = pd.read_parquet(DATA / "metrics" / "daily_otif_total.parquet")
    assert got["day"].astype(str).tolist() == expected["metric_date"].astype(str).tolist()
    for col in ("fill_rate_count", "fill_rate_weight", "ordered_weight_lb"):
        diff = (expected[col].to_numpy() - got[col].astype(float).to_numpy())
        assert max(abs(d) for d in diff if not math.isnan(d)) < 1e-6, col


@pytest.mark.postgres
def test_governed_and_legacy_otif_match_reconciliation_answer_key(gold_engine: Engine) -> None:
    truth = json.loads((DATA / "ground_truth" / "reconciliation.json").read_text(encoding="utf-8"))
    v3 = _query(gold_engine, aggregate_sql(_compiled("otif", 3), ["month"])).set_index("month")
    v2 = _query(gold_engine, aggregate_sql(_compiled("otif", 2), ["month"])).set_index("month")
    assert len(truth["monthly"]) == len(v3) == len(v2)
    for m in truth["monthly"]:
        month = m["month"]
        assert int(v3.at[month, "lines"]) == m["lines"]
        assert round(float(v3.at[month, "otif_rate"]), 5) == pytest.approx(m["governed_otif"], abs=1e-5)
        # the legacy report suite (tool 1) is exactly the v2 definition: count basis for every item
        assert round(float(v2.at[month, "otif_rate"]), 5) == pytest.approx(m["tool1_otif"], abs=1e-5)


@pytest.mark.postgres
def test_views_refuse_missing_source_columns(gold_engine: Engine) -> None:
    broken = compile_metric(_definition(inputs={"x": "no_such_column > 0"}), "demo.v1.yaml")
    with pytest.raises(CompileError, match="no_such_column"), gold_engine.begin() as conn:
        apply(conn, [broken])
