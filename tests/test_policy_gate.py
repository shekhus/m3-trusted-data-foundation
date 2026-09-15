"""Policy gate: adversarial fixes injected as a model might propose them (addendum §2.5: all blocked)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agent.contracts import EvidenceRef, FieldChange, ProposedFix, Resolution, Value
from agent.policy_gate import POLICY_FILE, SOP_PROTECTED, ExceptionFacts, PolicyError, evaluate, load_policy
from app.config import REPO_ROOT

SOP = REPO_ROOT / "data" / "kb" / "docs" / "SOP-DQ-001-v2.md"
EVIDENCE = [EvidenceRef(kind="resolution", ref="RES-0001")]

V009 = ExceptionFacts(
    1,
    "V009",
    {
        "uom": "LB",
        "ordered_weight_lb": 255.15,
        "invoiced_weight_lb": 256.0,
        "customer_no": "C000003",
        "ordered_qty": 12.0,
    },
    master_uom="CASE",
)
V010 = ExceptionFacts(
    2,
    "V010",
    {"order_date": None, "issue_date": "2026-03-04"},
    raw={"order_date": "13/02/2026", "issue_date": "2026-03-04"},
)
V004 = ExceptionFacts(3, "V004", {"customer_no": "CUST-0004"}, raw={"customer_no": "CUST-0004"})
V005 = ExceptionFacts(4, "V005", {"item_no": "IT00037  "}, raw={"item_no": "IT00037  "})


def _fix(kind: str, *changes: tuple[str, Value, Value], retain: bool = True) -> ProposedFix:
    return ProposedFix(
        kind=kind,
        retain_original=retain,
        changes=[FieldChange(field=f, original=o, proposed=p) for f, o, p in changes],
    )


def _resolution(facts: ExceptionFacts, fix: ProposedFix | None, outcome: str = "auto_fixable") -> Resolution:
    return Resolution(
        exception_id=facts.exception_id,
        outcome=outcome,  # type: ignore[arg-type]
        confidence=0.9,
        evidence_refs=EVIDENCE,
        proposed_fix=fix,
        rationale="a known normalisation for this source",
    )


# --- the fixes the SOP allows pass --------------------------------------------------------


def test_exact_kg_to_lb_conversion_with_the_original_retained_passes() -> None:
    fix = _fix(
        "uom_kg_to_lb",
        ("ordered_weight_lb", 255.15, 562.509),
        ("invoiced_weight_lb", 256.0, 564.383),
        ("uom", "LB", "CASE"),
    )
    verdict = evaluate(_resolution(V009, fix), V009)
    assert (verdict.disposition, verdict.reasons, verdict.applicable) == ("apply_after_approval", [], True)


def test_the_same_date_reread_in_an_allowed_format_passes() -> None:
    verdict = evaluate(
        _resolution(V010, _fix("date_format", ("order_date", "13/02/2026", "2026-02-13"))), V010
    )
    assert verdict.disposition == "apply_after_approval"


def test_a_fix_that_changes_no_field_is_described_and_goes_to_a_person() -> None:
    fix = ProposedFix(kind="exclude_duplicate", retain_original=True, description="keep the first occurrence")
    verdict = evaluate(_resolution(V009, fix), V009)
    assert verdict.disposition == "human_proposal"
    with pytest.raises(ValidationError, match="must describe what it does"):
        ProposedFix(kind="exclude_duplicate", retain_original=True)
    empty_allowed_kind = ProposedFix(kind="date_format", retain_original=True, description="re-read dates")
    assert "names no field change" in " ".join(evaluate(_resolution(V010, empty_allowed_kind), V010).reasons)


def test_a_classification_without_a_fix_is_not_a_fix() -> None:
    resolution = _resolution(V004, None, outcome="needs_master_data")
    assert evaluate(resolution, V004).disposition == "no_fix"


# --- adversarial: every one becomes a human proposal, never an applied fix ------------------


ADVERSARIAL: list[tuple] = [
    # the sharpest case: the agent is RIGHT (CUST-0004 is C000004) and is still blocked
    (
        "correct customer normalisation",
        V004,
        _fix("customer_no_normalisation", ("customer_no", "CUST-0004", "C000004")),
        "not an allowed auto-fix",
    ),
    (
        "customer change smuggled into an allowed kind",
        V009,
        _fix("uom_kg_to_lb", ("ordered_weight_lb", 255.15, 562.509), ("customer_no", "C000003", "C000004")),
        "customer_no may not be changed",
    ),
    (
        "correct item trim",
        V005,
        _fix("trim_item_no", ("item_no", "IT00037  ", "IT00037")),
        "not an allowed auto-fix",
    ),
    (
        "item trim labelled as a date fix",
        V005,
        _fix("date_format", ("item_no", "IT00037  ", "IT00037")),
        "item_no may not be changed",
    ),
    (
        "negative quantity negated",
        V009,
        _fix("sign_flip", ("ordered_qty", -12.0, 12.0)),
        "not an allowed auto-fix",
    ),
    (
        "quantity changed inside an allowed kind",
        V009,
        _fix("uom_kg_to_lb", ("ordered_qty", 12.0, 26.0)),
        "ordered_qty may not be changed",
    ),
    (
        "weight rounded up, not converted",
        V009,
        _fix("uom_kg_to_lb", ("ordered_weight_lb", 255.15, 600.0)),
        "is not 255.15 kg in lb",
    ),
    (
        "weight converted from an invented original",
        V009,
        _fix("uom_kg_to_lb", ("ordered_weight_lb", 300.0, 661.386)),
        "is not the row's value",
    ),
    (
        "unit relabelled without converting",
        V009,
        _fix("uom_kg_to_lb", ("uom", "LB", "CASE")),
        "must convert the weight",
    ),
    (
        "conversion without keeping the original",
        V009,
        _fix("uom_kg_to_lb", ("ordered_weight_lb", 255.15, 562.509), retain=False),
        "original value must be retained",
    ),
    (
        "kg to lb on the wrong rule",
        V010,
        _fix("uom_kg_to_lb", ("ordered_weight_lb", 255.15, 562.509)),
        "fixes V009 exceptions, not V010",
    ),
    (
        "date shifted, not re-read",
        V010,
        _fix("date_format", ("order_date", "13/02/2026", "2026-02-14")),
        "is not '13/02/2026' re-read",
    ),
    (
        "date fix claiming a different source value",
        V010,
        _fix("date_format", ("order_date", "12/02/2026", "2026-02-12")),
        "is not the source value",
    ),
    (
        "date fix on a field the kind does not cover",
        V010,
        _fix("date_format", ("lot_no", "L1", "L2")),
        "may not change lot_no",
    ),
    (
        "duplicate line deleted",
        V009,
        _fix("exclude_duplicate", ("status", "open", "excluded")),
        "not an allowed auto-fix",
    ),
    ("fix aimed at another exception", V010, _fix("date_format", ("order_date", "13/02/2026", "2026-02-13"))),
]


@pytest.mark.parametrize(
    ("name", "facts", "fix", "reason"),
    [a if len(a) == 4 else (*a, "different exception") for a in ADVERSARIAL],
    ids=[a[0] for a in ADVERSARIAL],
)
def test_adversarial_fixes_are_blocked_and_kept_as_human_proposals(
    name: str, facts: ExceptionFacts, fix: ProposedFix, reason: str
) -> None:
    resolution = _resolution(facts, fix)
    if name == "fix aimed at another exception":
        resolution = resolution.model_copy(update={"exception_id": facts.exception_id + 100})
    verdict = evaluate(resolution, facts)
    assert verdict.disposition == "human_proposal" and not verdict.applicable
    assert verdict.resolution.proposed_fix == fix  # not discarded: a person decides
    assert any(reason in r for r in verdict.reasons), verdict.reasons


def test_every_adversarial_case_is_blocked() -> None:
    blocked = 0
    for name, facts, fix, *_ in ADVERSARIAL:
        resolution = _resolution(facts, fix)
        if name == "fix aimed at another exception":
            resolution = resolution.model_copy(update={"exception_id": facts.exception_id + 100})
        blocked += not evaluate(resolution, facts).applicable
    assert blocked == len(ADVERSARIAL) == 16


# --- the contract --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"evidence_refs": []}, "at least 1"),
        ({"outcome": "unsure"}, "auto_fixable"),
        ({"outcome": "escalate"}, "only an auto_fixable classification may propose a fix"),
        ({"proposed_fix": None}, "must say which fix"),
        ({"confidence": 1.2}, "less than or equal to 1"),
        ({"outcome": "escalate", "proposed_fix": None, "not_covered": True}, "source_defect finding"),
        ({"rationale": "ok"}, "at least 10"),
    ],
)
def test_contract_rejects_classifications_the_rules_forbid(overrides: dict, message: str) -> None:
    base = _resolution(V010, _fix("date_format", ("order_date", "13/02/2026", "2026-02-13"))).model_dump()
    with pytest.raises(ValidationError, match=message):
        Resolution.model_validate({**base, **overrides})


# --- the allowlist file cannot widen the SOP ------------------------------------------------


@pytest.mark.skipif(not SOP.exists(), reason="data/ not generated (run `make synth`)")
def test_policy_file_matches_the_sop_text() -> None:
    policy = load_policy()
    sop = re.sub(r"\s+", " ", SOP.read_text(encoding="utf-8").replace("*", ""))
    assert policy.source.quote in sop and policy.source.doc_id == "SOP-DQ-001-v2"
    assert all(k.sop_text in sop for k in policy.allowed)
    assert {k.kind for k in policy.allowed} == {"uom_kg_to_lb", "date_format"}
    assert set(policy.never_changed) >= SOP_PROTECTED


def _tampered(tmp_path: Path, edit) -> Path:  # noqa: ANN001
    doc = yaml.safe_load(POLICY_FILE.read_text(encoding="utf-8"))
    edit(doc)
    path = tmp_path / "autofix.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (
            lambda d: d["allowed"].append(
                {
                    "kind": "customer_no_normalisation",
                    "sop_text": "x",
                    "rules": ["V004"],
                    "fields": ["customer_no"],
                }
            ),
            "does not name",
        ),
        (lambda d: d["never_changed"].remove("customer_no"), "stops protecting"),
        (lambda d: d["allowed"][1]["fields"].append("item_no"), "protected fields"),
        (lambda d: d["allowed"][0].update({"factor": 3.0}), "kg→lb factor"),
        (lambda d: d["allowed"][0]["converts"].append("ordered_qty"), "kg→lb factor"),
        (lambda d: d.update({"retain_original": False}), "retained"),
    ],
)
def test_a_widened_policy_file_is_refused(tmp_path: Path, edit, message: str) -> None:  # noqa: ANN001
    with pytest.raises(PolicyError, match=message):
        load_policy(_tampered(tmp_path, edit))
    shutil.rmtree(tmp_path, ignore_errors=True)
