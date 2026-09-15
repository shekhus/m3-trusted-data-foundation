"""The failure-case catalogue stays true: every plan §2.6 row is listed and every named test exists."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

from app.config import REPO_ROOT

CATALOG = Path(__file__).with_name("catalog.yaml")


def _plan_cases() -> list[str]:
    plan = (REPO_ROOT / "docs" / "plan.md").read_text(encoding="utf-8")
    section = plan[plan.index("### 2.6 Failure cases") : plan.index("### 2.7")]
    rows = [line for line in section.splitlines() if line.startswith("| ") and "---" not in line]
    return [row.split("|")[1].strip() for row in rows[1:]]  # skip the header row


def _test_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }


def test_every_plan_failure_case_is_catalogued() -> None:
    catalog = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    listed = [c["case"] for c in catalog["plan_2_6"]]
    assert listed == _plan_cases()
    assert all(c["tests"] for c in catalog["plan_2_6"] + catalog["addendum"])


def test_every_catalogued_test_exists() -> None:
    catalog = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    missing = []
    for case in catalog["plan_2_6"] + catalog["addendum"]:
        for node in case["tests"]:
            file, _, name = node.partition("::")
            path = REPO_ROOT / file
            if not path.exists() or re.sub(r"\[.*\]$", "", name) not in _test_functions(path):
                missing.append(node)
    assert not missing, missing
