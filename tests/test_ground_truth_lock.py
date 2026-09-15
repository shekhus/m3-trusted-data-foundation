"""The generated answer key must match the committed lock (D-008)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lock_ground_truth import DATA, current_hashes, read_lock  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (DATA / "ground_truth" / "manifest.json").exists(), reason="run `make synth` first"
)


def test_generated_data_matches_lock() -> None:
    expected, actual = read_lock(), current_hashes()
    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    changed = sorted(k for k in expected.keys() & actual.keys() if expected[k] != actual[k])
    assert not (missing or extra or changed), (
        f"generated data drifted from tests/ground_truth.lock\n missing={missing}\n extra={extra}\n"
        f" changed={changed}\n"
        "If intended: python scripts/lock_ground_truth.py and log it in docs/decisions.md"
    )
