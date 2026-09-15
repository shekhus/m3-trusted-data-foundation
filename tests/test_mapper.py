from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import REPO_ROOT
from evals.mapping_eval import run, summarise
from pipeline.canonical import BY_NAME
from pipeline.mapper.contract import ColumnMapping, MappingProposal, header_hash
from pipeline.mapper.heuristic import concepts, name_score, propose
from pipeline.profile import SourceProfile

DATA = REPO_ROOT / "data"


def _col(source: str, canonical: str | None, transforms: list | None = None) -> dict:
    return {"source_col": source, "canonical_col": canonical, "confidence": 0.9, "rationale": "r",
            "transforms": transforms or []}


# --- contract --------------------------------------------------------------------


def test_contract_rejects_hallucinated_canonical_column() -> None:
    with pytest.raises(ValidationError, match="unknown canonical column 'ship_to_city'"):
        ColumnMapping.model_validate(_col("city", "ship_to_city"))


def test_contract_rejects_unknown_transform() -> None:
    with pytest.raises(ValidationError):
        ColumnMapping.model_validate(_col("dt", "order_date", ["parse_date_guess"]))


@pytest.mark.parametrize(
    ("canonical", "transforms", "message"),
    [("order_date", [], "exactly one date parser"),
     ("order_date", ["parse_date_iso", "parse_date_us"], "exactly one date parser"),
     ("ordered_qty", ["parse_date_iso"], "only applies to dates"),
     ("ordered_qty", ["kg_to_lb"], "only applies to weights"),
     ("item_no", ["normalize_customer_no"], "only applies to the customer number")],
)
def test_contract_rejects_transforms_that_do_not_fit_the_column(canonical: str, transforms: list,
                                                                message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ColumnMapping.model_validate(_col("x", canonical, transforms))


def test_contract_rejects_column_not_in_header_and_missing_columns() -> None:
    with pytest.raises(ValidationError, match="missing \\['b'\\], not in header \\['c'\\]"):
        MappingProposal.model_validate({"source": "s", "header": ["a", "b"], "proposed_by": "llm",
                                        "columns": [_col("a", "order_no"), _col("c", "line_no")]})


def test_contract_rejects_two_columns_on_one_canonical() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        MappingProposal.model_validate({"source": "s", "header": ["a", "b"], "proposed_by": "llm",
                                        "columns": [_col("a", "order_no"), _col("b", "order_no")]})


def test_contract_reports_unmapped_required() -> None:
    p = MappingProposal.model_validate({"source": "s", "header": ["a"], "proposed_by": "human",
                                        "columns": [_col("a", "order_no")]})
    assert "customer_no" in p.unmapped_required and "order_no" not in p.unmapped_required


def test_header_hash_is_order_sensitive() -> None:
    assert header_hash(["a", "b"]) != header_hash(["b", "a"])


# --- heuristic ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [("ORDERNUM", {"order", "no"}), ("cfm_dlv_dt", {"confirmed", "delivery", "date"}),
     ("UnitOfMeasure", {"uom"}), ("WT_SHIP_KG", {"weight", "ship"}), ("LOTID", {"lot", "no"})],
)
def test_concepts_split_run_together_and_abbreviated_names(name: str, expected: set[str]) -> None:
    assert concepts(name) == expected


def test_name_score_prefers_the_matching_field() -> None:
    assert name_score("QTY_SHIP", BY_NAME["invoiced_qty"]) > name_score("QTY_SHIP", BY_NAME["ordered_qty"])
    assert name_score("DT_SHIP", BY_NAME["issue_date"]) == 1.0


@pytest.fixture(scope="module")
def eval_summary(generated_profiles: dict[str, SourceProfile]) -> dict:
    return summarise(run(propose, DATA, REPO_ROOT / "evals" / "cases", generated_profiles))


def test_heuristic_maps_every_answer_key_header(eval_summary: dict) -> None:
    s = eval_summary["answer_key_headers"]
    assert s["headers"] == 5
    assert s["top1_accuracy"] == 1.0
    assert s["transform_accuracy"] == 1.0


def test_heuristic_on_unseen_headers_is_measured_not_perfect(eval_summary: dict) -> None:
    # Guards against silently tuning the vocabulary to the eval case: if this starts passing at 1.0,
    # the case has leaked into the heuristic and needs replacing (docs/decisions.md D-013).
    s = eval_summary["mapping_unseen_headers"]
    assert s["columns"] == 14
    assert 0.5 <= s["top1_accuracy"] < 1.0
